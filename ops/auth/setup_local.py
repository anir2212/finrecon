"""Write the git-ignored secrets and Dex users for the local login proxy.

Creates, in ``ops/auth/local/`` by default (or ``--out``):

* ``.env``: ports, the shared proxy token, the OIDC client secret and the
  oauth2-proxy cookie secret, read by ``docker compose --env-file``;
* ``app.env``: only what the Streamlit process needs (proxy mode, role and
  user header names, the token and its port), read by ``make dashboard-proxied``;
* ``dex.yaml``: the Dex configuration with bcrypt hashes of the demo users;
* ``demo_users.txt``: the generated demo passwords, for signing in locally.

Every secret and password is random per setup and stays on this machine; the
repository holds only the template. Demo users, one per app role plus one
without a role:

* ``reviewer@finrecon.local`` -> ``reviewer`` (drill-through to source rows);
* ``summary@finrecon.local``  -> ``summary`` (figures only);
* ``norole@finrecon.local``   -> no FinRecon role (signs in, then the app denies).

The e-mail-to-role map lives in ``ops/auth/Caddyfile``.
"""

from __future__ import annotations

import argparse
import base64
import secrets
import shutil
import subprocess
import uuid
from pathlib import Path
from string import Template

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "local"
CADDY_IMAGE = "caddy:2.11.7"
ROLE_HEADER = "X-Forwarded-Role"
USER_HEADER = "X-Forwarded-User"
DEMO_USERS = (("reviewer", "reviewer@finrecon.local", "reviewer"),
              ("summary", "summary@finrecon.local", "summary"),
              ("norole", "norole@finrecon.local", None))


def bcrypt_hash(password: str) -> str:
    """Hash with the system htpasswd (stdin, cost 10), else Caddy's hasher in Docker."""
    if shutil.which("htpasswd"):
        out = subprocess.run(["htpasswd", "-niBC", "10", "user"], input=password + "\n",
                             capture_output=True, text=True, check=True).stdout
        return out.strip().split(":", 1)[1]
    out = subprocess.run(["docker", "run", "--rm", "-i", CADDY_IMAGE, "caddy",
                          "hash-password", "--plaintext", password],
                         capture_output=True, text=True, check=True).stdout
    return out.strip()


def _write_private(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def write_local(out: Path, auth_port: int, app_port: int, *, force: bool = False) -> dict:
    """Render all local files and return the values a check script needs."""
    out = Path(out)
    if (out / ".env").exists() and not force:
        raise FileExistsError(f"{out / '.env'} exists; pass --force to replace it")
    out.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    client_secret = secrets.token_urlsafe(32)
    cookie_secret = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    passwords = {email: secrets.token_urlsafe(12) for _, email, _ in DEMO_USERS}
    users = "\n".join(
        f'  - email: "{email}"\n    username: "{name}"\n'
        f'    userID: "{uuid.uuid5(uuid.NAMESPACE_DNS, email)}"\n'
        f'    hash: "{bcrypt_hash(passwords[email])}"'
        for name, email, _ in DEMO_USERS)
    template = Template((HERE / "dex.yaml.template").read_text(encoding="utf-8"))
    _write_private(out / "dex.yaml", template.substitute(
        auth_port=auth_port, client_secret=client_secret, users=users))
    # Dex runs as a non-root user in its container and must read its config.
    (out / "dex.yaml").chmod(0o644)
    _write_private(out / ".env", "\n".join([
        f"FINRECON_AUTH_PORT={auth_port}", f"FINRECON_APP_PORT={app_port}",
        f"FINRECON_AUTH_LOCAL_DIR={out}", f"FINRECON_PROXY_TOKEN={token}",
        f"OAUTH2_PROXY_CLIENT_SECRET={client_secret}",
        f"OAUTH2_PROXY_COOKIE_SECRET={cookie_secret}", ""]))
    _write_private(out / "app.env", "\n".join([
        f"FINRECON_APP_PORT={app_port}", f"FINRECON_ROLE_HEADER={ROLE_HEADER}",
        f"FINRECON_USER_HEADER={USER_HEADER}", f"FINRECON_PROXY_TOKEN={token}", ""]))
    _write_private(out / "demo_users.txt", "".join(
        f"{email}\t{passwords[email]}\trole={role or 'none'}\n"
        for _, email, role in DEMO_USERS))
    return {"token": token, "passwords": passwords, "auth_port": auth_port,
            "app_port": app_port, "dir": out}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--auth-port", type=int, default=8088)
    parser.add_argument("--app-port", type=int, default=8502)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = write_local(args.out, args.auth_port, args.app_port, force=args.force)
    print(f"Wrote {result['dir']}/.env, app.env, dex.yaml and demo_users.txt "
          f"(sign-in at http://127.0.0.1:{args.auth_port}/; passwords in demo_users.txt).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
