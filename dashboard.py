"""Local Streamlit review UI backed by persisted reports and row lineage."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from contracts import trace_finding
from warehouse import connect, trace


def load_views(db):
    """Read dashboard tables from DuckDB without changing financial figures."""
    status = db.execute("""select status, count(*) as figures
        from audit.reconciliation group by status order by figures desc""").df()
    daily = db.execute("""with figure_dates as (
        select fs.figure_id, min(coalesce(l.posted_on, b.posted_on)) as posted_on
        from audit.figure_sources fs
        left join staging.ledger l on fs.source_name = 'ledger'
          and l.source_file = fs.source_file and l.source_row = fs.source_row
          and l.source_hash = fs.source_hash
        left join staging.bank b on fs.source_name = 'bank'
          and b.source_file = fs.source_file and b.source_row = fs.source_row
          and b.source_hash = fs.source_hash
        where fs.figure_id like 'REC-%'
        group by fs.figure_id)
        select r.figure_id, r.reference, r.status, r.difference_cents,
               d.posted_on from audit.reconciliation r
        left join figure_dates d on r.figure_id = d.figure_id
        order by d.posted_on, r.figure_id""").df()
    findings = db.execute("""select figure_id, kind, explanation, amount_cents,
        ledger_ids from audit.findings order by kind, figure_id""").df()
    return status, daily, findings


def main() -> None:
    import pandas as pd
    import streamlit as st

    st.set_page_config(page_title="FinRecon", layout="wide")
    st.title("FinRecon: local finance controls")
    warehouse = Path("data/finrecon.duckdb")
    report = Path("reports/quality_report.json")
    if not warehouse.is_file() or not report.is_file():
        st.error("Run `make all` first to create the warehouse and reports.")
        st.stop()
    db = connect(warehouse)
    try:
        status, daily, findings = load_views(db)
        quality = json.loads(report.read_text(encoding="utf-8"))
        anomaly_path = Path("reports/anomaly_report.json")
        anomaly = (json.loads(anomaly_path.read_text(encoding="utf-8"))
                   if anomaly_path.is_file() else {"status": "unavailable", "findings": []})
        st.caption("Synthetic SGD data. Audit hits are review candidates, not proven fraud.")
        total_figures = int(status["figures"].sum())
        matched = int(status.loc[status["status"] == "matched", "figures"].sum())
        col1, col2, col3 = st.columns(3)
        col1.metric("Reconciliation figures", total_figures)
        col2.metric("Matched", matched)
        col3.metric("Quality checks failed", quality["failed"])

        st.subheader("Reconciliation status")
        st.bar_chart(status.set_index("status"))
        daily["age_days"] = (pd.Timestamp(date.today()) -
                             pd.to_datetime(daily["posted_on"])).dt.days.clip(lower=0)
        st.subheader("Breaks by date and age")
        breaks = daily[daily["status"] != "matched"]
        st.dataframe(breaks, width="stretch", hide_index=True)

        st.subheader("Delivery and quality controls")
        control_rows = [{"check": item["name"], "status": item["status"],
                         "evidence_count": len(item["evidence"])}
                        for item in quality["details"]]
        st.dataframe(control_rows, width="stretch", hide_index=True)
        st.subheader("Audit review findings")
        st.dataframe(findings, width="stretch", hide_index=True)
        st.subheader("IsolationForest review candidates")
        st.caption(f"Anomaly screen status: {anomaly['status']}. Scores are not fraud probabilities.")
        st.dataframe(anomaly["findings"], width="stretch", hide_index=True)

        st.subheader("Trace a figure to its source")
        figure_id = st.text_input("Figure ID", "REC-PAY-00001")
        if st.button("Trace"):
            try:
                selected = figure_id.strip()
                evidence = trace_finding(db, selected) if selected.startswith("DOC-") else trace(db, selected)
                st.json(json.loads(json.dumps(evidence, default=str)))
            except (KeyError, ValueError) as exc:
                st.error(str(exc))
        st.caption("Dataset grain, columns and exclusions: docs/DATA_DICTIONARY.md")
    finally:
        db.close()


if __name__ == "__main__":
    main()
