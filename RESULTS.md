# FinRecon measured results

Historical commands below use the repository-local `.venv/bin/python` path for reproducibility. The original October 12–15 measurements used an equivalent Python 3.12 environment in a sibling local project; that path is not part of this standalone repository.

## M8 local login proxy and automated accessibility audit, 23–24 October 2026

Synthetic data only, on this Mac (macOS 15.3.1, Apple silicon), with headless Google Chrome 156.0.8078.47, Streamlit 1.50.0 and Playwright 1.64.0 from P05's environment. Times below are Singapore time. These are local functional checks, not a security review and not an accessibility certification.

- **Login proxy (`ops/auth/`).** A Compose stack of Caddy 2.11.7, oauth2-proxy v7.15.5 and Dex v2.45.1 (images 89 MB, 65 MB and 192 MB) publishes one port on 127.0.0.1. Dex holds three static local users: `reviewer@finrecon.local`, `summary@finrecon.local`, and `norole@finrecon.local`, which has no FinRecon role. `ops/auth/setup_local.py` generates their passwords, the proxy token and the OIDC secrets into the git-ignored `ops/auth/local/`. Caddy deletes client-sent `X-Forwarded-Role`, `X-Forwarded-User`, `X-FinRecon-Proxy-Token` and `X-Auth-Request-*` headers. It checks the session with oauth2-proxy, maps the confirmed e-mail to a role, and sets the role, user and token itself. Streamlit runs on the host bound to 127.0.0.1 with `FINRECON_ROLE_HEADER`, `FINRECON_USER_HEADER` and `FINRECON_PROXY_TOKEN`.
- **Proxy walkthrough: 26/26 checks** ([evidence](evidence/proxy_walkthrough.json), [screenshot](evidence/browser/proxy_reviewer.png)). The checks were listed in `eval/proxy_walkthrough.py` and committed (`1091e02`) before the first run. That first run stopped with an exception at the summary sign-in, before any result was recorded. oauth2-proxy's default `approval_prompt=force` made Dex show a consent page. After `--approval-prompt=auto` was added, the run passed 26/26, and the final run, after the accessibility fixes below, passed 26/26 again. What it showed:
  - **Blocked (6/6).** Without a session, `/`, `/_stcore/health` and a websocket upgrade to `/_stcore/stream` were all answered with a 302 to `/oauth2/sign_in`. Adding the real proxy token, a reviewer role header and a reviewer e-mail header changed nothing. Reaching Streamlit directly with a reviewer role header but no token gave "Access denied". A wrong Dex password left the browser on `/dex/auth/local/login` with no session cookie.
  - **Roles (8/8).** `summary@finrecon.local` saw "summary (summary@finrecon.local)", the five metrics, no verify button, no label toggle and the source-access notice. The same user still saw `summary` and no verify button while the browser sent `X-Forwarded-Role: reviewer`, reviewer user and e-mail headers, and the real proxy token. `norole@finrecon.local` signed in and was denied with "no FinRecon role for this user (none)" and no metrics.
  - **Journey (10/10).** The reviewer journey from the browser walkthrough also passed through the proxy: metrics equal to the snapshot (123 figures, 91 matched, 32 breaks, 0 failed controls, 14 findings), `REC-PAY-00019` equal to `trace`, verified source rows, and planted versus false-positive labels. Keyboard verification took 14 keys.
  - **Websocket and session (2/2).** The reviewer page opened three `/_stcore/stream` websockets through the proxy, which received 11, 411 and 102 frames. After `/oauth2/sign_out`, the next visit landed on the Dex form.

  The stack and app server were removed after each run. No generated secret appears in the evidence (the script checks this). The Makefile path (`make auth-setup`, `auth-up`, `dashboard-proxied`, `auth-down`) was also run once: the app's own health check answered `ok`, and an unauthenticated `/` through the proxy, with or without a spoofed role header, got a 302. Limits: three local demo users, plain HTTP on 127.0.0.1 (cookies not `Secure`), no MFA, no real directory and no penetration test.
