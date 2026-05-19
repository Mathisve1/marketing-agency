-- ============================================================================
-- Yuvo OS — agent_runs (Phase 1W proposal)
-- ============================================================================
-- Migration: 008
-- Status:    FILE ONLY — NOT YET APPLIED. Applying this is a schema change to
--            the live Supabase project and therefore a "deploy"; it requires
--            explicit operator approval.
--
-- Scope:    Persist the input + output of every agent workflow (Brand
--           Analysis, UGC Prompt Planning, Calendar, Reporting, etc.).
--           The Phase 1W Brand Analysis agent works as a PREVIEW-ONLY
--           planner without this table; applying this migration lights up
--           "recent runs" history and the ability to revisit a previous
--           draft.
--
-- Backwards compatibility:
--   - No existing rows are touched. No CHECK on any existing column is
--     widened or narrowed.
--   - The table is new, additive, and indexed only on common access
--     patterns (workspace_id, brand_id, created_at).
--   - RLS mirrors the rest of the dashboard: operator-only on this table.
--     Client-portal roles MUST NOT be granted SELECT here.
--
-- Idempotency: every statement is guarded so re-running is a no-op.
-- ============================================================================

create table if not exists public.agent_runs (
  id                  uuid primary key default gen_random_uuid(),
  workspace_id        uuid not null references public.workspaces(id) on delete cascade,
  brand_id            uuid references public.brands(id) on delete set null,
  campaign_id         uuid references public.campaigns(id) on delete set null,
  content_item_id     uuid references public.content_items(id) on delete set null,
  agent_type          text not null,
  status              text not null,
  input               jsonb not null default '{}'::jsonb,
  output              jsonb,
  error_message       text,
  created_by          uuid references public.profiles(id) on delete set null,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

-- Status bound. Re-runnable.
do $$
begin
  if not exists (
    select 1 from information_schema.table_constraints
    where table_schema = 'public'
      and table_name = 'agent_runs'
      and constraint_name = 'agent_runs_status_ck'
  ) then
    alter table public.agent_runs
      add constraint agent_runs_status_ck
      check (status in ('draft', 'running', 'completed', 'failed'));
  end if;
end$$;

-- Agent-type allowlist. Re-runnable. New agent kinds extend this list.
do $$
begin
  if not exists (
    select 1 from information_schema.table_constraints
    where table_schema = 'public'
      and table_name = 'agent_runs'
      and constraint_name = 'agent_runs_agent_type_ck'
  ) then
    alter table public.agent_runs
      add constraint agent_runs_agent_type_ck
      check (agent_type in (
        'brand_analysis_ugc_prompt_planning'
      ));
  end if;
end$$;

create index if not exists agent_runs_workspace_id_idx
  on public.agent_runs (workspace_id, created_at desc);

create index if not exists agent_runs_brand_id_idx
  on public.agent_runs (brand_id, created_at desc);

create index if not exists agent_runs_agent_type_idx
  on public.agent_runs (agent_type, created_at desc);

-- Touch updated_at on every row mutation. Re-uses the standard helper
-- defined in migration 001 (app.set_updated_at). Guarded so re-runs
-- are a no-op.
do $$
begin
  if not exists (
    select 1 from pg_trigger
    where tgname = 'agent_runs_set_updated_at'
  ) then
    create trigger agent_runs_set_updated_at
      before update on public.agent_runs
      for each row execute function app.set_updated_at();
  end if;
end$$;

-- ---------------------------------------------------------------------------
-- RLS — OPERATOR-ONLY. Mirrors the existing operator-side pattern from
-- generation_jobs / generation_batches (migration 005): the
-- `app.is_workspace_member(workspace_id)` helper defined in 001 walks
-- workspace_members.profile_id = app.current_profile_id().
-- Client-portal roles must NEVER select from this table.
-- ---------------------------------------------------------------------------
alter table public.agent_runs enable row level security;

drop policy if exists agent_runs_operator_select on public.agent_runs;
create policy agent_runs_operator_select on public.agent_runs
  for select using (app.is_workspace_member(workspace_id));

drop policy if exists agent_runs_operator_insert on public.agent_runs;
create policy agent_runs_operator_insert on public.agent_runs
  for insert with check (app.is_workspace_member(workspace_id));

drop policy if exists agent_runs_operator_update on public.agent_runs;
create policy agent_runs_operator_update on public.agent_runs
  for update using (app.is_workspace_member(workspace_id))
              with check (app.is_workspace_member(workspace_id));

-- No DELETE policy. Agent runs are append-only for audit / cost tracking.

comment on table public.agent_runs is
  'Phase 1W — append-only record of operator-initiated agent runs '
  '(Brand Analysis + UGC Prompt Planning today; more agent_type values '
  'in later phases). Operator-only; client portals never see this.';

-- ============================================================================
-- POST-APPLY VERIFICATION (run manually after applying):
--   select count(*) from information_schema.tables
--    where table_schema = 'public' and table_name = 'agent_runs';  -- 1
--   select count(*) from information_schema.columns
--    where table_schema = 'public' and table_name = 'agent_runs';  -- 12
--   select policyname from pg_policies
--    where schemaname = 'public' and tablename = 'agent_runs';
--      -- expect: agent_runs_operator_select / insert / update
--   notify pgrst, 'reload schema';
-- ============================================================================
