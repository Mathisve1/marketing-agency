-- ============================================================================
-- Yuvo OS — Phase 1B RLS policies
-- ============================================================================
-- Migration: 002
-- Scope:    Enable RLS on every Phase 1B table + the minimum set of
--           policies needed for the two personas:
--             (1) Operator   — authenticated workspace member.
--             (2) Client     — authenticated client_portal_member.
--           The Next.js server still talks to Supabase with the service-
--           role key when the host process is the operator's machine
--           (Phase 1B). Phase 1C wires real auth + client-side anon-key
--           queries, at which point these policies become the load-
--           bearing layer.
--
-- Policy file layout per table:
--   1.  enable row level security
--   2.  policy "<table>_operator_<op>"
--   3.  policy "<table>_client_<op>"  (only where a client can touch it)
--
-- Helpers used:
--   app.is_workspace_member(workspace_uuid)
--   app.is_portal_member(portal_uuid)
--   app.current_profile_id()
-- ============================================================================

-- ---------------------------------------------------------------------------
-- workspaces
-- ---------------------------------------------------------------------------
alter table public.workspaces enable row level security;

create policy workspaces_operator_select on public.workspaces
  for select using (app.is_workspace_member(id));

create policy workspaces_operator_modify on public.workspaces
  for all using (app.is_workspace_member(id))
         with check (app.is_workspace_member(id));

-- A client never reads the workspace row directly.

-- ---------------------------------------------------------------------------
-- profiles
-- ---------------------------------------------------------------------------
alter table public.profiles enable row level security;

-- Everyone can read their own profile.
create policy profiles_self_select on public.profiles
  for select using (id = app.current_profile_id());

-- Workspace members can read each other's profiles within the same workspace.
create policy profiles_coworker_select on public.profiles
  for select using (
    exists (
      select 1
      from public.workspace_members wm_self
      join public.workspace_members wm_other
        on wm_self.workspace_id = wm_other.workspace_id
      where wm_self.profile_id  = app.current_profile_id()
        and wm_other.profile_id = profiles.id
    )
  );

-- Self-update only.
create policy profiles_self_update on public.profiles
  for update using (id = app.current_profile_id())
             with check (id = app.current_profile_id());

-- ---------------------------------------------------------------------------
-- workspace_members
-- ---------------------------------------------------------------------------
alter table public.workspace_members enable row level security;

create policy workspace_members_operator_select on public.workspace_members
  for select using (app.is_workspace_member(workspace_id));

-- Only an owner can write membership rows. The role-check function would
-- live alongside app.is_workspace_member; we keep it implicit until
-- Phase 1C and document it here:
-- TODO(phase-1c): split into app.is_workspace_owner(workspace_id) and
--                 wire it through the policies below.
create policy workspace_members_operator_modify on public.workspace_members
  for all using (app.is_workspace_member(workspace_id))
         with check (app.is_workspace_member(workspace_id));

-- ---------------------------------------------------------------------------
-- brands
-- ---------------------------------------------------------------------------
alter table public.brands enable row level security;

create policy brands_operator_select on public.brands
  for select using (app.is_workspace_member(workspace_id));

create policy brands_operator_modify on public.brands
  for all using (app.is_workspace_member(workspace_id))
         with check (app.is_workspace_member(workspace_id));

-- ---------------------------------------------------------------------------
-- brand_assets
-- ---------------------------------------------------------------------------
alter table public.brand_assets enable row level security;

create policy brand_assets_operator_select on public.brand_assets
  for select using (
    exists (
      select 1 from public.brands b
      where b.id = brand_assets.brand_id
        and app.is_workspace_member(b.workspace_id)
    )
  );

create policy brand_assets_operator_modify on public.brand_assets
  for all using (
    exists (
      select 1 from public.brands b
      where b.id = brand_assets.brand_id
        and app.is_workspace_member(b.workspace_id)
    )
  )
  with check (
    exists (
      select 1 from public.brands b
      where b.id = brand_assets.brand_id
        and app.is_workspace_member(b.workspace_id)
    )
  );

-- ---------------------------------------------------------------------------
-- clients
-- ---------------------------------------------------------------------------
alter table public.clients enable row level security;

create policy clients_operator_select on public.clients
  for select using (app.is_workspace_member(workspace_id));

create policy clients_operator_modify on public.clients
  for all using (app.is_workspace_member(workspace_id))
         with check (app.is_workspace_member(workspace_id));