- **Automated accessibility audit** ([before](evidence/a11y_audit_before.json), [after](evidence/a11y_audit_after.json)). The protocol in `eval/a11y_audit.py` was committed (`f94667c`) before the baseline run. It uses axe-core 4.13.0 with the WCAG 2.0/2.1/2.2 A and AA tags plus best-practice. It scans six screens: the reviewer's four tabs (Drill-through after verification), the summary role's Drill-through and the access-denied page. It then runs a counted Tab cycle and an accessibility-tree probe of the tables. After the baseline, the probe was extended to plain `st.table` elements and to the new plain-table option, so those plain-mode rows are extra measurements with no baseline.

  | Measure | Before (23 Oct, 19:00) | After (24 Oct, 01:18) |
  |---|---|---|
  | Unique axe violations (rule × element, six screens) | 71 | 33 |
  | WCAG-tagged | 32: colour contrast 22, `aria-allowed-attr` 7, `button-name` 2, `summary-name` 1 | 5: `aria-allowed-attr` 5 |
  | Best-practice only | 39: `region` 35, `heading-order` 4 | 28: `region` 28 |
  | Violation nodes, summed over the six screens | 186 | 105 |
  | Tab stops in one full cycle, Breaks tab | 45 (19 in the sidebar, 16 of them multiselect chips) | 21 (5 in the sidebar, 0 chips) |
  | Tab stops in one full cycle, Drill-through tab | 33 | 17 |
  | Keys to reach and press "Verify source rows" (browser walkthrough) | 29 on 20 October | 13 (9 Tab, 3 ArrowRight, Enter) |
  | Breaks table rows whose figure ID is in the accessibility tree (32 rows) | 12 (grid) | 12 in the default grid; 32 with "Plain tables" on |

  Changes behind these numbers:
  - the theme's primary red changed from #ff4b4b to #B42318 (white chip text and red tab labels went from 3.3:1 to 6.6:1);
  - success text is darker (4.49:1 to 6.1:1);
  - three captions became body text, since the caption grey measured 3.7:1;
  - section headings became h2, so none skips a level;
  - `toolbarMode = "minimal"` removed the developer Deploy button and an unlabelled menu button;
  - the bar chart moved to Altair without Vega's unnamed "…" actions menu, with a text description that lists the counts;
  - the status and finding-type multiselects sit in a collapsed expander, with a visible line saying what is selected;
  - the five-row age overview is now a static table whose first column is the row header, with darkened header text.

  A new sidebar option, "Plain tables", renders every table the same way. Glide grids draw on a canvas and exposed only the rows on screen: 12 data rows plus the header row per grid, which meant 12 of 32 break IDs, 12 of 14 finding IDs, and 60 of the 370 quality-control cells (12 of 74 rows). In plain mode all 224 break cells (192 cells and 32 row headers) and all 14 findings were in the tree. The grid remains the default because it can sort, search and download.

  **What remains:**
  - Five `aria-allowed-attr` findings: `aria-expanded` on Streamlit's own sidebar `<section>` and on chart and grid wrappers.
  - 28 `region` findings, because Streamlit renders no landmark elements.
  - "Needs review" results for colour contrast and duplicate ARIA IDs.
  - Each Glide grid still adds 4 toolbar buttons and several unnamed canvas stops (7 for the breaks grid).

  These come from Streamlit's own components, and the app cannot change them without patching Streamlit. **No screen-reader pass (VoiceOver or NVDA), zoom/reflow check or test with disabled users has been done; it needs a person.** The screen is therefore not described as accessible or WCAG conformant.
- **Browser walkthrough rerun.** `make browser-walkthrough` passed **30/30** on the changed screen, and every server stopped cleanly. `evidence/browser_walkthrough.json` and its screenshots now hold this 24 October run (Chrome 156). The 20 October figures below are kept as recorded then.
- **Tests.** 58 tests (one new AppTest for plain tables; the summary-role AppTest now checks for the label toggle by name, since every role gets the plain-table checkbox). In P10's environment, 51 passed and 7 AppTests were skipped. In P05's environment, 54 passed and the 4 Tableau tests were skipped. Before the changes, the same commands gave 57 tests (51/6 skipped and 53/4 skipped).
- **Not done.** No Tableau workbook (Tableau is not installed). No data-dictionary update: `docs/DATA_DICTIONARY.md` still has the user's uncommitted edit (SHA-256 `6abce7cb…c0a3ee`, unchanged), and the new `bi.*` and batch tables are still missing from `data_dictionary.py`. No manual screen-reader review.

## M8 roles, browser walkthrough and Tableau extract, 20 October 2026 (evening)

This section records the state on 20 October. Its "Not done" list is superseded in part by the 23–24 October section above.

Synthetic data only, as below. These checks run on this Mac and describe local behaviour, not a deployed service.

