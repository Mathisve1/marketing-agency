# Owner Command Center — Plan & Spec

Status: Phase 1V MVP built against existing schema. No migrations applied
in this phase. This document describes what shipped, why, and the
intended evolution toward a full agency operating system.

## Why this exists

Single-operator agencies have to track many small states at once — open
client requests, in-flight generations, failed jobs that need triage,
videos waiting to be shared, prompts pending approval. The Owner
Command Center collapses all of that into one operator-only surface
at `/agency` so the next correct action is one click away.

It is NOT the client portal. The client portal at `/client/[portalSlug]`
remains a separate, restricted surface (`web/lib/data/content.ts` reads
through the `client_content_items_v` view + `toClientContentView`
mapper). Costs, provider ids, raw prompts, failed states, audio-fixer
controls, and internal job ids stay operator-side.

## MVP — built on existing schema only

No new tables. The aggregator composes existing per-entity readers and
adds four workspace-wide listers that already had per-campaign /
per-content scopes elsewhere.

### Aggregator — `web/lib/data/owner-overview.ts`

- `listCampaignsForWorkspace(workspaceId)`
- `listContentItemsForWorkspace(workspaceId)`
- `listRegenerationRequestsForWorkspace(workspaceId)`
- `listAudioFixerJobsForWorkspace(workspaceId)`
- `getOwnerOverview(workspaceId)` returns a single `OwnerOverviewSnapshot`:
  brands, campaigns, content items, generation jobs, audio fixer jobs,
  regeneration requests, content requests, totalCreditsActual,
  creditsByBrandId, statusCounts, jobStatusCounts.
- `deriveOwnerNextActions(snapshot)` — pure derivation, deterministic.
- `deriveRecentActivity(snapshot, limit)` — merges jobs + regen requests +
  content requests + shared content into a time-sorted feed.

All functions are demo + supabase aware, matching the pattern of the
rest of `web/lib/data/*`.

### Dashboard sections — `web/app/agency/page.tsx`

1. **Business overview** — 8 stat cards: active clients, active
   campaigns, items in review (shared), jobs running, failed jobs,
   videos ready to share, client feedback waiting, total credits spent.
2. **Next actions** — deterministic queue from `deriveOwnerNextActions`,
   sorted by priority (Urgent → Today → Soon) then `createdAt` desc.
3. **Clients & brands** — per-brand row: campaign count, content
   status badges, open requests, portal link, latest shared item.
4. **Content pipeline** — grouped status counts (Draft / In flight /
   Raw or audio-fixed / Shared with client / Changes requested /
   Approved by client / Failed) plus a per-status generation-jobs
   breakdown.
5. **Agent launcher** — 9 cards, all `planned` except Generation and
   Review which link to `/agency/jobs`. No agent actually runs from
   the dashboard yet; every "coming soon" button is `disabled`.
6. **Recent activity** — merged feed: job created / submitted /
   completed / failed, regeneration requests opened / accepted,
   content requests received, assets shared.
7. **Cost & credits** — total realised, pending estimate (draft +
   queued), failed-jobs-no-cost, spend by brand, recent paid jobs,
   failed jobs list with estimated credits.
8. **Send to client (preview)** — latest shared items + portal link.
   "Send email to client" and "Send weekly content calendar" buttons
   are intentionally `disabled` — no email is sent from this dashboard
   in Phase 1V.

## Next actions — the deterministic model

`NextActionKind` enumerates every action the dashboard currently
recognises. Each is derived from objective state — no clock, no random
sort, no LLM call. The page renders the top 12.

| Kind | Trigger | Priority |
|---|---|---|
| `accept_regeneration_request` | regeneration_requests.status == 'open' | 1 |
| `fix_failed_job` | generation_jobs.status == 'failed' | 1 |
| `review_generated_video` | content_items.status == 'raw_ready' | 1 |
| `share_with_client` | status == 'audio_fixed' or 'ready_for_client_review' | 1 |
| `respond_to_feedback` | status == 'changes_requested_by_client' | 1 |
| `submit_next_clip` | clip N-1 completed AND clip N draft (same batch) | 2 |
| `stitch_completed_clips` | every clip in the batch is completed | 2 |
| `apply_generation_result` | status == 'audio_fixer_pending' | 2 |
| `approve_prompt` | status == 'draft' | 3 |
| `publish_ready_video` | status == 'approved_by_client' | 3 |
| `plan_content_calendar` | no draft items exist for a brand | 3 |

Sorting is `priority ASC, then createdAt DESC`. Identical priorities
are broken by recency so the freshest item wins.

## Agent launcher — the concept

The launcher is intentionally a planning surface. Each card represents
a future agent workflow that should be runnable end-to-end with one
operator click + one confirmation gate:

- **Lead Research Agent** — niche + region brief → prospects list.
- **Brand Analysis Agent** — brand site → brand brief.
- **Product / Website Analysis Agent** — product URL → extracted
  product, audience, and risk signals.
- **UGC Prompt Agent** — product URL → hook + script + scene plan +
  product constraints (the artefact that feeds the existing prompt
  editor).
