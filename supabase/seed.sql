-- ============================================================================
-- Yuvo OS — Phase 1B Pai Skincare demo seed
-- ============================================================================
-- Purpose:
--   Reproduce web/lib/demo-data.ts in Supabase so that, once
--   NEXT_PUBLIC_DATA_SOURCE=supabase, every UI surface renders the same
--   Pai content as the local-state Phase 1A scaffold.
--
-- Idempotency:
--   Fixed uuid literals + `on conflict (id) do nothing` so this file is safe
--   to re-run against an empty or partially-seeded database. Re-running will
--   never duplicate rows. If you want to RESET the seed, truncate the
--   downstream tables first:
--     truncate public.generation_job_events, public.audio_fixer_jobs,
--              public.generation_jobs, public.generation_batches,
--              public.generated_assets, public.content_items,
--              public.content_calendars, public.campaigns,
--              public.client_portal_members, public.client_portals,
--              public.clients, public.brand_assets, public.brands,
--              public.workspace_members, public.profiles,
--              public.workspaces
--       restart identity cascade;
--
-- UUID convention (all-hex, mnemonic by repeated digit):
--   1… workspace          (Yuvo Studio)
--   2… profile            (operator placeholder)
--   3… brand              (Pai Skincare)
--   4… client             (Pai Skincare contracting entity)
--   5… client_portal      (pai-skincare-demo)
--   6… campaign           (Route 01)
--   7… content_calendar   (Week of 20 May)
--   8… content_item       (the shared Pai test)
--   9… content_item       (the next-week 720p draft)
--   a… generated_asset    (raw mp4 for item 8)
--   b… generated_asset    (audio-fixed mp4 for item 8)
--   c… generated_asset    (thumbnail for item 8)
--   d… prompt_version     (1080p hero — content_item 8, superseded)
--   e… prompt_version     (720p stricter label-text — content_item 9, operator_editing)
--   f… generation_batch   (historical 1080p Pai run — completed)
--   0a… generation_job    (historical 1080p Pai run — completed)
--   0b… generation_job_event (created event for historical job)
--   0c… generation_job_event (completed event for historical job)
--   0d… audio_fixer_job   (historical 1080p Pai audio-fixer — completed)
--   1a… generation_batch  (mock 720p next-week run — ready)
--   1b… generation_job    (mock 720p next-week run — draft)
--   1c… generation_job_event (created event for mock job)
--
-- Important:
--   - No paid Enhancor calls are triggered by seeding.
--   - internal_*_path values mirror the demo-data.ts strings exactly so
--     operator audit-trail UIs render unchanged when switched to Supabase.
--   - Costs (5940 raw + 2104 audio-fixer) are the real Pai test numbers.
--   - The operator profile uuid is a placeholder until Phase 1C wires
--     Supabase Auth; once auth is live, replace it with the real auth.users
--     row and re-run (the on-conflict guards make this safe).
-- ============================================================================

begin;

-- ---------------------------------------------------------------------------
-- workspace: Yuvo Studio
-- ---------------------------------------------------------------------------
insert into public.workspaces (id, slug, name, agency_name)
values (
  '11111111-1111-1111-1111-111111111111',
  'yuvo-studio',
  'Yuvo Studio',
  'Yuvo Studio'
)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- profile: operator placeholder (Phase 1C will replace with real auth user)
-- ---------------------------------------------------------------------------
insert into public.profiles (id, display_name, avatar_url)
values (
  '22222222-2222-2222-2222-222222222222',
  'Yuvo Operator',
  null
)
on conflict (id) do nothing;

insert into public.workspace_members (workspace_id, profile_id, role)
values (
  '11111111-1111-1111-1111-111111111111',
  '22222222-2222-2222-2222-222222222222',
  'owner'
)
on conflict (workspace_id, profile_id) do nothing;

