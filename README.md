# FinRecon

Finance feed reconciliation with traceable controls.

A DuckDB-based finance lab that ingests six synthetic feeds, reconciles transactions, runs quality and audit controls, traces figures to source rows, and exposes a restricted SQL copilot.

## Why this project

Finance reports become difficult to defend when a figure cannot be traced back to its feed rows. This project builds a small reconciliation pipeline where classifications, quality checks, and audit findings retain source lineage.

## Run locally

You need Python 3.12.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
make all PYTHON=.venv/bin/python
make test PYTHON=.venv/bin/python
```

The pipeline writes local reconciliation and lineage reports. [Results](RESULTS.md) carries the exact seeded counts; `make dashboard` starts the viewer.

## Repository map

`gen/` creates the feeds; `reconcile_engine.py` classifies rows; `quality_checks.py` and `audit_analytics.py` check them; `pipeline.py` runs the path.

## Scope

The feeds are generated. A dbt project is present, but a dbt CLI production run, live scheduler, and external finance feeds have not been validated.
