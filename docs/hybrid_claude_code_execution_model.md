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

## Future (not in scope)

- `claude_code_tasks` table mirroring the task type (additive migration).
- An MCP tool that flips `in_progress`/`completed` as Claude Code works.
- Dashboard "Prepare task" action that snapshots context into a task row
  (still no auto-execution).
