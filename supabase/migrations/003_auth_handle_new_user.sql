-- ============================================================================
-- Yuvo OS — Phase 1D auth wiring
-- ============================================================================
-- Migration: 003
-- Scope:    Wire Supabase Auth (auth.users) into the dashboard schema:
--             1. Auto-create a public.profiles row when a new auth.users
--                row is inserted.
--             2. If the new user's email matches a pending
--                client_portal_members row, link them by setting
--                profile_id + joined_at.
--             3. Add a deferred FK from public.profiles(id) → auth.users(id)
--                on delete cascade.
--             4. Backfill profile rows for the demo seed (operator
--                placeholder) so the seed continues to work with the new
--                FK.
--
-- Idempotency:
--   - All `create or replace function` / `create trigger if not exists`
--     forms are re-runnable.
--   - The FK add is wrapped in a do-block that skips if already present.
--
-- Phase 1D scope intentionally OMITS:
--   - Owner/operator/viewer role enforcement on writes (still a Phase
--     1E TODO).
--   - Per-column UPDATE policies on content_items so clients can flip
--     status without service-role help (Phase 1E).
--   - Magic-link share tokens for unauthenticated portal access (Phase
--     1E or 1F).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. handle_new_user trigger function.
--
--    Runs `security definer` so it can write to public.profiles + update
--    public.client_portal_members even when invoked from the auth
--    server-side flow (the inserting role does not have direct grant on
--    these tables).
-- ---------------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  display text;
  match_email text;
begin
  -- Pull a display name from the auth.users row. Prefer raw_user_meta_data
  -- (set by the client when calling signInWithOtp({options:{data}})), else
  -- fall back to the email local-part.
  display := coalesce(
    nullif(new.raw_user_meta_data ->> 'display_name', ''),
    nullif(new.raw_user_meta_data ->> 'name', ''),
    split_part(coalesce(new.email, ''), '@', 1),
    'Member'
  );

  -- Upsert the profile row keyed on auth.users.id. The `on conflict`
  -- guard makes the trigger idempotent if Supabase fires it twice (it
  -- does on some recovery flows).
  insert into public.profiles (id, display_name)
  values (new.id, display)
  on conflict (id) do nothing;

  -- Link any pending client_portal_members rows that were invited by
  -- email. Case-insensitive match: invites stored as
  -- 'Client@Example.com' should still link 'client@example.com'.
  if new.email is not null then
    match_email := lower(new.email);
    update public.client_portal_members
      set profile_id = new.id,
          joined_at  = coalesce(joined_at, now())
      where profile_id is null
        and lower(invite_email) = match_email;
  end if;

  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- 2. Drop + recreate the trigger so this migration is re-runnable
--    against a database that already has an older copy.
-- ---------------------------------------------------------------------------
drop trigger if exists handle_new_user_trg on auth.users;
create trigger handle_new_user_trg
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------------
-- 3. Add the deferred FK profiles.id → auth.users.id (skip if present).
--    `on delete cascade` so deleting an auth user wipes their profile.
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (
    select 1
    from information_schema.table_constraints
    where table_schema   = 'public'
      and table_name     = 'profiles'
      and constraint_name = 'profiles_id_fkey'
  ) then
    alter table public.profiles
      add constraint profiles_id_fkey
      foreign key (id) references auth.users(id) on delete cascade;
  end if;
end$$;

-- ---------------------------------------------------------------------------
-- 4. Backfill: the seed's operator placeholder profile (uuid 2222…)
--    was inserted in seed.sql BEFORE this FK existed. If you re-apply
--    the seed after this migration on a fresh database, the FK will
--    reject the placeholder row because no matching auth.users row
--    exists.
--
--    Two options for production:
--      (a) Stop using the placeholder; sign the operator up via the
--          magic-link flow and add them to workspace_members manually.
--      (b) Insert a corresponding auth.users row first.
--
--    We document both in docs/dashboard_phase_1d_auth_feedback.md.
--    No automatic backfill here — that would require service-role
--    privileges inside a migration, which the Supabase SQL editor
--    has but `psql` against a hosted DB does not always have.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- 5. Helper: app.current_email() — exposes the JWT email to RLS so a
--    future policy can authorize portal-member self-reads even before
--    the operator runs the link step. Phase 1D does not yet attach a
--    policy that uses this, but the helper is needed by Phase 1E and
--    is safer to land alongside the trigger.
-- ---------------------------------------------------------------------------
create or replace function app.current_email()
returns text
language sql
stable
as $$
  select lower(coalesce(auth.jwt() ->> 'email', ''))
$$;

-- ============================================================================
-- TODO(phase-1e):
--   1. UPDATE policy on content_items so a client portal member can flip
--      status to 'approved_by_client' / 'changes_requested_by_client'
--      WITHOUT needing the service-role escape hatch.
--   2. Owner-only role check on workspace_members writes.
--   3. Move client SELECT off the base content_items table; rely solely
--      on client_content_items_v.
--   4. Per-email invite link (single-use share token) so a non-Supabase-
--      auth client can land on the portal via a tokenized URL.
-- ============================================================================
