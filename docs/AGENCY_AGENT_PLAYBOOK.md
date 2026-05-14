# Agency agent playbook (operator field guide)

How to talk to the Manager Agent and the four sub-agents (Outreach,
Strategist, Producer, Analyst) so the system actually does what you
want. Copy-paste prompts, fill in the placeholders, send.

This document is **practical**, not theoretical. The architecture is
documented elsewhere (README, OPERATIONS.md, SECURITY.md). This file is
the daily reference you keep open while running the agency.

---

## Golden rules

1. **Talk to the Manager first.** `manager_request("...")` (in MCP /
   Claude Desktop) is the central interface. Reach for the legacy tools
   (`run_agency_agent`, `resume_agency_workflow`) only when the
   Manager's guidance tells you to, or you're scripting.
2. **Be specific. Vague prompts get clarification questions.** Always
   include the client_id when the work is client-scoped. Always name the
   verb (start, run, approve, reject, where, show).
3. **Producer ALWAYS pauses before paid Kling submission.** No prompt,
   no flag, no agent will bypass HITL. If you want a video, you ask
   Producer to **plan**; you then approve in the surface that holds the
   resume context (Streamlit HITL panel or MCP `manager_request("approve
   plan VP-X for client Y", confirm=True)`).
4. **Reference videos guide motion / pacing / camera — not copying.**
   The Kling Omni model uses `<<<video_1>>>` as a STYLE reference, not
   a frame source. Your character + product imagery is what gets
   rendered.
5. **Long-running ads = signal, not proof.** When Strategist says a
   competitor's hook is "long-running", that's a candidate worth
   testing, not a guaranteed winner. Phrase customer-facing language
   accordingly.
6. **Approval surface follows pause channel.** A plan paused via MCP
   approves via MCP. A plan paused via Streamlit approves via Streamlit.
   The Manager refuses to cross-approve without the right resume context
   — that refusal is the safety net working, not a bug.
7. **Grade the output.** After every real run, take 30 seconds to grade
   the artifact via the Streamlit *Grade output* tab or `python
   scripts/eval_review.py`. Until automated evals exist, your grades
   are the only ground-truth signal.

---

## What to always include in a prompt

When you talk to the Manager (or a sub-agent directly), include:

| Field | Why |
|---|---|
| **Verb** ("start", "run", "approve", "reject", "show", "where") | Tells the classifier which intent bucket. |
| **Scope** (client_id OR prospect_id OR "all clients") | Tells the Manager which silo to act on. |
| **Subject** (which work product / hook / plan / niche / market) | Tells the sub-agent what to operate on. |
| **Constraint** (count, budget, timeframe, must-have, must-NOT) | Tells the sub-agent where to stop. |

Example, complete: *"Run Strategist for client `acme`. Target market:
Belgium fitness apparel. Find 5–8 candidate hooks from competitors that
have been running ≥30 days."*

Example, vague (will get a clarification): *"Do strategist."*

---

## Vague prompts to AVOID

| Don't say | Why it fails | Say instead |
|---|---|---|
| "Do something" | No verb, no scope, no subject. Manager → `clarify`. | "Show me what's open for client `acme`." |
| "Check the videos" | Which client? Which job status? Failed or pending? | "Show failed Kling jobs for client `acme`." |
| "Run the producer" | No client, no plan inputs. Producer would refuse. | "Create a video plan for client `acme` using hook `WH-003` and the default character + product." |
| "Approve" | No plan_id. Manager → `clarify`. | "Approve plan `VP-004` for client `acme`." |
| "Find leads" | No niche, no country. Outreach scrapes blindly. | "Start prospecting for fitness apparel brands in Belgium, target 5 prospects." |
| "Why is it slow?" | Not actionable. Manager → `clarify`. | "Show pending jobs older than 30 minutes for client `acme`." |

---

## Daily overview

The first prompt of your day. The Manager reads from the operator
inbox + persistent task store + MCP pending registry and tells you
exactly what's open.

```
What do I need to approve today?
```

Variants the Manager understands (all classify the same way):

```
What is waiting on me?
What should I do next?
Give me my daily overview.
Show open tasks.
Show me what's open right now.
```

Per-client variant:

```
Show tasks for client [CLIENT_ID].
Give me the [CLIENT_ID] overview.
What is waiting for me on [CLIENT_ID]?
```

