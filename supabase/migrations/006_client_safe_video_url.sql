-- ============================================================================
-- Yuvo OS — Phase 1O client-safe video URL exposure
-- ============================================================================
-- Migration: 006
-- Scope:    Let the client portal render the real generated video
--           instead of a static thumbnail.
--
--           Phase 1B's client_content_items_v view only exposed
--           `client_safe_poster_url` (an image). Operator-only tables
--           (generation_jobs.result_url, generated_assets.public_url
--           with kind='raw_video') hold the actual MP4 URL but are
--           gated by operator-only RLS.
--
--           This migration:
--             1. Adds `content_items.client_safe_video_url text` so the
--                operator explicitly opts in to sharing the playable
--                MP4 with the client.
--             2. Recreates `client_content_items_v` to project that
--                column. The view keeps `security_invoker = true` so
--                client RLS continues to apply.
--
--           Hard rule: the column is operator-set. The operator copies
--           the CDN URL from generation_jobs.result_url onto the
--           content_items row when they want the client to see it.
--           No automatic backfill from generation_jobs — that would
--           couple the client surface to operator-only state.
--
-- Idempotency:
--   - `alter table … add column if not exists` is non-destructive.
--   - `create or replace view` rebuilds the projection in place.
--
-- Phase 1O does NOT:
--   - Widen the generated_assets client policy. Phase 1B's
--     `generated_assets_client_select` (kind='thumbnail') stays.
--   - Add an audio_fixed video URL field. Phase 1F's audio_fixer_jobs
--     stores the post-fix MP4 separately; for Phase 1O the operator
--     copies whichever URL they want the client to see (raw OR fixed)
--     into `client_safe_video_url`.
--   - Trigger any paid Enhancor / Seedance / Audio Fixer call.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Add the column.
-- ---------------------------------------------------------------------------
alter table public.content_items
  add column if not exists client_safe_video_url text;

comment on column public.content_items.client_safe_video_url is
  'Client-safe MP4 URL (CDN public URL). Phase 1O: operator copies '
  'generation_jobs.result_url here when ready to share the playable '
  'video with the client. Exposed via client_content_items_v.';

-- ---------------------------------------------------------------------------
-- 2. Recreate the client-safe view with the new column.
--
--    `create or replace view` requires the column list to be a
--    superset of the previous columns in the same order, so we keep
--    every existing projection and append `client_safe_video_url`
--    at the end.
-- ---------------------------------------------------------------------------
create or replace view public.client_content_items_v
with (security_invoker = true) as
select
  ci.id,
  ci.campaign_id,
  ci.content_calendar_id,
  ci.title,
  ci.status,
  ci.scheduled_for,
  ci.platforms,
  ci.hook_text,
  ci.caption_draft,
  ci.client_safe_poster_url,
  ci.duration_sec,
  ci.created_at,
  ci.updated_at,
  ci.client_safe_video_url
from public.content_items ci
where ci.shared_with_client = true
  and ci.status in ('shared_with_client',
                    'approved_by_client',
                    'changes_requested_by_client');

comment on view public.client_content_items_v is
  'Client-safe projection. Phase 1O extends Phase 1B view with '
  'client_safe_video_url so the portal can render a real <video>. '
  'Operator-only columns (cost_*, prompt_summary, internal_*, '
  'audio_fixer_*, quality_tier, generation_jobs.*) remain absent.';

-- ============================================================================
-- TODO(phase-1p):
--   1. A helper server action to "share generation_jobs.result_url
--      with client" that copies the URL onto content_items
--      atomically with the status flip to 'shared_with_client'.
--   2. Distinguish raw vs audio-fixed in the client UI when both
--      are available.
-- ============================================================================