- **Roles.** `access.py` defines `summary` (figures, controls, findings; no source files, lines, hashes, rows or fixture labels) and `reviewer` (adds drill-through). Eight unit tests cover the following. Local mode defaults to `summary`. An unknown role is denied. Proxy mode needs the shared token, compares it in constant time and ignores `FINRECON_ROLE`. `evidence_for` raises `AccessDenied` for `summary`. The summary export drops the bridge, keeps the other tables byte-identical to the verified snapshot and refuses a modified snapshot. Two more AppTests check that `summary` renders one fewer table, with no verify button and no label toggle, and that an unknown role sees "Access denied" with no metrics or tables. Summary data still includes figure-level source IDs (`ledger_ids`, `bank_ids`) but not file paths, line numbers, hashes or row contents.
- **Headless-Chrome walkthrough: 30/30 checks** ([evidence](evidence/browser_walkthrough.json), [screenshots](evidence/browser/)). Chrome 155.0.8059.85, Streamlit 1.50.0 and Playwright 1.64.0, run from a separate Python 3.12 environment with Streamlit. That environment has scikit-learn, so its disposable batches had 14 findings, six of them anomaly candidates. On the reviewer journey the five metrics matched the snapshot (123 figures, 91 matched, 32 breaks, 0 failed controls, 14 findings). The selected fee/FX mismatch `REC-PAY-00019` showed the same reference, date (2026-09-20) and amounts (ledger −SGD 559.93, bank −SGD 560.68, difference SGD 0.75) as a separate `warehouse.trace` call, and "Verify source rows" listed both source IDs. With fixture labels switched on, the injected `AUD-after_hours-GL-00046` read "planted" and the known false positive `AUD-near_threshold-GL-00074` read "not planted".

  The verify button could be reached and pressed by keyboard alone. In the original single-page layout that took **76 Tab presses**, because every table toolbar and grid canvas is a stop. After the sections moved into four tabs and the button moved above the source table, it took **29 keys** (25 Tab, 3 ArrowRight, Enter), inside the 38-key target set before the change. This measures effort, not accessibility.

  In the role checks, `summary` saw the metrics but no verify button or label toggle, and an unknown role saw only "Access denied". Proxy mode was tested with real HTTP headers: no headers, a role without the token and a wrong token were all refused; a valid token with `summary` showed "summary (walkthrough-user)" without drill-through, and a valid token with `reviewer` got drill-through. After a failed refresh with a very short freshness limit, the screen kept the previous batch and showed both the failure and stale warnings. An empty batch folder showed "No published batch". Every server stopped cleanly.
- **Tableau Hyper extract** ([evidence](evidence/tableau_extract.json)). Power BI Desktop does not run on macOS, so the BI-tool path switched to Tableau. Using `tableauhyperapi` 0.0.26479 with telemetry off, `make tableau-extract` wrote `finrecon_summary.hyper` for the published batch `B20261020-081414-087737`: seven tables, 251 rows, with the bridge excluded. The reviewer extract has eight tables and 509 rows. Reopened in Hyper, every table's rows, column names and order, cent totals, figure keys and batch IDs matched the snapshot. Reconciliation cents were −8,227,368 ledger, −8,068,978 bank and −158,390 difference, the same as the warehouse. This batch was built without scikit-learn, so it has 8 findings. Four tests (skipped without the Hyper API) cover both audiences, refusing a tampered snapshot without leaving a file, and an unknown audience. **No Tableau workbook was built or rendered,** since Tableau is not installed.
- **dbt on a batch copy.** `dbt build` with dbt-core 1.12.6 and dbt-duckdb 1.9.6, run on a disposable copy of the published batch `B20261020-081414-087737`'s warehouse via an absolute `FINRECON_DB_PATH`, passed **69/69 nodes**: 8 view models and 61 data tests, with 0 warnings and 0 errors. The copy was deleted afterwards. This repeats the 18 October check on the batch layout. It is still one local build on synthetic data.
- **Tests.** 57 tests: with the project environment (now with the Hyper API), 51 pass and 6 AppTests are skipped. With that separate environment, 53 pass and the 4 Tableau tests are skipped. Between the two environments every test ran and passed. `git diff --check` passed.
- **Not done.** No real reverse proxy or identity provider, no accessibility audit, no Tableau workbook opened in Tableau, and no data-dictionary update.

## M8 batch publication and BI snapshot, 20 October 2026 (afternoon checkpoint)

