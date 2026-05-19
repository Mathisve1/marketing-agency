# Claude Code Task Queue (Phase 2L)

Status: code shipped **fail-soft**; migration 010 written but **NOT
applied** (no programmatic DDL channel — operator runbook below).

## Purpose

Phase 2K let the operator generate a paste-ready Claude Code brief from
any Unified Inbox row (copy-only, no persistence). Phase 2L adds a
durable queue: the operator can **save** a prepared task into
`public.claude_code_tasks` and track it through `draft →
ready_for_claude → in_progress → completed | failed | cancelled`.

The dashboard ONLY saves and re-statuses these rows. It never executes
Claude Code, never calls the Claude/Anthropic API, never spawns a
process (`child_process`/`spawn`/`exec`), never runs a local worker,
never calls a paid API, never emails or publishes.

## Statuses

| status | meaning |
|---|---|
| `draft` | saved from the inbox; brief is prepared |
| `ready_for_claude` | operator promoted it; ready to run in a Claude Code session |
| `in_progress` | a Claude Code session is working it (operator-set) |
| `completed` | Claude wrote results back to Supabase; operator confirmed |
| `failed` | did not complete; needs attention |
| `cancelled` | soft-delete (no row is ever physically deleted) |

Risk levels carried from the inbox handoff: `info_only`, `read_only`,
`draft_write`, `gated_paid`.

## Manual copy/paste flow

```
/agency/inbox row
  └─ "Prepare Claude Code task"  (Phase 2K, client-side, copy-only)
       ├─ "Copy prompt"          → clipboard, paste into your session
       └─ "Save task"            → claude_code_tasks row, status=draft
            └─ "Mark ready for Claude" → status=ready_for_claude
/agency/claude-tasks
  └─ per row: "Copy prompt" + (if draft) "Mark ready"
       → operator runs Claude Code in their OWN session
       → Claude Code writes results back to Supabase
       → operator marks the task completed/failed  (next phase)
```

## Why no execution yet

The website never having an execution path is the entire safety model:
no API key on the web runtime, no process spawn, no worker, nothing
that can spend money or take an irreversible action without a human
running Claude Code in a session they control. The queue is a
to-do list with provenance, not a job runner.

## Security boundaries

- Operator-only. `claude_code_tasks` RLS mirrors `agent_runs`
  (migration 008): `app.is_workspace_member(workspace_id)` for
  select/insert/update; **no delete policy**. Client portals never see
  it; nothing under `web/app/client/*` imports the queue.
- Fail-soft: until migration 010 is applied, `claudeCodeTasksTableReady()`
  returns false, the data layer returns `[]`, the save action returns a
  clear "apply migration 010" message, and the inbox hides the Save
  button (copy-paste handoff is unaffected).
- The save/markReady actions only touch `claude_code_tasks` — no other
  table, no provider, no email.

## How results get written back

Claude Code (run by the operator via MCP) does the work and writes its
output to the relevant Supabase rows directly (e.g. an
`operator_editing` prompt_version, a `caption_draft`, an `agent_run`).
The operator then returns to `/agency/claude-tasks` and (in the
**next** phase) pastes a result summary and marks the task
`completed`/`failed`. Phase 2L stops at save + ready — there is no
"complete" UI yet by design.

## Migration 010 — operator runbook (NOT applied)

No programmatic DDL channel exists (Supabase MCP token expired; no
`DATABASE_URL` / DB password / Management API token; PostgREST is
DML-only). Apply it yourself in the **Supabase SQL editor**:

1. Paste the full contents of
   `supabase/migrations/010_claude_code_tasks.sql` and run it. It is
   additive, idempotent, and re-runnable (every statement guarded).
2. Verify (also in the SQL editor):
   ```sql
   select count(*) from information_schema.tables
    where table_schema='public' and table_name='claude_code_tasks';  -- 1
   select count(*) from information_schema.columns
    where table_schema='public' and table_name='claude_code_tasks'; -- 18
   select policyname from pg_policies
    where schemaname='public' and tablename='claude_code_tasks';
     -- claude_code_tasks_operator_select / insert / update
   notify pgrst, 'reload schema';
   ```
3. Tell me "migration 010 applied" and I will run the Phase 2L live
   test (save one inbox task, assert exactly one `claude_code_tasks`
   row created with `status=draft`, every other table byte-identical,
   then mark it `ready_for_claude`).

## Phase 2M — manual complete / fail / cancel (shipped)

After the operator runs a task in their own Claude Code session, they
record the outcome on `/agency/claude-tasks`:

- **`completeClaudeCodeTaskAction({ taskId, resultSummary })`** —
  allowed from `ready_for_claude` or `in_progress`. Sets
  `status='completed'`, `result_summary`, `error_message=null`,
  `completed_at=now()`. Requires a non-empty summary (≤8000 chars).
