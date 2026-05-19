# Create prompt drafts for a calendar item

Status: Phase 2A. Closes the loop *paste URL → agent run → draft
calendar → draft prompts*, all operator-gated and draft-only.

## Purpose

A Phase 1Z draft `content_items` row carries the source Brand Analysis
`agent_run_id` in its `prompt_summary`. Phase 2A lets the operator turn
that into one or more `prompt_versions` rows (status
`operator_editing`) for the content item — without generating video,
approving anything, or touching the client.

## Input — content item requirements

`createPromptDraftsForCalendarItemAction(input)`
(`web/lib/actions/calendar-prompt-drafts.ts`):

| field | required | notes |
|---|---|---|
| `contentItemId` | yes | UUID. |
| `agentRunId` | no | Explicit override; otherwise parsed from `prompt_summary`. |
| `selectedDraftIndexes` | no | Which of the run's `promptDrafts` to materialise (0-based). Default `[0]` — the standard variant. |
| `operatorNotes` | no | ≤500 chars; appended to every created prompt_version's notes. |

Validation gates:

1. operator persona (demo mode rejected — Supabase only).
2. content item exists; status ∈ {draft, generating, raw_ready,
   audio_fixer_pending, audio_fixed, ready_for_client_review,
   changes_requested_by_client, failed}. The two client-decided
   statuses (`shared_with_client`, `approved_by_client`) are
   deliberately excluded — a new prompt under those must go through the
   regeneration-request flow, not this shortcut.
3. campaign → brand resolves (tenancy guard).

## Provenance strategy (no schema change)

The agent linkage is read from `content_items.prompt_summary`, which
Phase 1Z wrote as a structured block containing
`agent_run_id: <uuid>` and `product_url: <url>`. The action parses both
with anchored regexes. Resolution order:

1. `input.agentRunId` if supplied.
2. `agent_run_id:` parsed from `prompt_summary`.
3. **Fallback:** if no run is resolvable but `product_url:` is present,
   the deterministic `planBrandAnalysisUGCPrompt({ productUrl })` is
   re-run locally (no fetch, no LLM) to regenerate the prompt drafts.
4. If none of the above yields drafts → `{ ok: false }` with a clear
   "run the Brand Analysis agent first" message.

`draftSource` (`agent_run` | `regenerated_from_url` | `none`) is
returned so the UI is honest about where the drafts came from.

No migration 009 is required. (If a real FK is later wanted,
`content_items.source_agent_run_id uuid references agent_runs(id) on
delete set null` is the additive, idempotent path — still not needed.)

## prompt_versions output

The action delegates every insert to the Phase 1X
`createPromptVersionFromAgentDraftAction`, the single audited path that
guarantees, per created row:

- `status = operator_editing` (never `approved_for_generation`)
- `version_number = max(version_number for the content) + 1`
- `parent_version_id = latest existing version` (or null)
- `quality_tier = standard_720p`
- `source_regeneration_request_id = null`
- agent + calendar provenance in `notes`
  (`source: calendar_agent | source_content_item_id: … |
  calendar_item_title: …` plus the agent run id / product url /
  matched niche)

Multiple selected drafts are materialised **sequentially** so each
`version_number` resolution sees the prior insert (no race).

## Operator review flow

1. Brand Analysis agent run (Phase 1W/1Y) → persisted `agent_runs` row.
2. Calendar Agent (Phase 1Z) → draft `content_items`, each with
   `agent_run_id` provenance in `prompt_summary`.
3. **Phase 2A**: on the Calendar Agent panel success block, each created
   content item shows a **Create prompt draft** button →
   `createPromptDraftsForCalendarItemAction({ contentItemId })`.
4. The operator opens the returned prompt editor link, reviews/tightens
   every section, and only then uses the existing "Mark approved for
   generation" gate. The agent never short-circuits that gate.

## Safety boundaries

- Only writes `prompt_versions` rows, all `operator_editing`.
- No `generation_jobs` / `generation_batches` / `generated_assets` /
  `audio_fixer_jobs` created.
- No content item status change; `shared_with_client` /
  `client_safe_video_url` untouched.
- No Seedance / Enhancor / Audio Fixer / paid call / email.
- Operator-only. `web/app/client/**` imports nothing from this
  workflow; `prompt_versions` RLS is operator-only (migration 004).

## Live verification (Phase 2A)

Content item `2cb06509-3b4c-4a81-90c5-41e7c499750e` (Phase 1Z draft) →
`prompt_versions` `58a3abad-dc11-42a4-bdc8-a744ee3b4380`,
`version_number=1`, `status=operator_editing`,
`quality_tier=standard_720p`, `parent=null`,
`source_regeneration_request_id=null`. `draftSource=regenerated_from_url`
(the linked run had no `promptDrafts` so the deterministic URL fallback
fired). Content item stayed `draft`; `generation_jobs`,
`generated_assets`, `audio_fixer_jobs` byte-identical pre/post; every
prompt_version for the item is `operator_editing` (none approved).

