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
    # Calls Registered exports also contain Days Old and repeatcall flag, so test them first.
    if "callbookdate" in columns and ("assignon" in columns or "closedtime" in columns):
        return "calls_registered"
    if "daysold" in columns and "repeatcallflag" in columns:
        return "pending_calls"
    return "unknown"


def parse_report(filename: str, file_bytes: bytes, upload_order: int = 0) -> Dict[str, Any]:
    """Read one uploaded workbook and classify it using its headers and metadata."""
    preview = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=None, nrows=15, dtype=object)
    header_idx = _detect_header(preview)
    if header_idx is None:
        raise ValueError("Could not locate a report header row containing Ticket No and report fields.")

    metadata = ""
    for _, row in preview.iterrows():
        for cell in row.tolist():
            val = clean_text(cell)
            if val and "date range" in val.lower():
                metadata = val
                break
        if metadata:
            break
    period_start, period_end = _parse_report_range(metadata)

    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=header_idx, dtype=object)
    # Remove only fully empty / unnamed filler columns. Keep all meaningful source columns.
    drop_cols = []
    for c in df.columns:
        if clean_text(c).lower().startswith("unnamed") and df[c].isna().all():
            drop_cols.append(c)
    if drop_cols:
        df = df.drop(columns=drop_cols)
    df.columns = [clean_text(c) if clean_text(c) else f"Column_{i+1}" for i, c in enumerate(df.columns)]
    df = df.dropna(how="all").reset_index(drop=True)
    report_type = _classify(df)
    if report_type == "unknown":
        raise ValueError("The file was read, but its report type could not be identified. Expected Pending Calls, Calls Registered, or Customer Satisfaction.")

    filename_dt = _filename_timestamp(filename)
    if filename_dt is not None:
        snapshot_dt = filename_dt
        snapshot_source = "timestamp in filename"
    elif period_end is not None:
        # The report period end is a date label, not necessarily the real export time.
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
    """One record per ticket; component/spare fields are consolidated, not discarded."""
    ticket_col = get_ticket_col(df)
    if not ticket_col:
        return pd.DataFrame(columns=list(df.columns))
    working = df.copy()
    working["__ticket_key"] = working[ticket_col].map(clean_text)
    working = working[working["__ticket_key"] != ""].copy()
    part_norms = PART_COLUMNS
    columns = [c for c in working.columns if c != "__ticket_key"]
    records = []
    for key, group in working.groupby("__ticket_key", sort=False, dropna=False):
        rec: Dict[str, Any] = {}
        for col in columns:
            if norm(col) in part_norms:
                rec[col] = join_values(group[col])
            elif norm(col) == "daysold":
                # A ticket can occupy multiple component rows; use its greatest age only.
                numeric_ages = pd.to_numeric(group[col], errors="coerce").dropna()
                rec[col] = int(numeric_ages.max()) if not numeric_ages.empty else ""
            elif norm(col) == "callbookdate":
                # When Days Old is blank, the oldest valid booking date is the age fallback.
                parsed_dates = pd.to_datetime(group[col], errors="coerce", format="mixed").dropna()
                rec[col] = parsed_dates.min() if not parsed_dates.empty else choose_value(group[col])
            else:
                rec[col] = choose_value(group[col])
        rec[ticket_col] = clean_text(key)
        rec["Source Row Count"] = len(group)
        records.append(rec)
    return pd.DataFrame(records)


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