This section records the afternoon state. Its "Not done" list is superseded in part by the evening section above.


All figures are from the seeded synthetic run (seed 17, 120 rows) with DuckDB 1.4.1 on this Mac. They describe implementation consistency, not real finance data.

- **Atomic publication.** `pipeline.py all` built batch `B20261020-081414-087737` in about 1.5 seconds (8.7 MB on disk). It reproduced the earlier figures: **123/123** seeded classifications and **74/74** quality controls. It then published the batch with one atomic pointer replace, and the data as-of date was 2026-09-30, the latest ledger, bank or invoice business date. A test makes the fourth feed (`exchange`) malformed. The ledger, bank and processor feeds load, ingestion stops, and the attempt is recorded as `failed`. The pointer still names the previous batch, which stays fully readable, and the view reports "latest refresh failed". A first-ever failed run publishes nothing.
- **Power BI-ready star snapshot.** The `bi` schema and its CSV and Parquet export contain:
  - `fct_reconciliation`: 123 rows, one per `REC-` figure;
  - `fct_finding`: 8 rows, one per finding;
  - `fct_quality`: 74 rows, one per control outcome;
  - `bridge_figure_source`: 258 figure-to-source links, carrying no money;
  - `dim_date` (30 rows), `dim_status` (8), `dim_finding_type` (7) and `dim_batch` (1).

  In both formats, row counts, figure keys and integer-cent totals equal independent warehouse totals: ledger −8,227,368, bank −8,068,978 and difference −158,390 cents. A modified snapshot file, a snapshot from another batch, or a report changed after publication is rejected. In each case the view shows no figures. `age_days` equals the calendar days from each figure's earliest source date to the batch as-of date, for all 123 rows under an explicit date. An as-of date earlier than the data is refused.
- **Analyst view.** Four Streamlit AppTests, run with a separate environment (Streamlit 1.50.0) because the base environment has no Streamlit, covered the following. The published view rendered with no exception, and "Verify source rows" succeeded. After a source CSV changed, drill-through was rejected. A failed refresh showed the previous batch with a warning. With no batch, the view showed an error and no tables.
- **Tests.** `.venv/bin/python -m unittest discover -q` passed **43 tests**, 4 of them skipped without Streamlit. With that separate environment, all 43 ran and passed. The default `trace`, `quality`, `verify_dbt_models.py` and copilot-eval commands all ran against the published batch.
- **Not done.** No access control or row/source permissions exist. No real-browser reviewer walkthrough was run, no Power BI file was created, and no dbt CLI rerun was done. `docs/DATA_DICTIONARY.md` was not regenerated in this run.

## Real dbt CLI build, 18 October 2026

Installed dbt-core **1.12.6**, dbt-duckdb **1.9.6**, and DuckDB **1.4.1** in an isolated Python 3.12 environment. Copied the existing synthetic `data/finrecon.duckdb` to a disposable temporary file and pointed `FINRECON_DB_PATH` at that copy, so dbt did not modify the active warehouse. `dbt debug` confirmed the adapter connection. `dbt build --profiles-dir dbt --project-dir . --no-partial-parse --threads 1` then passed **69/69 nodes**: eight view models and 61 data tests, with zero warnings/errors. The direct SQL verifier's earlier **394 staging / 129 mart** row totals matched the eight dbt views: 128 bank, 6 exchange, 50 invoice, 120 ledger, 40 processor, 50 purchase-order, 123 reconciliation and 6 audit-finding rows. The compact record is [`evidence/dbt_cli_20261018.json`](evidence/dbt_cli_20261018.json).

This verifies one local adapter build against saved synthetic feeds. It does not show a recurring dbt schedule, independent finance-data accuracy, SEC/CUAD coverage, or production reliability. Historical sections below correctly describe earlier runs before the adapter was installed.

## M1–M2 local run, 12 October 2026

`.venv/bin/python pipeline.py all` completed on the seeded 120-ledger-transaction synthetic fixture. The generator wrote **120 ledger, 128 bank, 40 processor, 6 exchange, 50 invoice, and 50 purchase-order rows**. DuckDB's raw and typed staging counts matched each feed without dropping rows. Ten reconciliation figure IDs were traced back to exact source rows; the first ten figures referenced **16** source rows. The precise counts and file hashes are in `reports/reconciliation_summary.json` and the DuckDB load manifest.

