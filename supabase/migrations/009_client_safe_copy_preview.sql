-- ============================================================================
-- Yuvo OS — Phase 2G client-safe copy preview for non-video content
-- ============================================================================
-- Migration: 009
-- Scope:    Let the client portal render a clean, operator-prepared
--           copy preview for non-video content items (text post,
--           feed_post, story, carousel, email_snippet, blog_snippet,
--           static_image) WITHOUT exposing the raw Copy Draft Agent
--           output stored in `content_items.caption_draft`.
--
--           Background:
--           Phase 2E's Copy Draft Agent writes structured operator-only
--           markup to `caption_draft` ("[tone: …]", "HEADLINE: …",
--           "HASHTAGS: […]", per-frame outlines, etc.) — this is the
--           operator's working draft, not a polished client-facing
--           caption. Phase 2F approved the copy internally
--           (`copy_approval_status: approved_internal` in the
--           `[copy approval]` block of `prompt_summary`), but neither
--           phase touched the client portal.
--
--           For Phase 2G the operator explicitly prepares a clean
--           preview before any client share. The preview is stored on
--           `content_items.client_safe_copy_preview`, and
--           `client_content_items_v` is rebuilt to project that column
--           alongside the existing `caption_draft` (still used by the
--           video flow). The client portal page prefers the preview
--           when present, falls back to caption_draft only for legacy
--           video items.
--
--           This migration is purely additive. It does NOT:
--             - drop or rename any existing column
--             - tighten any RLS policy
--             - change any status enum / check constraint
--             - touch any operator-only table
--             - require any backfill (existing rows get NULL — only
--               new operator prepare-action runs populate the column)
--             - trigger any paid Enhancor / Seedance / Audio Fixer
--               call, any email, any social-platform publish, or any
--               other external side effect
--
-- Idempotency:
--   - `alter table … add column if not exists` is non-destructive.
--   - `create or replace view` rebuilds the projection in place; the
--     trailing column list is a superset of the prior view (every
--     prior column kept in the same order, new column appended last),
--     so `create or replace` is legal here.
--
-- Rollback (manual; no automatic down-migration in this repo):
--     create or replace view public.client_content_items_v
--       with (security_invoker = true) as ... (previous SELECT) ...
--     alter table public.content_items
--       drop column if exists client_safe_copy_preview;
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Add the column.
-- ---------------------------------------------------------------------------
alter table public.content_items
  add column if not exists client_safe_copy_preview text;

comment on column public.content_items.client_safe_copy_preview is
  'Phase 2G: client-safe, operator-prepared copy preview for non-video '
  'content. Populated ONLY by prepareClientCopyPreviewAction after the '
  'operator has internally approved the Copy Draft Agent output (see '
  'prompt_summary [copy approval] block). NULL until prepared. The '
  'client_content_items_v view exposes this column; the client portal '
  'page prefers it over caption_draft for non-video items.';

-- ---------------------------------------------------------------------------
-- 2. Recreate the client-safe view with the new column.
--
--    `create or replace view` requires the column list to be a superset
--    of the previous columns in the same order. Phase 1O (migration
--    006) appended `client_safe_video_url` at the end; Phase 2G appends
--    `client_safe_copy_preview` immediately after it.
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
  ci.client_safe_video_url,
  ci.client_safe_copy_preview
from public.content_items ci
where ci.shared_with_client = true
  and ci.status in ('shared_with_client',
                    'approved_by_client',
                    'changes_requested_by_client');

comment on view public.client_content_items_v is
  'Client-safe projection. Phase 2G extends Phase 1O view with '
  'client_safe_copy_preview so the portal can render non-video copy '
  'items without leaking the raw Copy Draft Agent output stored in '
  'caption_draft. Operator-only columns (cost_*, prompt_summary, '
  'internal_*, audio_fixer_*, quality_tier, generation_jobs.*) remain '
  'absent.';

-- ============================================================================
-- TODO(phase-2H):
--   1. Client approval/comment loop for non-video copy — re-use the
--      existing content_feedback + content_approvals tables (already
--      wired for the video flow), no new tables needed.
--   2. Decide whether to retire `caption_draft` from the view once
--      every video item also carries a `client_safe_*` field, so the
--      client surface stops touching that column entirely.
-- ============================================================================
