"""Real-browser reviewer walkthrough of the batch review screen.

Builds disposable batches in a temporary directory, starts headless Streamlit
servers on 127.0.0.1 and drives them with Playwright using the installed Google
Chrome (``channel="chrome"``; no browser download). Scenarios:

* reviewer journey: header and metrics against the snapshot, one mismatch
  checked against ``warehouse.trace``, verified drill-through, a planted
  finding versus the known false positive, and keyboard-only verification;
* summary role (no source links or rows) and an unknown role (denied);
* proxy mode with real HTTP headers: missing or wrong token denied, the
  token plus a role header admitted with that role;
* a failed refresh plus a stale batch, and an empty batch root.

Writes compact evidence to ``evidence/browser_walkthrough.json`` and three
screenshots to ``evidence/browser/``. Prints SKIP and exits 0 without evidence
when Chrome or Playwright is missing. Run with an interpreter that has
``requirements-dashboard.txt`` and ``requirements-browser.txt`` installed.
This is a local functional check, not an accessibility audit or a load test.
"""

from __future__ import annotations

import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from unittest import mock

import duckdb

import pipeline
from access import TOKEN_HEADER
from batches import resolve_published
from dashboard import fixture_labels, format_sgd
from reporting import read_snapshot_table
from warehouse import trace

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "evidence" / "browser_walkthrough.json"
SHOTS = ROOT / "evidence" / "browser"
MAC_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
VERIFY = "Verify source rows"
DRILL_TAB = "Drill-through"
# Fixed before the tabbed layout was measured: at most half the 76 Tab presses that
# the single-page layout needed on 20 October.
KEYBOARD_TARGET = 38


def chrome_path() -> Path | None:
    for candidate in (os.environ.get("CHROME_PATH"), str(MAC_CHROME),
                      shutil.which("google-chrome"), shutil.which("google-chrome-stable")):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def skip_reason() -> str | None:
    if chrome_path() is None:
        return "Google Chrome was not found (set CHROME_PATH to its executable)."
    for module, requirement in (("playwright.sync_api", "requirements-browser.txt"),
                                ("streamlit", "requirements-dashboard.txt")):
        try:
            __import__(module)
        except ImportError:
            return f"{module} is not installed; install {requirement}."
    return None


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


@contextmanager
def streamlit_server(env_overrides: dict, log_path: Path):
    """Run the dashboard headless on a free localhost port and always stop it."""
    port = free_port()
    env = {key: value for key, value in os.environ.items() if not key.startswith("FINRECON_")}
    env.update({"STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false", **env_overrides})
    command = [sys.executable, "-m", "streamlit", "run", "dashboard.py",
               "--server.address", "127.0.0.1", "--server.port", str(port),
               "--server.headless", "true", "--browser.gatherUsageStats", "false"]
    server = {"stopped_cleanly": None}
    with log_path.open("w") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                                   stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Streamlit exited early; see {log_path}")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health",
                                                timeout=1) as response:
                        if response.read() == b"ok":
                            break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("Streamlit did not become healthy within 60 s")
                time.sleep(0.3)
            yield f"http://127.0.0.1:{port}/", server
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            server["stopped_cleanly"] = not port_open(port)


def body_text(page) -> str:
    return page.locator("body").inner_text()


def wait_text(page, text: str, timeout: int = 30000) -> None:
    page.wait_for_function("(text) => document.body.innerText.includes(text)",
                           arg=text, timeout=timeout)


def metrics(page) -> dict[str, str]:
    found = {}
    for metric in page.locator('[data-testid="stMetric"]').all():
        label = metric.locator('[data-testid="stMetricLabel"]').inner_text().strip()
        found[label] = metric.locator('[data-testid="stMetricValue"]').inner_text().strip()
    return found


def alerts(page, kind: str) -> list[str]:
    return [item.inner_text().strip() for item in
            page.locator(f'[data-testid="stAlertContent{kind.capitalize()}"]').all()]