-- ---------------------------------------------------------------------------
-- brand: Pai Skincare
-- ---------------------------------------------------------------------------
insert into public.brands (
  id,
  workspace_id,
  name,
  niche,
  website_url,
  brand_tone,
  audience_assumption,
  primary_color_hex,
  thumbnail_path
)
values (
  '33333333-3333-3333-3333-333333333333',
  '11111111-1111-1111-1111-111111111111',
  'Pai Skincare',
  'premium organic skincare',
  'https://www.paiskincare.com',
  'Calm, considered, science-led, gentle.',
  'Women 28–50, sensitive/reactive skin, organic / clean values.',
  '#3C4A3B',
  '/demo/pai-thumb.webp'
)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- client: Pai Skincare contracting entity
-- ---------------------------------------------------------------------------
insert into public.clients (
  id,
  workspace_id,
  brand_id,
  display_name,
  contact_email
)
values (
  '44444444-4444-4444-4444-444444444444',
  '11111111-1111-1111-1111-111111111111',
  '33333333-3333-3333-3333-333333333333',
  'Pai Skincare',
  null
)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- client_portal: pai-skincare-demo
-- ---------------------------------------------------------------------------
insert into public.client_portals (id, client_id, slug, status)
values (
  '55555555-5555-5555-5555-555555555555',
  '44444444-4444-4444-4444-444444444444',
  'pai-skincare-demo',
  'active'
)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- campaign: Route 01 — UGC ingredient-led sensitive-skin serum
-- ---------------------------------------------------------------------------
insert into public.campaigns (
  id,
  brand_id,
  client_portal_id,
  title,
  strategic_pattern,
  created_at
)
values (
  '66666666-6666-6666-6666-666666666666',
  '33333333-3333-3333-3333-333333333333',
  '55555555-5555-5555-5555-555555555555',
  'Route 01 — UGC ingredient-led sensitive-skin serum',
  'ingredient_proof',
  '2026-05-16T08:00:00Z'
)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- content_calendar: Week of 20 May 2026
-- ---------------------------------------------------------------------------
insert into public.content_calendars (
  id,
  campaign_id,
  title,
  starts_on,
  ends_on
)
values (
  '77777777-7777-7777-7777-777777777777',
  '66666666-6666-6666-6666-666666666666',
  'Week of 20 May 2026',
  '2026-05-18',
  '2026-05-24'
)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- content_item 1: the audio-fixed Pai test, SHARED_WITH_CLIENT, 1080p
--   Mirrors DEMO_CONTENT[0] in web/lib/demo-data.ts.
--   - status = shared_with_client (visible in client portal)
--   - shared_with_client = true (required by the deferred check constraint)
--   - quality_tier = premium_1080p (honest record of the realised resolution)
--   - cost_actual_credits = 5940 (15s @ 1080p Enhancor base)
--   - audio_fixer_credits_actual = 2104 (≈2103.75 rounded)
-- ---------------------------------------------------------------------------
insert into public.content_items (
  id,
  campaign_id,
  content_calendar_id,
  title,
  status,
  scheduled_for,
  platforms,
  hook_text,
  hook_source,
  caption_draft,
  prompt_summary,
  quality_tier,
  resolution,
  duration_sec,
  cost_estimate_credits,
  cost_actual_credits,
  internal_raw_path,
  internal_audio_fixed_path,
  internal_thumb_path,
  client_safe_poster_url,
  shared_with_client,
  audio_fixer_triggered,
  audio_fixer_completed,
  audio_fixer_credits_actual
)
values (
  '88888888-8888-8888-8888-888888888888',
  '66666666-6666-6666-6666-666666666666',
  '77777777-7777-7777-7777-777777777777',
  'What I keep coming back to — 15s UGC product talk',
  'shared_with_client',
  '2026-05-20',
  array['instagram_reels', 'tiktok', 'meta_ads']::text[],
  'I like that this feels simple — one serum, a few ingredients I can actually understand.',
  'operator',
  'One serum. Ingredients you can read. A routine that doesn''t feel like too much. — for sensitive, reactive skin. #skincare #sensitiveskin',
  'UGC product-talk, 15s, 720p target on next runs. Real-friend register; calm British VO laid in post. No competitor reference uploaded.',
  'premium_1080p',
  '1080p',
  15,
  5940,
  5940,
  'prospects/pai-skincare/production/clips/route_01_enhancor_ugc_raw_15s.mp4',
  'prospects/pai-skincare/production/clips/route_01_enhancor_ugc_audiofixed_15s.mp4',
  'prospects/pai-skincare/production/clips/route_01_enhancor_ugc_thumb.webp',
  '/demo/pai-thumb.webp',
  true,
  true,
  true,
  2104
)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- content_item 2: next-week 720p, SHARED_WITH_CLIENT (Phase 1O)
--   Mirrors DEMO_CONTENT[1] in web/lib/demo-data.ts.
--   - Phase 1F+: a real 720p Seedance generation completed and the
--     operator shared it with the client (status flip).
--   - client_safe_video_url + client_safe_poster_url point at the CDN
--     URLs from generation_jobs row 1b1b1b1b-…
--   - quality_tier = standard_720p (the strategic default)
--   - cost_estimate_credits = 2646 (15s @ 720p Enhancor base)
-- ---------------------------------------------------------------------------
insert into public.content_items (
  id,
  campaign_id,
  content_calendar_id,
  title,
  status,
  scheduled_for,
  platforms,
  hook_text,
  hook_source,
  caption_draft,
  prompt_summary,
  quality_tier,
  resolution,
  duration_sec,
  cost_estimate_credits,
  client_safe_poster_url,
  client_safe_video_url,
  shared_with_client,
  audio_fixer_triggered,
  audio_fixer_completed
)
values (
  '99999999-9999-9999-9999-999999999999',
  '66666666-6666-6666-6666-666666666666',
  '77777777-7777-7777-7777-777777777777',
  'Next week — ingredient close-up variant',
  'shared_with_client',
  '2026-05-27',
  array['instagram_reels', 'tiktok']::text[],
  'Three ingredients. That''s it.',
  'operator',
  'Rosehip BioRegenerate. The whole story in one bottle. — made for sensitive skin.',
  'Macro-led variant. Standard 720p planned. Audio Fixer manual after raw review.',
  'standard_720p',
  '720p',
  15,
  2646,
  'https://d2i9jqncnkplwq.cloudfront.net/thumbnails/0a8813b8-76de-4d8a-bd6a-540c59880251.webp',
  'https://d2i9jqncnkplwq.cloudfront.net/videos/4e5a03d2-c5b7-418c-9897-90cf3cd60f2c.mp4',
  true,
  false,
  false
)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- Phase 1O operator one-liner (run AFTER applying migration 006 on a
-- production DB that was seeded BEFORE migration 006 existed):
--
--   update public.content_items
--   set client_safe_poster_url = 'https://d2i9jqncnkplwq.cloudfront.net/thumbnails/0a8813b8-76de-4d8a-bd6a-540c59880251.webp',
--       client_safe_video_url  = 'https://d2i9jqncnkplwq.cloudfront.net/videos/4e5a03d2-c5b7-418c-9897-90cf3cd60f2c.mp4',
--       status                 = 'shared_with_client',
--       shared_with_client     = true
--   where id = '99999999-9999-9999-9999-999999999999';
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- generated_assets for content_item 1
--   The audio-fixed Pai test has three physical artifacts on local FS today.
--   Once Supabase Storage lands, storage_path swaps to the bucket key and
--   public_url becomes a real signed URL.
-- ---------------------------------------------------------------------------
insert into public.generated_assets (
  id,
  content_item_id,
  kind,
  storage_path,
  public_url,
  mime,
  duration_sec,
  resolution
)
values
  (
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
    '88888888-8888-8888-8888-888888888888',
    'raw_video',
    'prospects/pai-skincare/production/clips/route_01_enhancor_ugc_raw_15s.mp4',
    null,
    'video/mp4',
    15,
    '1080p'
  ),
  (
    'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
    '88888888-8888-8888-8888-888888888888',
    'audio_fixed_video',
    'prospects/pai-skincare/production/clips/route_01_enhancor_ugc_audiofixed_15s.mp4',
    null,
    'video/mp4',
    15,
    '1080p'
  ),
  (
    'cccccccc-cccc-cccc-cccc-cccccccccccc',
    '88888888-8888-8888-8888-888888888888',
    'thumbnail',
    'prospects/pai-skincare/production/clips/route_01_enhancor_ugc_thumb.webp',
    '/demo/pai-thumb.webp',
    'image/webp',
    null,
    '1080p'
  )
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- Phase 1E — prompt_versions seed
--
-- Two rows, mirroring the two content_items above:
--   d… 1080p hero version for content_item 8 (the historical production
--      take). Marked `superseded` because the strategic default is now
--      720p — this row exists for lineage and operator reference.
--   e… 720p stricter label-text guard version for content_item 9 (the
--      next-week draft). Marked `operator_editing`; the operator is
--      still iterating in the prompt editor and has NOT marked it
--      approved-for-generation. Quality tier reflects the locked-in
--      720p default.
--
-- No paid generation calls are triggered by seeding. The fields here
-- are the operator's working draft, not a record of any run.
-- ---------------------------------------------------------------------------
insert into public.prompt_versions (
  id,
  content_item_id,
  version_number,
  label,
  hook,
  script,
  prompt_body,
  negative_prompt,
  scene_plan,
  creator_direction,
  product_constraints,
  quality_tier,
  status,
  notes,
  created_by
)
values (
  'dddddddd-dddd-dddd-dddd-dddddddddddd',
  '88888888-8888-8888-8888-888888888888',
  1,
  '1080p hero (historical)',
  'I like that this feels simple — one serum, a few ingredients I can actually understand.',
  'Intro hook on camera, hold serum bottle at chest height, brief glance at label, look back to camera for the close. Calm British register, real-friend cadence.',
  'UGC product-talk, 15s. Single take. Bathroom or soft-window living room. One creator, 28–40, sensitive-skin profile. Serum bottle visible but not the hero of the frame. Calm British VO. Slow ambient music bed.',
  'No exaggerated claims. No clinical-white studio. No on-screen logo overlays. No competing skincare visible in the frame. No glossy commercial lighting.',
  '0–2s hook on camera. 2–8s glance to product + brief read. 8–14s reflection on routine. 14–15s soft close.',
  'Real-friend register, not influencer-energy. Slow blinks, occasional looks away. Body language: comfortable, not posed.',
  'Pai Skincare BioRegenerate Rosehip Oil. Label must be legible when in frame; do not invent ingredient claims. Brand name spelled "Pai" (not "Pái" / "Pie").',
  'premium_1080p',
  'superseded',
  'Realised version. Production cost 5940 raw + 2104 audio-fixer. Kept for lineage; new takes target 720p.',
  '22222222-2222-2222-2222-222222222222'
)
on conflict (id) do nothing;

