# Non-video client review flow (Phase 2G)

Status: end-to-end flow for taking a non-video draft content item
through internal copy approval, client-safe preview preparation, and
an explicit operator-driven share. No email, no publishing, no paid
API calls.

## Why this flow exists

The Video branch goes:

```
Brand Analysis → Calendar → Prompt Drafts → Prompt Review
  → Approve for generation → Generate video → Review
  → Share with client (operator action) → Client portal renders <video>
```

The Organic/copy branch had no equivalent of "operator-shared, client-
visible artifact" before Phase 2G. Phase 2E (Copy Draft Agent) wrote
*operator-only* working copy to `content_items.caption_draft` (it
contained structured markup like `[tone: …]`, `HEADLINE:`, hashtag
arrays). Phase 2F approved that copy internally via a
`[copy approval]` block in `prompt_summary`. Neither phase changed
what the client portal could see for non-video items.

Phase 2G fills that gap with the smallest possible footprint:

```
… → Copy Draft Agent → Internal Copy Approval (Phase 2F)
  → ► Prepare client-safe preview (Phase 2G)
  → ► Share preview with client (Phase 2G — explicit gate)
  → Client portal renders the preview text
```

The Phase 2G actions never email, never publish to Instagram /
LinkedIn / Facebook, never call Seedance / Enhancor / Audio Fixer,
never create a generation_jobs row, and never touch any video URL.

## The lifecycle

| State | Where it lives | Set by | Visible to client? |
|---|---|---|---|
| no copy | `content_items.caption_draft IS NULL` | initial | no |
| copy drafted | `prompt_summary` block `[copy draft]` + `caption_draft` populated | `createCopyDraftForContentItemAction` (Phase 2E) | no |
| approved internally | `prompt_summary` block `[copy approval]` `copy_approval_status: approved_internal` | `approveCopyDraftAction` (Phase 2F) | no |
| **preview prepared** | `content_items.client_safe_copy_preview` populated + `[client copy preview]` block `prepared` (Phase 2G) | `prepareClientCopyPreviewAction` | no (`shared_with_client` still false) |
| **shared with client** | `shared_with_client = true` + `status = 'shared_with_client'` + `[client copy preview]` block `shared_with_client` (Phase 2G) | `shareCopyPreviewWithClientAction` | yes (via `client_content_items_v`) |

Each state transition is its own explicit operator action. No state
auto-advances. No timer flips a row. No background job notifies the
client.

## Schema (migration 009 — additive only)

```sql
alter table public.content_items
  add column if not exists client_safe_copy_preview text;

create or replace view public.client_content_items_v
with (security_invoker = true) as
select
  ci.id, ci.campaign_id, ci.content_calendar_id, ci.title, ci.status,
  ci.scheduled_for, ci.platforms, ci.hook_text, ci.caption_draft,
  ci.client_safe_poster_url, ci.duration_sec, ci.created_at,
  ci.updated_at, ci.client_safe_video_url, ci.client_safe_copy_preview
from public.content_items ci
where ci.shared_with_client = true
  and ci.status in ('shared_with_client',
                    'approved_by_client',
                    'changes_requested_by_client');
```

- The view STILL excludes `prompt_summary`, `cost_*`, `internal_*`,
  `quality_tier`, all audio-fixer columns, and every operator-only
  table.
- The view STILL only returns rows that the operator has explicitly
  set `shared_with_client = true` on.
- The migration is non-destructive: it adds one nullable column and
  rebuilds the view in place.
- See `supabase/migrations/009_client_safe_copy_preview.sql` for the
  full SQL + rollback instructions.

## Actions

### `prepareClientCopyPreviewAction({ contentItemId, clientSafeCopyPreview?, operatorNotes? })`

Pre-conditions (the action returns an error otherwise):
- operator persona,
- editable status (not under client sign-off),
- `copy_approval_status: approved_internal` block present in
  `prompt_summary`,
- non-empty caption_draft OR explicit
  `clientSafeCopyPreview` override.

Writes:
- `content_items.client_safe_copy_preview = <preview>`
- `content_items.prompt_summary` appends a fresh `[client copy preview]`
  block (`client_copy_preview_status: prepared`, timestamp, optional
  internal note).

