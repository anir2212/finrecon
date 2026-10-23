"""Headless-Chrome walkthrough through the real local login proxy.

Starts the packaged stack in ``ops/auth/`` (Caddy, oauth2-proxy and Dex) as a
disposable Compose project, runs the dashboard on the host bound to 127.0.0.1
in proxy mode, signs in through Dex's login form and repeats the reviewer
journey from ``eval/browser_walkthrough.py`` behind that login.

Checks, fixed before the first run (26 in total):

blocked (6)
  * ``GET /`` without a session is redirected to ``/oauth2/sign_in`` and
    returns no Streamlit page;
  * ``/_stcore/health`` without a session is redirected, not answered;
  * a websocket upgrade to ``/_stcore/stream`` without a session is refused;
  * ``GET /`` carrying the real proxy token, a reviewer role header and a
    reviewer e-mail header, but no session, is still redirected;
  * the app reached directly (bypassing the proxy) with a reviewer role header
    and no token shows "Access denied";
  * a wrong Dex password leaves the browser on the Dex form with no session.
roles (8)
  * ``summary@finrecon.local`` sees role ``summary (summary@finrecon.local)``,
    five metrics, no verify button, no fixture-label toggle and the
    "does not include source" notice (5 checks);
  * signed in as summary while the browser sends ``X-Forwarded-Role: reviewer``,
    reviewer ``X-Forwarded-User``/``X-Auth-Request-Email`` headers and the
    *real* proxy token, the app still shows ``summary`` and no verify button;
  * ``norole@finrecon.local`` signs in, but the app denies access and shows
    no metrics (2 checks).
journey (10)
  * the ten reviewer-journey checks of ``browser_walkthrough``, run on the
    proxy URL after signing in as ``reviewer@finrecon.local`` (the header
    check accepts the user suffix after the role).
websocket (1)
  * the reviewer page opened ``ws://127.0.0.1:<proxy port>/_stcore/stream``
    and received frames through the proxy.
session (1)
  * after ``/oauth2/sign_out`` the next visit lands on the Dex login form.

Writes ``evidence/proxy_walkthrough.json`` and one screenshot. The proxy
token and demo passwords are generated per run in a temporary folder and never
written to evidence. Prints SKIP and exits 0 when Docker, Chrome, Playwright or
Streamlit is missing. Synthetic data, local demo users, plain HTTP on
127.0.0.1: this is a functional check of the login path, not a security audit.
"""

from __future__ import annotations

import http.client
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

import pipeline
from access import TOKEN_HEADER
from batches import resolve_published
from eval.browser_walkthrough import (DRILL_TAB, VERIFY, alerts, body_text, free_port,
                                      metrics, open_app, open_tab, reviewer_journey,
                                      skip_reason as browser_skip_reason,
                                      streamlit_server, wait_text)

ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "ops" / "auth"
RESULT = ROOT / "evidence" / "proxy_walkthrough.json"
SHOT = ROOT / "evidence" / "browser" / "proxy_reviewer.png"
PROJECT = "finrecon-auth-check"
COOKIE = "_finrecon_auth"

sys.path.insert(0, str(AUTH))
import setup_local  # noqa: E402  (ops/auth is not a package)


def skip_reason() -> str | None:
    reason = browser_skip_reason()
    if reason:
        return reason
    if shutil.which("docker") is None:
        return "docker is not installed."
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        return "the Docker daemon is not running."
    return None


def compose(env_file: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", "--env-file", str(env_file), "-f",
                           str(AUTH / "compose.yaml"), "-p", PROJECT, *args],
                          capture_output=True, text=True)


