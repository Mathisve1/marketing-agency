-- ============================================================================
-- Yuvo OS — Phase 1E prompt versioning + regeneration requests
-- ============================================================================
-- Migration: 004
-- Scope:    Add the two tables that back the Phase 1E workflow:
--             1. prompt_versions          — operator-only versioned prompt
--                                          history per content_item. The
--                                          "approved_for_generation" row is
--                                          the one the operator would feed
--                                          to a paid generation call. No
--                                          paid call is wired in 1E.
--             2. regeneration_requests    — first-class record of "the
--                                          client (or operator) wants this
--                                          regenerated", separate from the
--                                          content_feedback thread because
--                                          it carries operator-side
--                                          workflow state (open / accepted
--                                          / dismissed / fulfilled).
--
-- Conventions inherited from migration 001:
--   - uuid pk default gen_random_uuid()
--   - created_at default now()
--   - mutable tables get updated_at + app.set_updated_at() trigger
--   - status columns gated by CHECK constraints
--   - RLS enabled from the first commit
--
-- Phase 1E intentionally OMITS:
--   - The per-row UPDATE policy on content_items.status for the client
--     persona (Phase 1D TODO; service-role still flips status).
--   - content_items.active_prompt_version_id FK column. The partial
--     unique index (content_item_id) WHERE status='approved_for_generation'
--     answers the "what would I generate with right now?" question
--     without a second source of truth.
--   - Any wiring to a real generation call. The migration is data-only.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Helper: workspace_id behind a content_item.
--
-- Used by the new RLS policies so the operator gate is readable.
-- `security definer` so it bypasses RLS on the chain
-- content_items → campaigns → brands.workspace_id; the function itself
-- still enforces the membership check at the call site via
-- app.is_workspace_member.
-- ---------------------------------------------------------------------------
create or replace function app.workspace_id_for_content(target uuid)
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select b.workspace_id
  from public.content_items ci
  join public.campaigns c on c.id = ci.campaign_id
  join public.brands b on b.id = c.brand_id
  where ci.id = target
$$;

-- ---------------------------------------------------------------------------
-- prompt_versions
-- ---------------------------------------------------------------------------
create table if not exists public.prompt_versions (
  id                              uuid primary key default gen_random_uuid(),
  content_item_id                 uuid not null references public.content_items(id) on delete cascade,
  version_number                  integer not null check (version_number >= 1),
  label                           text,
  hook                            text,
  script                          text,
  prompt_body                     text,
  negative_prompt                 text,
  scene_plan                      text,
  creator_direction               text,
  product_constraints             text,
  quality_tier                    text not null default 'standard_720p'
                                  check (quality_tier in ('draft_480p', 'standard_720p', 'premium_1080p')),
  status                          text not null
                                  check (status in (
                                    'draft',
                                    'operator_editing',
                                    'approved_for_generation',
                                    'superseded'
                                  )),
  notes                           text,
  parent_version_id               uuid references public.prompt_versions(id) on delete set null,
  -- forward-declared FK to regeneration_requests; the table is created
  -- below in the same migration, so the FK is added with an
  -- after-the-fact ALTER once both sides exist.
  source_regeneration_request_id  uuid,
  created_by                      uuid references public.profiles(id) on delete set null,
  created_at                      timestamptz not null default now(),
  updated_at                      timestamptz not null default now(),
  constraint prompt_versions_unique_version
    unique (content_item_id, version_number)
);

-- At most one approved_for_generation row per content_item. Implemented
-- as a partial unique index because the partial form is the cleanest
-- way to express "single active version" in PostgreSQL.
create unique index if not exists prompt_versions_one_active_per_item
  on public.prompt_versions (content_item_id)
  where status = 'approved_for_generation';

create index if not exists prompt_versions_content_idx
  on public.prompt_versions (content_item_id, version_number desc);
create index if not exists prompt_versions_status_idx
  on public.prompt_versions (status, content_item_id);

drop trigger if exists prompt_versions_set_updated_at on public.prompt_versions;
create trigger prompt_versions_set_updated_at
  before update on public.prompt_versions
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- regeneration_requests
-- ---------------------------------------------------------------------------
create table if not exists public.regeneration_requests (
  id                          uuid primary key default gen_random_uuid(),
  content_item_id             uuid not null references public.content_items(id) on delete cascade,
  source_feedback_id          uuid references public.content_feedback(id) on delete set null,
  source_approval_id          uuid references public.content_approvals(id) on delete set null,
  requested_by_profile_id     uuid references public.profiles(id) on delete set null,
  requested_by_kind           text not null check (requested_by_kind in ('client', 'operator')),
  reason                      text check (
                                reason is null or reason in (
                                  'wrong_tone',
                                  'wrong_product',
                                  'not_on_brand',
                                  'bad_voice',
                                  'bad_face_hands',
                                  'bad_caption',
                                  'different_offer_needed',
                                  'other'
                                )
                              ),
  body                        text not null,
  status                      text not null default 'open'
                              check (status in ('open', 'accepted', 'dismissed', 'fulfilled')),
  accepted_prompt_version_id  uuid references public.prompt_versions(id) on delete set null,
  resolved_at                 timestamptz,
  resolved_by_profile_id      uuid references public.profiles(id) on delete set null,
  created_at                  timestamptz not null default now(),
  updated_at                  timestamptz not null default now()
);

