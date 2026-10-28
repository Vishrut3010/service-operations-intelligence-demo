"""Analytics engine for the Service Operations Intelligence demonstration.
No network calls are made by this module. Workbooks are processed in memory within the app session.
"""
from __future__ import annotations

import io
import re
import textwrap
from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

DATE_FMT = "%Y-%m-%d"
PART_COLUMNS = {
    "spare", "sparedesc", "additive", "additivedesc", "accessories", "accessoriesdesc"
}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    s = str(value).strip()
    if s.lower() in {"nan", "nat", "none", "null"}:
        return ""
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return s


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean_text(value).lower())


def parse_dt(value: Any) -> Optional[pd.Timestamp]:
    if value is None or clean_text(value) == "":
        return None
    try:
        dt = pd.to_datetime(value, errors="coerce")
        if pd.isna(dt):
            return None
        dt = pd.Timestamp(dt)
        if dt.year <= 1900:
            return None
        return dt
    except Exception:
        return None


def _parse_report_range(text: str) -> Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    # Examples: "Date Range :10/1/2026 12:00:00 AM - 10/22/2026 12:00:00 AM"
    match = re.search(r"date\s*range\s*:\s*(.+?)\s+-\s+(.+)$", text, flags=re.I)
    if not match:
        return None, None
    start = parse_dt(match.group(1).strip())
    end = parse_dt(match.group(2).strip())
    return start, end


def _filename_timestamp(name: str) -> Optional[pd.Timestamp]:
    match = re.search(r"(20\d{2}-\d{2}-\d{2})T(\d{6})(?:\.(\d+))?", name)
    if not match:
        return None
    date_part, time_part, fractional = match.groups()
    fmt = "%Y-%m-%dT%H%M%S"
    stamp = f"{date_part}T{time_part}"
    try:
        dt = datetime.strptime(stamp, fmt)
        if fractional:
            micro = (fractional + "000000")[:6]
            dt = dt.replace(microsecond=int(micro))
        return pd.Timestamp(dt)
    except ValueError:
        return None


def _detect_header(preview: pd.DataFrame) -> Optional[int]:
    for idx in range(min(len(preview), 20)):
        vals = {norm(v) for v in preview.iloc[idx].tolist() if clean_text(v)}
        has_ticket = bool(vals & {"ticketno", "ticketnumber", "ticket"})
        has_context = bool(vals & {"calltype", "status", "feedback", "daysold", "repeatcallflag"})
        if has_ticket and has_context:
            return idx
    return None


def _classify(df: pd.DataFrame) -> str:
    columns = {norm(c) for c in df.columns}
    if "ticketno" not in columns and "ticketnumber" not in columns:
        return "unknown"
    if "feedback" in columns and "feedbackdate" in columns:
        return "customer_satisfaction"
    # Calls Registered exports can contain Soft Closure Date and Status too;
    # identify them before Soft Closure reports to avoid misclassifying the whole report.
    if "callbookdate" in columns and ("assignon" in columns or "closedtime" in columns):
        return "calls_registered"
    # Soft Closure (All Status) exports support closure reconciliation and productivity.
    if "softclosuredate" in columns and "status" in columns:
        return "soft_closure"
    if "daysold" in columns and "repeatcallflag" in columns:
        return "pending_calls"
    return "unknown"


def _dedupe_excel_headers(raw_headers: Sequence[Any]) -> List[str]:
    """Match pandas' usual duplicate-header naming while reading the workbook once."""
    names: List[str] = []
    used: set[str] = set()
    next_suffix: Dict[str, int] = {}
    for idx, value in enumerate(raw_headers):
        base = clean_text(value) or f"Unnamed: {idx}"
        name = base
        suffix = next_suffix.get(base, 0)
        if name in used:
            suffix = max(suffix, 1)
            name = f"{base}.{suffix}"
            while name in used:
                suffix += 1
                name = f"{base}.{suffix}"
        used.add(name)
        next_suffix[base] = suffix + 1
        names.append(name)
    return names


def parse_report(filename: str, file_bytes: bytes, upload_order: int = 0) -> Dict[str, Any]:
    """Read one Excel workbook once, detect its header and classify the report.

    Previous versions opened every workbook twice (once for the 15-row preview and
    once for the actual data). Reading the first sheet once reduces upload latency,
    especially for the 6k-row CSAT exports, without changing the parsed cell values.
    """
    sheet = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=None, dtype=object)
    preview = sheet.iloc[:15].copy()
    header_idx = _detect_header(preview)
    if header_idx is None:
        raise ValueError("Could not locate a report header row containing Ticket No and report fields.")

    metadata = ""
    for row in preview.itertuples(index=False, name=None):
        for cell in row:
            val = clean_text(cell)
            if val and "date range" in val.lower():
                metadata = val
                break
        if metadata:
            break
    period_start, period_end = _parse_report_range(metadata)

    columns = _dedupe_excel_headers(sheet.iloc[header_idx].tolist())
    df = sheet.iloc[header_idx + 1:].copy()
    df.columns = columns
    # Remove only fully empty / unnamed filler columns. Keep all meaningful source columns.
    drop_cols = [c for c in df.columns
                 if clean_text(c).lower().startswith("unnamed") and df[c].isna().all()]
    if drop_cols:
        df = df.drop(columns=drop_cols)
    df = df.dropna(how="all").reset_index(drop=True)
    # Prefer the actual report title printed inside the workbook, then the filename,
    # then headers. Portal downloads are often given generic filenames such as
    # report(3).xlsx. Soft Closure reports can share Call Book Date / Closed Time
    # columns with Calls Registered, so ignoring the workbook title hides SA activity.
    pre_header_text = " ".join(
        clean_text(cell)
        for row in preview.iloc[:header_idx].itertuples(index=False, name=None)
        for cell in row if clean_text(cell)
    )
    title_hint_text = " ".join([pre_header_text, metadata, filename])
    title_hint = norm(title_hint_text)
    if "softclosure" in title_hint or ("soft" in title_hint and "closure" in title_hint):
        report_type = "soft_closure"
    elif "customersatisfaction" in title_hint:
        report_type = "customer_satisfaction"
    elif "callsregistered" in title_hint:
        report_type = "calls_registered"
    elif "pendingcall" in title_hint:
        report_type = "pending_calls"
    else:
        # If a generic-named export has no readable title, use a content-level
        # closure signature before the more generic Calls Registered signature.
        status_col_hint = find_col(df, ["Status", "Call Status"])
        status_values_hint = (df[status_col_hint].map(norm) if status_col_hint else pd.Series(dtype=str))
        has_soft_closure_rows = bool(status_values_hint.map(lambda v: "softclosure" in v or "resolvedsoftclosure" in v).any())
        columns_hint = {norm(c) for c in df.columns}
        if has_soft_closure_rows and ("softclosuredate" in columns_hint or "status" in columns_hint):
            report_type = "soft_closure"
        else:
            report_type = _classify(df)
    if report_type == "unknown":
        raise ValueError("The file was read, but its report type could not be identified. Expected Pending Calls, Calls Registered, Soft Closure, or Customer Satisfaction.")

    filename_dt = _filename_timestamp(filename)
    if filename_dt is not None:
        snapshot_dt = filename_dt
        snapshot_source = "timestamp in filename"
    elif period_end is not None:
        snapshot_dt = pd.Timestamp(period_end.date()) + pd.Timedelta(hours=23, minutes=59)
        snapshot_source = "date range (export time unknown)"
    else:
        snapshot_dt = pd.Timestamp.now().floor("min")
        snapshot_source = "upload time fallback (export time unknown)"

    return {
        "filename": filename,
        "bytes": len(file_bytes),
        "report_type": report_type,
        "df": df,
        "header_row_excel": header_idx + 1,
        "metadata": metadata,
        "report_title": pre_header_text,
        "period_start": period_start,
        "period_end": period_end,
        "snapshot_dt": snapshot_dt,
        "snapshot_source": snapshot_source,
        "upload_order": upload_order,
        "rows": len(df),
    }


def find_col(df: pd.DataFrame, aliases: Sequence[str]) -> Optional[str]:
    mapping = {norm(c): c for c in df.columns}
    for alias in aliases:
        key = norm(alias)
        if key in mapping:
            return mapping[key]
    return None


def choose_value(series: pd.Series) -> Any:
    values = [clean_text(v) for v in series.tolist()]
    values = [v for v in values if v]
    if not values:
        return ""
    counts = Counter(values)
    # Counter preserves first-seen order for ties.
    return counts.most_common(1)[0][0]


def join_values(series: pd.Series, max_len: int = 300) -> str:
    vals = []
    seen = set()
    for value in series.tolist():
        value = clean_text(value)
        if value and value not in seen:
            vals.append(value)
            seen.add(value)
    out = " | ".join(vals)
    if len(out) > max_len:
        return out[: max_len - 3] + "..."
    return out


def get_ticket_col(df: pd.DataFrame) -> Optional[str]:
    return find_col(df, ["Ticket No", "TicketNo", "Ticket Number", "Ticket"])


# These call types are intentionally excluded from pending-call ranking and pending KPIs.
# The source exports may call the first type "COURTESY CALL" or "COURTESY VISIT";
# the second can appear as "DEALER DEFECTIVE" or "DEALER DEFECTIVE REQUEST".
_EXCLUDED_PENDING_CALL_TYPE_MARKERS = ("courtesy", "dealerdefective")


def excluded_pending_call_type_mask(df: pd.DataFrame) -> pd.Series:
    """Return True for courtesy and dealer-defective rows in a pending-call export."""
    call_type_col = find_col(df, ["Call Type", "CallType"])
    if call_type_col is None:
        return pd.Series(False, index=df.index, dtype=bool)
    return df[call_type_col].map(
        lambda value: any(marker in norm(value) for marker in _EXCLUDED_PENDING_CALL_TYPE_MARKERS)
    ).astype(bool)


def filter_pending_call_types(df: pd.DataFrame) -> pd.DataFrame:
    """Exclude Courtesy and Dealer Defective calls from pending-call analysis."""
    mask = excluded_pending_call_type_mask(df)
    return df.loc[~mask].copy()


def distinct_ticket_count(df: pd.DataFrame) -> int:
    ticket_col = get_ticket_col(df)
    if not ticket_col:
        return len(df)
    return len({clean_text(v) for v in df[ticket_col].tolist() if clean_text(v)})


