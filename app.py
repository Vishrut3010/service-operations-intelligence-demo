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

# Clean, restrained UI: one hero, one uploader, and exactly two output buttons.
st.markdown(
    """
    <style>
      :root {
        --navy: #15344D;
        --navy-2: #244C68;
        --ifb-red: #C93642;
        --ink: #1C2D3B;
        --muted: #718092;
        --line: #E3EAF0;
        --paper: #FFFFFF;
        --page: #F4F7FA;
      }
      #MainMenu, footer, header { visibility: hidden; }
      [data-testid="stAppViewContainer"] {
        background: linear-gradient(160deg, #F8FAFC 0%, #F2F6F9 62%, #F7F8FA 100%);
      }
      [data-testid="stMain"] .block-container {
        max-width: 850px;
        padding-top: 2.0rem;
        padding-bottom: 2.6rem;
      }
      .hero {
        position: relative;
        overflow: hidden;
        display: flex;
        align-items: center;
        gap: 18px;
        min-height: 165px;
        padding: 28px 30px;
        margin-bottom: 23px;
        border: 1px solid rgba(218, 228, 237, .95);
        border-radius: 22px;
        background: linear-gradient(115deg, #FFFFFF 0%, #FAFCFE 66%, #F2F6FA 100%);
        box-shadow: 0 12px 34px rgba(22, 52, 77, .055);
      }
      .hero::after {
        content: "IFB";
        position: absolute;
        right: 21px;
        top: 4px;
        font-family: Arial, Helvetica, sans-serif;
        font-weight: 900;
        font-style: italic;
        letter-spacing: -0.12em;
        line-height: 1;
        font-size: clamp(110px, 19vw, 190px);
        color: rgba(21, 52, 77, .045);
        pointer-events: none;
        user-select: none;
      }
      .brand-mark {
        z-index: 1;
        flex: 0 0 70px;
        width: 70px;
        height: 70px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 18px;
        color: #FFFFFF;
        background: linear-gradient(145deg, #D84852, #B92332);
        font-family: Arial, Helvetica, sans-serif;
        font-size: 26px;
        font-weight: 900;
        font-style: italic;
        letter-spacing: -2px;
        box-shadow: 0 8px 18px rgba(201, 54, 66, .18);
      }
      .hero-copy { position: relative; z-index: 1; }
      .eyebrow {
        margin: 0 0 7px 0;
        color: var(--ifb-red);
        font-size: 10px;
        line-height: 1.3;
        font-weight: 800;
        letter-spacing: .17em;
      }
      .hero h1 {
        margin: 0;
        color: var(--navy);
        font-size: clamp(24px, 4vw, 32px);
        font-weight: 760;
        line-height: 1.12;
        letter-spacing: -.035em;
      }
      .hero p {
        margin: 9px 0 0 0;
        color: #647488;
        font-size: 14px;
        line-height: 1.45;
      }
      .privacy-note {
        margin: 0 0 12px 1px;
        color: #788696;
        font-size: 11px;
        line-height: 1.45;
      }
      .upload-title {
        display: flex;
        align-items: center;
        gap: 11px;
        margin: 2px 0 4px 0;
        color: var(--ink);
        font-size: 17px;
        font-weight: 750;
      }
      .upload-subtitle {
        margin: 0 0 13px 0;
        color: var(--muted);
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
        color: var(--navy);
        background: #EAF0F5;
        font-size: 17px;
      }
      div[data-testid="stFileUploader"] {
        padding: 8px 10px;
        border: 1px dashed #BAC8D5;
        border-radius: 15px;
        background: rgba(255,255,255,.88);
      }
      div[data-testid="stFileUploader"] section {
        border: 0;
        background: transparent;
      }
      div[data-testid="stCheckbox"] label {
        color: #667789;
        font-size: 12px;
      }
      div[data-testid="stDownloadButton"] button {
        width: 100%;
        min-height: 54px;
        white-space: normal;
        border-radius: 13px;
        font-size: 14px;
        font-weight: 750;
        border: 1px solid transparent;
        box-shadow: 0 6px 16px rgba(21, 52, 77, .09);
        transition: transform .12s ease, box-shadow .12s ease;
      }
      div[data-testid="stDownloadButton"] button:hover {
        transform: translateY(-1px);
        box-shadow: 0 9px 22px rgba(21, 52, 77, .13);
      }
      div[data-testid="stHorizontalBlock"] > div:first-child div[data-testid="stDownloadButton"] button {
        background: linear-gradient(135deg, #D3444E, #BB2D3A);
        color: white;
      }
      div[data-testid="stHorizontalBlock"] > div:nth-child(2) div[data-testid="stDownloadButton"] button {
        background: linear-gradient(135deg, #1F4C69, #15344D);
        color: white;
      }
      .ready-line {
        display: flex;
        align-items: center;
        gap: 9px;
        margin: 23px 0 14px 0;
        padding: 12px 15px;
        border: 1px solid #D8EAE0;
        border-radius: 12px;
        background: #F3FAF6;
        color: #286445;
        font-size: 13px;
        font-weight: 700;
      }
      .ready-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #36A36D;
        box-shadow: 0 0 0 4px rgba(54,163,109,.12);
      }
      .footer-note {
        margin-top: 22px;
        color: #97A2AE;
        text-align: center;
        font-size: 10px;
        line-height: 1.5;
      }
      @media (max-width: 560px) {
        [data-testid="stMain"] .block-container { padding-left: 1rem; padding-right: 1rem; }
        .hero { padding: 22px 18px; gap: 13px; min-height: 145px; }
        .brand-mark { flex-basis: 54px; width: 54px; height: 54px; border-radius: 15px; font-size: 22px; }
        .hero p { font-size: 12px; }
      }
    </style>
    <div class="hero">
      <div class="brand-mark">IFB</div>
      <div class="hero-copy">
        <div class="eyebrow">BRANCH SERVICE OPERATIONS</div>
        <h1>Service Operations Intelligence</h1>
        <p>One upload. Two clear reports.</p>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="privacy-note">Public demonstration only — upload synthetic or anonymised reports, not confidential IFB/customer data.</div>',
    unsafe_allow_html=True,
)
confirmed = st.checkbox("I confirm my files are synthetic or anonymised.", value=False)

st.markdown(
    """
    <div class="upload-title"><span class="upload-symbol">↑</span> Upload your reports</div>
    <div class="upload-subtitle">Select one or more Excel exports. Include hourly Pending Calls snapshots and, when available, Calls Registered and Customer Satisfaction reports.</div>
    """,
    unsafe_allow_html=True,
)

uploaded = st.file_uploader(
    "Choose Excel or CSV files",
    type=["xlsx", "xls", "csv"],
    accept_multiple_files=True,
    disabled=not confirmed,
    label_visibility="collapsed",
    help="Multiple reports can be selected together.",
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


if uploaded and confirmed:
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
elif not confirmed:
    st.markdown(
        '<div class="footer-note">Excel and CSV supported&nbsp; · &nbsp;Multiple files at once&nbsp; · &nbsp;No paid AI API required</div>',
        unsafe_allow_html=True,
    )
