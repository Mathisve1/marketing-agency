# Brand Analysis + UGC Prompt Planning Agent

Status: Phase 1W. First real agent workflow wired into the Owner Command
Center. Deterministic, preview-only, no external calls.

## Purpose

Collapse the "new client → first generation plan" loop into a single
operator-facing surface. The operator pastes a product URL (plus optional
brand context + free-text notes); the agent returns a structured planning
draft the operator can iterate on in the existing prompt editor.

This is the smallest agent that produces a real artefact the rest of the
Yuvo pipeline already consumes — a `prompt_versions`-shaped draft.

## Inputs

| Field | Required | Notes |
|---|---|---|
| `productUrl` | yes | `http(s)://…`. **Parsed locally, never fetched.** |
| `brandName` | no | Used in the prompt-draft label + negative-prompt brand-spelling guard. |
| `brandNiche` | no | Overrides the URL-derived niche template. |
| `brandTone` | no | Pre-filled from the selected brand if any. |
| `audienceAssumption` | no | Pre-filled from the selected brand if any. |
| `operatorNotes` | no | Free text. Echoed verbatim in the output; never interpreted as a claim. |

Route: `/agency/agents/brand-analysis` (operator-only).
Server action: `runBrandAnalysisAgentAction` in
`web/lib/actions/brand-analysis.ts`.
Planner: `planBrandAnalysisUGCPrompt` in
`web/lib/agents/brand-analysis.ts`.

## Outputs

`BrandAnalysisPlan` returned to the page and rendered inline. Sections:

1. **Brand brief** — hypotheses + a "needs operator verification" list.
2. **Product summary** — inferred category and form factor (hypothesis).
3. **Target audience** — primary + secondary hypothesis.
4. **Key selling points** — title + rationale, niche-templated.
5. **Objections / trust issues** — title + rebuttal, niche-templated.
6. **Content angles** — title + idea.
7. **UGC scenes (15s)** — 3-4 beats with timing and framing.
8. **Prompt draft suggestions** — one or two complete drafts. Every
   draft carries: `label`, `hook`, `script`, `promptBody`, `scenePlan`,
   `creatorDirection`, `productConstraints`, `negativePrompt` (capped at
   500 chars to fit the Seedance wire limit). The "label-strict" variant
   appears only for niches where label hallucination has been the
   recurring defect (skincare, supplements — the Pai V4 lesson).
9. **Content calendar ideas** — 6 day-offsets from D+0 (concept lock)
   through D+14 (calendar restock).
10. **Caveats** — explicit reminder that every claim is a hypothesis.

## Safety boundaries

- **No paid call.** Seedance / Enhancor / Audio Fixer are not touched.
- **No HTTP fetch.** The URL is parsed (`new URL(raw)`) for the hostname
  + slug words; the page is never requested.
- **No LLM call.** Output comes from a small, auditable template bank
  selected by URL/niche keyword.
- **No DB write.** Persistence (the `agent_runs` table from migration
  008) is proposed but **not yet applied** in this phase.
- **No client-facing exposure.** `web/app/client/*` does not import any
  Phase 1W module. The agent surface is `/agency/agents/brand-analysis`
  only.
- **No claim escalation.** Every output field is labelled "Hypothesis"
  or "Needs operator verification". Operator notes are echoed verbatim,
  never interpreted.

## How it connects to the pipeline

```
        ┌──── operator pastes URL + optional notes ────┐
        ▼                                              │
  /agency/agents/brand-analysis                        │
        │                                              │
  runBrandAnalysisAgentAction                          │
        │                                              │
  planBrandAnalysisUGCPrompt (pure, deterministic)     │
        │                                              │
        ▼                                              │
  BrandAnalysisPlan (returned to the page)             │
        │                                              │
  operator copies a prompt-draft block ────────────────┘
        │
        ▼
  /agency/campaigns/[campaignId]/content/[contentId]/prompt
  (existing prompt editor — operator pastes as a new draft /
   operator_editing prompt_version)
        │
        ▼
  (existing Phase 1F+ flow — Mark approved for generation,
   duration planner, per-clip submit gate, ingestion…)
```

There is **no automatic** path from the agent output into a
`prompt_versions` row in Phase 1W. The operator pastes the blocks into
the prompt editor and saves it there. This is intentional: the
"Create prompt version from agent output" action is reserved for the
next phase as a draft / operator_editing write only — never approved
for generation, never triggering a paid call.

### Phase 1X — Create draft prompt version from agent output

Phase 1X lights up the one-click handoff. Each prompt draft on the
agent result page carries a "Create draft prompt version" panel:

1. Operator picks a **target content item** from a workspace-wide
   dropdown (grouped Brand · Campaign · Content item; sorted
   alphabetically).
2. Optional one-line operator note (gets appended to
   `prompt_versions.notes`).
3. "Save as draft prompt version" calls
   `createPromptVersionFromAgentDraftAction` in
   `web/lib/actions/prompt-versions.ts`.

The action:

