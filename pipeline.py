"""Reproducible local ingestion, reconciliation, evidence and evaluation CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anomaly_detection import evaluate as evaluate_anomalies, screen as screen_anomalies
from audit_analytics import evaluate as evaluate_audit, screen
from contracts import evaluate as evaluate_contracts, run as run_contracts, trace_finding
from eval.copilot_eval import run as evaluate_copilot
from eval.independent_audit_cases import run as evaluate_audit_edges
from eval.independent_cases import run as evaluate_edges
from gen.contracts import generate_contracts
from gen.synthetic import generate
from quality_checks import run_checks
from reconcile_engine import evaluate, reconcile
from warehouse import connect, ingest, stage_counts, trace
from verify_dbt_models import verify as verify_dbt_sql


def run(data_dir: Path, warehouse_path: Path, report_dir: Path, seed: int = 17,
        count: int = 120) -> dict:
    generated = generate(data_dir, seed=seed, count=count)
    contract_count = generate_contracts(data_dir)
    db = connect(warehouse_path)
    try:
        ingested = ingest(db, data_dir)
        counts = stage_counts(db)
        if any(raw != staged or raw != ingested[name] for name, (raw, staged) in counts.items()):
            raise ValueError(f"ingestion/staging row-count mismatch: {counts}")
        quality = run_checks(db, data_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "quality_report.json").write_text(
            json.dumps(quality, indent=2) + "\n", encoding="utf-8")
        if quality["status"] != "pass":
            raise ValueError(f"data quality failed: {quality['failed']} checks; see quality_report.json")
        rows = reconcile(db)
        score = evaluate(data_dir / "ground_truth.json", rows)
        audit = screen(db)
        audit_score = evaluate_audit(data_dir / "audit_ground_truth.json",
                                     audit["findings"])
        anomaly = screen_anomalies(db)
        anomaly_score = evaluate_anomalies(data_dir / "anomaly_ground_truth.json",
                                           anomaly)
        contracts = run_contracts(db, data_dir)
        contract_score = evaluate_contracts(data_dir / "contract_ground_truth.json",
                                            contracts)
        copilot_score = (evaluate_copilot(db, data_dir, report_dir)
                         if count >= 46 else {"skipped": "25-question corpus requires at least 46 generated transactions", "cases": []})
        edge_score = evaluate_edges(report_dir)
        audit_edge_score = evaluate_audit_edges(report_dir)
        dbt_sql = verify_dbt_sql(db, Path(__file__).resolve().parent)
        evidence = [trace(db, row["figure_id"]) for row in rows[:10]]
        report = {"generated": generated, "ingested": ingested,
                  "stage_counts": {name: {"raw": raw, "staged": staged}
                                   for name, (raw, staged) in counts.items()},
                  "evaluation": score, "traced_figure_count": len(evidence),
                  "trace_source_rows_first_ten": sum(map(len, evidence)),
                  "audit_evaluation": audit_score,
                  "audit_finding_counts": audit["counts"],
                  "anomaly_evaluation": anomaly_score,
                  "contract_documents": contract_count,
                  "contract_evaluation": contract_score,
                  "copilot_evaluation": {key: value for key, value in
                                         copilot_score.items() if key != "cases"},
                  "edge_evaluation": {key: value for key, value in
                                      edge_score.items() if key not in ("by_status", "mismatches")},
                  "audit_edge_evaluation": audit_edge_score,
                  "dbt_sql_verification": dbt_sql,
                  "quality": {key: quality[key] for key in
                              ("status", "checks", "passed", "failed", "failures")}}
        (report_dir / "reconciliation_summary.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (report_dir / "reconciliation_rows.json").write_text(
            json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        (report_dir / "audit_report.json").write_text(
            json.dumps({**audit, "evaluation": audit_score}, indent=2) + "\n",
            encoding="utf-8")
        (report_dir / "anomaly_report.json").write_text(
            json.dumps({**anomaly, "evaluation": anomaly_score}, indent=2) + "\n",
            encoding="utf-8")
        (report_dir / "contract_report.json").write_text(
            json.dumps({**contracts, "evaluation": contract_score}, indent=2) + "\n",
            encoding="utf-8")
        (report_dir / "copilot_eval.json").write_text(
            json.dumps(copilot_score, indent=2) + "\n", encoding="utf-8")
        (report_dir / "dbt_sql_verification.json").write_text(
            json.dumps(dbt_sql, indent=2) + "\n", encoding="utf-8")
        return report
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["all", "trace", "quality"])
    parser.add_argument("--data-dir", type=Path, default=Path("data/generated"))
    parser.add_argument("--warehouse", type=Path, default=Path("data/finrecon.duckdb"))
    parser.add_argument("--reports", type=Path, default=Path("reports"))
    parser.add_argument("--figure-id", help="For trace, e.g. REC-PAY-00001")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--count", type=int, default=120)
    args = parser.parse_args()
    if args.command == "all":
        print(json.dumps(run(args.data_dir, args.warehouse, args.reports,
                             args.seed, args.count)["evaluation"], indent=2))
    elif args.command == "trace":
        if not args.figure_id:
            parser.error("--figure-id is required for trace")
        database = connect(args.warehouse)
        try:
            evidence = (trace_finding(database, args.figure_id)
                        if args.figure_id.startswith("DOC-")
                        else trace(database, args.figure_id))
            print(json.dumps(evidence, indent=2, default=str))
        finally:
            database.close()
    else:
        database = connect(args.warehouse)
        try:
            result = run_checks(database, args.data_dir)
            args.reports.mkdir(parents=True, exist_ok=True)
            (args.reports / "quality_report.json").write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({key: result[key] for key in
                              ("status", "checks", "passed", "failed", "failures")}, indent=2))
            if result["status"] != "pass":
                raise SystemExit(1)
        finally:
            database.close()
