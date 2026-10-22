"""Read-only roles for the analyst view and the BI snapshot export.

Two roles exist:

* ``summary``: batch status, figures, quality controls and the finding queue.
  No source-file links, no source rows, no synthetic fixture labels.
* ``reviewer``: everything ``summary`` sees plus the source bridge and
  byte-verified source rows (drill-through).

Streamlit has no authorization of its own, so the role comes from one of two
places, decided by the server's environment and never by the browser alone:

* **Proxy mode** (``FINRECON_ROLE_HEADER`` set): a trusted reverse proxy that
  authenticates the user sets the role header and a shared token header. The
  token must equal ``FINRECON_PROXY_TOKEN``; a request without it is denied,
  so a browser reaching Streamlit directly cannot claim a role.
* **Local mode** (no header configured): ``FINRECON_ROLE`` names the role for a
  single-user local session and defaults to the least-privileged ``summary``.

Any unknown or missing role in proxy mode denies access entirely.
"""

from __future__ import annotations

import hmac
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from batches import atomic_write_json, sha256_file
from reporting import SNAPSHOT_MANIFEST, verify_snapshot


SOURCE_ROWS = "source_rows"
ROLES = {"summary": frozenset({"figures"}),
         "reviewer": frozenset({"figures", SOURCE_ROWS})}
DEFAULT_LOCAL_ROLE = "summary"
TOKEN_HEADER = "X-FinRecon-Proxy-Token"
SOURCE_TABLES = ("bridge_figure_source",)


class AccessDenied(PermissionError):
    """The caller's role does not allow this action."""


@dataclass(frozen=True)
class Access:
    role: str | None
    mode: str
    user: str | None = None
    reason: str | None = None

    @property
    def allowed(self) -> bool:
        return self.role in ROLES

    def can(self, permission: str) -> bool:
        return self.allowed and permission in ROLES[self.role]


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def resolve_access(headers: Mapping[str, str], env: Mapping[str, str]) -> Access:
    """Decide the role for one request from server configuration and headers."""
    role_header = env.get("FINRECON_ROLE_HEADER")
    if role_header:
        expected = env.get("FINRECON_PROXY_TOKEN", "")
        if not expected:
            return Access(None, "proxy", reason="FINRECON_PROXY_TOKEN is not configured")
        supplied = _header(headers, TOKEN_HEADER) or ""
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            return Access(None, "proxy", reason="request did not come through the trusted proxy")
        role = (_header(headers, role_header) or "").strip().lower()
        user_header = env.get("FINRECON_USER_HEADER")
        user = _header(headers, user_header) if user_header else None
        if role not in ROLES:
            return Access(None, "proxy", user, reason=f"no FinRecon role for this user ({role or 'none'})")
        return Access(role, "proxy", user)
    role = env.get("FINRECON_ROLE", DEFAULT_LOCAL_ROLE).strip().lower()
    if role not in ROLES:
        return Access(None, "local", reason=f"unknown FINRECON_ROLE {role!r}")
    return Access(role, "local")


def require(access: Access, permission: str) -> None:
    if not access.can(permission):
        raise AccessDenied(f"role {access.role or 'none'} may not access {permission}")


def export_summary_snapshot(snapshot: Path, out_dir: Path, batch_id: str) -> dict:
    """Copy a verified snapshot without source-link tables, with its own manifest.

    The copy carries no file paths, line numbers or source hashes, so it can be
    handed to a ``summary`` audience (for example a shared Tableau workbook) without
    granting source-row access.
    """
    snapshot, out_dir = Path(snapshot), Path(out_dir)
    verify_snapshot(snapshot, batch_id)
    manifest = json.loads((snapshot / SNAPSHOT_MANIFEST).read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=False)
    kept = {}
    for table, entry in manifest["tables"].items():
        if table in SOURCE_TABLES:
            continue
        for fmt in ("csv", "parquet"):
            shutil.copyfile(snapshot / entry[fmt]["file"], out_dir / entry[fmt]["file"])
            if sha256_file(out_dir / entry[fmt]["file"]) != entry[fmt]["sha256"]:
                raise ValueError(f"copy of {entry[fmt]['file']} does not match its hash")
        kept[table] = entry
    summary = {**{key: value for key, value in manifest.items() if key != "tables"},
               "audience": "summary", "excluded_tables": list(SOURCE_TABLES),
               "source_snapshot_manifest_sha256": sha256_file(snapshot / SNAPSHOT_MANIFEST),
               "tables": kept}
    atomic_write_json(out_dir / SNAPSHOT_MANIFEST, summary)
    verify_snapshot(out_dir, batch_id)
    leaked = sorted(path.name for path in out_dir.iterdir()
                    if path.stem in SOURCE_TABLES)
    if leaked:
        raise ValueError(f"source tables leaked into summary export: {leaked}")
    return summary