-- ---------------------------------------------------------------------------
-- client_portals
-- ---------------------------------------------------------------------------
alter table public.client_portals enable row level security;

-- Operator: visible if you own the parent client's workspace.
create policy client_portals_operator_select on public.client_portals
  for select using (
    exists (
      select 1 from public.clients c
      where c.id = client_portals.client_id
        and app.is_workspace_member(c.workspace_id)
    )
  );

create policy client_portals_operator_modify on public.client_portals
  for all using (
    exists (
      select 1 from public.clients c
      where c.id = client_portals.client_id
        and app.is_workspace_member(c.workspace_id)
    )
  )
  with check (
    exists (
      select 1 from public.clients c
      where c.id = client_portals.client_id
        and app.is_workspace_member(c.workspace_id)
    )
  );

-- Client: read your own portal row only.
create policy client_portals_client_select on public.client_portals
  for select using (app.is_portal_member(id));

-- ---------------------------------------------------------------------------
-- client_portal_members
-- ---------------------------------------------------------------------------
alter table public.client_portal_members enable row level security;

-- Operator: visible if you own the parent portal.
create policy client_portal_members_operator_select on public.client_portal_members
  for select using (
    exists (
      select 1
      from public.client_portals cp
      join public.clients c on c.id = cp.client_id
      where cp.id = client_portal_members.portal_id
        and app.is_workspace_member(c.workspace_id)
    )
  );

create policy client_portal_members_operator_modify on public.client_portal_members
  for all using (
    exists (
      select 1
      from public.client_portals cp
      join public.clients c on c.id = cp.client_id
      where cp.id = client_portal_members.portal_id
        and app.is_workspace_member(c.workspace_id)
    )
  )
  with check (
    exists (
      select 1
      from public.client_portals cp
      join public.clients c on c.id = cp.client_id
      where cp.id = client_portal_members.portal_id
        and app.is_workspace_member(c.workspace_id)
    )
  );

-- Client: visible only to the client themselves.
create policy client_portal_members_self_select on public.client_portal_members
  for select using (profile_id = app.current_profile_id());

-- ---------------------------------------------------------------------------
-- campaigns
-- ---------------------------------------------------------------------------
alter table public.campaigns enable row level security;

create policy campaigns_operator_select on public.campaigns
  for select using (
    exists (
      select 1 from public.brands b
      where b.id = campaigns.brand_id
        and app.is_workspace_member(b.workspace_id)
    )
  );

create policy campaigns_operator_modify on public.campaigns
  for all using (
    exists (
      select 1 from public.brands b
      where b.id = campaigns.brand_id
        and app.is_workspace_member(b.workspace_id)
    )
  )
  with check (
    exists (
      select 1 from public.brands b
      where b.id = campaigns.brand_id
        and app.is_workspace_member(b.workspace_id)
    )
  );

-- Client: visible only via the linked portal.
create policy campaigns_client_select on public.campaigns
  for select using (
    client_portal_id is not null
    and app.is_portal_member(client_portal_id)
  );

-- ---------------------------------------------------------------------------
-- content_calendars
-- ---------------------------------------------------------------------------
alter table public.content_calendars enable row level security;

create policy content_calendars_operator_select on public.content_calendars
  for select using (
    exists (
      select 1
      from public.campaigns ca
      join public.brands b on b.id = ca.brand_id
      where ca.id = content_calendars.campaign_id
        and app.is_workspace_member(b.workspace_id)
    )
  );

create policy content_calendars_operator_modify on public.content_calendars
  for all using (
    exists (
      select 1
      from public.campaigns ca
      join public.brands b on b.id = ca.brand_id
      where ca.id = content_calendars.campaign_id
        and app.is_workspace_member(b.workspace_id)
    )
  )
  with check (
    exists (
      select 1
      from public.campaigns ca
      join public.brands b on b.id = ca.brand_id
      where ca.id = content_calendars.campaign_id
        and app.is_workspace_member(b.workspace_id)
    )
  );

-- Client: visible if the parent campaign is on their portal.
create policy content_calendars_client_select on public.content_calendars
  for select using (
    exists (
      select 1 from public.campaigns ca
      where ca.id = content_calendars.campaign_id
        and ca.client_portal_id is not null
        and app.is_portal_member(ca.client_portal_id)
    )
  );

