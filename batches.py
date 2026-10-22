"""Batch directories and one atomically replaced publication pointer.

Every ``pipeline.py all`` run builds a complete batch in its own directory:
source CSVs, a fresh DuckDB warehouse, JSON reports and a BI snapshot. Readers
see a batch only after ``published.json`` names it. Replacing that small file
with ``os.replace`` is atomic on one filesystem, so a failed or interrupted run
leaves the previous complete batch visible and never mixes files from two runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_ROOT = Path(__file__).resolve().parent / "data" / "batches"
POINTER = "published.json"
MANIFEST = "batch_manifest.json"
BUSINESS_TIMEZONE = "Asia/Singapore"
BATCH_ID = re.compile(r"^B\d{8}-\d{6}-\d{6}$")
STATUSES = {"running", "failed", "complete"}


@dataclass(frozen=True)
class BatchPaths:
    """File layout of one self-contained batch."""

    batch_id: str
    directory: Path

    @property
    def data_dir(self) -> Path:
        return self.directory / "generated"

    @property
    def warehouse(self) -> Path:
        return self.directory / "finrecon.duckdb"

    @property
    def reports(self) -> Path:
        return self.directory / "reports"

    @property
    def snapshot(self) -> Path:
        return self.directory / "bi_snapshot"

    @property
    def manifest(self) -> Path:
        return self.directory / MANIFEST


def new_batch_id(now: datetime | None = None) -> str:
    """Return a sortable UTC batch ID such as ``B20261020-013000-123456``."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("batch time must include a timezone")
    return now.astimezone(timezone.utc).strftime("B%Y%m%d-%H%M%S-%f")


def batch_paths(root: Path, batch_id: str) -> BatchPaths:
    if not BATCH_ID.fullmatch(batch_id or ""):
        raise ValueError(f"invalid batch ID: {batch_id!r}")
    return BatchPaths(batch_id, Path(root) / batch_id)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_write_json(path: Path, value) -> None:
    """Write JSON to a sibling temporary file, flush it, then replace the target."""
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as file:
        file.write(json.dumps(value, indent=2, default=str) + "\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def write_manifest(paths: BatchPaths, **fields) -> dict:
    """Merge fields into the batch's own status file."""
    current = (json.loads(paths.manifest.read_text(encoding="utf-8"))
               if paths.manifest.is_file() else {"batch_id": paths.batch_id})
    current.update(fields)
    if current.get("status") not in STATUSES:
        raise ValueError(f"unknown batch status: {current.get('status')}")
    atomic_write_json(paths.manifest, current)
    return current


def read_pointer(root: Path) -> dict | None:
    path = Path(root) / POINTER
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def publish(root: Path, pointer: dict) -> None:
    """Make one complete batch visible to every reader in a single replace."""
    paths = batch_paths(root, pointer["batch_id"])
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise ValueError(f"cannot publish batch with status {manifest.get('status')}")
    atomic_write_json(Path(root) / POINTER, pointer)


def list_attempts(root: Path) -> list[dict]:
    """Return every batch status file, oldest first."""
    attempts = []
    root = Path(root)
    if not root.is_dir():
        return attempts
    for directory in root.iterdir():
        if directory.is_dir() and BATCH_ID.fullmatch(directory.name):
            path = directory / MANIFEST
            if path.is_file():
                attempts.append(json.loads(path.read_text(encoding="utf-8")))
            else:
                attempts.append({"batch_id": directory.name, "status": "unknown"})
    return sorted(attempts, key=lambda item: item["batch_id"])


def resolve_published(root: Path | None = None) -> BatchPaths:
    """Return the published batch or raise if none exists or it is incomplete."""
    root = Path(root or DEFAULT_ROOT)
    pointer = read_pointer(root)
    if pointer is None:
        raise FileNotFoundError(
            f"no published batch under {root}; run `make all` or pass explicit paths")
    paths = batch_paths(root, pointer["batch_id"])
    if not paths.manifest.is_file():
        raise FileNotFoundError(f"published batch directory is missing: {paths.directory}")
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    if manifest.get("status") != "complete" or manifest.get("batch_id") != paths.batch_id:
        raise ValueError(f"published batch {paths.batch_id} is not complete")
    return paths


def cli_paths(warehouse: Path | None, data_dir: Path | None,
              root: Path | None = None) -> tuple[Path, Path]:
    """Explicit paths win (both required); otherwise use the published batch."""
    if warehouse or data_dir:
        if not (warehouse and data_dir):
            raise ValueError("pass both --warehouse and --data-dir, or neither")
        return Path(warehouse), Path(data_dir)
    paths = resolve_published(root)
    return paths.warehouse, paths.data_dir


def prune(root: Path, keep: int = 3) -> list[str]:
    """Delete older batch directories, never the published one."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    root = Path(root)
    pointer = read_pointer(root)
    published = pointer["batch_id"] if pointer else None
    names = sorted(directory.name for directory in root.iterdir()
                   if directory.is_dir() and BATCH_ID.fullmatch(directory.name))
    removed = []
    for name in names[:-keep]:
        if name != published:
            shutil.rmtree(root / name)
            removed.append(name)
    return removed