def open_app(page, url: str, ready_text: str) -> None:
    page.goto(url, wait_until="domcontentloaded")
    wait_text(page, ready_text, timeout=60000)
    page.wait_for_timeout(500)


def open_tab(page, name: str) -> None:
    """Select a dashboard tab; hidden panels are out of the DOM's visible text."""
    tab = page.get_by_role("tab", name=name)
    tab.click()
    page.wait_for_function(
        "(name) => [...document.querySelectorAll('[role=tab]')].some("
        "t => t.innerText.trim() === name && t.getAttribute('aria-selected') === 'true')",
        arg=name, timeout=30000)
    page.wait_for_timeout(300)


def select_figure(page, figure_id: str) -> None:
    open_tab(page, DRILL_TAB)
    box = page.locator('[data-testid="stSelectbox"]').filter(has_text="Figure ID")
    box.locator("input").click()
    box.locator("input").fill(figure_id)
    page.keyboard.press("Enter")
    wait_text(page, f"Selected {figure_id}")
    page.wait_for_timeout(300)


def verify_selected(page) -> str:
    page.get_by_role("button", name=VERIFY).click()
    wait_text(page, "Source hashes match")
    page.wait_for_timeout(300)
    return body_text(page)


def expected_from_trace(warehouse: Path, figure_id: str) -> dict:
    """Independent expectation from the lineage CLI path, not the snapshot."""
    db = duckdb.connect(str(warehouse), read_only=True)
    try:
        rows = trace(db, figure_id)
    finally:
        db.close()
    ledger = sum(int(row["values"]["amount_cents"]) for row in rows
                 if row["source_name"] == "ledger")
    bank = sum(int(row["values"]["amount_cents"]) for row in rows
               if row["source_name"] == "bank")
    return {"reference": rows[0]["values"]["reference"],
            "posted_on": min(row["values"]["posted_on"] for row in rows),
            "ledger": format_sgd(ledger), "bank": format_sgd(bank),
            "difference": format_sgd(ledger - bank),
            "source_ids": sorted(row["values"]["id"] for row in rows)}


def snapshot_rows(snapshot: Path, table: str) -> list[dict]:
    columns, rows = read_snapshot_table(snapshot, table)
    return [dict(zip(columns, row)) for row in rows]


