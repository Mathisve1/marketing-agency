# Multi-format content operations (Phase 2D)

Status: foundation landed. Taxonomy + planner + Calendar Agent + queue
are multi-format aware. No schema change.

## Why the platform is not only for ads

Yuvo is an agency operating system for **all** content work, not just
paid UGC video ads. The same brand brief feeds organic reels, stories,
carousels, feed posts, LinkedIn/Facebook copy, email snippets, and
website copy — most of which never touch Seedance. Phase 2D makes the
dashboard model that explicitly so it stops pushing every content item
toward a video generation job.

## Channels & formats

Single source of truth: `web/lib/content/taxonomy.ts`
(+ Python mirror `agents/producer/dashboard/content_taxonomy.py`).

- **channel:** instagram · tiktok · facebook · linkedin · email ·
  website · other
- **format:** ugc_video_ad · organic_reel · story · feed_post ·
  carousel · static_image · short_video · long_video · text_post ·
  email_snippet · blog_snippet
- **distribution_type:** paid · organic · client_review_only
- **content_goal:** awareness · trust_building · education · offer ·
  launch · testimonial · conversion · community · retention
- **recommended_asset_type:** ugc_video · short_video · long_video ·
  static_image · carousel_slides · story_frames · copy_only ·
  email_copy · blog_copy

Helpers: `formatNeedsVideoGeneration(format)` and
`formatNeedsPromptVersion(format)` — only ugc_video_ad / organic_reel /
short_video / long_video are video/prompt formats; everything else is a
copy/visual-brief workflow.

## Paid vs organic workflows

- **paid** → ad (currently only `ugc_video_ad`). Needs a prompt_version
  and, later and gated, a Seedance generation.
- **organic** → reels still need a video prompt, but stories /
  carousels / posts / email are copy/brief only.
- **client_review_only** → planning checkpoints; no asset, no share.

## How calendar items map to formats

The Brand Analysis planner now emits a **multi-format two-week plan**
(`BrandAnalysisPlan.contentCalendarIdeas: MultiFormatCalendarIdea[]`).
Each idea carries `dayOffset, title, brief, suggestedChannel,
suggestedFormat, distributionType, contentGoal, recommendedAssetType,
needsGeneration, needsPromptVersion, operatorNotes`. The default plan
mixes: a planning checkpoint, an organic reel, a story, a LinkedIn
post, a carousel, **one** paid UGC ad, a Facebook feed post, an email
snippet, and a restock retro. Only the paid UGC ad and the organic reel
set `needsGeneration: true`.

The **Calendar Agent** (`web/lib/actions/calendar-agent.ts`) writes the
selected ideas as draft `content_items` with:
- `platforms = [suggestedChannel]` (existing `text[]` column, no
  migration)
- `prompt_summary` provenance block extended with `channel:`,
  `format:`, `distribution_type:`, `content_goal:`,
  `recommended_asset_type:`, `needs_generation:`,
  `needs_prompt_version:`, `source_agent_run_id:`
- still `status='draft'`, `shared_with_client=false`,
  `client_safe_video_url=null` — no jobs, no prompt_versions, no
  provider calls

It also normalises legacy (pre-2D) `{label}` runs to the safest
non-video defaults so nothing old is mis-routed to Seedance.

## How video generation stays optional

The Prompt Review Queue's `derivePromptReviewNextAction` now parses
`format:` from `prompt_summary`:

- `carousel` → **create_carousel_outline**
- `story` → **create_story_brief**
- `text_post` / `feed_post` / `static_image` / `email_snippet` /
  `blog_snippet` → **create_copy_draft** / **review_copy_draft**
- video/prompt formats (or legacy untagged) → the existing
  create/review prompt + create_generation_job path

So a LinkedIn post never shows "create prompt draft" or implies a
Seedance video.

## Non-video items: copy / brief / review paths

Non-video formats move through copy or visual-brief work (Social Copy
Agent, Carousel Outline Agent, Story Builder Agent, LinkedIn Post Agent
— all `planned`, and runnable today via the **Claude Code task
handoff** model). They still land as operator-review drafts; nothing is
sent to a client automatically.

## Fit with the hybrid Claude Code execution model

Complex non-deterministic work (research, long-form copy, calendar
reasoning) is prepared by the dashboard and executed by Claude Code in
an operator session, writing results back to Supabase. See
`docs/hybrid_claude_code_execution_model.md`. No Claude API, no
website→Claude bridge, no auto-run.

## Future schema migration proposal (NOT applied)

When the `prompt_summary` provenance grep becomes limiting, promote the
metadata to real columns — additive/idempotent, same pattern as
migrations 007/008, **do not apply without explicit approval**:

```sql
-- 009_content_items_multi_format.sql  (PROPOSAL ONLY)
alter table public.content_items
  add column if not exists channel text,
  add column if not exists format text,
  add column if not exists distribution_type text,
  add column if not exists content_goal text,
  add column if not exists needs_generation boolean not null default false,
  add column if not exists source_agent_run_id uuid
    references public.agent_runs(id) on delete set null;

create index if not exists content_items_source_agent_run_idx
  on public.content_items (source_agent_run_id);
```

Until applied, the `prompt_summary` structured prefix is authoritative
and every reader tolerates its absence (legacy items default to the
video/prompt path only when no `format:` tag is present).
