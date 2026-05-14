# Operations runbook (solo operator)

Practical day-2 instructions for running the agency from a single laptop.
Linked from the project README. Treat this as the source of truth for
"how do I do X again" when the README's "Running the agency" section
isn't enough.

This document does NOT cover:
- hosted deployment (Docker / cloud) — out of scope
- multi-operator setup, auth, or RBAC — out of scope
- output-quality validation workflow — see SECURITY.md and the eval review
  framework documented in the README

Pass 2.1 added: persistent operator task store, daily summary script,
and Streamlit task-action buttons (Mark done / Dismiss / Snooze).
Pass 2.2 will add: cost ledger + provider instrumentation + cost dashboard.

---

## 1. Install (CI-parity path)

The lockfile is the canonical install. CI uses the same path; do this
locally and you'll catch dependency drift before pushing.

```bash
py -3.11 -m pip install -r requirements-lock.txt
py -3.11 -m pip install -e . --no-deps
py -3.11 -m pip check                # must report no broken requirements
```

`pip install -e ".[dev]"` (resolving from `pyproject` ranges) still
works for fast experimentation but may pick up versions CI hasn't seen.
Don't commit a change that only works under the unlocked install path.

Refresh the lockfile after editing `pyproject.toml`:

```bash
py -3.11 -m pip install --upgrade pip pip-tools
py -3.11 -m piptools compile pyproject.toml --extra dev -o requirements-lock.txt
```

The lockfile MUST be generated under Python 3.11 (CI runs 3.11).

---

## 2. Daily commands

### Run the Streamlit command center

```bash
py -3.11 -m streamlit run ui/app.py
```

Opens at `http://localhost:8501`. The Agency Overview tab is the first
thing you see; everything else is per-client work + the Grade output
form.

### Run the MCP server (Claude Desktop integration)

```bash
py -3.11 mcp_server.py
```

Wire it into `claude_desktop_config.json` per the README. Once Claude
Desktop is connected, prefer `manager_request(...)` over the legacy
tools — it's the central interface.

### Run doctor (no API calls, exit code is your signal)

```bash
py -3.11 scripts/doctor.py
```

`Result: 0 failure(s), N warning(s)` is healthy. Warnings about a
missing `.env` or unset env vars are expected when running outside
the Streamlit/MCP launchers.

### Run the test suite

```bash
py -3.11 -m pytest
```

Should be fast (≈25 s). If anything fails, do not push.

### Run the repo hygiene scanner

```bash
py -3.11 scripts/check_repo_hygiene.py --tracked   # CI does this on every push
py -3.11 scripts/check_repo_hygiene.py --staged    # pre-commit hook style
```

### Generate the daily summary (manual, no background process)

The daily summary is a one-shot CLI that syncs the inferred inbox into
the persistent task store and renders the open tasks as markdown grouped
by priority + category.

```bash
# Print to stdout (default)
py -3.11 scripts/daily_summary.py

# Write to a file (path is gitignored and hygiene-protected)
py -3.11 scripts/daily_summary.py --out reports/daily-summary-2026-05-14.md
```

There is intentionally **no background scheduler in the repo**. Wire it
to Windows Task Scheduler or cron yourself if you want a daily reminder.
Always `cd` into the repo first so `reports/` resolves to the right
place:

```bash
# cron (macOS / Linux), 09:00 local daily:
0 9 * * *  cd /path/to/Marketing_Agency && \
           /usr/bin/env py -3.11 scripts/daily_summary.py \
             --out reports/daily-summary-$(date +\%Y-\%m-\%d).md

# Windows Task Scheduler "Program/script":
#   py -3.11
# "Arguments":
#   scripts\daily_summary.py --out reports\daily-summary-%date%.md
# "Start in":
#   C:\Users\mathi\Desktop\Marketing_Agency
```

The script makes NO external API calls. Pass the path overrides
(`--db-path`, `--clients-root`, `--prospects-root`, `--mcp-db-path`,
`--eval-path`, `--today`) only for tests / debugging.

### Manage open tasks (mark done / dismiss / snooze)

Pass 2.1 introduced a persistent layer (`operator_tasks.db`) on top of
the inferred inbox. The operator can now act on individual tasks without
losing their decisions to the next inference sweep.

