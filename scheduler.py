"""Catch-up-safe weekly quality runner and optional macOS launchd definition.

The runner does not start a background service. A separate scheduler can call
``run-if-due`` repeatedly; the state file and lock allow one attempt per week.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import plistlib
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from batches import resolve_published
from quality_checks import run_checks


ZONE = ZoneInfo("Asia/Singapore")
LABEL = "com.finrecon.weekly-quality"


def week_start(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    local = now.astimezone(ZONE)
    return (local.date() - timedelta(days=local.weekday())).isoformat()


def is_due(now: datetime, last_week: str | None) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    local = now.astimezone(ZONE)
    monday = local.date() - timedelta(days=local.weekday())
    opening = datetime(monday.year, monday.month, monday.day, 9, tzinfo=ZONE)
    return local >= opening and last_week != monday.isoformat()


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_if_due(warehouse_path: Path | None, data_dir: Path | None, report_dir: Path, *,
               now: datetime | None = None, force: bool = False,
               batch_root: Path | None = None) -> dict:
    """Run one weekly quality attempt; persist a report even on input failure.

    With no explicit warehouse and data directory, the attempt checks the
    published batch (``batches.py``) and records its batch ID.
    """
    now = now or datetime.now(ZONE)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    local = now.astimezone(ZONE)
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    lock_path = report_dir / ".weekly_quality.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state_path = report_dir / "scheduler_state.json"
        state = (json.loads(state_path.read_text(encoding="utf-8"))
                 if state_path.is_file() else {})
        week = week_start(local)
        if not force and not is_due(local, state.get("last_attempt_week")):
            return {"status": "skipped", "week_start": week,
                    "reason": "not due or already attempted this week"}
        output = report_dir / f"weekly_quality_{week}.json"
        try:
            batch_id = None
            if warehouse_path is None and data_dir is None:
                published = resolve_published(batch_root)
                warehouse_path, data_dir = published.warehouse, published.data_dir
                batch_id = published.batch_id
            if warehouse_path is None or data_dir is None:
                raise ValueError("pass both warehouse and data directory, or neither")
            if not Path(warehouse_path).is_file():
                raise FileNotFoundError(f"missing warehouse: {warehouse_path}")
            db = duckdb.connect(str(warehouse_path), read_only=True)
            try:
                quality = run_checks(db, data_dir, as_of=now)
            finally:
                db.close()
            status = quality["status"]
            report = {"status": status, "attempted_at": local.isoformat(),
                      "week_start": week, "batch_id": batch_id,
                      "warehouse": str(warehouse_path), "quality": quality}
        except Exception as exc:
            status = "error"
            report = {"status": status, "attempted_at": local.isoformat(),
                      "week_start": week, "error": str(exc)}
        _atomic_json(output, report)
        _atomic_json(state_path, {"last_attempt_week": week, "last_status": status,
                                  "last_report": str(output.resolve()),
                                  "attempted_at": local.isoformat()})
        return {"status": status, "week_start": week,
                "report": str(output), "failed_checks":
                report.get("quality", {}).get("failed")}


def render_launchd(root: Path, python: Path) -> bytes:
    """Return a reviewable, inactive launchd definition for Monday 09:00 SGT."""
    root = Path(root).resolve()
    # Keep a virtualenv executable path instead of following its symlink to
    # the base interpreter, which may not have DuckDB installed.
    python = Path(os.path.abspath(python))
    if not (root / "scheduler.py").is_file() or not python.is_file():
        raise FileNotFoundError("project root or Python interpreter does not exist")
    report_dir = root / "reports"
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(python), str(root / "scheduler.py"), "run-if-due",
                             "--reports", str(report_dir)],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": {"Weekday": 1, "Hour": 9, "Minute": 0},
        "StandardOutPath": str(report_dir / "weekly.stdout.log"),
        "StandardErrorPath": str(report_dir / "weekly.stderr.log"),
        "RunAtLoad": True,
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run-if-due", "render-launchd"])
    parser.add_argument("--warehouse", type=Path, help="Default: published batch")
    parser.add_argument("--data-dir", type=Path, help="Default: published batch")
    parser.add_argument("--reports", type=Path, default=Path("reports"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    if args.command == "render-launchd":
        target = args.root / "ops" / f"{LABEL}.plist"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(render_launchd(args.root, args.python))
        print(target)
        return 0
    result = run_if_due(args.warehouse, args.data_dir, args.reports, force=args.force)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in ("pass", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
