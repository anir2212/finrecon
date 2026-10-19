# FinRecon

Finance figures are hard to review when nobody can show which feed rows produced them. FinRecon is a local workbench for that problem: it loads six synthetic finance feeds, reconciles ledger entries against bank settlements, checks delivery and accounting controls, and traces each reported figure back to source files and row numbers.

The pipeline is small enough to run on a laptop. It includes a read-only Streamlit review screen, explainable audit rules, a deterministic contract-term extractor, and a restricted question interface over approved SQL queries. [RESULTS.md](RESULTS.md) records measured results and their limits.

## A screen for analysts

The existing Streamlit screen shows reconciliation status, break ages, quality failures, audit findings, optional anomaly candidates, and a figure-ID source trace. A local automated render and trace click completed without app exceptions. It is a development review screen, not a deployed user product: browser usability and access controls have not been verified. Its break age currently uses today's date, so older synthetic fixture transactions can appear misleadingly old.

The next milestone is a **planned Power BI-ready analyst dashboard**. It starts with a published batch ID and an explicit as-of date so the warehouse, quality reports and dashboard cannot silently mix runs. A read-only reporting model would hold one row per reconciliation figure, separate audit and quality facts, date/status dimensions, and a source-evidence bridge keyed by figure ID, file, row and hash. A nonempty export/import must match DuckDB row counts and integer-cent totals; only the presentation layer formats cents as SGD. An analyst should be able to filter aged breaks, inspect control failures, and drill through to source evidence, with visible refresh status and permission boundaries. Power BI itself has **not** been built or tested.

## Run it

Use Python 3.12 from the repository root:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
make all PYTHON=.venv/bin/python
make test PYTHON=.venv/bin/python
```

`make all` generates the fixture feeds and a DuckDB warehouse under `data/`, then writes local reports under `reports/`. Both directories contain generated data and are ignored by Git. To inspect a figure and its underlying rows, run:

```sh
.venv/bin/python pipeline.py trace --figure-id REC-PAY-00001
```

The reconciliation engine uses integer SGD cents, exact references, a bounded date window, and a limited split-settlement search. It treats counterparty name similarity as a review hint. The quality gate checks source delivery, hashes, type conversion, totals, and lineage before a report is accepted.

## dbt check

The six staging models and two marts can also be built with dbt-duckdb. The 18 October local run built all eight views and passed 61 declared data tests on a disposable copy of the synthetic warehouse. Its 394 staging and 129 mart rows matched the separate direct SQL verifier; the exact versions and counts are in [the run record](evidence/dbt_cli_20261018.json).

To repeat the check after `make all`, keep dbt's writes away from the working warehouse:

```sh
.venv/bin/python -m pip install -r requirements-dbt.txt
cp data/finrecon.duckdb /tmp/finrecon-dbt-check.duckdb
FINRECON_DB_PATH=/tmp/finrecon-dbt-check.duckdb .venv/bin/dbt build --profiles-dir dbt --project-dir . --no-partial-parse --threads 1
```

Run these commands from the repository root. The absolute `FINRECON_DB_PATH` selects the copied database. The default profile path targets the local working warehouse.

## Repository map

| Path | Role |
|---|---|
| `gen/`, `warehouse.py` | Create the synthetic feeds and ingest raw rows with source metadata |
| `reconcile_engine.py`, `quality_checks.py` | Classify settlements and enforce source and finance controls |
| `audit_analytics.py`, `anomaly_detection.py` | Produce review candidates with row evidence |
| `contracts.py`, `copilot.py` | Trace contract terms and answer a fixed set of finance questions |
| `models/`, `dbt/` | DuckDB staging and mart views, profile, and data tests |
| `pipeline.py`, `dashboard.py` | Run the pipeline and review persisted results |
| `eval/`, `test_*.py` | Designed boundary fixtures and local tests |

## What the results mean

The main feeds and labels are synthetic. The 123/123 seeded reconciliation score and 74/74 quality checks measure this implementation against its authored fixture. A separate set of 15 hand-authored boundary cases checks dates, amounts, duplicates, and split settlements. The seeded audit rules found 5 injected scenarios and 1 ordinary false positive; optional anomaly screening found 3 injected cases and 3 ordinary candidates. These are review signals, not fraud verdicts. The dbt run proves local compilation, view creation, and declared tests on one copied warehouse. None of these establish performance on independent bank feeds, a recurring production schedule, or fraud detection accuracy. See [RESULTS.md](RESULTS.md) for the underlying counts and remaining checks.
