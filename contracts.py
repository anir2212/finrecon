"""Local deterministic contract-term baseline with exact source-span evidence."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

import duckdb

from warehouse import add_evidence, trace


PATTERNS = {
    "vendor": re.compile(r"^(?:Vendor|Supplier): ([^\n]+)$", re.MULTILINE),
    "payment_days": re.compile(r"^(?:Payment terms: Net |Invoices payable within )(\d+) days\.$", re.MULTILINE),
    "unit_price_cents": re.compile(r"^(?:Unit price:|Agreed price) SGD (\d+\.\d{2})\.$", re.MULTILINE),
    "cap_cents": re.compile(r"^(?:Invoice cap:|Maximum invoice amount) SGD (\d+\.\d{2})\.$", re.MULTILINE),
    "termination_days": re.compile(r"^(?:Termination notice: |Terminate with )(\d+) days(?:' notice)?\.$", re.MULTILINE),
    "auto_renewal": re.compile(r"^(?:Automatic renewal: |Renews automatically: )(Yes|No|true|false)\.$", re.MULTILINE),
}


def extract(path: Path) -> dict:
    """Parse all required fields, recording the exact character span of each value."""
    path = Path(path)
    content = path.read_text(encoding="utf-8")
    fields = {}
    for name, pattern in PATTERNS.items():
        matches = list(pattern.finditer(content))
        if len(matches) != 1:
            raise ValueError(f"{path}: expected exactly one {name}, found {len(matches)}")
        match = matches[0]
        raw = match.group(1)
        if name.endswith("_cents"):
            value = int(Decimal(raw) * 100)
        elif name.endswith("_days"):
            value = int(raw)
        elif name == "auto_renewal":
            value = raw.casefold() in ("yes", "true")
        else:
            value = raw
        start, end = match.span(1)
        if content[start:end] != raw or start < 0 or end > len(content):
            raise ValueError(f"{path}: invalid {name} span")
        fields[name] = {"value": value, "start": start, "end": end,
                        "source_text": raw}
    return {"contract_id": path.stem, "source_file": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "fields": fields}


def run(db: duckdb.DuckDBPyConnection, data_dir: Path) -> dict:
    """Extract local contracts and compare caps with invoices from the warehouse."""
    folder = Path(data_dir) / "contracts"
    documents = [extract(path) for path in sorted(folder.glob("CT-*.txt"))]
    invoice_rows = db.execute("""select id, vendor, amount_cents, source_file,
        source_row, source_hash from staging.invoices order by id""").fetchall()
    invoices = {row[0]: row for row in invoice_rows}
    findings = []
    db.execute("begin transaction")
    try:
        db.execute("""create or replace table audit.contract_findings (
            figure_id varchar primary key, contract_id varchar, invoice_id varchar,
            kind varchar, invoice_cents bigint, cap_cents bigint, contract_path varchar,
            contract_hash varchar, cap_start integer, cap_end integer)""")
        db.execute("delete from audit.figure_sources where figure_id like 'DOC-%'")
        for doc in documents:
            invoice_id = "INV-" + doc["contract_id"].split("-", 1)[1]
            invoice = invoices.get(invoice_id)
            if invoice is None or invoice[2] is None:
                raise ValueError(f"{doc['contract_id']}: missing/invalid invoice")
            if doc["fields"]["vendor"]["value"] != invoice[1]:
                raise ValueError(f"{doc['contract_id']}: vendor mismatch; manual review required")
            cap = doc["fields"]["cap_cents"]["value"]
            if invoice[2] > cap:
                figure_id = "DOC-" + doc["contract_id"]
                item = {"figure_id": figure_id, "kind": "invoice_above_contract_cap",
                        "contract_id": doc["contract_id"], "invoice_id": invoice_id,
                        "invoice_cents": invoice[2], "cap_cents": cap,
                        "currency": "SGD", "contract_file": doc["source_file"],
                        "contract_sha256": doc["sha256"],
                        "cap_source_span": doc["fields"]["cap_cents"]}
                findings.append(item)
                db.execute("insert into audit.contract_findings values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           [figure_id, doc["contract_id"], invoice_id, item["kind"],
                            invoice[2], cap, doc["source_file"], doc["sha256"],
                            doc["fields"]["cap_cents"]["start"],
                            doc["fields"]["cap_cents"]["end"]])
                add_evidence(db, figure_id, "invoices", invoice[3], invoice[4], invoice[5])
        db.execute("commit")
    except BaseException:
        db.execute("rollback")
        raise
    return {"documents": documents, "findings": findings}


def trace_finding(db: duckdb.DuckDBPyConnection, figure_id: str) -> dict:
    """Verify the contract bytes/span and return them with invoice row evidence."""
    row = db.execute("""select contract_path, contract_hash, cap_start, cap_end,
        cap_cents from audit.contract_findings where figure_id = ?""",
        [figure_id]).fetchone()
    if row is None:
        raise KeyError(f"unknown contract finding: {figure_id}")
    path, digest, start, end, cap_cents = row
    source = Path(path)
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise ValueError(f"contract changed since extraction for {figure_id}")
    content = source.read_text(encoding="utf-8")
    if not 0 <= start < end <= len(content):
        raise ValueError(f"invalid stored contract span for {figure_id}")
    excerpt = content[start:end]
    if not re.fullmatch(r"\d+\.\d{2}", excerpt) or int(Decimal(excerpt) * 100) != cap_cents:
        raise ValueError(f"contract span/value mismatch for {figure_id}")
    return {"contract": {"source_file": path, "sha256": digest,
                         "cap_cents": cap_cents, "start": start, "end": end,
                         "source_text": excerpt},
            "invoice_sources": trace(db, figure_id)}


def evaluate(truth_path: Path, result: dict) -> dict:
    """Evaluate six fields per document and the injected cap violations."""
    labels = json.loads(Path(truth_path).read_text(encoding="utf-8"))["contracts"]
    expected = {item["contract_id"]: item for item in labels}
    predicted = {item["contract_id"]: item for item in result["documents"]}
    if expected.keys() != predicted.keys():
        raise ValueError("contract IDs differ from evaluation manifest")
    correct = 0
    total = 0
    by_field = {}
    for field in PATTERNS:
        hit = sum(predicted[contract_id]["fields"][field]["value"] == label["terms"][field]
                  for contract_id, label in expected.items())
        by_field[field] = {"correct": hit, "total": len(expected),
                           "accuracy": hit / len(expected) if expected else None}
        correct += hit
        total += len(expected)
    expected_findings = {item["contract_id"] for item in labels if item["cap_violation"]}
    actual_findings = {item["contract_id"] for item in result["findings"]}
    return {"documents": len(expected), "fields": total, "correct_fields": correct,
            "field_accuracy": correct / total if total else None,
            "by_field": by_field, "cap_true_positives": len(actual_findings & expected_findings),
            "cap_false_positives": len(actual_findings - expected_findings),
            "cap_false_negatives": len(expected_findings - actual_findings)}
