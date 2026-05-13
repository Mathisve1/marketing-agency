# marketing-agency

A multi-tenant AI performance-marketing agency built on LangGraph. A single
supervisor graph routes each operator request to one of four worker agents,
each scoped to a per-client SQLite silo:

| Worker     | Purpose                                                              | Client-scoped? |
|------------|----------------------------------------------------------------------|----------------|
| Strategist | Competitor scraping + winning-hook extraction (Tavily, Apify, LLM). | Yes            |
| Producer   | Compiles a video brief into a SQL-backed plan, submits to Kling.    | Yes            |
| Analyst    | Pulls Meta Ads insights, derives negative constraints for the brand.| Yes            |
| Outreach   | Discovers prospects + generates pitch PDFs (no client silo).        | No             |

The Producer is gated by a **Human-in-the-Loop (HITL) approval** before any
paid Kling API call: the supervisor graph is compiled with
`interrupt_before=["producer_submit"]`, and the compiled plan (exact prompt
and asset references) is loaded from SQL and shown to the operator before
they approve or reject.

The same compiled graph and the same HITL service back both the Streamlit
UI (`ui/app.py`) and the MCP server (`mcp_server.py`) consumed by Claude
Desktop — so the approval/reject codepath is reviewed in one place.

---

## Local setup

Requires **Python 3.11+**. The repo is a standard PEP 621 project (no Poetry).

```bash
# 1. Create + activate a virtualenv (any tool: venv, uv, virtualenv...)
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # macOS / Linux

# 2. Install in editable mode with dev extras (ruff, pytest, pip-tools)
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

# 3. Create your .env from the example and fill in keys
cp .env.example .env           # macOS / Linux
# copy .env.example .env       # Windows

# 4. Seed the empty template asset directories (idempotent)
python scripts/bootstrap_env.py

# 5. Verify the environment looks healthy (no API calls made)
python scripts/doctor.py
```

### Environment variables

All keys are read from `.env` at the repo root. See
[`.env.example`](.env.example) for the full list. The minimum set required
for the agents to start:

| Variable             | Used by              |
|----------------------|----------------------|
| `ANTHROPIC_API_KEY`  | All agents (LLM)     |
| `TAVILY_API_KEY`     | Strategist (web)     |
| `APIFY_API_TOKEN`    | Strategist / Outreach|
| `KLING_API_KEY`      | Producer             |
| `KLING_API_SECRET`   | Producer             |
| `META_ACCESS_TOKEN`  | Analyst              |
| `META_AD_ACCOUNT_ID` | Analyst (default)    |

Per-client overrides (for example, a different Meta ad account per client)
go in `clients/<client_id>/.env`, which is also gitignored. The root `.env`
is the fallback.

---

## Running the agency

### Streamlit UI

```bash
streamlit run ui/app.py
```

The UI handles client onboarding, task dispatch (research / produce /
analyze / outreach), and the HITL approve / reject screen for paused
Producer runs. The compiled supervisor graph is cached in
`st.session_state` per browser session.

### MCP server (Claude Desktop integration)

```bash
python mcp_server.py
```

Exposes two tools:

- `run_agency_agent(prompt, client_id=None, task_type=None, model=None)` —
  dispatches the supervisor. When routing resolves to the Producer it
  returns a `Workflow paused` message containing the `thread_id` and the
  exact compiled plan.
- `resume_agency_workflow(thread_id, approve)` — approves the paused
  Producer run (spending Kling credits) or rejects it (no spend).

To wire it into Claude Desktop add a block to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "marketing-agency": {
      "command": "python",
      "args": ["C:/path/to/Marketing_Agency/mcp_server.py"]
    }
  }
}
```

`.env` is loaded automatically from the repo root before any agent module
imports, so you do not need to duplicate API keys in the host config's
`env` block.

### CLI (debug)

Most ad-hoc dispatch happens through Streamlit or MCP. For a one-shot
non-interactive run during debugging, you can drive the graph directly:

```python
import asyncio
from core.supervisor import build_supervisor_graph, initial_state, run_supervisor_async