Does NOT change: `status`, `shared_with_client`, `caption_draft`,
`client_safe_video_url`, `client_safe_poster_url`, costs, audio-fixer
fields, any other table.

### `shareCopyPreviewWithClientAction({ contentItemId, confirmationToken, operatorNotes? })`

Pre-conditions:
- operator persona,
- editable status,
- `client_safe_copy_preview` already populated,
- `copy_approval_status: approved_internal`,
- `confirmationToken === "SHARE COPY"` (literal string).

Writes:
- `content_items.shared_with_client = true`
- `content_items.status = 'shared_with_client'` (required by the
  existing `content_items_shared_flag_consistent` check constraint)
- `content_items.prompt_summary` appends an updated
  `[client copy preview]` block
  (`client_copy_preview_status: shared_with_client`, timestamp,
  optional internal note).

Does NOT: send any email, publish to any social platform, call any
provider (Seedance / Enhancor / Audio Fixer / Kling / Meta / etc.),
create any generation_jobs / prompt_versions / generated_assets /
audio_fixer_jobs row, touch any video URL.

## Client portal

`web/app/client/[portalSlug]/content/[contentId]/page.tsx` branches
on `mediaType` + `clientSafeCopyPreview`:

- `mediaType === "none"` AND `clientSafeCopyPreview` populated →
  copy-only layout: title + status chip + the prepared preview text
  (rendered in a `<pre class="whitespace-pre-wrap">` so paragraph
  breaks survive) + hook (if any) + the existing client feedback
  panel.
- otherwise → existing video-item layout (player + caption_draft +
  hook).

The client portal NEVER receives:
- `prompt_summary` (excluded by the view)
- `[copy approval]` / `[client copy preview]` provenance metadata
- internal asset paths, costs, provider ids, quality tiers
- raw Copy Draft Agent output (it lives in `caption_draft`, which
  the portal renders only for video items — non-video items go
  through `client_safe_copy_preview` instead).

## Safety summary

Phase 2G never:
- sends email
- publishes to Instagram / Facebook / LinkedIn / TikTok / X / any
  social platform
- calls Seedance / Enhancor / Audio Fixer / Kling / any paid API
- triggers any background scheduler / queue
- creates a generation_jobs / prompt_versions / generated_assets /
  audio_fixer_jobs row
- changes any client-portal RLS policy
- exposes `prompt_summary`, costs, or any operator-only column

The only state changes Phase 2G can produce are:
- `content_items.client_safe_copy_preview` (newly populated)
- `content_items.shared_with_client` (false → true, on share)
- `content_items.status` ('draft' → 'shared_with_client', on share)
- `content_items.prompt_summary` (append of the
  `[client copy preview]` block; idempotent strip-then-append)

## Hybrid Claude Code execution model

This flow keeps the dashboard as a deterministic control plane.
Advanced copy improvements — rewriting in a specific brand voice,
generating multiple variants, polishing a long-form piece — are
delegated to the **Claude Code task handoff** model described in
[`docs/hybrid_claude_code_execution_model.md`](./hybrid_claude_code_execution_model.md):

- the dashboard never calls the Claude API directly,
- the dashboard never executes Claude Code automatically,
- when the operator needs better copy than the deterministic
  template, they prepare a Claude Code task and run it locally,
- the locally-improved copy is written back to
  `content_items.caption_draft` (still operator-only) or directly
  to `content_items.client_safe_copy_preview` (post-approval).

Phase 2G does not change that posture; it only adds the explicit
gates that follow internal approval.

## Phase 2H — Approve / request-changes (shipped)

The existing client-feedback path (Phase 1D/1O) handles copy items
unchanged. `ClientFeedbackPanel` renders the **Approve** /
**Request changes** / **Comment** buttons under the copy preview just
as it does under the video player. The route handler
`POST /api/portal/feedback` dispatches into
`approveContentAction` / `requestChangesContentAction` /
`commentContentAction`.

Per path:

- **Approve.** Insert `content_approvals (decision='approved',
  actor_kind='client')`, `bumpStatus → 'approved_by_client'`.
- **Request changes.** Insert `content_feedback`,
  `content_approvals (decision='changes_requested', actor_kind='client')`,
  optionally a `regeneration_requests (status='open')`,
  `bumpStatus → 'changes_requested_by_client'`.