-- ---------------------------------------------------------------------------
-- content_items
--   Operator policies live on the BASE table.
--   The client policy lives on client_content_items_v (see below).
-- ---------------------------------------------------------------------------
alter table public.content_items enable row level security;

create policy content_items_operator_select on public.content_items
  for select using (
    exists (
      select 1
      from public.campaigns ca
      join public.brands b on b.id = ca.brand_id
      where ca.id = content_items.campaign_id
        and app.is_workspace_member(b.workspace_id)
    )
  );

create policy content_items_operator_modify on public.content_items
  for all using (
    exists (
      select 1
      from public.campaigns ca
      join public.brands b on b.id = ca.brand_id
      where ca.id = content_items.campaign_id
        and app.is_workspace_member(b.workspace_id)
    )
  )
  with check (
    exists (
      select 1
      from public.campaigns ca
      join public.brands b on b.id = ca.brand_id
      where ca.id = content_items.campaign_id
        and app.is_workspace_member(b.workspace_id)
    )
  );

-- Client policy lives on the view (security_invoker = true means the
-- view inherits the caller's grants, and since the base table denies
-- clients, the view also denies them — UNLESS we add a permissive policy
-- below that re-grants SELECT on the base table when joined through the
-- view's filter. The simpler pattern is: grant SELECT on the view to
-- authenticated, deny on the base table to clients, and rely on the
-- WHERE clause in the view definition + a permissive client policy
-- scoped to shared rows below.
create policy content_items_client_select on public.content_items
  for select using (
    shared_with_client = true
    and status in ('shared_with_client',
                   'approved_by_client',
                   'changes_requested_by_client')
    and exists (
      select 1 from public.campaigns ca
      where ca.id = content_items.campaign_id
        and ca.client_portal_id is not null
        and app.is_portal_member(ca.client_portal_id)
    )
  );

-- Even though we allow SELECT on content_items for clients above, the
-- API layer in Phase 1C MUST route client queries through
-- client_content_items_v so operator-only columns are not exposed. The
-- view drops the operator-only columns at the schema level — it is the
-- belt to the policy's braces.

-- ---------------------------------------------------------------------------
-- generated_assets
-- ---------------------------------------------------------------------------
alter table public.generated_assets enable row level security;

create policy generated_assets_operator_select on public.generated_assets
  for select using (
    exists (
      select 1
      from public.content_items ci
      join public.campaigns ca on ca.id = ci.campaign_id
      join public.brands b on b.id = ca.brand_id
      where ci.id = generated_assets.content_item_id
        and app.is_workspace_member(b.workspace_id)
    )
  );

create policy generated_assets_operator_modify on public.generated_assets
  for all using (
    exists (
      select 1
      from public.content_items ci
      join public.campaigns ca on ca.id = ci.campaign_id
      join public.brands b on b.id = ca.brand_id
      where ci.id = generated_assets.content_item_id
        and app.is_workspace_member(b.workspace_id)
    )
  )
  with check (
    exists (
      select 1
      from public.content_items ci
      join public.campaigns ca on ca.id = ci.campaign_id
      join public.brands b on b.id = ca.brand_id
      where ci.id = generated_assets.content_item_id
        and app.is_workspace_member(b.workspace_id)
    )
  );

-- Client: only the thumbnail row is exposed, and only when the parent
-- content_item is shared. Internal raw/audio_fixed paths stay hidden.
create policy generated_assets_client_select on public.generated_assets
  for select using (
    kind = 'thumbnail'
    and exists (
      select 1
      from public.content_items ci
      join public.campaigns ca on ca.id = ci.campaign_id
      where ci.id = generated_assets.content_item_id
        and ci.shared_with_client = true
        and ci.status in ('shared_with_client',
                          'approved_by_client',
                          'changes_requested_by_client')
        and ca.client_portal_id is not null
        and app.is_portal_member(ca.client_portal_id)
    )
  );

-- ---------------------------------------------------------------------------
-- content_feedback
-- ---------------------------------------------------------------------------
alter table public.content_feedback enable row level security;

-- Operator sees everything on their workspace's content.
create policy content_feedback_operator_select on public.content_feedback
  for select using (
    exists (
      select 1
      from public.content_items ci
      join public.campaigns ca on ca.id = ci.campaign_id
      join public.brands b on b.id = ca.brand_id
      where ci.id = content_feedback.content_item_id
        and app.is_workspace_member(b.workspace_id)
    )
  );

-- Operator can insert feedback on their workspace's content.
create policy content_feedback_operator_insert on public.content_feedback
  for insert with check (
    author_kind = 'operator'
    and exists (
      select 1
      from public.content_items ci
      join public.campaigns ca on ca.id = ci.campaign_id
      join public.brands b on b.id = ca.brand_id
      where ci.id = content_feedback.content_item_id
        and app.is_workspace_member(b.workspace_id)
    )
  );

-- Client can read their own portal's feedback (their messages + the
-- operator's replies that the operator chose to leave on a shared item).
create policy content_feedback_client_select on public.content_feedback
  for select using (
    exists (
      select 1
      from public.content_items ci
      join public.campaigns ca on ca.id = ci.campaign_id
      where ci.id = content_feedback.content_item_id
        and ci.shared_with_client = true
        and ca.client_portal_id is not null
        and app.is_portal_member(ca.client_portal_id)
    )
  );

-- Client can insert feedback on a shared item on their portal.
create policy content_feedback_client_insert on public.content_feedback
  for insert with check (
    author_kind = 'client'
    and exists (
      select 1
      from public.content_items ci
      join public.campaigns ca on ca.id = ci.campaign_id
      where ci.id = content_feedback.content_item_id
        and ci.shared_with_client = true
        and ca.client_portal_id is not null
        and app.is_portal_member(ca.client_portal_id)
    )
  );

-- ---------------------------------------------------------------------------
-- content_approvals
-- ---------------------------------------------------------------------------
alter table public.content_approvals enable row level security;

create policy content_approvals_operator_select on public.content_approvals
  for select using (
    exists (
      select 1
      from public.content_items ci
      join public.campaigns ca on ca.id = ci.campaign_id
      join public.brands b on b.id = ca.brand_id
      where ci.id = content_approvals.content_item_id
        and app.is_workspace_member(b.workspace_id)
    )
  );

create policy content_approvals_operator_insert on public.content_approvals
  for insert with check (
    actor_kind = 'operator'
    and exists (
      select 1
      from public.content_items ci
      join public.campaigns ca on ca.id = ci.campaign_id
      join public.brands b on b.id = ca.brand_id
      where ci.id = content_approvals.content_item_id
        and app.is_workspace_member(b.workspace_id)
    )
  );

create policy content_approvals_client_select on public.content_approvals
  for select using (
    exists (
      select 1
      from public.content_items ci
      join public.campaigns ca on ca.id = ci.campaign_id
      where ci.id = content_approvals.content_item_id
        and ci.shared_with_client = true
        and ca.client_portal_id is not null
        and app.is_portal_member(ca.client_portal_id)
    )
  );

create policy content_approvals_client_insert on public.content_approvals
  for insert with check (
    actor_kind = 'client'
    and exists (
      select 1
      from public.content_items ci
      join public.campaigns ca on ca.id = ci.campaign_id
      where ci.id = content_approvals.content_item_id
        and ci.shared_with_client = true
        and ca.client_portal_id is not null
        and app.is_portal_member(ca.client_portal_id)
    )
  );

-- ---------------------------------------------------------------------------
-- content_requests
-- ---------------------------------------------------------------------------
alter table public.content_requests enable row level security;

create policy content_requests_operator_select on public.content_requests
  for select using (
    exists (
      select 1
      from public.client_portals cp
      join public.clients c on c.id = cp.client_id
      where cp.id = content_requests.client_portal_id
        and app.is_workspace_member(c.workspace_id)
    )
  );

create policy content_requests_client_insert on public.content_requests
  for insert with check (
    app.is_portal_member(client_portal_id)
  );

create policy content_requests_client_select on public.content_requests
  for select using (app.is_portal_member(client_portal_id));

-- ---------------------------------------------------------------------------
-- TODO(phase-1c) — items deferred from this migration:
--   1. Owner-only role enforcement on workspace_members writes
--      (split app.is_workspace_member → is_member / is_owner).
--   2. Tighten content_items_client_select so it reads only via the view.
--   3. Wire profile auto-creation on auth.users insert via a trigger.
--   4. Add an UPDATE policy that lets operators flip
--      shared_with_client / audio_fixer_* but blocks clients from
--      writing those fields (clients only write through feedback +
--      approvals + content_requests).
--   5. Replace app.is_portal_member with a JWT-claim helper
--      (auth.jwt() ->> 'portal_slug') for the unauthenticated-share
--      link case once the share-token model is decided.
-- ============================================================================
