#!/usr/bin/env python3
"""Phase 2F live-test harness for approveCopyDraftAction.

OPERATOR-ONLY. Read-only by default. Pass --apply to actually write the
[copy approval] block exactly as web/lib/actions/copy-draft.ts would.

The script never:
  - calls Seedance / Enhancor / Audio Fixer / any paid API
  - sends an email
  - publishes anything
  - touches `status`, `shared_with_client`, `client_safe_video_url`
  - creates a generation_jobs / prompt_versions / generated_assets /
    audio_fixer_jobs row

Usage:
    py -3.11 scripts/test_copy_approval_action.py \
        --content-item-id 5d11c478-68c0-4ec6-b2a5-62dbefeb9515

    py -3.11 scripts/test_copy_approval_action.py \
        --content-item-id 5d11c478-68c0-4ec6-b2a5-62dbefeb9515 \
        --apply \
        --notes "phase-2F dry-run: copy reviewed by operator"
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

# Mirror the action's marker exactly.
COPY_APPROVAL_MARKER = "\n\n[copy approval]\n"
# Editable statuses set used by the action.
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


def _load_root_env() -> None:
    """Tiny KEY=VALUE .env loader. We avoid a python-dotenv dep."""
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


def _strip_approval_block(prompt_summary: str | None) -> str:
    """Drop any prior [copy approval] block. Same logic as the action's
    stripApprovalBlock helper."""
    if not prompt_summary:
        return ""
    idx = prompt_summary.find(COPY_APPROVAL_MARKER)
    return prompt_summary if idx == -1 else prompt_summary[:idx]


def _build_approval_block(*, approver: str, notes: str | None) -> tuple[str, str]:
    now_iso = (
        _dt.datetime.now(_dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    lines = [
        "copy_approval_status: approved_internal",
        f"copy_approved_at: {now_iso}",
        f"copy_approved_by: {approver}",
    ]
    if notes:
        lines.append(f"copy_approval_notes: {notes}")
    return COPY_APPROVAL_MARKER + "\n".join(lines), now_iso


def _supabase_env() -> tuple[str, str]:
    url = (os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise SystemExit(
            "FATAL: NEXT_PUBLIC_SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not "
            "set in .env."
        )
    return url, key


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
    """Count generation_jobs / prompt_versions / generated_assets /
    audio_fixer_jobs rows tied to this content item BEFORE + AFTER the
    write so we can prove the action did not touch them."""
    url, key = _supabase_env()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Prefer": "count=exact",
        "Accept": "application/json",
    }
    out: dict[str, int] = {}

    def _count(path: str, filter_: str) -> int:
        req = urllib.request.Request(
            f"{url}/rest/v1/{path}?{filter_}&select=id&limit=1",
            headers=headers,
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                cr = resp.headers.get("Content-Range") or ""
                m = re.search(r"/(\d+)$", cr)
                return int(m.group(1)) if m else 0
        except urllib.error.HTTPError as e:
            # Some tables (e.g. audio_fixer_jobs) may not directly carry
            # a content_item_id column; we soft-fail and return -1 so
            # the harness reports the error rather than crash.
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
    # audio_fixer_jobs are scoped to a generation_jobs row, not a
    # content_item directly. We instead count rows tied to any job for
    # this content item.
    req = urllib.request.Request(
        f"{url}/rest/v1/generation_jobs?content_item_id=eq.{content_id}&select=id",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            job_ids = [
                r["id"] for r in json.loads(resp.read().decode("utf-8"))
            ]
    except urllib.error.HTTPError:
        job_ids = []
    if job_ids:
        # PostgREST IN list: `in.(id1,id2)`
        ids = ",".join(job_ids)
        out["audio_fixer_jobs"] = _count(
            "audio_fixer_jobs", f"generation_job_id=in.({ids})"
        )
    else:
        out["audio_fixer_jobs"] = 0
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Phase 2F approve-copy live test.")
    p.add_argument("--content-item-id", required=True)
    p.add_argument("--apply", action="store_true", help="actually write")
    p.add_argument(
        "--approver",
        default="operator",
        help="value for copy_approved_by (default: 'operator')",
    )
    p.add_argument("--notes", default=None)
    args = p.parse_args()

    _load_root_env()

    before = _get_content_item(args.content_item_id)
    if not before:
        print(f"NOT FOUND: {args.content_item_id}")
        return 2

    before_counts = _safety_counts(args.content_item_id)

    print("=== BEFORE ===")
    print(json.dumps({k: before.get(k) for k in [
        "id", "title", "status", "shared_with_client",
        "client_safe_video_url", "client_safe_poster_url",
    ]}, indent=2))
    print(f"caption_draft (chars): {len(before.get('caption_draft') or '')}")
    print(f"prompt_summary (chars): {len(before.get('prompt_summary') or '')}")
    print(f"safety counts: {before_counts}")

    if before["status"] not in EDITABLE_STATUSES:
        print(f"REFUSE: status {before['status']!r} is not editable.")
        return 3
    if not (before.get("caption_draft") or "").strip():
        print("REFUSE: caption_draft is empty — generate copy first.")
        return 4

    base = _strip_approval_block(before.get("prompt_summary") or "")
    block, now_iso = _build_approval_block(approver=args.approver, notes=args.notes)
    new_summary = base + block

    print("\n=== PLANNED PATCH ===")
    print(json.dumps({"prompt_summary": new_summary}, indent=2))

    if not args.apply:
        print("\nDry-run: pass --apply to write.")
        return 0

    patched = _patch_content_item(
        args.content_item_id, {"prompt_summary": new_summary}
    )

    after_counts = _safety_counts(args.content_item_id)
    print("\n=== AFTER ===")
    print(json.dumps({k: patched.get(k) for k in [
        "id", "title", "status", "shared_with_client",
        "client_safe_video_url", "client_safe_poster_url",
    ]}, indent=2))
    print(f"caption_draft (chars): {len(patched.get('caption_draft') or '')}")
    print(f"prompt_summary (chars): {len(patched.get('prompt_summary') or '')}")
    print(f"safety counts: {after_counts}")

    # Safety asserts — fail the script if any unintended field changed.
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
        bad.append("caption_draft changed (this run did not override it)")
    for tbl in ("generation_jobs", "prompt_versions", "generated_assets", "audio_fixer_jobs"):
        if after_counts[tbl] != before_counts[tbl]:
            bad.append(
                f"{tbl} count drifted: {before_counts[tbl]} -> {after_counts[tbl]}"
            )

    print("\n=== SAFETY ===")
    if bad:
        for b in bad:
            print(f"  FAIL: {b}")
        return 5
    print("  OK: only prompt_summary changed; no other write detected.")
    print(f"  copy_approved_at = {now_iso}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
