#!/usr/bin/env python3
"""Phase 2G live-test harness for prepareClientCopyPreviewAction +
shareCopyPreviewWithClientAction.

OPERATOR-ONLY. Mirrors what the TypeScript server actions do (same
PostgREST patches, same provenance-block format, same safety
invariants). Read-only by default. Pass --apply to actually write.

Modes:
    py -3.11 scripts/test_client_preview_actions.py \
        --content-item-id 5d11c478-68c0-4ec6-b2a5-62dbefeb9515 \
        --mode prepare

    py -3.11 scripts/test_client_preview_actions.py \
        --content-item-id 5d11c478-68c0-4ec6-b2a5-62dbefeb9515 \
        --mode prepare --apply --notes "Phase 2G live: prepared"

    py -3.11 scripts/test_client_preview_actions.py \
        --content-item-id 5d11c478-68c0-4ec6-b2a5-62dbefeb9515 \
        --mode share --apply --notes "Phase 2G live: shared"

This script never:
  - calls Seedance / Enhancor / Audio Fixer / any paid API
  - sends an email
  - publishes anything
  - touches `client_safe_video_url`
  - creates a generation_jobs / prompt_versions / generated_assets /
    audio_fixer_jobs row
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CLIENT_PREVIEW_MARKER = "\n\n[client copy preview]\n"
EDITABLE_STATUSES = {
    "draft",
    "generating",
    "raw_ready",
    "audio_fixer_pending",
    "audio_fixed",
    "ready_for_client_review",
    "changes_requested_by_client",
    "failed",
}
SHARE_TOKEN = "SHARE COPY"


def _load_env() -> None:
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def _supabase_env() -> tuple[str, str]:
    url = (os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise SystemExit(
            "FATAL: NEXT_PUBLIC_SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are "
            "not set in .env."
        )
    return url, key


def _strip_client_preview_block(prompt_summary: str | None) -> str:
    if not prompt_summary:
        return ""
    idx = prompt_summary.find(CLIENT_PREVIEW_MARKER)
    return prompt_summary if idx == -1 else prompt_summary[:idx]


def _parse_copy_approval_status(prompt_summary: str | None) -> str | None:
    if not prompt_summary:
        return None
    # The marker `\n\n[copy approval]\n` already consumes the trailing
    # newline; an additional `(?:^|\n)` before the key would never match
    # because the regex engine is positioned at the first char of
    # `copy_approval_status:` after the marker, not at a `\n` or start
    # of input. Mirrors the (fixed) TS parser in
    # web/lib/data/owner-overview.ts and web/lib/actions/copy-draft.ts.
    m = re.search(
        r"\n\n\[copy approval\]\n[\s\S]*?copy_approval_status:\s*"
        r"([a-z_]+)",
        prompt_summary,
        re.IGNORECASE,
    )
    return m.group(1) if m else None


def _now_iso() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _get_content_item(content_id: str) -> dict[str, Any] | None:
    url, key = _supabase_env()
    cols = ",".join(
        [
            "id",
            "campaign_id",
            "title",
            "status",
            "shared_with_client",
            "client_safe_video_url",
            "client_safe_poster_url",
            "client_safe_copy_preview",
            "caption_draft",
            "prompt_summary",
        ]
    )
    q = urllib.parse.urlencode({"id": f"eq.{content_id}", "select": cols})
    req = urllib.request.Request(
        f"{url}/rest/v1/content_items?{q}",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        rows = json.loads(resp.read().decode("utf-8"))
    return rows[0] if rows else None


def _patch_content_item(content_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    url, key = _supabase_env()
    body = json.dumps(patch).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/rest/v1/content_items?id=eq.{content_id}",
        data=body,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": "return=representation",
        },
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        rows = json.loads(resp.read().decode("utf-8"))
    if not rows:
        raise RuntimeError("PATCH returned no rows.")
    return rows[0]


def _safety_counts(content_id: str) -> dict[str, int]:
    url, key = _supabase_env()
    headers_count = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Prefer": "count=exact",
        "Accept": "application/json",
    }
    headers_get = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    out: dict[str, int] = {}

    def _count(path: str, filter_: str) -> int:
        req = urllib.request.Request(
            f"{url}/rest/v1/{path}?{filter_}&select=id&limit=1",
            headers=headers_count,
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                cr = resp.headers.get("Content-Range") or ""
                m = re.search(r"/(\d+)$", cr)
                return int(m.group(1)) if m else 0
        except urllib.error.HTTPError as e:
            sys.stderr.write(
                f"[warn] count {path} {filter_}: HTTP {e.code} {e.read()[:200]!r}\n"
            )
            return -1

    out["generation_jobs"] = _count(
        "generation_jobs", f"content_item_id=eq.{content_id}"
    )
    out["prompt_versions"] = _count(
        "prompt_versions", f"content_item_id=eq.{content_id}"
    )
    out["generated_assets"] = _count(
        "generated_assets", f"content_item_id=eq.{content_id}"
    )
    req = urllib.request.Request(
        f"{url}/rest/v1/generation_jobs?content_item_id=eq.{content_id}&select=id",
        headers=headers_get,
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            job_ids = [r["id"] for r in json.loads(resp.read().decode("utf-8"))]
    except urllib.error.HTTPError:
        job_ids = []
    if job_ids:
        ids = ",".join(job_ids)
        out["audio_fixer_jobs"] = _count(
            "audio_fixer_jobs", f"generation_job_id=in.({ids})"
        )
    else:
        out["audio_fixer_jobs"] = 0
    return out


def _client_view_for(content_id: str) -> dict[str, Any] | None:
    """SELECT the row from client_content_items_v — proves what a client
    would see. Returns the row dict (with the view's projected columns
    only) or None when not visible."""
    url, key = _supabase_env()
    q = urllib.parse.urlencode({"id": f"eq.{content_id}", "select": "*"})
    req = urllib.request.Request(
        f"{url}/rest/v1/client_content_items_v?{q}",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        rows = json.loads(resp.read().decode("utf-8"))
    return rows[0] if rows else None


# --------------------------------------------------------------------------- #
# Mode A — prepare client preview
# --------------------------------------------------------------------------- #


def run_prepare(args: argparse.Namespace) -> int:
    before = _get_content_item(args.content_item_id)
    if not before:
        print(f"NOT FOUND: {args.content_item_id}")
        return 2

    before_counts = _safety_counts(args.content_item_id)

    print("=== BEFORE (prepare) ===")
    print(
        json.dumps(
            {
                k: before.get(k)
                for k in [
                    "id",
                    "title",
                    "status",
                    "shared_with_client",
                    "client_safe_video_url",
                    "client_safe_poster_url",
                    "client_safe_copy_preview",
                ]
            },
            indent=2,
        )
    )
    print(f"caption_draft (chars): {len(before.get('caption_draft') or '')}")
    print(f"prompt_summary (chars): {len(before.get('prompt_summary') or '')}")
    print(f"safety counts: {before_counts}")

    if before["status"] not in EDITABLE_STATUSES:
        print(f"REFUSE: status {before['status']!r} is not editable.")
        return 3
    if _parse_copy_approval_status(before.get("prompt_summary")) != "approved_internal":
        print("REFUSE: copy is not approved internally.")
        return 4

    preview = (
        args.preview_text
        if args.preview_text
        else (before.get("caption_draft") or "")
    )
    if not preview.strip():
        print("REFUSE: no preview text + caption_draft empty.")
        return 5

    now_iso = _now_iso()
    base = _strip_client_preview_block(before.get("prompt_summary") or "")
    block = CLIENT_PREVIEW_MARKER + "\n".join(
        [
            "client_copy_preview_status: prepared",
            f"client_copy_preview_prepared_at: {now_iso}",
        ]
        + (
            [f"client_copy_preview_operator_note: {args.notes}"]
            if args.notes
            else []
        )
    )
    new_summary = base + block

    patch: dict[str, Any] = {
        "client_safe_copy_preview": preview,
        "prompt_summary": new_summary,
    }

    print("\n=== PLANNED PATCH ===")
    print(json.dumps({k: (len(v) if isinstance(v, str) else v) for k, v in patch.items()}, indent=2))

    if not args.apply:
        print("\nDry-run. Pass --apply to write.")
        return 0

    patched = _patch_content_item(args.content_item_id, patch)
    after_counts = _safety_counts(args.content_item_id)

    print("\n=== AFTER (prepare) ===")
    print(
        json.dumps(
            {
                k: patched.get(k)
                for k in [
                    "id",
                    "title",
                    "status",
                    "shared_with_client",
                    "client_safe_video_url",
                    "client_safe_poster_url",
                ]
            },
            indent=2,
        )
    )
    cscp = patched.get("client_safe_copy_preview") or ""
    print(f"client_safe_copy_preview (chars): {len(cscp)}")
    print(f"prompt_summary (chars): {len(patched.get('prompt_summary') or '')}")
    print(f"safety counts: {after_counts}")

    bad: list[str] = []
    if patched["status"] != before["status"]:
        bad.append(f"status changed: {before['status']} -> {patched['status']}")
    if patched["shared_with_client"] != before["shared_with_client"]:
        bad.append("shared_with_client changed")
    if patched["client_safe_video_url"] != before["client_safe_video_url"]:
        bad.append("client_safe_video_url changed")
    if patched["client_safe_poster_url"] != before["client_safe_poster_url"]:
        bad.append("client_safe_poster_url changed")
    if patched["caption_draft"] != before["caption_draft"]:
        bad.append("caption_draft changed (prepare should not edit it)")
    for tbl in (
        "generation_jobs",
        "prompt_versions",
        "generated_assets",
        "audio_fixer_jobs",
    ):
        if after_counts[tbl] != before_counts[tbl]:
            bad.append(
                f"{tbl} count drifted: {before_counts[tbl]} -> {after_counts[tbl]}"
            )

    print("\n=== SAFETY ===")
    if bad:
        for b in bad:
            print(f"  FAIL: {b}")
        return 6
    print(
        "  OK: only client_safe_copy_preview + prompt_summary changed; "
        "no other write detected."
    )
    print(f"  client_copy_preview_prepared_at = {now_iso}")
    return 0


# --------------------------------------------------------------------------- #
# Mode B — share preview with client
# --------------------------------------------------------------------------- #


def run_share(args: argparse.Namespace) -> int:
    before = _get_content_item(args.content_item_id)
    if not before:
        print(f"NOT FOUND: {args.content_item_id}")
        return 2

    before_counts = _safety_counts(args.content_item_id)
    before_view = _client_view_for(args.content_item_id)

    print("=== BEFORE (share) ===")
    print(
        json.dumps(
            {
                k: before.get(k)
                for k in [
                    "id",
                    "status",
                    "shared_with_client",
                    "client_safe_video_url",
                    "client_safe_poster_url",
                ]
            },
            indent=2,
        )
    )
    cscp = before.get("client_safe_copy_preview") or ""
    print(f"client_safe_copy_preview (chars): {len(cscp)}")
    print(f"prompt_summary (chars): {len(before.get('prompt_summary') or '')}")
    print(f"safety counts: {before_counts}")
    print(f"client_content_items_v sees row pre-share: {before_view is not None}")

    if args.confirmation_token != SHARE_TOKEN:
        print(f"REFUSE: confirmation token must be exactly {SHARE_TOKEN!r}.")
        return 7
    if before["status"] not in EDITABLE_STATUSES:
        print(f"REFUSE: status {before['status']!r} is not editable.")
        return 3
    if not cscp.strip():
        print("REFUSE: client_safe_copy_preview is empty — prepare first.")
        return 8
    if _parse_copy_approval_status(before.get("prompt_summary")) != "approved_internal":
        print("REFUSE: copy is not approved internally.")
        return 4

    now_iso = _now_iso()
    base = _strip_client_preview_block(before.get("prompt_summary") or "")
    block = CLIENT_PREVIEW_MARKER + "\n".join(
        [
            "client_copy_preview_status: shared_with_client",
            f"client_copy_preview_shared_at: {now_iso}",
        ]
        + (
            [f"client_copy_preview_share_note: {args.notes}"]
            if args.notes
            else []
        )
    )
    new_summary = base + block

    patch: dict[str, Any] = {
        "status": "shared_with_client",
        "shared_with_client": True,
        "prompt_summary": new_summary,
    }

    print("\n=== PLANNED PATCH ===")
    print(json.dumps({k: (len(v) if isinstance(v, str) else v) for k, v in patch.items()}, indent=2))

    if not args.apply:
        print("\nDry-run. Pass --apply to write.")
        return 0

    patched = _patch_content_item(args.content_item_id, patch)
    after_counts = _safety_counts(args.content_item_id)
    after_view = _client_view_for(args.content_item_id)

    print("\n=== AFTER (share) ===")
    print(
        json.dumps(
            {
                k: patched.get(k)
                for k in [
                    "id",
                    "status",
                    "shared_with_client",
                    "client_safe_video_url",
                    "client_safe_poster_url",
                ]
            },
            indent=2,
        )
    )
    print(f"client_safe_copy_preview (chars): {len(patched.get('client_safe_copy_preview') or '')}")
    print(f"prompt_summary (chars): {len(patched.get('prompt_summary') or '')}")
    print(f"safety counts: {after_counts}")

    print("\n=== CLIENT VIEW (client_content_items_v) ===")
    if after_view is None:
        print("  FAIL: view does not return the row after share.")
        return 9
    keys = sorted(after_view.keys())
    print(f"  projected columns ({len(keys)}): {keys}")
    forbidden = {"prompt_summary", "cost_estimate_credits", "cost_actual_credits",
                 "internal_raw_path", "internal_audio_fixed_path",
                 "internal_thumb_path", "quality_tier", "audio_fixer_triggered",
                 "audio_fixer_completed", "audio_fixer_credits_actual"}
    leaks = sorted(forbidden & set(keys))
    if leaks:
        print(f"  FAIL: forbidden columns leaked through view: {leaks}")
        return 10
    print("  OK: no forbidden columns in client view.")
    print(f"  client_safe_copy_preview projected: {'client_safe_copy_preview' in keys}")
    print(f"  client_safe_copy_preview value (chars): {len(after_view.get('client_safe_copy_preview') or '')}")

    bad: list[str] = []
    if patched["status"] != "shared_with_client":
        bad.append(f"status not shared_with_client: {patched['status']}")
    if not patched["shared_with_client"]:
        bad.append("shared_with_client not true")
    if patched["client_safe_video_url"] != before["client_safe_video_url"]:
        bad.append("client_safe_video_url changed (must not)")
    if patched["client_safe_poster_url"] != before["client_safe_poster_url"]:
        bad.append("client_safe_poster_url changed (must not)")
    if patched["client_safe_copy_preview"] != before["client_safe_copy_preview"]:
        bad.append("client_safe_copy_preview changed (must not — already on file)")
    if patched["caption_draft"] != before["caption_draft"]:
        bad.append("caption_draft changed (must not)")
    for tbl in (
        "generation_jobs",
        "prompt_versions",
        "generated_assets",
        "audio_fixer_jobs",
    ):
        if after_counts[tbl] != before_counts[tbl]:
            bad.append(
                f"{tbl} count drifted: {before_counts[tbl]} -> {after_counts[tbl]}"
            )

    print("\n=== SAFETY ===")
    if bad:
        for b in bad:
            print(f"  FAIL: {b}")
        return 11
    print(
        "  OK: only status + shared_with_client + prompt_summary changed; "
        "no other write detected."
    )
    print(f"  client_copy_preview_shared_at = {now_iso}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Phase 2G live test.")
    p.add_argument("--content-item-id", required=True)
    p.add_argument(
        "--mode",
        required=True,
        choices=["prepare", "share"],
    )
    p.add_argument("--apply", action="store_true")
    p.add_argument("--notes", default=None)
    p.add_argument(
        "--preview-text",
        default=None,
        help="Override preview text (prepare mode). Defaults to current "
        "caption_draft if omitted.",
    )
    p.add_argument(
        "--confirmation-token",
        default=SHARE_TOKEN,
        help='Must equal "SHARE COPY" for share mode.',
    )
    args = p.parse_args()
    _load_env()
    if args.mode == "prepare":
        return run_prepare(args)
    if args.mode == "share":
        return run_share(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