def reviewer_journey(page, url: str, paths, shots: dict) -> dict:
    rec = snapshot_rows(paths.snapshot, "fct_reconciliation")
    findings = snapshot_rows(paths.snapshot, "fct_finding")
    quality = snapshot_rows(paths.snapshot, "fct_quality")
    open_app(page, url, f"Published batch {paths.batch_id}")
    shown = metrics(page)
    expected_metrics = {
        "Figures in batch": str(len(rec)),
        "Matched": str(sum(row["status"] == "matched" for row in rec)),
        "Breaks shown": str(sum(bool(row["is_break"]) for row in rec)),
        "Quality checks failed": str(sum(row["status"] == "fail" for row in quality)),
        "Findings shown": str(len(findings))}
    header = body_text(page)

    mismatch = sorted(row["figure_id"] for row in rec if row["status"] == "fee_fx_difference")[0]
    expected = expected_from_trace(paths.warehouse, mismatch)
    select_figure(page, mismatch)
    line = next(text for text in body_text(page).splitlines() if f"Selected {mismatch}" in text)
    verified = verify_selected(page)
    page.screenshot(path=str(shots["reviewer"]), full_page=True)

    labels = fixture_labels(paths.data_dir)
    audit = sorted(row["figure_id"] for row in findings if row["figure_id"].startswith("AUD-"))
    planted = next(figure for figure in audit if figure in labels)
    false_positives = [figure for figure in audit if figure not in labels]
    page.get_by_text("Show synthetic fixture labels").click()
    open_tab(page, DRILL_TAB)
    wait_text(page, "Fixture label (development only):")
    label_seen = {}
    for figure in (planted, *false_positives):
        select_figure(page, figure)
        label_seen[figure] = next(
            text.split(":", 1)[1].strip() for text in body_text(page).splitlines()
            if text.startswith("Fixture label (development only):"))
        verify_selected(page)

    open_app(page, url, f"Published batch {paths.batch_id}")
    keys = []

    def focused() -> dict:
        return page.evaluate("""() => { const e = document.activeElement;
            return { role: e && e.getAttribute('role'), text: e ? e.innerText.trim() : '',
                     selected: e && e.getAttribute('aria-selected') }; }""")

    for _ in range(60):  # Tab into the tab list.
        page.keyboard.press("Tab")
        keys.append("Tab")
        if focused()["role"] == "tab":
            break
    for _ in range(6):  # Arrow keys move between tabs (WAI-ARIA tabs pattern).
        state = focused()
        if state["role"] == "tab" and state["text"] == DRILL_TAB and state["selected"] == "true":
            break
        page.keyboard.press("ArrowRight")
        keys.append("ArrowRight")
        page.wait_for_timeout(250)
    for _ in range(20):  # Then Tab to the verify button inside the panel.
        page.keyboard.press("Tab")
        keys.append("Tab")
        if focused()["text"] == VERIFY:
            break
    keyboard_ok = False
    if focused()["text"] == VERIFY:
        page.keyboard.press("Enter")
        keys.append("Enter")
        try:
            wait_text(page, "Source hashes match")
            keyboard_ok = True
        except Exception:
            keyboard_ok = False
    presses = len(keys)

    checks = {
        "header_names_published_batch_and_role": f"Published batch {paths.batch_id}" in header
        and "role: reviewer" in header,
        "metrics_equal_snapshot": shown == expected_metrics,
        "mismatch_reference_matches_trace": f"reference {expected['reference']}" in line,
        "mismatch_date_matches_trace": f"posted {expected['posted_on']}" in line,
        "mismatch_amounts_match_trace": all(f"{side} {expected[side]}" in line
                                            for side in ("ledger", "bank", "difference")),
        "drill_through_verifies_source_rows": "Source hashes match" in verified
        and all(source in verified for source in expected["source_ids"]),
        "planted_finding_labelled_planted": label_seen[planted] == "planted",
        "known_false_positive_labelled_not_planted": len(false_positives) == 1
        and label_seen[false_positives[0]] == "not planted",
        "keyboard_reaches_and_activates_verify": keyboard_ok,
        "keyboard_path_within_target": keyboard_ok and presses <= KEYBOARD_TARGET,
    }
    return {"checks": checks, "metrics_shown": shown, "metrics_expected": expected_metrics,
            "mismatch": {"figure_id": mismatch, "screen_line": line, "trace": expected},
            "labels_seen": label_seen, "keys_to_verify": presses,
            "keyboard_target": KEYBOARD_TARGET,
            "key_sequence": f"{keys.count('Tab')} Tab, {keys.count('ArrowRight')} ArrowRight, "
                            f"{keys.count('Enter')} Enter"}


def summary_and_denied(browser, root: Path, paths, logs: Path, shots: dict) -> dict:
    with streamlit_server({"FINRECON_BATCH_ROOT": str(root), "FINRECON_ROLE": "summary"},
                          logs / "summary.log") as (url, server):
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        open_app(page, url, f"Published batch {paths.batch_id}")
        open_tab(page, DRILL_TAB)
        text = body_text(page)
        summary = {
            "role_shown": "role: summary" in text,
            "metrics_visible": len(metrics(page)) == 5,
            "no_verify_button": page.get_by_role("button", name=VERIFY).count() == 0,
            "no_fixture_label_toggle": "Show synthetic fixture labels" not in text,
            "explains_missing_source_access": any("does not include source" in item
                                                  for item in alerts(page, "info")),
        }
        page.screenshot(path=str(shots["summary"]), full_page=True)
        page.close()
    summary_server = server
    with streamlit_server({"FINRECON_BATCH_ROOT": str(root), "FINRECON_ROLE": "admin"},
                          logs / "denied.log") as (url, server):
        page = browser.new_page()
        open_app(page, url, "Access denied")
        denied = {"access_denied_shown": any("Access denied" in item
                                             for item in alerts(page, "error")),
                  "no_metrics": len(metrics(page)) == 0,
                  "no_batch_header": f"Published batch {paths.batch_id}" not in body_text(page)}
        page.close()
    return {"summary": summary, "denied": denied,
            "servers_stopped": summary_server["stopped_cleanly"] and server["stopped_cleanly"]}