def summarize_pending_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Consolidate component rows to one record per ticket using vectorised grouping.

    Normal columns use the most-frequent nonblank value, ties go to the earliest
    source occurrence. Component columns preserve distinct values in source order;
    Days Old uses the maximum and Call Book Date uses the earliest valid date.
    """
    ticket_col = get_ticket_col(df)
    if not ticket_col or df.empty:
        return pd.DataFrame(columns=list(df.columns))

    working = df.copy()
    working["__ticket_key"] = working[ticket_col].map(clean_text)
    working = working.loc[working["__ticket_key"].ne("")].copy()
    if working.empty:
        return pd.DataFrame(columns=list(df.columns) + ["Source Row Count"])

    keys = working["__ticket_key"]
    key_order = keys.drop_duplicates().tolist()
    group_sizes = keys.value_counts(sort=False)
    # Use the first source row as the base, then replace consolidated columns.
    result = (working.drop_duplicates("__ticket_key", keep="first")
                    .set_index("__ticket_key", drop=True)
                    .reindex(key_order))
    columns = [c for c in working.columns if c != "__ticket_key"]
    part_norms = {norm(c) for c in columns} & PART_COLUMNS
    part_columns = {c for c in columns if norm(c) in part_norms}
    days_col = next((c for c in columns if norm(c) == "daysold"), None)
    book_col = next((c for c in columns if norm(c) == "callbookdate"), None)

    # Consolidate part/additive/accessory descriptions while preserving source order.
    for col in part_columns:
        cleaned = working[col].map(clean_text)
        tmp = pd.DataFrame({"key": keys, "value": cleaned})
        tmp = tmp[tmp["value"].ne("")]
        if tmp.empty:
            result[col] = ""
        else:
            # Series are already in source order; dict insertion order preserves it.
            joined = tmp.groupby("key", sort=False)["value"].agg(
                lambda values: join_values(values, max_len=300)
            )
            result[col] = joined.reindex(key_order).fillna("")

    if days_col:
        numeric_ages = pd.to_numeric(working[days_col], errors="coerce")
        max_ages = numeric_ages.groupby(keys, sort=False).max()
        result[days_col] = max_ages.reindex(key_order)
        result[days_col] = result[days_col].map(lambda value: int(value) if pd.notna(value) else "")

    if book_col:
        # Parse each source row once, not once for every ticket group.
        parsed_dates = pd.to_datetime(working[book_col], errors="coerce", format="mixed")
        try:
            parsed_dates = parsed_dates.where(parsed_dates.dt.year > 1900)
        except Exception:
            pass
        earliest = parsed_dates.groupby(keys, sort=False).min().reindex(key_order)
        # Where no valid date exists, preserve the previous mode/first-seen fallback.
        cleaned_book = working[book_col].map(clean_text)
        valid = cleaned_book.ne("")
        if valid.any():
            tmp = pd.DataFrame({"key": keys[valid], "value": cleaned_book[valid], "row": range(int(valid.sum()))})
            mode_book = _first_mode_map(tmp)
        else:
            mode_book = {}
        result[book_col] = [earliest.loc[k] if pd.notna(earliest.loc[k]) else mode_book.get(k, "") for k in key_order]

    # Efficient per-column modes: group value frequencies and stable first occurrence.
    special = part_columns | {c for c in (days_col, book_col) if c}
    for col in columns:
        if col in special or col == ticket_col:
            continue
        cleaned = working[col].map(clean_text)
        nonblank = cleaned.ne("")
        if not nonblank.any():
            result[col] = ""
            continue
        tmp = pd.DataFrame({"key": keys[nonblank].to_numpy(),
                            "value": cleaned[nonblank].to_numpy(),
                            "row": working.index[nonblank].to_numpy()})
        # Each (ticket, value) pair appears once with count and its first row.
        stats = tmp.groupby(["key", "value"], sort=False, as_index=False).agg(
            count=("row", "size"), first=("row", "min")
        )
        winners = (stats.sort_values(["key", "count", "first"],
                                     ascending=[True, False, True], kind="stable")
                        .drop_duplicates("key", keep="first"))
        mode_map = dict(zip(winners["key"], winners["value"]))
        result[col] = [mode_map.get(k, "") for k in key_order]

    result[ticket_col] = key_order
    result["Source Row Count"] = [int(group_sizes.get(k, 0)) for k in key_order]
    result = result.reset_index(drop=True)
    return result[columns + ["Source Row Count"]]


def _first_mode_map(tmp: pd.DataFrame) -> Dict[str, str]:
    """Return most frequent value per key; ties use earliest source row."""
    if tmp.empty:
        return {}
    stats = tmp.groupby(["key", "value"], sort=False, as_index=False).agg(
        count=("row", "size"), first=("row", "min")
    )
    winners = (stats.sort_values(["key", "count", "first"],
                                 ascending=[True, False, True], kind="stable")
                    .drop_duplicates("key", keep="first"))
    return dict(zip(winners["key"], winners["value"]))


def _get_value(row: pd.Series, aliases: Sequence[str]) -> str:
    for alias in aliases:
        col = find_col(pd.DataFrame(columns=row.index), [alias])
        if col is not None:
            return clean_text(row.get(col, ""))
    return ""


def enrich_pending(pending_df: pd.DataFrame, snapshot_dt: pd.Timestamp) -> pd.DataFrame:
    df = pending_df.copy()
    aliases = {
        "Ticket No": ["Ticket No", "TicketNo", "Ticket Number", "Ticket"],
        "Franchise Name": ["Franchise Name", "FrName", "Franchise"],
        "Customer Name": ["Customer Name"],
        "Service Executive Name": ["Service Executive Name", "Branch Exe Name", "Executive Name"],
        "Technician Name": ["Technician Name"],
        "Days Old": ["Days Old"],
        "Escalation Level": ["ESCLATION LEVEL", "Escalation Level", "Escalation"],
        "Scheduled Date": ["Scheduled date", "Schedule Date", "Scheduled Date"],
        "Status": ["Status"],
        "Repeat Call Flag": ["repeatcall flag", "Repeat Call Flag"],
        "Call Type": ["Call Type"],
        "Pending Reason": ["Pending Reason", "PendingReason", "Reason for Pendency", "Pendency Reason"],
        "Product": ["Product"],
        "Model": ["Model"],
        "Customer Code": ["Customer Code"],
        "Call Book Date": ["Call Book Date"],
    }
    for standard, choices in aliases.items():
        col = find_col(df, choices)
        df[standard] = df[col].map(clean_text) if col else ""
    ages = pd.to_numeric(df["Days Old"], errors="coerce")
    call_dates = pd.to_datetime(df["Call Book Date"], errors="coerce", format="mixed")
    calc_ages = (snapshot_dt.normalize() - call_dates.dt.normalize()).dt.days
    calc_ages = calc_ages.where(call_dates.dt.year > 1900)
    ages = ages.fillna(calc_ages).fillna(0).clip(lower=0)
    df["Days Old"] = ages.astype(int)
    sched = pd.to_datetime(df["Scheduled Date"], errors="coerce", format="mixed")
    sched = sched.where(sched.dt.year > 1900)
    sched_overdue = (sched < snapshot_dt).fillna(False)

    # Keep source escalation/repeat values as descriptive indicators only; they do not affect ordering.
    df["Has Escalation"] = df["Escalation Level"].map(
        lambda v: bool(clean_text(v)) and norm(v) not in {"na", "none", "nan", "0"}
    )
    df["Is Repeat Call"] = df["Repeat Call Flag"].map(
        lambda v: norm(v) in {"x", "yes", "y", "true", "1", "repeat", "repeatcall"}
    )
    df["Missing Executive"] = df["Service Executive Name"].map(lambda v: not bool(clean_text(v)))
    df["Missing Technician"] = df["Technician Name"].map(lambda v: not bool(clean_text(v)))
    df["Snapshot As Of"] = snapshot_dt.strftime("%Y-%m-%d %H:%M")
    df["Scheduled Overdue"] = sched_overdue.astype(bool)
    # ONLY sort key: Days Old descending. Equal-age calls preserve their source order.
    # There is no score, weighting, escalation/repeat/schedule tie-breaker, or colour band.
    df = df.sort_values(by="Days Old", ascending=False, kind="stable", na_position="last").reset_index(drop=True)
    df.insert(0, "Order", range(1, len(df) + 1))
    return df


def _group_ticket_ids(df: pd.DataFrame) -> set:
    col = get_ticket_col(df)
    if not col:
        return set()
    return {clean_text(v) for v in df[col].tolist() if clean_text(v)}


def _pending_status_map(df: pd.DataFrame) -> Dict[str, str]:
    col_ticket = get_ticket_col(df)
    col_status = find_col(df, ["Status"])
    if not col_ticket:
        return {}
    if not col_status:
        return {clean_text(t): "" for t in df[col_ticket] if clean_text(t)}
    out = {}
    for ticket, group in df.groupby(df[col_ticket].map(clean_text), sort=False):
        if ticket:
            out[clean_text(ticket)] = choose_value(group[col_status])
    return out


def _valid_datetime_series(series: pd.Series) -> pd.Series:
    """Parse mixed Excel timestamps while treating 1900 placeholder dates as missing."""
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    try:
        return parsed.where(parsed.notna() & (parsed.dt.year > 1900))
    except Exception:
        return pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")


def soft_closure_ticket_ids_as_of(reports: List[Dict[str, Any]], snapshot_dt: pd.Timestamp) -> set:
    """Ticket IDs with a soft-closure event at or before the pending snapshot time."""
    closed_ids = set()
    for report in reports:
        if report.get("report_type") != "soft_closure":
            continue
        df = report.get("df", pd.DataFrame())
        ticket_col = get_ticket_col(df)
        if not ticket_col or df.empty:
            continue
        soft_col = find_col(df, ["Soft Closure Date", "Soft Closed Date", "SoftClosureDate", "Closure Date"])
        closed_col = find_col(df, ["ClosedDate", "Closed Date", "Closed Time", "Closed Timestamp"])
        if soft_col:
            event_ts = _valid_datetime_series(df[soft_col])
        else:
            event_ts = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
        if closed_col:
            event_ts = event_ts.fillna(_valid_datetime_series(df[closed_col]))
        for idx, row in df.iterrows():
            ticket = clean_text(row.get(ticket_col, ""))
            if not ticket:
                continue
            event_time = event_ts.loc[idx] if idx in event_ts.index else pd.NaT
            if pd.notna(event_time):
                if pd.Timestamp(event_time) <= pd.Timestamp(snapshot_dt):
                    closed_ids.add(ticket)
            else:
                # Conservative fallback only when event timestamps are absent: use report-period timing.
                period_end = report.get("period_end")
                if period_end is not None and pd.Timestamp(period_end).date() < pd.Timestamp(snapshot_dt).date():
                    closed_ids.add(ticket)
                elif (period_end is not None and pd.Timestamp(period_end).date() == pd.Timestamp(snapshot_dt).date()
                      and pd.Timestamp(report.get("snapshot_dt")) <= pd.Timestamp(snapshot_dt)):
                    closed_ids.add(ticket)
    return closed_ids


def active_pending_data(report: Dict[str, Any], reports: Optional[List[Dict[str, Any]]] = None) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Return one row per active pending ticket plus its source rows and exclusion stats.

    Exclusions: Courtesy / Dealer Defective call types, tickets soft-closed at or before
    the snapshot timestamp, and tickets whose status is Negative Response from Customer.
    """
    reports = reports or [report]
    # Cache consolidation/exclusions within one analysis run. Snapshot comparison
    # asks for current and previous active queues again; don't regroup thousands of
    # component rows on each call. The key changes when closure reports change.
    soft_sig = tuple(sorted(
        (str(r.get("filename", "")), str(r.get("snapshot_dt", "")), int(r.get("rows", 0)))
        for r in reports if r.get("report_type") == "soft_closure"
    ))
    cache_key = (str(report.get("filename", "")), str(report.get("snapshot_dt", "")), soft_sig)
    cached = report.setdefault("_active_pending_cache", {}).get(cache_key)
    if cached is not None:
        cached_grouped, cached_rows, cached_stats = cached
        return cached_grouped.copy(), cached_rows.copy(), dict(cached_stats)

    source_df = report.get("df", pd.DataFrame())
    by_type = filter_pending_call_types(source_df)
    ticket_col = get_ticket_col(by_type)
    if not ticket_col:
        return pd.DataFrame(columns=list(by_type.columns)), by_type.iloc[0:0].copy(), {
            "source_distinct_tickets": 0, "active_tickets": 0,
            "soft_closed_tickets": 0, "negative_response_tickets": 0,
            "excluded_ticket_ids": set(), "active_ticket_ids": set(),
        }

    grouped = summarize_pending_rows(by_type)
    grouped_ticket_col = get_ticket_col(grouped)
    source_ids = {clean_text(v) for v in grouped[grouped_ticket_col].tolist() if clean_text(v)}

    # If any source row for a ticket carries a negative-response status, exclude the whole ticket.
    status_col_raw = find_col(by_type, ["Status"])
    negative_ids = set()
    if status_col_raw:
        negative_rows = by_type[by_type[status_col_raw].map(lambda v: "negativeresponse" in norm(v))]
        negative_ids = {clean_text(v) for v in negative_rows[ticket_col].tolist() if clean_text(v)}

    snapshot_dt = pd.Timestamp(report.get("snapshot_dt"))
    soft_closed_ids = soft_closure_ticket_ids_as_of(reports, snapshot_dt)
    soft_matched = source_ids & soft_closed_ids
    negative_only = negative_ids - soft_matched
    excluded_ids = soft_matched | negative_ids
    active_ids = source_ids - excluded_ids
    active_grouped = grouped[grouped[grouped_ticket_col].map(clean_text).isin(active_ids)].copy().reset_index(drop=True)
    source_ticket_ids = by_type[ticket_col].map(clean_text)
    active_source_rows = by_type[source_ticket_ids.isin(active_ids)].copy().reset_index(drop=True)

    stats = {
        "source_distinct_tickets": len(source_ids),
        "active_tickets": len(active_ids),
        "soft_closed_tickets": len(soft_matched),
        "negative_response_tickets": len(negative_only),
        "excluded_ticket_ids": excluded_ids,
        "active_ticket_ids": active_ids,
        "negative_response_ids": negative_only,
        "soft_closed_ids": soft_matched,
    }
    report.setdefault("_active_pending_cache", {})[cache_key] = (
        active_grouped.copy(), active_source_rows.copy(), dict(stats)
    )
    return active_grouped, active_source_rows, stats


def snapshot_comparison(previous: Optional[Dict[str, Any]], current: Dict[str, Any],
                        reports: Optional[List[Dict[str, Any]]] = None) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """Compare only tickets active at each snapshot, using closure timestamps when available."""
    reports = reports or [current] + ([previous] if previous else [])
    cur_grouped, _, _ = active_pending_data(current, reports)
    cur_ticket = get_ticket_col(cur_grouped)
    if not cur_ticket:
        return pd.DataFrame(), {"new": 0, "continuing": 0, "missing": 0, "status_changed": 0, "age_increased": 0}
    cur_map = {}
    age_col_cur = find_col(cur_grouped, ["Days Old"])
    exec_col_cur = find_col(cur_grouped, ["Service Executive Name"])
    franchise_col_cur = find_col(cur_grouped, ["Franchise Name", "Franchise"])
    status_col_cur = find_col(cur_grouped, ["Status"])
    for _, r in cur_grouped.iterrows():
        key = clean_text(r.get(cur_ticket, ""))
        if not key:
            continue
        cur_map[key] = {
            "status": clean_text(r.get(status_col_cur, "")) if status_col_cur else "",
            "age": pd.to_numeric(pd.Series([r.get(age_col_cur, 0)]), errors="coerce").fillna(0).iloc[0] if age_col_cur else 0,
            "executive": clean_text(r.get(exec_col_cur, "")) if exec_col_cur else "",
            "franchise": clean_text(r.get(franchise_col_cur, "")) if franchise_col_cur else "",
        }

    prev_map = {}
    if previous:
        prev_grouped, _, _ = active_pending_data(previous, reports)
        prev_tcol = get_ticket_col(prev_grouped)
        age_col_prev = find_col(prev_grouped, ["Days Old"])
        exec_col_prev = find_col(prev_grouped, ["Service Executive Name"])
        franchise_col_prev = find_col(prev_grouped, ["Franchise Name", "Franchise"])
        status_col_prev = find_col(prev_grouped, ["Status"])
        for _, r in prev_grouped.iterrows():
            key = clean_text(r.get(prev_tcol, ""))
            if not key:
                continue
            prev_map[key] = {
                "status": clean_text(r.get(status_col_prev, "")) if status_col_prev else "",
                "age": pd.to_numeric(pd.Series([r.get(age_col_prev, 0)]), errors="coerce").fillna(0).iloc[0] if age_col_prev else 0,
                "executive": clean_text(r.get(exec_col_prev, "")) if exec_col_prev else "",
                "franchise": clean_text(r.get(franchise_col_prev, "")) if franchise_col_prev else "",
            }

    prev_ids, cur_ids = set(prev_map), set(cur_map)
    records = []
    status_changed = age_increased = 0
    for key in sorted(prev_ids | cur_ids):
        prev_item = prev_map.get(key)
        cur_item = cur_map.get(key)
        if prev_item and cur_item:
            status_diff = bool(prev_item["status"] and cur_item["status"] and norm(prev_item["status"]) != norm(cur_item["status"]))
            age_diff = float(cur_item["age"]) > float(prev_item["age"])
            status_changed += int(status_diff)
            age_increased += int(age_diff)
            if status_diff and age_diff:
                movement = "Present; status changed and age increased"
            elif status_diff:
                movement = "Present; status changed"
            elif age_diff:
                movement = "Present; age increased"
            else:
                movement = "Present in both snapshots"
        elif cur_item:
            movement = "Newly present in latest pending report"
        else:
            movement = "No longer present in latest report (not proof of closure)"
        records.append({
            "Ticket No": key,
            "Movement": movement,
            "Previous Status": prev_item["status"] if prev_item else "",
            "Current Status": cur_item["status"] if cur_item else "",
            "Previous Days Old": prev_item["age"] if prev_item else "",
            "Current Days Old": cur_item["age"] if cur_item else "",
            "Previous Executive": prev_item["executive"] if prev_item else "",
            "Current Executive": cur_item["executive"] if cur_item else "",
            "Previous Franchise": prev_item["franchise"] if prev_item else "",
            "Current Franchise": cur_item["franchise"] if cur_item else "",
        })
    counts = {
        "new": len(cur_ids - prev_ids),
        "continuing": len(cur_ids & prev_ids),
        "missing": len(prev_ids - cur_ids),
        "status_changed": status_changed,
        "age_increased": age_increased,
    }
    return pd.DataFrame(records), counts

def _report_period_end_date(report: Dict[str, Any]) -> date:
    """Return the latest date the report claims to cover, distinct from export time."""
    period_end = report.get("period_end")
    if period_end is not None and not pd.isna(period_end):
        return pd.Timestamp(period_end).date()
    return pd.Timestamp(report.get("snapshot_dt")).date()


def _has_precise_export_time(report: Dict[str, Any]) -> bool:
    """Only a timestamp parsed from the filename is treated as a known export time."""
    return clean_text(report.get("snapshot_source")).lower() == "timestamp in filename"


def _report_sort_key(report: Dict[str, Any]) -> Tuple[Any, ...]:
    """Sort first by report end date, then by a real export timestamp when available."""
    end_day = _report_period_end_date(report)
    if _has_precise_export_time(report):
        time_key = pd.Timestamp(report.get("snapshot_dt"))
    else:
        # This is only an ordering fallback. UI selection warns/asks when this would
        # compete with another candidate on the same report date.
        time_key = pd.Timestamp(datetime.combine(end_day, time.max))
    return (end_day, time_key, int(report.get("upload_order", 0)))


def _is_before_report(candidate: Dict[str, Any], current: Dict[str, Any]) -> bool:
    """Whether a report can safely be treated as an earlier snapshot than current."""
    candidate_day = _report_period_end_date(candidate)
    current_day = _report_period_end_date(current)
    if candidate_day < current_day:
        return True
    if candidate_day > current_day:
        return False
    # Same-day ordering is reliable only when both reports have true export timestamps.
    if _has_precise_export_time(candidate) and _has_precise_export_time(current):
        return pd.Timestamp(candidate.get("snapshot_dt")) < pd.Timestamp(current.get("snapshot_dt"))
    return False


def _latest_by_type(
    reports: List[Dict[str, Any]],
    report_type: str,
    on_or_before: Optional[date] = None,
    selected_report_orders: Optional[Dict[str, int]] = None,
) -> Optional[Dict[str, Any]]:
    items = [r for r in reports if r["report_type"] == report_type]
    if on_or_before:
        eligible = [r for r in items if _report_period_end_date(r) <= on_or_before]
        # If no file ends on/before the selected date, use the latest available file
        # rather than silently pretending that it covers the selected date.
        if eligible:
            items = eligible
    if not items:
        return None
    selected_order = (selected_report_orders or {}).get(report_type)
    if selected_order is not None:
        chosen = [r for r in items if int(r.get("upload_order", -1)) == int(selected_order)]
        if chosen:
            return chosen[0]
    return max(items, key=_report_sort_key)