- **Streamlit:** *Agency overview* tab. Each task card renders with
  three buttons: **Mark done**, **Dismiss**, **Snooze 1 day**. Approval-
  category tasks render guidance text only — approvals still flow
  through the existing HITL surfaces (Streamlit HITL panel or MCP
  `resume_agency_workflow`). No generic Approve button on inbox cards.
- **Manager:** `manager_request("what do I need to approve today?")`
  reads from the persistent store after syncing — done / dismissed /
  snoozed tasks won't reappear.
- **Daily summary:** same store, same semantics.

Sync semantics in plain English:

- An inferred task that's NEW → inserted as `open`.
- An inferred task that already exists as `open` → content (title /
  description / priority / location) refreshed; status preserved.
- An inferred task already marked `done` or `dismissed` → NOT reopened.
  `inferred_seen_at` is bumped for audit.
- An inferred task that's `snoozed` and expired → resurfaces as `open`.
- A row whose inferred fingerprint disappears (job polled to completion,
  plan rejected, eval graded) → auto-closed as `done` with `resolved_at`
  set. Operator can `reopen_task` from the Manager / a REPL if needed.

The store does NOT replace the inferred inbox; the inbox is still the
source of truth for "what should be open right now".

---

## 3. Resetting runtime state safely

The agency stores runtime state in a few SQLite databases at the repo
root and inside each client silo. Resetting the runtime DBs is safe;
deleting per-client commercial data is not.

### Safe to delete (will be recreated on demand)

| Path | What it is | When to nuke |
|---|---|---|
| `checkpoints.db`, `*.db-shm`, `*.db-wal` | LangGraph SqliteSaver — paused supervisor checkpoints | When a paused workflow is wedged and you want a clean slate |
| `mcp_pending_runs.db`, `*.db-shm`, `*.db-wal` | Operator-facing MCP pending registry | When you've abandoned every paused MCP workflow and want to clear the table |
| `operator_tasks.db`, `*.db-shm`, `*.db-wal` | Persistent operator task store (Pass 2.1) | When you want to drop every operator decision (done / dismissed / snoozed) and re-sync the inferred inbox from scratch |
| `reports/daily-summary-*.md` | Daily summary exports (Pass 2.1) | Any time; regenerated on demand by re-running `scripts/daily_summary.py` |
| `clients/<id>/client_data.db-shm`, `*.db-wal` | SQLite WAL sidecars only | Almost never needed; SQLite manages these itself |

```bash
# Wipe ALL paused MCP workflows + checkpoints. Stop MCP first.
rm -f checkpoints.db checkpoints.db-shm checkpoints.db-wal
rm -f mcp_pending_runs.db mcp_pending_runs.db-shm mcp_pending_runs.db-wal

# Wipe operator decisions (Pass 2.1). The inferred inbox will re-populate
# the store on the next Streamlit / Manager / daily_summary call.
rm -f operator_tasks.db operator_tasks.db-shm operator_tasks.db-wal
```

### NEVER delete without intent

| Path | What it is | Consequence |
|---|---|---|
| `clients/<id>/client_data.db` | Client's winning hooks, motions, negative constraints, video plans, video jobs | Loses all the agency's accumulated work for that client |
| `clients/<id>/MASTER_CONTEXT.md` | Static client metadata (brand, locale, benchmarks) | Loses brand context |
| `clients/<id>/references/` | Customer-supplied character + product imagery | Loses customer assets |
| `clients/<id>/outputs/` | Generated MP4s + PDF reports | Loses delivered work |
| `prospects/<id>/audit.json` + `pitch.pdf` | Cold-outreach pipeline | Loses lead pipeline state |
| `evals/output_reviews.jsonl` | Manual quality grades | Loses your only ground-truth signal until automated evals exist |

### Deleting one client cleanly

```bash
# Confirm first
ls clients/<id>/

# Backup if at all unsure
cp -r clients/<id> /some/external/backup/

# Then remove
rm -rf clients/<id>
```

The Streamlit sidebar dropdown re-scans the `clients/` directory on
every page load, so the dropped client disappears from the UI without
restarting.

---

## 4. Backups (manual, weekly is plenty for solo use)

Tar these onto an external drive or cloud sync once a week:

```bash
tar -czf backup-YYYY-MM-DD.tgz \
    clients/ prospects/ evals/ \
    checkpoints.db mcp_pending_runs.db
```

