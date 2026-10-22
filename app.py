from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

import matplotlib
matplotlib.use("Agg")

from service_ops_engine import analyze_reports, parse_report, make_management_png, make_priority_png

APP_TITLE = "Service Operations Intelligence"

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="📊",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# Official logo artwork supplied by the user, embedded so deployment needs no extra asset files.
IFB_LOGO_DATA_URI = "data:image/png;base64,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"

st.markdown(
    """
    <style>
      :root {
        --navy: #253846;
        --ifb-red: #C93642;
        --ink: #25292D;
        --muted: #727980;
        --line: #E4E7EA;
        --paper: #FFFFFF;
        --page: #F5F6F7;
        --brand-serif: "Rockwell Extra Bold", Rockwell, "Roboto Slab", Georgia, serif;
        --body-sans: Arial, "Helvetica Neue", Helvetica, sans-serif;
      }
      html, body, [data-testid="stAppViewContainer"] {
        font-family: var(--body-sans);
        color: var(--ink);
      }
      #MainMenu, footer, header { visibility: hidden; }
      [data-testid="stAppViewContainer"] {
        background: linear-gradient(160deg, #FAFAFA 0%, #F4F6F7 60%, #F9F9F8 100%);
      }
      [data-testid="stMain"] .block-container {
        max-width: 850px;
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
        border: 1px solid rgba(222, 226, 229, .95);
        border-radius: 19px;
        background: linear-gradient(112deg, #FFFFFF 0%, #FFFFFF 48%, #F7F8F8 100%);
        box-shadow: 0 12px 32px rgba(31, 43, 50, .055);
      }
      .brand-logo {
        position: relative;
        z-index: 2;
        display: block;
        flex: 0 0 auto;
        width: 138px;
        height: auto;
        object-fit: contain;
        border: 0;
        background: #FFFFFF;
      }
      .hero-copy { position: relative; z-index: 2; min-width: 0; }
      .eyebrow {
        margin: 0 0 7px 0;
        color: var(--ifb-red);
        font-family: var(--body-sans);
        font-size: 10px;
        line-height: 1.3;
        font-weight: 800;
        letter-spacing: .16em;
      }
      .hero h1 {
        margin: 0;
        color: #303236;
        font-family: var(--brand-serif);
        font-size: clamp(23px, 3.5vw, 31px);
        font-weight: 800;
        line-height: 1.12;
        letter-spacing: -.025em;
      }
      .hero p {
        margin: 9px 0 0 0;
        color: #666D73;
        font-family: var(--body-sans);
        font-size: 13px;
        line-height: 1.45;
      }
      .upload-title {
        display: flex;
        align-items: center;
        gap: 11px;
        margin: 2px 0 4px 0;
        color: var(--ink);
        font-family: var(--body-sans);
        font-size: 16px;
        font-weight: 750;
      }
      .upload-subtitle {
        margin: 0 0 13px 0;
        color: var(--muted);
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
        color: #34383B;
        background: #ECEEEF;
        font-size: 18px;
      }
      div[data-testid="stFileUploader"] {
        padding: 8px 10px;
        border: 1px dashed #BFC6CB;
        border-radius: 14px;
        background: rgba(255,255,255,.9);
      }
      div[data-testid="stFileUploader"] section {
        border: 0;
        background: transparent;
      }
      /* Keep Streamlit's Browse files control visible in its normal state too. */
      div[data-testid="stFileUploader"] button,
      div[data-testid="stFileUploader"] section button,
      div[data-testid="stFileUploaderDropzone"] button,
      div[data-testid="stFileUploaderDropzone"] button[kind="secondary"],
      div[data-testid="stFileUploaderDropzone"] button[data-testid="stBaseButton-secondary"] {
        color: #253846 !important;
        -webkit-text-fill-color: #253846 !important;
        background: #F1F3F4 !important;
        border: 1px solid #C93642 !important;
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
        border-color: #AE2733 !important;
      }
      div[data-testid="stFileUploader"] button:focus,
      div[data-testid="stFileUploader"] button:focus-visible,
      div[data-testid="stFileUploaderDropzone"] button:focus,
      div[data-testid="stFileUploaderDropzone"] button:focus-visible {
        color: #253846 !important;
        -webkit-text-fill-color: #253846 !important;
        background: #F1F3F4 !important;
        outline: 2px solid rgba(201, 54, 66, .28) !important;
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
        border: 1px solid transparent;
        box-shadow: 0 6px 16px rgba(31, 43, 50, .09);
        transition: transform .12s ease, box-shadow .12s ease;
      }
      div[data-testid="stDownloadButton"] button:hover {
        transform: translateY(-1px);
        box-shadow: 0 9px 22px rgba(31, 43, 50, .13);
      }
      div[data-testid="stHorizontalBlock"] > div:first-child div[data-testid="stDownloadButton"] button {
        background: linear-gradient(135deg, #C93642, #AE2733);
        color: white;
      }
      div[data-testid="stHorizontalBlock"] > div:nth-child(2) div[data-testid="stDownloadButton"] button {
        background: linear-gradient(135deg, #45484B, #292D30);
        color: white;
      }
      .ready-line {
        display: flex;
        align-items: center;
        gap: 9px;
        margin: 20px 0 13px 0;
        padding: 11px 14px;
        border: 1px solid #E2E6E8;
        border-radius: 11px;
        background: rgba(255,255,255,.8);
        color: #4E585F;
        font-family: var(--body-sans);
        font-size: 12px;
        font-weight: 700;
      }
      .ready-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #4E9A70;
        box-shadow: 0 0 0 4px rgba(78,154,112,.12);
      }
      @media (max-width: 560px) {
        [data-testid="stMain"] .block-container { padding-left: 1rem; padding-right: 1rem; }
        .hero { padding: 20px 16px; gap: 13px; min-height: 126px; }
        .brand-logo { width: 105px; }
        .hero h1 { font-size: 22px; }
        .hero p { font-size: 12px; }
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
    <div class="upload-subtitle">Select one or more hourly Excel exports. Supporting call and customer feedback reports can be included too.</div>
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
    from service_ops_engine import _classify

    report_type = _classify(df)
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
if uploaded:
    reports, errors = parse_uploaded_files(uploaded)

    if errors:
        with st.expander("Some files could not be read"):
            for issue in errors:
                st.write(f"- {issue}")

    pending_reports = [r for r in reports if r["report_type"] == "pending_calls"]
    if reports and pending_reports:
        latest_report = max(
            pending_reports,
            key=lambda r: (r["snapshot_dt"], r["upload_order"]),
        )
        analysis_date = latest_report["snapshot_dt"].date()

        try:
            with st.spinner("Creating your reports…"):
                analysis = analyze_reports(reports, analysis_date)
                priority_png = make_priority_png(analysis)
                management_png = make_management_png(analysis)
        except Exception as exc:
            st.error(f"Could not generate the reports. Check the uploaded file formats and try again. Details: {exc}")
            st.stop()

        st.markdown(
            '<div class="ready-line"><span class="ready-dot"></span> Your reports are ready to download</div>',
            unsafe_allow_html=True,
        )
        left, right = st.columns(2, gap="medium")
        with left:
            st.download_button(
                label="↓  Priority Calls Photo",
                data=priority_png,
                file_name=f"Priority_Pending_Calls_{analysis_date.isoformat()}.png",
                mime="image/png",
                use_container_width=True,
                type="primary",
            )
        with right:
            st.download_button(
                label="↓  Management Summary Photo",
                data=management_png,
                file_name=f"Management_Summary_{analysis_date.isoformat()}.png",
                mime="image/png",
                use_container_width=True,
                type="primary",
            )
    elif reports:
        st.warning("Please include at least one Pending Calls report. Supporting reports can be uploaded with it.")
    elif not errors:
        st.info("No supported report format was detected. Please check the uploaded files.")
