# Calendar Agent — draft content items from an agent run

Status: Phase 1Z. Turns a completed Brand Analysis agent run into draft
`content_items` rows. Deterministic, draft-only, no schema change.

## Purpose

A completed Brand Analysis run already contains `contentCalendarIdeas`
(6 day-offset entries: D+0 concept lock → D+14 restock). Phase 1Z lets
the operator materialise the selected ideas as real, schedulable draft
content items that show up on the campaign calendar — without writing a
prompt, queuing a job, or touching the client.

## Input — agent_run requirements

`createDraftContentCalendarFromAgentRunAction(input)`:

| field | required | notes |
|---|---|---|
| `agentRunId` | yes | Must be a UUID. |
| `campaignId` | yes | Draft items attach here. |
| `selectedDayOffsets` | yes | Integer D+N values; each must exist in the run's output. |
| `startDate` | no | `yyyy-MM-dd`; defaults to today (UTC). `scheduled_for = startDate + dayOffset days`. |
| `operatorNotes` | no | ≤500 chars; appended to every row's `prompt_summary`. |

Validation gates (all must pass or the action returns `{ ok: false }`):

1. operator persona (demo mode is explicitly rejected — Supabase only).
2. `agent_run.agent_type === 'brand_analysis_ugc_prompt_planning'`.
3. `agent_run.status === 'completed'`.
4. `agent_run.output.contentCalendarIdeas` is a non-empty array.
5. Every selected `dayOffset` exists in that array.
6. Campaign exists and its `brand.workspace_id === agent_run.workspace_id`
   (tenancy guard — you cannot materialise one workspace's run into
   another workspace's campaign).

## Output — draft content_items

One `content_items` row per selected idea:

| column | value |
|---|---|
| `campaign_id` | the selected campaign |
| `title` | the idea's `label` |
| `status` | **`draft`** (hard-coded) |
| `scheduled_for` | `startDate + dayOffset` (UTC date) |
| `caption_draft` | the idea's `brief` |
| `prompt_summary` | structured provenance block (see below) + the brief |
| `hook_source` | `ai_suggested` |
| `platforms` | `[]` (DB default — operator sets later) |
| `quality_tier` | `standard_720p` (DB default) |
| `shared_with_client` | **`false`** (DB default, never set true here) |
| `client_safe_video_url` | **`null`** (never written) |
| `client_safe_poster_url`, `internal_*`, `cost_*`, `audio_fixer_*` | untouched / null / default |

No `content_calendar_id` is set (the legacy `content_calendars` join is
optional and unused by this flow).

## How provenance is stored

No schema change. The agent linkage lives in `prompt_summary` as a
structured prefix, mirroring the Phase 1X `prompt_versions.notes`
convention:

```
[agent draft] source: brand_analysis_ugc_prompt_planning
agent_run_id: <uuid>
matched_niche: <niche>          (if present in output)
product_url: <url>              (if present in input)
calendar_day_offset: <N>
status: draft — created from a deterministic agent run.
operator must verify, schedule, and approve before any client share.

<the idea brief>

[operator note] <notes>         (if operatorNotes given)
```

`agent_run_id:` is greppable, so a follow-up phase can trace a content
item back to its source run without a new FK column. If a real FK is
wanted later, migration 009 (`content_items.source_agent_run_id uuid
references agent_runs(id) on delete set null`) is the additive,
idempotent path — not needed for Phase 1Z.

## Safety boundaries

- **Draft-only.** The only `status` the action writes is `draft`.
- **Never client-facing.** `shared_with_client` stays false,
  `client_safe_video_url` stays null. The `content_items_shared_flag_consistent`
  CHECK constraint guarantees a draft can never be in a client-visible
  status.
- **No generation.** No `generation_jobs`, `generation_batches`,
  `generated_assets`, or `audio_fixer_jobs` row is created.
- **No prompt.** No `prompt_versions` row is created or modified
  (creating prompt drafts for a calendar item is the *next* phase).
- **No provider / no email.** Zero Seedance / Enhancor / Audio Fixer /
  SMTP calls.
- **Operator-only.** The action requires the operator persona. The
  client portal imports nothing from `web/lib/actions/calendar-agent.ts`
  or `web/components/agents/calendar-agent-panel.tsx`.

## Client visibility rules

A draft content item is invisible to `/client/[portalSlug]` because the
client view only projects rows in the client-visible status set
(`shared_with_client` / `approved_by_client` / `changes_requested_by_client`)
through `client_content_items_v`. A Phase 1Z row is `draft`, so it never
surfaces until an operator explicitly progresses + shares it through the
existing gated flow.

## UI

`/agency/agents/brand-analysis`:

- **Fresh run:** the `CalendarAgentPanel` renders under "Content
  calendar ideas" once the run is persisted (`agentRunId` present).
- **Recent runs:** every completed run with `contentCalendarIdeas`
  gets an inline `CalendarAgentPanel`.
- Panel: campaign selector, start-date picker, per-idea checkboxes
  (all selected by default), optional operator note, explicit warning
  "This creates draft content items only. It does not generate videos
  or share with the client.", and on success the created ids + a link
  to the campaign calendar.

Owner Command Center: a generic priority-3 next action
`create_content_calendar_from_agent_run` is emitted for up to the 3
most-recent completed runs (it does not track whether items were
already created — that would need new schema).

## Live verification (Phase 1Z)

Agent run `40388f71-ef68-46fa-a1a7-a3f94b8f126f` (completed, 6 ideas) →
materialised D+0 and D+3:

- `content_items` `2cb06509-…` and `dce674ff-…` created,
  `status=draft`, `shared_with_client=false`,
  `client_safe_video_url=null`, `scheduled_for` = 2026-05-19 / 2026-05-22,
  `prompt_summary` contains `agent_run_id: 40388f71-…`.
- `generation_jobs`, `prompt_versions`, `generated_assets`,
  `audio_fixer_jobs` all byte-identical pre/post.

## Future improvements

- Calendar drag-and-drop reschedule on the campaign calendar page.
- "Create prompt drafts for calendar item" — attach one or more
  `prompt_versions` (status `operator_editing`) per draft calendar item
  (the recommended next phase).
- Track which agent runs already produced calendar items (migration
  009 `content_items.source_agent_run_id`) so the Owner Command Center
  next action can suppress already-materialised runs.
- Weekly client calendar email (behind the existing typed-confirmation
  send gate; never auto-sent).
- Publishing integrations (per-platform, behind OAuth + review).
- Per-calendar-item approval workflow before generation.
