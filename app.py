from __future__ import annotations

import io
from datetime import date

import pandas as pd
import streamlit as st

import matplotlib
matplotlib.use("Agg")

from service_ops_engine import analyze_reports, parse_report

APP_TITLE = "Service Operations Intelligence"

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="📊",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# Minimal visual design: one title, one uploader, two output buttons.
st.markdown(
    """
    <style>
      #MainMenu, footer, header {visibility: hidden;}
      .block-container {max-width: 900px; padding-top: 2.2rem; padding-bottom: 2.5rem;}
      .hero {padding: 0.25rem 0 1.1rem 0; border-bottom: 1px solid #E4EAF0; margin-bottom: 1.2rem;}
      .hero h1 {font-size: 2rem; line-height: 1.2; margin: 0 0 .4rem 0; color: #12304A;}
      .hero p {font-size: 1rem; color: #5D6C7B; margin: 0;}
      .privacy {font-size: .88rem; color: #596979;}
      div[data-testid="stDownloadButton"] button {width: 100%; min-height: 3.2rem; font-weight: 600;}
      div[data-testid="stFileUploader"] {border-radius: 10px;}
    </style>
    <div class="hero">
      <h1>Service Operations Intelligence</h1>
      <p>Upload your reports. Download two ready-to-share images.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    "<div class='privacy'>Public demo: upload only synthetic or properly anonymized files. "
    "Files are sent to the hosted server for processing. Do not upload confidential IFB records.</div>",
    unsafe_allow_html=True,
)

confirmed = st.checkbox("I will use synthetic or anonymized demonstration data only.")
uploaded = st.file_uploader(
    "Upload Excel reports",
    type=["xlsx", "xls", "csv"],
    accept_multiple_files=True,
    disabled=not confirmed,
    help="Upload one or more Pending Calls reports. Add Calls Registered and Customer Satisfaction reports for a richer management summary.",
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
        st.error("Some files could not be read. Check the file format and try again.")
        with st.expander("Show file issues"):
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
            with st.spinner("Preparing your two reports..."):
                analysis = analyze_reports(reports, analysis_date)
                from service_ops_engine import make_management_png, make_priority_png

                priority_png = make_priority_png(analysis)
                management_png = make_management_png(analysis)
        except Exception as exc:
            st.error(f"Could not generate the reports: {exc}")
            st.stop()

        latest_snapshot = analysis["current_pending_report"]["snapshot_dt"]
        st.success(
            f"Ready · {len(reports)} file(s) analysed · latest pending snapshot "
            f"{latest_snapshot.strftime('%d %b %Y, %I:%M %p')}"
        )

        st.markdown("### Download your reports")
        left, right = st.columns(2, gap="medium")
        with left:
            st.download_button(
                label="Download Priority Calls Photo",
                data=priority_png,
                file_name=f"Priority_Pending_Calls_{analysis_date.isoformat()}.png",
                mime="image/png",
                use_container_width=True,
                type="primary",
            )
        with right:
            st.download_button(
                label="Download Management Summary",
                data=management_png,
                file_name=f"Management_Summary_{analysis_date.isoformat()}.png",
                mime="image/png",
                use_container_width=True,
                type="primary",
            )

        st.caption(
            "The management summary reflects the latest uploaded snapshot and available supporting reports. "
            "A call missing from a later snapshot is not automatically treated as closed."
        )
    elif reports:
        st.warning("Upload at least one Pending Calls report. Supporting reports can be included alongside it.")
    elif not errors:
        st.info("No supported reports were detected. Upload Excel or CSV exports from the supported report types.")
elif confirmed:
    st.info("Choose your Excel reports above to generate the two images.")
else:
    st.caption("Tick the confirmation above to enable the file uploader.")