graph = build_supervisor_graph()  # no HITL gate -> Producer will submit
state = initial_state(client_id="acme", user_message="Run analyst")
result = asyncio.run(run_supervisor_async(graph, state))
print(result)
```

### Tests

```bash
python -m pytest
```

The full suite is fast (a few seconds) and runs entirely offline — every
external API (Anthropic, Tavily, Apify, Kling, Meta) is mocked at the
SDK / `requests` boundary.

### Doctor

```bash
python scripts/doctor.py
```

Checks repo structure, `.env` presence (not contents), required env-var
*names*, the `clients/_template/` template, an end-to-end client
onboarding probe in a tmpdir, and the LangGraph checkpoint config. Never
makes a real network call. Exit code is non-zero iff at least one hard
check fails.

---

## Runtime data — DO NOT COMMIT

Several directories are written by the running agents and contain
**customer assets, scraped competitor intelligence, model audit trails,
and per-client commercial context**. They are gitignored and must stay
that way. See [SECURITY.md](SECURITY.md) for the data-handling policy
before adding or modifying ignore rules.

| Path                                 | Contents                                |
|--------------------------------------|-----------------------------------------|
| `clients/<id>/client_data.db`        | Per-client SQLite (winning hooks, plans, jobs, constraints) |
| `clients/<id>/.env`                  | Per-client API overrides                |
| `clients/<id>/references/`           | Customer-supplied character/product/referral assets |
| `clients/<id>/outputs/`              | Generated videos + reports              |
| `clients/<id>/performance_log.json`  | Performance audit trail                 |
| `prospects/`                         | Scraped Meta ad-library data, pitch PDFs|
| `logs/`                              | Kling request/response audit logs       |
| `checkpoints.db`                     | LangGraph SqliteSaver (HITL/in-flight)  |

The `clients/_template/` directory IS committed (the template skeleton)
and should never contain anything sensitive.

---

## Dependency management

Two layers:

1. **`pyproject.toml`** declares acceptable version *ranges* — known-good
   lower floor + upper bound on the next likely-breaking major (or minor
   for pre-1.0 SDKs whose minor bumps routinely break — `langchain-*`,
   `langgraph`).
2. **`requirements-lock.txt`** pins the *exact* resolved versions used in
   CI and recommended for local development. Regenerated whenever
   `pyproject.toml` deps change.

CI installs from the lockfile, then installs the local project with
`--no-deps` so `pyproject` ranges cannot silently upgrade anything the
lock pinned.

### Local install (recommended: from the lock)

```bash
python -m pip install -r requirements-lock.txt
python -m pip install -e . --no-deps
python -m pip check          # must report "No broken requirements found."
```

`pip install -e ".[dev]"` (resolving from `pyproject` ranges) still
works for fast experimentation, but you may pick up versions CI hasn't
seen.

### Refreshing the lockfile

The lockfile **must** be generated under Python 3.11 — same version CI
runs. Resolving under 3.13 / 3.14 produces wheels and environment-marker
choices that 3.11 cannot necessarily install.

```bash
# Use the Windows py launcher to pick 3.11 explicitly:
py -3.11 -m pip install --upgrade pip pip-tools
py -3.11 -m piptools compile pyproject.toml --extra dev -o requirements-lock.txt
```

On macOS / Linux the equivalent is `python3.11 -m piptools compile …`.

After regenerating, re-run the verification gate before committing the
new lock:

```bash
py -3.11 -m pip install -r requirements-lock.txt
py -3.11 -m pip install -e . --no-deps
py -3.11 -m pip check
py -3.11 -m ruff check .
py -3.11 -m pytest
py -3.11 scripts/doctor.py
```

### When to refresh

- Any time `pyproject.toml` `dependencies` or `[dev]` extras change.
- When a security advisory hits one of the locked packages
  (`pip-audit -r requirements-lock.txt` is a good periodic check).
- When CI starts failing on an upgraded transitive (rare with the lock
  in place — almost always means a maintainer changed `pyproject` and
  forgot to refresh the lock).

### Why no `--generate-hashes`?

Adds 800-1000 lines to the lockfile, slows CI installs noticeably, and
complicates the manual refresh workflow above. Worth adding in a
follow-up if/when supply-chain pinning becomes a hard requirement.

### Why not `uv`?

`uv` is faster and has a `--python` flag that targets a Python version
without needing it locally. We standardised on `pip-tools` because it
is already in `[dev]`, every contributor already has `pip` installed,
and the resolver bug fixed by the lockfile (a leftover
`langgraph-prebuilt 1.x` clashing with `langchain-core 0.3.x`) needs
nothing more sophisticated. Revisit if/when supply-chain or speed
become bottlenecks.

---

## Known limitations

These are documented gaps in the current build, NOT bugs to file:

- **MCP pending-runs registry is in-memory** (`_PENDING_RUNS` in
  `mcp_server.py`). If the MCP server process restarts while a Producer
  approval is paused, the registry entry is lost. The underlying LangGraph
  checkpoint survives (it lives in `checkpoints.db` via the SqliteSaver,
  V1.5), but the operator-facing context — prompt, client_id, chosen
  model — would have to be reconstructed manually. A future pass should
  persist `_PENDING_RUNS` alongside the checkpoint.

- **Streamlit dispatch is async-plumbed but not streamed**: the UI calls
  `asyncio.run(run_supervisor_async(...))` and renders the final result
  in one shot. Per-node progress streaming is not implemented; long
  Strategist or Producer runs appear to "hang" until the graph returns.

- **`langgraph-prebuilt` ↔ `langchain-core` resolver warning** is
  intentionally deferred. `langgraph-prebuilt 1.1.x` requests
  `langchain-core >= 1.3.1` but the repo is pinned to `langchain-core
  < 0.4` to keep `langchain-anthropic` aligned. The current install
  surfaces a `pip` dependency-resolver WARNING; runtime behaviour is
  unaffected. Fixing it cleanly requires the dedicated dependency pass
  above.

- **No authentication or RBAC**: anyone with shell access to the host
  can dispatch any client's agents, approve Producer runs, and read
  every `client_data.db`. Suitable for single-operator use only.

- **No hosted deployment recipe**: the only documented run modes are
  local Streamlit and local MCP. Containerisation, secret management,
  and multi-process Streamlit are out of scope for V1.

---

## Repository layout (top-level)

```
agents/        Worker agents (strategist, producer, analyst, outreach)
core/          Supervisor, client context, schema, router, model registry
services/      Cross-cutting services (HITL approve/reject codepath)
ui/            Streamlit app
clients/       Per-client silos. _template/ is committed; others are gitignored.
scripts/       Operational scripts (bootstrap_env.py, doctor.py)
tests/         pytest suite (offline; mocks every external API)
mcp_server.py  MCP entrypoint for Claude Desktop
pyproject.toml Dependencies + ruff/pytest config
SECURITY.md    Data-handling policy
```