- requires the operator persona (demo mode bypasses this);
- resolves the target content item + its campaign via service-role read;
- computes `next_version_number = max(version_number for the content) + 1`;
- inserts ONE `prompt_versions` row with:
  - `status = 'operator_editing'` (hard-coded — never approved)
  - `quality_tier = 'standard_720p'`
  - `parent_version_id = latest existing version on that content` (or
    `null` if the content has none)
  - `source_regeneration_request_id = null` (this isn't a regen request)
  - all agent fields copied verbatim (`hook`, `script`, `prompt_body`,
    `scene_plan`, `creator_direction`, `product_constraints`,
    `negative_prompt`)
  - `notes` carries the agent provenance (agent_type, product_url,
    matched_niche, plus the operator's optional one-liner)
- returns `{ ok: true, promptVersionId, editorHref }` so the UI can
  immediately link to the existing prompt editor for review.

**The action never:**

- flips a status to `approved_for_generation` (the only status it can
  write is `operator_editing`)
- creates a `generation_jobs` row
- creates a `generated_assets` row
- creates an `audio_fixer_jobs` row
- contacts Seedance / Enhancor / Audio Fixer / any provider

### Phase 1X live verification (Pai V4)

The live insert was exercised against the Pai content item
(`88888888-8888-8888-8888-888888888888`) which already had v1–v4.
Result:

- A new row `prompt_versions.id = 642c916d-4870-4c15-baca-b4fe505341f7`,
  `version_number = 5`, `status = operator_editing`,
  `parent_version_id = 27465fbd…` (Pai V4),
  `source_regeneration_request_id = null`,
  `quality_tier = standard_720p`.
- Pai V4 row: `status` remained `approved_for_generation` (the new
  draft does NOT supersede an approved sibling — operator_editing has
  no uniqueness constraint).
- `generation_jobs`: row count unchanged, full
  `(id, status, provider_request_id)` signature byte-identical.
- Pai V4 `generated_assets`: unchanged.
- `audio_fixer_jobs`: unchanged.

This proves the action is additive-only and safe to expose to the
operator UI.

### Why the new draft stays draft-only

`operator_editing` is the only status the action can write. To progress
to a paid generation, the operator must:

1. Open the new draft in the existing prompt editor at
   `/agency/campaigns/[campaignId]/content/[contentId]/prompt`.
2. Review every section (every agent claim is a hypothesis — verify it).
3. Tighten the negative prompt + product constraints if needed.
4. Click "Mark approved for generation" in the existing editor — this
   is the *only* path to `approved_for_generation`, and it is gated by
   the existing operator confirmation.
5. Plan the duration in the existing duration planner.
6. Submit individual clips via the existing Phase 1S per-clip gate
   (typed confirmation, real product/influencer URLs, real webhook).

The agent never short-circuits any of those existing gates.

## How claims should be verified

Every output is a planning hypothesis derived from the URL + the
selected niche template, plus operator-supplied context. None of it is a
factual claim about a real brand.

Before any client share, the operator must:

1. Open the supplied URL and confirm the exact brand wordmark
   (case + diacritics). Update the negative-prompt brand-spelling guard
   if needed.
2. Confirm the product form factor matches the matched scene template.
   If the URL described a beverage but the page describes a topical, the
   matched niche is wrong — re-run with a `brandNiche` override.
3. Confirm tone-of-voice against the brand's homepage or about page.
4. Sanity-check the target audience hypothesis: would they actually
   watch a 15s 9:16 UGC ad?
5. For skincare / supplements: confirm the "label-strict" variant is
   feasible (a label-stripped reference image is available) before
   queuing the strict draft.

## Migration status

`supabase/migrations/008_agent_runs.sql` is written but **not applied**.
Phase 1W works fully without it — the UI gracefully states that
"Recent runs" is unavailable until the migration lands. When the
operator applies it (in a future phase with explicit approval), a
follow-up phase wires the server action to insert an `agent_runs` row
per invocation and the UI to list past runs.

The migration is fully audited, additive, idempotent. It introduces:

- `public.agent_runs` table (12 columns, no destructive statements)
- `agent_runs_status_ck` and `agent_runs_agent_type_ck` CHECK constraints
- three indexes (`workspace_id`, `brand_id`, `agent_type` × `created_at`)
- one `updated_at` trigger reusing the existing `touch_updated_at` helper
- RLS policies: operator SELECT / INSERT / UPDATE; no DELETE policy
  (append-only for audit + cost tracking)

Operator-only by RLS. Client-portal roles cannot read it.

## Future improvements

In priority order:

1. **Create prompt version from agent output** (next phase) —
   draft / operator_editing only. Picks a target content_item, writes a
   `prompt_versions` row, navigates to the prompt editor. Never sets
   status to `approved_for_generation`. Never triggers a paid call.
2. **Apply migration 008 + persist runs** — when the operator approves
   the schema change, store `(input, output, agent_type, status)` so the
   "Recent runs" card lights up.
3. **Website screenshot analysis** — a safe, opt-in screenshot pass
   (Playwright/Browserless behind a typed-confirmation gate) feeding the
   planner with on-page brand-mark + tone hints. Still operator-only.
4. **Competitor ad analysis** — operator pastes 3-5 competitor ad URLs;
   planner surfaces patterns + contrast hooks. Same safety envelope.
5. **Content calendar creation** — once `content_calendar_items` ships
   (the Phase 1V doc proposes the table), the agent can write proposed
   calendar slots directly with `source = 'agent_suggestion'`.
6. **LLM-assisted variant generation** — gated by typed confirmation +
   per-run cost ceiling. Only after the deterministic flow is trusted
   and operators ask for it.
7. **Automatic brief → client report** — agent output rendered as a
   client-facing brief that the operator reviews before sending. Behind
   the existing typed-phrase send gate; never auto-sent.

## Hard rules carried forward

- Hypothesis-labelled output, always.
- No fetch / no scrape / no LLM call without explicit operator gate.
- No write to `prompt_versions` from this agent in Phase 1W.
- No client-facing exposure of agent output.
- No deploy / commit / push without explicit user approval.