The ledger/bank result had **123 reference-level classifications**: 91 matched, 5 missing in bank, 3 missing in ledger, 5 timing differences, 6 fee/FX differences, 7 many-to-one settlements, 3 duplicates, and 3 unexplained amount differences. It agreed with all 123 labels in the separate injected-issue manifest. Per-category precision and recall were 1.0 on this development fixture. These cases are produced by the same rules that the matcher encodes, so this is a consistency/coverage check and **not** a held-out estimate of reconciliation performance. Fuzzy counterparty suggestions remain review-only; no automatic fuzzy matches are counted.

The local run used P05's existing Python 3.12 virtual environment with DuckDB **1.4.1**. The reconciliation count remained 123/123 after M3/M4 fixture additions. The first ten figures still cite 16 exact source rows.

## M3 quality controls, 12 October 2026

`pipeline.py all` ran **72 checks: 72 passed, 0 failed** on the 120-transaction fixture. The output in `reports/quality_report.json` includes per-check evidence. Coverage includes 30 expected daily ledger deliveries; six each of row counts, source hashes, load freshness, required typed fields and ID uniqueness; signed amount conventions; processor fee identity; processor-to-GL control totals; and invoice-to-PO referential/cap integrity. Six mutation tests showed the controls failing on a missing daily file, changed source bytes, stale load, bad sign/date, missing staged row, bad processor arithmetic or ledger tie, and orphan/over-cap invoices. This is **mutation coverage of selected cases**, not proof that all possible defects are detectable. A failure writes the JSON report and causes `pipeline.py quality` or `make all` to exit nonzero.

## M4 audit screens, 12 October 2026

`pipeline.py all` produced five review findings from four injected audit scenarios: one duplicate vendor payment, two near-threshold payments, one segregation-of-duties conflict and one split purchase. The second near-threshold hit, `GL-00074`, was **not injected** and is counted as a false positive. Against the separate injection manifest: **4 true positives, 1 false positive, 0 false negatives**; micro precision **4/5 = 0.80**, recall **4/4 = 1.00**. Near-threshold precision is 1/2; the other three screens each have 1/1 precision and recall on this development fixture. Every finding is stored with ledger IDs and traceable file/line/hash evidence. The descriptive Benford sample has **120 amounts** and chi-square **32.1892**; the generator draws bounded uniform amounts, so the statistic is not used to claim fraud. Weekend posting IDs are listed as context, not classified fraud.

`.venv/bin/python -m unittest discover -v` passed **13 tests**: three inherited prototype tests, two warehouse/pipeline tests, six quality mutation tests, and two audit tests. These tests verify exact source line numbers, invalid amount failure, lineage invalidation, all selected quality failures, injected audit hits, one measured false positive, repeatability, and finding traceability.

## M5 local dashboard and dictionary, 12 October 2026

`data_dictionary.py` generated `docs/DATA_DICTIONARY.md` for all six feeds, their columns, grain and exclusions. A local Streamlit `AppTest` render of `dashboard.py` completed with **0 app exceptions**, **3 metrics** and **3 data tables**; the underlying DuckDB view contained **8 reconciliation categories, 123 figures and 5 audit findings**. Browser layout and interactive click behavior have not been manually reviewed.

An operating-system schedule and external XBRL reconciliation remain **unmeasured**. The audit labels were generated from the same scenario rules as the screens, so this is a test of implementation and fixture coverage, not independent detection performance.

## M6 synthetic contracts and M7 restricted copilot, 12 October 2026

The local generator wrote **50 synthetic agreements** with two text layouts. The deterministic extractor returned **300/300 exact field values** for vendor, payment days, unit price, invoice cap, termination notice and auto-renewal; every extracted value has a checked character span. It found **2/2 injected invoice-above-cap cases**, with 0 false positives and 0 false negatives in this fixture. This is a template-consistency result. No CUAD documents, external contract corpus or LLM were evaluated. `DOC-CT-00010` traces to a verified contract span (`717.03` SGD, 71,703 cents) and invoice row 12 (71,803 cents).

The deterministic SQL copilot answered **25/25** fixed-grammar questions with SQL-checked facts and **25/25** correct citation sets on the local warehouse. It refused **4/4** adversarial/out-of-scope prompts, and the numeric guard rejected **4/4** proposed answers with unsupported numbers. These are local template tests; they do not measure general language understanding or LLM behavior. The full `make all` run now writes `reports/contract_report.json` and `reports/copilot_eval.json` alongside the existing reports.

