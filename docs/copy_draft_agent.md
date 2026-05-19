# Copy Draft Agent (Phase 2E + 2F)

Status: deterministic copy drafting for non-video formats, plus an
internal operator approval gate. No schema change. No LLM / no fetch /
no paid call. No email, no publishing, no client share.

## Purpose

Phase 2D made the platform multi-format but non-video items had no
"produce the actual copy" step — they only had a brief. Phase 2E adds a
deterministic Copy Draft Agent that turns a non-video draft content
item into operator-review copy written into
`content_items.caption_draft`. It is the non-video counterpart of the
prompt-draft workflow.

## Supported formats

Mode is derived from the item's `format` (parsed from `prompt_summary`):

| format | mode | output |
|---|---|---|
| `text_post` (LinkedIn etc.) | `linkedin_text` | headline/hook, post body, CTA, hashtags, short alt version |
| `feed_post` / `static_image` | `social_feed` | first-line hook, caption, CTA, hashtags, visual brief |
| `story` | `story` | 3–5 frames (text + visual direction), CTA sticker |
| `carousel` | `carousel` | slide-by-slide outline + headlines + per-slide copy + final CTA slide |
| `email_snippet` | `email` | subject, preview text, short body, CTA (DRAFT — never auto-sent) |
| `blog_snippet` | `blog` | title, intro paragraph, bullet outline, CTA |
| `ugc_video_ad` / `organic_reel` / `short_video` / `long_video` | `video_caption` | **supporting caption only** — never a script; the video prompt/script stays in the prompt-version workflow |

## Input / output

Action: `createCopyDraftForContentItemAction`
(`web/lib/actions/copy-draft.ts`). Generator:
`planCopyDraft` (`web/lib/agents/copy-draft.ts`).

Input: `contentItemId`, optional `tone`, optional `cta`, optional
`operatorNotes`. Format / channel / brand context (matched_niche,
product_url) are parsed from `prompt_summary`; channel falls back to
`platforms[0]`; format falls back to `feed_post` if untagged.

Output: a deterministic plain-text copy block (per mode) +
caveats. The action writes it to `caption_draft` and appends a
structured block to `prompt_summary`:

```
[copy draft]
copy_draft_status: drafted
copy_draft_source: copy_draft_agent
copy_format: <format>
copy_channel: <channel>
copy_mode: <mode>
copy_drafted_at: <iso>
copy_operator_note: <note?>
```

Re-runs are idempotent: the prior `[copy draft]` block is stripped
before the new one is appended. The original agent provenance above the
marker is preserved.

## Where drafts are stored

`content_items.caption_draft` (existing `text` column — sufficient, no
schema change). Provenance lives in the `prompt_summary` structured
block. No new table.

## Safety boundaries

- Deterministic only — no LLM, no OpenAI/Anthropic, no fetch, no scrape,
  no paid API.
- Writes only `caption_draft` + `prompt_summary`. Never touches
  `status`, `shared_with_client`, `client_safe_video_url`.
- No `generation_jobs`, `prompt_versions`, `generated_assets`,
  `audio_fixer_jobs` created.
- No email sent, nothing published, nothing shared with the client,
  nothing approved.
- Operator-only. Client portal imports nothing from this workflow.
- Editable-status guard: only `draft` / internal-editable items
  (never `shared_with_client` / `approved_by_client`).

## How it fits the organic content workflow

```
Brand Analysis run → Calendar Agent → multi-format draft content_items
  → non-video items routed (Phase 2D next-action) to copy/brief path
  → ► Copy Draft Agent (2E): caption_draft populated, operator-review ◄
  → operator reviews/edits the copy in the dashboard
  → (future) approve for client review / publishing — separate gate
```

The video items keep going through the prompt-version + Seedance gate
unchanged; non-video items now have a real production step that never
touches generation.

## When to use the Claude Code handoff

The deterministic draft is intentionally a *starting point* (template
voice, must be rewritten). For high-stakes or long-form copy, the
dashboard can later **prepare** a Claude Code task (see
`docs/hybrid_claude_code_execution_model.md`) that an operator runs in
their own session, writing the improved copy back to Supabase. Phase 2E
ships only the placeholder — no Claude Code execution, no Claude API,
no auto-run.

## Phase 2F — Review & Approve copy (INTERNAL ONLY)

