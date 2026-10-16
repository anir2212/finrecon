# FinRecon measured results

## M1–M2 local run, 12 October 2026

`python pipeline.py all` completed on the seeded 120-ledger-transaction synthetic fixture. The generator wrote **120 ledger, 128 bank, 40 processor, 6 exchange, 50 invoice, and 50 purchase-order rows**. DuckDB's raw and typed staging counts matched each feed without dropping rows. Ten reconciliation figure IDs were traced back to exact source rows; the first ten figures referenced **16** source rows. The precise counts and file hashes are in `reports/reconciliation_summary.json` and the DuckDB load manifest.

The ledger/bank result had **123 reference-level classifications**: 91 matched, 5 missing in bank, 3 missing in ledger, 5 timing differences, 6 fee/FX differences, 7 many-to-one settlements, 3 duplicates, and 3 unexplained amount differences. It agreed with all 123 labels in the separate injected-issue manifest. Per-category precision and recall were 1.0 on this development fixture. These cases are produced by the same rules that the matcher encodes, so this is a consistency/coverage check and **not** a held-out estimate of reconciliation performance. Fuzzy counterparty suggestions remain review-only; no automatic fuzzy matches are counted.

The local run used P05's existing Python 3.12 virtual environment with DuckDB **1.4.1**. The reconciliation count remained 123/123 after M3/M4 fixture additions. The first ten figures still cite 16 exact source rows.

## M3 quality controls, 12 October 2026

`pipeline.py all` ran **72 checks: 72 passed, 0 failed** on the 120-transaction fixture. The output in `reports/quality_report.json` includes per-check evidence. Coverage includes 30 expected daily ledger deliveries; six each of row counts, source hashes, load freshness, required typed fields and ID uniqueness; signed amount conventions; processor fee identity; processor-to-GL control totals; and invoice-to-PO referential/cap integrity. Six mutation tests showed the controls failing on a missing daily file, changed source bytes, stale load, bad sign/date, missing staged row, bad processor arithmetic or ledger tie, and orphan/over-cap invoices. This is **mutation coverage of selected cases**, not proof that all possible defects are detectable. A failure writes the JSON report and causes `pipeline.py quality` or `make all` to exit nonzero.

## M4 audit screens, 12 October 2026

`pipeline.py all` produced five review findings from four injected audit scenarios: one duplicate vendor payment, two near-threshold payments, one segregation-of-duties conflict and one split purchase. The second near-threshold hit, `GL-00074`, was **not injected** and is counted as a false positive. Against the separate injection manifest: **4 true positives, 1 false positive, 0 false negatives**; micro precision **4/5 = 0.80**, recall **4/4 = 1.00**. Near-threshold precision is 1/2; the other three screens each have 1/1 precision and recall on this development fixture. Every finding is stored with ledger IDs and traceable file/line/hash evidence. The descriptive Benford sample has **120 amounts** and chi-square **32.1892**; the generator draws bounded uniform amounts, so the statistic is not used to claim fraud. Weekend posting IDs are listed as context, not classified fraud.

`python -m unittest discover -v` passed **13 tests**: three inherited prototype tests, two warehouse/pipeline tests, six quality mutation tests, and two audit tests. These tests verify exact source line numbers, invalid amount failure, lineage invalidation, all selected quality failures, injected audit hits, one measured false positive, repeatability, and finding traceability.

## M5 local dashboard and dictionary, 12 October 2026

`data_dictionary.py` generated `docs/DATA_DICTIONARY.md` for all six feeds, their columns, grain and exclusions. A local Streamlit `AppTest` render of `dashboard.py` completed with **0 app exceptions**, **3 metrics** and **3 data tables**; the underlying DuckDB view contained **8 reconciliation categories, 123 figures and 5 audit findings**. Browser layout and interactive click behavior have not been manually reviewed.

An operating-system schedule and external XBRL reconciliation remain **unmeasured**. The audit labels were generated from the same scenario rules as the screens, so this is a test of implementation and fixture coverage, not independent detection performance.

## M6 synthetic contracts and M7 restricted copilot, 12 October 2026

The local generator wrote **50 synthetic agreements** with two text layouts. The deterministic extractor returned **300/300 exact field values** for vendor, payment days, unit price, invoice cap, termination notice and auto-renewal; every extracted value has a checked character span. It found **2/2 injected invoice-above-cap cases**, with 0 false positives and 0 false negatives in this fixture. This is a template-consistency result. No CUAD documents, external contract corpus or LLM were evaluated. `DOC-CT-00010` traces to a verified contract span (`717.03` SGD, 71,703 cents) and invoice row 12 (71,803 cents).