Or run the daily summary script (writes markdown to disk):

```bash
py -3.11 scripts/daily_summary.py --out reports/daily-summary-$(date +%Y-%m-%d).md
```

---

## Onboarding a new client

There's no Manager prompt for this — onboarding is a one-time UI
action that creates the client silo + SQLite database.

**Streamlit route (recommended):**

1. `py -3.11 -m streamlit run ui/app.py`
2. Sidebar → expand *Onboard a new client*
3. Fill in:
   - **client_id** — lowercase, alphanumeric + `-`, max 64 chars,
     starts with `[a-z0-9]`. Examples: `acme`, `gymshark`,
     `nuun-coffee`. **Cannot be `_template`** (reserved).
   - **Display name** — what the agent prompts will call them.
   - **Locale** — e.g. `nl-BE` (default), `en-GB`, `en-US`. Used by the
     Strategist to scope ad-library scrapes.
4. Click **Create silo**. The page reruns; the new client appears in
   the dropdown.

After onboarding:

1. Drop reference assets into the new `clients/[CLIENT_ID]/references/`
   folder:
   - `references/characters/` — UGC creator face / persona images
   - `references/products/` — product hero images
   - `references/referral_videos/` — competitor videos used as motion
     references (optional, only if you want video-style guidance)
2. Open `clients/[CLIENT_ID]/MASTER_CONTEXT.md` and fill in:
   - **Brand voice attributes** (e.g. "practical", "value-first")
   - **Forbidden terms** (e.g. "cheap", "discount-only" — these become
     the Kling negative_prompt)
   - **Performance benchmarks** if you already know your ROAS/CTR
     targets (the Analyst reads these from frontmatter)
3. Now you can run Strategist / Producer / Analyst against this client.

---

## Outreach research (find new prospects)

Use this when you want to **discover brands you don't yet have as
clients** and produce a Brand Audit + Pitch PDF for cold outreach.

**Prompt template:**

```
Start prospecting for [NICHE] brands in [COUNTRY_CODE]. Target [N]
prospects. Focus on long-running ad creative as the signal.
```

**Concrete example:**

```
Start prospecting for fitness apparel brands in BE. Target 5 prospects.
Focus on long-running ad creative as the signal.
```

**Phrasings the Manager understands** (all route to Outreach):

```
Find new leads in fitness apparel for Belgium.
Run outreach for skincare brands in NL.
Start prospecting for [NICHE] brands in [COUNTRY_CODE].
```

**What happens:**

1. Outreach uses Tavily to discover candidate brand names in the niche.
2. For each brand (capped at `MAX_APIFY_CALLS_PER_RUN=5`), Apify scrapes
   the top 10 long-running Meta ads.
3. Outreach analyzes text + metadata only (it cannot see videos).
4. For each prospect, writes:
   - `prospects/[PROSPECT_ID]/audit.json` — strategic gaps + candidate
     hooks + referral motions, each with evidence + confidence.
   - `prospects/[PROSPECT_ID]/pitch.pdf` — Brand Audit & Pitch PDF.
5. Returns the list of prospect_ids + paths.

**Hard caps you cannot exceed in one run** (cost-control invariants):

| Cap | Value | Why |
|---|---|---|
| `MAX_APIFY_CALLS_PER_RUN` | 5 | Each Apify scrape is a paid call |
| `MAX_TAVILY_CALLS_PER_RUN` | 2 | Each Tavily search is a paid call |

To audit more than 5 prospects in a single sitting, run the prompt
again — fresh outreach turn = fresh per-run counters.

---

## Reviewing a prospect (before sending the pitch)

After Outreach drops a prospect, you grade the audit + pitch BEFORE
emailing the prospect.

**Find the artifact:**

```
Where is the pitch PDF for prospect [PROSPECT_ID]?
```

The Manager returns a path like `prospects/[PROSPECT_ID]/pitch.pdf`.

**Open the pitch + audit:**

- `prospects/[PROSPECT_ID]/audit.json` — read this first; check that
  every weakness has at least one piece of evidence (`ad_archive_id`,
  body_text excerpt, distribution count, etc.) and confidence is
  honestly set. The Outreach prompt requires it but it's worth a
  human eye.
