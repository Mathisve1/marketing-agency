#!/usr/bin/env python3
"""Phase 4F live test harness for saveCreativePreviewQAAction.

OPERATOR-ONLY. Read-only by default. Pass --apply to actually write a
`[creative preview QA]` block to prompt_summary, then restore to the
pre-test state.

Modes:
    py -3.11 scripts/test_creative_preview_qa.py \
        --content-item-id b920e5e2-a67d-45ca-96c9-f9422218d675

    py -3.11 scripts/test_creative_preview_qa.py \
        --content-item-id b920e5e2-a67d-45ca-96c9-f9422218d675 --apply

Never:
  - calls Seedance / Enhancor / Audio Fixer / OpenAI / Anthropic / any
    image-gen / paid API
  - inserts into generated_assets / generation_jobs / prompt_versions /
    audio_fixer_jobs
  - touches caption_draft / status / shared_with_client /
    client_safe_video_url / client_safe_poster_url /
    client_safe_copy_preview
  - applies any DDL migration

The --apply path:
  1. snapshots prompt_summary
  2. PATCHes prompt_summary with the QA block
  3. snapshots the row + side-effect tables
  4. PATCHes prompt_summary back to the pre-test value
  5. verifies the final state matches the pre-test snapshot byte-for-byte
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

QA_MARKER = "\n\n[creative preview QA]\n"
ALLOWED_ITEM_IDS = {
    "text_readable",
    "cta_visible",
    "layout_fits",
    "no_forbidden_text",
    "no_internal_notes_visible",
    "brand_tone_ok",
    "claim_safe",
    "ready_for_export_later",
}


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
            "FATAL: NEXT_PUBLIC_SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY "
            "are not set in .env."
        )
    return url, key


def _get(path: str, query: str) -> Any:
    url, key = _supabase_env()
    req = urllib.request.Request(
        f"{url}/rest/v1/{path}?{query}",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
    with urllib.request.urlopen(req, timeout=30) as resp:
        rows = json.loads(resp.read().decode("utf-8"))
    if not rows:
        raise RuntimeError("PATCH returned no rows.")
    return rows[0]


def _safety_counts(content_id: str) -> dict[str, int]:
    """Pull counts on every side-effect table tied to this content item."""
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
            sys.stderr.write(
                f"[warn] count {path} {filter_}: HTTP {e.code} "
                f"{e.read()[:200]!r}\n"
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
    out["content_feedback"] = _count(
        "content_feedback", f"content_item_id=eq.{content_id}"
    )
    out["content_approvals"] = _count(
        "content_approvals", f"content_item_id=eq.{content_id}"
    )
    out["regeneration_requests"] = _count(
        "regeneration_requests", f"content_item_id=eq.{content_id}"
    )
    # audio_fixer_jobs are tied to a generation_jobs row, not a content
    # item directly. Count rows tied to ANY job for this content item.
    try:
        rows = _get(
            "generation_jobs",
            urllib.parse.urlencode({
                "content_item_id": f"eq.{content_id}",
                "select": "id",
            }),
        )
        if rows:
            ids = ",".join(r["id"] for r in rows)
            out["audio_fixer_jobs"] = _count(
                "audio_fixer_jobs", f"generation_job_id=in.({ids})"
            )
        else:
            out["audio_fixer_jobs"] = 0
    except urllib.error.HTTPError:
        out["audio_fixer_jobs"] = -1
    # agent_runs is filtered by content_item_id when present.
    out["agent_runs"] = _count(
        "agent_runs", f"content_item_id=eq.{content_id}"
    )
    # claude_code_tasks (if table exists).
    try:
        out["claude_code_tasks"] = _count(
            "claude_code_tasks", f"content_item_id=eq.{content_id}"
        )
    except Exception:
        out["claude_code_tasks"] = -1
    return out


def _strip_qa_block(prompt_summary: str | None) -> str:
    if not prompt_summary:
        return ""
    idx = prompt_summary.find(QA_MARKER)
    return prompt_summary if idx == -1 else prompt_summary[:idx]


def _row_hash(row: dict[str, Any], skip: tuple[str, ...] = ()) -> str:
    payload = {k: v for k, v in row.items() if k not in skip}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _now_iso() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Phase 4F QA isolated test.")
    p.add_argument("--content-item-id", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    _load_env()

    cid = args.content_item_id

    # ---- BEFORE ----
    pre_rows = _get(
        "content_items",
        urllib.parse.urlencode({
            "id": f"eq.{cid}",
            "select": "*",
        }),
    )
    if not pre_rows:
        print(f"NOT FOUND: {cid}")
        return 2
    pre_row = pre_rows[0]
    pre_counts = _safety_counts(cid)
    pre_hash = _row_hash(pre_row, skip=("updated_at",))

    print("=== BEFORE ===")
    print(f"id: {pre_row['id']}")
    print(f"title: {pre_row.get('title')!r}")
    print(f"status: {pre_row.get('status')!r}")
    print(f"shared_with_client: {pre_row.get('shared_with_client')}")
    print(f"client_safe_video_url: {pre_row.get('client_safe_video_url')!r}")
    print(f"client_safe_poster_url: {pre_row.get('client_safe_poster_url')!r}")
    print(
        f"client_safe_copy_preview chars: "
        f"{len(pre_row.get('client_safe_copy_preview') or '')}"
    )
    print(f"caption_draft chars: {len(pre_row.get('caption_draft') or '')}")
    print(
        f"prompt_summary chars: {len(pre_row.get('prompt_summary') or '')}"
    )
    print(f"row hash (sans updated_at): {pre_hash}")
    print(f"safety counts: {pre_counts}")

    if not args.apply:
        print("\nRead-only mode complete. Pass --apply to do isolated QA save.")
        return 0

    pre_prompt_summary = pre_row.get("prompt_summary") or ""

    # ---- APPLY (isolated QA save + restore) ----
    base = _strip_qa_block(pre_prompt_summary)
    now_iso = _now_iso()
    items_line = ",".join([f"{k}=pass" for k in sorted(ALLOWED_ITEM_IDS)])
    qa_block = QA_MARKER + "\n".join([
        "qa_status: passed",
        f"qa_checked_at: {now_iso}",
        f"qa_items: {items_line}",
    ])
    new_summary = base + qa_block

    print("\n=== APPLY QA ===")
    print(f"qa block bytes: {len(qa_block)}")
    print(f"new prompt_summary chars: {len(new_summary)}")
    _patch_content_item(cid, {"prompt_summary": new_summary})

    # Snapshot mid-test
    mid_rows = _get(
        "content_items",
        urllib.parse.urlencode({"id": f"eq.{cid}", "select": "*"}),
    )
    mid_row = mid_rows[0]
    mid_counts = _safety_counts(cid)

    print("\n=== MID-TEST (QA saved) ===")
    print(
        f"prompt_summary chars: {len(mid_row.get('prompt_summary') or '')}"
    )
    print(f"safety counts: {mid_counts}")

    # Verify only prompt_summary changed
    bad: list[str] = []
    for col in (
        "status",
        "shared_with_client",
        "client_safe_video_url",
        "client_safe_poster_url",
        "client_safe_copy_preview",
        "caption_draft",
        "title",
        "campaign_id",
        "scheduled_for",
        "platforms",
        "quality_tier",
        "resolution",
        "duration_sec",
        "cost_estimate_credits",
        "cost_actual_credits",
        "internal_raw_path",
        "internal_audio_fixed_path",
        "internal_thumb_path",
        "audio_fixer_triggered",
        "audio_fixer_completed",
        "audio_fixer_credits_actual",
    ):
        if pre_row.get(col) != mid_row.get(col):
            bad.append(f"  FAIL: {col} changed in mid-test")
    if "[creative preview QA]" not in (mid_row.get("prompt_summary") or ""):
        bad.append("  FAIL: QA block not present in mid-test prompt_summary")
    for tbl in (
        "generation_jobs",
        "prompt_versions",
        "generated_assets",
        "audio_fixer_jobs",
        "content_feedback",
        "content_approvals",
        "regeneration_requests",
        "agent_runs",
        "claude_code_tasks",
    ):
        if mid_counts.get(tbl, -1) != pre_counts.get(tbl, -1):
            bad.append(
                f"  FAIL: {tbl} count drifted "
                f"{pre_counts.get(tbl)} -> {mid_counts.get(tbl)}"
            )
    if bad:
        print("\nMID-TEST SAFETY FAILED:")
        for b in bad:
            print(b)
    else:
        print(
            "\nMID-TEST SAFETY OK: only prompt_summary changed; no other "
            "row/column/table touched."
        )

    # ---- RESTORE ----
    print("\n=== RESTORE ===")
    _patch_content_item(
        cid, {"prompt_summary": pre_prompt_summary or None}
    )

    post_rows = _get(
        "content_items",
        urllib.parse.urlencode({"id": f"eq.{cid}", "select": "*"}),
    )
    post_row = post_rows[0]
    post_counts = _safety_counts(cid)
    post_hash = _row_hash(post_row, skip=("updated_at",))

    print(
        f"prompt_summary chars: {len(post_row.get('prompt_summary') or '')}"
    )
    print(f"row hash (sans updated_at): {post_hash}")
    print(f"safety counts: {post_counts}")

    if post_hash != pre_hash:
        print("\nRESTORE FAILED: post-row hash differs from pre.")
        # Diff the columns
        for k in set(pre_row.keys()) | set(post_row.keys()):
            if k == "updated_at":
                continue
            if pre_row.get(k) != post_row.get(k):
                print(f"  diff {k}: {pre_row.get(k)!r} vs {post_row.get(k)!r}")
        return 3

    for tbl, n in pre_counts.items():
        if post_counts.get(tbl) != n:
            print(
                f"  FAIL: {tbl} count drifted in restore "
                f"{n} -> {post_counts.get(tbl)}"
            )
            return 4

    print(
        "\nRESTORE OK: byte-identical to pre-test (sans updated_at). "
        "All side-effect tables unchanged."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