`bumpStatus` updates only `content_items.status` and keeps
`shared_with_client=true`. It never touches `client_safe_video_url`,
`client_safe_copy_preview`, `prompt_summary`, or operator-only
internals. `client_content_items_v` is unchanged.

### Operator-side surfacing (Owner Command Center, Phase 2H)

`deriveOwnerNextActions` now parses `format:` from `prompt_summary`
and routes:

- `changes_requested_by_client` + copy format →
  `revise_copy_from_client_feedback` (priority 1, link
  `/agency/copy-drafts`).
- `approved_by_client` + copy format → `copy_approved_by_client`
  (priority 3, link `/agency/copy-drafts`, label "Client approved copy
  — publish is manual").

Video routing keeps `respond_to_feedback` / `publish_ready_video`.

### Hard guarantees preserved by 2H

No email, no publishing, no paid API, no Seedance, no Audio Fixer, no
`generation_jobs` / `generated_assets` / `audio_fixer_jobs` /
`prompt_versions` rows created. `client_content_items_v` still does not
project `prompt_summary` or any operator-only internal column.

### Live verification (Phase 2H, approve)

Content item `5d11c478-68c0-4ec6-b2a5-62dbefeb9515`:

- New `content_approvals` row `a4710e2d-…` with `decision='approved'`,
  `actor_kind='client'`.
- `content_items.status` flipped `shared_with_client →
  approved_by_client`; `shared_with_client=true`,
  `client_safe_video_url=null`, `client_safe_copy_preview` unchanged
  (431 chars), `prompt_summary` unchanged.
- `client_content_items_v` does not expose `prompt_summary`; still
  exposes `client_safe_copy_preview`; status now `approved_by_client`.
- `generation_jobs / prompt_versions / generated_assets /
  audio_fixer_jobs` byte-identical pre/post.

Request-changes path documented, not exercised on the same row
(would overwrite the approval). Same handler with
`action: "request_changes"` produces an open `content_feedback`
row + optional `regeneration_requests` row + status flip.

## Phase 2I — Revise copy from client feedback (shipped)

Action: `reviseCopyDraftFromClientFeedbackAction`
(`web/lib/actions/copy-draft.ts`). Composes the
latest `content_feedback` + open `regeneration_request` into the Copy
Draft Agent's `operatorNotes`, re-runs the agent (which idempotently
strips and rewrites the `[copy draft]` block, AND removes any prior
`[copy approval]` / `[client copy preview]` block), then atomically
flips `content_items.status → 'draft'` and `shared_with_client →
false`. Best-effort: marks the source `regeneration_requests.status =
'accepted'`.

The revised draft is hidden from `/client/[portalSlug]` because
`client_content_items_v` filters by status — `draft` is not in
`CLIENT_VISIBLE_STATUSES`. Internal approval (Phase 2F) and the
prepare → share preview cycle (Phase 2G) MUST run again before the
client sees the revision.

UI: a new `CopyRevisionPanel` on `/agency/copy-drafts` renders only
for items in `changes_requested_by_client` or with an open feedback /
regen row. It surfaces the client's words + reason and a single
"Revise copy from client feedback" button. The queue helper batches
the per-item feedback + regen fetch (`.in(content_item_id, …)` —
no N+1).

Hard guarantees still preserved: no email, no publishing, no Seedance
/ Enhancor / Audio Fixer / paid API, no `generation_jobs /
prompt_versions / generated_assets / audio_fixer_jobs` rows, no
automatic client share. Live-verified end-to-end on
`b920e5e2-a67d-45ca-96c9-f9422218d675` (format `feed_post`).

## Future phases

| Phase | Scope |
|---|---|
| recommended next | **Content Review Inbox** — unified operator queue combining video feedback, copy feedback, prompt review, and client approvals. No new schema; one composed listing across `regeneration_requests`, `content_feedback`, `content_approvals`, and the existing per-domain queues. |
| later | Per-platform publishing (Instagram, LinkedIn, Facebook) behind OAuth + explicit per-item `publish_to_<platform>` action. Out of scope until OAuth is wired. |
| later | Email send via operator-driven action (campaign-by-campaign opt-in). Out of scope until a send-quota / suppression-list layer is in place. |
