"""Hand-authored time boundaries and journal anomalies outside the generator."""

from __future__ import annotations

import csv
import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

from anomaly_detection import evaluate as evaluate_anomalies, screen as screen_anomalies
from audit_analytics import evaluate as evaluate_audit, screen as screen_audit
from gen.synthetic import HEADERS
from warehouse import connect, ingest, trace


def _write(path: Path, source: str, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=HEADERS[source])
        writer.writeheader()
        writer.writerows(rows)


def run(report_dir: Path | None = None) -> dict:
    """Evaluate 07:59/08:00/17:59/18:00 boundaries and optional ML flags."""
    with tempfile.TemporaryDirectory(prefix="finrecon-audit-edge-") as folder:
        root = Path(folder)
        data = root / "sources"
        data.mkdir()
        ledger, bank = [], []
        base = date(2026, 9, 1)
        for i in range(24):
            day = base + timedelta(days=i)
            clock = {20: "07:59:00", 21: "18:00:00", 22: "08:00:00",
                     23: "17:59:00"}.get(i, "10:00:00")
            amount = 1_000_000 if i == 21 else 1_000 + i * 100
            ledger.append({"id": f"L-{i:02d}", "reference": f"TIME-{i:02d}",
                           "posted_on": day.isoformat(),
                           "posted_at": f"{day.isoformat()}T{clock}+08:00",
                           "counterparty": f"Vendor {i:02d}", "currency": "SGD",
                           "amount_cents": -amount,
                           "account": "SUSPENSE" if i == 21 else "AP-SETTLEMENT",
                           "contra_account": "CASH",
                           "created_by": "temp-user" if i == 21 else f"clerk-{i % 4}",
                           "approved_by": "manager"})
            bank.append({"id": f"B-{i:02d}", "reference": f"TIME-{i:02d}",
                         "posted_on": day.isoformat(),
                         "counterparty": f"Vendor {i:02d}", "currency": "SGD",
                         "amount_cents": -amount})
        for source in HEADERS:
            _write(data / f"{source}.csv", source,
                   ledger if source == "ledger" else bank if source == "bank" else [])
        (data / "audit_ground_truth.json").write_text(json.dumps({"findings": [
            {"kind": "after_hours", "ids": ["L-20"]},
            {"kind": "after_hours", "ids": ["L-21"]},
        ]}) + "\n", encoding="utf-8")
        (data / "anomaly_ground_truth.json").write_text(json.dumps(
            {"injected_ids": ["L-20", "L-21"]}) + "\n", encoding="utf-8")
        db = connect(root / "audit_edge.duckdb")
        try:
            ingest(db, data)
            audit = screen_audit(db)
            audit_score = evaluate_audit(data / "audit_ground_truth.json",
                                         audit["findings"])
            anomaly = screen_anomalies(db, contamination=0.10)
            anomaly_score = evaluate_anomalies(data / "anomaly_ground_truth.json",
                                                anomaly)
            citations = [item["figure_id"] for item in audit["findings"]]
            citations += [item["figure_id"] for item in anomaly["findings"]]
            traced = {figure_id: len(trace(db, figure_id)) for figure_id in citations}
        finally:
            db.close()
    result = {"rows": 24,
              "after_hours_ids": audit["after_hours_posting_ids"],
              "boundary_in_hours_ids": ["L-22", "L-23"],
              "audit_evaluation": audit_score,
              "anomaly_evaluation": anomaly_score,
              "traceable_figures": len(traced),
              "source_rows_traced": sum(traced.values()),
              "scope": "hand-authored local time and account fixtures; not real fraud labels"}
    if report_dir is not None:
        Path(report_dir).mkdir(parents=True, exist_ok=True)
        (Path(report_dir) / "independent_audit_eval.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run(Path("reports")), indent=2))