## Bulk creation (Phase 2B)

`createPromptDraftsForCalendarItemsBulkAction(input)` (same file):

| field | required | notes |
|---|---|---|
| `contentItemIds` | yes | 1–50 UUIDs; de-duped before work. |
| `agentRunId` | no | Passed through to every per-item call. |
| `selectedDraftIndexes` | no | Passed through (default `[0]`). |
| `operatorNotes` | no | Passed through (≤500 chars). |

Behavior:

- Operator persona required; ids validated/de-duped up front (any
  malformed id rejects the whole batch before any write).
- Iterates the **single-item** `createPromptDraftsForCalendarItemAction`
  **sequentially** — each call independently re-validates editability +
  tenancy + provenance, so a bad item cannot poison a good one and
  `version_number` resolution per content item never races.
- No new write surface: the bulk action only ever produces the same
  `operator_editing` `prompt_versions` rows the single-item action does.

Partial-success contract (`BulkPromptDraftsResult`):

- `ok` = at least one item produced a draft (partial success is a
  useful, non-destructive outcome).
- `allSucceeded` = every requested item succeeded.
- `createdCount`, `failedCount`, and `perItem[]` (per-item ok / ids /
  editorHref / draftSource / error) so the UI can show exactly what
  landed and what didn't.
- A failing item NEVER aborts the rest, NEVER approves anything, NEVER
  creates a generation job.

### Bulk UI

The Calendar Agent success panel renders `BulkPromptDraftsPanel`:
checkbox list of every created content item (all selected by default),
a single "Create prompt drafts for N items" button, the same explicit
warning, and inline per-item results (created prompt_version ids +
draftSource, or the failure reason) plus a created/failed summary.

### Why outputs remain operator_editing (and why this is safe pre-generation)

Every insert still goes through the Phase 1X
`createPromptVersionFromAgentDraftAction`, whose only writable status is
`operator_editing`. Bulk changes the *fan-out*, not the *write
contract*. Nothing in this path can set `approved_for_generation`,
create a `generation_jobs` row, call a provider, or touch a client
surface — so running it across a whole materialised calendar is exactly
as safe as one item. Generation only ever happens later, behind the
existing separate "Mark approved for generation" operator gate.

### Proposed migration 009 (NOT applied)

Bulk targeting currently relies on parsing `agent_run_id:` out of
`content_items.prompt_summary`. That is robust enough for Phase 2B but
brittle long-term. The clean follow-up — additive, idempotent, same
pattern as migrations 007/008, **do not apply without explicit
approval**:

```sql
-- 009_content_items_source_agent_run.sql  (PROPOSAL ONLY)
alter table public.content_items
  add column if not exists source_agent_run_id uuid
  references public.agent_runs(id) on delete set null;

create index if not exists content_items_source_agent_run_idx
  on public.content_items (source_agent_run_id);
```

With that column, the Calendar Agent would set
`source_agent_run_id` directly and the bulk action could target "every
draft content item from agent run X" by FK instead of a `prompt_summary`
grep. No behaviour change is required until it is applied — the grep
path keeps working.

## Live verification (Phase 2A)

Content item `2cb06509-3b4c-4a81-90c5-41e7c499750e` (Phase 1Z draft) →
`prompt_versions` `58a3abad-dc11-42a4-bdc8-a744ee3b4380`,
`version_number=1`, `status=operator_editing`,
`quality_tier=standard_720p`, `parent=null`,
`source_regeneration_request_id=null`. `draftSource=regenerated_from_url`
(the linked run had no `promptDrafts` so the deterministic URL fallback
fired). Content item stayed `draft`; `generation_jobs`,
`generated_assets`, `audio_fixer_jobs` byte-identical pre/post; every
prompt_version for the item is `operator_editing` (none approved).

## Live verification (Phase 2B — bulk)

Bulk over `2cb06509-…` and `dce674ff-…`:

- `2cb06509-…` → `prompt_versions` `371d2ea7-f740-4ddb-b289-9abe64ea7e73`
  `version_number=2` (it already had v1 from Phase 2A) `operator_editing`.
- `dce674ff-…` → `prompt_versions` `61d6a3e8-eccb-4299-895a-9174dc254a71`
  `version_number=1` `operator_editing`.
- Both items stayed `draft`, `shared_with_client=false`,
  `client_safe_video_url=null`. Every prompt_version per item is
  `operator_editing` (none approved). `generation_jobs`,
  `generated_assets`, `audio_fixer_jobs` byte-identical pre/post.

## Future improvements

- Apply migration 009 `content_items.source_agent_run_id` (proposed
  above) — replace the `prompt_summary` grep with a real FK and let the
  bulk action target "all drafts from agent run X" directly.
- Multiple prompt variants per item in one click (the standard +
  label-strict drafts together).
- Quality-tier selection (currently fixed at `standard_720p`).
- An explicit, separate approval gate surfaced right after review (the
  recommended next phase — an operator review queue).
