from __future__ import annotations

import io
import hashlib
from pathlib import Path

import pandas as pd
import streamlit as st

import matplotlib
matplotlib.use("Agg")

from service_ops_engine_v3 import analyze_reports, parse_report, make_management_png, make_priority_png, scope_analysis

APP_TITLE = "Service Operations Intelligence"

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Official logo artwork supplied by the user, embedded so deployment needs no extra asset files.
IFB_LOGO_DATA_URI = "data:image/png;base64,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"

st.markdown(
    """
    <style>
      :root {
        --navy: #172634;
        --navy-2: #223444;
        --ifb-red: #D6424E;
        --ink: #EDF2F7;
        --muted: #B3C0CC;
        --line: #344657;
        --paper: #1A2733;
        --page: #101820;
        --brand-serif: "Rockwell Extra Bold", Rockwell, "Roboto Slab", Georgia, serif;
        --body-sans: Arial, "Helvetica Neue", Helvetica, sans-serif;
      }
      html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
        font-family: var(--body-sans) !important;
        color: var(--ink) !important;
        background: var(--page) !important;
      }
      #MainMenu, footer, header { visibility: hidden; }
      [data-testid="stAppViewContainer"] {
        background: radial-gradient(ellipse at top left, #1B2A37 0%, #101820 58%, #0D141B 100%) !important;
      }
      [data-testid="stMain"] .block-container {
        max-width: 1450px;
        padding-top: 1.65rem;
        padding-bottom: 2.2rem;
      }
      .hero {
        position: relative;
        overflow: hidden;
        display: flex;
        align-items: center;
        gap: 21px;
        min-height: 145px;
        padding: 25px 27px;
        margin-bottom: 22px;
        border: 1px solid #344758;
        border-radius: 19px;
        background: linear-gradient(112deg, #1A2733 0%, #1D2D3B 55%, #243441 100%);
        box-shadow: 0 12px 32px rgba(0, 0, 0, .24);
      }
      .brand-logo {
        position: relative;
        z-index: 2;
        display: block;
        flex: 0 0 auto;
        width: 138px;
        height: auto;
        object-fit: contain;
        border: 1px solid #E6E9EC;
        border-radius: 9px;
        background: #FFFFFF;
        padding: 3px;
      }
      .hero-copy { position: relative; z-index: 2; min-width: 0; }
      .eyebrow {
        margin: 0 0 7px 0;
        color: #FF737B;
        font-family: var(--body-sans);
        font-size: 10px;
        line-height: 1.3;
        font-weight: 800;
        letter-spacing: .16em;
      }
      .hero h1 {
        margin: 0;
        color: #F4F7FA;
        font-family: var(--brand-serif);
        font-size: clamp(23px, 3.5vw, 31px);
        font-weight: 800;
        line-height: 1.12;
        letter-spacing: -.025em;
      }
      .hero p {
        margin: 9px 0 0 0;
        color: #BDCAD4;
        font-family: var(--body-sans);
        font-size: 13px;
        line-height: 1.45;
      }
      .upload-title {
        display: flex;
        align-items: center;
        gap: 11px;
        margin: 2px 0 4px 0;
        color: #F0F4F8;
        font-family: var(--body-sans);
        font-size: 16px;
        font-weight: 750;
      }
      .upload-subtitle {
        margin: 0 0 13px 0;
        color: #AEBCC8;
        font-family: var(--body-sans);
        font-size: 12px;
        line-height: 1.5;
      }
      .upload-symbol {
        width: 33px;
        height: 33px;
        border-radius: 10px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        color: #FFFFFF;
        background: #3A2730;
        font-size: 18px;
      }
      div[data-testid="stFileUploader"] {
        padding: 10px 12px;
        border: 1px dashed #506273;
        border-radius: 14px;
        background: rgba(27, 40, 52, .92) !important;
      }
      div[data-testid="stFileUploader"] section {
        border: 0 !important;
        background: transparent !important;
      }
      div[data-testid="stFileUploader"] section > div,
      div[data-testid="stFileUploaderDropzone"] {
        background: transparent !important;
        color: #EAF0F5 !important;
      }
      div[data-testid="stFileUploader"] button,
      div[data-testid="stFileUploader"] section button,
      div[data-testid="stFileUploaderDropzone"] button,
      div[data-testid="stFileUploaderDropzone"] button[kind="secondary"],
      div[data-testid="stFileUploaderDropzone"] button[data-testid="stBaseButton-secondary"] {
        color: #F5F7FA !important;
        -webkit-text-fill-color: #F5F7FA !important;
        background: #354858 !important;
        border: 1px solid #D6424E !important;
        opacity: 1 !important;
        visibility: visible !important;
        text-shadow: none !important;
        box-shadow: none !important;
      }
      div[data-testid="stFileUploaderDropzone"] button span {
        color: inherit !important;
        -webkit-text-fill-color: inherit !important;
      }
      div[data-testid="stFileUploader"] button:hover,
      div[data-testid="stFileUploader"] section button:hover,
      div[data-testid="stFileUploaderDropzone"] button:hover {
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
        background: #C93642 !important;
        border-color: #F06C75 !important;
      }
      div[data-testid="stFileUploader"] button:focus,
      div[data-testid="stFileUploader"] button:focus-visible,
      div[data-testid="stFileUploaderDropzone"] button:focus,
      div[data-testid="stFileUploaderDropzone"] button:focus-visible {
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
        background: #354858 !important;
        outline: 2px solid rgba(255, 115, 123, .55) !important;
        outline-offset: 2px !important;
      }
      div[data-testid="stDownloadButton"] button {
        width: 100%;
        min-height: 53px;
        white-space: normal;
        border-radius: 12px;
        font-family: var(--body-sans);
        font-size: 13px;
        font-weight: 750;
        border: 1px solid #526170;
        box-shadow: 0 6px 16px rgba(0, 0, 0, .24);
        transition: transform .12s ease, box-shadow .12s ease;
      }
      div[data-testid="stDownloadButton"] button:hover {
        transform: translateY(-1px);
        box-shadow: 0 9px 22px rgba(0, 0, 0, .32);
      }
      div[data-testid="stHorizontalBlock"] > div:first-child div[data-testid="stDownloadButton"] button {
        background: linear-gradient(135deg, #D6424E, #AE2733) !important;
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
      }
      div[data-testid="stHorizontalBlock"] > div:nth-child(2) div[data-testid="stDownloadButton"] button {
        background: linear-gradient(135deg, #354858, #263541) !important;
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
      }
      /* Dark, high-contrast status alerts. */
      [data-testid="stAlert"], div.stAlert, [role="alert"] {
        background: #3A311D !important;
        border: 1px solid #8B7131 !important;
        border-left: 5px solid #E5B94B !important;
        border-radius: 11px !important;
        color: #FFE7A3 !important;
        box-shadow: none !important;
      }
      [data-testid="stAlert"] *, div.stAlert *, [role="alert"] * {
        color: #FFE7A3 !important;
        -webkit-text-fill-color: #FFE7A3 !important;
        text-shadow: none !important;
        opacity: 1 !important;
      }
      [data-testid="stAlert"] svg, div.stAlert svg, [role="alert"] svg {
        color: #F1C75B !important;
        fill: #F1C75B !important;
      }
      div[data-testid="stFileUploader"] small,
      div[data-testid="stFileUploader"] [data-testid="stMarkdownContainer"],
      div[data-testid="stFileUploader"] [data-testid="stMarkdownContainer"] p {
        color: #B8C6D1 !important;
        -webkit-text-fill-color: #B8C6D1 !important;
        opacity: 1 !important;
      }
      .ready-line {
        display: flex;
        align-items: center;
        gap: 9px;
        margin: 20px 0 13px 0;
        padding: 11px 14px;
        border: 1px solid #354858;
        border-radius: 11px;
        background: rgba(30, 45, 58, .9);
        color: #D1DCE5;
        font-family: var(--body-sans);
        font-size: 12px;
        font-weight: 700;
      }
      .ready-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #54C18B;
        box-shadow: 0 0 0 4px rgba(84,193,139,.13);
      }
      /* Persistent dark tab navigation with high contrast in every state. */
      div[data-testid="stRadio"] div[role="radiogroup"] {
        display: flex !important;
        flex-wrap: nowrap !important;
        align-items: stretch !important;
        gap: 10px !important;
        width: 100% !important;
        padding: 7px !important;
        margin: 6px 0 14px 0 !important;
        background: #151F29 !important;
        border: 1px solid #344657 !important;
        border-radius: 14px !important;
      }
      div[data-testid="stRadio"] div[role="radiogroup"] > div[data-baseweb="radio"] {
        flex: 1 1 0 !important;
        min-width: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        border: 0 !important;
        background: transparent !important;
      }
      div[data-testid="stRadio"] div[role="radiogroup"] label[data-baseweb="radio"],
      div[data-testid="stRadio"] div[role="radiogroup"] > div[data-baseweb="radio"] label,
      div[data-testid="stRadio"] div[role="radiogroup"] > label,
      div[data-testid="stRadio"] div[role="radiogroup"] label {
        box-sizing: border-box !important;
        width: 100% !important;
        min-height: 48px !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        margin: 0 !important;
        padding: 10px 14px !important;
        border: 1px solid #405465 !important;
        border-radius: 10px !important;
        background: #243442 !important;
        color: #E9F0F5 !important;
        -webkit-text-fill-color: #E9F0F5 !important;
        opacity: 1 !important;
        cursor: pointer !important;
      }
      div[data-testid="stRadio"] div[role="radiogroup"] label *,
      div[data-testid="stRadio"] div[role="radiogroup"] label p,
      div[data-testid="stRadio"] div[role="radiogroup"] label span,
      div[data-testid="stRadio"] div[role="radiogroup"] label div,
      div[data-testid="stRadio"] div[role="radiogroup"] label [data-testid="stMarkdownContainer"] {
        color: inherit !important;
        -webkit-text-fill-color: inherit !important;
        opacity: 1 !important;
        text-shadow: none !important;
        font-weight: 800 !important;
      }
      div[data-testid="stRadio"] div[role="radiogroup"] label > div:first-child { display: none !important; }
      div[data-testid="stRadio"] div[role="radiogroup"] > div[data-baseweb="radio"]:has(input:checked) label,
      div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked),
      div[data-testid="stRadio"] div[role="radiogroup"] label[aria-checked="true"] {
        background: #C93642 !important;
        border-color: #F06C75 !important;
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
        box-shadow: 0 3px 12px rgba(214,66,78,.24) !important;
      }
      div[data-testid="stRadio"] div[role="radiogroup"] > div[data-baseweb="radio"]:has(input:checked) label *,
      div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) *,
      div[data-testid="stRadio"] div[role="radiogroup"] label[aria-checked="true"] * {
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
      }
      div[data-testid="stRadio"] div[role="radiogroup"] label:hover:not(:has(input:checked)) {
        background: #354858 !important;
        border-color: #D6424E !important;
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
      }
      div[data-testid="stRadio"] div[role="radiogroup"] label:hover:not(:has(input:checked)) * {
        color: #FFFFFF !important;
        -webkit-text-fill-color: #FFFFFF !important;
      }
      div[data-testid="stRadio"] div[role="radiogroup"] label p {
        margin: 0 !important;
        font-size: 14px !important;
        line-height: 1.25 !important;
        white-space: normal !important;
        text-align: center !important;
      }
      div[data-testid="stRadio"] div[role="radiogroup"] input:focus-visible + div {
        outline: 2px solid #FF737B !important;
        outline-offset: 2px !important;
      }
      [data-testid="stSelectbox"] label,
      [data-testid="stMultiSelect"] label,
      [data-testid="stFileUploader"] label {
        color: #E7EDF3 !important;
        -webkit-text-fill-color: #E7EDF3 !important;
        font-weight: 750 !important;
      }
      [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
      [data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
        background: #1D2B37 !important;
        border-color: #4A5D6D !important;
        color: #EDF2F7 !important;
      }
      [data-testid="stSelectbox"] div[data-baseweb="select"] *,
      [data-testid="stMultiSelect"] div[data-baseweb="select"] * {
        color: #EDF2F7 !important;
        -webkit-text-fill-color: #EDF2F7 !important;
      }
      [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] *,
      [data-testid="stMarkdownContainer"], [data-testid="stMarkdownContainer"] p,
      .stMarkdown, .stMarkdown p, .stText, label {
        color: #D5DEE6;
      }
      [data-testid="stExpander"] {
        background: #192631 !important;
        border: 1px solid #344657 !important;
        border-radius: 10px !important;
      }
      [data-testid="stExpander"] summary,
      [data-testid="stExpander"] summary * { color: #EAF0F5 !important; }
      @media (max-width: 760px) {
        [data-testid="stMain"] .block-container { padding-left: 1rem; padding-right: 1rem; }
        .hero { padding: 20px 16px; gap: 13px; min-height: 126px; }
        .brand-logo { width: 105px; }
        .hero h1 { font-size: 22px; }
        .hero p { font-size: 12px; }
        div[data-testid="stRadio"] div[role="radiogroup"] { gap: 5px !important; padding: 5px !important; }
        div[data-testid="stRadio"] div[role="radiogroup"] label { padding: 8px 5px !important; min-height: 43px !important; }
        div[data-testid="stRadio"] div[role="radiogroup"] label p { font-size: 11px !important; }
      }
    </style>
    <div class="hero">
      <img class="brand-logo" src="IFB_LOGO_HTML_PLACEHOLDER" alt="IFB — Set yourself free" />
      <div class="hero-copy">
        <div class="eyebrow">BRANCH SERVICE OPERATIONS</div>
        <h1>Service Operations Intelligence</h1>
        <p>One upload. Two clear reports.</p>
      </div>
    </div>
    """.replace("IFB_LOGO_HTML_PLACEHOLDER", IFB_LOGO_DATA_URI),
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="upload-title"><span class="upload-symbol">↑</span> Upload your reports</div>
    <div class="upload-subtitle">Select one or more hourly Excel exports. Supporting Soft Closure and customer feedback reports can be included too.</div>
    """,
    unsafe_allow_html=True,
)

uploaded = st.file_uploader(
    "Choose Excel or CSV files",
    type=["xlsx", "xls", "csv"],
    accept_multiple_files=True,
    label_visibility="collapsed",
    help="Select several reports together, including hourly snapshots.",
)

def read_csv_report(filename: str, data: bytes, order: int) -> dict:
    df = pd.read_csv(io.BytesIO(data), dtype=object)
    from service_ops_engine_v3 import _classify

    report_type = _classify(df)
    # CSV exports sometimes have overlapping columns; use the supplied report name
    # to disambiguate a Soft Closure export from Calls Registered.
    name_hint = filename.casefold().replace("_", " ").replace("-", " ")
    if "soft closure" in name_hint or "soft_closure" in filename.casefold():
        report_type = "soft_closure"
    if report_type == "unknown":
        raise ValueError("The CSV columns do not match a supported report type.")
    snapshot_dt = pd.Timestamp.now().floor("min")
    return {
        "filename": filename,
        "bytes": len(data),
        "report_type": report_type,
        "df": df,
        "header_row_excel": 1,
        "metadata": "CSV report (date range not embedded)",
        "period_start": None,
        "period_end": None,
        "snapshot_dt": snapshot_dt,
        "snapshot_source": "upload time fallback (CSV has no embedded export timestamp)",
        "upload_order": order,
        "rows": len(df),
    }


def parse_uploaded_files(files) -> tuple[list[dict], list[str]]:
    reports: list[dict] = []
    errors: list[str] = []
    for idx, file in enumerate(files):
        try:
            raw = file.getvalue()
            if file.name.lower().endswith(".csv"):
                parsed = read_csv_report(file.name, raw, idx)
            else:
                parsed = parse_report(file.name, raw, idx)
            reports.append(parsed)
        except Exception as exc:
            errors.append(f"{file.name}: {exc}")
    return reports, errors
@st.cache_data(show_spinner=False, max_entries=4)
def cached_parse_reports(file_items: tuple[tuple[str, bytes], ...]):
    """Parse each uploaded file once; date selection and view changes reuse the result."""
    reports: list[dict] = []
    errors: list[str] = []
    for idx, (filename, raw) in enumerate(file_items):
        try:
            if filename.lower().endswith(".csv"):
                parsed = read_csv_report(filename, raw, idx)
            else:
                parsed = parse_report(filename, raw, idx)
            reports.append(parsed)
        except Exception as exc:
            errors.append(f"{filename}: {type(exc).__name__}: {exc}")
    return reports, errors


def report_period_end_date(report: dict):
    end = report.get("period_end")
    if end is not None and not pd.isna(end):
        return pd.Timestamp(end).date()
    return pd.Timestamp(report.get("snapshot_dt")).date()


def has_reliable_export_timestamp(report: dict) -> bool:
    return str(report.get("snapshot_source", "")).strip().lower() == "timestamp in filename"


def report_choice_label(report: dict) -> str:
    period_start = report.get("period_start")
    period_end = report.get("period_end")
    if period_start is not None and period_end is not None:
        period_text = f"{pd.Timestamp(period_start):%d %b} – {pd.Timestamp(period_end):%d %b %Y}"
    else:
        period_text = f"Period not embedded · {report_period_end_date(report):%d %b %Y} inferred from export/upload"
    if has_reliable_export_timestamp(report):
        time_text = f"exported {pd.Timestamp(report['snapshot_dt']):%d %b %Y %H:%M:%S}"
    else:
        time_text = "export time not present in filename"
    return f"{report['filename']} · {period_text} · {time_text} · {report.get('rows', 0):,} rows"


def latest_candidates_for_type(reports: list[dict], report_type: str, cutoff_date=None):
    """Return best automatic choice, latest-date candidates and ambiguity flag."""
    eligible = [r for r in reports if r.get("report_type") == report_type and
                (cutoff_date is None or report_period_end_date(r) <= cutoff_date)]
    if not eligible:
        return None, [], False
    latest_date = max(report_period_end_date(r) for r in eligible)
    candidates = [r for r in eligible if report_period_end_date(r) == latest_date]
    if len(candidates) == 1:
        return candidates[0], candidates, False
    # If every candidate has a real filename timestamp and those timestamps differ,
    # choose the newest timestamp automatically. Date-range-end placeholders and upload
    # times are not treated as reliable export timestamps.
    all_precise = all(has_reliable_export_timestamp(r) for r in candidates)
    distinct_timestamps = len({str(r.get("snapshot_dt")) for r in candidates}) == len(candidates)
    if all_precise and distinct_timestamps:
        chosen = max(candidates, key=lambda r: pd.Timestamp(r["snapshot_dt"]))
        return chosen, candidates, False
    # Same reporting end date with missing/identical export time is genuinely ambiguous.
    # Require a human choice instead of relying on upload order or row count.
    default = sorted(candidates, key=lambda r: (str(r.get("filename", "")).casefold(), int(r.get("upload_order", 0))))[0]
    return default, candidates, True


@st.cache_data(show_spinner=False, max_entries=8)
def cached_analyze_selected(file_items: tuple[tuple[str, bytes], ...],
                           selected_orders_tuple: tuple[tuple[str, int], ...],
                           analysis_date_text: str):
    reports, errors = cached_parse_reports(file_items)
    selected_orders = {str(k): int(v) for k, v in selected_orders_tuple}
    try:
        from datetime import date as _date
        base_analysis = analyze_reports(reports, _date.fromisoformat(analysis_date_text),
                                        selected_report_orders=selected_orders)
        status_message = ""
    except Exception as exc:
        base_analysis = None
        status_message = ("Could not analyse the reports. Check the uploaded file formats and try again. "
                          f"Details: {type(exc).__name__}: {exc}")
    return reports, errors, base_analysis, status_message


def safe_filename(value: str) -> str:
    import re
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")
    return text[:70] or "selected"


def get_scoped_analysis(base_analysis: dict, scope_mode: str, selection: str = "") -> dict:
    """Memoize scoped analysis within this browser session for faster tab switching."""
    fingerprint = st.session_state.get("_upload_fingerprint", "no_upload")
    cache = st.session_state.setdefault("_scoped_analysis_cache", {})
    key = (fingerprint, scope_mode, selection)
    if key in cache:
        return cache[key]
    result = scope_analysis(base_analysis, scope_mode, selection)
    cache[key] = result
    # Bound session memory when executives move through many franchises.
    while len(cache) > 18:
        cache.pop(next(iter(cache)))
    return result


def render_outputs(view_analysis: dict, view_key: str, name_suffix: str = "") -> None:
    if view_analysis["current_unique_tickets"] <= 0:
        st.info("No active pending calls match this selection. Try another franchise or executive.")
        return

    # Images are expensive to draw and don't change when the user revisits the same
    # uploaded dataset + view + selection. Keep a small session cache so view switching
    # and repeated downloads don't regenerate both charts every time.
    upload_fingerprint = st.session_state.get("_upload_fingerprint", "no_upload")
    image_cache_key = (upload_fingerprint, view_key, name_suffix,
                       str(view_analysis.get("analysis_date", "")),
                       int(view_analysis.get("current_unique_tickets", 0)))
    image_cache = st.session_state.setdefault("_report_png_cache", {})
    try:
        if image_cache_key in image_cache:
            priority_png, management_png = image_cache[image_cache_key]
        else:
            priority_png = make_priority_png(view_analysis)
            management_png = make_management_png(view_analysis)
            image_cache[image_cache_key] = (priority_png, management_png)
            # Avoid unbounded growth if many executives/franchises are selected.
            while len(image_cache) > 12:
                image_cache.pop(next(iter(image_cache)))
    except Exception as exc:
        st.error(f"Could not generate this view. Details: {exc}")
        return
    st.markdown(
        '<div class="ready-line"><span class="ready-dot"></span> Your reports are ready to download</div>',
        unsafe_allow_html=True,
    )
    left, right = st.columns(2, gap="medium")
    with left:
        st.download_button(
            label="↓  Oldest Pending Calls Photo",
            data=priority_png,
            file_name=f"Oldest_Pending_Calls_{view_key}{name_suffix}_{view_analysis['analysis_date'].isoformat()}.png",
            mime="image/png",
            use_container_width=True,
            type="primary",
            key=f"download_priority_{view_key}_{name_suffix}",
        )
    with right:
        st.download_button(
            label="↓  Management Summary Photo",
            data=management_png,
            file_name=f"Management_Summary_{view_key}{name_suffix}_{view_analysis['analysis_date'].isoformat()}.png",
            mime="image/png",
            use_container_width=True,
            type="primary",
            key=f"download_management_{view_key}_{name_suffix}",
        )



# Detect report periods first, then automatically choose the most recent compatible report.
# A user is asked to confirm only when multiple latest candidates cannot be ordered by a
# reliable filename timestamp.
reports: list[dict] = []
errors: list[str] = []
base_analysis = None
status_message = "Upload at least one Pending Calls report above to populate these views. Supporting Soft Closure and Customer Satisfaction reports can be uploaded alongside it."
report_selection_orders: dict[str, int] = {}
selected_analysis_date = None
period_ambiguities: list[tuple[str, list[dict], int]] = []
file_items: tuple[tuple[str, bytes], ...] = tuple()
upload_fingerprint = "no_upload"

if uploaded:
    file_items = tuple((file.name, file.getvalue()) for file in uploaded)
    digest = hashlib.sha256()
    for filename, raw in file_items:
        digest.update(filename.encode("utf-8", errors="replace"))
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(hashlib.sha256(raw).digest())
    upload_fingerprint = digest.hexdigest()
    st.session_state["_upload_fingerprint"] = upload_fingerprint

    with st.spinner("Reading report dates and identifying the latest versions…"):
        reports, errors = cached_parse_reports(file_items)

    pending_auto, pending_candidates, pending_ambiguous = latest_candidates_for_type(reports, "pending_calls")
    if pending_auto is not None:
        selected_pending = pending_auto
        if pending_ambiguous:
            with st.container(border=True):
                st.warning("More than one Pending Calls file ends on the latest report date, and the export time is not reliable enough to order them. Choose the snapshot to use for the current backlog.")
                order_options = [int(r["upload_order"]) for r in pending_candidates]
                option_map = {int(r["upload_order"]): r for r in pending_candidates}
                selected_order = st.selectbox(
                    "Confirm current Pending Calls snapshot",
                    options=order_options,
                    index=order_options.index(int(pending_auto["upload_order"])),
                    format_func=lambda order: report_choice_label(option_map[order]),
                    key=f"period_pending_{upload_fingerprint[:10]}",
                )
                selected_pending = option_map[int(selected_order)]
                report_selection_orders["pending_calls"] = int(selected_order)
                period_ambiguities.append(("pending_calls", pending_candidates, int(selected_order)))
        else:
            report_selection_orders["pending_calls"] = int(selected_pending["upload_order"])

        selected_analysis_date = report_period_end_date(selected_pending)
        selected_snapshot_label = (
            pd.Timestamp(selected_pending["snapshot_dt"]).strftime("%d %b %Y, %H:%M:%S")
            if has_reliable_export_timestamp(selected_pending) else "export time not embedded"
        )
        st.caption(
            f"Reporting period: {selected_analysis_date:%d %b %Y} · "
            f"Pending Calls snapshot: {selected_snapshot_label}"
        )
        # Pick the latest available version of each supporting report as of the chosen
        # pending snapshot's report date. Keep all older files loaded for comparisons.
        type_labels = {
            "customer_satisfaction": "Customer Satisfaction",
            "calls_registered": "Calls Registered",
            "soft_closure": "Soft Closure / Productivity",
        }
        supporting_choices = {}
        for report_type, label in type_labels.items():
            auto_choice, candidates, ambiguous = latest_candidates_for_type(reports, report_type, selected_analysis_date)
            if auto_choice is None:
                continue
            chosen = auto_choice
            if ambiguous:
                supporting_choices[report_type] = (label, candidates, auto_choice)
            else:
                report_selection_orders[report_type] = int(chosen["upload_order"])

        if supporting_choices:
            with st.container(border=True):
                st.warning("Some supporting report versions share the same latest reporting date without a reliable export timestamp. Confirm which version should feed the summary.")
                for report_type, (label, candidates, default_choice) in supporting_choices.items():
                    order_options = [int(r["upload_order"]) for r in candidates]
                    option_map = {int(r["upload_order"]): r for r in candidates}
                    selected_order = st.selectbox(
                        f"Select {label} report",
                        options=order_options,
                        index=order_options.index(int(default_choice["upload_order"])),
                        format_func=lambda order, m=option_map: report_choice_label(m[order]),
                        key=f"period_{report_type}_{upload_fingerprint[:10]}",
                    )
                    report_selection_orders[report_type] = int(selected_order)
                    period_ambiguities.append((report_type, candidates, int(selected_order)))

        # Surface a compact manifest so users can see what was detected and which files
        # the tool is using. Historical Pending Call snapshots remain available for movement analysis.
        selected_order_values = set(report_selection_orders.values())
        manifest_rows = []
        for report in sorted(reports, key=lambda r: (r.get("report_type", ""), report_period_end_date(r), int(r.get("upload_order", 0)))):
            rtype = report.get("report_type", "unknown")
            if int(report.get("upload_order", -1)) in selected_order_values:
                role = ("Selected for productivity; all closure files remain available for reconciliation"
                        if rtype == "soft_closure" else "Selected for current summary")
            elif rtype == "pending_calls" and report_period_end_date(report) < selected_analysis_date:
                role = "Earlier snapshot retained for comparison"
            elif rtype == "pending_calls" and report_period_end_date(report) == selected_analysis_date:
                if (has_reliable_export_timestamp(report) and has_reliable_export_timestamp(selected_pending)
                        and pd.Timestamp(report["snapshot_dt"]) < pd.Timestamp(selected_pending["snapshot_dt"])):
                    role = "Earlier same-day snapshot retained for comparison"
                else:
                    role = "Same-date alternative; not treated as an earlier snapshot"
            elif report_period_end_date(report) > selected_analysis_date:
                role = "After selected report date; not used for current summary"
            else:
                role = "Available as historical/supporting data"
            start, end = report.get("period_start"), report.get("period_end")
            period = (f"{pd.Timestamp(start):%d %b %Y} – {pd.Timestamp(end):%d %b %Y}"
                      if start is not None and end is not None else f"{report_period_end_date(report):%d %b %Y} (inferred)")
            export_time = (pd.Timestamp(report["snapshot_dt"]).strftime("%d %b %Y %H:%M:%S")
                           if has_reliable_export_timestamp(report) else "Not embedded")
            manifest_rows.append({
                "Report type": type_labels.get(rtype, {"pending_calls": "Pending Calls"}.get(rtype, rtype.replace("_", " ").title())),
                "File": report.get("filename", ""), "Reporting period": period,
                "Export timestamp": export_time, "Rows": int(report.get("rows", 0)), "Selection": role,
            })
        expanded_manifest = bool(period_ambiguities)
        with st.expander("Detected reporting periods", expanded=expanded_manifest):
            st.caption(
                f"Current backlog date: {selected_analysis_date:%d %b %Y}. "
                "The latest eligible report is selected automatically when its date/order is clear. Older Pending Calls snapshots are retained for comparison."
            )
            if not has_reliable_export_timestamp(selected_pending):
                st.info(
                    "The selected Pending Calls file has no export timestamp in its filename. "
                    "The report end date is used, but its exact same-day export time cannot be verified."
                )
            st.dataframe(pd.DataFrame(manifest_rows), use_container_width=True, hide_index=True)

        # A confirmation is required only for ambiguous selections. On the first upload,
        # the default choice is shown but no report is generated until the user confirms.
        selection_key = (
            upload_fingerprint + "|" + selected_analysis_date.isoformat() + "|" +
            "|".join(f"{k}:{v}" for k, v in sorted(report_selection_orders.items()))
        )
        if period_ambiguities:
            if st.session_state.get("_confirmed_period_selection") != selection_key:
                if st.button("Confirm reporting periods and generate reports", type="primary", key="confirm_reporting_periods"):
                    st.session_state["_confirmed_period_selection"] = selection_key
                    st.rerun()
                st.info("Select the correct latest file(s) above, then confirm once. This confirmation appears only because the reporting versions are ambiguous.")
                status_message = "Confirm the selected reporting periods above to generate the reports."
            else:
                with st.spinner("Generating reports for the selected reporting period…"):
                    reports, errors, base_analysis, status_message = cached_analyze_selected(
                        file_items, tuple(sorted(report_selection_orders.items())), selected_analysis_date.isoformat()
                    )
        else:
            # Invalidate a prior confirmation if the uploaded file set changed or ambiguity cleared.
            with st.spinner("Generating reports from the latest detected reporting period…"):
                reports, errors, base_analysis, status_message = cached_analyze_selected(
                    file_items, tuple(sorted(report_selection_orders.items())), selected_analysis_date.isoformat()
                )

    elif reports:
        status_message = "Files were uploaded, but no Pending Calls report was detected. Please include a Pending Calls report; Soft Closure and Customer Satisfaction files are supporting reports."
    elif errors:
        status_message = "None of the uploaded files could be read. Check the file formats and try again."

    if errors:
        with st.expander("Some files could not be read"):
            for issue in errors:
                st.write(f"- {issue}")

view_choice = st.radio(
    "Report view",
    ["Branch-wise", "Franchise-wise", "Executive-wise"],
    horizontal=True,
    key="report_view_choice",
    label_visibility="collapsed",
)

if view_choice == "Branch-wise":
    st.caption("Branch-level view · Active pending calls ordered only by Days Old, oldest first.")
    if base_analysis is None:
        st.info(status_message)
    else:
        render_outputs(get_scoped_analysis(base_analysis, "branch"), "branch")

elif view_choice == "Franchise-wise":
    st.caption("Select one franchise to generate a shareable update for its franchise group.")
    if base_analysis is None:
        st.info(status_message)
    else:
        active_queue = base_analysis["priorities"]
        franchise_col = "Franchise Name" if "Franchise Name" in active_queue.columns else None
        franchise_options = sorted(
            {str(v).strip() for v in active_queue[franchise_col].tolist()
             if str(v).strip() and str(v).strip().lower() not in {"nan", "none"}},
            key=str.casefold,
        ) if franchise_col else []
        if not franchise_options:
            st.warning("No franchise names were found in the active pending-call report.")
        else:
            selected_franchise = st.selectbox(
                "Select franchise", franchise_options, key="selected_franchise", label_visibility="visible"
            )
            try:
                franchise_analysis = get_scoped_analysis(base_analysis, "franchise", selected_franchise)
                st.caption(
                    f"{franchise_analysis['current_unique_tickets']:,} active pending tickets · "
                    f"{franchise_analysis['scope_summary']['aged_over_2']:,} aged over 2 days · "
                    f"{franchise_analysis['scope_summary']['aged_7_plus']:,} aged 7+ days"
                )
                st.caption(
                    "Daily Service Ambassador productivity counts distinct resolved/closed tickets "
                    "with a valid closure timestamp on the selected report date; target is 6 calls per observed SA."
                )
                render_outputs(franchise_analysis, "franchise", safe_filename(selected_franchise))
            except Exception as exc:
                st.error(f"Could not generate the selected franchise view ({type(exc).__name__}). Try clearing/re-uploading the source reports, or check the app logs for details.")

else:
    st.caption("Select your name to generate a personal view across all franchises assigned to you.")
    if base_analysis is None:
        st.info(status_message)
    else:
        active_queue = base_analysis["priorities"]
        executive_col = "Service Executive Name" if "Service Executive Name" in active_queue.columns else None
        executive_options = sorted(
            {str(v).strip() for v in active_queue[executive_col].tolist()
             if str(v).strip() and str(v).strip().lower() not in {"nan", "none"}},
            key=str.casefold,
        ) if executive_col else []
        if not executive_options:
            st.warning("No service executive names were found in the active pending-call report.")
        else:
            selected_executive = st.selectbox(
                "Select service executive", executive_options, key="selected_executive", label_visibility="visible"
            )
            try:
                executive_analysis = get_scoped_analysis(base_analysis, "executive", selected_executive)
                exec_franchises = executive_analysis["franchise_summary"]
                franchise_count = int(exec_franchises["Franchise"].nunique()) if not exec_franchises.empty and "Franchise" in exec_franchises.columns else 0
                st.caption(
                    f"{executive_analysis['current_unique_tickets']:,} active pending tickets across "
                    f"{franchise_count:,} franchise(s) · "
                    f"{executive_analysis['scope_summary']['aged_7_plus']:,} aged 7+ days"
                )
                render_outputs(executive_analysis, "executive", safe_filename(selected_executive))
            except Exception as exc:
                st.error(f"Could not generate the selected executive view ({type(exc).__name__}). Try a different selection or check the app logs for details.")



# Persistent prototype disclaimer at the bottom of every view.
st.markdown(
    """
    <div style="margin-top:2.8rem; padding:1rem 1.2rem; border-top:1px solid #344657;
                color:#B3C0CC; font-family:Arial, sans-serif; font-size:12px;
                line-height:1.6; text-align:center;">
      <span style="color:#EDF2F7; font-weight:700; letter-spacing:.04em;">PROTOTYPE DISCLAIMER</span>
      <span style="padding:0 .45rem; color:#D6424E;">·</span>
      This Service Operations Intelligence tool is a prototype developed by Vishrut
      under the <strong style="color:#F3F6F9;">“To Branch With Love”</strong> project.
    </div>

    """,
    unsafe_allow_html=True,
)