The deterministic SQL copilot answered **25/25** fixed-grammar questions with SQL-checked facts and **25/25** correct citation sets on the local warehouse. It refused **4/4** adversarial/out-of-scope prompts, and the numeric guard rejected **4/4** proposed answers with unsupported numbers. These are local template tests; they do not measure general language understanding or LLM behavior. The full `make all` run now writes `reports/contract_report.json` and `reports/copilot_eval.json` alongside the existing reports.

After M6/M7, `python -m unittest discover -q` passed **17 tests**. A fresh Streamlit AppTest render and trace click had **0 exceptions**, **3 metrics**, **3 data tables** and **1 trace panel**.

## Hand-authored edges, weekly runner and dbt SQL, 13 October 2026

A separate edge fixture writes its own six source CSVs without calling `gen/synthetic.py`. It classified **15/15 hand-labeled cases** and traced all 15 figures to **40 source rows**. The cases cover exact amount/date, timing at three and four days, fee differences at 100 and 101 cents, duplicates, two- and four-row settlement, five-row rejection, missing sides and multi-row date-window violations. The fixture exposed and led to fixes for empty CSV ingestion and late bank rows in multi-row matches. These are designed boundary tests, not an external performance estimate. The original seeded run still reports **123/123** fixture classifications.

The weekly runner was executed once in `--force` mode on the freshly loaded local warehouse and wrote `reports/weekly_quality_2026-10-12.json`: **72 checks passed, 0 failed**. Its tests also confirmed same-week skip behavior, a nonzero failure after source-file mutation, a persisted error report when the warehouse is absent, and a valid reviewable Monday 09:00 plist. The plist was generated but **not loaded into launchd**; no recurring job is active.

Six dbt staging SQL files and two mart SQL files were executed through DuckDB after substituting their `source()` calls. The verifier compared **394 staged rows** and **128 mart rows** field-for-field with the existing views/tables. `models/schema.yml` documents **120 source, staging and mart column entries** and includes uniqueness/non-null tests. **dbt CLI compilation, model builds and dbt tests were not executed** because the adapter was not installed. The local `unittest` suite now passes **22 tests**.

## Posting-time and anomaly extension, 13 October 2026

After adding a timezone-aware ledger posting instant and contra account, the default seeded run passed **74/74 quality checks**. The rule-based audit screen found the new injected 22:30 Singapore posting alongside the prior four scenarios: **5 true positives, 1 false positive, 0 false negatives**. The same ordinary near-threshold row remains the false positive. Rule-screen precision is **5/6 ≈ 0.833** and recall **5/5 = 1.0** on this synthetic fixture. The descriptive Benford chi-square changed to **29.8214** on 120 amounts because the synthetic amount distribution changed; it is not used for classification.

With the already installed scikit-learn **1.6.1**, IsolationForest scored 120 ledger rows and flagged **6**: the **3 injected anomalies** (`GL-00046`, `GL-00047`, `GL-00048`) and **3 other review candidates** (`GL-00014`, `GL-00045`, `GL-00082`). Against the injection manifest this is **3 TP, 3 FP, 0 FN**, precision **0.50** and recall **1.00**. The model was not trained on a separate real journal population; this score is a local fixture measurement and the flags are not fraud verdicts. The optional dependency was already present; no package was downloaded for this run.

The dbt-compatible SQL verifier still matched **394 staging rows**, and now matched **129 mart rows** because the audit screen added one finding. `models/schema.yml` now documents **124 columns**. The hand-authored 24-row after-hours/anomaly fixture was added at this checkpoint and was run on 15 October; see the result below.

## Final local fixture and pipeline verification, 15 October 2026

`python -m eval.independent_audit_cases` completed the 24-row hand-authored fixture: the deterministic after-hours rule found `L-20` (07:59) and `L-21` (18:00), while `L-22` (08:00) and `L-23` (17:59) remained in hours. Against the two authored labels, the rule had **2 TP, 0 FP, 0 FN**. The optional IsolationForest had **2 TP, 1 FP, 0 FN**, falsely flagging `L-23`; its precision was 2/3 and recall 2/2 on these 24 rows. All five findings traced to five source rows. These are small designed boundary cases, not independent field labels.

`python -m unittest discover -v` passed **26 tests in 2.808 seconds** with no failures or errors. `python pipeline.py all` exited zero and reported **123/123** seeded reconciliation classifications. The current integrated reports record **74/74** quality checks, seeded audit **5 TP/1 FP/0 FN**, seeded anomaly **3 TP/3 FP/0 FN**, **15/15** reconciliation edge labels, and DuckDB SQL parity over **394 staging/129 mart rows**. This run did not execute dbt CLI, a live scheduler, external LLM, SEC XBRL, CUAD or production data feeds.