insert into public.prompt_versions (
  id,
  content_item_id,
  version_number,
  label,
  hook,
  script,
  prompt_body,
  negative_prompt,
  scene_plan,
  creator_direction,
  product_constraints,
  quality_tier,
  status,
  notes,
  created_by
)
values (
  'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee',
  '99999999-9999-9999-9999-999999999999',
  1,
  '720p stricter label-text guard',
  'Three ingredients. That''s it.',
  'Macro-led pickup of the bottle, slow rotation to expose the label, hands stay in soft focus, voice-over rides over the rotation. Close on the dropper.',
  'Macro-led UGC variant, 15s, 720p. One creator''s hands only — no face this take. Soft daylight. Two cuts maximum. Calm British VO. The story is the label.',
  'No animated text, no graphics overlays, no warped or melted label text, no AI-typical extra fingers, no fake ingredient names, no jewellery, no nail polish, no competing brands.',
  '0–3s ingredient-led hook over a hand reaching for the bottle. 3–9s slow rotation of the bottle so the label reads. 9–13s dropper close-up. 13–15s soft close, hand setting bottle down.',
  'Hands-only this take. Calm, deliberate motion. No fidgeting. Skin tone should match a sensitive-skin profile (no heavy makeup on hands).',
  'Pai Skincare BioRegenerate Rosehip Oil. Label text MUST be legible and spelled exactly as on the real packaging. If the label cannot be rendered legibly, fall back to an out-of-focus pass rather than inventing text. No competing skincare visible.',
  'standard_720p',
  'operator_editing',
  'Working draft. Sharpened label-text guard after the 1080p take rendered an OK but not perfect label. NOT approved for generation yet — operator is still iterating.',
  '22222222-2222-2222-2222-222222222222'
)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- Phase 1F — generation_batches + generation_jobs seed
--
-- Two batches, mirroring the two prompt_versions above:
--   f… HISTORICAL 1080p Pai run, status=completed. cost_actual=5940 raw
--      + 2104 audio_fixer (real Pai test numbers). Linked to the
--      existing generated_assets rows so the operator job-detail page
--      can render the thumbnail + audio-fixed result without re-running
--      anything.
--   1a… MOCK 720p next-week run, status=ready. cost_estimated=2646
--      (15s @ 720p Enhancor base). No provider_request_id, no
--      result_url, no API response. This row exists purely as a record
--      of operator intent and is what /agency/jobs surfaces by default.
--
-- Phase 1F also seeds one audio_fixer_jobs row attached to the
-- historical generation_job, with status=completed and the real Pai
-- numbers (estimated 2100 / actual 2104). The new (mock) generation
-- job has NO audio_fixer_jobs row — Audio Fixer is manual, and the
-- operator hasn't opted in yet.
--
-- No paid generation calls are triggered by seeding.
-- ---------------------------------------------------------------------------

