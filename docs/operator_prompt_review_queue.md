# Operator Prompt Review Queue (Phase 2C)

Status: Phase 2C. A workspace-wide, **read-only** operator surface that
shows every content item and its prompt-draft state with a deterministic
next action.

## Purpose

After the Calendar Agent (Phase 1Z) materialises draft content items and
the bulk prompt-draft action (Phase 2B) attaches `operator_editing`
prompt versions, the operator needs a single place to see *what still
needs attention*: which items have no prompt, which have drafts waiting
for review, which are approved, which are stuck. This queue is that
funnel. It never mutates anything — it points the operator at the
existing prompt editor where the already-audited approval gate lives.

Route: `/agency/prompt-review` (operator-only).
Data helper: `listPromptReviewQueueForWorkspace(workspaceId)` in
`web/lib/data/owner-overview.ts`.

## Queue states (per item)

Each `PromptReviewQueueItem` carries: content item id, title, campaign
id/name, brand name, content status, scheduled date, platforms, latest
prompt_version id + status, prompt version count,
`hasOperatorEditingPrompt`, `hasApprovedForGenerationPrompt`, latest
updated timestamp, the next action, and the prompt-editor href.

Prompt version statuses observed: `draft`, `operator_editing`,
`approved_for_generation`, `superseded`.

## Next-action model (deterministic, pure)

`derivePromptReviewNextAction({ contentStatus, promptCount,
hasOperatorEditing, hasApproved })`:

| condition | next action |
|---|---|
| `contentStatus === 'failed'` | `blocked_or_needs_attention` |
| content status ∈ {generating, raw_ready, audio_fixer_pending, audio_fixed, ready_for_client_review, shared_with_client, approved_by_client} | `already_approved` |
| operator-editable, `promptCount === 0` | `create_prompt_draft` |
| operator-editable, has an `approved_for_generation` prompt | `create_generation_job` |
| operator-editable, otherwise (has `operator_editing`, or only `draft`/`superseded`) | `review_prompt_draft` |

`approve_prompt_for_generation` is part of the type for forward
compatibility but is not emitted yet — approval is an explicit operator
action inside the prompt editor, never a queue auto-step.

Summary cards: `missingPrompt` (= create_prompt_draft),
`needsReview` (= review_prompt_draft), `approvedReady`
(= any item with an approved_for_generation prompt), `blocked`
(= blocked_or_needs_attention).

## How it fits after Calendar Agent + bulk prompt drafts

```
Brand Analysis agent run (1W/1Y)
  → Calendar Agent → draft content_items (1Z)
  → bulk "Create prompt drafts" → operator_editing prompt_versions (2B)
  → ► Prompt Review Queue (2C): see everything, pick what to review ◄
  → operator opens the prompt editor per item, reviews, and uses the
    EXISTING "Mark approved for generation" gate (Phase 1X action,
    unchanged) — only THEN does generation become possible.
```

## Safety boundaries

- **Read-only.** The page and `listPromptReviewQueueForWorkspace` issue
  only SELECTs. No INSERT/UPDATE/DELETE anywhere in this phase.
- **No approval here.** The queue links to the prompt editor; the
  `markPromptVersionApprovedForGenerationAction` (Phase 1X) is reused
  unchanged — not duplicated.
- **No generation / no provider / no Audio Fixer / no email.**
- **Operator-only.** `web/app/client/**` imports nothing from this
  workflow or from `owner-overview`. `prompt_versions` and the queried
  tables are operator-RLS only (migrations 002/004).
- The editor link target
  (`/agency/campaigns/[campaignId]/content/[contentId]/prompt`) is the
  existing route. Note: that editor page currently reads campaign/content
  from the in-memory demo store, so Supabase-only content items will not
  render there until that page is migrated to the Supabase data layer —
  a known, pre-existing limitation tracked for a later phase, out of
  Phase 2C scope. The queue itself is fully Supabase-backed.

## Live verification (Phase 2C)

Read-only check against the two Phase 2B items:

- `2cb06509-…` — content_status `draft`, 2 prompt versions, latest
  `operator_editing`, next action `review_prompt_draft`, editor href
  present.
- `dce674ff-…` — content_status `draft`, 1 prompt version, latest
  `operator_editing`, next action `review_prompt_draft`, editor href
  present.
- `generation_jobs`, `generated_assets`, `audio_fixer_jobs` row counts
  identical before/after (no writes; impossible by construction).

## Future improvements

- **Bulk approve** — a guarded multi-select that calls the existing
  approval action per selected item (still no generation job).
- **Quality score** — surface a heuristic prompt-quality signal so the
  operator can triage the longest queues first.
- **Prompt diff view** — show what changed between prompt versions
  inline in the queue.
- **Agent-run grouping** — group queue rows by their source agent run.
- **Migration 009** `content_items.source_agent_run_id uuid references
  agent_runs(id) on delete set null` (proposed in
  `docs/create_prompt_drafts_for_calendar_item.md`) — replace the
  `prompt_summary` provenance grep with a real FK so the queue can group
  by run without string parsing. Additive/idempotent; **not applied**.
