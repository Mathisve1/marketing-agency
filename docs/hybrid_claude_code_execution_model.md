# Hybrid Claude Code execution model (Phase 2D)

Status: concept + task model only. No execution wired.

## The shape

```
┌─────────────────────────┐      ┌──────────────────────┐      ┌──────────────┐
│  Dashboard (control)    │      │  Claude Code (worker)│      │  Supabase    │
│  - overview / tasks     │ ───▶ │  runs via MCP in an  │ ───▶ │  state bridge│
│  - next actions         │ task │  operator session    │ write│  agent_runs, │
│  - approvals / status   │ prep │  - complex workflows │ back │  content_*,  │
│  - prepares a task brief │      │  - research / copy   │      │  prompt_*    │
└─────────────────────────┘      └──────────────────────┘      └──────┬───────┘
            ▲                                                          │
            └───────────────── dashboard reflects results ◀────────────┘
```

- **Dashboard = control center.** It prepares a task (a brief + context
  links), tracks status, and reflects results once they land in
  Supabase.
- **Claude Code = execution worker.** A human runs Claude Code (via the
  MCP server) in a session on their machine. It does the heavy
  multi-step work and writes results back to Supabase.
- **Supabase = state bridge.** Neither side calls the other directly;
  state flows through the database the dashboard already reads.

## Why this (for now)

- **No API cost.** We do not call the Claude API from the website. The
  operator's existing Claude Code session does the work.
- **No daemon / no website→Claude bridge.** The website never spawns or
  triggers Claude Code. Lower attack surface, no always-on process.
- **Safety.** Every paid/irreversible step (Seedance, Audio Fixer,
  client send) stays behind the existing operator gates regardless of
  who prepared the task.

## Task object

Defined in `web/lib/tasks/claude-code-tasks.ts` (types + templates
only — **not persisted in Phase 2D**, no table):

- `kind`: competitor_research | organic_content_calendar |
  social_copy_generation | ugc_prompt_draft |
  audit_failed_generation | client_feedback_revision
- `status`: `draft` → `ready_for_claude` → `in_progress` →
  `completed` | `failed`
- `title`, `instructions` (the brief the operator pastes into Claude
  Code), optional `context` (brand/campaign/contentItem/agentRun ids)

A read-only **"Claude Code task handoff (concept)"** card on `/agency`
surfaces the templates. The "Prepare task" button is disabled — this
phase only defines the model.

## Status semantics

| status | meaning |
|---|---|
| `draft` | dashboard is still assembling the brief |
| `ready_for_claude` | brief is complete; operator can run it in Claude Code |
| `in_progress` | a Claude Code session is working it |
| `completed` | results written back to Supabase; dashboard reflects them |
| `failed` | did not complete; needs operator attention |

## Limitations (explicit)

- A computer / session **must be running Claude Code** — not automatic.
- It is **not** real-time and **not** suitable for client self-service.
- Status transitions are operator-driven until a future phase persists
  the task table + (optionally) an MCP-side updater.
- No website → Claude Code execution. No Claude API. No local daemon.
  These are deliberate non-goals for now.

## Phase 2K — Inbox row → Claude Code task (copy-only, shipped)

The Unified Inbox (`/agency/inbox`) now turns any row into a
paste-ready Claude Code brief.

### Flow

```
/agency/inbox  (server-rendered, read-only)
  └─ per row: buildClaudeCodeTaskForInboxItem(item)   ← pure, no I/O
        └─ <ClaudeTaskHandoffPanel> (client)          ← expand + clipboard
              └─ operator copies copyText into their own Claude Code
                 session → Claude Code does the work → writes results
                 back to Supabase → dashboard reflects them
```

`buildClaudeCodeTaskForInboxItem` (`web/lib/tasks/claude-code-tasks.ts`)
is a deterministic pure function. It type-imports `InboxItem` only (no
server data layer pulled into the client bundle), maps the item's
`kind` to a recipe, and returns a `PreparedClaudeCodeTask`:
`title, taskType, riskLevel, instructions, safetyRules[],
expectedOutputs[], relatedLinks[], copyText`.

`copyText` always contains: project path, branch, task type, risk
level, the inbox kind + title, parsed id refs
(`generation_job_id` / `regeneration_request_id` / `agent_run_id` /
`content_item_id`), the requested action, the universal hard-safety
block, expected outputs, and reporting requirements.

### Risk levels

| level | meaning |
|---|---|
| `read_only` | inspect/diagnose only (failed job, agent run, clip review) |
| `draft_write` | may write a draft (prompt_versions=operator_editing / caption_draft) — never approve/share |
| `gated_paid` | a paid step exists downstream; dry-run + estimate only, real run needs the exact approval phrase |
| `info_only` | nothing to do (client-approved; publish is manual) |

### Examples