-- HISTORICAL 1080p Pai batch — completed.
insert into public.generation_batches (
  id,
  workspace_id,
  brand_id,
  campaign_id,
  created_by,
  label,
  status,
  total_estimated_credits,
  total_actual_credits,
  created_at,
  updated_at
)
values (
  'ffffffff-ffff-ffff-ffff-ffffffffffff',
  '11111111-1111-1111-1111-111111111111',
  '33333333-3333-3333-3333-333333333333',
  '66666666-6666-6666-6666-666666666666',
  '22222222-2222-2222-2222-222222222222',
  'Route 01 — 1080p hero (historical Pai test)',
  'completed',
  5940,
  5940,
  '2026-05-15T12:00:00Z',
  '2026-05-15T12:34:00Z'
)
on conflict (id) do nothing;

-- HISTORICAL 1080p Pai generation_job — completed.
insert into public.generation_jobs (
  id,
  batch_id,
  content_item_id,
  prompt_version_id,
  provider,
  provider_mode,
  quality_tier,
  resolution,
  duration_seconds,
  status,
  estimated_credits,
  actual_credits,
  provider_request_id,
  result_url,
  thumbnail_url,
  raw_asset_id,
  error_message,
  raw_request_json,
  raw_response_json,
  created_at,
  updated_at
)
values (
  '0a0a0a0a-0a0a-0a0a-0a0a-0a0a0a0a0a0a',
  'ffffffff-ffff-ffff-ffff-ffffffffffff',
  '88888888-8888-8888-8888-888888888888',
  'dddddddd-dddd-dddd-dddd-dddddddddddd',
  'enhancor_seedance',
  'ugc',
  'premium_1080p',
  '1080p',
  15,
  'completed',
  5940,
  5940,
  -- provider_request_id is recorded as a synthetic value because the
  -- original Pai test predates this table; the real id lives in the
  -- producer-side ledger.
  'historical-pai-1080p-15s',
  -- result_url is the operator-side local FS path. NOT a public client URL.
  'prospects/pai-skincare/production/clips/route_01_enhancor_ugc_raw_15s.mp4',
  '/demo/pai-thumb.webp',
  'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
  null,
  '{"note":"historical — original request payload not retained"}'::jsonb,
  '{"note":"historical — original response payload not retained"}'::jsonb,
  '2026-05-15T12:00:30Z',
  '2026-05-15T12:18:00Z'
)
on conflict (id) do nothing;