Phase 2E only wrote the copy. Phase 2F adds an explicit operator review
+ internal approval gate. Approval is **internal-only**: the client does
not see it, no email goes out, nothing is published, no generation job
is created.

### Approval state — no schema change

State lives in a second provenance block at the end of
`content_items.prompt_summary`. Marker: `[copy approval]`.

```
[copy approval]
copy_approval_status: approved_internal
copy_approved_at: <iso>
copy_approved_by: <profile_id or 'operator'>
copy_approval_notes: <optional one-liner>
```

Re-approvals strip the prior `[copy approval]` block then append a
fresh one, so the column never grows unbounded. Resetting approval
strips the block without touching anything else.

The `client_content_items_v` VIEW does **not** project
`prompt_summary`, so this state is structurally invisible to the client
portal.

### Actions

`web/lib/actions/copy-draft.ts`:

- `approveCopyDraftAction({ contentItemId, approvedCaptionDraft?, approvalNotes? })`
  - Requires operator persona.
  - Editable-status guard (never touches client-signed-off items).
  - Requires non-empty `caption_draft`.
  - Optionally overrides `caption_draft` with `approvedCaptionDraft`.
  - Writes ONLY `caption_draft` (when provided) + `prompt_summary`
    (always).
- `resetCopyApprovalAction({ contentItemId })`
  - Strips the `[copy approval]` block. No-op when none is present.

### UI

`/agency/copy-drafts` mounts a `CopyApprovalPanel` next to the existing
`CopyDraftPanel` for every queue item. The panel:

- shows the current `caption_draft` (full text),
- offers an optional inline textarea to edit the copy *before*
  approving,
- shows an optional approval-notes input,
- shows the current approval state + timestamp,
- has a "Reset approval" button on approved items.

A warning chip in every variant of the panel reminds the operator:
"internal sign-off · NOT sent · NOT published · NOT shared with client".

### Next-action surfacing

`deriveOwnerNextActions` (Owner Command Center) emits per-item actions
for non-video formats:

| state | NextActionKind |
|---|---|
| no `caption_draft` | `create_copy_draft` (priority 3) |
| copy drafted, no approval block | `review_copy_draft` (priority 2) |
| `copy_approval_status: approved_internal` | `ready_for_client_review_or_publish_later` (priority 3) |

All three link to `/agency/copy-drafts`. None of them triggers a paid
call, email, publish, or client share.

### Why approval is internal-only

Sharing copy with the client, sending emails, or publishing to social
platforms requires real third-party integrations (OAuth, send-quota
management, schedule queues, reconciliation). None of those exist yet.
Phase 2F deliberately stops at "operator says this copy is good"; it
is a holding state, not a publish.

### What is still required before client review / publishing

The recommended next phase is a **client-review preparation step** for
non-video copy. Concretely:

- a parallel of `client_safe_video_url` for static/text formats — a
  pre-rendered preview the client portal can show (image render for
  feed posts, snippet card for text posts, etc.),
- a new `content_items.client_safe_copy_preview` (or equivalent),
  populated only after internal approval + an explicit "share preview
  with client" action,
- a `client_safe_copy_url` exposed via `client_content_items_v` so the
  view continues to enforce projection-level safety.

Publishing (Instagram / LinkedIn / Facebook) stays a separate later
phase, gated by per-platform OAuth + a "publish to <platform>" action
that itself is operator-only and opt-in per item.

### Future schema (documented, NOT applied)

If/when copy approval becomes a first-class workflow, the natural shape
is three new columns on `content_items`:

```
alter table public.content_items
  add column copy_status        text
       check (copy_status is null or copy_status in ('drafted', 'approved_internal')),
  add column copy_approved_at   timestamptz,
  add column copy_approved_by   uuid references public.profiles(id);
```

These would replace the parsing logic in
`web/lib/data/owner-overview.ts` (`parseApprovalStatus` &c) with a
direct column read. Phase 2F intentionally does NOT apply this
migration.

## Future improvements

- Brand-voice memory (persisted tone/voice per brand).
- Multiple copy variants per item + pick-one.
- Platform-specific constraints (char limits, hashtag caps, link rules).
- Client-review preparation for non-video items (the recommended next
  phase — see above).
- Publishing integrations (per-platform, behind OAuth + explicit gate).