def snapshot_comparison(previous: Optional[Dict[str, Any]], current: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, int]]:
    # Keep the hourly movement numbers on the same eligible call-type population as the Top 20.
    cur_raw = filter_pending_call_types(current["df"])
    cur_ticket = get_ticket_col(cur_raw)
    if not cur_ticket:
        return pd.DataFrame(), {"new": 0, "continuing": 0, "missing": 0, "status_changed": 0, "age_increased": 0}
    cur_map = {}
    cur_grouped = summarize_pending_rows(cur_raw)
    cur_tcol = get_ticket_col(cur_grouped)
    age_col_cur = find_col(cur_grouped, ["Days Old"])
    exec_col_cur = find_col(cur_grouped, ["Service Executive Name"])
    for _, r in cur_grouped.iterrows():
        key = clean_text(r.get(cur_tcol, ""))
        cur_map[key] = {
            "status": clean_text(r.get(find_col(cur_grouped, ["Status"]), "")),
            "age": pd.to_numeric(pd.Series([r.get(age_col_cur, 0)]), errors="coerce").fillna(0).iloc[0] if age_col_cur else 0,
            "executive": clean_text(r.get(exec_col_cur, "")) if exec_col_cur else "",
        }
    prev_map = {}
    if previous:
        prev_raw = filter_pending_call_types(previous["df"])
        prev_grouped = summarize_pending_rows(prev_raw)
        prev_tcol = get_ticket_col(prev_grouped)
        age_col_prev = find_col(prev_grouped, ["Days Old"])
        exec_col_prev = find_col(prev_grouped, ["Service Executive Name"])
        status_col_prev = find_col(prev_grouped, ["Status"])
        for _, r in prev_grouped.iterrows():
            key = clean_text(r.get(prev_tcol, ""))
            prev_map[key] = {
                "status": clean_text(r.get(status_col_prev, "")) if status_col_prev else "",
                "age": pd.to_numeric(pd.Series([r.get(age_col_prev, 0)]), errors="coerce").fillna(0).iloc[0] if age_col_prev else 0,
                "executive": clean_text(r.get(exec_col_prev, "")) if exec_col_prev else "",
            }

    prev_ids, cur_ids = set(prev_map), set(cur_map)
    records = []
    status_changed = age_increased = 0
    for key in sorted(prev_ids | cur_ids):
        prev = prev_map.get(key)
        cur = cur_map.get(key)
        if prev and cur:
            status_diff = bool(prev["status"] and cur["status"] and norm(prev["status"]) != norm(cur["status"]))
            age_diff = float(cur["age"]) > float(prev["age"])
            if status_diff:
                status_changed += 1
            if age_diff:
                age_increased += 1
            if status_diff and age_diff:
                movement = "Present; status changed and age increased"
            elif status_diff:
                movement = "Present; status changed"
            elif age_diff:
                movement = "Present; age increased"
            else:
                movement = "Present in both snapshots"
        elif cur:
            movement = "Newly present in latest pending report"
        else:
            movement = "No longer present in latest report (not proof of closure)"
        records.append({
            "Ticket No": key,
            "Movement": movement,
            "Previous Status": prev["status"] if prev else "",
            "Current Status": cur["status"] if cur else "",
            "Previous Days Old": prev["age"] if prev else "",
            "Current Days Old": cur["age"] if cur else "",
            "Previous Executive": prev["executive"] if prev else "",
            "Current Executive": cur["executive"] if cur else "",
        })
    counts = {
        "new": len(cur_ids - prev_ids),
        "continuing": len(cur_ids & prev_ids),
        "missing": len(prev_ids - cur_ids),
        "status_changed": status_changed,
        "age_increased": age_increased,
    }
    return pd.DataFrame(records), counts


def _latest_by_type(reports: List[Dict[str, Any]], report_type: str, on_or_before: Optional[date] = None) -> Optional[Dict[str, Any]]:
    items = [r for r in reports if r["report_type"] == report_type]
    if on_or_before:
        bound = pd.Timestamp(datetime.combine(on_or_before, time.max))
        eligible = [r for r in items if r["snapshot_dt"] <= bound or (r["period_end"] is not None and r["period_end"].date() <= on_or_before)]
        if eligible:
            items = eligible
        elif items:
            # No older report available; return latest available and let the UI identify its period.
            pass
    return sorted(items, key=lambda r: (r["snapshot_dt"], r["upload_order"]))[-1] if items else None