After M6/M7, `.venv/bin/python -m unittest discover -q` passed **17 tests**. A fresh Streamlit AppTest render and trace click had **0 exceptions**, **3 metrics**, **3 data tables** and **1 trace panel**.

## Hand-authored edges, weekly runner and dbt SQL, 13 October 2026

A separate edge fixture writes its own six source CSVs without calling `gen/synthetic.py`. It classified **15/15 hand-labeled cases** and traced all 15 figures to **40 source rows**. The cases cover exact amount/date, timing at three and four days, fee differences at 100 and 101 cents, duplicates, two- and four-row settlement, five-row rejection, missing sides and multi-row date-window violations. The fixture exposed and led to fixes for empty CSV ingestion and late bank rows in multi-row matches. These are designed boundary tests, not an external performance estimate. The original seeded run still reports **123/123** fixture classifications.

The weekly runner was executed once in `--force` mode on the freshly loaded local warehouse and wrote `reports/weekly_quality_2026-10-12.json`: **72 checks passed, 0 failed**. Its tests also confirmed same-week skip behavior, a nonzero failure after source-file mutation, a persisted error report when the warehouse is absent, and a valid reviewable Monday 09:00 plist. The plist was generated but **not loaded into launchd**; no recurring job is active.

Six dbt staging SQL files and two mart SQL files were executed through DuckDB after substituting their `source()` calls. The verifier compared **394 staged rows** and **128 mart rows** field-for-field with the existing views/tables. `models/schema.yml` documents **120 source, staging and mart column entries** and includes uniqueness/non-null tests. **dbt CLI compilation, model builds and dbt tests were not executed** because the adapter was not installed. The local `unittest` suite now passes **22 tests**.

## Posting-time and anomaly extension, 13 October 2026

After adding a timezone-aware ledger posting instant and contra account, the default seeded run passed **74/74 quality checks**. The rule-based audit screen found the new injected 22:30 Singapore posting alongside the prior four scenarios: **5 true positives, 1 false positive, 0 false negatives**. The same ordinary near-threshold row remains the false positive. Rule-screen precision is **5/6 ≈ 0.833** and recall **5/5 = 1.0** on this synthetic fixture. The descriptive Benford chi-square changed to **29.8214** on 120 amounts because the synthetic amount distribution changed; it is not used for classification.

With the already installed scikit-learn **1.6.1**, IsolationForest scored 120 ledger rows and flagged **6**: the **3 injected anomalies** (`GL-00046`, `GL-00047`, `GL-00048`) and **3 other review candidates** (`GL-00014`, `GL-00045`, `GL-00082`). Against the injection manifest this is **3 TP, 3 FP, 0 FN**, precision **0.50** and recall **1.00**. The model was not trained on a separate real journal population; this score is a local fixture measurement and the flags are not fraud verdicts. The optional dependency was already present; no package was downloaded for this run.

The dbt-compatible SQL verifier still matched **394 staging rows**, and now matched **129 mart rows** because the audit screen added one finding. `models/schema.yml` now documents **124 columns**. The hand-authored 24-row after-hours/anomaly fixture was added at this checkpoint and was run on 15 October; see the result below.

## Final local fixture and pipeline verification, 15 October 2026

`.venv/bin/python -m eval.independent_audit_cases` completed the 24-row hand-authored fixture: the deterministic after-hours rule found `L-20` (07:59) and `L-21` (18:00), while `L-22` (08:00) and `L-23` (17:59) remained in hours. Against the two authored labels, the rule had **2 TP, 0 FP, 0 FN**. The optional IsolationForest had **2 TP, 1 FP, 0 FN**, falsely flagging `L-23`; its precision was 2/3 and recall 2/2 on these 24 rows. All five findings traced to five source rows. These are small designed boundary cases, not independent field labels.

`.venv/bin/python -m unittest discover -v` passed **26 tests in 2.808 seconds** with no failures or errors. `.venv/bin/python pipeline.py all` exited zero and reported **123/123** seeded reconciliation classifications. The current integrated reports record **74/74** quality checks, seeded audit **5 TP/1 FP/0 FN**, seeded anomaly **3 TP/3 FP/0 FN**, **15/15** reconciliation edge labels, and DuckDB SQL parity over **394 staging/129 mart rows**. This run did not execute dbt CLI, a live scheduler, external LLM, SEC XBRL, CUAD or production data feeds.