def proxy_mode(browser, root: Path, paths, logs: Path) -> dict:
    token = secrets.token_urlsafe(24)  # Per-run secret; never written to evidence.
    env = {"FINRECON_BATCH_ROOT": str(root), "FINRECON_ROLE_HEADER": "X-Forwarded-Role",
           "FINRECON_PROXY_TOKEN": token, "FINRECON_USER_HEADER": "X-Forwarded-User",
           "FINRECON_ROLE": "reviewer"}
    cases = {
        "no_headers": {},
        "role_without_token": {"X-Forwarded-Role": "reviewer"},
        "wrong_token": {"X-Forwarded-Role": "reviewer", TOKEN_HEADER: "guess"},
        "token_summary": {"X-Forwarded-Role": "summary", "X-Forwarded-User": "walkthrough-user",
                          TOKEN_HEADER: token},
        "token_reviewer": {"X-Forwarded-Role": "reviewer", TOKEN_HEADER: token},
    }
    seen = {}
    with streamlit_server(env, logs / "proxy.log") as (url, server):
        for name, headers in cases.items():
            context = browser.new_context(extra_http_headers=headers)
            page = context.new_page()
            denied = name in ("no_headers", "role_without_token", "wrong_token")
            open_app(page, url, "Access denied" if denied else f"Published batch {paths.batch_id}")
            if not denied:
                open_tab(page, DRILL_TAB)
            text = body_text(page)
            seen[name] = {"denied": "Access denied" in text,
                          "role_line": next((line.split("role:", 1)[1].strip() for line in
                                             text.splitlines() if "role:" in line), None),
                          "verify_button": page.get_by_role("button", name=VERIFY).count()}
            context.close()
    checks = {
        "missing_headers_denied_although_local_role_set": seen["no_headers"]["denied"],
        "role_without_token_denied": seen["role_without_token"]["denied"],
        "wrong_token_denied": seen["wrong_token"]["denied"],
        "token_summary_admitted_without_drill_through": seen["token_summary"]["role_line"]
        == "summary (walkthrough-user)" and seen["token_summary"]["verify_button"] == 0,
        "token_reviewer_admitted_with_drill_through": seen["token_reviewer"]["role_line"]
        == "reviewer" and seen["token_reviewer"]["verify_button"] == 1,
    }
    return {"checks": checks, "observed": seen, "server_stopped": server["stopped_cleanly"]}


