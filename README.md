# FinRecon

Finance figures are hard to review when nobody can show which feed rows produced them. FinRecon is a local workbench for that problem: it loads six synthetic finance feeds, reconciles ledger entries against bank settlements, checks delivery and accounting controls, and traces each reported figure back to source files and row numbers.

The pipeline is small enough to run on a laptop. It includes a read-only Streamlit review screen, explainable audit rules, a deterministic contract-term extractor, and a restricted question interface over approved SQL queries. [RESULTS.md](RESULTS.md) records measured results and their limits.

## A screen for analysts

Each run now builds a self-contained **batch** with its own warehouse, reports and a BI-ready snapshot. The batch is published only after its manifests, source hashes, figure links and snapshot totals all check out. A failed run is marked `failed`, and the previously published batch stays current. The Streamlit review screen opens on the published batch and its as-of date. Its four tabs cover reconciliation status, break ages, quality results, audit findings and a figure-ID source trace, and it can re-verify the cited source rows against their hashes.

Two roles control what the screen shows:
- `summary` shows figures, controls and findings, but no source files, rows or hashes.
- `reviewer` adds the drill-through to source rows.

Locally the role comes from `FINRECON_ROLE` and defaults to `summary`. Behind a proxy, a role header is accepted only together with a shared token, compared in constant time. `pipeline.py export-summary` writes a copy of the snapshot without the source bridge.

Since 23 October `ops/auth/` provides a real local login in front of the screen: Caddy, oauth2-proxy and a local Dex identity provider with one demo user per role and one with no role. `make auth-setup` generates the passwords and secrets into an ignored folder, `make auth-up` starts the stack, and `make dashboard-proxied` runs Streamlit behind it. Caddy deletes any identity headers the browser sends, checks the session on every path including Streamlit's websocket, and sets the role from the signed-in e-mail. `make proxy-walkthrough` passed 26/26 headless-Chrome checks: signed-out requests and websocket upgrades were redirected to the login even with the real token, spoofed role headers were ignored, the user with no role was refused, the reviewer walkthrough passed through the login, and sign-out ended the session ([evidence](evidence/proxy_walkthrough.json)). It is a local demo over plain HTTP with fixed users and no MFA.

A headless-Chrome walkthrough (`make browser-walkthrough`) passed 30/30 checks on 20 October, covering both roles, a refused unknown role, and a stale-source failure. The tab layout cut the keyboard path to the verify button from 76 to 29 Tab presses, and the 23–24 October accessibility fixes cut it again to 13. An axe-core audit under a protocol committed before the baseline scan found 71 unique violations before the fixes and 33 after (WCAG-tagged 32 → 5); the remainder come from Streamlit's own components. A "Plain tables" option renders every table as HTML so screen readers get all rows, not only the ones on screen ([before](evidence/a11y_audit_before.json), [after](evidence/a11y_audit_after.json)). This is an automated audit, not a WCAG conformance claim; no screen-reader review by a person has been done. Power BI Desktop does not run on macOS, so the BI path uses Tableau instead. `make tableau-extract` writes a Tableau Hyper extract of the snapshot, and its row counts and cent totals matched the snapshot. No Tableau workbook has been built yet, and no production identity provider or deployed proxy is involved. Exact figures are in [RESULTS.md](RESULTS.md).

## Run it

Use Python 3.12 from the repository root:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
make all PYTHON=.venv/bin/python
make test PYTHON=.venv/bin/python
```

`make all` builds a batch under `data/batches/<batch ID>/` (feeds, a fresh DuckDB warehouse, reports and the BI snapshot), validates it and then publishes it in `data/batches/published.json`. Generated data is ignored by Git. `make dashboard` opens the screen as `summary`; `make dashboard-reviewer` opens it as `reviewer`. The optional browser walkthrough and Tableau extract need `requirements-browser.txt` (plus an installed Google Chrome) and `requirements-tableau.txt`. To inspect a figure and its underlying rows, run:

```sh
.venv/bin/python pipeline.py trace --figure-id REC-PAY-00001
```

The reconciliation engine uses integer SGD cents, exact references, a bounded date window, and a limited split-settlement search. It treats counterparty name similarity as a review hint. The quality gate checks source delivery, hashes, type conversion, totals, and lineage before a report is accepted.

## dbt check

The six staging models and two marts can also be built with dbt-duckdb. The 18 October local run built all eight views and passed 61 declared data tests on a disposable copy of the synthetic warehouse. Its 394 staging and 129 mart rows matched the separate direct SQL verifier; the exact versions and counts are in [the run record](evidence/dbt_cli_20261018.json).

To repeat the check after `make all`, keep dbt's writes away from the working warehouse:

```sh
.venv/bin/python -m pip install -r requirements-dbt.txt
.venv/bin/python pipeline.py published   # prints the published batch's warehouse path
cp data/batches/<batch ID>/finrecon.duckdb /tmp/finrecon-dbt-check.duckdb
FINRECON_DB_PATH=/tmp/finrecon-dbt-check.duckdb .venv/bin/dbt build --profiles-dir dbt --project-dir . --no-partial-parse --threads 1
```

Run these commands from the repository root. The absolute `FINRECON_DB_PATH` selects the copied database; the profile's default relative path points at the legacy `data/finrecon.duckdb`, so always pass it. On 20 October the same build passed 69/69 nodes on a copy of a published batch's warehouse.

## Repository map

| Path | Role |
|---|---|
| `gen/`, `warehouse.py` | Create the synthetic feeds and ingest raw rows with source metadata |
| `reconcile_engine.py`, `quality_checks.py` | Classify settlements and enforce source and finance controls |
| `audit_analytics.py`, `anomaly_detection.py` | Produce review candidates with row evidence |
| `contracts.py`, `copilot.py` | Trace contract terms and answer a fixed set of finance questions |
| `models/`, `dbt/` | DuckDB staging and mart views, profile, and data tests |
| `pipeline.py`, `batches.py`, `dashboard.py` | Run the pipeline, publish validated batches and review the published one |
| `access.py`, `reporting.py`, `tableau_export.py` | Roles, the BI snapshot, and the Tableau Hyper extract |
| `eval/`, `test_*.py` | Designed boundary fixtures and local tests |

## What the results mean

The main feeds and labels are synthetic. The 123/123 seeded reconciliation score and 74/74 quality checks measure this implementation against its authored fixture. A separate set of 15 hand-authored boundary cases checks dates, amounts, duplicates, and split settlements. The seeded audit rules found 5 injected scenarios and 1 ordinary false positive; optional anomaly screening found 3 injected cases and 3 ordinary candidates. These are review signals, not fraud verdicts. The dbt run proves local compilation, view creation, and declared tests on one copied warehouse. None of these establish performance on independent bank feeds, a recurring production schedule, or fraud detection accuracy. See [RESULTS.md](RESULTS.md) for the underlying counts and remaining checks.