- **`failClaudeCodeTaskAction({ taskId, errorMessage })`** — same
  source statuses. Sets `status='failed'`, `error_message`,
  `completed_at=now()`. Requires a non-empty reason (≤8000 chars).
- **`cancelClaudeCodeTaskAction({ taskId })`** — soft-delete from
  `draft`/`ready_for_claude` → `cancelled`. No physical delete.

All three are status-guarded with `.in("status", [...])` so a
double-submit or wrong-state click is a no-op (returns "not in a
completable/failable status"), require the operator persona, and write
**only** `claude_code_tasks`.

### Manual completion flow

```
operator runs the task in their OWN Claude Code session
  → Claude Code does the work + writes results to the relevant
    Supabase rows directly (operator_editing prompt_version, caption,
    agent_run, …)
  → operator returns to /agency/claude-tasks, clicks "Record completion"
  → pastes Claude Code's reported summary → "Mark completed"
       status=completed, result_summary stored, completed_at set
```

### Manual failure flow

```
task could not complete (provider/billing/blocked/etc.)
  → operator clicks "Record failure" → pastes the error / reason
       status=failed, error_message stored, completed_at set
```

The UI shows a textarea + button only for `ready_for_claude` /
`in_progress` tasks; completed/failed tasks render their stored
`result_summary` / `error_message` + `completed_at` read-only. Every
control carries the warning "This only records the outcome. Run the
task manually in Claude Code first — the dashboard never executes it."

### Why this is still not a worker

Nothing here runs anything. `complete`/`fail`/`cancel` are pure status
+ text writes to one table. There is no claim, no run, no log stream,
no process — the operator is the executor, the dashboard is the
ledger. This keeps the zero-execution safety model intact while giving
the queue a real lifecycle.

### Live verification (Phase 2M)

- Existing `ready_for_claude` task
  `98957f53-7cd2-4017-99d3-228275d1ba52` → `completed`,
  `result_summary` set, `error_message=null`, `completed_at` set.
- Fresh task `ca172f72-…` → `failed`, `error_message` set,
  `completed_at` set, `result_summary=null`.
- All 9 non-`claude_code_tasks` tables byte-identical pre/post.

## Phase 2N — Claude Task Activity card on the OCC (shipped, read-only)

The Owner Command Center (`/agency`) now reflects queue health. This
phase adds **zero** writes, no schema change, no new server action.

`getClaudeCodeTaskSummaryForWorkspace(workspaceId)`
(`web/lib/data/claude-code-tasks.ts`) is a pure read-only derivation
over the already-fail-soft `listClaudeCodeTasksForWorkspace`
(`limit:500`). It returns: `tableReady`, `total`, per-status counts
(`draft`/`readyForClaude`/`inProgress`/`completed`/`failed`/`cancelled`),
`overdueReadyCount`, `overdueThresholdHours`, `latest`
(`{id,title,status,updatedAt}`), `recentTasks` (≤5), and
`failedRecentTasks` (≤5). Fail-soft: returns the empty summary
(`tableReady:false`, all zero) on demo or when migration 010 is not
applied — no throw.

**Overdue heuristic:** a `ready_for_claude` task whose `updatedAt` is
older than `OVERDUE_READY_HOURS = 24` counts as overdue (a prepared
task nobody has run yet). Threshold is exported so the UI can show it.

The OCC card (the existing `/agency/claude-tasks` link, upgraded)
renders a single health tone, never a control:

| condition | tone | label |
|---|---|---|
| migration 010 not applied | neutral | `queue` (copy-only blurb) |
| `failedCount > 0` | danger | `needs attention` |
| `overdueReadyCount > 0` | warn | `overdue` |
| `readyForClaudeCount > 0` | info | `ready to run` |
| tasks exist, none of the above | success | `healthy` |
| no tasks | neutral | `queue` |

When the table is ready it also shows ready/in-progress/completed/
failed (+overdue) count chips, the latest task title+status, and the
fixed disclaimer **"Tasks are manual. The dashboard does not execute
Claude Code."** It links to `/agency/claude-tasks`; it has no
run/claim/execute affordance.

**Why this stays safe:** the card is a pure read. The data layer issues
only `.select()` (no insert/update/upsert/delete/rpc), no
`child_process`/`spawn`/`exec`, no Claude/Anthropic API, no paid call,
no email, no publish, no client-portal import. Live-verified Phase 2N:
all 10 tables (claude_code_tasks + 9 side-effect) byte-identical
pre/post, delta 0 everywhere.

The inbox Claude-task signal was evaluated and **deferred (OCC-only)**:
`/agency/inbox` rows are composed from content/prompt/job state in
`owner-overview.ts`; a synthetic Claude-task row would mean extending
the `InboxItemKind` enum + composition pipeline, which exceeds a
read-only phase. OCC health is sufficient.

## Future

- Status sync via an MCP-side updater (operator-run, not a daemon).
- A local worker that claims `ready_for_claude` — explicitly out of
  scope until the manual lifecycle is trusted in production.
