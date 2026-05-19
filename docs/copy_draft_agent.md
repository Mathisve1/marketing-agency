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

## Phase 2G — Client-review preparation (INTERNAL → CLIENT PORTAL ONLY)

Phase 2F ended at "operator has approved this copy internally". Phase
2G adds two further steps so the operator can show the approved copy
to the client in the portal — still without email, publishing, or
any social-platform integration. See
[`docs/non_video_client_review_flow.md`](./non_video_client_review_flow.md)
for the end-to-end flow.

### New column + view (migration 009)

`content_items.client_safe_copy_preview text` — operator-set, NULL
until prepared. `client_content_items_v` rebuilt to project this
column alongside the existing fields. The view does NOT add any
operator-only columns; the safety guarantee from migrations 001 +
006 is preserved.

### Actions (web/lib/actions/copy-draft.ts)

- `prepareClientCopyPreviewAction({ contentItemId, clientSafeCopyPreview?, operatorNotes? })`
  - Requires `copy_approval_status: approved_internal` first.
  - Writes ONLY `client_safe_copy_preview` (and a
    `[client copy preview]` provenance block in `prompt_summary`).
  - NEVER flips `shared_with_client`, status, video/poster URLs,
    caption_draft, or any cost/internal column.
- `shareCopyPreviewWithClientAction({ contentItemId, confirmationToken, operatorNotes? })`
  - Requires the literal token `SHARE COPY` to confirm.
  - Refuses to run when no `client_safe_copy_preview` is on file.
  - Flips `shared_with_client = true` AND `status = 'shared_with_client'`
    together (the schema check constraint
    `content_items_shared_flag_consistent` requires both).
  - NEVER sends email, NEVER publishes to any social platform, NEVER
    calls Seedance / Enhancor / Audio Fixer / any paid API, NEVER
    creates a generation_jobs / prompt_versions / generated_assets /
    audio_fixer_jobs row, NEVER touches `client_safe_video_url`.

### UI

`/agency/copy-drafts` now mounts a `ClientPreviewPanel` next to the
existing `CopyDraftPanel` + `CopyApprovalPanel`. The panel is gated:
preparation is disabled until the copy is approved internally;
sharing is disabled until the preview is on file and the operator
types `SHARE COPY`.

### Client portal

`web/app/client/[portalSlug]/content/[contentId]/page.tsx` now
branches: for items where `mediaType === "none"` and a
`clientSafeCopyPreview` is on file, it renders the preview text
(via `<pre>` to preserve whitespace) instead of the video player +
raw caption. For video items the existing layout is unchanged.

## Phase 2H — Client approval / change-request loop for copy

Status: shipped, **no new schema, no new actions**. The existing
client-feedback infrastructure (Phase 1D/1O) handles copy items
without modification:

- Route: `POST /api/portal/feedback` (action handler at
  `web/app/api/portal/feedback/route.ts`).
- Actions: `approveContentAction`, `requestChangesContentAction`,
  `commentContentAction` in `web/lib/actions/client-feedback.ts`.
- Component: `ClientFeedbackPanel` (renders Approve / Request changes
  / Comment buttons under whatever the page surfaces — video player OR
  copy preview).
- Tables: writes to `content_approvals` / `content_feedback` /
  optionally `regeneration_requests`, and `bumpStatus` flips
  `content_items.status` to `approved_by_client` or
  `changes_requested_by_client`.

None of those touch `client_safe_video_url`, video assets, generation
jobs, Audio Fixer, or any provider. The same code path works for a
copy-only item because the actions only know about content items, not
media types.

### How Phase 2G + 2H compose at the client portal

For a copy-only content item:

1. Phase 2G's page branch renders the `client_safe_copy_preview` text
   (no player) and `ClientFeedbackPanel` below it.
2. The client clicks **Approve** or **Request changes** in that panel.
3. The route handler routes to `approveContentAction` or
   `requestChangesContentAction`. Inserts the row, flips
   `content_items.status`, revalidates the page.
4. The dashboard's next-action surface picks up the new status (see
   below).