def _pendency_reason_outputs(pending_df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Summarise recorded pendency reasons from one row per active ticket.

    Shares use only tickets with a recorded reason as denominator. Reason coverage is
    shown separately against all active tickets so sparse data cannot be misrepresented.
    """
    total_tickets = int(len(pending_df)) if pending_df is not None else 0
    reason_col = find_col(
        pending_df if pending_df is not None else pd.DataFrame(),
        ["Pending Reason", "PendingReason", "Reason for Pendency", "Pendency Reason"],
    )
    empty = pd.DataFrame(columns=["Reason", "Tickets", "Share of Recorded Reasons (%)"])
    if pending_df is None or pending_df.empty or not reason_col:
        return empty, {
            "total_tickets": total_tickets, "recorded_reason_tickets": 0,
            "unrecorded_reason_tickets": total_tickets, "coverage_pct": 0.0,
            "reason_col_available": bool(reason_col),
        }
    values = pending_df[reason_col].map(clean_text)
    # Treat common placeholders as not recorded, not as real reasons.
    placeholders = {"", "-", "--", "na", "n/a", "none", "null", "not available", "not specified", "unknown"}
    valid_mask = values.map(lambda value: norm(value) not in {norm(x) for x in placeholders})
    valid = values[valid_mask]
    counts = valid.value_counts()
    recorded = int(valid_mask.sum())
    summary = pd.DataFrame({"Reason": counts.index.astype(str), "Tickets": counts.values.astype(int)})
    if not summary.empty and recorded:
        summary["Share of Recorded Reasons (%)"] = (summary["Tickets"] * 100.0 / recorded).round(1)
        summary = summary.sort_values(["Tickets", "Reason"], ascending=[False, True], kind="stable").reset_index(drop=True)
    else:
        summary = empty.copy()
    return summary, {
        "total_tickets": total_tickets,
        "recorded_reason_tickets": recorded,
        "unrecorded_reason_tickets": max(0, total_tickets - recorded),
        "coverage_pct": round(100.0 * recorded / total_tickets, 1) if total_tickets else 0.0,
        "reason_col_available": True,
    }


def analyze_reports(
    reports: List[Dict[str, Any]],
    analysis_date: Optional[date] = None,
    selected_report_orders: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    selected_report_orders = selected_report_orders or {}
    pending_reports = sorted(
        [r for r in reports if r["report_type"] == "pending_calls"],
        key=_report_sort_key,
    )
    if not pending_reports:
        raise ValueError("Upload at least one Pending Call report to generate the oldest-first pending-call list.")

    selected_pending_order = selected_report_orders.get("pending_calls")
    selected_pending = next(
        (r for r in pending_reports if selected_pending_order is not None and int(r.get("upload_order", -1)) == int(selected_pending_order)),
        None,
    )
    if analysis_date is None:
        analysis_date = _report_period_end_date(selected_pending or max(pending_reports, key=_report_sort_key))
    eligible_pending = [r for r in pending_reports if _report_period_end_date(r) <= analysis_date]
    if not eligible_pending:
        raise ValueError("No Pending Call snapshot exists on or before the selected analysis date. Choose a later date or upload an earlier report.")
    if selected_pending is not None and _report_period_end_date(selected_pending) <= analysis_date:
        current = selected_pending
    else:
        current = max(eligible_pending, key=_report_sort_key)
    # Do not compare two same-date reports when either lacks an actual export time;
    # their chronological order is unknown. Older dated snapshots remain available.
    prev_candidates = [r for r in pending_reports if _is_before_report(r, current)]
    previous = max(prev_candidates, key=_report_sort_key) if prev_candidates else None

    current_type_rows = filter_pending_call_types(current["df"])
    current_ticket_df, current_eligible_rows, current_exclusion_stats = active_pending_data(current, reports)
    # Keep recorded Pending Reason values for a transparent distribution chart. Blank reasons
    # are tracked separately and never assigned an inferred category.
    priorities = enrich_pending(current_ticket_df, current["snapshot_dt"])
    top20 = priorities.head(20).copy()
    executive_name_col = find_col(top20, ["Service Executive Name"])
    if len(top20):
        if executive_name_col:
            top20_executives = top20[executive_name_col].map(lambda v: clean_text(v) or "Unspecified")
        else:
            top20_executives = pd.Series(["Unspecified"] * len(top20), index=top20.index)
        top20_exec_counts = top20_executives.value_counts()
        top20["Executive % of Top 20"] = top20_executives.map(top20_exec_counts).astype(float) * 100.0 / len(top20)
        top20_executive_summary = pd.DataFrame({"Executive": top20_exec_counts.index, "Top 20 Calls": top20_exec_counts.values})
        top20_executive_summary["Share of Top 20 (%)"] = top20_executive_summary["Top 20 Calls"] * 100.0 / len(top20)
        top20_executive_summary = top20_executive_summary.sort_values(["Top 20 Calls", "Executive"], ascending=[False, True], kind="stable").reset_index(drop=True)
    else:
        top20["Executive % of Top 20"] = pd.Series(dtype=float)
        top20_executive_summary = pd.DataFrame(columns=["Executive", "Top 20 Calls", "Share of Top 20 (%)"])
    comparisons, movement_counts = snapshot_comparison(previous, current, reports)

    csat_report = _latest_by_type(reports, "customer_satisfaction", analysis_date, selected_report_orders)
    reg_report = _latest_by_type(reports, "calls_registered", analysis_date, selected_report_orders)
    csat = csat_report["df"].copy() if csat_report else pd.DataFrame()
    registered = reg_report["df"].copy() if reg_report else pd.DataFrame()

    # If Calls Registered spans multiple days, report only the selected analysis date when booking date exists.
    registered_for_day = registered.copy()
    reg_date_col = find_col(registered_for_day, ["Call Book Date"]) if not registered_for_day.empty else None
    registered_date_coverage = ""
    registered_date_covered = False
    if reg_report is not None:
        meta_start = reg_report["period_start"].date() if reg_report["period_start"] is not None else None
        meta_end = reg_report["period_end"].date() if reg_report["period_end"] is not None else None
        if meta_start and meta_end and meta_start <= analysis_date <= meta_end:
            registered_date_covered = True
    if reg_date_col:
        reg_dates = pd.to_datetime(registered_for_day[reg_date_col], errors="coerce", format="mixed")
        valid_dates = reg_dates.dropna()
        if not valid_dates.empty:
            min_date, max_date = valid_dates.min().date(), valid_dates.max().date()
            registered_date_coverage = f"Bookings {min_date:%d %b %Y}–{max_date:%d %b %Y}"
            if not registered_date_covered and not (reg_report and reg_report["period_start"] and reg_report["period_end"]):
                registered_date_covered = min_date <= analysis_date <= max_date
            mask = reg_dates.dt.date == analysis_date
            registered_for_day = registered_for_day.loc[mask.fillna(False)].copy() if registered_date_covered else registered_for_day.iloc[0:0].copy()
    elif not registered_date_covered:
        registered_for_day = registered_for_day.iloc[0:0].copy()

    csat_feedback_col = find_col(csat, ["FeedBack", "Feedback", "Feedback Score"]) if not csat.empty else None
    csat_summary = pd.DataFrame(columns=["Feedback", "Count", "Share %"])
    csat_metrics = {
        "total": 0, "source_rows": 0, "blank": 0,
        "happy": 0, "ok": 0, "not_happy": 0,
        "happy_pct": None, "not_happy_pct": None,
        "css_pct": None, "css_pct_rounded": None,
    }
    if csat_feedback_col:
        feedback = csat[csat_feedback_col].map(lambda v: clean_text(v).upper())
        # CSS denominator is actual nonblank feedback responses, not the number of happy responses.
        # Formula supplied by the user: (HAPPY - NOT HAPPY) / TOTAL FEEDBACK * 100.
        valid_feedback = feedback[feedback != ""]
        counts = valid_feedback.value_counts()
        total = int(len(valid_feedback))
        csat_summary = pd.DataFrame([
            {"Feedback": k, "Count": int(v), "Share %": round(100 * int(v) / total, 2) if total else 0}
            for k, v in counts.items()
        ])
        happy_count = int((valid_feedback == "HAPPY").sum())
        ok_count = int((valid_feedback == "OK").sum())
        not_happy_count = int((valid_feedback == "NOT HAPPY").sum())
        css_pct = (100 * (happy_count - not_happy_count) / total) if total else None
        csat_metrics = {
            "total": total,
            "source_rows": int(len(csat)),
            "blank": int((feedback == "").sum()),
            "happy": happy_count,
            "ok": ok_count,
            "not_happy": not_happy_count,
            "happy_pct": round(100 * happy_count / total, 2) if total else None,
            "not_happy_pct": round(100 * not_happy_count / total, 2) if total else None,
            "css_pct": round(css_pct, 2) if css_pct is not None else None,
            # Management image rounds CSS to the nearest whole percentage point.
            "css_pct_rounded": int(round(css_pct)) if css_pct is not None else None,
        }

    # Compare the latest Customer Satisfaction snapshot with the previous available snapshot.
    # Shares show feedback mix; CSS itself uses (Happy - Not Happy) / total nonblank feedback × 100.
    csat_items = sorted(
        [r for r in reports if r["report_type"] == "customer_satisfaction"
         and _report_period_end_date(r) <= analysis_date],
        key=_report_sort_key,
    )
    csat_previous_report = None
    if csat_report is not None:
        prior_items = [r for r in csat_items if _is_before_report(r, csat_report)]
        if prior_items:
            csat_previous_report = max(prior_items, key=_report_sort_key)
    csat_comparison = pd.DataFrame(columns=["Feedback", "Previous Count", "Current Count", "Count Change", "Previous Share %", "Current Share %", "Share Change (pp)"])
    csat_movement = {"previous_rows": None, "current_rows": len(csat), "new_tickets": None, "missing_tickets": None, "happy_delta_pp": None, "not_happy_delta_pp": None, "css_delta_pp": None}
    if csat_previous_report is not None:
        prev_df = csat_previous_report["df"].copy()
        prev_fb_col = find_col(prev_df, ["FeedBack", "Feedback", "Feedback Score"])
        current_fb = csat[csat_feedback_col].map(lambda v: clean_text(v).upper()) if csat_feedback_col else pd.Series(dtype=str)
        prev_fb = prev_df[prev_fb_col].map(lambda v: clean_text(v).upper()) if prev_fb_col else pd.Series(dtype=str)
        prev_valid = prev_fb[prev_fb != ""]
        current_valid = current_fb[current_fb != ""]
        prev_total, curr_total = len(prev_valid), len(current_valid)
        prev_counts, curr_counts = prev_valid.value_counts(), current_valid.value_counts()
        feedback_labels = sorted(set(prev_counts.index.tolist()) | set(curr_counts.index.tolist()))
        comp_rows = []
        for label in feedback_labels:
            p_count, c_count = int(prev_counts.get(label, 0)), int(curr_counts.get(label, 0))
            p_share = round(100 * p_count / prev_total, 2) if prev_total else 0.0
            c_share = round(100 * c_count / curr_total, 2) if curr_total else 0.0
            comp_rows.append({"Feedback": label or "Blank", "Previous Count": p_count, "Current Count": c_count,
                              "Count Change": c_count - p_count, "Previous Share %": p_share,
                              "Current Share %": c_share, "Share Change (pp)": round(c_share - p_share, 2)})
        csat_comparison = pd.DataFrame(comp_rows).sort_values("Current Count", ascending=False).reset_index(drop=True) if comp_rows else pd.DataFrame(columns=["Feedback", "Previous Count", "Current Count", "Count Change", "Previous Share %", "Current Share %", "Share Change (pp)"])
        prev_ticket_col, curr_ticket_col = get_ticket_col(prev_df), get_ticket_col(csat)
        prev_ids = {clean_text(v) for v in prev_df[prev_ticket_col].tolist() if clean_text(v)} if prev_ticket_col else set()
        curr_ids = {clean_text(v) for v in csat[curr_ticket_col].tolist() if clean_text(v)} if curr_ticket_col else set()
        prev_happy_share = 100 * (prev_valid == "HAPPY").sum() / prev_total if prev_total else None
        curr_happy_share = 100 * (current_valid == "HAPPY").sum() / curr_total if curr_total else None
        prev_not_happy_share = 100 * (prev_valid == "NOT HAPPY").sum() / prev_total if prev_total else None
        curr_not_happy_share = 100 * (current_valid == "NOT HAPPY").sum() / curr_total if curr_total else None
        prev_css = 100 * (((prev_valid == "HAPPY").sum()) - ((prev_valid == "NOT HAPPY").sum())) / prev_total if prev_total else None
        curr_css = 100 * (((current_valid == "HAPPY").sum()) - ((current_valid == "NOT HAPPY").sum())) / curr_total if curr_total else None
        csat_movement = {
            "previous_rows": prev_total, "current_rows": curr_total,
            "new_tickets": len(curr_ids - prev_ids), "missing_tickets": len(prev_ids - curr_ids),
            "happy_delta_pp": round(round(curr_happy_share, 2) - round(prev_happy_share, 2), 2) if prev_happy_share is not None and curr_happy_share is not None else None,
            "not_happy_delta_pp": round(round(curr_not_happy_share, 2) - round(prev_not_happy_share, 2), 2) if prev_not_happy_share is not None and curr_not_happy_share is not None else None,
            "css_delta_pp": round(curr_css - prev_css, 2) if prev_css is not None and curr_css is not None else None,
        }

    def count_distinct(df: pd.DataFrame) -> int:
        c = get_ticket_col(df)
        return len({clean_text(v) for v in df[c].tolist() if clean_text(v)}) if c else len(df)

    reg_ticket_col = get_ticket_col(registered_for_day) if not registered_for_day.empty else None
    registered_count = count_distinct(registered_for_day) if registered_date_covered else None
    status_col = find_col(priorities, ["Status"])
    status_summary = priorities[status_col].map(lambda v: clean_text(v) or "Unspecified").value_counts().rename_axis("Status").reset_index(name="Unique Tickets") if status_col else pd.DataFrame(columns=["Status", "Unique Tickets"])
    call_type_col = find_col(priorities, ["Call Type"])
    call_type_summary = priorities[call_type_col].map(lambda v: clean_text(v) or "Unspecified").value_counts().rename_axis("Call Type").reset_index(name="Unique Tickets") if call_type_col else pd.DataFrame(columns=["Call Type", "Unique Tickets"])
    pendency_reason_summary, pendency_reason_metrics = _pendency_reason_outputs(priorities)
    franchise_col = find_col(priorities, ["Franchise Name"])
    if franchise_col:
        _fr = priorities.copy()
        _fr["Franchise"] = _fr[franchise_col].map(lambda v: clean_text(v) or "Unspecified")
        franchise_summary = _fr.groupby("Franchise", dropna=False).agg(
            **{"Pending Tickets": ("Ticket No", "count"),
               "Aged >2 Days": ("Days Old", lambda s: int((pd.to_numeric(s, errors="coerce").fillna(0) > 2).sum())),
               "Aged 7+ Days": ("Days Old", lambda s: int((pd.to_numeric(s, errors="coerce").fillna(0) >= 7).sum())),
               "Escalated Tickets": ("Has Escalation", lambda s: int(s.astype(bool).sum())),
               "Repeat-call Flags": ("Is Repeat Call", lambda s: int(s.astype(bool).sum())),
               "Average Age (days)": ("Days Old", lambda s: round(pd.to_numeric(s, errors="coerce").fillna(0).mean(), 1)),
            }
        ).reset_index().sort_values(["Aged >2 Days", "Pending Tickets"], ascending=False).reset_index(drop=True)
    else:
        franchise_summary = pd.DataFrame(columns=["Franchise", "Pending Tickets", "Aged >2 Days", "Aged 7+ Days", "Escalated Tickets", "Repeat-call Flags", "Average Age (days)"])
    exec_col = find_col(priorities, ["Service Executive Name"])
    if exec_col:
        _work = priorities.copy()
        _work["Executive"] = _work[exec_col].map(lambda v: clean_text(v) or "Unspecified")
        executive_summary = _work.groupby("Executive", dropna=False).agg(
            **{"Pending Tickets": ("Ticket No", "count"),
               "Aged 7+ Days": ("Days Old", lambda s: int((pd.to_numeric(s, errors="coerce").fillna(0) >= 7).sum())),
               "Escalated Tickets": ("Has Escalation", lambda s: int(s.astype(bool).sum())),
               "Repeat-call Flags": ("Is Repeat Call", lambda s: int(s.astype(bool).sum())),
               "Overdue Scheduled": ("Scheduled Overdue", lambda s: int(s.astype(bool).sum())),
               "Average Age (days)": ("Days Old", lambda s: round(pd.to_numeric(s, errors="coerce").fillna(0).mean(), 1)),
            }
        ).reset_index().sort_values(["Pending Tickets", "Escalated Tickets"], ascending=False).reset_index(drop=True)
    else:
        executive_summary = pd.DataFrame(columns=["Executive", "Pending Tickets", "Aged 7+ Days", "Escalated Tickets", "Repeat-call Flags", "Overdue Scheduled", "Average Age (days)"])
    age_summary = pd.cut(priorities["Days Old"], bins=[-1, 0, 2, 6, 13, 29, float("inf")], labels=["0 days", "1–2 days", "3–6 days", "7–13 days", "14–29 days", "30+ days"]).value_counts(sort=False).rename_axis("Age Bucket").reset_index(name="Unique Tickets")
    escalated = int(priorities["Has Escalation"].astype(bool).sum())
    repeat_calls = int(priorities["Is Repeat Call"].astype(bool).sum())
    overdue_scheduled = int(priorities["Scheduled Overdue"].sum())
    older_7 = int((priorities["Days Old"] >= 7).sum())

    # Use closed timestamps only where the uploaded Calls Registered report actually contains them.
    closed_count = None
    closed_time_col = find_col(registered_for_day, ["CLOSED TIME", "Closed Time"]) if not registered_for_day.empty else None
    if registered_count is not None and closed_time_col:
        closed_ts = pd.to_datetime(registered_for_day[closed_time_col], errors="coerce", format="mixed")
        closed_ts = closed_ts.where(closed_ts.dt.year > 1900)
        closed_count = int((closed_ts.dt.date == analysis_date).sum())

    # Data-quality indicators use the final active queue, while ticketless source rows remain flagged.
    missing_exec_count = int(priorities["Missing Executive"].sum()) if "Missing Executive" in priorities else 0
    missing_tech_count = int(priorities["Missing Technician"].sum()) if "Missing Technician" in priorities else 0
    missing_schedule_count = int(priorities["Scheduled Date"].map(lambda v: not bool(clean_text(v))).sum()) if "Scheduled Date" in priorities else len(priorities)
    current_raw_ticket_col = get_ticket_col(current_type_rows)
    missing_ticket_count = int(current_type_rows[current_raw_ticket_col].map(lambda v: not bool(clean_text(v))).sum()) if current_raw_ticket_col else len(current_type_rows)
    source_row_duplicates = max(0, len(current_eligible_rows) - len(priorities))
    excluded_pending_rows = int(excluded_pending_call_type_mask(current["df"]).sum())
    excluded_pending_tickets = distinct_ticket_count(current["df"].loc[excluded_pending_call_type_mask(current["df"])])
    data_quality_metrics = {
        "missing_executive": missing_exec_count,
        "missing_technician": missing_tech_count,
        "missing_schedule": missing_schedule_count,
        "missing_ticket_number": missing_ticket_count,
        "rows_beyond_distinct_tickets": source_row_duplicates,
        "soft_closed_tickets_excluded": current_exclusion_stats["soft_closed_tickets"],
        "negative_response_tickets_excluded": current_exclusion_stats["negative_response_tickets"],
        "current_source_distinct_tickets": current_exclusion_stats["source_distinct_tickets"],
    }

    # Data-quality / provenance notes. Eligible counts mean active tickets after queue reconciliation.
    dq_rows = []
    for r in reports:
        if r["report_type"] == "pending_calls":
            source_df = r["df"]
            unique = count_distinct(source_df)
            active_grouped, active_rows, report_exclusions = active_pending_data(r, reports)
            all_ids = _group_ticket_ids(summarize_pending_rows(filter_pending_call_types(source_df)))
            active_ids = report_exclusions["active_ticket_ids"]
            dq_rows.append({
                "File": r["filename"], "Report Type": r["report_type"], "Source Rows": len(source_df),
                "Distinct Tickets": unique, "Eligible Rows": len(active_rows),
                "Eligible Tickets": len(active_grouped),
                "Excluded Rows": max(0, len(source_df) - len(active_rows)),
                "Excluded Tickets": len(all_ids - active_ids),
                "Rows beyond distinct tickets": max(0, len(active_rows) - len(active_grouped)),
                "Period": r["metadata"], "Snapshot Used For": r["snapshot_dt"].strftime("%Y-%m-%d %H:%M"),
                "Timestamp Basis": r["snapshot_source"],
                "Note": (f"Active queue excludes {report_exclusions['soft_closed_tickets']} ticket(s) soft-closed by this snapshot and "
                         f"{report_exclusions['negative_response_tickets']} ticket(s) marked Negative Response from Customer; "
                         "Courtesy/Courtesy Visit and Dealer Defective call types are also excluded."),
            })
        elif r["report_type"] == "soft_closure":
            dq_rows.append({
                "File": r["filename"], "Report Type": r["report_type"], "Source Rows": len(r["df"]),
                "Distinct Tickets": count_distinct(r["df"]), "Eligible Rows": len(r["df"]),
                "Eligible Tickets": count_distinct(r["df"]), "Excluded Rows": 0, "Excluded Tickets": 0,
                "Rows beyond distinct tickets": max(0, len(r["df"]) - count_distinct(r["df"])),
                "Period": r["metadata"], "Snapshot Used For": r["snapshot_dt"].strftime("%Y-%m-%d %H:%M"),
                "Timestamp Basis": r["snapshot_source"], "Note": "Used to remove tickets with soft-closure event timestamps at or before each Pending Call snapshot.",
            })
        else:
            dq_rows.append({
                "File": r["filename"], "Report Type": r["report_type"], "Source Rows": len(r["df"]),
                "Distinct Tickets": count_distinct(r["df"]), "Eligible Rows": len(r["df"]),
                "Eligible Tickets": count_distinct(r["df"]), "Excluded Rows": 0, "Excluded Tickets": 0,
                "Rows beyond distinct tickets": max(0, len(r["df"]) - count_distinct(r["df"])),
                "Period": r["metadata"], "Snapshot Used For": r["snapshot_dt"].strftime("%Y-%m-%d %H:%M"),
                "Timestamp Basis": r["snapshot_source"], "Note": "Source report classified from its column headers.",
            })
    dq_rows += [
        {"File": "Analysis rule", "Report Type": "Age-only ordering", "Source Rows": "", "Distinct Tickets": "", "Eligible Rows": "", "Eligible Tickets": "", "Excluded Rows": "", "Excluded Tickets": "", "Rows beyond distinct tickets": "", "Period": "Days Old descending; oldest first", "Snapshot Used For": "", "Timestamp Basis": "Days Old column; Call Book Date used only if Days Old is blank", "Note": "The only call-ordering key is age. Equal-age tickets retain source order."},
        {"File": "Analysis rule", "Report Type": "Active pending queue", "Source Rows": "", "Distinct Tickets": "", "Eligible Rows": "", "Eligible Tickets": "", "Excluded Rows": "", "Excluded Tickets": "", "Rows beyond distinct tickets": "", "Period": "Active calls only", "Snapshot Used For": "", "Timestamp Basis": "Soft Closure Date / ClosedDate compared with Pending Call snapshot time", "Note": "Exclude Courtesy and Dealer Defective types, tickets soft-closed by snapshot time, and tickets marked Negative Response from Customer."},
        {"File": "Analysis rule", "Report Type": "Snapshot comparison", "Source Rows": "", "Distinct Tickets": "", "Eligible Rows": "", "Eligible Tickets": "", "Excluded Rows": "", "Excluded Tickets": "", "Rows beyond distinct tickets": "", "Period": "New / continuing / no longer present / status changed / age increased", "Snapshot Used For": "", "Timestamp Basis": "Ticket-number matching on the active queue for each snapshot", "Note": "A ticket absent from a later Pending Call export is not automatically labelled closed."},
    ]

    return {
        "analysis_date": analysis_date,
        "selected_report_orders": dict(selected_report_orders),
        "reports": reports,
        "pending_reports": pending_reports,
        "current_pending_report": current,
        "previous_pending_report": previous,
        "current_pending_rows": len(current_eligible_rows),
        "current_unique_tickets": len(priorities),
        "current_source_distinct_tickets": current_exclusion_stats["source_distinct_tickets"],
        "soft_closed_tickets_excluded": current_exclusion_stats["soft_closed_tickets"],
        "negative_response_tickets_excluded": current_exclusion_stats["negative_response_tickets"],
        "prior_unique_tickets": len(active_pending_data(previous, reports)[0]) if previous else None,
        "excluded_pending_rows": excluded_pending_rows,
        "excluded_pending_tickets": excluded_pending_tickets,
        "priorities": priorities,
        "top20": top20,
        "top20_executive_summary": top20_executive_summary,
        "comparison": comparisons,
        "movement_counts": movement_counts,
        "csat_report": csat_report,
        "csat": csat,
        "csat_summary": csat_summary,
        "csat_metrics": csat_metrics,
        "csat_previous_report": csat_previous_report,
        "csat_comparison": csat_comparison,
        "csat_movement": csat_movement,
        "registered_report": reg_report,
        "registered": registered,
        "registered_for_day": registered_for_day,
        "registered_count": registered_count,
        "registered_date_coverage": registered_date_coverage,
        "closed_count": closed_count,
        "status_summary": status_summary,
        "call_type_summary": call_type_summary,
        "pendency_reason_summary": pendency_reason_summary,
        "pendency_reason_metrics": pendency_reason_metrics,
        "franchise_summary": franchise_summary,
        "executive_summary": executive_summary,
        "age_summary": age_summary,
        "escalated": escalated,
        "repeat_calls": repeat_calls,
        "overdue_scheduled": overdue_scheduled,
        "older_7": older_7,
        "data_quality_metrics": data_quality_metrics,
        "data_quality": pd.DataFrame(dq_rows),
    }


def _filter_to_scope(df: pd.DataFrame, scope_mode: str, selection: str) -> Tuple[pd.DataFrame, bool]:
    """Filter a source frame to a selected franchise or service executive.

    Returns (filtered_frame, key_supported). If the source does not have the matching
    column, returns an empty frame so branch-wide values are never mislabelled as scoped.
    """
    if scope_mode == "branch":
        # Callers treat source frames as read-only; avoid deep-copying thousands of
        # CSAT rows on every view render. A branch-scoped priorities frame is copied
        # by scope_analysis before it is modified.
        return df, True
    aliases = ( ["Franchise Name", "FrName", "Franchise"] if scope_mode == "franchise"
                else ["Service Executive Name", "Branch Exe Name", "Branch Executive Name", "Executive Name"] )
    col = find_col(df, aliases)
    if col is None:
        return df.iloc[0:0].copy(), False
    target = norm(selection)
    return df.loc[df[col].map(norm) == target].copy(), True


def _feedback_outputs(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    feedback_col = find_col(df, ["FeedBack", "Feedback", "Feedback Score"]) if not df.empty else None
    summary = pd.DataFrame(columns=["Feedback", "Count", "Share %"])
    metrics = {"total": 0, "source_rows": len(df), "blank": 0, "happy": 0, "ok": 0,
               "not_happy": 0, "happy_pct": None, "not_happy_pct": None,
               "css_pct": None, "css_pct_rounded": None}
    if feedback_col is None:
        return summary, metrics
    feedback = df[feedback_col].map(lambda v: clean_text(v).upper())
    valid = feedback[feedback != ""]
    total = len(valid)
    counts = valid.value_counts()
    summary = pd.DataFrame([
        {"Feedback": label, "Count": int(count), "Share %": round(100 * int(count) / total, 2) if total else 0}
        for label, count in counts.items()
    ])
    happy = int((valid == "HAPPY").sum())
    ok = int((valid == "OK").sum())
    not_happy = int((valid == "NOT HAPPY").sum())
    css = 100 * (happy - not_happy) / total if total else None
    metrics = {
        "total": total, "source_rows": len(df), "blank": int((feedback == "").sum()),
        "happy": happy, "ok": ok, "not_happy": not_happy,
        "happy_pct": round(100 * happy / total, 2) if total else None,
        "not_happy_pct": round(100 * not_happy / total, 2) if total else None,
        "css_pct": round(css, 2) if css is not None else None,
        "css_pct_rounded": int(round(css)) if css is not None else None,
    }
    return summary, metrics


def _simple_counts(df: pd.DataFrame, source_col_aliases: Sequence[str], output_col: str) -> pd.DataFrame:
    col = find_col(df, source_col_aliases) if not df.empty else None
    if col is None:
        return pd.DataFrame(columns=[output_col, "Unique Tickets"])
    vals = df[col].map(lambda v: clean_text(v) or "Unspecified")
    return vals.value_counts().rename_axis(output_col).reset_index(name="Unique Tickets")


def _is_soft_closure_source(report: Dict[str, Any]) -> bool:
    """Identify a Soft Closure export by report type, embedded title, filename or row signature.

    Some CRM downloads are renamed to generic filenames, and date-range metadata alone
    does not identify the report. This fallback inspects the original workbook title and
    the actual status values so the SA graph can still use the correct source.
    """
    if report.get("report_type") == "soft_closure":
        return True
    hint = norm(" ".join([clean_text(report.get("filename", "")), clean_text(report.get("metadata", "")), clean_text(report.get("report_title", ""))]))
    if "softclosure" in hint or ("soft" in hint and "closure" in hint):
        return True
    df = report.get("df", pd.DataFrame())
    if df is None or df.empty:
        return False
    status_col = find_col(df, ["Status", "Call Status"])
    event_col = find_col(df, ["Soft Closure Date", "Soft Closed Date", "SoftClosureDate", "Closure Date"])
    ticket_col = get_ticket_col(df)
    technician_col = find_col(df, ["Technician Name", "Service Ambassador", "Service Ambassador Name", "Technician"])
    if not (status_col and event_col and ticket_col and technician_col):
        return False
    statuses = df[status_col].map(norm)
    # This specific source status is strong evidence; don't classify a general call
    # register as Soft Closure merely because it includes a Soft Closure Date column.
    return bool(statuses.map(lambda value: "softclosure" in value).any())


def _soft_closure_candidates_for_date(
    reports: List[Dict[str, Any]], analysis_date: date, selected_report_order: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Return closure reports with event evidence for the selected date, even if their
    declared report period is inconsistent with the dated rows. Candidates with actual
    closure events on the date are preferred over metadata-only candidates.
    """
    candidates = []
    for report in reports or []:
        if not _is_soft_closure_source(report):
            continue
        df = report.get("df", pd.DataFrame())
        event_col = find_col(df, ["Soft Closure Date", "Soft Closed Date", "SoftClosureDate", "Closure Date"]) if not df.empty else None
        closed_col = find_col(df, ["ClosedDate", "Closed Date", "Closed Time", "Closed Timestamp"]) if not df.empty else None
        event_ts = (_valid_datetime_series(df[event_col]) if event_col else
                    pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]"))
        if closed_col:
            event_ts = event_ts.fillna(_valid_datetime_series(df[closed_col]))
        status_col = find_col(df, ["Status", "Call Status"]) if not df.empty else None
        if status_col:
            statuses = df[status_col].map(norm)
            is_completed = statuses.map(lambda v: ("resolved" in v) or ("closed" in v) or ("completed" in v))
            event_on_date = bool((is_completed & event_ts.dt.date.eq(analysis_date)).any())
        else:
            event_on_date = False
        start, end = report.get("period_start"), report.get("period_end")
        metadata_covers = bool(start is not None and end is not None and start.date() <= analysis_date <= end.date())
        if event_on_date or metadata_covers:
            candidates.append((report, event_on_date))
    if not candidates:
        return []
    # Prefer candidates with actual closed/completed rows on the selected day. If a
    # specific report order was confirmed, honor it only when it has those date rows
    # or its declared period covers the date.
    if selected_report_order is not None:
        selected = [r for r, has_event in candidates if int(r.get("upload_order", -1)) == int(selected_report_order)]
        if selected:
            selected_has_event = [r for r, has_event in candidates if int(r.get("upload_order", -1)) == int(selected_report_order) and has_event]
            if selected_has_event:
                return selected_has_event
            return selected
    event_candidates = [r for r, has_event in candidates if has_event]
    pool = event_candidates or [r for r, _ in candidates]
    return sorted(pool, key=_report_sort_key)


def _completion_timestamp_series(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """Return (completion timestamps, completed-status mask) for supported exports.

    Calls Registered exports contain Technician Name, Status, Soft Closure Date and
    CLOSED TIME. A separate Soft Closure export may contain the same event fields.
    Prefer Soft Closure Date for resolved/soft-closed tickets, and CLOSED TIME for
    Closed/Completed tickets, with the other field as a fallback. 1900 placeholders
    are removed by _valid_datetime_series.
    """
    status_col = find_col(df, ["Status", "Call Status"]) if df is not None and not df.empty else None
    if not status_col:
        return (pd.Series(pd.NaT, index=df.index if df is not None else [], dtype="datetime64[ns]"),
                pd.Series(False, index=df.index if df is not None else [], dtype=bool))
    status = df[status_col].map(norm)
    soft_col = find_col(df, ["Soft Closure Date", "Soft Closed Date", "SoftClosureDate", "Closure Date"])
    closed_col = find_col(df, ["CLOSED TIME", "Closed Time", "ClosedDate", "Closed Date", "Closed Timestamp"])
    soft_ts = (_valid_datetime_series(df[soft_col]) if soft_col else
               pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]"))
    closed_ts = (_valid_datetime_series(df[closed_col]) if closed_col else
                 pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]"))
    is_soft = status.map(lambda value: "resolved" in value or "softclosure" in value)
    event_ts = closed_ts.copy()
    soft_status_idx = is_soft[is_soft].index
    other_status_idx = is_soft[~is_soft].index
    if len(soft_status_idx):
        event_ts.loc[soft_status_idx] = soft_ts.loc[soft_status_idx].fillna(closed_ts.loc[soft_status_idx])
    if len(other_status_idx):
        event_ts.loc[other_status_idx] = closed_ts.loc[other_status_idx].fillna(soft_ts.loc[other_status_idx])
    completed_mask = status.map(lambda value: ("resolved" in value) or ("closed" in value) or ("completed" in value))
    return event_ts, completed_mask


def _daily_completed_rows(df: pd.DataFrame, analysis_date: date) -> pd.DataFrame:
    """Distinct-source rows eligible for daily completion counting on analysis_date."""
    if df is None or df.empty:
        return pd.DataFrame()
    ticket_col = get_ticket_col(df)
    if not ticket_col:
        return pd.DataFrame()
    event_ts, completed_mask = _completion_timestamp_series(df)
    day_mask = event_ts.dt.date.eq(analysis_date)
    out = df.loc[completed_mask & day_mask].copy()
    if out.empty:
        return out
    call_type_col = find_col(out, ["Call Type", "CallType"])
    if call_type_col:
        out = out.loc[~excluded_pending_call_type_mask(out)].copy()
    if out.empty:
        return out
    # Preserve the timestamp calculated against the source rows after filtering.
    out["__completion_timestamp"] = event_ts.loc[out.index]
    out["__ticket_key"] = out[ticket_col].map(clean_text)
    out = out.loc[out["__ticket_key"].ne("")].copy()
    return out


def _productivity_source_for_date(
    reports: List[Dict[str, Any]], analysis_date: date,
    selected_soft_order: Optional[int] = None,
    selected_calls_order: Optional[int] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Choose a dated completion source, preferring Soft Closure then Calls Registered.

    The three-file upload workflow does not include a separate Soft Closure workbook.
    Calls Registered has the required Technician Name, Status, Soft Closure Date and
    CLOSED TIME columns, so it is used as a fallback when it contains dated completion
    rows. This source may not include calls booked before its report period; the note
    returned with the chart makes that coverage limitation explicit.
    """
    for report_type, selected_order in (("soft_closure", selected_soft_order), ("calls_registered", selected_calls_order)):
        candidates = []
        for report in reports or []:
            if report.get("report_type") != report_type:
                continue
            frame = report.get("df", pd.DataFrame())
            if not frame.empty and not _daily_completed_rows(frame, analysis_date).empty:
                candidates.append(report)
        if not candidates:
            continue
        if selected_order is not None:
            matched = [r for r in candidates if int(r.get("upload_order", -1)) == int(selected_order)]
            if matched:
                return matched[0], report_type
        return max(candidates, key=_report_sort_key), report_type
    return None, None


def _service_ambassador_productivity(
    reports: List[Dict[str, Any]], franchise_name: str, analysis_date: date,
    selected_report_order: Optional[int] = None,
    selected_calls_report_order: Optional[int] = None,
) -> Tuple[pd.DataFrame, str]:
    """Daily distinct completed calls per technician for a single franchise."""
    empty = pd.DataFrame(columns=["Service Ambassador", "Calls Done", "Share (%)"])
    report, source_type = _productivity_source_for_date(
        reports, analysis_date, selected_report_order, selected_calls_report_order
    )
    if report is None:
        if any(r.get("report_type") in {"soft_closure", "calls_registered"} for r in reports or []):
            return empty, f"No completed rows with a valid timestamp were found for {analysis_date:%d %b %Y}."
        return empty, "Upload Calls Registered (or Soft Closure) with Technician Name, Status and closure timestamps to calculate daily SA productivity."
    df = report.get("df", pd.DataFrame()).copy()
    franchise_col = find_col(df, ["Franchise Name", "FrName", "Franchise"])
    tech_col = find_col(df, ["Technician Name", "Service Ambassador", "Service Ambassador Name", "Technician"])
    if not franchise_col or not tech_col:
        return empty, "The completion report needs franchise and technician columns for daily productivity."
    completed_rows = _daily_completed_rows(df, analysis_date)
    if completed_rows.empty:
        return empty, f"No completed tickets with valid timestamps were found for {analysis_date:%d %b %Y}."
    completed_rows = completed_rows.loc[completed_rows[franchise_col].map(norm) == norm(franchise_name)].copy()
    if completed_rows.empty:
        return empty, "No completed rows for this franchise were found in the selected source report."
    completed_rows["__technician"] = completed_rows[tech_col].map(lambda value: clean_text(value) or "Unspecified SA")
    completed_rows = completed_rows.sort_values("__completion_timestamp", kind="stable").drop_duplicates("__ticket_key", keep="last")
    summary = completed_rows.groupby("__technician", dropna=False).agg(**{"Calls Done": ("__ticket_key", "nunique")}).reset_index()
    summary = summary.rename(columns={"__technician": "Service Ambassador"})
    total = int(summary["Calls Done"].sum())
    summary["Share (%)"] = (summary["Calls Done"] * 100.0 / total).round(1) if total else 0.0
    summary = summary.sort_values(["Calls Done", "Service Ambassador"], ascending=[False, True], kind="stable").reset_index(drop=True)
    source_label = "Soft Closure report" if source_type == "soft_closure" else "Calls Registered report"
    return summary, f"Distinct completed tickets on {analysis_date:%d %b %Y} · source: {source_label}"


def _service_ambassador_target_hierarchy(
    reports: List[Dict[str, Any]],
    analysis_date: date,
    pending_roster: pd.DataFrame,
    scope_mode: str = "branch",
    selection: str = "",
    selected_report_order: Optional[int] = None,
    selected_calls_report_order: Optional[int] = None,
    daily_target: int = 6,
) -> Tuple[pd.DataFrame, str]:
    """Daily SA productivity grouped Executive -> Franchise -> SA.

    Uses a dated Soft Closure report when available; otherwise derives completions from
    Calls Registered rows by using Technician Name, Status, Soft Closure Date and CLOSED
    TIME. Only distinct tickets with a valid completion timestamp on analysis_date count.
    The Calls Registered fallback is limited to tickets present in that registration
    report's period, so older-booked calls closed on the day may not be represented.
    """
    columns = [
        "Executive", "Franchise", "Service Ambassador", "Calls Done", "Daily Target",
        "Target Achievement (%)", "Calls Short of Target", "Franchise Calls Done",
        "Franchise SA Count", "Franchise Target Achievement (%)", "Executive Calls Done",
        "Executive SA Count", "Executive Target Achievement (%)",
    ]
    empty = pd.DataFrame(columns=columns)
    mode = (scope_mode or "branch").strip().lower()
    report, source_type = _productivity_source_for_date(
        reports, analysis_date, selected_report_order, selected_calls_report_order
    )
    if report is None:
        if any(r.get("report_type") in {"soft_closure", "calls_registered"} for r in reports or []):
            return empty, f"No completed rows with a valid closure timestamp were found for {analysis_date:%d %b %Y}."
        return empty, "Upload Calls Registered (or Soft Closure) with Technician Name, Status and closure timestamps for the selected date."
    source = report.get("df", pd.DataFrame()).copy()
    if source.empty:
        return empty, "The selected completion report contains no rows."

    ticket_col = get_ticket_col(source)
    franchise_col = find_col(source, ["Franchise Name", "FrName", "Franchise"])
    executive_col = find_col(source, ["Branch Exe Name", "Service Executive Name", "Branch Executive Name", "Executive Name"])
    technician_col = find_col(source, ["Technician Name", "Service Ambassador", "Service Ambassador Name", "Technician"])
    if not ticket_col or not franchise_col or not technician_col:
        return empty, "The completion report needs ticket, franchise and technician columns for daily productivity."

    completion_rows = _daily_completed_rows(source, analysis_date)
    if completion_rows.empty:
        return empty, f"No resolved/closed tickets with valid completion timestamps were found on {analysis_date:%d %b %Y}."

    def row_in_scope(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame
        if mode == "franchise":
            col = find_col(out, ["Franchise Name", "FrName", "Franchise"])
            if col is None:
                return out.iloc[0:0].copy()
            out = out.loc[out[col].map(norm) == norm(selection)]
        elif mode == "executive":
            col = find_col(out, ["Branch Exe Name", "Service Executive Name", "Branch Executive Name", "Executive Name"])
            if col is None:
                return out.iloc[0:0].copy()
            out = out.loc[out[col].map(norm) == norm(selection)]
        return out

    completion_rows = row_in_scope(completion_rows).copy()
    if completion_rows.empty:
        return empty, f"No completed tickets matched this {mode} selection on {analysis_date:%d %b %Y}."
    completion_rows["__executive"] = completion_rows[executive_col].map(lambda v: clean_text(v) or "Unspecified executive") if executive_col else "Unspecified executive"
    completion_rows["__franchise"] = completion_rows[franchise_col].map(lambda v: clean_text(v) or "Unspecified franchise")
    completion_rows["__sa"] = completion_rows[technician_col].map(lambda v: clean_text(v) or "Unspecified SA")
    completion_rows = completion_rows.sort_values("__completion_timestamp", kind="stable").drop_duplicates("__ticket_key", keep="last")

    # Productivity roster is derived from SAs with a recorded completion on this date.
    # Zero-completion/absence cannot be inferred without a separate daily SA roster.
    grouped = completion_rows.groupby(["__executive", "__franchise", "__sa"], dropna=False).agg(
        **{"Calls Done": ("__ticket_key", "nunique")}
    ).reset_index().rename(columns={
        "__executive": "Executive", "__franchise": "Franchise", "__sa": "Service Ambassador"
    })
    result = grouped.copy()
    result["Daily Target"] = int(daily_target)
    result["Target Achievement (%)"] = (result["Calls Done"] * 100.0 / daily_target).round(1)
    result["Calls Short of Target"] = (daily_target - result["Calls Done"]).clip(lower=0).astype(int)

    fr_roll = result.groupby(["Executive", "Franchise"], dropna=False).agg(
        **{"Franchise Calls Done": ("Calls Done", "sum"), "Franchise SA Count": ("Service Ambassador", "nunique")}
    ).reset_index()
    fr_roll["Franchise Target Achievement (%)"] = (
        fr_roll["Franchise Calls Done"] / (fr_roll["Franchise SA Count"] * daily_target).replace(0, pd.NA) * 100
    ).fillna(0).round(1)
    ex_roll = result.groupby("Executive", dropna=False).agg(
        **{"Executive Calls Done": ("Calls Done", "sum"), "Executive SA Count": ("Service Ambassador", "nunique")}
    ).reset_index()
    ex_roll["Executive Target Achievement (%)"] = (
        ex_roll["Executive Calls Done"] / (ex_roll["Executive SA Count"] * daily_target).replace(0, pd.NA) * 100
    ).fillna(0).round(1)
    result = result.merge(fr_roll, on=["Executive", "Franchise"], how="left").merge(ex_roll, on="Executive", how="left")
    result = result[columns].sort_values(
        ["Executive", "Franchise", "Calls Done", "Service Ambassador"],
        ascending=[True, True, False, True], kind="stable"
    ).reset_index(drop=True)

    if source_type == "calls_registered":
        note = (f"Distinct tickets with a resolved/closed status and valid completion timestamp on {analysis_date:%d %b %Y}, counted from the Calls Registered export. "
                "This reflects calls present in that report's registration period; older-booked calls closed that day may be absent. "
                "Only SAs with a recorded completion are shown; zero-completion SAs require a daily roster.")
    else:
        note = (f"Daily distinct completed tickets on {analysis_date:%d %b %Y}, counted from the Soft Closure report. "
                "Only SAs with a recorded completion are shown; zero-completion SAs require a daily roster.")
    return result, note


def scope_analysis(analysis: Dict[str, Any], scope_mode: str = "branch", selection: str = "") -> Dict[str, Any]:
    """Return a view-specific analysis while preserving age-only ordering.

    scope_mode is one of branch, franchise, executive. Scoped CSAT and registered-call
    figures are calculated only when those source reports contain a matching scope field.
    """
    mode = (scope_mode or "branch").strip().lower()
    if mode not in {"branch", "franchise", "executive"}:
        mode = "branch"
    if mode != "branch" and not clean_text(selection):
        raise ValueError("Choose a franchise or service executive for this view.")

    scoped = dict(analysis)
    scoped["scope_mode"] = mode
    scoped["scope_selection"] = clean_text(selection)
    scoped["scope_label"] = (
        "Hyderabad Branch" if mode == "branch" else
        f"Franchise: {clean_text(selection)}" if mode == "franchise" else
        f"Service Executive: {clean_text(selection)}"
    )

    all_priorities = analysis["priorities"].copy()
    priorities, _ = _filter_to_scope(all_priorities, mode, selection)
    # Age is the only sort key in every view. Stable sorting keeps source order for ties.
    priorities["Days Old"] = pd.to_numeric(priorities.get("Days Old", pd.Series(index=priorities.index, dtype=float)), errors="coerce").fillna(0).astype(int)
    priorities = priorities.sort_values("Days Old", ascending=False, kind="stable").reset_index(drop=True)
    if "Order" in priorities.columns:
        priorities["Order"] = range(1, len(priorities) + 1)
    scoped["priorities"] = priorities
    scoped["top20"] = priorities.head(20).copy()

    # Calculate descriptive mixes strictly from the scoped queue/Top 20.
    top20 = scoped["top20"].copy()
    exec_col = find_col(top20, ["Service Executive Name"])
    franchise_col = find_col(top20, ["Franchise Name"])
    status_col = find_col(top20, ["Status"])
    if len(top20):
        if exec_col:
            exec_vals = top20[exec_col].map(lambda v: clean_text(v) or "Unspecified")
            counts = exec_vals.value_counts()
            top20["Executive % of Top 20"] = exec_vals.map(counts).astype(float) * 100 / len(top20)
            ex_summary = pd.DataFrame({"Executive": counts.index, "Top 20 Calls": counts.values})
            ex_summary["Share of Top 20 (%)"] = ex_summary["Top 20 Calls"] * 100 / len(top20)
        else:
            top20["Executive % of Top 20"] = 100.0
            ex_summary = pd.DataFrame({"Executive": ["Unspecified"], "Top 20 Calls": [len(top20)], "Share of Top 20 (%)": [100.0]})
        if franchise_col:
            fr_vals = top20[franchise_col].map(lambda v: clean_text(v) or "Unspecified")
            counts_fr = fr_vals.value_counts()
            top20["Franchise % of Top 20"] = fr_vals.map(counts_fr).astype(float) * 100 / len(top20)
            fr_top_summary = pd.DataFrame({"Franchise": counts_fr.index, "Top 20 Calls": counts_fr.values})
            fr_top_summary["Share of Top 20 (%)"] = fr_top_summary["Top 20 Calls"] * 100 / len(top20)
        else:
            top20["Franchise % of Top 20"] = 100.0
            fr_top_summary = pd.DataFrame({"Franchise": ["Unspecified"], "Top 20 Calls": [len(top20)], "Share of Top 20 (%)": [100.0]})
        if status_col:
            st_vals = top20[status_col].map(lambda v: clean_text(v) or "Unspecified")
            counts_st = st_vals.value_counts()
            st_top_summary = pd.DataFrame({"Status": counts_st.index, "Top 20 Calls": counts_st.values})
            st_top_summary["Share of Top 20 (%)"] = st_top_summary["Top 20 Calls"] * 100 / len(top20)
        else:
            st_top_summary = pd.DataFrame(columns=["Status", "Top 20 Calls", "Share of Top 20 (%)"])
        ex_summary = ex_summary.sort_values(["Top 20 Calls", "Executive"], ascending=[False, True], kind="stable").reset_index(drop=True)
        fr_top_summary = fr_top_summary.sort_values(["Top 20 Calls", "Franchise"], ascending=[False, True], kind="stable").reset_index(drop=True)
        st_top_summary = st_top_summary.sort_values("Top 20 Calls", ascending=False, kind="stable").reset_index(drop=True)
        scoped["top20"] = top20
    else:
        scoped["top20_executive_summary"] = pd.DataFrame(columns=["Executive", "Top 20 Calls", "Share of Top 20 (%)"])
        ex_summary = scoped["top20_executive_summary"]
        fr_top_summary = pd.DataFrame(columns=["Franchise", "Top 20 Calls", "Share of Top 20 (%)"])
        st_top_summary = pd.DataFrame(columns=["Status", "Top 20 Calls", "Share of Top 20 (%)"])
    scoped["top20_executive_summary"] = ex_summary
    scoped["top20_franchise_summary"] = fr_top_summary
    scoped["top20_status_summary"] = st_top_summary

    # Reconcile source-row counts and exclusions for the selected scope without
    # re-running the expensive full-ticket consolidation on every dropdown rerun.
    # The branch analysis already contains the active, one-row-per-ticket queue.
    current = analysis["current_pending_report"]
    all_reports = analysis.get("reports", [])
    filtered_source = filter_pending_call_types(current.get("df", pd.DataFrame()))
    filtered_source_scoped, scope_col_supported = _filter_to_scope(filtered_source, mode, selection)
    ticket_col = get_ticket_col(filtered_source_scoped)
    source_ids = (
        {clean_text(v) for v in filtered_source_scoped[ticket_col].tolist() if clean_text(v)}
        if ticket_col else set()
    )
    scoped["current_source_distinct_tickets"] = len(source_ids)
    active_ids = set(priorities["Ticket No"].map(clean_text)) if "Ticket No" in priorities.columns else set()

    # Count source rows belonging to currently active tickets; do not call
    # active_pending_data() again here (it re-groups every component row and can
    # cause timeouts when executives/franchises are switched).
    if ticket_col:
        scoped_ticket_keys = filtered_source_scoped[ticket_col].map(clean_text)
        active_rows_scoped = filtered_source_scoped.loc[scoped_ticket_keys.isin(active_ids)].copy()
    else:
        active_rows_scoped = filtered_source_scoped.iloc[0:0].copy()
    scoped["current_pending_rows"] = len(active_rows_scoped)
    scoped["current_unique_tickets"] = len(priorities)

    # Determine excluded ticket counts cheaply for this scope, keeping soft-closed
    # and negative-response counts mutually exclusive as in active_pending_data().
    negative_ids = set()
    status_col_raw = find_col(filtered_source_scoped, ["Status"])
    if ticket_col and status_col_raw:
        negative_rows = filtered_source_scoped[
            filtered_source_scoped[status_col_raw].map(lambda v: "negativeresponse" in norm(v))
        ]
        negative_ids = {clean_text(v) for v in negative_rows[ticket_col].tolist() if clean_text(v)}
    snapshot_dt = pd.Timestamp(current.get("snapshot_dt"))
    soft_source_ids = soft_closure_ticket_ids_as_of(all_reports, snapshot_dt) & source_ids
    soft_ids = soft_source_ids - active_ids
    negative_only = (negative_ids - active_ids) - soft_ids
    scoped["soft_closed_tickets_excluded"] = len(soft_ids)
    scoped["negative_response_tickets_excluded"] = len(negative_only)
    scoped["prior_unique_tickets"] = None

    # Scope comparison movement: count new/continuing from the current scope and missing from the previous scope.
    comp = analysis.get("comparison", pd.DataFrame()).copy()
    if mode != "branch" and not comp.empty:
        if mode == "franchise":
            prev_key, cur_key = "Previous Franchise", "Current Franchise"
        else:
            prev_key, cur_key = "Previous Executive", "Current Executive"
        if prev_key in comp.columns and cur_key in comp.columns:
            prev_match = comp[prev_key].map(norm) == norm(selection)
            cur_match = comp[cur_key].map(norm) == norm(selection)
            movement_col = find_col(comp, ["Movement"])
            movement_text = comp[movement_col].map(clean_text) if movement_col else pd.Series("", index=comp.index)
            new_mask = movement_text.str.startswith("Newly present") & cur_match
            missing_mask = movement_text.str.startswith("No longer present") & prev_match
            common_scope = prev_match & cur_match
            continuing_mask = common_scope & movement_text.str.startswith(("Present",))
            changed_mask = common_scope & movement_text.str.contains("status changed", case=False)
            age_mask = common_scope & movement_text.str.contains("age increased", case=False)
            scoped["movement_counts"] = {"new": int(new_mask.sum()), "continuing": int(continuing_mask.sum()),
                                         "missing": int(missing_mask.sum()), "status_changed": int(changed_mask.sum()),
                                         "age_increased": int(age_mask.sum())}
            scoped["comparison"] = comp.loc[new_mask | missing_mask | common_scope].copy().reset_index(drop=True)
        else:
            scoped["movement_counts"] = {"new": 0, "continuing": 0, "missing": 0, "status_changed": 0, "age_increased": 0}
            scoped["comparison"] = pd.DataFrame()
    else:
        scoped["movement_counts"] = analysis["movement_counts"]
        scoped["comparison"] = comp

    # Scoped status, call type, age-band and franchise/executive/technician summaries.
    scoped["status_summary"] = _simple_counts(priorities, ["Status"], "Status")
    scoped["call_type_summary"] = _simple_counts(priorities, ["Call Type"], "Call Type")
    scoped["pendency_reason_summary"], scoped["pendency_reason_metrics"] = _pendency_reason_outputs(priorities)
    scoped["age_summary"] = pd.cut(
        priorities["Days Old"], bins=[-1, 0, 2, 6, 13, 29, float("inf")],
        labels=["0 days", "1–2 days", "3–6 days", "7–13 days", "14–29 days", "30+ days"],
    ).value_counts(sort=False).rename_axis("Age Bucket").reset_index(name="Unique Tickets")
    scoped["older_7"] = int((priorities["Days Old"] >= 7).sum())
    scoped["escalated"] = int(priorities.get("Has Escalation", pd.Series(False, index=priorities.index)).astype(bool).sum())
    scoped["repeat_calls"] = int(priorities.get("Is Repeat Call", pd.Series(False, index=priorities.index)).astype(bool).sum())
    scoped["overdue_scheduled"] = int(priorities.get("Scheduled Overdue", pd.Series(False, index=priorities.index)).astype(bool).sum())

    franchise_col_all = find_col(priorities, ["Franchise Name"])
    if franchise_col_all:
        temp = priorities.copy()
        temp["Franchise"] = temp[franchise_col_all].map(lambda v: clean_text(v) or "Unspecified")
        scoped["franchise_summary"] = temp.groupby("Franchise", dropna=False).agg(
            **{"Pending Tickets": ("Ticket No", "count"),
               "Aged >2 Days": ("Days Old", lambda x: int((pd.to_numeric(x, errors="coerce").fillna(0) > 2).sum())),
               "Aged 7+ Days": ("Days Old", lambda x: int((pd.to_numeric(x, errors="coerce").fillna(0) >= 7).sum())),
               "Escalated Tickets": ("Has Escalation", lambda x: int(x.astype(bool).sum())),
               "Repeat-call Flags": ("Is Repeat Call", lambda x: int(x.astype(bool).sum())),
               "Average Age (days)": ("Days Old", lambda x: round(pd.to_numeric(x, errors="coerce").fillna(0).mean(), 1)),
            }
        ).reset_index().sort_values(["Aged >2 Days", "Pending Tickets"], ascending=False, kind="stable").reset_index(drop=True)
    else:
        scoped["franchise_summary"] = pd.DataFrame(columns=["Franchise", "Pending Tickets", "Aged >2 Days", "Aged 7+ Days", "Escalated Tickets", "Repeat-call Flags", "Average Age (days)"])

    ex_col_all = find_col(priorities, ["Service Executive Name"])
    if ex_col_all:
        temp = priorities.copy()
        temp["Executive"] = temp[ex_col_all].map(lambda v: clean_text(v) or "Unspecified")
        scoped["executive_summary"] = temp.groupby("Executive", dropna=False).agg(
            **{"Pending Tickets": ("Ticket No", "count"), "Aged 7+ Days": ("Days Old", lambda x: int((x >= 7).sum())),
               "Escalated Tickets": ("Has Escalation", lambda x: int(x.astype(bool).sum())),
               "Repeat-call Flags": ("Is Repeat Call", lambda x: int(x.astype(bool).sum())),
               "Overdue Scheduled": ("Scheduled Overdue", lambda x: int(x.astype(bool).sum())),
               "Average Age (days)": ("Days Old", lambda x: round(float(x.mean()), 1))}
        ).reset_index().sort_values("Pending Tickets", ascending=False, kind="stable").reset_index(drop=True)
    else:
        scoped["executive_summary"] = pd.DataFrame(columns=["Executive", "Pending Tickets", "Aged 7+ Days", "Escalated Tickets", "Repeat-call Flags", "Overdue Scheduled", "Average Age (days)"])

    tech_col = find_col(priorities, ["Technician Name"])
    if tech_col:
        tech = priorities.copy()
        tech["Technician"] = tech[tech_col].map(lambda v: clean_text(v) or "Unspecified")
        scoped["technician_summary"] = tech.groupby("Technician", dropna=False).agg(
            **{"Pending Tickets": ("Ticket No", "count"), "Aged >2 Days": ("Days Old", lambda x: int((x > 2).sum())),
               "Aged 7+ Days": ("Days Old", lambda x: int((x >= 7).sum()))}
        ).reset_index().sort_values(["Pending Tickets", "Aged 7+ Days"], ascending=False, kind="stable").reset_index(drop=True)
    else:
        scoped["technician_summary"] = pd.DataFrame(columns=["Technician", "Pending Tickets", "Aged >2 Days", "Aged 7+ Days"])

    # Daily Service Ambassador output is scoped consistently in all three views.
    # It uses exact completion dates and the six-call daily mandate.
    productivity_hierarchy, productivity_hierarchy_note = _service_ambassador_target_hierarchy(
        analysis.get("reports", []), analysis.get("analysis_date", date.today()), priorities,
        scope_mode=mode, selection=clean_text(selection),
        selected_report_order=analysis.get("selected_report_orders", {}).get("soft_closure"),
        selected_calls_report_order=analysis.get("selected_report_orders", {}).get("calls_registered"),
        daily_target=6,
    )
    scoped["sa_productivity_hierarchy"] = productivity_hierarchy
    scoped["sa_productivity_hierarchy_note"] = productivity_hierarchy_note

    if mode == "franchise":
        productivity, productivity_note = _service_ambassador_productivity(
            analysis.get("reports", []), clean_text(selection), analysis.get("analysis_date", date.today()),
            selected_report_order=analysis.get("selected_report_orders", {}).get("soft_closure"),
            selected_calls_report_order=analysis.get("selected_report_orders", {}).get("calls_registered"),
        )
        scoped["service_ambassador_productivity"] = productivity
        scoped["service_ambassador_productivity_note"] = productivity_note
    else:
        scoped["service_ambassador_productivity"] = pd.DataFrame(
            columns=["Service Ambassador", "Calls Done", "Share (%)"]
        )
        scoped["service_ambassador_productivity_note"] = ""

    # Exclusion call types within the selected scope.
    source_all = current.get("df", pd.DataFrame())
    ex_mask = excluded_pending_call_type_mask(source_all)
    excluded_by_type = source_all.loc[ex_mask].copy()
    excluded_by_type_scoped, _ = _filter_to_scope(excluded_by_type, mode, selection)
    scoped["excluded_pending_rows"] = len(excluded_by_type_scoped)
    scoped["excluded_pending_tickets"] = distinct_ticket_count(excluded_by_type_scoped)
    missing_ticket_col = get_ticket_col(filtered_source_scoped)
    missing_ticket_count = int(filtered_source_scoped[missing_ticket_col].map(lambda x: not bool(clean_text(x))).sum()) if missing_ticket_col else len(filtered_source_scoped)
    missing_schedule = int(priorities["Scheduled Date"].map(lambda x: not bool(clean_text(x)) or (parse_dt(x) is None)).sum()) if "Scheduled Date" in priorities else len(priorities)
    scoped["data_quality_metrics"] = {
        "missing_executive": int(priorities.get("Missing Executive", pd.Series(False, index=priorities.index)).astype(bool).sum()),
        "missing_technician": int(priorities.get("Missing Technician", pd.Series(False, index=priorities.index)).astype(bool).sum()),
        "missing_schedule": missing_schedule,
        "missing_ticket_number": missing_ticket_count,
        "rows_beyond_distinct_tickets": max(0, len(active_rows_scoped) - len(priorities)),
        "soft_closed_tickets_excluded": len(soft_ids),
        "negative_response_tickets_excluded": len(negative_ids - soft_ids),
        "current_source_distinct_tickets": len(source_ids),
    }

    # Scope the satisfaction and registered-call datasets only when their own report has the required field.
    csat_report = analysis.get("csat_report")
    csat_source = csat_report.get("df", pd.DataFrame()) if csat_report else pd.DataFrame()
    csat_filtered, csat_key_supported = _filter_to_scope(csat_source, mode, selection)
    if mode != "branch" and not csat_key_supported:
        csat_filtered = csat_source.iloc[0:0].copy()
    scoped["csat"] = csat_filtered
    scoped["csat_summary"], scoped["csat_metrics"] = _feedback_outputs(csat_filtered)
    scoped["csat_scope_supported"] = csat_key_supported
    previous_csat_report = analysis.get("csat_previous_report")
    previous_csat_df = previous_csat_report.get("df", pd.DataFrame()) if previous_csat_report else pd.DataFrame()
    previous_csat_filtered, prev_csat_supported = _filter_to_scope(previous_csat_df, mode, selection)
    prev_sum, prev_metrics = _feedback_outputs(previous_csat_filtered)
    css_delta = None
    if prev_metrics.get("css_pct") is not None and scoped["csat_metrics"].get("css_pct") is not None:
        css_delta = round(scoped["csat_metrics"]["css_pct"] - prev_metrics["css_pct"], 2)
    scoped["csat_movement"] = dict(analysis.get("csat_movement", {}))
    scoped["csat_movement"]["css_delta_pp"] = css_delta
    scoped["csat_movement"]["previous_rows"] = prev_metrics.get("total") if previous_csat_report else None
    scoped["csat_movement"]["current_rows"] = scoped["csat_metrics"].get("total", 0)

    # Scope Calls Registered figures too, when a matching column is available.
    reg = analysis.get("registered", pd.DataFrame()).copy()
    reg_day = analysis.get("registered_for_day", pd.DataFrame()).copy()
    reg_scoped, reg_supported = _filter_to_scope(reg, mode, selection)
    reg_day_scoped, reg_day_supported = _filter_to_scope(reg_day, mode, selection)
    if mode != "branch" and (not reg_supported or not reg_day_supported):
        scoped["registered_count"] = None
        scoped["registered_for_day"] = reg_day.iloc[0:0].copy()
    else:
        scoped["registered_for_day"] = reg_day_scoped
        col_reg = get_ticket_col(reg_day_scoped)
        scoped["registered_count"] = len({clean_text(x) for x in reg_day_scoped[col_reg] if clean_text(x)}) if col_reg and analysis.get("registered_count") is not None else analysis.get("registered_count")
    scoped["registered"] = reg_scoped

    # Add scope metadata for reporting and group-update snapshots.
    scoped["scope_summary"] = {
        "mode": mode,
        "selection": clean_text(selection),
        "active_tickets": len(priorities),
        "aged_over_2": int((priorities["Days Old"] > 2).sum()),
        "aged_7_plus": int((priorities["Days Old"] >= 7).sum()),
        "oldest_days": int(priorities["Days Old"].max()) if not priorities.empty else None,
        "status_counts": scoped["status_summary"].to_dict("records"),
        "csat_scoped": bool(csat_key_supported or mode == "branch"),
    }
    return scoped


def _date_label(value: Any) -> str:
    try:
        return pd.Timestamp(value).strftime("%d %b %Y")
    except Exception:
        return clean_text(value)


def _snapshot_status(analysis: Dict[str, Any]) -> str:
    snap = analysis["current_pending_report"]["snapshot_dt"]
    # Conservative label: a late snapshot can be called an EOD snapshot, but never assume it is complete.
    if snap.date() == analysis["analysis_date"] and snap.hour >= 18:
        return "LATE-DAY SNAPSHOT · confirm all source reports are final"
    return "INTRADAY SNAPSHOT · not a confirmed end-of-day closeout"


def make_priority_png(analysis: Dict[str, Any]) -> bytes:
    """Render the 20 oldest tickets with a scope-appropriate descriptive chart."""
    import matplotlib.pyplot as plt
    import numpy as np
    import textwrap

    mode = analysis.get("scope_mode", "branch")
    selection = clean_text(analysis.get("scope_selection", ""))
    data = analysis["top20"].copy().head(20)
    snap = analysis["current_pending_report"]["snapshot_dt"]
    fig = plt.figure(figsize=(21, 11.2), dpi=160, facecolor="#FFFFFF")

    if mode == "franchise":
        title = f"Pending calls — {selection}: 20 oldest tickets"
        context = f"Franchise view: {selection}"
    elif mode == "executive":
        title = f"Pending calls — {selection}: 20 oldest tickets"
        context = f"Executive view: all franchises assigned to {selection}"
    else:
        title = "Pending calls — 20 oldest tickets"
        context = "Hyderabad Service Branch"
    fig.text(0.018, 0.962, title, fontsize=20, color="#303236", va="top")
    fig.text(0.018, 0.925,
             f"{context}  |  As of {snap.strftime('%d %b %Y, %I:%M %p')}  |  Ordered by call age, oldest first",
             fontsize=10, color="#5B6570", va="top")
    fig.text(0.982, 0.962, f"{analysis['current_unique_tickets']:,} ACTIVE PENDING TICKETS",
             fontsize=11, weight="bold", ha="right", va="top", color="#303030")

    ax = fig.add_axes([0.012, 0.305, 0.976, 0.555])
    ax.axis("off")
    col_def = [
        ("Order", "No.", 0.035),
        ("Ticket No", "Ticket No.", 0.085),
        ("Days Old", "Days Old", 0.055),
        ("Customer Name", "Customer", 0.125),
        ("Franchise Name", "Franchise", 0.130),
        ("Service Executive Name", "Service Executive", 0.130),
        ("Technician Name", "Technician", 0.110),
        ("Call Type", "Call Type", 0.105),
        ("Status", "Status", 0.100),
    ]
    if mode == "branch":
        col_def.append(("Executive % of Top 20", "Exec. % of Top 20", 0.080))
    elif mode == "executive":
        col_def.append(("Franchise % of Top 20", "Franchise % of Top 20", 0.080))
    present = [(src, label, width) for src, label, width in col_def if src in data.columns]
    total = sum(c[2] for c in present) or 1
    col_widths = [c[2] / total for c in present]

    rows = []
    for _, row in data.iterrows():
        vals = []
        for src, _, _ in present:
            value = clean_text(row.get(src, ""))
            if src in {"Executive % of Top 20", "Franchise % of Top 20"} and value:
                try:
                    value = f"{float(value):.1f}%"
                except (TypeError, ValueError):
                    pass
            limit = {
                "Franchise Name": 30, "Customer Name": 26,
                "Service Executive Name": 27, "Technician Name": 23,
                "Call Type": 20, "Status": 20,
            }.get(src)
            if limit and len(value) > limit:
                value = value[:limit - 3] + "..."
            vals.append(value)
        rows.append(vals)

    table = ax.table(
        cellText=rows,
        colLabels=[label for _, label, _ in present],
        cellLoc="left", colLoc="center", loc="center",
        colWidths=col_widths, bbox=[0.0, 0.0, 1.0, 1.0],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8.2)
    centered = {"Order", "Days Old", "Executive % of Top 20", "Franchise % of Top 20"}
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#D0D4D8")
        cell.set_linewidth(0.55)
        if r == 0:
            cell.set_facecolor("#EEEEEE")
            cell.get_text().set_color("#202326")
            cell.get_text().set_weight("bold")
            cell.get_text().set_fontsize(8.1)
            cell.set_height(0.045)
            cell.get_text().set_ha("center")
        else:
            cell.set_facecolor("#FFFFFF")
            cell.get_text().set_color("#202326")
            cell.set_height(0.034)
            if present[c][0] in centered:
                cell.get_text().set_ha("center")
            if present[c][0] == "Order":
                cell.get_text().set_weight("bold")

    # The lower chart changes by view; it is descriptive only and never affects ordering.
    if mode == "franchise":
        chart = analysis.get("top20_status_summary", pd.DataFrame()).copy()
        category_col = "Status"
        chart_title = "Top 20 calls by status — franchise update"
    elif mode == "executive":
        chart = analysis.get("top20_franchise_summary", pd.DataFrame()).copy()
        category_col = "Franchise"
        if len(chart) <= 1:
            chart = analysis.get("top20_status_summary", pd.DataFrame()).copy()
            category_col = "Status"
            chart_title = "Top 20 calls by status — executive update"
        else:
            chart_title = "Top 20 calls across your franchises"
    else:
        chart = analysis.get("top20_executive_summary", pd.DataFrame()).copy()
        category_col = "Executive"
        chart_title = "Top 20 calls by service executive"

    if not chart.empty and category_col in chart.columns and "Top 20 Calls" in chart.columns:
        chart["Top 20 Calls"] = pd.to_numeric(chart["Top 20 Calls"], errors="coerce").fillna(0)
        chart["Share of Top 20 (%)"] = pd.to_numeric(chart.get("Share of Top 20 (%)", 0), errors="coerce").fillna(0)
        chart[category_col] = chart[category_col].map(lambda v: clean_text(v) or "Unspecified")
        chart = chart.sort_values("Top 20 Calls", ascending=False, kind="stable").copy()
        if len(chart) > 6:
            main = chart.head(5).copy()
            rest = chart.iloc[5:]
            main = pd.concat([main, pd.DataFrame([{
                category_col: "Other", "Top 20 Calls": int(rest["Top 20 Calls"].sum()),
                "Share of Top 20 (%)": float(rest["Share of Top 20 (%)"].sum()),
            }])], ignore_index=True)
            chart = main
        labels = [textwrap.fill(v, width=29, break_long_words=False) for v in chart[category_col].astype(str)]
        y = np.arange(len(chart))
        counts = chart["Top 20 Calls"].to_numpy(dtype=float)
        shares = chart["Share of Top 20 (%)"].to_numpy(dtype=float)
        chart_ax = fig.add_axes([0.19, 0.075, 0.735, 0.155])
        chart_ax.barh(y, counts, color="#3A78A5", height=0.58)
        chart_ax.set_yticks(y)
        chart_ax.set_yticklabels(labels, fontsize=7.8, color="#263746")
        chart_ax.invert_yaxis()
        chart_ax.set_title(chart_title, loc="left", fontsize=10.5, color="#18344D", fontweight="bold", pad=5)
        chart_ax.tick_params(axis="x", labelsize=7.5, colors="#435365", length=0, pad=3)
        chart_ax.tick_params(axis="y", length=0, pad=7)
        chart_ax.xaxis.grid(True, color="#E3E8ED", linewidth=0.65)
        chart_ax.set_axisbelow(True)
        for spine in chart_ax.spines.values():
            spine.set_visible(False)
        xmax = max(float(counts.max()), 1.0)
        chart_ax.set_xlim(0, xmax * 1.42)
        chart_ax.set_xlabel("Number of calls in Top 20", fontsize=7.5, color="#435365", labelpad=2)
        for yy, count, share in zip(y, counts, shares):
            chart_ax.text(count + xmax * 0.025, yy, f"{int(count)} calls  ·  {share:.1f}%",
                          va="center", ha="left", fontsize=7.6, color="#263746", weight="bold")
    else:
        fig.text(0.018, 0.15, "No chartable calls in this view.", fontsize=9, color="#435365")

    note = "Sorted only by Days Old, highest to lowest. No weighted score or escalation-based ranking is used."
    fig.text(0.018, 0.025, note, fontsize=8.7, color="#4F5963", va="bottom")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def make_management_png(analysis: Dict[str, Any]) -> bytes:
    """Create a cleaner one-page management report with honest pendency-reason coverage."""
    import matplotlib.pyplot as plt
    import numpy as np
    import textwrap

    navy = "#18344D"
    teal = "#0B7E83"
    amber = "#C78318"
    red = "#B9363E"
    green = "#237A55"
    blue = "#3A78A5"
    ink = "#263746"
    muted = "#526170"
    grid = "#E3E8ED"
    page = "#F3F6F8"
    mode = analysis.get("scope_mode", "branch")
    selection = clean_text(analysis.get("scope_selection", ""))

    fig = plt.figure(figsize=(16, 10.5), dpi=180, facecolor=page)
    if mode == "franchise":
        heading = "SERVICE OPERATIONS | FRANCHISE DAILY UPDATE"
        scope_subtitle = f"Franchise: {selection}"
    elif mode == "executive":
        heading = "SERVICE OPERATIONS | EXECUTIVE DAILY BRIEF"
        scope_subtitle = f"Service Executive: {selection} · Assigned franchise queues"
    else:
        heading = "SERVICE OPERATIONS | DAILY MANAGEMENT BRIEF"
        scope_subtitle = "Hyderabad Branch"

    snap = analysis["current_pending_report"]["snapshot_dt"]
    fig.text(0.04, 0.963, heading, fontsize=18.5, weight="bold", color=navy, va="top")
    fig.text(0.04, 0.923,
             f"{scope_subtitle}  ·  {_date_label(analysis['analysis_date'])}  ·  {_snapshot_status(analysis)}",
             fontsize=9.2, color=muted, va="top")
    fig.text(0.96, 0.963, f"AS OF {snap.strftime('%d %b %Y  %I:%M %p')}",
             fontsize=9.1, weight="bold", color=teal, ha="right", va="top")

    current = int(analysis["current_unique_tickets"])
    css_rounded = analysis.get("csat_metrics", {}).get("css_pct_rounded")
    aged_over_2 = int(analysis.get("scope_summary", {}).get(
        "aged_over_2", (analysis.get("priorities", pd.DataFrame()).get("Days Old", pd.Series(dtype=float)) > 2).sum()
    ))
    older_7 = int(analysis.get("older_7", 0))
    reasons_meta = analysis.get("pendency_reason_metrics", {})
    reason_cov = float(reasons_meta.get("coverage_pct", 0.0) or 0.0)
    kpis = [
        ("ACTIVE PENDING", f"{current:,}", navy),
        ("AGED >2 DAYS", f"{aged_over_2:,}", amber),
        ("AGED 7+ DAYS", f"{older_7:,}", red),
        ("CSS", f"{css_rounded}%" if css_rounded is not None else "—", green),
    ]
    card_xs = [0.04, 0.275, 0.51, 0.745]
    for x, (label, value, value_color) in zip(card_xs, kpis):
        fig.patches.append(plt.Rectangle((x, 0.825), 0.215, 0.073, transform=fig.transFigure,
                                         facecolor="#FFFFFF", edgecolor="#D8E0E7", linewidth=0.9, zorder=-10))
        fig.text(x + 0.012, 0.872, label, fontsize=8.0, color=muted, weight="bold", va="center")
        fig.text(x + 0.012, 0.842, value, fontsize=17, color=value_color, weight="bold", va="center")

    def style_axis(ax, title, title_size=10.3):
        ax.set_facecolor("#FFFFFF")
        ax.set_title(title, loc="left", color=navy, fontsize=title_size, fontweight="bold", pad=7)
        ax.tick_params(axis="both", labelsize=7.7, colors=ink, length=0, pad=3)
        ax.grid(axis="x", color=grid, linewidth=0.65)
        ax.set_axisbelow(True)
        for spine in ax.spines.values():
            spine.set_visible(False)

    def empty_axis(ax, title, note="No matching data available for this view."):
        ax.clear()
        ax.set_facecolor("#FFFFFF")
        ax.axis("off")
        ax.set_title(title, loc="left", color=navy, fontsize=10.3, fontweight="bold", pad=7)
        ax.text(0.02, 0.50, note, color=muted, fontsize=8.8, transform=ax.transAxes, va="center")

    def draw_hbar(ax, frame, label_col, value_col, title, share_col=None, max_rows=5, label_wrap=23):
        df = frame.copy() if frame is not None else pd.DataFrame()
        if df.empty or label_col not in df.columns or value_col not in df.columns:
            empty_axis(ax, title)
            return
        df[value_col] = pd.to_numeric(df[value_col], errors="coerce").fillna(0)
        df = df.loc[df[value_col] > 0].sort_values(value_col, ascending=False, kind="stable").head(max_rows).copy()
        if df.empty:
            empty_axis(ax, title, "No records to display.")
            return
        df[label_col] = df[label_col].map(lambda x: clean_text(x) or "Unspecified")
        labels = [textwrap.fill(v, width=label_wrap, break_long_words=False) for v in df[label_col].astype(str)]
        vals = df[value_col].to_numpy(dtype=float)
        y = np.arange(len(df))
        ax.barh(y, vals, color=blue, height=0.57)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=7.5, color=ink)
        ax.invert_yaxis()
        style_axis(ax, title)
        ax.tick_params(axis="y", labelsize=7.5, colors=ink, pad=4)
        xmax = max(float(vals.max()), 1.0)
        ax.set_xlim(0, xmax * 1.48)
        if share_col and share_col in df.columns:
            shares = pd.to_numeric(df[share_col], errors="coerce").fillna(0).to_numpy(dtype=float)
        else:
            total = float(pd.to_numeric(frame[value_col], errors="coerce").fillna(0).sum()) or 1.0
            shares = vals * 100.0 / total
        for yy, val, share in zip(y, vals, shares):
            ax.text(val + xmax * 0.025, yy, f"{int(val):,}  ·  {share:.1f}%", va="center", ha="left",
                    fontsize=7.3, color=ink, weight="bold")

    # Four evenly spaced panels: age, recorded reasons, view-specific operations, SA target attainment.
    # Feedback mix is removed; OK and Not Happy counts are shown compactly in the footer.
    panels = [(0.04, 0.515, 0.445, 0.265), (0.515, 0.515, 0.445, 0.265),
              (0.04, 0.275, 0.445, 0.220), (0.515, 0.275, 0.445, 0.220)]
    for x, y, w, h in panels:
        fig.patches.append(plt.Rectangle((x, y), w, h, transform=fig.transFigure,
                                         facecolor="#FFFFFF", edgecolor="#D9E1E7", linewidth=0.9, zorder=-10))

    # Panel 1 — ageing.
    ax_age = fig.add_axes([0.105, 0.545, 0.345, 0.19])
    age = analysis.get("age_summary", pd.DataFrame()).copy()
    if not age.empty and {"Age Bucket", "Unique Tickets"}.issubset(age.columns):
        age["Unique Tickets"] = pd.to_numeric(age["Unique Tickets"], errors="coerce").fillna(0)
        labels = age["Age Bucket"].astype(str).tolist()
        vals = age["Unique Tickets"].to_numpy(dtype=float)
        yy = np.arange(len(age))
        palette = ["#A8BAC8", "#8AA9BC", "#648EA8", "#2E8588", "#D39A35", "#B94842"]
        ax_age.barh(yy, vals, color=palette[:len(vals)], height=0.61)
        ax_age.set_yticks(yy)
        ax_age.set_yticklabels(labels, fontsize=7.6, color=ink)
        ax_age.invert_yaxis()
        style_axis(ax_age, "Pending backlog by age")
        xmax = max(float(vals.max()) if len(vals) else 0, 1.0)
        ax_age.set_xlim(0, xmax * 1.18)
        for yv, val in zip(yy, vals):
            ax_age.text(val + xmax * 0.018, yv, f"{int(val):,}", va="center", ha="left", fontsize=7.6, color=ink, weight="bold")
    else:
        empty_axis(ax_age, "Pending backlog by age", "No pending calls in this view.")

    # Panel 2 — recorded reasons only. Percentages use reason-recorded tickets as denominator.
    ax_reason = fig.add_axes([0.605, 0.55, 0.315, 0.185])
    reason_frame = analysis.get("pendency_reason_summary", pd.DataFrame()).copy()
    reason_meta = analysis.get("pendency_reason_metrics", {})
    reason_total = int(reason_meta.get("recorded_reason_tickets", 0) or 0)
    reason_all = int(reason_meta.get("total_tickets", current) or 0)
    reason_missing = int(reason_meta.get("unrecorded_reason_tickets", max(0, reason_all - reason_total)) or 0)
    coverage = float(reason_meta.get("coverage_pct", 0.0) or 0.0)
    if not reason_frame.empty and {"Reason", "Tickets"}.issubset(reason_frame.columns) and reason_total > 0:
        reason_frame["Tickets"] = pd.to_numeric(reason_frame["Tickets"], errors="coerce").fillna(0)
        reason_frame = reason_frame.loc[reason_frame["Tickets"] > 0].sort_values("Tickets", ascending=False, kind="stable")
        if len(reason_frame) > 5:
            top = reason_frame.head(4).copy()
            other = reason_frame.iloc[4:]
            other_count = int(other["Tickets"].sum())
            other_pct = round(100.0 * other_count / reason_total, 1)
            top = pd.concat([top, pd.DataFrame([{"Reason": "Other recorded reasons", "Tickets": other_count,
                                                   "Share of Recorded Reasons (%)": other_pct}])], ignore_index=True)
            reason_frame = top
        if "Share of Recorded Reasons (%)" not in reason_frame.columns:
            reason_frame["Share of Recorded Reasons (%)"] = reason_frame["Tickets"] * 100.0 / reason_total
        reason_frame["Share of Recorded Reasons (%)"] = pd.to_numeric(
            reason_frame["Share of Recorded Reasons (%)"], errors="coerce").fillna(0)
        reason_frame = reason_frame.sort_values("Share of Recorded Reasons (%)", ascending=False, kind="stable")
        reason_labels = [textwrap.fill(clean_text(v) or "Unspecified", width=23, break_long_words=False)
                         for v in reason_frame["Reason"].astype(str)]
        reason_shares = reason_frame["Share of Recorded Reasons (%)"].to_numpy(dtype=float)
        reason_counts = reason_frame["Tickets"].to_numpy(dtype=int)
        y_reason = np.arange(len(reason_frame))
        ax_reason.barh(y_reason, reason_shares, color=blue, height=0.57)
        ax_reason.set_yticks(y_reason)
        ax_reason.set_yticklabels(reason_labels, fontsize=7.2, color=ink)
        ax_reason.invert_yaxis()
        style_axis(ax_reason, "Reason for pendency · share of recorded reasons", title_size=9.7)
        ax_reason.tick_params(axis="y", labelsize=7.2, colors=ink, pad=3)
        max_share = max(float(reason_shares.max()) if len(reason_shares) else 0, 1.0)
        axis_limit = min(100.0, max_share * 1.65)
        ax_reason.set_xlim(0, axis_limit)
        ax_reason.set_xlabel("Share of recorded reasons (%)", fontsize=6.8, color=muted, labelpad=1)
        for yv, share, count in zip(y_reason, reason_shares, reason_counts):
            label = f"{share:.1f}%  ·  {count} ticket(s)"
            if share >= axis_limit * 0.78:
                ax_reason.text(share - max_share * 0.025, yv, label, va="center", ha="right",
                               fontsize=6.8, color="#FFFFFF", weight="bold")
            else:
                ax_reason.text(share + max_share * 0.025, yv, label, va="center", ha="left",
                               fontsize=7.0, color=ink, weight="bold")
        ax_reason.text(0.0, -0.19,
                       f"Reason recorded: {reason_total:,}/{reason_all:,} tickets ({coverage:.1f}%) · Not recorded: {reason_missing:,}",
                       transform=ax_reason.transAxes, fontsize=6.8, color=muted, va="top", clip_on=False)
    else:
        empty_axis(ax_reason, "Reason for pendency", "No nonblank Pending Reason values in the selected queue.")
        ax_reason.text(0.02, -0.11,
                       f"Reason recorded: {reason_total:,}/{reason_all:,} tickets ({coverage:.1f}%) · Not recorded: {reason_missing:,}",
                       transform=ax_reason.transAxes, fontsize=7.1, color=muted, va="top", clip_on=False)

    # Panel 3 — context appropriate to the selected view. The branch executive chart remains on the Top 20 image.
    ax_ops = fig.add_axes([0.105, 0.305, 0.345, 0.155])
    if mode == "franchise":
        # The separate SA target chart (panel 4) shows completed calls vs the 6/day mandate.
        # Use this panel for complementary open workload, avoiding two near-identical productivity charts.
        technician_frame = analysis.get("technician_summary", pd.DataFrame()).copy()
        draw_hbar(ax_ops, technician_frame, "Technician", "Pending Tickets",
                  "Open pending workload by technician", max_rows=5, label_wrap=23)
    elif mode == "executive":
        fr_frame = analysis.get("franchise_summary", pd.DataFrame()).copy()
        if len(fr_frame) > 1:
            draw_hbar(ax_ops, fr_frame, "Franchise", "Pending Tickets", "Pending workload by franchise", max_rows=5)
        else:
            status_frame = analysis.get("status_summary", pd.DataFrame()).copy()
            draw_hbar(ax_ops, status_frame, "Status", "Unique Tickets", "Your pending calls by status", max_rows=5)
    else:
        fr_frame = analysis.get("franchise_summary", pd.DataFrame()).copy()
        if not fr_frame.empty and "Aged >2 Days" in fr_frame.columns:
            fr_frame["Aged >2 Days"] = pd.to_numeric(fr_frame["Aged >2 Days"], errors="coerce").fillna(0)
            fr_frame = fr_frame.loc[fr_frame["Aged >2 Days"] > 0]
        draw_hbar(ax_ops, fr_frame, "Franchise", "Aged >2 Days", "Franchises with calls aged >2 days", max_rows=5, label_wrap=22)

    # Panel 4 — daily SA productivity against the six-call mandate.
    # Keep individual labels readable. In a single Executive/Franchise scope, show SA names
    # only and place the parent hierarchy in a subtitle; in a multi-team branch view, attach
    # parent names only when needed to distinguish repeated SA names.
    ax_sa = fig.add_axes([0.625, 0.312, 0.300, 0.145])
    sa_frame = analysis.get("sa_productivity_hierarchy", pd.DataFrame()).copy()
    sa_note = clean_text(analysis.get("sa_productivity_hierarchy_note", ""))
    if not sa_frame.empty and {"Executive", "Franchise", "Service Ambassador", "Calls Done"}.issubset(sa_frame.columns):
        sa_frame["Calls Done"] = pd.to_numeric(sa_frame["Calls Done"], errors="coerce").fillna(0).astype(int)
        all_sa_frame = sa_frame.copy()
        sa_frame = sa_frame.sort_values(
            ["Calls Done", "Executive", "Franchise", "Service Ambassador"],
            ascending=[False, True, True, True], kind="stable"
        ).head(6).copy()
        exec_values = all_sa_frame["Executive"].map(lambda v: clean_text(v) or "Unspecified executive").drop_duplicates().tolist()
        franchise_values = all_sa_frame["Franchise"].map(lambda v: clean_text(v) or "Unspecified franchise").drop_duplicates().tolist()
        simple_single_group = len(exec_values) == 1 and len(franchise_values) == 1
        sa_name_counts = all_sa_frame["Service Ambassador"].map(lambda v: norm(v)).value_counts()
        labels = []
        for _, row in sa_frame.iterrows():
            sa = clean_text(row.get("Service Ambassador", "")) or "Unspecified SA"
            ex = clean_text(row.get("Executive", "")) or "Unspecified executive"
            fr = clean_text(row.get("Franchise", "")) or "Unspecified franchise"
            if simple_single_group or sa_name_counts.get(norm(sa), 0) == 1:
                labels.append(textwrap.fill(sa, width=22, break_long_words=False))
            else:
                labels.append(textwrap.fill(f"{sa} · {fr} · {ex}", width=24, break_long_words=False))
        values = sa_frame["Calls Done"].to_numpy(dtype=float)
        y_sa = np.arange(len(sa_frame))
        ax_sa.barh(y_sa, values, color=blue, height=0.58)
        ax_sa.set_yticks(y_sa)
        ax_sa.set_yticklabels(labels, fontsize=7.0, color=ink)
        ax_sa.invert_yaxis()
        style_axis(ax_sa, "SA completed calls · 6/day target (top 6)", title_size=9.3)
        ax_sa.tick_params(axis="y", labelsize=7.0, colors=ink, pad=3)
        xmax = max(6.0, float(values.max()) if len(values) else 6.0)
        ax_sa.set_xlim(0, xmax * 1.48)
        ax_sa.axvline(6, color=red, linestyle="--", linewidth=1.0, alpha=0.8)
        for yy, value, (_, row) in zip(y_sa, values, sa_frame.iterrows()):
            pct = float(row.get("Target Achievement (%)", 0) or 0)
            ax_sa.text(value + max(xmax * 0.025, 0.08), yy, f"{int(value)}/6 · {pct:.0f}%",
                       va="center", ha="left", fontsize=7.0, color=ink, weight="bold")
        if simple_single_group:
            hierarchy_label = f"{exec_values[0]}  →  {franchise_values[0]}"
        else:
            hierarchy_label = "Grouped by executive → franchise → SA"
        if "Calls Registered export" in sa_note:
            source_note = "Source: Calls Registered; older-booked closures may be absent."
        else:
            source_note = "Source: dated Soft Closure report."
        tail = f"{hierarchy_label} · {source_note}"
        ax_sa.text(0.0, -0.15, textwrap.fill(tail, width=56),
                   transform=ax_sa.transAxes, fontsize=6.0, color=muted, va="top", clip_on=False)
    else:
        empty_axis(ax_sa, "SA daily productivity vs 6-call target", sa_note or "Upload Calls Registered with Technician Name, Status and closure timestamps to calculate daily target attainment.")

    # Compact three-column footer: movement, the requested OK/Not Happy counts, and data notes.
    fig.patches.append(plt.Rectangle((0.04, 0.065), 0.92, 0.165, transform=fig.transFigure,
                                     facecolor="#FFFFFF", edgecolor="#D8E0E7", linewidth=0.9, zorder=0))
    mc = analysis.get("movement_counts", {})
    fig.text(0.055, 0.202, "SNAPSHOT MOVEMENT", fontsize=7.8, color=teal, weight="bold", va="center")
    if analysis.get("previous_pending_report"):
        movement = (f"New: {int(mc.get('new', 0)):,} · Continuing: {int(mc.get('continuing', 0)):,} · "
                    f"No longer listed: {int(mc.get('missing', 0)):,}")
    else:
        movement = "Upload an earlier snapshot to compare movement."
    fig.text(0.055, 0.176, textwrap.fill(movement, width=46), fontsize=7.3, color=ink, va="top")
    fig.text(0.055, 0.118, "Missing from a later snapshot does not prove closure.", fontsize=6.8, color=red, va="top")

    fig.lines.append(plt.Line2D([0.345, 0.345], [0.085, 0.215], transform=fig.transFigure, color="#E0E6EB", linewidth=0.8))
    fig.text(0.365, 0.202, "CUSTOMER FEEDBACK COUNTS", fontsize=7.8, color=teal, weight="bold", va="center")
    fb_metrics = analysis.get("csat_metrics", {})
    ok_count = fb_metrics.get("ok")
    not_happy_count = fb_metrics.get("not_happy")
    fig.text(0.365, 0.164, f"OK  {int(ok_count):,}" if ok_count is not None else "OK  —",
             fontsize=11.0, color=amber, weight="bold", va="center")
    fig.text(0.465, 0.164, f"NOT HAPPY  {int(not_happy_count):,}" if not_happy_count is not None else "NOT HAPPY  —",
             fontsize=10.0, color=red, weight="bold", va="center")
    feedback_note = "Counts from the selected feedback report; CSS is shown above."
    fig.text(0.365, 0.119, textwrap.fill(feedback_note, width=43), fontsize=6.8, color=muted, va="top")

    fig.lines.append(plt.Line2D([0.625, 0.625], [0.085, 0.215], transform=fig.transFigure, color="#E0E6EB", linewidth=0.8))
    fig.text(0.645, 0.202, "DATA NOTES", fontsize=7.8, color=teal, weight="bold", va="center")
    dq = analysis.get("data_quality_metrics", {})
    exclude_line = (f"Excluded: {int(dq.get('soft_closed_tickets_excluded', 0)):,} soft-closed, "
                    f"{int(dq.get('negative_response_tickets_excluded', 0)):,} negative-response; Courtesy and Dealer Defective excluded.")
    fig.text(0.645, 0.176, textwrap.fill(exclude_line, width=49), fontsize=6.8, color=ink, va="top")
    sa_all = analysis.get("sa_productivity_hierarchy", pd.DataFrame()).copy()
    sa_total_done = int(pd.to_numeric(sa_all.get("Calls Done", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not sa_all.empty else 0
    sa_total_target = int(pd.to_numeric(sa_all.get("Daily Target", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not sa_all.empty else 0
    sa_count = int(sa_all["Service Ambassador"].nunique()) if not sa_all.empty and "Service Ambassador" in sa_all.columns else 0
    sa_attainment = (100.0 * sa_total_done / sa_total_target) if sa_total_target else None
    if sa_attainment is not None:
        source_line = f"SA target: {sa_attainment:.1f}% ({sa_total_done}/{sa_total_target} calls across {sa_count} observed SAs)."
    else:
        source_line = "SA target attainment unavailable: no valid daily closure data."
    fig.text(0.645, 0.119, textwrap.fill(source_line, width=49), fontsize=6.8, color=muted, va="top")
    fig.text(0.96, 0.027, "INTERNAL WORKING REPORT  ·  Snapshot-based figures; validate source coverage", 
             ha="right", va="center", fontsize=7.2, color=muted)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()



def make_pdf(priority_png: bytes, management_png: bytes) -> bytes:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from PIL import Image

    buf = io.BytesIO()
    page_size = landscape(A4)
    pdf = canvas.Canvas(buf, pagesize=page_size)
    page_w, page_h = page_size
    for img_bytes in [priority_png, management_png]:
        img = Image.open(io.BytesIO(img_bytes))
        iw, ih = img.size
        scale = min((page_w - 22) / iw, (page_h - 22) / ih)
        w, h = iw * scale, ih * scale
        x, y = (page_w - w) / 2, (page_h - h) / 2
        pdf.drawImage(ImageReader(img), x, y, width=w, height=h, preserveAspectRatio=True, mask="auto")
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def make_excel(analysis: Dict[str, Any]) -> bytes:
    """Create a polished, multi-sheet review workbook in memory."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter", datetime_format="dd-mmm-yyyy hh:mm", date_format="dd-mmm-yyyy") as writer:
        wb = writer.book
        navy = "#12304A"
        teal = "#148A8A"
        pale = "#EAF1F5"
        header_fmt = wb.add_format({"bold": True, "font_color": "white", "bg_color": navy, "text_wrap": True, "valign": "vcenter", "border": 0})
        title_fmt = wb.add_format({"bold": True, "font_size": 18, "font_color": navy})
        subtitle_fmt = wb.add_format({"font_color": "#5D6C7B", "font_size": 10})
        section_fmt = wb.add_format({"bold": True, "font_color": "white", "bg_color": teal, "font_size": 11})
        key_fmt = wb.add_format({"bold": True, "font_color": navy, "bg_color": pale, "border": 1, "border_color": "#D9E1E8"})
        val_fmt = wb.add_format({"bold": True, "font_size": 14, "font_color": navy, "border": 1, "border_color": "#D9E1E8", "align": "center"})
        note_fmt = wb.add_format({"text_wrap": True, "valign": "top", "font_color": "#465563"})

        overview = wb.add_worksheet("Management Summary")
        writer.sheets["Management Summary"] = overview
        overview.set_column("A:A", 31)
        overview.set_column("B:B", 30)
        overview.set_column("C:F", 18)
        overview.merge_range("A1:F1", "SERVICE OPERATIONS ANALYSIS", title_fmt)
        overview.merge_range("A2:F2", f"Hyderabad Branch · Analysis date {_date_label(analysis['analysis_date'])} · Snapshot {analysis['current_pending_report']['snapshot_dt'].strftime('%d %b %Y %H:%M')}", subtitle_fmt)
        overview.merge_range("A4:F4", "KEY METRICS", section_fmt)
        kpis = [
            ("Unique pending tickets", analysis["current_unique_tickets"]),
            ("Eligible rows in pending export", analysis["current_pending_rows"]),
            ("Pending aged 7+ days", analysis["older_7"]),
            ("Escalated tickets", analysis["escalated"]),
            ("Repeat-call flags", analysis["repeat_calls"]),
            ("Overdue scheduled calls", analysis["overdue_scheduled"]),
            ("Newly present vs previous snapshot", analysis["movement_counts"]["new"] if analysis["previous_pending_report"] else "N/A"),
            ("No longer listed vs previous snapshot", analysis["movement_counts"]["missing"] if analysis["previous_pending_report"] else "N/A"),
            ("Customer Satisfaction Score (CSS, exact)", f"{analysis['csat_metrics']['css_pct']:.2f}%" if analysis["csat_metrics"].get("css_pct") is not None else "N/A"),
            ("Customer Satisfaction Score (rounded for image)", f"{analysis['csat_metrics']['css_pct_rounded']}%" if analysis["csat_metrics"].get("css_pct_rounded") is not None else "N/A"),
            ("Happy feedback share (feedback mix only)", f"{analysis['csat_metrics']['happy_pct']:.2f}%" if analysis["csat_metrics"]["happy_pct"] is not None else "N/A"),
            ("Not-happy feedback share", f"{analysis['csat_metrics']['not_happy_pct']:.2f}%" if analysis["csat_metrics"]["not_happy_pct"] is not None else "N/A"),
            ("CSS formula", "(Happy count - Not Happy count) / total nonblank feedback responses × 100"),
            ("CSS change vs previous CSAT snapshot (percentage points)", f"{analysis['csat_movement']['css_delta_pp']:.2f}" if analysis['csat_movement'].get('css_delta_pp') is not None else "N/A"),
            ("Calls registered on analysis date", analysis["registered_count"] if analysis["registered_count"] is not None else "N/A"),
            ("Records with closure timestamp on date", analysis["closed_count"] if analysis["closed_count"] is not None else "N/A"),
        ]
        for idx, (key, val) in enumerate(kpis):
            row = 4 + idx
            overview.write(row, 0, key, key_fmt)
            overview.write(row, 1, val, val_fmt)
        overview.merge_range("D5:F5", "INTERPRETATION NOTES", section_fmt)
        notes = [
            "The 20-call list is sorted only by Days Old, highest to lowest. Equal-age calls retain their source order.",
            "Repeated ticket rows can reflect multiple component/spare details; ranking consolidates by Ticket No and aggregates those details.",
            "A ticket missing from a later pending export is not automatically labelled closed.",
            "The selected snapshot timestamp is taken from the filename when available; otherwise the date-range label is used and export time is unknown.",
            _snapshot_status(analysis),
        ]
        for i, note in enumerate(notes, start=6):
            overview.merge_range(i - 1, 3, i - 1, 5, note, note_fmt)
            overview.set_row(i - 1, 35)

        tables: List[Tuple[str, pd.DataFrame]] = []
        top_cols = [c for c in ["Order", "Ticket No", "Days Old", "Franchise Name", "Customer Name", "Service Executive Name", "Technician Name", "Call Type", "Status", "Executive % of Top 20", "Missing Executive", "Missing Technician", "Source Row Count", "Spare", "SpareDesc", "Additive", "AdditiveDesc", "Accessories", "AccessoriesDesc"] if c in analysis["top20"].columns]
        tables.append(("20 Oldest Calls", analysis["top20"][top_cols].copy()))
        tables.append(("Top 20 by Executive", analysis.get("top20_executive_summary", pd.DataFrame()).copy()))
        tables.append(("Current Pending", analysis["priorities"].copy()))
        tables.append(("Snapshot Comparison", analysis["comparison"].copy()))
        tables.append(("Backlog by Franchise", analysis["franchise_summary"].copy()))
        tables.append(("Executive Workload", analysis["executive_summary"].copy()))
        tables.append(("Ageing Summary", analysis["age_summary"].copy()))
        tables.append(("Status Summary", analysis["status_summary"].copy()))
        tables.append(("CSAT Summary", analysis["csat_summary"].copy()))
        tables.append(("CSAT Snapshot Comparison", analysis["csat_comparison"].copy()))
        tables.append(("Data Quality", analysis["data_quality"].copy()))

        if not analysis["registered"].empty:
            tables.append(("Calls Registered", analysis["registered"].copy()))
        else:
            tables.append(("Calls Registered", pd.DataFrame([{"Note": "No Calls Registered report uploaded."}])))
        if not analysis["csat"].empty:
            csat = analysis["csat"]
            chosen_cols = [find_col(csat, [alias]) for alias in ["TicketNo", "Call Type", "Status", "Closed Date", "FrName", "Technician Name", "Branch Exe Name", "Description", "FeedBack", "FeedBack Date"]]
            chosen_cols = [c for c in chosen_cols if c]
            tables.append(("CSAT Feedback Detail", csat[chosen_cols].copy()))

        source_rows = []
        for r in analysis["reports"]:
            source_rows.append({
                "File": r["filename"], "Detected type": r["report_type"], "Source rows": r["rows"],
                "Period": r["metadata"], "Snapshot / sort time": r["snapshot_dt"], "Timestamp basis": r["snapshot_source"],
            })
        tables.append(("Source Files", pd.DataFrame(source_rows)))

        for sheet_name, df in tables:
            # Excel tab name limit is 31 chars; names above fit.
            df = df.copy()
            df.to_excel(writer, sheet_name=sheet_name, index=False, startrow=0)
            ws = writer.sheets[sheet_name]
            ws.freeze_panes(1, 0)
            if len(df.columns):
                ws.autofilter(0, 0, max(1, len(df)), len(df.columns) - 1)
                for col_num, col in enumerate(df.columns):
                    vals = df[col].head(500).map(clean_text).tolist() if not df.empty else []
                    max_len = max([len(clean_text(col))] + [min(len(v), 45) for v in vals])
                    width = min(max(max_len + 2, 11), 38)
                    if norm(col) in {"customername", "suggestednextaction", "franchisename", "technicianname", "serviceexecutivename", "note"}:
                        width = min(max(width, 24), 42)
                    ws.set_column(col_num, col_num, width)
                for col_num, col in enumerate(df.columns):
                    ws.write(0, col_num, col, header_fmt)
                ws.set_row(0, 30)
    buf.seek(0)
    return buf.getvalue()
