"""Local, read-only analyst view over the published batch's BI snapshot.

This is the local review screen; ``tableau_export.py`` carries the same verified
snapshot to Tableau (Power BI Desktop does not run on macOS). It reads only the batch named by ``published.json``: verified
Parquet snapshot tables for the screens and a read-only DuckDB connection for
source-row drill-through. It never writes to the warehouse, never corrects a
payment and treats every finding as a review candidate, not an accusation.
Source links and source rows are shown only to the ``reviewer`` role
(see ``access.py``); the ``summary`` role never loads them.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from access import SOURCE_ROWS, SOURCE_TABLES, Access, require, resolve_access
from batches import (BUSINESS_TIMEZONE, DEFAULT_ROOT, list_attempts, read_pointer,
                     resolve_published, sha256_file)
from contracts import trace_finding
from reporting import SNAPSHOT_MANIFEST, SnapshotError, TABLES, verify_snapshot
from warehouse import trace


DEFAULT_STALE_HOURS = 24.0
AGE_BUCKETS = ((0, 2, "0-2 days"), (3, 7, "3-7 days"), (8, 14, "8-14 days"),
               (15, 30, "15-30 days"), (31, None, "31+ days"))


def format_sgd(cents: int) -> str:
    """Format integer cents exactly; presentation layer only."""
    cents = int(cents)
    sign = "-" if cents < 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}SGD {dollars:,}.{remainder:02d}"


def age_bucket(days: int) -> str:
    for low, high, label in AGE_BUCKETS:
        if days >= low and (high is None or days <= high):
            return label
    raise ValueError(f"negative age: {days}")


def ages(posted: list[date], report_date: date) -> list[int]:
    """Calendar-day age of each business date relative to the report date."""
    result = [(report_date - day).days for day in posted]
    if any(value < 0 for value in result):
        raise ValueError("report date precedes a source business date")
    return result


def load_state(root: Path, *, now: datetime | None = None,
               stale_hours: float = DEFAULT_STALE_HOURS) -> dict:
    """Decide what the view may show. Any integrity error blocks all figures."""
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    state = {"ok": False, "errors": [], "warnings": [], "infos": [], "pointer": None,
             "paths": None, "refresh_status": "no published batch", "attempts": []}
    attempts = list_attempts(root)
    state["attempts"] = attempts
    pointer = read_pointer(root)
    failed = [item for item in attempts if item.get("status") == "failed"]
    if pointer is None:
        state["errors"].append(
            "No published batch is available. Run `make all` to build and publish one.")
        if failed:
            state["errors"].append(f"Latest failed attempt {failed[-1]['batch_id']}: "
                                   f"{failed[-1].get('error')}")
        return state
    state["pointer"] = pointer
    try:
        paths = resolve_published(root)
    except (FileNotFoundError, ValueError) as exc:
        state["errors"].append(f"Published batch cannot be opened: {exc}")
        return state
    state["paths"] = paths
    for name, digest in pointer.get("reports", {}).items():
        path = paths.reports / name
        if not path.is_file():
            state["errors"].append(f"Missing published report: {name}.")
            continue
        if sha256_file(path) != digest:
            state["errors"].append(f"Report changed after publication: {name}.")
            continue
        batch = json.loads(path.read_text(encoding="utf-8")).get("batch", {})
        if batch.get("batch_id") != paths.batch_id or batch.get("status") != "complete":
            state["errors"].append(f"Report {name} is not from complete batch {paths.batch_id}.")
    if not paths.warehouse.is_file():
        state["errors"].append("Published warehouse file is missing.")
    manifest = paths.snapshot / SNAPSHOT_MANIFEST
    if not manifest.is_file() or sha256_file(manifest) != pointer.get("snapshot_manifest_sha256"):
        state["errors"].append("BI snapshot manifest is missing or changed after publication.")
    else:
        try:
            verify_snapshot(paths.snapshot, paths.batch_id)
        except SnapshotError as exc:
            state["errors"].append(f"BI snapshot rejected: {exc}")
    if state["errors"]:
        state["refresh_status"] = "published batch failed integrity checks"
        return state

    completed = datetime.fromisoformat(pointer["completed_at"])
    age_hours = (now - completed).total_seconds() / 3600
    state["age_hours"] = age_hours
    state["refresh_status"] = "published"
    if age_hours > stale_hours:
        state["warnings"].append(
            f"Stale batch: completed {age_hours:.1f} hours ago, beyond the "
            f"{stale_hours:g}-hour freshness limit. Figures may be out of date.")
    newer = [item for item in attempts if item["batch_id"] > paths.batch_id]
    if newer:
        last = newer[-1]
        if last.get("status") == "failed":
            state["refresh_status"] = "latest refresh failed"
            state["warnings"].append(
                f"Latest refresh {last['batch_id']} failed: {last.get('error')}. "
                f"Showing the previous complete batch {paths.batch_id}; no figures "
                "from the failed attempt are displayed.")
        elif last.get("status") == "running":
            state["refresh_status"] = "refresh running or interrupted"
            state["infos"].append(
                f"Refresh {last['batch_id']} started at {last.get('started_at')} and has "
                f"not published. Showing complete batch {paths.batch_id}.")
        else:
            state["refresh_status"] = "newer batch not published"
            state["warnings"].append(
                f"Batch {last['batch_id']} has status {last.get('status')} but was not "
                f"published. Showing complete batch {paths.batch_id}.")
    anomaly = paths.reports / "anomaly_report.json"
    status = json.loads(anomaly.read_text(encoding="utf-8")).get("status") \
        if anomaly.is_file() else "missing"
    if status != "ok":
        state["infos"].append(f"Optional IsolationForest screen status: {status}; "
                              "anomaly candidates are absent from this batch.")
    state["ok"] = True
    return state


def load_tables(snapshot: Path, access: Access) -> dict:
    """Read verified Parquet snapshot tables the role may see (needs pandas)."""
    allowed = [table for table in TABLES
               if access.can(SOURCE_ROWS) or table not in SOURCE_TABLES]
    with duckdb.connect() as db:
        return {table: db.execute("select * from read_parquet(?)",
                                  [(Path(snapshot) / f"{table}.parquet").as_posix()]).df()
                for table in allowed}


def fixture_labels(data_dir: Path) -> dict[str, str]:
    """Development-only labels for the synthetic fixture; never used for scoring."""
    labels = {}
    audit_path = Path(data_dir) / "audit_ground_truth.json"
    if audit_path.is_file():
        for item in json.loads(audit_path.read_text(encoding="utf-8"))["findings"]:
            labels[f"AUD-{item['kind']}-{'-'.join(sorted(item['ids']))}"] = "planted"
    anomaly_path = Path(data_dir) / "anomaly_ground_truth.json"
    if anomaly_path.is_file():
        for ledger_id in json.loads(anomaly_path.read_text(encoding="utf-8"))["injected_ids"]:
            labels[f"ANOM-{ledger_id}"] = "planted"
    return labels


def money_or_dash(value) -> str:
    """Format cents, or an em dash where a side is absent (for example missing_in_bank)."""
    try:
        return format_sgd(int(value))
    except (TypeError, ValueError):
        return "—"


def selected_summary(figure_id: str, rec, findings) -> str:
    """One text line describing the selected figure from the snapshot facts."""
    match = rec[rec["figure_id"] == figure_id]
    if len(match):
        row = match.iloc[0]
        return (f"**Selected** `{figure_id}` · status {row['status']} · posted "
                f"{row['posted_on']} · reference `{row['reference']}` · ledger "
                f"{money_or_dash(row['ledger_cents'])} · bank {money_or_dash(row['bank_cents'])}"
                f" · difference {money_or_dash(row['difference_cents'])}")
    row = findings[findings["figure_id"] == figure_id].iloc[0]
    return (f"**Selected** `{figure_id}` · {row['finding_type']} · posted {row['posted_on']}"
            f" · reviewed amount {money_or_dash(row['amount_cents'])}")


# Streamlit draws st.table headers in a faded grey (3.7:1 measured); this keeps
# them at body-text contrast. The selector is scoped to the table by Styler.
TABLE_HEADER_STYLE = [{"selector": "th, th *", "props": [("color", "#31333F")]}]


def plain_table(frame):
    """A static HTML table whose first column is the row header.

    Streamlit ignores ``Styler.hide(axis="index")`` and would add a 0..n index
    column under an empty header cell; using the first column as the index
    avoids both and gives screen readers a meaningful row header.
    """
    import pandas as pd
    frame = pd.DataFrame(frame).reset_index(drop=True)
    return frame.set_index(frame.columns[0]).style.set_table_styles(TABLE_HEADER_STYLE)


def label_for(figure_id: str, labels: dict[str, str]) -> str:
    if figure_id.startswith(("AUD-", "ANOM-")):
        return labels.get(figure_id, "not planted")
    return "not labelled here"


def evidence_for(figure_id: str, warehouse: Path, access: Access):
    """Verify source rows through the existing lineage checks, read-only."""
    require(access, SOURCE_ROWS)
    db = duckdb.connect(str(warehouse), read_only=True)
    try:
        return trace_finding(db, figure_id) if figure_id.startswith("DOC-") else trace(db, figure_id)
    finally:
        db.close()


def main() -> None:
    import altair as alt
    import pandas as pd
    import streamlit as st

    st.set_page_config(page_title="FinRecon review", layout="wide")
    st.title("FinRecon: batch review")
    # Plain text rather than st.caption: the caption grey measured 3.7:1 contrast.
    st.markdown("Synthetic SGD data. Read-only: no write-back, no payment correction. "
                "Findings are review candidates, not proof of error or fraud.")
    try:
        headers = dict(st.context.headers)
    except Exception:  # Older Streamlit or a non-HTTP test session.
        headers = {}
    access = resolve_access(headers, os.environ)
    if not access.allowed:
        st.error(f"Access denied: {access.reason}. Nothing from this batch is shown.")
        st.stop()
    root = Path(os.environ.get("FINRECON_BATCH_ROOT", DEFAULT_ROOT))
    stale_hours = float(os.environ.get("FINRECON_STALE_HOURS", DEFAULT_STALE_HOURS))
    state = load_state(root, stale_hours=stale_hours)
    for message in state["errors"]:
        st.error(message)
    if not state["ok"]:
        st.stop()
    pointer, paths = state["pointer"], state["paths"]
    zone = ZoneInfo(BUSINESS_TIMEZONE)
    completed = datetime.fromisoformat(pointer["completed_at"])
    as_of = date.fromisoformat(pointer["as_of_date"])
    st.markdown(
        f"**Published batch** `{pointer['batch_id']}` · completed "
        f"{completed.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC "
        f"({completed.astimezone(zone):%Y-%m-%d %H:%M} SGT) · **data as of** {as_of} "
        f"({BUSINESS_TIMEZONE} business date; {pointer.get('as_of_source')}) · "
        f"**refresh status:** {state['refresh_status']} · **role:** {access.role}"
        + (f" ({access.user})" if access.user else ""))
    for message in state["warnings"]:
        st.warning(message)
    for message in state["infos"]:
        st.info(message)

    tables = load_tables(paths.snapshot, access)
    rec, findings = tables["fct_reconciliation"], tables["fct_finding"]
    quality = tables["fct_quality"]
    reviewer = access.can(SOURCE_ROWS)
    rec["posted_on"] = pd.to_datetime(rec["posted_on"]).dt.date
    findings["posted_on"] = pd.to_datetime(findings["posted_on"]).dt.date

    with st.sidebar:
        st.header("Filters")
        report_date = st.date_input(
            "Report date for ageing", value=as_of, min_value=as_of,
            help="Age is counted in calendar days from each figure's earliest source "
                 "business date to this date. Defaults to the batch as-of date.")
        all_dates = list(rec["posted_on"]) + list(findings["posted_on"])
        start_default, end_default = min(all_dates), max(all_dates)
        window = st.date_input("Source business date range",
                               value=(start_default, end_default),
                               min_value=start_default, max_value=end_default)
        start, end = (window if isinstance(window, tuple) and len(window) == 2
                      else (start_default, end_default))
        status_options = list(tables["dim_status"].sort_values("status_key")["status"])
        break_statuses = list(tables["dim_status"].query("is_break")["status"])
        type_options = list(tables["dim_finding_type"].sort_values(
            "finding_type_key")["finding_type"])
        summary_slot = st.empty()
        # Every selected option is a removable chip with its own Tab stop (16 by
        # default). Collapsed, the expander is one stop; the line above it says
        # what is selected, so the hidden state is never invisible.
        with st.expander("Status and finding-type filters"):
            statuses = st.multiselect("Reconciliation status", status_options,
                                      default=break_statuses)
            types = st.multiselect("Finding type", type_options, default=type_options)
        summary_slot.markdown(
            f"Showing {len(statuses)} of {len(status_options)} statuses "
            f"({'break statuses' if set(statuses) == set(break_statuses) else 'custom'}) "
            f"and {len(types)} of {len(type_options)} finding types.")
        plain_tables = st.checkbox(
            "Plain tables (every row as text, for screen readers)",
            help="Interactive grids are drawn on a canvas and expose only the visible "
                 "rows to assistive technology. Plain tables list every row as HTML "
                 "text but cannot be sorted, searched or downloaded.")
        show_labels = reviewer and st.checkbox(
            "Show synthetic fixture labels (development only)")

    rec["age_days"] = ages(list(rec["posted_on"]), report_date)
    findings["age_days"] = ages(list(findings["posted_on"]), report_date)
    in_window = rec[(rec["posted_on"] >= start) & (rec["posted_on"] <= end)]
    selected = in_window[in_window["status"].isin(statuses)]
    breaks = selected[selected["is_break"]].copy()
    queue = findings[(findings["posted_on"] >= start) & (findings["posted_on"] <= end)
                     & findings["finding_type"].isin(types)].copy()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Figures in batch", len(rec))
    col2.metric("Matched", int((rec["status"] == "matched").sum()))
    col3.metric("Breaks shown", len(breaks))
    col4.metric("Quality checks failed", int((quality["status"] == "fail").sum()))
    col5.metric("Findings shown", len(queue))

    def show_table(frame) -> None:
        frame = pd.DataFrame(frame).reset_index(drop=True)
        if plain_tables:
            st.table(plain_table(frame))
        else:
            st.dataframe(frame, width="stretch", hide_index=True)

    # Tabs keep hidden sections out of the keyboard order: each table toolbar and
    # grid otherwise adds several Tab stops before the drill-through controls.
    tab_breaks, tab_quality, tab_findings, tab_drill = st.tabs(
        ["Breaks", "Quality controls", "Finding queue", "Drill-through"])
    with tab_breaks:
        st.header("Reconciliation status (date filter applied)")
        counts = in_window.groupby("status").size().rename("figures").reset_index()
        # Altair rather than st.bar_chart: Vega's "..." actions menu is an unnamed
        # <summary> and an extra Tab stop; the description becomes the chart's label.
        st.altair_chart(alt.Chart(counts).mark_bar().encode(
            x=alt.X("status:N", title="status"), y=alt.Y("figures:Q", title="figures"),
            tooltip=["status", "figures"]).properties(
            description="Bar chart of figures by reconciliation status: " + ", ".join(
                f"{row.status} {row.figures}" for row in counts.itertuples()),
            usermeta={"embedOptions": {"actions": False}}), use_container_width=True)
        st.header("Aged-break overview")
        breaks["age_bucket"] = [age_bucket(days) for days in breaks["age_days"]]
        overview = []
        for _, _, label in AGE_BUCKETS:
            group = breaks[breaks["age_bucket"] == label]
            overview.append({"age bucket": label, "breaks": len(group),
                             "absolute difference": format_sgd(
                                 group["difference_cents"].abs().sum())})
        # Five rows: a static HTML table exposes every cell and adds no Tab stops.
        st.table(plain_table(overview))
        show_table(pd.DataFrame({
            "figure_id": breaks["figure_id"], "reference": breaks["reference"],
            "status": breaks["status"], "posted_on": breaks["posted_on"],
            "age_days": breaks["age_days"],
            "difference (ledger - bank)": [format_sgd(value)
                                           for value in breaks["difference_cents"]],
            "explanation": breaks["explanation"]}).sort_values(
                ["age_days", "figure_id"], ascending=[False, True]))

    with tab_quality:
        st.header("Delivery and quality controls")
        failed = quality[quality["status"] == "fail"]
        if len(failed):
            st.error(f"{len(failed)} control(s) failed in this batch.")
        else:
            st.success(f"All {len(quality)} controls passed when this batch was built.")
        show_table(quality.sort_values(["status", "check_name"])[
            ["check_name", "check_group", "status", "evidence_count", "description"]])

    labels = fixture_labels(paths.data_dir) if show_labels else {}
    with tab_findings:
        st.header("Finding queue")
        st.markdown("Rule, model and contract findings for human review. A finding is not a "
                   "fraud accusation; IsolationForest scores are not probabilities.")
        view = pd.DataFrame({
            "figure_id": queue["figure_id"], "finding_type": queue["finding_type"],
            "source": queue["finding_source"], "posted_on": queue["posted_on"],
            "age_days": queue["age_days"],
            "reviewed amount": [format_sgd(value) for value in queue["amount_cents"]],
            "explanation": queue["explanation"]})
        if show_labels:
            view["fixture label"] = [label_for(figure, labels) for figure in view["figure_id"]]
        show_table(view.sort_values(["age_days", "figure_id"], ascending=[False, True]))

    with tab_drill:
        st.header("Drill through to source evidence")
        options = list(breaks["figure_id"]) + list(queue["figure_id"])
        if not reviewer:
            st.info(f"Your role ({access.role}) does not include source files or source rows. "
                    "Ask for the reviewer role to drill through.")
        elif not options:
            st.info("No figures match the current filters.")
        else:
            figure_id = st.selectbox("Figure ID", options)
            st.markdown(selected_summary(figure_id, rec, findings))
            if show_labels:
                st.markdown(f"**Fixture label (development only):** "
                            f"{label_for(figure_id, labels)}")
            evidence = None
            if st.button("Verify source rows"):
                try:
                    evidence = evidence_for(figure_id, paths.warehouse, access)
                except (KeyError, ValueError) as exc:
                    st.error(f"Evidence rejected: {exc}")
                else:
                    st.success("Source hashes match the load manifest and current file bytes.")
            bridge = tables["bridge_figure_source"]
            links = bridge[bridge["figure_id"] == figure_id]
            show_table(pd.DataFrame({
                "source": links["source_name"],
                "file": [Path(value).name for value in links["source_file"]],
                "line": links["source_row"],
                "sha256": [value[:16] + "…" for value in links["source_hash"]]}))
            if evidence is not None:
                st.json(json.loads(json.dumps(evidence, default=str)))
    st.markdown("Roles are enforced by this app from a trusted proxy header or, locally, "
                "FINRECON_ROLE. This is a local review build: do not load real financial data.")


if __name__ == "__main__":
    main()
