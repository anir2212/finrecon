"""Optional IsolationForest screen for unusual synthetic journal postings."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from warehouse import add_evidence


def screen(db: duckdb.DuckDBPyConnection, *, contamination: float = 0.05,
           random_state: int = 17) -> dict:
    """Flag unusual postings; scores are review aids, not fraud probabilities."""
    if not 0 < contamination < 0.5:
        raise ValueError("contamination must be between zero and 0.5")
    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        return {"status": "unavailable", "reason": "scikit-learn is not installed",
                "findings": [], "rows_scored": 0}
    result = db.execute("""select id, posted_at, amount_cents, created_by,
        account, contra_account, source_file, source_row, source_hash
        from staging.ledger order by id""")
    columns = [item[0] for item in result.description]
    rows = [dict(zip(columns, values, strict=True)) for values in result.fetchall()]
    if any(row["posted_at"] is None or row["amount_cents"] is None or
           not row["created_by"] or not row["account"] or
           not row["contra_account"] for row in rows):
        raise ValueError("anomaly screen requires typed time, amount, user and account pair")
    if len(rows) < 20:
        return {"status": "insufficient_rows", "reason": "at least 20 postings required",
                "findings": [], "rows_scored": len(rows)}
    users = sorted({row["created_by"] for row in rows})
    pairs = sorted({(row["account"], row["contra_account"]) for row in rows})
    features = []
    for row in rows:
        local = row["posted_at"].astimezone(ZoneInfo("Asia/Singapore"))
        features.append([
            math.log1p(abs(row["amount_cents"])), local.hour + local.minute / 60,
            local.weekday(),
            *[int(row["created_by"] == user) for user in users],
            *[int((row["account"], row["contra_account"]) == pair) for pair in pairs],
        ])
    model = IsolationForest(n_estimators=128, contamination=contamination,
                            random_state=random_state, n_jobs=1)
    labels = model.fit_predict(features)
    scores = model.decision_function(features)
    db.execute("""create table if not exists audit.anomalies (
        figure_id varchar primary key, ledger_id varchar, score double,
        amount_cents bigint, posted_at timestamptz, created_by varchar,
        account varchar, contra_account varchar, recorded_at timestamptz)""")
    findings = []
    db.execute("begin transaction")
    try:
        db.execute("delete from audit.anomalies")
        db.execute("delete from audit.figure_sources where figure_id like 'ANOM-%'")
        for row, label, score in zip(rows, labels, scores, strict=True):
            if label != -1:
                continue
            figure_id = "ANOM-" + row["id"]
            item = {"figure_id": figure_id, "ledger_id": row["id"],
                    "score": round(float(score), 6),
                    "amount_cents": row["amount_cents"], "currency": "SGD",
                    "posted_at": row["posted_at"].isoformat(),
                    "created_by": row["created_by"], "account": row["account"],
                    "contra_account": row["contra_account"],
                    "explanation": "IsolationForest low-density review candidate; inspect source row."}
            findings.append(item)
            db.execute("insert into audit.anomalies values (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                figure_id, row["id"], float(score), row["amount_cents"],
                row["posted_at"], row["created_by"], row["account"],
                row["contra_account"], datetime.now(timezone.utc)])
            add_evidence(db, figure_id, "ledger", row["source_file"],
                         row["source_row"], row["source_hash"])
        db.execute("commit")
    except BaseException:
        db.execute("rollback")
        raise
    return {"status": "ok", "rows_scored": len(rows),
            "contamination": contamination, "random_state": random_state,
            "feature_names": ["log_abs_amount_cents", "local_hour", "weekday",
                              *[f"user:{user}" for user in users],
                              *[f"account_pair:{debit}/{credit}" for debit, credit in pairs]],
            "findings": findings}


def evaluate(truth_path: Path, result: dict) -> dict:
    if result["status"] != "ok":
        return {"status": "unmeasured", "reason": result.get("reason")}
    expected = set(json.loads(Path(truth_path).read_text(
        encoding="utf-8"))["injected_ids"])
    predicted = {item["ledger_id"] for item in result["findings"]}
    tp = len(expected & predicted)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    return {"status": "measured", "injected": len(expected),
            "flagged": len(predicted), "tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "flagged_ids": sorted(predicted), "missed_ids": sorted(expected - predicted)}
