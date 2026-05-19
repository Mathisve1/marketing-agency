-- ============================================================================
-- Yuvo OS — claude_code_tasks (Phase 2L proposal)
-- ============================================================================
-- Migration: 010
-- Status:    FILE ONLY — NOT YET APPLIED. Applying this is a schema change to
--            the live Supabase project and therefore a "deploy"; it requires
--            explicit operator approval.
--
-- Scope:    Persist Claude Code handoff tasks the operator prepares from
--            the Unified Inbox (Phase 2K). The dashboard ONLY saves these
--            rows — it never executes Claude Code, never calls the Claude
--            API, never spawns a process, never runs a local worker.
--            Claude Code (run by the operator, via MCP, in their own
--            session) does the work and writes results back to Supabase;
--            the operator then marks the task completed/failed.
--
--            The Phase 2K copy-only handoff works fully WITHOUT this
--            table (the prompt is generated client-side and copy-pasted).
--            Applying this migration adds a durable task queue on top.
--
-- Backwards compatibility:
--   - Brand-new table. No existing row is read or written. No CHECK on
--     any existing column is widened or narrowed. No trigger/policy on
--     any existing table is changed.
--   - Indexed only on common access patterns
--     (workspace_id, status, task_type, created_at).
--   - RLS mirrors the rest of the dashboard: OPERATOR-ONLY. Client-
--     portal roles MUST NOT be granted any policy here.
--
-- Idempotency: every statement is guarded so re-running is a no-op.
-- ============================================================================

create table if not exists public.claude_code_tasks (
  id                  uuid primary key default gen_random_uuid(),
  workspace_id        uuid not null references public.workspaces(id) on delete cascade,
  inbox_item_kind     text not null,
  task_type           text not null,
  risk_level          text not null,
  status              text not null default 'draft',
  title               text not null,
  instructions        text not null,
  safety_rules        jsonb not null default '[]'::jsonb,
  expected_outputs    jsonb not null default '[]'::jsonb,
  related_links       jsonb not null default '[]'::jsonb,
  context             jsonb not null default '{}'::jsonb,
  result_summary      text,
  error_message       text,
  created_by          uuid references public.profiles(id) on delete set null,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  completed_at        timestamptz
);

-- Status bound. Re-runnable.
do $$
begin
  if not exists (
    select 1 from information_schema.table_constraints
    where table_schema = 'public'
      and table_name = 'claude_code_tasks'
      and constraint_name = 'claude_code_tasks_status_ck'
  ) then
    alter table public.claude_code_tasks
      add constraint claude_code_tasks_status_ck
      check (status in (
        'draft',
        'ready_for_claude',
        'in_progress',
        'completed',
        'failed',
        'cancelled'
      ));
  end if;
end$$;

-- Risk-level bound. Re-runnable.
do $$
begin
  if not exists (
    select 1 from information_schema.table_constraints
    where table_schema = 'public'
      and table_name = 'claude_code_tasks'
      and constraint_name = 'claude_code_tasks_risk_level_ck'
  ) then
    alter table public.claude_code_tasks
      add constraint claude_code_tasks_risk_level_ck
      check (risk_level in (
        'info_only',
        'read_only',
        'draft_write',
        'gated_paid'
      ));
  end if;
end$$;

create index if not exists claude_code_tasks_workspace_id_idx
  on public.claude_code_tasks (workspace_id, created_at desc);

create index if not exists claude_code_tasks_status_idx
  on public.claude_code_tasks (status, created_at desc);

create index if not exists claude_code_tasks_task_type_idx
  on public.claude_code_tasks (task_type, created_at desc);

create index if not exists claude_code_tasks_created_at_idx
  on public.claude_code_tasks (created_at desc);

-- Touch updated_at on every row mutation. Re-uses the standard helper
-- defined in migration 001 (app.set_updated_at). Guarded so re-runs
-- are a no-op.
do $$
begin
  if not exists (
    select 1 from pg_trigger
    where tgname = 'claude_code_tasks_set_updated_at'
  ) then
    create trigger claude_code_tasks_set_updated_at
      before update on public.claude_code_tasks
      for each row execute function app.set_updated_at();
  end if;
end$$;

-- ---------------------------------------------------------------------------
-- RLS — OPERATOR-ONLY. Mirrors the existing operator-side pattern from
-- agent_runs (migration 008) / generation_jobs (005): the
-- `app.is_workspace_member(workspace_id)` helper defined in 001 walks
-- workspace_members.profile_id = app.current_profile_id().
-- Client-portal roles must NEVER touch this table.
-- ---------------------------------------------------------------------------
alter table public.claude_code_tasks enable row level security;

drop policy if exists claude_code_tasks_operator_select on public.claude_code_tasks;
create policy claude_code_tasks_operator_select on public.claude_code_tasks
  for select using (app.is_workspace_member(workspace_id));

drop policy if exists claude_code_tasks_operator_insert on public.claude_code_tasks;
create policy claude_code_tasks_operator_insert on public.claude_code_tasks
  for insert with check (app.is_workspace_member(workspace_id));

drop policy if exists claude_code_tasks_operator_update on public.claude_code_tasks;
create policy claude_code_tasks_operator_update on public.claude_code_tasks
  for update using (app.is_workspace_member(workspace_id))
              with check (app.is_workspace_member(workspace_id));

-- No DELETE policy. Tasks are kept for audit; "cancelled" status is the
-- soft-delete. The dashboard never deletes a task row.

comment on table public.claude_code_tasks is
  'Phase 2L — durable queue of operator-prepared Claude Code handoff '
  'tasks. The dashboard ONLY saves these; it never executes Claude '
  'Code, calls the Claude API, spawns a process, or runs a worker. '
  'Operator-only; client portals never see this.';

-- ============================================================================
-- POST-APPLY VERIFICATION (run manually after applying):
--   select count(*) from information_schema.tables
--    where table_schema = 'public' and table_name = 'claude_code_tasks';  -- 1
--   select count(*) from information_schema.columns
--    where table_schema = 'public' and table_name = 'claude_code_tasks'; -- 18
--   select conname from pg_constraint
--    where conrelid = 'public.claude_code_tasks'::regclass and contype = 'c';
--      -- expect: claude_code_tasks_status_ck, claude_code_tasks_risk_level_ck
--   select policyname from pg_policies
--    where schemaname = 'public' and tablename = 'claude_code_tasks';
--      -- expect: claude_code_tasks_operator_select / insert / update
--   notify pgrst, 'reload schema';
-- ============================================================================