- `prospects/[PROSPECT_ID]/pitch.pdf` — open in any PDF viewer. Look
  for: factual claims, brand voice, the framework summary, and the
  CTA. If the pitch reads as overclaiming ("proven", "winning"),
  reject it (Outreach was instructed to use "candidate" /
  "longevity signal" framing).

**Grade it:**

Streamlit → *Grade output* tab → fill the form. Or:

```bash
py -3.11 scripts/eval_review.py \
    --agent outreach \
    --prospect-id [PROSPECT_ID] \
    --output-type pitch_pdf \
    --source prospects/[PROSPECT_ID]/pitch.pdf
```

(Interactive: prompts for specificity / accuracy / usefulness /
sendable / notes.)

**Promote to client (if the prospect signs):**

In Streamlit → Lead generation mode → *Prospects* tab → expand the
prospect → fill out the *Onboard + seed from audit* form. This copies
the audit's winning_hooks + referral_motions into a new client silo.

---

## Strategist research (per-client market work)

Use this for an **existing client**: scrape competitors, extract
candidate hooks, persist them into the client's `winning_hooks` table,
and produce a Market Analysis PDF.

**Prompt template:**

```
Run Strategist for client [CLIENT_ID]. Target [MARKET]. Find 5–10
candidate hooks from long-running competitor creative (≥14 days).
```

**Concrete example:**

```
Run Strategist for client acme. Target Belgian fitness apparel. Find
5–10 candidate hooks from long-running competitor creative (≥14 days).
```

**Phrasings the Manager understands:**

```
Research competitors for client [CLIENT_ID].
Look into the market for client [CLIENT_ID].
Find candidate hooks for client [CLIENT_ID].
```

**What happens:**