### Operator follow-up — Owner Command Center routing (Phase 2H)

`deriveOwnerNextActions` (`web/lib/data/owner-overview.ts`) parses
`format:` from `prompt_summary`. New `NextActionKind` values:

- `revise_copy_from_client_feedback` — emitted when status is
  `changes_requested_by_client` AND format is a copy format. Priority
  1, links to `/agency/copy-drafts`.
- `copy_approved_by_client` — emitted when status is
  `approved_by_client` AND format is a copy format. Priority 3, links
  to `/agency/copy-drafts`. Label: "Client approved copy — publish is
  manual". (Publishing is deliberately out of scope.)

Video items keep the previous `respond_to_feedback` /
`publish_ready_video` routing.

### How it differs from video review

The wire / action contract is identical. The differences are only:

| | video item | copy item |
|---|---|---|
| What the client sees | `<video>` + thumbnail | `<pre>` copy preview |
| Source of preview | `client_safe_video_url` | `client_safe_copy_preview` |
| Next action on approval | "Ready to publish" → outputs | "Client approved copy" → copy-drafts |
| Next action on changes | "Respond to client feedback" → prompt editor | "Revise copy from client feedback" → copy-drafts |
| What "publish" means | upload / scheduled push (not built) | post / email (not built) |

In both cases the dashboard never publishes or sends — that gate is
explicitly deferred.

### Live verification (Phase 2H, approve path)

Content item `5d11c478-68c0-4ec6-b2a5-62dbefeb9515` (Phase 2G copy
item, was `status=shared_with_client`):

- `content_approvals` row `a4710e2d…` created, `decision=approved`,
  `actor_kind=client`.
- `content_items.status` → `approved_by_client`,
  `shared_with_client=true`, `client_safe_video_url=null` (unchanged),
  `client_safe_copy_preview` unchanged (431 chars), `prompt_summary`
  unchanged (no leak).
- `client_content_items_v` still does NOT project `prompt_summary` and
  still projects the copy preview.
- `generation_jobs`, `prompt_versions`, `generated_assets`,
  `audio_fixer_jobs` byte-identical pre/post.

### Request-changes path (documented, not exercised on the same row)

Identical wire path via the same handler with `action: "request_changes"`:

- Inserts `content_feedback` row.
- Optionally inserts `regeneration_requests` row (existing behavior).
- Bumps status to `changes_requested_by_client`.
- Owner Command Center surfaces `revise_copy_from_client_feedback`
  pointing at `/agency/copy-drafts`.

Not executed on `5d11c478…` because it would conflict with the
approve we just ran. To test it cleanly: run on a fresh non-video
draft + shared item.

## Phase 2I — Revise copy from client feedback

Status: shipped. Single new action; no new schema. Strips and rewrites
provenance via the audited Phase 2E path.

Action: `reviseCopyDraftFromClientFeedbackAction` in
`web/lib/actions/copy-draft.ts`.

Input: `contentItemId`, optional `feedbackId` /
`regenerationRequestId` overrides, optional `operatorNotes`.

Behavior:

1. Load the content item, validate format is a copy format (rejects
   video formats which use the prompt-version flow).
2. Resolve sources: explicit `feedbackId` / `regenerationRequestId` if
   given, otherwise pick the latest client `content_feedback` + the
   open `regeneration_requests` row. At least one source is required
   (the action refuses to "revise" without a change request to
   address — re-run the regular Copy Draft Agent for blank slate).
3. Compose a revision brief (≤500 chars) combining
   feedback/regen reason + body + the operator note, prefixed with
   `[revise from client feedback]`.
4. Delegate to `createCopyDraftForContentItemAction({contentItemId,
   operatorNotes: revisionBrief})`. `stripCopyBlock` there removes the
   prior `[copy draft]` AND every block after it — including
   `[copy approval]` (Phase 2F) and `[client copy preview]` (Phase
   2G). The freshly-written `[copy draft]` block is now the only
   provenance block on the row.
