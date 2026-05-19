# Unified Content Review Inbox (Phase 2J)

Status: **read-only** operator surface. No new schema, no new actions,
no provider calls, no email, no publishing.

## Purpose

A single operator queue at `/agency/inbox` that composes every existing
review signal across the workspace into one prioritised stream:

- video review tasks (Phase 1F/1S),
- copy review tasks (Phase 2E/2F),
- prompt review tasks (Phase 2C),
- client approvals + change requests (Phase 1D + 2H + 2I),
- open regeneration_requests (Phase 1E + 2I),
- failed generation jobs (Phase 1G/1H),
- generated clips awaiting review,
- copy previews ready to share,
- approved prompts ready to queue,
- completed agent runs (Phase 1Y),
- items missing prompt / copy.

The page issues **zero** server actions, mutates **zero** DB rows, and
links every row into the existing domain page where the matching gated
action already lives. It is purely an attention surface.

## Included item kinds

`InboxItemKind` (see `web/lib/data/owner-overview.ts`):

| kind | priority | category | links to |
|---|---|---|---|
| `failed_generation_job` | 1 | failed | `/agency/jobs/[jobId]` |
| `generated_video_needs_review` | 1 | video | `/agency/jobs/[jobId]` |
| `copy_client_requested_changes` | 1 | copy | `/agency/copy-drafts` |
| `video_client_requested_changes` | 1 | video | prompt editor |
| `open_regeneration_request` | 1 | client | copy-drafts (if copy) / prompt editor (if video) |
| `prompt_draft_needs_review` | 2 | prompt | prompt editor |
| `approved_prompt_ready_for_generation` | 2 | prompt | prompt editor |
| `copy_draft_needs_review` | 2 | copy | `/agency/copy-drafts` |
| `copy_preview_ready_to_share` | 2 | copy | `/agency/copy-drafts` |
| `content_item_missing_prompt` | 2 | prompt | prompt editor |
| `calendar_item_needs_copy` | 2 | calendar | `/agency/copy-drafts` |
| `copy_approved_by_client` | 3 | copy | `/agency/copy-drafts` |
| `video_approved_by_client` | 3 | video | campaign outputs |
| `agent_run_completed` | 3 | agent | `/agency/agents/brand-analysis` |

## Priority model

- **P1 (urgent — red badge):** failed jobs, client requested changes
  (copy or video), open regeneration requests not yet covered by a
  change-request emission, generated clips awaiting operator review.
- **P2 (today — amber badge):** prompt drafts to review, approved
  prompts ready to queue, copy drafts to review, copy previews ready
  to share, items missing a prompt, calendar items needing copy.
- **P3 (soon — neutral badge):** client approvals (copy + video),
  recent completed agent runs (capped at 5).

Sort key: `priority ASC, then updatedAt DESC` — most recent wins
within a tier.

### Summary cards

- **Urgent** = count of priority 1 items.
- **Needs review** = count of priority 2 items.
- **Ready to share / generate** = `approved_prompt_ready_for_generation`
  + `copy_preview_ready_to_share`.
- **Client decisions** = client-approved (copy + video) +
  client-requested-changes (copy + video) + open regen.

### Filter tabs (URL searchParam)

`?filter=all|urgent|video|copy|prompts|client|failed`. Each tab is a
plain server-component link (`/agency/inbox?filter=…`) — no JS state.

## Read-only safety boundary

- The page has **no `"use server"` actions**, no inserts/updates/deletes,
  no provider/email/publishing imports.
- The data helper `listAgencyInboxItemsForWorkspace`
  (`web/lib/data/owner-overview.ts`) only calls existing read paths:
  `getOwnerOverview`, `listPromptReviewQueueForWorkspace`,
  `listCopyDraftQueueForWorkspace`. None of those write either.
- Live verification: snapshot of every relevant table
  (generation_jobs / content_items / prompt_versions /
  content_approvals / content_feedback / regeneration_requests /
  generated_assets / audio_fixer_jobs / agent_runs) is **byte-identical**
  before and after a full helper read pass.
- Client portal imports nothing from the inbox; `prompt_summary` is
  still not projected by `client_content_items_v`.

## How it connects to the existing flows

The inbox does NOT replace the domain pages — it points at them:

- **Video flow.** Failed job / completed-awaiting-review →
  `/agency/jobs/[jobId]` (existing dry-run + per-clip submit gate).
- **Prompt flow.** Missing / draft / approved →
  `/agency/campaigns/[campaignId]/content/[contentId]/prompt` (Phase 1E
  editor; Phase 1F "Mark approved for generation" gate).
- **Copy flow.** Draft / approval / preview / share / revise →
  `/agency/copy-drafts` (Phase 2E + 2F + 2G + 2I panels).
- **Client decisions.** Approved / changes-requested → the relevant
  domain page above (the client itself acts in the portal).
- **Agent runs.** Completed → `/agency/agents/brand-analysis`
  (Phase 1W/1Y Recent runs).

Generation submits, prompt approvals, copy approvals, preview
prepare/share, and client sends all stay behind their existing gates.

## Live verification (Phase 2J)

`tmp/_phase_2j_live.py` snapshots 9 tables, simulates the helper's
GETs, re-snapshots, and asserts byte-identical state. Result against
current Supabase data:

- `generation_jobs` 8 → 8 (signature byte-identical).
- `content_items` 7 → 7.
- `prompt_versions` 9 → 9.
- `content_approvals` 5 → 5.
- `content_feedback` 4 → 4.
- `regeneration_requests` 2 → 2.
- `generated_assets` 9 → 9.
- `audio_fixer_jobs` 1 → 1.
- `agent_runs` 2 → 2.

Inbox simulation:
- 1 failed job (P1), 0 open regens (P1), 0 client-requested-changes (P1),
  7 completed jobs (P1 if content status is in operator-review),
- 1 client-approved (P3), 2 shared-with-client (P3),
- 2 recent agent runs (P3, capped at 5).

## Future improvements

- **Bulk actions** — multi-select rows then dispatch the matching
  domain action (e.g. bulk-mark-approved for prompts, bulk-share
  previews). Each action stays behind its existing gate.
- **Snooze / dismiss** — operator-private "I will look at this later"
  state. Needs a small `inbox_dismissals` table.
- **Due dates** — pair items with `scheduled_for` and surface "due
  today / overdue".
- **Owner notifications** — daily/weekly digest email of P1 items.
  Behind a typed-confirmation send gate, same pattern as
  `shareCopyPreviewWithClientAction`.
- **Claude Code task handoff** — "Prepare Claude Code task" button per
  inbox row that snapshots the row's context into a task brief
  (Phase 2D hybrid execution model). Still no auto-execution.

## Hard rules preserved

- No Seedance / Enhancor / Audio Fixer / paid API.
- No email / publishing / social-platform writes.
- No `generation_jobs / prompt_versions / generated_assets /
  audio_fixer_jobs / content_approvals / content_feedback /
  regeneration_requests / content_items` mutations.
- Client portal boundary unchanged. The inbox is operator-only and
  loads no client-only data.
