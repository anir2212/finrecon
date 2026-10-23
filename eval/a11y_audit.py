"""Automated accessibility scan and keyboard/grid probe of the review screen.

This is an automated audit only. It does not make the screen accessible or
show WCAG conformance: axe-core finds a subset of problems, and a manual
screen-reader pass by a person (VoiceOver or NVDA) is still needed.

Protocol, fixed before the first (baseline) run:

* **Setup.** A disposable synthetic batch, the dashboard in local mode on
  127.0.0.1 (the login proxy does not change the page's DOM), headless Google
  Chrome through Playwright, viewport 1400 x 1000.
* **Screens.** S1 reviewer, Breaks tab (as loaded); S2 reviewer, Quality
  controls; S3 reviewer, Finding queue; S4 reviewer, Drill-through after
  "Verify source rows"; S5 summary role, Drill-through; S6 access denied
  (unknown role). axe skips hidden tab panels, so each tab is scanned on its own.
* **axe-core 4.13.0** (``package.json``) with tags wcag2a, wcag2aa, wcag21a,
  wcag21aa, wcag22aa and best-practice. Reported per screen: violated rules,
  impact and affected nodes, plus "incomplete" (needs manual review) rules.
  Totals: unique (rule, element) violations across screens, split into
  WCAG-tagged and best-practice-only.
* **Keyboard.** From a fresh S1 load, press Tab until focus returns to the
  first stop (at most 150 presses). Each stop's role and accessible name come
  from Chrome's accessibility tree (CDP). Reported: stops in one full cycle,
  stops in the sidebar, stops inside the two multiselects, stops with an
  empty accessible name. The same cycle is repeated with the Drill-through tab
  selected, counting stops inside that panel.
* **Glide grids.** For each table on S1 and S3, compare the rows the table
  should hold (from the snapshot) with the grid cells and row headers Chrome
  exposes in its accessibility tree, and check whether the first and last
  figure IDs (in display order) can be found there by name.

Writes ``evidence/a11y_audit_<label>.json``. Needs Chrome, Playwright,
Streamlit and ``node_modules/axe-core`` (``npm install``); prints SKIP and
exits 0 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import tempfile
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

import pipeline
from batches import resolve_published
from eval.browser_walkthrough import (DRILL_TAB, open_app, open_tab, select_figure,
                                      skip_reason as browser_skip_reason, snapshot_rows,
                                      streamlit_server, verify_selected)

ROOT = Path(__file__).resolve().parents[1]
AXE = Path(os.environ.get("AXE_CORE_JS", ROOT / "node_modules" / "axe-core" / "axe.min.js"))
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]
WCAG_TAGS = set(TAGS) - {"best-practice"}
MAX_TABS = 150
VISIBLE_TABLES = ("[...document.querySelectorAll('[data-testid=\"stDataFrame\"], "
                  "[data-testid=\"stTable\"]')].filter(e => e.offsetParent !== null)")
PLAIN = "Plain tables"

FOCUS_INFO = """() => {
  const e = document.activeElement;
  if (!e || e === document.body) return {body: true};
  if (!e.dataset.a11yStop) e.dataset.a11yStop = String(document.querySelectorAll('[data-a11y-stop]').length + 1);
  const owner = e.closest('[data-testid^="st"]');
  return {body: false, stop: e.dataset.a11yStop, tag: e.tagName.toLowerCase(),
          testid: owner ? owner.getAttribute('data-testid') : null,
          sidebar: !!e.closest('[data-testid="stSidebar"]'),
          multiselect: !!e.closest('[data-testid="stMultiSelect"]'),
          tabpanel: !!e.closest('[role="tabpanel"]')};
}"""


def skip_reason() -> str | None:
    if not AXE.is_file():
        return f"axe-core not found at {AXE}; run `npm install` in the project root."
    return browser_skip_reason()


def run_axe(page) -> dict:
    page.add_script_tag(path=str(AXE))
    raw = page.evaluate("""(tags) => axe.run(document, {
        runOnly: {type: 'tag', values: tags}, resultTypes: ['violations', 'incomplete']
      }).then(r => ({
        violations: r.violations.map(v => ({id: v.id, impact: v.impact, tags: v.tags,
          help: v.help, nodes: v.nodes.map(n => ({target: n.target.join(' '),
          summary: (n.failureSummary || '').slice(0, 300)}))})),
        incomplete: r.incomplete.map(v => ({id: v.id, impact: v.impact,
          nodes: v.nodes.length}))}))""", TAGS)
    for item in raw["violations"]:
        item["wcag"] = bool(WCAG_TAGS & set(item["tags"]))
        item["node_count"] = len(item["nodes"])
    return raw


def ax_node(cdp, expression: str) -> dict | None:
    handle = cdp.send("Runtime.evaluate", {"expression": expression})
    object_id = handle.get("result", {}).get("objectId")
    if not object_id:
        return None
    nodes = cdp.send("Accessibility.getPartialAXTree",
                     {"objectId": object_id, "fetchRelatives": False})["nodes"]
    node = next((item for item in nodes if not item.get("ignored")), nodes[0] if nodes else None)
    if node is None:
        return None
    return {"role": node.get("role", {}).get("value"),
            "name": (node.get("name", {}).get("value") or "").strip()}


def tab_cycle(page, cdp) -> dict:
    stops, first = [], None
    for _ in range(MAX_TABS):
        page.keyboard.press("Tab")
        page.wait_for_timeout(60)
        info = page.evaluate(FOCUS_INFO)
        if info.get("body"):
            if stops:
                break
            continue
        if first is None:
            first = info["stop"]
        elif info["stop"] == first:
            break
        node = ax_node(cdp, "document.activeElement") or {}
        stops.append({**info, **node})
    else:
        return {"complete_cycle": False, "stops": stops}
    return {"complete_cycle": True, "stops": stops}


def summarise_cycle(cycle: dict) -> dict:
    stops = cycle["stops"]
    return {"complete_cycle": cycle["complete_cycle"], "total_stops": len(stops),
            "sidebar_stops": sum(stop["sidebar"] for stop in stops),
            "multiselect_stops": sum(stop["multiselect"] for stop in stops),
            "tabpanel_stops": sum(stop["tabpanel"] for stop in stops),
            "unnamed_stops": [f"{stop.get('role')} <{stop['tag']}> in {stop['testid']}"
                              for stop in stops if not stop.get("name")],
            "sequence": [f"{stop.get('role')}: {stop.get('name', '')[:60]}"
                         + (" [sidebar]" if stop["sidebar"] else "") for stop in stops]}


def grid_probe(page, cdp, expected: list[dict]) -> list[dict]:
    """For each visible table (Glide grid or plain st.table), what reaches the AX tree."""
    count = page.evaluate(f"() => {VISIBLE_TABLES}.length")
    found = []
    for index in range(min(count, len(expected))):
        expression = f"{VISIBLE_TABLES}[{index}]"
        handle = cdp.send("Runtime.evaluate", {"expression": expression})
        object_id = handle["result"]["objectId"]
        roles = {}
        names = set()
        for role in ("grid", "table", "row", "gridcell", "cell", "columnheader", "rowheader"):
            nodes = cdp.send("Accessibility.queryAXTree",
                             {"objectId": object_id, "role": role})["nodes"]
            nodes = [node for node in nodes if not node.get("ignored")]
            roles[role] = len(nodes)
            names.update((node.get("name", {}).get("value") or "").strip() for node in nodes)
        spec = expected[index]
        keys = spec["keys"]
        kind = page.evaluate(f"() => {VISIBLE_TABLES}[{index}].getAttribute('data-testid')")
        found.append({"table": spec["name"], "element": kind, "expected_rows": spec["rows"],
                      "expected_columns": spec["columns"],
                      "expected_cells": spec["rows"] * spec["columns"],
                      "ax_role_counts": roles,
                      "data_cells_exposed": roles["gridcell"] + roles["cell"],
                      "first_key_found": bool(keys) and keys[0] in names,
                      "last_key_found": bool(keys) and keys[-1] in names,
                      "keys_found": sum(key in names for key in keys)})
    return found


def expected_tables(paths) -> dict[str, list[dict]]:
    """Display-order keys and sizes for the default filters (break statuses, all dates)."""
    rec = snapshot_rows(paths.snapshot, "fct_reconciliation")
    findings = snapshot_rows(paths.snapshot, "fct_finding")
    quality = snapshot_rows(paths.snapshot, "fct_quality")
    breaks = sorted((row for row in rec if row["is_break"]),
                    key=lambda row: (row["posted_on"], row["figure_id"]))
    queue = sorted(findings, key=lambda row: (row["posted_on"], row["figure_id"]))
    return {
        "S1": [{"name": "aged-break overview", "rows": 5, "columns": 3, "keys": ["0-2 days"]},
               {"name": "breaks", "rows": len(breaks), "columns": 7,
                "keys": [row["figure_id"] for row in breaks]}],
        "S2": [{"name": "quality controls", "rows": len(quality), "columns": 5, "keys": []}],
        "S3": [{"name": "finding queue", "rows": len(queue), "columns": 7,
                "keys": [row["figure_id"] for row in queue]}],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="axe-core, keyboard and grid probe")
    parser.add_argument("--label", default="after")
    args = parser.parse_args()
    reason = skip_reason()
    if reason:
        print(f"SKIP: {reason}")
        return 0
    from playwright.sync_api import sync_playwright

    out = ROOT / "evidence" / f"a11y_audit_{args.label}.json"
    started = datetime.now(timezone.utc)
    screens, keyboard, grids, plain = {}, {}, {}, {}
    with tempfile.TemporaryDirectory(prefix="finrecon-a11y-") as folder:
        root, logs = Path(folder) / "batches", Path(folder) / "logs"
        logs.mkdir()
        pipeline.run_batch(root, keep=3)
        paths = resolve_published(root)
        ready = f"Published batch {paths.batch_id}"
        expected = expected_tables(paths)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                env = {"FINRECON_BATCH_ROOT": str(root), "FINRECON_ROLE": "reviewer"}
                with streamlit_server(env, logs / "reviewer.log") as (url, _):
                    page = browser.new_page(viewport={"width": 1400, "height": 1000})
                    cdp = page.context.new_cdp_session(page)
                    cdp.send("Accessibility.enable")
                    open_app(page, url, ready)
                    page.wait_for_timeout(1000)
                    screens["S1_reviewer_breaks"] = run_axe(page)
                    grids["S1"] = grid_probe(page, cdp, expected["S1"])
                    open_tab(page, "Quality controls")
                    screens["S2_reviewer_quality"] = run_axe(page)
                    grids["S2"] = grid_probe(page, cdp, expected["S2"])
                    open_tab(page, "Finding queue")
                    screens["S3_reviewer_findings"] = run_axe(page)
                    grids["S3"] = grid_probe(page, cdp, expected["S3"])
                    figure = expected["S1"][1]["keys"][0]
                    select_figure(page, figure)
                    verify_selected(page)
                    screens["S4_reviewer_drill_verified"] = run_axe(page)

                    # Added after the baseline run, when the app gained the option:
                    # the same S1/S3 checks with "Plain tables" switched on.
                    if page.get_by_text(PLAIN).count():
                        open_app(page, url, ready)
                        page.get_by_text(PLAIN).click()
                        page.wait_for_timeout(1500)
                        plain["S1_plain"] = run_axe(page)
                        grids["S1_plain"] = grid_probe(page, cdp, expected["S1"])
                        open_tab(page, "Finding queue")
                        plain["S3_plain"] = run_axe(page)
                        grids["S3_plain"] = grid_probe(page, cdp, expected["S3"])

                    open_app(page, url, ready)
                    page.wait_for_timeout(1000)
                    keyboard["breaks_tab"] = summarise_cycle(tab_cycle(page, cdp))
                    open_app(page, url, ready)
                    page.wait_for_timeout(500)
                    open_tab(page, DRILL_TAB)
                    page.evaluate("() => document.activeElement && document.activeElement.blur()")
                    keyboard["drill_through_tab"] = summarise_cycle(tab_cycle(page, cdp))
                    page.close()
                env = {"FINRECON_BATCH_ROOT": str(root), "FINRECON_ROLE": "summary"}
                with streamlit_server(env, logs / "summary.log") as (url, _):
                    page = browser.new_page(viewport={"width": 1400, "height": 1000})
                    open_app(page, url, ready)
                    open_tab(page, DRILL_TAB)
                    screens["S5_summary_drill"] = run_axe(page)
                    page.close()
                env = {"FINRECON_BATCH_ROOT": str(root), "FINRECON_ROLE": "admin"}
                with streamlit_server(env, logs / "denied.log") as (url, _):
                    page = browser.new_page(viewport={"width": 1400, "height": 1000})
                    open_app(page, url, "Access denied")
                    screens["S6_access_denied"] = run_axe(page)
                    page.close()
                chrome_version = browser.version
            finally:
                browser.close()

    unique = {}
    for screen, result in screens.items():
        for rule in result["violations"]:
            for node in rule["nodes"]:
                unique.setdefault((rule["id"], node["target"]), {
                    "rule": rule["id"], "impact": rule["impact"], "wcag": rule["wcag"],
                    "screens": []})["screens"].append(screen)
    per_screen = {screen: {
        "violated_rules": len(result["violations"]),
        "violation_nodes": sum(rule["node_count"] for rule in result["violations"]),
        "wcag_rules": sorted(rule["id"] for rule in result["violations"] if rule["wcag"]),
        "best_practice_rules": sorted(rule["id"] for rule in result["violations"]
                                      if not rule["wcag"]),
        "incomplete_rules": sorted(rule["id"] for rule in result["incomplete"])}
        for screen, result in screens.items()}
    rules = {}
    for item in unique.values():
        entry = rules.setdefault(item["rule"], {"impact": item["impact"], "wcag": item["wcag"],
                                                "elements": 0})
        entry["elements"] += 1
    evidence = {
        "generated_at": started.isoformat(timespec="seconds"),
        "label": args.label,
        "what": "axe-core scan, keyboard Tab cycle and Glide-grid accessibility-tree probe "
                "of dashboard.py on a disposable synthetic batch",
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "chrome": chrome_version, "streamlit": metadata.version("streamlit"),
                        "playwright": metadata.version("playwright"),
                        "axe_core": AXE.read_text(encoding="utf-8")[:40].split("v", 1)[1]
                        .split()[0], "axe_tags": TAGS},
        "totals": {"unique_violations": len(unique),
                   "unique_wcag_violations": sum(item["wcag"] for item in unique.values()),
                   "unique_best_practice_violations": sum(not item["wcag"]
                                                          for item in unique.values()),
                   "rules": rules},
        "per_screen": per_screen,
        "plain_tables_mode": {screen: {
            "violated_rules": sorted(rule["id"] for rule in result["violations"]),
            "violation_nodes": sum(rule["node_count"] for rule in result["violations"])}
            for screen, result in plain.items()},
        "keyboard": keyboard,
        "grids": grids,
        "axe_raw": screens,
        "axe_raw_plain_tables": plain,
        "limits": ["Automated checks only: axe-core detects a subset of WCAG failures and "
                   "lists others as needing manual review.",
                   "No screen-reader (VoiceOver/NVDA) pass, zoom/reflow check or user test "
                   "was done; these need a person.",
                   "This is not a statement of accessibility or WCAG conformance.",
                   "Synthetic data on a single machine and viewport."],
    }
    out.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"label": args.label, "totals": {key: value for key, value in
                                                      evidence["totals"].items()
                                                      if key != "rules"},
                      "rules": rules, "per_screen": {key: value["violation_nodes"]
                                                     for key, value in per_screen.items()},
                      "plain_tables_mode": evidence["plain_tables_mode"],
                      "keyboard": {key: {k: v for k, v in value.items()
                                         if k in ("total_stops", "sidebar_stops",
                                                  "multiselect_stops", "tabpanel_stops",
                                                  "complete_cycle")}
                                   for key, value in keyboard.items()},
                      "grids": grids}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