-- Historical job timeline.
insert into public.generation_job_events (
  id, generation_job_id, event_type, message, raw_payload, created_at
)
values
  (
    '0b0b0b0b-0b0b-0b0b-0b0b-0b0b0b0b0b0b',
    '0a0a0a0a-0a0a-0a0a-0a0a-0a0a0a0a0a0a',
    'created',
    'Historical Pai 1080p run — backfilled from operator notes.',
    null,
    '2026-05-15T12:00:30Z'
  ),
  (
    '0c0c0c0c-0c0c-0c0c-0c0c-0c0c0c0c0c0c',
    '0a0a0a0a-0a0a-0a0a-0a0a-0a0a0a0a0a0a',
    'completed',
    'Raw 15s clip rendered. Operator triggered Audio Fixer manually.',
    null,
    '2026-05-15T12:18:00Z'
  )
on conflict (id) do nothing;

-- Backfill generated_assets backlinks for the historical job. raw_video +
-- audio_fixed_video belong to the generation job; thumbnail belongs to
-- the generation job too (a single thumbnail represents the run).
update public.generated_assets
   set generation_job_id = '0a0a0a0a-0a0a-0a0a-0a0a-0a0a0a0a0a0a'
 where id in (
   'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
   'cccccccc-cccc-cccc-cccc-cccccccccccc'
 )
   and generation_job_id is null;