def analyze_reports(reports: List[Dict[str, Any]], analysis_date: Optional[date] = None) -> Dict[str, Any]:
    pending_reports = sorted(
        [r for r in reports if r["report_type"] == "pending_calls"],
        key=lambda r: (r["snapshot_dt"], r["upload_order"]),
    )
    if not pending_reports:
        raise ValueError("Upload at least one Pending Call report to generate the oldest-first pending-call list.")
    if analysis_date is None:
        analysis_date = pending_reports[-1]["snapshot_dt"].date()
    bound = pd.Timestamp(datetime.combine(analysis_date, time.max))
    eligible_pending = [r for r in pending_reports if r["snapshot_dt"] <= bound]
    if not eligible_pending:
        raise ValueError("No Pending Call snapshot exists on or before the selected analysis date. Choose a later date or upload an earlier report.")
    current = eligible_pending[-1]
    prev_candidates = [r for r in pending_reports if (r["snapshot_dt"], r["upload_order"]) < (current["snapshot_dt"], current["upload_order"])]
    previous = prev_candidates[-1] if prev_candidates else None

    current_eligible_rows = filter_pending_call_types(current["df"])
    current_ticket_df = summarize_pending_rows(current_eligible_rows)
    # Courtesy Call / Courtesy Visit and Dealer Defective call types are excluded from all pending-call KPIs and outputs.
    # Pending Reason is intentionally excluded from analysis: it is often blank/inconsistent in source exports.
    current_ticket_df = current_ticket_df.drop(columns=[c for c in current_ticket_df.columns if norm(c) == "pendingreason"], errors="ignore")
    priorities = enrich_pending(current_ticket_df, current["snapshot_dt"])
    comparisons, movement_counts = snapshot_comparison(previous, current)

    csat_report = _latest_by_type(reports, "customer_satisfaction", analysis_date)
    reg_report = _latest_by_type(reports, "calls_registered", analysis_date)
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
    csat_metrics = {"total": 0, "happy": 0, "ok": 0, "not_happy": 0, "happy_pct": None, "not_happy_pct": None}
    if csat_feedback_col:
        feedback = csat[csat_feedback_col].map(lambda v: clean_text(v).upper())
        counts = feedback.value_counts(dropna=False)
        total = int(counts.sum())
        csat_summary = pd.DataFrame([{"Feedback": k if k else "Blank", "Count": int(v), "Share %": round(100 * int(v) / total, 2) if total else 0} for k, v in counts.items()])
        csat_metrics = {
            "total": total,
            "happy": int((feedback == "HAPPY").sum()),
            "ok": int((feedback == "OK").sum()),
            "not_happy": int((feedback == "NOT HAPPY").sum()),
            "happy_pct": round(100 * (feedback == "HAPPY").sum() / total, 2) if total else None,
            "not_happy_pct": round(100 * (feedback == "NOT HAPPY").sum() / total, 2) if total else None,
        }

    # Compare the latest Customer Satisfaction snapshot with the previous available snapshot.
    csat_items = sorted(
        [r for r in reports if r["report_type"] == "customer_satisfaction" and
         (r["snapshot_dt"] <= bound or (r["period_end"] is not None and r["period_end"].date() <= analysis_date))],
        key=lambda r: (r["snapshot_dt"], r["upload_order"]),
    )
    csat_previous_report = None
    if csat_report is not None:
        prior_items = [r for r in csat_items if (r["snapshot_dt"], r["upload_order"]) < (csat_report["snapshot_dt"], csat_report["upload_order"])]
        if prior_items:
            csat_previous_report = prior_items[-1]
    csat_comparison = pd.DataFrame(columns=["Feedback", "Previous Count", "Current Count", "Count Change", "Previous Share %", "Current Share %", "Share Change (pp)"])
    csat_movement = {"previous_rows": None, "current_rows": len(csat), "new_tickets": None, "missing_tickets": None, "happy_delta_pp": None, "not_happy_delta_pp": None}
    if csat_previous_report is not None:
        prev_df = csat_previous_report["df"].copy()
        prev_fb_col = find_col(prev_df, ["FeedBack", "Feedback", "Feedback Score"])
        current_fb = csat[csat_feedback_col].map(lambda v: clean_text(v).upper()) if csat_feedback_col else pd.Series(dtype=str)
        prev_fb = prev_df[prev_fb_col].map(lambda v: clean_text(v).upper()) if prev_fb_col else pd.Series(dtype=str)
        prev_total, curr_total = len(prev_fb), len(current_fb)
        prev_counts, curr_counts = prev_fb.value_counts(), current_fb.value_counts()
        feedback_labels = sorted(set(prev_counts.index.tolist()) | set(curr_counts.index.tolist()))
        comp_rows = []
        for label in feedback_labels:
            p_count, c_count = int(prev_counts.get(label, 0)), int(curr_counts.get(label, 0))
            p_share = round(100 * p_count / prev_total, 2) if prev_total else 0.0
            c_share = round(100 * c_count / curr_total, 2) if curr_total else 0.0
            comp_rows.append({"Feedback": label or "Blank", "Previous Count": p_count, "Current Count": c_count,
                              "Count Change": c_count - p_count, "Previous Share %": p_share,
                              "Current Share %": c_share, "Share Change (pp)": round(c_share - p_share, 2)})
        csat_comparison = pd.DataFrame(comp_rows).sort_values("Current Count", ascending=False).reset_index(drop=True)
        prev_ticket_col, curr_ticket_col = get_ticket_col(prev_df), get_ticket_col(csat)
        prev_ids = {clean_text(v) for v in prev_df[prev_ticket_col].tolist() if clean_text(v)} if prev_ticket_col else set()
        curr_ids = {clean_text(v) for v in csat[curr_ticket_col].tolist() if clean_text(v)} if curr_ticket_col else set()
        prev_happy_share = 100 * (prev_fb == "HAPPY").sum() / prev_total if prev_total else None
        curr_happy_share = 100 * (current_fb == "HAPPY").sum() / curr_total if curr_total else None
        prev_not_happy_share = 100 * (prev_fb == "NOT HAPPY").sum() / prev_total if prev_total else None
        curr_not_happy_share = 100 * (current_fb == "NOT HAPPY").sum() / curr_total if curr_total else None
        csat_movement = {
            "previous_rows": prev_total, "current_rows": curr_total,
            "new_tickets": len(curr_ids - prev_ids), "missing_tickets": len(prev_ids - curr_ids),
            "happy_delta_pp": round(round(curr_happy_share, 2) - round(prev_happy_share, 2), 2) if prev_happy_share is not None and curr_happy_share is not None else None,
            "not_happy_delta_pp": round(round(curr_not_happy_share, 2) - round(prev_not_happy_share, 2), 2) if prev_not_happy_share is not None and curr_not_happy_share is not None else None,
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

    # Data-quality indicators use fields that are generally consistently recorded.
    missing_exec_count = int(priorities["Missing Executive"].sum()) if "Missing Executive" in priorities else 0
    missing_tech_count = int(priorities["Missing Technician"].sum()) if "Missing Technician" in priorities else 0
    missing_schedule_count = int(priorities["Scheduled Date"].map(lambda v: not bool(clean_text(v))).sum()) if "Scheduled Date" in priorities else len(priorities)
    missing_ticket_count = int(sum(not clean_text(v) for v in current_eligible_rows[get_ticket_col(current_eligible_rows)])) if get_ticket_col(current_eligible_rows) else len(current_eligible_rows)
    source_row_duplicates = max(0, len(current_eligible_rows) - len(priorities))
    excluded_pending_rows = int(excluded_pending_call_type_mask(current["df"]).sum())
    excluded_pending_tickets = distinct_ticket_count(current["df"].loc[excluded_pending_call_type_mask(current["df"])])
    data_quality_metrics = {
        "missing_executive": missing_exec_count,
        "missing_technician": missing_tech_count,
        "missing_schedule": missing_schedule_count,
        "missing_ticket_number": missing_ticket_count,
        "rows_beyond_distinct_tickets": source_row_duplicates,
    }

    # Data quality / provenance notes.
    dq_rows = []
    for r in reports:
        if r["report_type"] == "pending_calls":
            tcol = get_ticket_col(r["df"])
            unique = count_distinct(r["df"])
            eligible_df = filter_pending_call_types(r["df"])
            excluded_mask = excluded_pending_call_type_mask(r["df"])
            excluded_df = r["df"].loc[excluded_mask]
            dq_rows.append({
                "File": r["filename"], "Report Type": r["report_type"], "Source Rows": len(r["df"]),
                "Distinct Tickets": unique, "Eligible Rows": len(eligible_df),
                "Eligible Tickets": distinct_ticket_count(eligible_df),
                "Excluded Rows": int(excluded_mask.sum()),
                "Excluded Tickets": distinct_ticket_count(excluded_df),
                "Rows beyond distinct tickets": max(0, len(eligible_df) - distinct_ticket_count(eligible_df)),
                "Period": r["metadata"], "Snapshot Used For": r["snapshot_dt"].strftime("%Y-%m-%d %H:%M"),
                "Timestamp Basis": r["snapshot_source"],
                "Note": "Courtesy Call/Courtesy Visit and Dealer Defective call types are excluded from pending analysis. Other repeated ticket rows may represent multiple spare/additive/accessory details; component values are aggregated.",
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
        {"File": "Analysis rule", "Report Type": "Age-only ordering", "Source Rows": "", "Distinct Tickets": "", "Eligible Rows": "", "Eligible Tickets": "", "Excluded Rows": "", "Excluded Tickets": "", "Rows beyond distinct tickets": "", "Period": "Days Old descending; oldest first", "Snapshot Used For": "", "Timestamp Basis": "Days Old column; Call Book Date used only if Days Old is blank", "Note": "The only call-ordering key is age. Equal-age tickets retain source order. Courtesy/Courtesy Visit and Dealer Defective calls are excluded."},
        {"File": "Analysis rule", "Report Type": "Snapshot comparison", "Source Rows": "", "Distinct Tickets": "", "Eligible Rows": "", "Eligible Tickets": "", "Excluded Rows": "", "Excluded Tickets": "", "Rows beyond distinct tickets": "", "Period": "New / continuing / no longer present / status changed / age increased", "Snapshot Used For": "", "Timestamp Basis": "Ticket-number matching on eligible call types", "Note": "A ticket absent from a later Pending Call export is not automatically labelled closed."},
    ]

    return {
        "analysis_date": analysis_date,
        "reports": reports,
        "pending_reports": pending_reports,
        "current_pending_report": current,
        "previous_pending_report": previous,
        "current_pending_rows": len(current_eligible_rows),
        "current_unique_tickets": len(priorities),
        "prior_unique_tickets": len(summarize_pending_rows(filter_pending_call_types(previous["df"]))) if previous else None,
        "excluded_pending_rows": excluded_pending_rows,
        "excluded_pending_tickets": excluded_pending_tickets,
        "priorities": priorities,
        "top20": priorities.head(20).copy(),
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
    """Render the 20 oldest pending tickets in a neutral spreadsheet-style layout."""
    import matplotlib.pyplot as plt

    data = analysis["top20"].copy().head(20)
    snap = analysis["current_pending_report"]["snapshot_dt"]
    fig = plt.figure(figsize=(21, 8.6), dpi=160, facecolor="#FFFFFF")
    ax = fig.add_axes([0.012, 0.105, 0.976, 0.755])
    ax.axis("off")
    fig.text(0.018, 0.945, "Pending calls — 20 oldest tickets", fontsize=20, color="#303236")
    fig.text(0.018, 0.908,
             f"Hyderabad Service Branch  |  As of {snap.strftime('%d %b %Y, %I:%M %p')}  |  Ordered by call age, oldest first",
             fontsize=10, color="#5B6570")
    fig.text(0.982, 0.945, f"{analysis['current_unique_tickets']:,} UNIQUE TICKETS",
             fontsize=11, weight="bold", ha="right", color="#303030")

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
    present = [(src, label, width) for src, label, width in col_def if src in data.columns]
    total = sum(c[2] for c in present)
    col_widths = [c[2] / total for c in present]

    rows = []
    for _, row in data.iterrows():
        vals = []
        for src, _, _ in present:
            value = clean_text(row.get(src, ""))
            limit = {
                "Franchise Name": 30,
                "Customer Name": 26,
                "Service Executive Name": 27,
                "Technician Name": 23,
                "Call Type": 20,
                "Status": 20,
            }.get(src)
            if limit and len(value) > limit:
                value = value[:limit - 3] + "..."
            vals.append(value)
        rows.append(vals)

    table = ax.table(
        cellText=rows,
        colLabels=[label for _, label, _ in present],
        cellLoc="left",
        colLoc="center",
        loc="center",
        colWidths=col_widths,
        bbox=[0.0, 0.0, 1.0, 1.0],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8.2)

    centered = {"Order", "Days Old"}
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
            src = present[c][0]
            if src in centered:
                cell.get_text().set_ha("center")
            if src == "Order":
                cell.get_text().set_weight("bold")

    fig.text(0.018, 0.064,
             "Sorted only by Days Old (highest to lowest). Equal-age calls retain their order in the source report.",
             fontsize=9, color="#4F5963")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def make_management_png(analysis: Dict[str, Any]) -> bytes:
    """Create a high-contrast, one-page management summary with fixed non-overlapping panels."""
    import matplotlib.pyplot as plt
    import numpy as np
    import textwrap

    navy = "#18344D"
    teal = "#0B7E83"
    amber = "#C78318"
    red = "#B9363E"
    green = "#237A55"
    ink = "#263746"
    muted = "#435365"
    grid = "#E3E8ED"
    page = "#F3F6F8"

    # A little taller than the earlier version creates more breathing room for labels and notes.
    fig = plt.figure(figsize=(16, 11), dpi=180, facecolor=page)
    fig.text(0.04, 0.962, "SERVICE OPERATIONS | DAILY MANAGEMENT BRIEF",
             fontsize=20, weight="bold", color=navy, va="top")
    fig.text(0.04, 0.923,
             f"Hyderabad Branch  ·  Report date {_date_label(analysis['analysis_date'])}  ·  {_snapshot_status(analysis)}",
             fontsize=10.2, color=muted, va="top")
    snap = analysis["current_pending_report"]["snapshot_dt"]
    fig.text(0.96, 0.962, f"AS OF {snap.strftime('%d %b %Y  %I:%M %p')}",
             fontsize=9.5, weight="bold", color=teal, ha="right", va="top")

    # KPI tiles have consistent widths, baselines, and padding.
    current = analysis["current_unique_tickets"]
    mc = analysis["movement_counts"]
    happy = analysis["csat_metrics"]["happy_pct"]
    kpis = [
        ("PENDING TICKETS", f"{current:,}", navy),
        ("AGED 7+ DAYS", f"{analysis['older_7']:,}", amber),
        ("ESCALATED", f"{analysis['escalated']:,}", red),
        ("REPEAT FLAGS", f"{analysis['repeat_calls']:,}", teal),
        ("NEW IN LATEST SNAPSHOT", f"{mc['new']:,}" if analysis["previous_pending_report"] else "—", "#2E6D9C"),
        ("HAPPY FEEDBACK", f"{happy:.1f}%" if happy is not None else "—", green),
    ]
    x0, step, card_w = 0.04, 0.155, 0.143
    for i, (label, value, value_color) in enumerate(kpis):
        x = x0 + i * step
        fig.patches.append(plt.Rectangle(
            (x, 0.817), card_w, 0.078, transform=fig.transFigure,
            facecolor="#FFFFFF", edgecolor="#D8E0E7", linewidth=1.0, zorder=0
        ))
        fig.text(x + 0.010, 0.868, label, fontsize=7.5, color=muted,
                 weight="bold", va="center")
        fig.text(x + 0.010, 0.835, value, fontsize=17, color=value_color,
                 weight="bold", va="center")

    def style_axis(ax, title, title_size=11.5):
        ax.set_facecolor("#FFFFFF")
        ax.set_title(title, loc="left", color=navy, fontsize=title_size,
                     fontweight="bold", pad=10)
        ax.tick_params(axis="both", labelsize=8.3, colors=ink, length=0, pad=5)
        ax.grid(axis="x", color=grid, linewidth=0.75)
        ax.set_axisbelow(True)
        for spine in ax.spines.values():
            spine.set_visible(False)

    # Fixed chart grid: axes never intrude into the bottom management notes.
    ax_age = fig.add_axes([0.075, 0.545, 0.37, 0.205])
    age = analysis["age_summary"].copy()
    if not age.empty:
        age["Unique Tickets"] = pd.to_numeric(age["Unique Tickets"], errors="coerce").fillna(0)
        age_colors = ["#A8BAC8", "#8AA9BC", "#648EA8", "#2E8588", "#D39A35", "#B94842"]
        ax_age.barh(age["Age Bucket"].astype(str), age["Unique Tickets"],
                    color=age_colors[:len(age)], height=0.62)
        ax_age.invert_yaxis()
        style_axis(ax_age, "Pending backlog by age")
        ax_age.tick_params(axis="y", labelsize=8.6, colors=ink)
        xmax = max(float(age["Unique Tickets"].max()), 1)
        ax_age.set_xlim(0, xmax * 1.13)
        for y, val in enumerate(age["Unique Tickets"].to_numpy()):
            ax_age.text(val + xmax * 0.015, y, f"{int(val):,}", va="center",
                        ha="left", fontsize=8.2, color=ink, weight="bold")
        ax_age.grid(axis="y", visible=False)
    else:
        ax_age.axis("off")
        ax_age.text(0.03, 0.5, "Ageing data unavailable", color=muted, fontsize=10,
                    transform=ax_age.transAxes, va="center")

    ax_exec = fig.add_axes([0.575, 0.545, 0.35, 0.205])
    ex = analysis["executive_summary"].copy()
    if not ex.empty:
        ex["Pending Tickets"] = pd.to_numeric(ex["Pending Tickets"], errors="coerce").fillna(0)
        ex["Aged 7+ Days"] = pd.to_numeric(ex["Aged 7+ Days"], errors="coerce").fillna(0)
        ex = ex.sort_values("Pending Tickets", ascending=False, kind="stable").head(5).copy()
        labels = [textwrap.shorten(clean_text(x), width=21, placeholder="…") for x in ex["Executive"].astype(str)]
        y = np.arange(len(ex))
        totals = ex["Pending Tickets"].to_numpy()
        aged = ex["Aged 7+ Days"].to_numpy()
        ax_exec.barh(y, totals, color="#3A78A5", height=0.56)
        ax_exec.set_yticks(y, labels)
        ax_exec.invert_yaxis()
        style_axis(ax_exec, "Executive workload")
        ax_exec.tick_params(axis="y", labelsize=8.1, colors=ink)
        xmax = max(float(totals.max()), 1)
        ax_exec.set_xlim(0, xmax * 1.45)
        for yy, total, aged_v in zip(y, totals, aged):
            ax_exec.text(total + xmax * 0.025, yy,
                         f"{int(total):,} total  ·  {int(aged_v):,} aged 7+",
                         va="center", ha="left", fontsize=7.8, color=ink)
        ax_exec.grid(axis="y", visible=False)
    else:
        ax_exec.axis("off")
        ax_exec.text(0.03, 0.5, "Executive assignment data unavailable", color=muted,
                     fontsize=10, transform=ax_exec.transAxes, va="center")

    ax_fr = fig.add_axes([0.075, 0.295, 0.37, 0.19])
    fr = analysis["franchise_summary"].copy()
    # This chart intentionally includes only distinct pending tickets aged strictly more than 2 days.
    # It is separate from the overall backlog count shown in other management KPIs.
    if not fr.empty and "Aged >2 Days" in fr.columns:
        fr["Aged >2 Days"] = pd.to_numeric(fr["Aged >2 Days"], errors="coerce").fillna(0)
        fr = fr.loc[fr["Aged >2 Days"] > 0]
        fr = fr.sort_values("Aged >2 Days", ascending=False, kind="stable").head(5).copy()
    else:
        fr = pd.DataFrame()
    if not fr.empty:
        # Show complete franchise names by wrapping them onto multiple lines instead of truncating.
        labels = [textwrap.fill(clean_text(x), width=14, break_long_words=False, break_on_hyphens=False)
                  for x in fr["Franchise"].astype(str)]
        y = np.arange(len(fr))
        aged_over_two = fr["Aged >2 Days"].to_numpy()
        ax_fr.barh(y, aged_over_two, color="#3A78A5", height=0.56)
        ax_fr.set_yticks(y, labels)
        ax_fr.invert_yaxis()
        style_axis(ax_fr, "Franchise calls pending >2 days")
        ax_fr.tick_params(axis="y", labelsize=7.6, colors=ink, pad=5)
        for tick_label in ax_fr.get_yticklabels():
            tick_label.set_multialignment("right")
        xmax = max(float(aged_over_two.max()), 1)
        ax_fr.set_xlim(0, xmax * 1.18)
        for yy, count in zip(y, aged_over_two):
            ax_fr.text(count + xmax * 0.025, yy, f"{int(count):,}",
                       va="center", ha="left", fontsize=8.3, color=ink, weight="bold")
        ax_fr.grid(axis="y", visible=False)
    else:
        ax_fr.axis("off")
        ax_fr.text(0.03, 0.5, "No franchise calls aged >2 days", color=muted,
                   fontsize=10, transform=ax_fr.transAxes, va="center")

    ax_feedback = fig.add_axes([0.575, 0.295, 0.35, 0.19])
    csat_summary = analysis["csat_summary"].copy()
    if not csat_summary.empty:
        order = {"HAPPY": 0, "OK": 1, "NOT HAPPY": 2}
        csat_summary["_ord"] = csat_summary["Feedback"].map(lambda x: order.get(clean_text(x).upper(), 3))
        csat_summary = csat_summary.sort_values("_ord")
        labels = csat_summary["Feedback"].astype(str).str.upper().tolist()
        vals = pd.to_numeric(csat_summary["Share %"], errors="coerce").fillna(0).tolist()
        color_map = {"HAPPY": green, "OK": amber, "NOT HAPPY": red}
        bars = ax_feedback.bar(labels, vals, color=[color_map.get(x, "#8A9BA9") for x in labels], width=0.55)
        style_axis(ax_feedback, "Customer feedback mix")
        ax_feedback.set_ylim(0, 112)
        ax_feedback.tick_params(axis="x", labelsize=8.4, colors=ink, pad=6)
        ax_feedback.tick_params(axis="y", labelsize=8.0, colors=ink)
        ax_feedback.grid(axis="x", visible=False)
        for bar, val in zip(bars, vals):
            ax_feedback.text(bar.get_x() + bar.get_width() / 2, min(bar.get_height() + 2.0, 106),
                             f"{val:.1f}%", ha="center", va="bottom", fontsize=9,
                             color=ink, weight="bold")
    else:
        ax_feedback.axis("off")
        ax_feedback.text(0.03, 0.5, "No Customer Satisfaction report uploaded",
                         transform=ax_feedback.transAxes, color=muted, fontsize=10, va="center")

    # Bounded, two-column insight panel. Explicit wrapping prevents long source data from
    # running into charts or each other on the exported PNG.
    fig.patches.append(plt.Rectangle((0.04, 0.045), 0.92, 0.20,
                                     transform=fig.transFigure, facecolor="#FFFFFF",
                                     edgecolor="#D8E0E7", linewidth=1.0, zorder=0))
    fig.text(0.055, 0.222, "SNAPSHOT MOVEMENT", fontsize=9.0, color=teal,
             weight="bold", va="center")
    prev = analysis["previous_pending_report"]
    if prev:
        movement_text = (
            f"Previous: {prev['snapshot_dt'].strftime('%d %b %Y %I:%M %p')}  ·  "
            f"Newly present: {mc['new']:,}  ·  Continuing: {mc['continuing']:,}\n"
            f"No longer listed: {mc['missing']:,}  ·  Status changed: {mc['status_changed']:,}  ·  Age increased: {mc['age_increased']:,}"
        )
    else:
        movement_text = "Only one Pending Call snapshot was uploaded; hourly movement cannot yet be calculated."
    fig.text(0.055, 0.199, movement_text, fontsize=8.4, color=ink,
             va="top", linespacing=1.45)
    caveat = "Note: a call no longer listed in a later snapshot is not proof of closure."
    fig.text(0.055, 0.145, caveat, fontsize=8.1, color=red, va="top")
    status_counts = analysis["status_summary"].head(4)
    status_line = "Status mix: " + "  ·  ".join(
        f"{clean_text(row['Status']) or 'Unspecified'} {int(row['Unique Tickets']):,}"
        for _, row in status_counts.iterrows()
    ) if not status_counts.empty else "Status mix: not available"
    fig.text(0.055, 0.118, status_line, fontsize=8.0, color=muted, va="top")

    # Second column for data quality and coverage, separated by a vertical divider.
    fig.lines.append(plt.Line2D([0.655, 0.655], [0.065, 0.225], transform=fig.transFigure,
                                color="#E0E6EB", linewidth=1.0))
    fig.text(0.675, 0.222, "DATA QUALITY & COVERAGE", fontsize=9.0, color=teal,
             weight="bold", va="center")
    dq = analysis["data_quality_metrics"]
    dq_text = (
        f"Missing executive: {dq['missing_executive']:,}  ·  Missing technician: {dq['missing_technician']:,}\n"
        f"Missing ticket ID: {dq['missing_ticket_number']:,}  ·  Extra source rows: {dq['rows_beyond_distinct_tickets']:,}"
    )
    fig.text(0.675, 0.199, dq_text, fontsize=8.2, color=ink, va="top", linespacing=1.45)
    reg = analysis["registered_count"]
    if reg is not None:
        reg_line = f"Calls registered: {reg:,} distinct tickets ({analysis['registered_date_coverage']})."
        if analysis["closed_count"] is not None:
            reg_line += f" Closure timestamps: {analysis['closed_count']:,}."
    else:
        reg_line = "Calls registered for selected date: unavailable from uploaded report period."
    reg_line = textwrap.fill(reg_line, width=61)
    fig.text(0.675, 0.145, reg_line, fontsize=7.9, color=muted, va="top", linespacing=1.35)

    fig.text(0.96, 0.020,
             "INTERNAL WORKING REPORT  ·  Verify source coverage before formal management circulation",
             ha="right", va="center", fontsize=7.5, color="#435365")

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
            ("Happy feedback share", f"{analysis['csat_metrics']['happy_pct']:.2f}%" if analysis["csat_metrics"]["happy_pct"] is not None else "N/A"),
            ("Not-happy feedback share", f"{analysis['csat_metrics']['not_happy_pct']:.2f}%" if analysis["csat_metrics"]["not_happy_pct"] is not None else "N/A"),
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
        top_cols = [c for c in ["Order", "Ticket No", "Days Old", "Franchise Name", "Customer Name", "Service Executive Name", "Technician Name", "Call Type", "Status", "Missing Executive", "Missing Technician", "Source Row Count", "Spare", "SpareDesc", "Additive", "AdditiveDesc", "Accessories", "AccessoriesDesc"] if c in analysis["priorities"].columns]
        tables.append(("20 Oldest Calls", analysis["top20"][top_cols].copy()))
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
