"""Phase 1N — create / update Supabase Auth test users with passwords.

This is an OPERATOR-ONLY admin script. It uses the service-role key
(server-only, read from .env) to:

  1. Look up the existing auth.users row by email (via the GoTrue admin
     `GET /auth/v1/admin/users` endpoint — pages through all users and
     matches client-side).
  2. If the user exists       → PUT a new password + `email_confirm=true`
     so they can sign in immediately (no email round-trip).
     If the user does NOT exist → POST a new user with the same fields.
  3. Ensure the `public.profiles` row exists (the
     `handle_new_user` trigger creates it on first auth.users insert,
     but we double-check via PostgREST so re-running the script is
     idempotent against partially-seeded environments).
  4. Ensure the persona-membership row exists:
       - operator → `public.workspace_members(workspace_id, profile_id,
         role='owner')`
       - client   → `public.client_portal_members(portal_id, profile_id,
         joined_at)`

Hard rules:
  - NEVER sends an email (Supabase admin endpoint with the explicit
    `email_confirm=true` flag short-circuits the confirmation email).
  - NEVER writes the password to any file.
  - Prints the generated password EXACTLY ONCE to stdout at the end of
    the run, in a clearly-marked block — copy it into your password
    manager and never re-run with stdout going to a log file.
  - Refuses to run if SUPABASE_SERVICE_ROLE_KEY isn't in the parent env.
  - Repeated runs are idempotent: re-running just resets the password
    and re-confirms membership rows.

Usage:

    py -3.11 scripts/create_test_auth_users.py
        # both personas, auto-generated passwords

    py -3.11 scripts/create_test_auth_users.py --operator-password "<pw>"
        # operator only, supplied password

    py -3.11 scripts/create_test_auth_users.py --client-password "<pw>"
        # client only, supplied password

    py -3.11 scripts/create_test_auth_users.py --dry-run
        # show what would change without writing anything
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Constants — seeded UUIDs from supabase/seed.sql
# --------------------------------------------------------------------------- #

OPERATOR_EMAIL = "vaneeckhoutmathis2@gmail.com"
CLIENT_EMAIL = "mathis.van.eeckhout@gmail.com"

WORKSPACE_ID = "11111111-1111-1111-1111-111111111111"
PORTAL_ID = "55555555-5555-5555-5555-555555555555"


# --------------------------------------------------------------------------- #
# Env loading (no python-dotenv dependency)
# --------------------------------------------------------------------------- #


def _load_dotenv() -> None:
    """Reads .env + web/.env.local into os.environ for any var not already set."""
    for path in (_REPO_ROOT / ".env", _REPO_ROOT / "web" / ".env.local"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k and k not in os.environ:
                os.environ[k] = v


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(
            f"FATAL: {name} not set in env. Put it in .env or "
            "export it in your shell. Never paste secrets on the CLI."
        )
    return v


# --------------------------------------------------------------------------- #
# Password generation
# --------------------------------------------------------------------------- #


def _generate_password(length: int = 24) -> str:
    """URL-safe 24-char password drawn from ASCII letters + digits.

    Avoids punctuation to keep terminal copy/paste simple across
    Windows / Mac / Linux quoting quirks.
    """
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# --------------------------------------------------------------------------- #
# Supabase HTTP helpers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SupabaseClient:
    url: str
    service_key: str

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> tuple[int, Any]:
        headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"{self.url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                payload: Any = None
                if raw:
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        payload = raw.decode("utf-8", errors="replace")
                return r.status, payload
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                payload = json.loads(raw)
            except Exception:  # noqa: BLE001
                payload = raw.decode("utf-8", errors="replace")
            return e.code, payload

    # ---- auth admin -------------------------------------------------- #

    def find_user_by_email(self, email: str) -> Optional[dict[str, Any]]:
        """Two-layer lookup so this script survives Supabase Auth admin
        endpoint flakiness:

          1. Primary: `GET /auth/v1/admin/users` (paged). Some projects
             return HTTP 500 "Database error finding users" — when that
             happens, log + fall through to the profile fallback.
          2. Fallback: PostgREST query against `public.profiles` matching
             `display_name = split_part(email, '@', 1)` (that's exactly
             what the `handle_new_user` trigger sets in migration 003 on
             first sign-up).

        If the user simply doesn't exist yet, both layers return None and
        the caller proceeds with a fresh POST.
        """
        target = email.strip().lower()
        try:
            page, per_page = 1, 200
            while True:
                status, body = self._request(
                    "GET",
                    f"/auth/v1/admin/users?page={page}&per_page={per_page}",
                )
                if status >= 400:
                    raise RuntimeError(
                        f"GET admin/users page {page} failed: HTTP {status} {body!r}"
                    )
                users = (body or {}).get("users", body) if isinstance(body, dict) else body
                if not isinstance(users, list):
                    raise RuntimeError(f"unexpected admin/users body: {body!r}")
                for u in users:
                    if (u.get("email") or "").lower() == target:
                        return u
                if len(users) < per_page:
                    break
                page += 1
        except RuntimeError as primary_err:
            print(
                f"NOTE: admin GET users failed ({primary_err}). "
                "Falling back to public.profiles lookup by display_name.",
                file=sys.stderr,
            )
        # Fallback — public.profiles lookup.
        localpart = target.split("@", 1)[0]
        status, body = self._request(
            "GET",
            "/rest/v1/profiles"
            f"?select=id,display_name&display_name=eq.{urllib.parse.quote(localpart)}",
        )
        if status >= 400:
            raise RuntimeError(
                f"profile fallback failed: HTTP {status} {body!r}"
            )
        if not isinstance(body, list) or not body:
            return None
        # Synthesize an admin-style row so the caller code stays uniform.
        return {"id": body[0]["id"], "email": email, "_via": "profiles_fallback"}

    def create_user_with_password(
        self, email: str, password: str, display_name: str
    ) -> dict[str, Any]:
        status, body = self._request(
            "POST",
            "/auth/v1/admin/users",
            body={
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"display_name": display_name},
            },
        )
        if status >= 400:
            raise RuntimeError(f"create user failed: HTTP {status} {body!r}")
        if not isinstance(body, dict):
            raise RuntimeError(f"create user returned unexpected body: {body!r}")
        return body

    def update_user_password(
        self, user_id: str, password: str
    ) -> dict[str, Any]:
        status, body = self._request(
            "PUT",
            f"/auth/v1/admin/users/{user_id}",
            body={"password": password, "email_confirm": True},
        )
        if status >= 400:
            raise RuntimeError(f"update user failed: HTTP {status} {body!r}")
        if not isinstance(body, dict):
            raise RuntimeError(f"update user returned unexpected body: {body!r}")
        return body

    # ---- public schema via PostgREST --------------------------------- #

    def upsert_profile(self, profile_id: str, display_name: str) -> None:
        # ON CONFLICT (id) DO UPDATE display_name — safe to re-run.
        status, body = self._request(
            "POST",
            "/rest/v1/profiles",
            body={"id": profile_id, "display_name": display_name},
            extra_headers={
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        if status >= 400:
            raise RuntimeError(f"upsert profile failed: HTTP {status} {body!r}")

    def upsert_workspace_member(self, workspace_id: str, profile_id: str) -> None:
        status, body = self._request(
            "POST",
            "/rest/v1/workspace_members",
            body={
                "workspace_id": workspace_id,
                "profile_id": profile_id,
                "role": "owner",
            },
            extra_headers={
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        if status >= 400:
            raise RuntimeError(
                f"upsert workspace_member failed: HTTP {status} {body!r}"
            )

    def upsert_portal_member(
        self, portal_id: str, profile_id: str, invite_email: str
    ) -> None:
        # client_portal_members PK is (portal_id, coalesce(profile_id,
        # invite_email)) — feed both so the merge updates the right row
        # regardless of the seed state.
        status, body = self._request(
            "POST",
            "/rest/v1/client_portal_members",
            body={
                "portal_id": portal_id,
                "profile_id": profile_id,
                "invite_email": invite_email,
                "joined_at": "now()",
            },
            extra_headers={
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        if status >= 400:
            # 409 here typically means the PK collides with a row that has
            # a NULL profile_id — fall back to an explicit PATCH that
            # locks the invite_email row and stamps profile_id + joined_at.
            patch_status, patch_body = self._request(
                "PATCH",
                f"/rest/v1/client_portal_members?portal_id=eq.{portal_id}"
                f"&invite_email=eq.{urllib.parse.quote(invite_email)}",
                body={"profile_id": profile_id, "joined_at": "now()"},
                extra_headers={"Prefer": "return=minimal"},
            )
            if patch_status >= 400:
                raise RuntimeError(
                    "upsert portal_member failed: "
                    f"POST {status} {body!r}; PATCH {patch_status} {patch_body!r}"
                )


# --------------------------------------------------------------------------- #
# Workflow
# --------------------------------------------------------------------------- #


@dataclass
class ResetReport:
    email: str
    persona: str
    user_id: str
    action: str  # "created" | "updated"
    password: str  # only printed at end of run
    membership: str  # "linked" | "already linked"


def _set_password_for(
    sb: SupabaseClient,
    email: str,
    password: str,
    display_name: str,
    *,
    dry_run: bool,
) -> tuple[str, str]:
    """Returns (user_id, action) where action is 'created' | 'updated'."""
    existing = sb.find_user_by_email(email) if not dry_run else None
    if existing:
        if dry_run:
            return ("<existing-id>", "updated (dry-run)")
        updated = sb.update_user_password(existing["id"], password)
        return (updated["id"], "updated")
    if dry_run:
        return ("<new-id>", "created (dry-run)")
    created = sb.create_user_with_password(email, password, display_name)
    return (created["id"], "created")


def _reset_operator(
    sb: SupabaseClient, password: str, dry_run: bool
) -> ResetReport:
    user_id, action = _set_password_for(
        sb,
        OPERATOR_EMAIL,
        password,
        "vaneeckhoutmathis2",
        dry_run=dry_run,
    )
    if not dry_run:
        sb.upsert_profile(user_id, "vaneeckhoutmathis2")
        sb.upsert_workspace_member(WORKSPACE_ID, user_id)
    return ResetReport(
        email=OPERATOR_EMAIL,
        persona="operator",
        user_id=user_id,
        action=action,
        password=password,
        membership="linked"
        if not dry_run
        else "would link to workspace 11111111-…",
    )


def _reset_client(
    sb: SupabaseClient, password: str, dry_run: bool
) -> ResetReport:
    user_id, action = _set_password_for(
        sb,
        CLIENT_EMAIL,
        password,
        "mathis.van.eeckhout",
        dry_run=dry_run,
    )
    if not dry_run:
        sb.upsert_profile(user_id, "mathis.van.eeckhout")
        sb.upsert_portal_member(PORTAL_ID, user_id, CLIENT_EMAIL)
    return ResetReport(
        email=CLIENT_EMAIL,
        persona="client",
        user_id=user_id,
        action=action,
        password=password,
        membership="linked"
        if not dry_run
        else f"would link to portal {PORTAL_ID}",
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="create_test_auth_users",
        description=(
            "Create or reset Supabase Auth passwords for the two Phase 1L "
            "test personas. No emails sent. Service-role only. "
            "Prints passwords ONCE."
        ),
    )
    ap.add_argument(
        "--operator-password",
        default=None,
        help="Use this password instead of generating one for the operator.",
    )
    ap.add_argument(
        "--client-password",
        default=None,
        help="Use this password instead of generating one for the client.",
    )
    ap.add_argument(
        "--operator-only",
        action="store_true",
        help="Only reset the operator account.",
    )
    ap.add_argument(
        "--client-only",
        action="store_true",
        help="Only reset the client account.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned actions without writing to Supabase.",
    )
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)

    if args.operator_only and args.client_only:
        ap.error("--operator-only and --client-only are mutually exclusive.")

    _load_dotenv()
    url = _require_env("NEXT_PUBLIC_SUPABASE_URL").rstrip("/")
    service_key = _require_env("SUPABASE_SERVICE_ROLE_KEY")
    sb = SupabaseClient(url=url, service_key=service_key)

    do_operator = not args.client_only
    do_client = not args.operator_only

    reports: list[ResetReport] = []
    if do_operator:
        op_password = args.operator_password or _generate_password()
        reports.append(_reset_operator(sb, op_password, args.dry_run))
    if do_client:
        cl_password = args.client_password or _generate_password()
        reports.append(_reset_client(sb, cl_password, args.dry_run))

    # Action summary (no passwords here).
    print("\n--- account state ---")
    for r in reports:
        print(
            f"  [{r.persona:<8}] {r.email:<35} "
            f"id={r.user_id}  action={r.action}  membership={r.membership}"
        )

    # Passwords block — separated by a clear marker.
    print("\n" + "=" * 70)
    print("PASSWORDS (copy NOW — they will NOT be printed again)")
    print("=" * 70)
    for r in reports:
        print(f"  {r.persona:<8} {r.email}")
        print(f"           {r.password}")
        print()
    print("=" * 70)
    print(
        "Each password is set on Supabase Auth with email_confirm=true. "
        "No confirmation email was sent. Re-run this script any time to "
        "rotate."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