def failure_states(browser, root: Path, paths, logs: Path, shots: dict) -> dict:
    from test_batches import _corrupting_generate

    with mock.patch.object(pipeline, "generate", _corrupting_generate("exchange")):
        try:
            pipeline.run_batch(root, keep=3)
        except ValueError:
            pass
    env = {"FINRECON_BATCH_ROOT": str(root), "FINRECON_ROLE": "reviewer",
           "FINRECON_STALE_HOURS": "0.0001"}
    with streamlit_server(env, logs / "failed.log") as (url, server):
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        open_app(page, url, "failed")
        warnings = alerts(page, "warning")
        text = body_text(page)
        page.screenshot(path=str(shots["failed_stale"]), full_page=True)
        page.close()
    failed = {
        "still_shows_previous_batch": f"Published batch {paths.batch_id}" in text,
        "refresh_status_failed": "refresh status: latest refresh failed" in text,
        "failed_refresh_warning": any("failed" in item and paths.batch_id in item
                                      for item in warnings),
        "stale_warning": any("Stale batch" in item for item in warnings),
        "metrics_still_visible": "Figures in batch" in text,
    }
    with tempfile.TemporaryDirectory() as empty:
        with streamlit_server({"FINRECON_BATCH_ROOT": empty, "FINRECON_ROLE": "reviewer"},
                              logs / "empty.log") as (url, empty_server):
            page = browser.new_page()
            open_app(page, url, "No published batch")
            empty_state = {"no_published_batch_error": any(
                "No published batch" in item for item in alerts(page, "error")),
                "no_metrics": len(metrics(page)) == 0}
            page.close()
    return {"failed_and_stale": failed, "empty_root": empty_state,
            "servers_stopped": server["stopped_cleanly"] and empty_server["stopped_cleanly"]}


def flatten(result: dict) -> dict[str, bool]:
    groups = {"journey": result["reviewer"]["checks"],
              "summary": result["access"]["summary"],
              "denied": result["access"]["denied"],
              "proxy": result["proxy"]["checks"],
              "failed": result["failure"]["failed_and_stale"],
              "empty": result["failure"]["empty_root"]}
    return {f"{group}.{name}": bool(ok)
            for group, checks in groups.items() for name, ok in checks.items()}


def main() -> int:
    reason = skip_reason()
    if reason:
        print(f"SKIP: {reason}")
        return 0
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    shots = {name: SHOTS / f"{name}.png" for name in ("reviewer", "summary", "failed_stale")}
    started = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix="finrecon-browser-") as folder:
        root, logs = Path(folder) / "batches", Path(folder) / "logs"
        logs.mkdir()
        pipeline.run_batch(root, keep=3)
        paths = resolve_published(root)
        result = {}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                with streamlit_server({"FINRECON_BATCH_ROOT": str(root),
                                       "FINRECON_ROLE": "reviewer"},
                                      logs / "reviewer.log") as (url, server):
                    page = browser.new_page(viewport={"width": 1400, "height": 1000})
                    result["reviewer"] = reviewer_journey(page, url, paths, shots)
                    page.close()
                result["reviewer"]["server_stopped"] = server["stopped_cleanly"]
                result["access"] = summary_and_denied(browser, root, paths, logs, shots)
                result["proxy"] = proxy_mode(browser, root, paths, logs)
                result["failure"] = failure_states(browser, root, paths, logs, shots)
                chrome_version = browser.version
            finally:
                browser.close()
    checks = flatten(result)
    servers = [result["reviewer"]["server_stopped"], result["access"]["servers_stopped"],
               result["proxy"]["server_stopped"], result["failure"]["servers_stopped"]]
    evidence = {
        "generated_at": started.isoformat(timespec="seconds"),
        "what": "Headless Chrome walkthrough of dashboard.py on disposable synthetic batches",
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "chrome": chrome_version,
                        "streamlit": metadata.version("streamlit"),
                        "playwright": metadata.version("playwright")},
        "passed": sum(checks.values()), "total": len(checks),
        "all_servers_stopped": all(servers),
        "checks": checks,
        "details": result,
        "screenshots": [str(path.relative_to(ROOT)) for path in shots.values()],
        "limits": ["Synthetic fixture data; batch IDs refer to deleted temporary batches.",
                   "Local single-machine check, not an accessibility audit or load test.",
                   "Proxy mode is exercised with browser-set headers standing in for a "
                   "reverse proxy; no real identity provider was used."],
    }
    RESULT.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"passed": evidence["passed"], "total": evidence["total"],
                      "failed": [name for name, ok in checks.items() if not ok],
                      "all_servers_stopped": evidence["all_servers_stopped"]}, indent=2))
    return 0 if evidence["passed"] == evidence["total"] and all(servers) else 1


if __name__ == "__main__":
    raise SystemExit(main())