- **Generation Agent** — duration plan + clip drafts (already exists
  end-to-end via `/agency/campaigns/.../prompt` + duration planner).
- **Review Agent** — surfaces completed clips for quick operator
  review (read-only today via `/agency/jobs`).
- **Content Calendar Agent** — suggest next-week posts from past
  hits + client asks.
- **Client Communication Agent** — drafts client-facing updates from
  operator state (still requires explicit operator send).
- **Reporting Agent** — weekly metrics email, saved as draft first,
  never auto-sent.

Constraints:
- No agent ever spends credits without an explicit operator gate
  (mirrors the Phase 1S clip submit gate and the Phase 1G CLI).
- No agent ever contacts a client without an explicit operator gate.
- Every agent run records intent in the database first; the side
  effect (HTTP call, email send, etc.) is the final step under a
  confirmation gate.

## Future schema proposal (NOT applied)

These tables capture the missing structure to support the agent + ops
workflows above. Apply them when the corresponding agent ships, not
upfront.

```text
agent_runs
  id uuid pk
  workspace_id uuid not null fk
  kind text not null               -- matches NextActionKind / agent key
  status text not null             -- 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  initiated_by_profile_id uuid
  input_jsonb jsonb
  result_jsonb jsonb
  cost_credits_actual int
  created_at, updated_at timestamptz

agent_tasks
  id uuid pk
  agent_run_id uuid fk
  step_index int
  kind text                        -- 'fetch' | 'compose_prompt' | 'classify' | 'persist'
  status text
  payload_jsonb jsonb
  error_message text
  created_at, updated_at timestamptz

client_messages
  id uuid pk
  workspace_id uuid not null fk
  brand_id uuid fk
  client_portal_id uuid fk
  direction text not null          -- 'inbound' | 'outbound_draft' | 'outbound_sent'
  channel text not null            -- 'portal_comment' | 'email' | 'whatsapp'
  subject text
  body text not null
  attached_content_item_id uuid fk
  sent_at timestamptz
  created_by_profile_id uuid
  created_at timestamptz

content_calendar_items
  id uuid pk
  campaign_id uuid fk
  scheduled_for date not null
  status text                      -- 'proposed' | 'accepted' | 'rejected'
  source text                      -- 'operator' | 'agent_suggestion' | 'client_request'
  hook_text text
  notes text
  created_at, updated_at timestamptz

outbound_emails
  id uuid pk
  workspace_id uuid not null fk
  recipient_email text not null
  recipient_kind text              -- 'client' | 'internal'
  subject text
  body_html text
  body_text text
  status text                      -- 'draft' | 'queued' | 'sent' | 'failed'
  related_agent_run_id uuid fk
  sent_at timestamptz
  created_at, updated_at timestamptz

publishing_targets
  id uuid pk
  brand_id uuid not null fk
  platform text not null           -- 'instagram_reels' | 'tiktok' | 'meta_ads' | 'youtube_shorts'
  external_account_handle text
  oauth_token_ref text             -- secret manager handle, never raw token
  created_at, updated_at timestamptz

dashboard_notifications
  id uuid pk
  workspace_id uuid not null fk
  audience text                    -- 'operator' | 'specific_user'
  kind text                        -- mirrors NextActionKind + system events
  title text
  body text
  href text
  read_at timestamptz
  created_at timestamptz
```

RLS pattern matches the rest of the dashboard:
- workspace_id filter on every table that carries it
- `outbound_emails` and `publishing_targets` are operator-only — no
  client-portal role can read them
- `client_messages.direction = 'outbound_draft'` is operator-only;
  `'outbound_sent'` is visible to the client portal (read-only)

## Recommended next implementation order

1. **Brand Analysis + UGC Prompt Planning Agent** — single workflow,
   single confirmation gate, reuses the existing prompt editor. No
   paid Seedance call, only LLM + scrape + structured save. This is
   the highest-leverage next agent: it materially shortens the
   "new client → first generation" loop.
2. **`agent_runs` + `agent_tasks` tables** — minimal viable schema
   for the agent runs above; no further tables until needed.
3. **Client Communication Agent (drafts only)** — produces email +
   portal-comment drafts; storage in `client_messages` /
   `outbound_emails`. No send without typed-phrase gate.
4. **Content Calendar Agent** — proposes calendar items from past
   hits + open client requests; stores in `content_calendar_items`.
5. **`outbound_emails` send pipeline** — only after the draft flow is
   trusted. Always behind operator typed-phrase confirmation.
6. **Reporting Agent + `dashboard_notifications`** — weekly snapshot
   stored locally first, optional email second.
7. **Publishing pipeline** — `publishing_targets` + per-platform
   adapters. Last because it requires per-platform OAuth + review.

## Hard rules carried into every future phase

- No paid call (Seedance / Enhancor / Audio Fixer) without an explicit
  operator gate (typed confirmation or CLI).
- No client-facing send (email, portal comment) without explicit
  operator approval.
- No client portal surface ever reads from `owner-overview` or any
  operator-only column (costs, provider ids, raw prompts, internal
  errors, audio-fixer controls, internal job ids).
- Audio Fixer never runs automatically.
- No deploy / commit / push without explicit user approval.