5. Single atomic UPDATE: `status='draft'`, `shared_with_client=false`.
   The `content_items_shared_flag_consistent` CHECK is satisfied
   because both columns flip together. The row drops out of
   `client_content_items_v` (status=draft is not in
   `CLIENT_VISIBLE_STATUSES`).
6. Best-effort: mark the consumed `regeneration_requests.status =
   'accepted'`. A failure here is non-fatal — the revision itself
   landed; the next manual review can clear an open row.

Result: `{ok, newCopyText, newStatus, regenerationRequestAccepted,
approvalAndPreviewStripped, usedFeedbackId, usedRegenerationRequestId}`.

### Why approval + preview must be repeated

A revised draft has never been internally reviewed and has never had a
new client preview prepared. Re-running Phase 2F approval and Phase 2G
prepare→share is therefore mandatory before anything the client sees
reflects the revision. The action enforces this implicitly: by
stripping the two blocks AND flipping status back to `draft`, the
queue's `nextAction` reverts to `review_copy_draft` (the operator
must approve again) and then `prepare_client_preview` →
`share_client_preview`.

### UI

`/agency/copy-drafts` queue helper now batch-fetches per-item:
`content_feedback.author_kind='client'` (latest) and
`regeneration_requests.status='open'` (latest). New panel
`CopyRevisionPanel` renders before `CopyDraftPanel` only when the item
is `changes_requested_by_client` or has an open feedback/regen row.
The panel shows the client's words + reason + a "Revise copy from
client feedback" button + an optional operator note. After success,
the revised caption_draft is shown inline.

Queue `nextAction` now has a `revise_from_client_feedback` value that
takes precedence over `review_copy_draft` / `prepare_client_preview` /
`share_client_preview` so this work is always at the top of the queue
for an affected item.

### Live verification (Phase 2I)

Fresh copy item `b920e5e2-a67d-45ca-96c9-f9422218d675` (format
`feed_post`, channel `facebook`):

1. Inserted with `status=shared_with_client`, full
   `[copy draft]` + `[copy approval]` + `[client copy preview]`
   provenance, populated `client_safe_copy_preview`.
2. Simulated client request-changes:
   `content_feedback` `a12d0953-…` with `[wrong_tone]` reason,
   `content_approvals` `decision=changes_requested`,
   `regeneration_requests` `1d4848b2-…` `status=open`,
   `reason=wrong_tone`, `content_items.status →
   changes_requested_by_client`.
3. Revise applied:
   - `caption_draft` rewritten (504 chars) with the operator-revision
     brief.
   - `[copy approval]` block stripped ✓
   - `[client copy preview]` block stripped ✓
   - `content_items.status → draft`, `shared_with_client → false` ✓
   - `regeneration_requests.status → accepted` ✓
4. Safety invariants:
   - `generation_jobs / prompt_versions / generated_assets /
     audio_fixer_jobs` byte-identical pre/post.
   - `client_content_items_v` no longer returns the row
     (draft + not shared falls outside the client view).
   - `client_safe_video_url` stayed `null` throughout.

### Hard guarantees preserved

- **No Seedance / Enhancor / Audio Fixer / paid API.**
- **No email / no publishing.**
- **No `generation_jobs` / `prompt_versions` / `generated_assets` /
  `audio_fixer_jobs` rows** created or mutated.
- **No automatic client share.** The revised draft is hidden from the
  client portal by `client_content_items_v`'s status filter; the
  operator must re-approve (Phase 2F) and re-prepare + re-share
  (Phase 2G) before the client sees anything.
- Client-portal boundary intact: `prompt_summary` is still not
  projected by the client view; the revision brief lives inside the
  `[copy draft]` block in that operator-only column.

## Future improvements

- Brand-voice memory (persisted tone/voice per brand).
- Multiple copy variants per item + pick-one.
- Platform-specific constraints (char limits, hashtag caps, link rules).
- Multiple revision variants per feedback (operator picks the best).
- Diff view between previous and revised `caption_draft`.
- Surface feedback categories more richly (group by `reason`).
- Claude Code handoff for deeper rewriting when the deterministic
  template is too generic for the feedback.
- Publishing integrations (per-platform, behind OAuth + explicit gate).