-- Historical audio_fixer_jobs row — completed with the real Pai numbers.
insert into public.audio_fixer_jobs (
  id,
  generation_job_id,
  input_asset_id,
  provider,
  status,
  estimated_credits,
  actual_credits,
  provider_request_id,
  result_url,
  output_asset_id,
  error_message,
  raw_request_json,
  raw_response_json,
  created_at,
  updated_at
)
values (
  '0d0d0d0d-0d0d-0d0d-0d0d-0d0d0d0d0d0d',
  '0a0a0a0a-0a0a-0a0a-0a0a-0a0a0a0a0a0a',
  'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
  'enhancor_audio_fixer',
  'completed',
  2100,
  -- 2103.75 rounded — see audio_fixer_credits_actual on content_items.
  2104,
  'historical-pai-audiofixer-15s',
  'prospects/pai-skincare/production/clips/route_01_enhancor_ugc_audiofixed_15s.mp4',
  'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
  null,
  '{"note":"historical — original audio-fixer payload not retained"}'::jsonb,
  '{"note":"historical — original audio-fixer response not retained"}'::jsonb,
  '2026-05-15T12:20:00Z',
  '2026-05-15T12:24:00Z'
)
on conflict (id) do nothing;

-- Tag the audio-fixed asset back to its audio_fixer_job.
update public.generated_assets
   set audio_fixer_job_id = '0d0d0d0d-0d0d-0d0d-0d0d-0d0d0d0d0d0d'
 where id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
   and audio_fixer_job_id is null;

-- MOCK 720p next-week batch — status=ready. No paid call has been made.
insert into public.generation_batches (
  id,
  workspace_id,
  brand_id,
  campaign_id,
  created_by,
  label,
  status,
  total_estimated_credits,
  total_actual_credits
)
values (
  '1a1a1a1a-1a1a-1a1a-1a1a-1a1a1a1a1a1a',
  '11111111-1111-1111-1111-111111111111',
  '33333333-3333-3333-3333-333333333333',
  '66666666-6666-6666-6666-666666666666',
  '22222222-2222-2222-2222-222222222222',
  'Route 01 — 720p ingredient close-up (next-week mock)',
  'ready',
  2646,
  null
)
on conflict (id) do nothing;

-- MOCK 720p next-week generation_job — status=draft. No provider call.
insert into public.generation_jobs (
  id,
  batch_id,
  content_item_id,
  prompt_version_id,
  provider,
  provider_mode,
  quality_tier,
  resolution,
  duration_seconds,
  status,
  estimated_credits,
  actual_credits,
  provider_request_id,
  result_url,
  thumbnail_url,
  raw_asset_id,
  error_message,
  raw_request_json,
  raw_response_json
)
values (
  '1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b',
  '1a1a1a1a-1a1a-1a1a-1a1a-1a1a1a1a1a1a',
  '99999999-9999-9999-9999-999999999999',
  'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee',
  'enhancor_seedance',
  'ugc',
  'standard_720p',
  '720p',
  15,
  'draft',
  2646,
  null,
  null,
  null,
  null,
  null,
  null,
  null,
  null
)
on conflict (id) do nothing;

-- "Created" event for the mock job.
insert into public.generation_job_events (
  id, generation_job_id, event_type, message, raw_payload, created_at
)
values (
  '1c1c1c1c-1c1c-1c1c-1c1c-1c1c1c1c1c1c',
  '1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b',
  'created',
  'Mock generation job created from approved 720p prompt version. No paid call has been made.',
  null,
  '2026-05-16T08:30:00Z'
)
on conflict (id) do nothing;

commit;

-- ---------------------------------------------------------------------------
-- Quick sanity checks (run manually after seeding):
--   select count(*) from public.workspaces;              -- 1
--   select count(*) from public.brands;                  -- 1
--   select count(*) from public.campaigns;               -- 1
--   select count(*) from public.content_items;           -- 2
--   select count(*) from public.client_content_items_v;  -- 1 (only the shared one)
--   select count(*) from public.generated_assets;        -- 3
--   select count(*) from public.prompt_versions;         -- 2
--   select count(*) from public.regeneration_requests;   -- 0 (none seeded)
--   select count(*) from public.generation_batches;      -- 2 (1 historical + 1 mock)
--   select count(*) from public.generation_jobs;         -- 2
--   select count(*) from public.generation_job_events;   -- 3
--   select count(*) from public.audio_fixer_jobs;        -- 1 (historical only)
-- ---------------------------------------------------------------------------