1. Strategist uses Tavily to discover 3–5 competitors (only if you
   didn't name them).
2. Apify scrapes their Meta ad libraries.
3. `score_ads_by_longevity(min_days=14)` filters out ads below the
   longevity-signal threshold.
4. For each recurring pattern (not each ad), Strategist calls
   `record_winning_hook` — persists into `client_data.db`.
5. For visually distinctive video ads, also `record_referral_motion`.
6. Generates `clients/[CLIENT_ID]/outputs/reports/market-analysis-*.pdf`.
7. Appends a 2–4 sentence summary to MASTER_CONTEXT.md's Recent
   Strategic Notes.

**Grade afterwards:**

```
Streamlit → Grade output tab → agent=strategist, output_type=pdf_report,
source=clients/[CLIENT_ID]/outputs/reports/market-analysis-*.pdf
```

---

## Video planning (Producer — the safe path)

This is the path for **creating a new video**. Producer is split into
two LangGraph nodes by design: **plan** runs first (compiles a
deterministic Kling brief, persists as `pending_approval`), then the
graph **pauses** before any Kling call, then you **approve**, then the
**submit** node runs (the only place that calls Kling).

You never bypass that pause.

### Step 1 — Tell Producer to plan

**Prompt template (no reference video):**

```
Create a video plan for client [CLIENT_ID]. Use hook [HOOK_ID]
([WH-XXX]), character `references/characters/[CHARACTER_FILENAME]`,
and product `references/products/[PRODUCT_IMAGE_FILENAME]`. Duration
[N] seconds, aspect ratio 9:16.
```

**Concrete example:**

```
Create a video plan for client acme. Use hook WH-003, character
references/characters/sarah_outdoor.png, and product
references/products/leggings_black.jpg. Duration 10 seconds, aspect
ratio 9:16.
```

**Prompt template (with reference video for motion guidance):**

```
Create a video plan for client [CLIENT_ID]. Use hook [HOOK_ID], motion
[MOTION_ID] ([RM-XXX]), character
`references/characters/[CHARACTER_FILENAME]`, and product
`references/products/[PRODUCT_IMAGE_FILENAME]`. Duration [N] seconds.
The reference video at `references/referral_videos/[REFERENCE_VIDEO_FILENAME]`
guides pacing + camera + energy ONLY. The output is original — character
+ product imagery come from the assets above.
```

**Concrete example:**

```
Create a video plan for client acme. Use hook WH-003, motion RM-001
(handheld aisle walk-and-talk), character
references/characters/sarah_outdoor.png, and product
references/products/leggings_black.jpg. Duration 10 seconds. The
reference video at references/referral_videos/walkthrough.mp4 guides
pacing + camera + energy ONLY. The output is original — character +
product imagery come from the assets above.
```

**Phrasings the Manager understands** (all route to Producer planning):

```
Create a video plan for client [CLIENT_ID].
Make a new creative concept for client [CLIENT_ID].
Prepare a video for client [CLIENT_ID].
Build a video for client [CLIENT_ID].
```

**Cost-control invariant:** Producer is hard-capped at 1 plan per
turn (`MAX_PLANS_PER_TURN = 1`). If you want 3 video variations,
that's 3 separate Producer runs, each with its own approval gate.

### Step 2 — The pause

After the plan is compiled, the graph pauses at `producer_submit`. The
Manager (in MCP) returns:

```
Workflow paused for financial safety. Review the COMPILED plan below
and use the resume_agency_workflow tool to approve or reject.

thread_id: mcp-acme-abc12345
client_id: acme
plan_id:   VP-007
...
```

In Streamlit, the page reloads with the **HITL approval panel** at
the top of the client view, showing:

- Hook, motion, character asset, product asset
- Full Kling prompt (collapsible)
- Negative prompt (collapsible)
- Duration / aspect_ratio / mode
- Enforced HARD constraints
- Submit attempts + last submit_error if any (V1.4.1+)

### Step 3 — Approve OR reject

#### Approve via MCP (when the pause originated in MCP)

```
Approve plan [PLAN_ID] for client [CLIENT_ID].
```

The Manager will refuse without an explicit `confirm=True`. Add it:

```
manager_request("approve plan VP-007 for client acme", confirm=True)
```

Or use the legacy tool directly with the thread_id from the pause
message:

```
resume_agency_workflow(thread_id="mcp-acme-abc12345", approve=True)
```

#### Approve via Streamlit (when the pause originated in Streamlit)

Click **Approve - submit to Kling** in the HITL panel. The page reloads
with the submitted task_id.

#### Reject (either channel)

```
Reject plan [PLAN_ID] for client [CLIENT_ID].
```

Reject is safe everywhere — `reject_video_plan` SQL is `pending_approval
| submitting → rejected`. No Kling call, plan stays in audit.

### Step 4 — Check the render

```
Check status of task [KLING_TASK_ID] for client [CLIENT_ID].
```

Or use Streamlit → *Generated videos* tab → click **Check Status**
on the pending task card. When complete, the MP4 downloads to
`clients/[CLIENT_ID]/outputs/videos/[HOOK_ID]-[MOTION_ID|images]-[TIMESTAMP].mp4`.

### Step 5 — Grade the output

Streamlit → *Grade output* tab. Or:

```bash
py -3.11 scripts/eval_review.py \
    --agent producer \
    --client-id [CLIENT_ID] \
    --output-type kling_video \
    --source clients/[CLIENT_ID]/outputs/videos/[FILENAME].mp4
```

---

## Approving a plan (standalone)

When the Manager / Streamlit surfaced a pending plan and you're ready:

```
Approve plan [PLAN_ID] for client [CLIENT_ID].
```

If the Manager replies with the "I cannot approve via MCP" guidance,
that means the plan was paused in Streamlit. Open Streamlit, select
the client, the HITL panel shows the compiled brief, click **Approve
- submit to Kling**.

If the Manager replies with an ambiguity (multiple thread_ids share
this plan_id — rare), it lists each candidate's `source_channel`. Pick
the right one and call `resume_agency_workflow(thread_id="...",
approve=True)` explicitly.

**Never** try to brute-force an approve via the MCP path on a
Streamlit-paused plan. The Manager refuses for a reason.

---

## Rejecting a plan

```
Reject plan [PLAN_ID] for client [CLIENT_ID].
```

Reject works regardless of which channel paused. The plan transitions
to `rejected` in SQL, the LangGraph checkpoint drains, no Kling call.
The plan row stays in `video_plans` for audit (you can see all
historical decisions via the Streamlit *Performance log* tab).

---

## Checking video status

When you've submitted a Kling task and want to know if it's done:

```
Check status of task [KLING_TASK_ID] for client [CLIENT_ID].
```

Or directly via Streamlit → *Generated videos* tab → **Check Status**
button on the pending row.

Three outcomes:

- **Still rendering** — Kling typically takes 1–5 minutes. Poll again
  in 30–60s.
- **Failed** — error string is recorded in the `video_jobs` row;
  surfaces at the top of *Generated videos*. Common cause:
  `MISSING_KEY` (fix `.env`), `TIMEOUT` (Kling may have accepted
  anyway — check Kling dashboard before re-approving the same plan).
- **Completed** — MP4 downloads to
  `clients/[CLIENT_ID]/outputs/videos/`. The job row updates to
  `completed` with the relative `video_path`.

---

## Grading an output

Always do this after a real run. Until automated evals exist, your
grade is the only signal we have.

**Streamlit (recommended):**

Sidebar → select client → *Grade output* tab → fill form:

- **Agent**: strategist / producer / analyst / outreach
- **Output type**: `pdf_report`, `kling_video`, `pitch_pdf`, `audit_json`,
  `constraint`, `winning_hook`, ...
- **Source path**: e.g. `clients/[CLIENT_ID]/outputs/videos/[FILENAME].mp4`
- **Specificity** / **Accuracy** / **Usefulness**: 1–5 each
- **Sendable**: yes/no — would you actually use this with a paying
  customer?
- **Notes**: 1–2 free-text sentences. Be honest. Low scores are useful.

**CLI:**

```bash
py -3.11 scripts/eval_review.py \
    --agent [AGENT] \
    --client-id [CLIENT_ID]      # OR --prospect-id [PROSPECT_ID]
    --output-type [TYPE] \
    --source [PATH] \
    --specificity 4 --accuracy 5 --usefulness 4 --sendable yes \
    --notes "Hook landed; product shot too dark." \
    --non-interactive
```

Reviews append to `evals/output_reviews.jsonl` (gitignored).

---

## Checking cost / activity

```
Show me the cost ledger for the last 7 days.
```

Or in Streamlit → *Agency overview* tab → scroll to the **Cost &
activity** section (Pass 2.2). It shows recent paid events grouped
by provider:

- **Kling** — video submits (the largest individual cost)
- **Apify** — Meta ad-library scrapes
- **Tavily** — competitor discovery searches
- **Meta** — Insights API pulls

**Anthropic LLM token costs are NOT tracked** in the ledger. They're
the largest gap and would require LangChain callbacks; a separate
workstream.

The ledger DB is `cost_ledger.db` at the repo root, gitignored.
Reset it by deleting the file (the next paid call repopulates).

---

## End-of-day summary

```
Give me my daily overview.
```

Or generate a markdown file you can scan offline / archive:

```bash
py -3.11 scripts/daily_summary.py --out reports/daily-summary-[YYYY-MM-DD].md
```

The summary lists:

- Critical approvals waiting
- High-priority failures
- Approvals stuck more than a day
- Ungraded outputs from today
- Stale pending Kling tasks

If you're using Windows Task Scheduler / cron, see
[`docs/OPERATIONS.md`](OPERATIONS.md) for the recipe.

---

## Troubleshooting (operator-side)

### "The Manager keeps asking for clarification."

Your prompt is missing one of: verb, scope (client_id), subject. Add
the missing piece. The classifier is deterministic — same words →
same answer.

### "The pending-approval plan won't let me approve from MCP."

The Manager will tell you exactly why in markdown. The most common
cause is the pause originated in Streamlit. Fix:

1. `streamlit run ui/app.py`
2. Sidebar → select the client mentioned in the Manager's response.
3. The HITL panel renders at the top with the compiled plan.
4. Click **Approve - submit to Kling**.

### "I approved but got a TIMEOUT error from Kling."

Kling MAY have accepted the request even though the local connection
dropped. The Manager / Producer surfaces a `TIMEOUT_WARNING` in the
plan's `submit_error`. **Do not blindly re-approve** — that creates
duplicate Kling submissions and double cost. Steps:

1. Open the Kling dashboard in your browser.
2. Look for a recently-submitted task that matches your hook /
   character / product.
3. If yes — the work is in flight; do nothing, the Streamlit
   *Generated videos* tab will pick it up.
4. If no — re-approve the plan. The atomic claim guarantees one
   Kling call per claim, so retry is safe.

### "A plan is stuck in `submitting` status."

This means a previous submit attempt was claimed but didn't finish
cleanly (process killed, etc). The MCP / Streamlit overview surfaces
these as critical tasks. Fix:

1. Open Streamlit → *Performance log* tab → "Unresolved plans" panel.
2. The stuck plan shows `:red[SUBMITTING]`. Click **Reject stale plan**.
3. The plan transitions to `rejected`; SQL audit preserved. If Kling
   actually accepted (rare but possible), check the Kling dashboard
   per the timeout-warning playbook above before assuming nothing
   happened.

### "Outreach hit the SAFETY LIMIT after 5 prospects."

That's the `MAX_APIFY_CALLS_PER_RUN = 5` cap working. Run the prompt
again for the next 5; the per-run counter resets every fresh outreach
turn.

### "The Manager classified my prompt incorrectly."

Two paths:

1. **Reword more explicitly** — add the verb + scope + subject. The
   classifier is deterministic; rewording will change the result.
2. **If you think the keyword list is genuinely missing a phrasing
   the operator would use** — open an issue / PR adding it to the
   `_DISPATCH_KEYWORDS` / `_OVERVIEW_KEYWORDS` tuples in
   `services/manager_service.py` and add a parametrized test in
   `tests/test_manager_classifier_phrases.py`. This is how the V1.9
   coverage expansion happened.

### "I broke something."

Reset paths in `docs/OPERATIONS.md` § 3 (runtime state reset). The
golden rule: anything in the runtime DBs (`checkpoints.db`,
`mcp_pending_runs.db`, `operator_tasks.db`, `cost_ledger.db`) is safe
to delete. Anything in `clients/<id>/`, `prospects/<id>/`, or `evals/`
is NOT.

---

## Quick reference card

```
DAILY:               "What do I need to approve today?"
CLIENT VIEW:         "Show tasks for client [CLIENT_ID]."
LOCATE ARTIFACT:     "Where is the pitch PDF for prospect [PROSPECT_ID]?"

OUTREACH:            "Start prospecting for [NICHE] brands in [COUNTRY_CODE].
                      Target [N] prospects."

STRATEGIST:          "Run Strategist for client [CLIENT_ID]. Target
                      [MARKET]. Find 5–10 candidate hooks."

PRODUCER (plan):     "Create a video plan for client [CLIENT_ID]. Use
                      hook [HOOK_ID], character `[PATH]`, product
                      `[PATH]`. Duration [N]s, aspect 9:16."

PRODUCER (motion):   "...  motion [MOTION_ID], reference video
                      `[PATH]` guides pacing + camera ONLY."

ANALYST:             "Analyze performance for client [CLIENT_ID]."

APPROVE PLAN:        "Approve plan [PLAN_ID] for client [CLIENT_ID]."
                     (add confirm=True via MCP)

REJECT PLAN:         "Reject plan [PLAN_ID] for client [CLIENT_ID]."

CHECK STATUS:        "Check status of task [KLING_TASK_ID] for client
                      [CLIENT_ID]."

GRADE OUTPUT:        Streamlit → Grade output tab.
                     OR: py -3.11 scripts/eval_review.py --agent ...

COST:                "Show me the cost ledger for the last 7 days."

DAILY SUMMARY:       py -3.11 scripts/daily_summary.py --out
                       reports/daily-summary-[YYYY-MM-DD].md
```

---

## What this playbook does NOT cover

- **Auth / RBAC** — none. Single-operator only.
- **Hosted deployment** — none. Local Streamlit + local MCP.
- **Real-time streaming progress** — Streamlit shows a spinner; no
  per-node updates yet.
- **Output-quality validation** — only manual grading via the eval
  framework. Automated evals are a separate workstream.
- **Anthropic LLM token cost tracking** — not in the cost ledger.
- **MCP cross-channel auto-bridging** — the Manager refuses to approve
  a Streamlit-paused plan via MCP and vice versa. By design.

For everything else, see:

- [`README.md`](../README.md) — concepts, install, MCP wiring
- [`docs/OPERATIONS.md`](OPERATIONS.md) — runbook, runtime reset,
  backups, troubleshooting (system-side)
- [`SECURITY.md`](../SECURITY.md) — what must never be committed,
  pre-push checklist
