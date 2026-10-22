"""Reproducible local ingestion, reconciliation, evidence and evaluation CLI.

``all`` builds a self-contained batch and publishes it atomically; ``trace``
and ``quality`` read the published batch unless explicit paths are passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from anomaly_detection import evaluate as evaluate_anomalies, screen as screen_anomalies
from audit_analytics import evaluate as evaluate_audit, screen
from batches import (BUSINESS_TIMEZONE, DEFAULT_ROOT, atomic_write_json, batch_paths, cli_paths,
                     new_batch_id, prune, publish, read_pointer, resolve_published,
                     sha256_file, write_manifest)
from contracts import evaluate as evaluate_contracts, run as run_contracts, trace_finding
from eval.copilot_eval import run as evaluate_copilot
from eval.independent_audit_cases import run as evaluate_audit_edges
from eval.independent_cases import run as evaluate_edges
from gen.contracts import generate_contracts
from gen.synthetic import generate
from quality_checks import run_checks
from reconcile_engine import evaluate, reconcile
from reporting import build_star, data_as_of_date, export_snapshot, verify_snapshot
from warehouse import connect, ingest, record_batch, stage_counts, trace
from verify_dbt_models import verify as verify_dbt_sql


def _stamp(value, batch: dict) -> dict:
    """Attach the batch block every report carries."""
    body = value if isinstance(value, dict) else {"rows": value}
    return {"batch": batch, **{key: item for key, item in body.items() if key != "batch"}}


def run(data_dir: Path, warehouse_path: Path, report_dir: Path, seed: int = 17,
        count: int = 120, batch_id: str | None = None) -> dict:
    """Generate, load, check, reconcile, screen and evaluate into the given paths.

    Without ``batch_id`` this writes unbatched reports (used by tests and
    ad-hoc runs). ``run_batch`` calls it with a batch directory and ID.
    """
    batch = ({"batch_id": batch_id, "status": "staged"} if batch_id
             else {"batch_id": None, "status": "unbatched"})

    def write(name: str, value) -> None:
        atomic_write_json(report_dir / name, _stamp(value, batch))

    generated = generate(data_dir, seed=seed, count=count)
    contract_count = generate_contracts(data_dir)
    db = connect(warehouse_path)
    try:
        if batch_id:
            record_batch(db, batch_id, "staged", started_at=datetime.now(timezone.utc),
                         business_timezone=BUSINESS_TIMEZONE)
        ingested = ingest(db, data_dir, batch_id)
        counts = stage_counts(db)
        if any(raw != staged or raw != ingested[name] for name, (raw, staged) in counts.items()):
            raise ValueError(f"ingestion/staging row-count mismatch: {counts}")
        quality = run_checks(db, data_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        write("quality_report.json", quality)
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
        write("reconciliation_summary.json", report)
        write("reconciliation_rows.json", rows)
        write("audit_report.json", {**audit, "evaluation": audit_score})
        write("anomaly_report.json", {**anomaly, "evaluation": anomaly_score})
        write("contract_report.json", {**contracts, "evaluation": contract_score})
        write("copilot_eval.json", copilot_score)
        write("dbt_sql_verification.json", dbt_sql)
        for name in ("independent_edge_eval.json", "independent_audit_eval.json"):
            path = report_dir / name
            write(name, json.loads(path.read_text(encoding="utf-8")))
        return report
    finally:
        db.close()


def _validate_batch(db, paths, ingested: dict) -> None:
    """Checks that must pass before a batch may become visible."""
    rows = db.execute("""select source_name, source_file, sha256, row_count, batch_id
        from audit.load_manifest""").fetchall()
    if {row[0] for row in rows} != set(ingested) or len(rows) != len(ingested):
        raise ValueError("load manifest does not list exactly the six feeds of this batch")
    for name, path, digest, count, batch_id in rows:
        if batch_id != paths.batch_id:
            raise ValueError(f"{name}: manifest row belongs to batch {batch_id}")
        if Path(path).parent != paths.data_dir.resolve():
            raise ValueError(f"{name}: source file is outside the batch directory")
        if sha256_file(Path(path)) != digest or count != ingested[name]:
            raise ValueError(f"{name}: source changed or row count differs before publish")
    stale = db.execute("""select count(*) from audit.figure_sources fs
        left join audit.load_manifest m on m.source_name = fs.source_name
          and m.source_file = fs.source_file
        where m.sha256 is null or m.sha256 != fs.source_hash""").fetchone()[0]
    if stale:
        raise ValueError(f"{stale} figure source links cite a stale file hash")
    unlinked = db.execute("""select count(*) from audit.reconciliation r
        where not exists (select 1 from audit.figure_sources fs
                          where fs.figure_id = r.figure_id)""").fetchone()[0]
    if unlinked:
        raise ValueError(f"{unlinked} reconciliation figures have no source evidence")


def run_batch(batch_root: Path = DEFAULT_ROOT, seed: int = 17, count: int = 120,
              as_of_date: date | None = None, keep: int = 3) -> dict:
    """Build one batch in isolation, validate it, then publish one pointer.

    Any exception before ``publish`` marks the batch failed and leaves the
    previously published batch, with its own CSVs, warehouse and reports,
    as the only visible one.
    """
    root = Path(batch_root)
    root.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    paths = batch_paths(root, new_batch_id(started))
    paths.directory.mkdir()
    write_manifest(paths, status="running", started_at=started.isoformat(),
                   seed=seed, count=count, business_timezone=BUSINESS_TIMEZONE)
    try:
        report = run(paths.data_dir, paths.warehouse, paths.reports, seed, count,
                     batch_id=paths.batch_id)
        db = connect(paths.warehouse)
        try:
            _validate_batch(db, paths, report["ingested"])
            cut_off = data_as_of_date(db)
            as_of = as_of_date or cut_off
            completed = datetime.now(timezone.utc)
            quality = json.loads((paths.reports / "quality_report.json").read_text(
                encoding="utf-8"))
            counts = build_star(db, batch_id=paths.batch_id, status="complete",
                                started_at=started, completed_at=completed,
                                as_of_date=as_of, quality=quality)
            export_snapshot(db, paths.snapshot, batch_id=paths.batch_id,
                            completed_at=completed.isoformat(), as_of_date=as_of)
            parity = verify_snapshot(paths.snapshot, paths.batch_id, db)
            record_batch(db, paths.batch_id, "complete", completed_at=completed,
                         as_of_date=as_of)
        finally:
            db.close()
        batch = {"batch_id": paths.batch_id, "status": "complete",
                 "completed_at": completed.isoformat(), "as_of_date": as_of.isoformat()}
        atomic_write_json(paths.reports / "bi_snapshot_parity.json",
                          {"batch": batch, "star_row_counts": counts, **parity})
        report_hashes = {}
        for path in sorted(paths.reports.glob("*.json")):
            body = json.loads(path.read_text(encoding="utf-8"))
            atomic_write_json(path, _stamp(body, batch))
            report_hashes[path.name] = sha256_file(path)
        pointer = {"batch_id": paths.batch_id, "started_at": started.isoformat(),
                   "completed_at": completed.isoformat(), "as_of_date": as_of.isoformat(),
                   "as_of_source": "explicit" if as_of_date else "latest ledger/bank/invoice business date",
                   "business_timezone": BUSINESS_TIMEZONE,
                   "snapshot_manifest_sha256": sha256_file(
                       paths.snapshot / "snapshot_manifest.json"),
                   "reports": report_hashes}
        write_manifest(paths, status="complete", completed_at=completed.isoformat(),
                       as_of_date=as_of.isoformat(), error=None)
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        write_manifest(paths, status="failed", failed_at=datetime.now(timezone.utc).isoformat(),
                       error=error)
        if paths.warehouse.is_file():
            try:
                db = connect(paths.warehouse)
                try:
                    record_batch(db, paths.batch_id, "failed", error=error)
                finally:
                    db.close()
            except Exception:  # The JSON status file already records the failure.
                pass
        raise
    publish(root, pointer)
    removed = prune(root, keep) if keep else []
    return {"batch_id": paths.batch_id, "pointer": pointer, "report": report,
            "star_row_counts": counts, "pruned": removed}


def _published_or_explicit(args) -> tuple[Path, Path, dict | None]:
    """Explicit --warehouse/--data-dir win; otherwise use the published batch."""
    try:
        warehouse_path, data_dir = cli_paths(args.warehouse, args.data_dir, args.batch_root)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    pointer = None if args.warehouse else read_pointer(args.batch_root)
    return warehouse_path, data_dir, pointer


if __name__ == "__main__":
    import duckdb

    parser = argparse.ArgumentParser()
    parser.add_argument("command",
                        choices=["all", "trace", "quality", "published", "export-summary"])
    parser.add_argument("--batch-root", type=Path, default=DEFAULT_ROOT,
                        help="Directory holding batch folders and published.json")
    parser.add_argument("--data-dir", type=Path,
                        help="Explicit source directory (trace/quality); default: published batch")
    parser.add_argument("--warehouse", type=Path,
                        help="Explicit DuckDB file (trace/quality); default: published batch")
    parser.add_argument("--reports", type=Path, default=Path("reports"),
                        help="Where `quality` writes its recheck report")
    parser.add_argument("--figure-id", help="For trace, e.g. REC-PAY-00001")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--as-of-date", type=date.fromisoformat,
                        help="Explicit report date for age_days (YYYY-MM-DD, Asia/Singapore)")
    parser.add_argument("--keep", type=int, default=3, help="Batch folders to retain")
    parser.add_argument("--out", type=Path,
                        help="For export-summary: new directory for the summary-role snapshot")
    args = parser.parse_args()
    if args.command == "all":
        result = run_batch(args.batch_root, args.seed, args.count, args.as_of_date, args.keep)
        print(json.dumps({"batch_id": result["batch_id"],
                          "completed_at": result["pointer"]["completed_at"],
                          "as_of_date": result["pointer"]["as_of_date"],
                          "evaluation": result["report"]["evaluation"]}, indent=2))
    elif args.command == "published":
        pointer = read_pointer(args.batch_root)
        if pointer is None:
            raise SystemExit(f"no published batch under {args.batch_root}")
        paths = resolve_published(args.batch_root)
        print(json.dumps({**pointer, "warehouse": str(paths.warehouse),
                          "data_dir": str(paths.data_dir),
                          "reports_dir": str(paths.reports)}, indent=2))
    elif args.command == "export-summary":
        from access import export_summary_snapshot

        if not args.out:
            parser.error("--out is required for export-summary")
        paths = resolve_published(args.batch_root)
        summary = export_summary_snapshot(paths.snapshot, args.out, paths.batch_id)
        print(json.dumps({"batch_id": summary["batch_id"], "out": str(args.out),
                          "tables": sorted(summary["tables"]),
                          "excluded_tables": summary["excluded_tables"]}, indent=2))
    elif args.command == "trace":
        if not args.figure_id:
            parser.error("--figure-id is required for trace")
        warehouse_path, _, _ = _published_or_explicit(args)
        database = duckdb.connect(str(warehouse_path), read_only=True)
        try:
            evidence = (trace_finding(database, args.figure_id)
                        if args.figure_id.startswith("DOC-")
                        else trace(database, args.figure_id))
            print(json.dumps(evidence, indent=2, default=str))
        except (KeyError, ValueError) as exc:
            print(f"trace rejected: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        finally:
            database.close()
    else:
        warehouse_path, data_dir, pointer = _published_or_explicit(args)
        database = duckdb.connect(str(warehouse_path), read_only=True)
        try:
            result = run_checks(database, data_dir)
        finally:
            database.close()
        args.reports.mkdir(parents=True, exist_ok=True)
        atomic_write_json(args.reports / "quality_report.json", _stamp(result, {
            "batch_id": pointer["batch_id"] if pointer else None,
            "status": "recheck", "warehouse": str(warehouse_path),
            "checked_at": datetime.now(timezone.utc).isoformat()}))
        print(json.dumps({key: result[key] for key in
                          ("status", "checks", "passed", "failed", "failures")}, indent=2))
        if result["status"] != "pass":
            raise SystemExit(1)