The runtime DBs are optional in the backup — they're regenerated on
demand. The client/prospect/eval directories are not.

Do NOT commit the backup tarball to git.

---

## 5. Troubleshooting

### Dependency issue (pip check fails)

```bash
py -3.11 -m pip check
```

If a conflict appears, refresh from the lock:

```bash
py -3.11 -m pip install -r requirements-lock.txt --force-reinstall
py -3.11 -m pip install -e . --no-deps
```

If the conflict persists the lock itself is stale — refresh per
"Install" above and re-run `py -3.11 -m pytest` to confirm everything
still works under the new pins.

### Missing env var

`scripts/doctor.py` prints which env var blocks which integration:

```
TAVILY_API_KEY       -> tavily      (Strategist + Outreach competitor search)
APIFY_API_TOKEN      -> apify       (Strategist + Outreach FB ad-library scrape)
KLING_API_KEY        -> kling       (Producer paid video submission)
KLING_API_SECRET     -> kling       (Producer paid video submission)
META_ACCESS_TOKEN    -> meta-ads    (Analyst Insights pull)
META_AD_ACCOUNT_ID   -> meta-ads    (Analyst Insights account scoping)
ANTHROPIC_API_KEY    -> anthropic   (every agent's LLM calls)
```

Add the missing key to `.env` at the repo root (gitignored). Streamlit
+ MCP both load `.env` on startup via python-dotenv.

### Failed Kling job

Look in the Streamlit *Generated videos* tab — failed jobs render at
the top of that tab with the provider's error string verbatim. Common
causes:

- **MISSING_KEY** — `.env` is unset; see above.
- **TIMEOUT** — Kling may have accepted the request despite the local
  error. Check the Kling dashboard before re-approving the same plan
  to avoid duplicate spend.
- **PROVIDER_ERROR (5xx)** — usually transient; the plan is left in
  `pending_approval` so you can retry through the same approval channel.

### Stuck pending plan

Symptoms: plan sits in `pending_approval` for hours; Manager / overview
keeps surfacing it.

1. Open Streamlit → Agency Overview tab → "Pending approvals — where
   to act" panel. The channel hint tells you exactly where to act.
2. If the plan is no longer wanted: reject it from Streamlit (Run agent
   tab HITL panel "Reject - cancel") or via `manager_request("reject
   plan VP-X for client Y")`.
3. If the plan should run: approve via the indicated channel.

### Streamlit appears frozen during dispatch

A long Strategist or Producer turn (30–90s) renders as a spinner with
no progress text. Don't refresh — refreshing kills the dispatch. The
"do not refresh" warning above the spinner is your reminder.

If the page seems wedged for >2 minutes, open browser devtools → Network
to confirm the WebSocket is alive. If it's dead, restart Streamlit
(`Ctrl+C` then re-run); the LangGraph checkpoint survives the restart.

### MCP restart recovery

After restarting the MCP server, paused workflows survive (durable
checkpoint + durable pending registry, V1.7+). Resume by listing the
pending registry:

```python
# In a Python REPL with the project on PYTHONPATH:
from services import mcp_pending_store
for row in mcp_pending_store.list_pending():
    print(row["thread_id"], row["client_id"], row["plan_id"], row["source_channel"])
```

Then call `resume_agency_workflow(thread_id="...", approve=True/False)`
from Claude Desktop, or use `manager_request("approve plan VP-X for
client Y", confirm=True)`.

If a paused plan came from Streamlit (`source_channel='streamlit'`),
approve it in Streamlit — the Manager will refuse to cross-approve via
MCP without the right resume context.

---

## 6. Known limitations

- **No proactive notifications.** Pass 2.1 ships a manual daily summary
  script (`scripts/daily_summary.py`) you can wire to cron / Windows
  Task Scheduler. The agency itself never reaches out — Manager only
  answers when asked.
- **No cost ledger / dashboard yet** — Pass 2.2.
- **Anthropic LLM call costs are not instrumented** even after Pass 2
  ships the cost ledger. Token accounting requires LangChain callbacks
  and is a separate workstream.
- **Single-operator only.** No auth / RBAC / multi-tenant boundary.
  Anyone with shell access to the host can dispatch any client's
  agents and read every `client_data.db`.