def wait_ready(base: str, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    for path in ("dex/.well-known/openid-configuration", "oauth2/ping"):
        while True:
            try:
                with urllib.request.urlopen(base + path, timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"{base}{path} not ready within {timeout:.0f} s")
            time.sleep(0.5)


def raw_get(port: int, path: str, headers: dict | None = None) -> tuple[int, str, str]:
    """One HTTP request without following redirects or keeping cookies."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path, headers=headers or {})
        response = conn.getresponse()
        body = response.read(4096).decode("utf-8", "replace")
        return response.status, response.getheader("Location") or "", body
    finally:
        conn.close()


def login(context, base: str, email: str, password: str):
    page = context.new_page()
    page.goto(base, wait_until="domcontentloaded")
    page.wait_for_selector("#login", timeout=30000)
    page.fill("#login", email)
    page.fill("#password", password)
    page.click("#submit-login")
    return page


def role_line(text: str) -> str | None:
    return next((line.split("role:", 1)[1].strip() for line in text.splitlines()
                 if "role:" in line), None)


def blocked_checks(browser, base: str, auth_port: int, app_url: str, info: dict,
                   ready: str) -> tuple[dict, dict]:
    observed = {}
    status, location, body = raw_get(auth_port, "/")
    observed["root"] = {"status": status, "location": location}
    root_ok = status == 302 and location.startswith("/oauth2/sign_in") \
        and "streamlit" not in body.lower()
    status, location, _ = raw_get(auth_port, "/_stcore/health")
    observed["health"] = {"status": status, "location": location}
    status, location, _ = raw_get(auth_port, "/_stcore/stream", {
        "Connection": "Upgrade", "Upgrade": "websocket", "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="})
    observed["websocket"] = {"status": status, "location": location}
    ws_ok = status != 101 and location.startswith("/oauth2/sign_in")
    status, location, _ = raw_get(auth_port, "/", {
        "X-Forwarded-Role": "reviewer", TOKEN_HEADER: info["token"],
        "X-Forwarded-User": "reviewer@finrecon.local",
        "X-Auth-Request-Email": "reviewer@finrecon.local"})
    observed["spoofed_no_session"] = {"status": status, "location": location}

    context = browser.new_context(extra_http_headers={
        "X-Forwarded-Role": "reviewer", "X-Forwarded-User": "reviewer@finrecon.local"})
    page = context.new_page()
    open_app(page, app_url, "Access denied")
    direct_denied = "Access denied" in body_text(page) and len(metrics(page)) == 0
    context.close()

    context = browser.new_context()
    page = login(context, base, "reviewer@finrecon.local", "wrong-password")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1000)
    cookies = [cookie["name"] for cookie in context.cookies()]
    observed["wrong_password"] = {"path": page.url.split(str(auth_port), 1)[-1].split("?")[0],
                                  "session_cookie": COOKIE in cookies}
    wrong_ok = "/dex/" in page.url and COOKIE not in cookies \
        and page.locator("#password").count() == 1
    context.close()
    checks = {
        "unauthenticated_root_redirected_to_login": root_ok,
        "unauthenticated_health_redirected": observed["health"]["status"] == 302,
        "unauthenticated_websocket_refused": ws_ok,
        "spoofed_headers_with_real_token_but_no_session_redirected":
            observed["spoofed_no_session"]["status"] == 302,
        "direct_app_without_token_denied": direct_denied,
        "wrong_password_gives_no_session": wrong_ok,
    }
    return checks, observed


def role_checks(browser, base: str, info: dict, ready: str) -> tuple[dict, dict]:
    observed = {}
    passwords = info["passwords"]
    context = browser.new_context(viewport={"width": 1400, "height": 1000})
    page = login(context, base, "summary@finrecon.local", passwords["summary@finrecon.local"])
    wait_text(page, ready, timeout=60000)
    page.wait_for_timeout(500)
    open_tab(page, DRILL_TAB)
    text = body_text(page)
    observed["summary"] = {"role_line": role_line(text),
                           "verify_button": page.get_by_role("button", name=VERIFY).count()}
    summary = {
        "summary_role_shown_with_user": observed["summary"]["role_line"]
        == "summary (summary@finrecon.local)",
        "summary_metrics_visible": len(metrics(page)) == 5,
        "summary_no_verify_button": observed["summary"]["verify_button"] == 0,
        "summary_no_fixture_label_toggle": "Show synthetic fixture labels" not in text,
        "summary_explains_missing_source_access": any(
            "does not include source" in item for item in alerts(page, "info")),
    }
    context.close()

    context = browser.new_context(extra_http_headers={
        "X-Forwarded-Role": "reviewer", TOKEN_HEADER: info["token"],
        "X-Forwarded-User": "reviewer@finrecon.local",
        "X-Auth-Request-Email": "reviewer@finrecon.local"})
    page = login(context, base, "summary@finrecon.local", passwords["summary@finrecon.local"])
    wait_text(page, ready, timeout=60000)
    page.wait_for_timeout(500)
    open_tab(page, DRILL_TAB)
    text = body_text(page)
    observed["summary_spoofing"] = {
        "role_line": role_line(text),
        "verify_button": page.get_by_role("button", name=VERIFY).count()}
    spoof_ok = observed["summary_spoofing"]["role_line"] == "summary (summary@finrecon.local)" \
        and observed["summary_spoofing"]["verify_button"] == 0
    context.close()

    context = browser.new_context()
    page = login(context, base, "norole@finrecon.local", passwords["norole@finrecon.local"])
    wait_text(page, "Access denied", timeout=60000)
    page.wait_for_timeout(500)
    errors = alerts(page, "error")
    observed["norole"] = {"errors": errors}
    norole = {"norole_user_denied_by_app": any("no FinRecon role" in item for item in errors),
              "norole_user_sees_no_metrics": len(metrics(page)) == 0}
    context.close()
    return {**summary, "summary_spoofed_reviewer_headers_ignored": spoof_ok, **norole}, observed


def reviewer_checks(browser, base: str, auth_port: int, info: dict, paths,
                    ready: str) -> dict:
    context = browser.new_context(viewport={"width": 1400, "height": 1000})
    sockets = []

    def on_socket(ws):
        record = {"url": ws.url, "frames": 0}
        ws.on("framereceived", lambda _frame: record.__setitem__("frames", record["frames"] + 1))
        sockets.append(record)

    page = login(context, base, "reviewer@finrecon.local",
                 info["passwords"]["reviewer@finrecon.local"])
    page.on("websocket", on_socket)
    wait_text(page, ready, timeout=60000)
    journey = reviewer_journey(page, base, paths, {"reviewer": SHOT})
    stream = f"ws://127.0.0.1:{auth_port}/_stcore/stream"
    ws_ok = any(item["url"].startswith(stream) and item["frames"] > 0 for item in sockets)

    page.goto(base + "oauth2/sign_out", wait_until="domcontentloaded")
    page.goto(base, wait_until="domcontentloaded")
    try:
        page.wait_for_selector("#login", timeout=30000)
        signed_out = COOKIE not in [cookie["name"] for cookie in context.cookies()]
    except Exception:
        signed_out = False
    context.close()
    return {"journey": journey, "websocket": {
        "websocket_stream_through_proxy_received_frames": ws_ok},
        "session": {"sign_out_returns_to_login_form": signed_out},
        "sockets": [{"url": item["url"].replace(str(auth_port), "<proxy port>"),
                     "frames": item["frames"]} for item in sockets]}


def main() -> int:
    reason = skip_reason()
    if reason:
        print(f"SKIP: {reason}")
        return 0
    from playwright.sync_api import sync_playwright

    started = datetime.now(timezone.utc)
    result, compose_log = {}, ""
    with tempfile.TemporaryDirectory(prefix="finrecon-proxy-") as folder:
        root, logs = Path(folder) / "batches", Path(folder) / "logs"
        logs.mkdir()
        pipeline.run_batch(root, keep=3)
        paths = resolve_published(root)
        ready = f"Published batch {paths.batch_id}"
        auth_port, app_port = free_port(), free_port()
        info = setup_local.write_local(Path(folder) / "auth", auth_port, app_port)
        env_file = info["dir"] / ".env"
        base, app_url = f"http://127.0.0.1:{auth_port}/", f"http://127.0.0.1:{app_port}/"
        app_env = {"FINRECON_BATCH_ROOT": str(root),
                   "FINRECON_ROLE_HEADER": setup_local.ROLE_HEADER,
                   "FINRECON_USER_HEADER": setup_local.USER_HEADER,
                   "FINRECON_PROXY_TOKEN": info["token"],
                   # Must be ignored in proxy mode.
                   "FINRECON_ROLE": "reviewer"}
        up = None
        try:
            with streamlit_server(app_env, logs / "proxied.log", port=app_port) as (_, server):
                up = compose(env_file, "up", "-d")
                if up.returncode != 0:
                    raise RuntimeError(f"docker compose up failed: {up.stderr[-2000:]}")
                wait_ready(base)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                    try:
                        result["blocked"], result["blocked_observed"] = blocked_checks(
                            browser, base, auth_port, app_url, info, ready)
                        result["roles"], result["roles_observed"] = role_checks(
                            browser, base, info, ready)
                        result.update(reviewer_checks(browser, base, auth_port, info,
                                                      paths, ready))
                        chrome_version = browser.version
                    finally:
                        browser.close()
        finally:
            if up is not None:
                compose_log = compose(env_file, "logs", "--no-color").stdout
                (logs / "compose.log").write_text(compose_log)
                down = compose(env_file, "down")
                left = compose(env_file, "ps", "-a", "-q").stdout.strip()
                result["stack_removed"] = down.returncode == 0 and not left
    result["app_server_stopped"] = server["stopped_cleanly"]
    groups = {"blocked": result["blocked"], "roles": result["roles"],
              "journey": result["journey"]["checks"], "websocket": result["websocket"],
              "session": result["session"]}
    checks = {f"{group}.{name}": bool(ok) for group, items in groups.items()
              for name, ok in items.items()}
    images = {}
    for service in ("dex", "oauth2-proxy", "caddy"):
        for line in (AUTH / "compose.yaml").read_text().splitlines():
            if line.strip().startswith("image:") and service.split("-")[0] in line:
                images[service] = line.split("image:", 1)[1].strip()
    leaks = [secret for secret in (info["token"], *info["passwords"].values())
             if secret in json.dumps(result, default=str)]
    evidence = {
        "generated_at": started.isoformat(timespec="seconds"),
        "what": "Headless Chrome through Caddy + oauth2-proxy + Dex (ops/auth) to dashboard.py "
                "in proxy mode, on disposable synthetic batches",
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "chrome": chrome_version, "streamlit": metadata.version("streamlit"),
                        "playwright": metadata.version("playwright"), "images": images},
        "passed": sum(checks.values()), "total": len(checks),
        "secrets_in_evidence": len(leaks),
        "stack_removed": result["stack_removed"],
        "app_server_stopped": result["app_server_stopped"],
        "checks": checks,
        "details": {key: value for key, value in result.items()
                    if key not in ("blocked", "roles", "websocket", "session")},
        "screenshot": str(SHOT.relative_to(ROOT)),
        "limits": ["Local demo identity provider with three static users and random "
                   "per-run passwords; no real directory, MFA or TLS.",
                   "Plain HTTP on 127.0.0.1; cookies are not marked Secure.",
                   "Synthetic fixture data; batch IDs refer to deleted temporary batches.",
                   "Functional check of the login path, not a penetration test or "
                   "security review."],
    }
    if leaks:
        raise RuntimeError("a generated secret reached the evidence record")
    RESULT.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"passed": evidence["passed"], "total": evidence["total"],
                      "failed": [name for name, ok in checks.items() if not ok],
                      "stack_removed": evidence["stack_removed"],
                      "app_server_stopped": evidence["app_server_stopped"]}, indent=2))
    ok = evidence["passed"] == evidence["total"] and evidence["stack_removed"] \
        and evidence["app_server_stopped"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
