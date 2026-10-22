from __future__ import annotations

import io
import textwrap
from datetime import date, datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from service_ops_engine import analyze_reports, parse_report, find_col, clean_text, get_ticket_col

APP_TITLE = "Service Operations Intelligence — Public Demo"
NAVY = "#12304A"
TEAL = "#148A8A"
BLUE = "#3478A8"
AMBER = "#E7A83E"
RED = "#C74646"
GREEN = "#2E8B62"
LIGHT = "#F2F5F8"
MID = "#5D6C7B"

st.set_page_config(page_title=APP_TITLE, page_icon="📊", layout="wide")
st.markdown(f"""
<style>
.block-container {{padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1500px;}}
.hero {{background: linear-gradient(105deg, {NAVY} 0%, #1E526B 100%); color: white; padding: 1.35rem 1.6rem; border-radius: 14px; margin-bottom: 1rem;}}
.hero h1 {{color: white; margin: 0 0 .25rem 0; font-size: 1.8rem;}}
.hero p {{color: #D7E6EF; margin: 0; font-size: .98rem;}}
.small-muted {{color: {MID}; font-size: .85rem;}}
div[data-testid="stMetric"] {{background: {LIGHT}; border: 1px solid #E2E8EF; padding: .8rem; border-radius: 12px;}}
</style>
<div class="hero"><h1>Service Operations Intelligence</h1><p>PUBLIC DEMO · Use synthetic or anonymized sample data only · Not an official IFB application.</p></div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("Public demo & privacy")
    st.warning(
        "This app runs on a hosted server. Files uploaded here are transmitted to that server for processing. "
        "Upload synthetic or properly anonymized demo files ONLY. Do not upload real customer, employee, ticket, "
        "phone, address, serial-number, or company-confidential records."
    )
    st.write("1. Upload sample report workbooks.\n2. Review detected report types and dates.\n3. Generate analyses.\n4. Download the priority image, management image, PDF and Excel workbook.")
    st.divider()
    st.caption("No paid AI API is used. The app does not intentionally save uploaded workbooks to persistent storage, but this is not an approved environment for confidential company data.")
    st.caption("Priority scores are prototype rules, not official service policy. Validate thresholds before operational use.")
    st.caption("Independent demonstration prototype; not an official IFB system.")

st.warning(
    "PUBLIC DEMO SAFETY NOTICE: Do not upload actual IFB operational exports or real customer/employee information. "
    "Use synthetic or properly anonymized test reports only. The files are processed on the hosted app server."
)
demo_data_confirmed = st.checkbox(
    "I confirm that every file I upload contains only synthetic or properly anonymized demonstration data.",
    value=False,
)
uploaded = st.file_uploader(
    "Upload demo reports (multiple files supported)",
    type=["xlsx", "xls", "csv"], accept_multiple_files=True,
    disabled=not demo_data_confirmed,
    help="Use synthetic/anonymized Pending Calls, Calls Registered, and Customer Satisfaction reports. Do not upload live operational data. Hourly Pending Call exports should ideally have timestamps in their filenames."
)
if not demo_data_confirmed:
    st.info("Confirm the data-safety notice above to enable uploads. This public website is for demonstration only.")


def read_csv_report(filename: str, data: bytes, order: int):
    # CSVs have no Excel metadata; schema detection is still supported.
    df = pd.read_csv(io.BytesIO(data), dtype=object)
    tmp = io.BytesIO()
    # Build a minimal synthetic Excel-like path is unnecessary; classify directly here.
    from service_ops_engine import _classify
    typ = _classify(df)
    if typ == "unknown":
        raise ValueError("CSV column headers did not match a supported report type.")
    ts = pd.Timestamp.now().floor("min")
    return {"filename": filename, "bytes": len(data), "report_type": typ, "df": df,
            "header_row_excel": 1, "metadata": "CSV report (date range not embedded)",
            "period_start": None, "period_end": None, "snapshot_dt": ts,
            "snapshot_source": "upload time fallback (CSV has no embedded export timestamp)",
            "upload_order": order, "rows": len(df)}


reports = []
errors = []
if uploaded:
    for idx, file in enumerate(uploaded):
        try:
            data = file.getvalue()
            if file.name.lower().endswith(".csv"):
                report = read_csv_report(file.name, data, idx)
            else:
                report = parse_report(file.name, data, idx)
            reports.append(report)
        except Exception as exc:
            errors.append((file.name, str(exc)))

if errors:
    st.warning("Some files could not be processed. Correct or remove them and try again.")
    st.dataframe(pd.DataFrame(errors, columns=["File", "Issue"]), use_container_width=True, hide_index=True)

if reports:
    summary_rows = []
    for r in sorted(reports, key=lambda x: (x["snapshot_dt"], x["upload_order"])):
        period = r["metadata"] or "Period not embedded"
        summary_rows.append({
            "File": r["filename"],
            "Detected report": {"pending_calls": "Pending Calls", "calls_registered": "Calls Registered", "customer_satisfaction": "Customer Satisfaction"}.get(r["report_type"], r["report_type"]),
            "Rows": r["rows"],
            "Snapshot / ordering date": r["snapshot_dt"].strftime("%d %b %Y %H:%M"),
            "Timestamp basis": r["snapshot_source"],
            "Period in report": period,
        })
    with st.expander("Review detected files", expanded=False):
        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    pending_reports = [r for r in reports if r["report_type"] == "pending_calls"]
    if pending_reports:
        latest_dt = max(r["snapshot_dt"] for r in pending_reports)
        chosen_date = st.date_input("Analysis / management report date", value=latest_dt.date(), help="The app uses the latest Pending Calls snapshot on or before this date. For a true end-of-day review, upload the final snapshot for that date.")
        try:
            analysis = analyze_reports(reports, chosen_date)
        except Exception as exc:
            st.error(f"Analysis could not be completed: {exc}")
            st.stop()

        cur = analysis["current_pending_report"]
        prev = analysis["previous_pending_report"]
        top20 = analysis["top20"]
        priorities = analysis["priorities"]
        metrics = analysis["csat_metrics"]

        st.subheader("Current snapshot at a glance")
        st.caption(f"Selected snapshot: **{cur['filename']}** · as of **{cur['snapshot_dt'].strftime('%d %b %Y, %I:%M %p')}** · {cur['snapshot_source']}")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Unique pending tickets", f"{analysis['current_unique_tickets']:,}", help="One record per distinct Ticket No after consolidating multiple part/detail rows.")
        c2.metric("Aged 7+ days", f"{analysis['older_7']:,}")
        c3.metric("Escalated tickets", f"{analysis['escalated']:,}")
        c4.metric("Repeat-call flags", f"{analysis['repeat_calls']:,}")
        c5.metric("Overdue schedule", f"{analysis['overdue_scheduled']:,}")

        if prev:
            mc = analysis["movement_counts"]
            st.markdown("### Movement versus the preceding Pending Call snapshot")
            st.caption(f"Previous snapshot: {prev['snapshot_dt'].strftime('%d %b %Y, %I:%M %p')} · A ticket absent from the newer list is not automatically a closure.")
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Newly present", f"{mc['new']:,}")
            m2.metric("Present in both", f"{mc['continuing']:,}")
            m3.metric("No longer listed", f"{mc['missing']:,}")
            m4.metric("Status changed", f"{mc['status_changed']:,}")
            m5.metric("Age increased", f"{mc['age_increased']:,}")

        st.markdown("### Top 20 priority calls")
        st.caption("Transparent scoring: age (0–35) + escalation (0–25) + repeat call (0–20) + pending reason (0–15) + overdue appointment (0–5). These are provisional weights for demonstration.")
        display_cols = [c for c in ["Priority Rank", "Ticket No", "Priority", "Priority Score", "Days Old", "Franchise Name", "Service Executive Name", "Technician Name", "Pending Reason", "Escalation Level", "Status", "Suggested Next Action"] if c in top20.columns]
        st.dataframe(top20[display_cols], use_container_width=True, hide_index=True, height=610)

        st.markdown("### Additional report signals")
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Pending backlog by age**")
            st.dataframe(analysis["age_summary"], use_container_width=True, hide_index=True)
            st.markdown("**Largest franchise backlogs**")
            st.dataframe(analysis["franchise_summary"], use_container_width=True, hide_index=True)
        with col_b:
            st.markdown("**Top pending reasons**")
            st.dataframe(analysis["reason_summary"], use_container_width=True, hide_index=True)
            st.markdown("**Customer satisfaction (latest available snapshot)**")
            if analysis["csat_report"] is not None:
                st.caption(f"Source: {analysis['csat_report']['filename']} · {analysis['csat_report']['metadata']}")
                st.metric("Happy feedback share", f"{metrics['happy_pct']:.2f}%" if metrics["happy_pct"] is not None else "—", help="Share of feedback records marked HAPPY. Not a service SLA measure.")
                st.dataframe(analysis["csat_summary"], use_container_width=True, hide_index=True)
                if analysis["csat_previous_report"] is not None and not analysis["csat_comparison"].empty:
                    cm = analysis["csat_movement"]
                    happy_delta = cm["happy_delta_pp"]
                    if happy_delta is not None:
                        st.caption(f"Snapshot comparison: {cm['previous_rows']:,} → {cm['current_rows']:,} feedback records; Happy share changed by {happy_delta:+.2f} percentage points.")
                    else:
                        st.caption(f"Snapshot comparison: {cm['previous_rows']:,} → {cm['current_rows']:,} feedback records.")
                    st.dataframe(analysis["csat_comparison"], use_container_width=True, hide_index=True)
            else:
                st.info("No Customer Satisfaction report was uploaded.")

        st.markdown("### Executive workload view")
        st.caption("This is an open-queue workload view, not a productivity ranking. Fair productivity comparisons require reliable closure/output and capacity data.")
        st.dataframe(analysis["executive_summary"], use_container_width=True, hide_index=True)

        st.markdown("### Generate final outputs")
        from service_ops_engine import make_priority_png, make_management_png, make_pdf, make_excel
        try:
            priority_png = make_priority_png(analysis)
            management_png = make_management_png(analysis)
            pdf_bytes = make_pdf(priority_png, management_png)
            excel_bytes = make_excel(analysis)
            st.success("Reports generated. Preview them below, then download the files.")
            out1, out2 = st.columns(2)
            with out1:
                st.markdown("**Image 1 — Priority Pending Calls**")
                st.image(priority_png, use_container_width=True)
                st.download_button("Download priority-call PNG", priority_png, file_name=f"Priority_Pending_Calls_{chosen_date}.png", mime="image/png", use_container_width=True)
            with out2:
                st.markdown("**Image 2 — Management Daily Summary**")
                st.image(management_png, use_container_width=True)
                st.download_button("Download management-summary PNG", management_png, file_name=f"Service_Management_Summary_{chosen_date}.png", mime="image/png", use_container_width=True)
            d1, d2, d3 = st.columns(3)
            d1.download_button("Download detailed Excel workbook", excel_bytes, file_name=f"Service_Operations_Analysis_{chosen_date}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
            d2.download_button("Download combined 2-page PDF", pdf_bytes, file_name=f"Service_Operations_Reports_{chosen_date}.pdf", mime="application/pdf", use_container_width=True)
            d3.download_button("Download snapshot comparison CSV", analysis["comparison"].to_csv(index=False).encode("utf-8-sig"), file_name=f"Pending_Snapshot_Comparison_{chosen_date}.csv", mime="text/csv", use_container_width=True)
        except Exception as exc:
            st.error(f"Output generation failed: {exc}")

        with st.expander("Data quality and interpretation notes"):
            st.dataframe(analysis["data_quality"], use_container_width=True, hide_index=True)
            st.markdown("- Multiple rows for the same Ticket No may carry different spare/additive/accessory details; those details are consolidated for ranking, not discarded.\n- Snapshot absence does not prove call closure.\n- End-of-day metrics are only as complete as the latest uploaded report.\n- The priority algorithm is a prototype, not official IFB policy. Confirm score weights and statuses with the service team before operational use.")
    else:
        st.info("Upload at least one Pending Call report to generate the Top 20 and the management report. Calls Registered and Customer Satisfaction files will be incorporated when available.")
else:
    st.info("After confirming the safety notice, upload one or more sample reports. The app recognises Pending Calls, Calls Registered, and Customer Satisfaction exports from their column headers.")
    st.markdown("**Supported file types:** `.xlsx`, `.xls`, or `.csv`. Analysis is performed in the hosted app session; do not use this public demo for confidential operational data.")