create index if not exists regeneration_requests_content_idx
  on public.regeneration_requests (content_item_id, created_at desc);
create index if not exists regeneration_requests_status_idx
  on public.regeneration_requests (status, created_at desc);
create index if not exists regeneration_requests_requester_idx
  on public.regeneration_requests (requested_by_profile_id);

drop trigger if exists regeneration_requests_set_updated_at on public.regeneration_requests;
create trigger regeneration_requests_set_updated_at
  before update on public.regeneration_requests
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- Now that both tables exist, attach the deferred FK from
-- prompt_versions.source_regeneration_request_id back to
-- regeneration_requests.id. Wrapped in a do-block so this migration is
-- safe to re-apply.
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (
    select 1
    from information_schema.table_constraints
    where table_schema   = 'public'
      and table_name     = 'prompt_versions'
      and constraint_name = 'prompt_versions_source_regen_fkey'
  ) then
    alter table public.prompt_versions
      add constraint prompt_versions_source_regen_fkey
      foreign key (source_regeneration_request_id)
      references public.regeneration_requests(id)
      on delete set null;
  end if;
end$$;

-- ---------------------------------------------------------------------------
-- RLS: prompt_versions   (operator-only — NO client policy)
-- ---------------------------------------------------------------------------
alter table public.prompt_versions enable row level security;

drop policy if exists prompt_versions_operator_select on public.prompt_versions;
create policy prompt_versions_operator_select on public.prompt_versions
  for select
  using (app.is_workspace_member(app.workspace_id_for_content(content_item_id)));

drop policy if exists prompt_versions_operator_modify on public.prompt_versions;
create policy prompt_versions_operator_modify on public.prompt_versions
  for all
  using (app.is_workspace_member(app.workspace_id_for_content(content_item_id)))
  with check (app.is_workspace_member(app.workspace_id_for_content(content_item_id)));

-- ---------------------------------------------------------------------------
-- RLS: regeneration_requests
--   - Operator: full access for content in their workspace.
--   - Client:   SELECT only on rows they themselves requested. They
--               INSERT through requestChangesContentAction which writes
--               via service-role (the action validates portal ownership
--               in app code before calling).
-- ---------------------------------------------------------------------------
alter table public.regeneration_requests enable row level security;

drop policy if exists regeneration_requests_operator_select on public.regeneration_requests;
create policy regeneration_requests_operator_select on public.regeneration_requests
  for select
  using (app.is_workspace_member(app.workspace_id_for_content(content_item_id)));

drop policy if exists regeneration_requests_operator_modify on public.regeneration_requests;
create policy regeneration_requests_operator_modify on public.regeneration_requests
  for all
  using (app.is_workspace_member(app.workspace_id_for_content(content_item_id)))
  with check (app.is_workspace_member(app.workspace_id_for_content(content_item_id)));

drop policy if exists regeneration_requests_client_self_select on public.regeneration_requests;
create policy regeneration_requests_client_self_select on public.regeneration_requests
  for select
  using (
    requested_by_kind = 'client'
    and requested_by_profile_id = app.current_profile_id()
  );

-- ---------------------------------------------------------------------------
-- Convenience view: latest prompt version per content_item.
--   Used by the operator outputs page to render a one-line "current
--   prompt version" pill without a separate lookup. NOT a client view —
--   security_invoker so the prompt_versions RLS still applies.
-- ---------------------------------------------------------------------------
create or replace view public.latest_prompt_version_v
with (security_invoker = true) as
select distinct on (pv.content_item_id)
  pv.content_item_id,
  pv.id          as prompt_version_id,
  pv.version_number,
  pv.label,
  pv.status,
  pv.quality_tier,
  pv.updated_at
from public.prompt_versions pv
order by pv.content_item_id, pv.version_number desc;

comment on view public.latest_prompt_version_v is
  'Operator-only convenience: highest version_number per content item. '
  'security_invoker=true so prompt_versions RLS still applies.';

-- ============================================================================
-- TODO(phase-1f):
--   1. content_items.active_prompt_version_id FK so the operator can pin
--      a non-latest version as the one to generate with.
--   2. Per-row UPDATE policy on content_items.status for the client
--      persona (rolled forward from Phase 1D TODO).
--   3. generation_runs table linking prompt_versions → enhancor job ids.
--   4. Optional: split content_feedback.reason out of the [reason:<key>]
--      prefix into a real column once a schema-disruption window opens.
-- ============================================================================