- **failed_generation_job** → `audit_failed_generation`, `read_only`:
  diagnose payload/provider/billing/asset, do NOT resubmit.
- **copy_client_requested_changes** → `client_feedback_revision`,
  `draft_write`: revise copy internally, do NOT re-share.
- **prompt_draft_needs_review** → `ugc_prompt_draft`, `draft_write`:
  tighten the draft, keep status `operator_editing`.
- **approved_prompt_ready_for_generation** → `ugc_prompt_draft`,
  `gated_paid`: build the dry-run payload + credit estimate; no paid
  submit without the exact approval phrase.
- **agent_run_completed** → `organic_content_calendar`, `read_only`:
  inspect the run, propose next steps, execute none.

### Why copy-only is the safest first step

- The dashboard never spawns a process, never calls the Claude API,
  never touches the DB for this feature. The blast radius is a
  clipboard string.
- The operator stays in the loop: they read the brief, decide, and run
  Claude Code in a session they control. The universal safety block is
  embedded in every brief so the constraints travel with the task.
- No new schema, no new server action — nothing to roll back.

### Future path (not in scope)

- Persist prepared tasks: `claude_code_tasks` table mirroring the
  Phase 2D `ClaudeCodeTask` type (additive migration 010 — propose,
  do not apply without approval).
- "Mark ready_for_claude / completed / failed" status sync, driven by
  the operator first, later by an MCP-side updater.
- A local worker that claims `ready_for_claude` tasks. Out of scope
  until persistence + status sync are trusted.

### Limitations (unchanged)

- A computer / session **must be running Claude Code** — not automatic.
- It is **not** real-time and **not** suitable for client self-service.
- Status transitions are operator-driven until a future phase persists
  the task table + (optionally) an MCP-side updater.
- No website → Claude Code execution. No Claude API. No local daemon.
  No `child_process` / `spawn`. These are deliberate non-goals.

## Phase 2L — persistence (shipped fail-soft; migration pending)

The copy-only handoff (2K) now optionally persists to
`public.claude_code_tasks` (migration 010 — written, NOT yet applied).
Once an operator applies 010:

- Inbox "Save task" → `claude_code_tasks` row, `status='draft'`.
- "Mark ready for Claude" → `status='ready_for_claude'` (status
  transition only — executes nothing).
- `/agency/claude-tasks` lists the durable queue with copy-prompt +
  mark-ready, **no run/claim button, no worker**.

Until 010 is applied everything degrades fail-soft (Save hidden, queue
empty, copy-paste handoff unchanged). Full detail + runbook:
`docs/claude_code_task_queue.md`. The non-negotiable rule is unchanged:
the dashboard saves and re-statuses tasks; a human runs Claude Code and
it writes results back to Supabase.

## Phase 2M — manual complete / fail (shipped)

Migration 010 is applied. The lifecycle is now end-to-end **manual**:

```
save (draft) → mark ready (ready_for_claude)
            → [operator runs Claude Code themselves]
            → record completion (completed + result_summary)
              or record failure (failed + error_message)
            → or cancel (cancelled, soft-delete) from draft/ready
```

`completeClaudeCodeTaskAction` / `failClaudeCodeTaskAction` /
`cancelClaudeCodeTaskAction` write **only** `claude_code_tasks`
(status + result/error + completed_at), are status-guarded, and
execute nothing. There is still NO website→Claude execution, NO Claude
API, NO `child_process`/`spawn`, NO worker. The operator pastes Claude
Code's own reported summary; the dashboard is purely the ledger.

## Phase 2N — Claude Task Activity card on the OCC (shipped, read-only)

The Owner Command Center (`/agency`) now reflects queue health with
**zero** writes, no schema change, no new server action.
`getClaudeCodeTaskSummaryForWorkspace(workspaceId)` is a pure
derivation over the fail-soft reader: per-status counts, an
`overdueReadyCount` (`ready_for_claude` whose `updatedAt` is older than
`OVERDUE_READY_HOURS = 24`), `latest`, `recentTasks`,
`failedRecentTasks`. The card shows one health tone — danger
`needs attention` (failed > 0) ▸ warn `overdue` (overdue > 0) ▸ info
`ready to run` (ready > 0) ▸ success `healthy` ▸ neutral `queue` — plus
count chips, the latest task, a link to `/agency/claude-tasks`, and the
fixed disclaimer **"Tasks are manual. The dashboard does not execute
Claude Code."** It has no run/claim/execute affordance. Fail-soft: the
empty summary when migration 010 is unapplied or in demo. The data
layer issues only `.select()` — no write, no exec/spawn, no Claude
API, no paid call, no email, no publish, no client-portal import.
Live-verified: all 10 tables byte-identical pre/post (delta 0). The
inbox Claude-task signal was deferred (OCC-only) to keep the phase
strictly read-only. Full detail: `docs/claude_code_task_queue.md`.
