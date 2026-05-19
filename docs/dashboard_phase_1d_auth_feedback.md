# Yuvo OS — Phase 1D: Supabase Auth + persisted client feedback

Phase 1D promotes the dashboard from "structurally wired" to "actually
useful for a client". After Phase 1D:

- Operators sign in to the agency console via magic-link.
- Clients sign in to their private review portal via magic-link.
- Clients can **Approve**, **Request changes (with a structured reason)**,
  and **Comment** — all persisted to Supabase.
- Operators see those approvals + comments inline on the outputs page.
- Demo mode keeps working with in-memory feedback so the showcase still
  runs without any Supabase config.

## What ships in Phase 1D

| Asset | Path | Purpose |
|---|---|---|
| Dep | `@supabase/ssr ^0.10.x` | Next.js cookie-aware Supabase clients |
| Migration | `supabase/migrations/003_auth_handle_new_user.sql` | `handle_new_user` trigger, auto-link invited portal members, profile FK to `auth.users`, `app.current_email()` helper |
| Server clients | `web/lib/supabase/server.ts` | `getServerSupabase()` (cookies + RLS), `getServiceRoleSupabase()` (admin), `hasSupabaseEnv()` |
| Browser client | `web/lib/supabase/browser.ts` | `getBrowserSupabase()` for client components |
| Persona resolver | `web/lib/auth/persona.ts` | `getCurrentPersona()` returns operator / client / unaffiliated / null |
| Auth actions | `web/lib/actions/auth.ts` | `sendMagicLink(email, redirectTo)`, `signOut()` |
| Feedback actions | `web/lib/actions/client-feedback.ts` | `approveContentAction`, `requestChangesContentAction`, `commentContentAction` |
| Feedback reads | `web/lib/data/feedback.ts` | `getContentFeedback`, `getContentApprovals` + `FEEDBACK_REASON_LABELS` |
| Operator login | `web/app/login/page.tsx` | Magic-link form; demo-mode notice |
| Client login | `web/app/client/[portalSlug]/login/page.tsx` | Per-portal magic-link form |
| Callback | `web/app/auth/callback/route.ts` | OTP-code exchange + cookie write |
| Login form | `web/components/auth/login-form.tsx` | Shared client component |
| Logout button | `web/components/auth/logout-button.tsx` | Calls `signOut` action |
| Client feedback panel | `web/components/content/client-feedback-panel.tsx` | Replaces the local-state ApprovalControls on the client page |
| Operator feedback summary | `web/components/content/client-feedback-summary.tsx` | Renders approvals + comments on agency outputs page |
| Updated pages | `web/app/agency/layout.tsx`, `web/app/agency/campaigns/[campaignId]/outputs/page.tsx`, `web/app/client/[portalSlug]/layout.tsx`, `web/app/client/[portalSlug]/content/[contentId]/page.tsx`, `web/app/page.tsx`, `web/components/layout/agency-topbar.tsx` | Auth gates, sign-in CTAs, feedback wiring |

## Auth model

```
                 ┌────────────────────┐
                 │  /login            │  ←  operators
                 │  /client/<slug>/   │  ←  invited clients
                 │  login             │
                 └─────────┬──────────┘
                           │ email + magic link
                           ▼
              supabase.auth.signInWithOtp()
                           │
                           │ email arrives in inbox
                           ▼
              user clicks link → /auth/callback?code=...&next=...
                           │
              exchangeCodeForSession() + cookie set
                           │
                           ▼
                     redirect to `next`
```

### Persona resolution

`web/lib/auth/persona.ts::getCurrentPersona()` is called from server
components (agency + client layouts) and from server actions before any
mutation. It returns:

- `{ kind: "operator", userId, email, workspaceIds }` — user is in
  `workspace_members`. Wins when also a client.
- `{ kind: "client", userId, email, portalIds }` — user is in
  `client_portal_members`.
- `{ kind: "unaffiliated", userId, email }` — signed in but neither.
- `null` — not signed in (or demo mode).

The lookup uses the **service-role** client so the `workspace_members`
/ `client_portal_members` query isn't blocked by the same RLS
helpers it powers. Service-role is server-only and never exposed.

### Profile auto-creation & portal-member linking

`supabase/migrations/003_auth_handle_new_user.sql` installs a trigger
on `auth.users` insert that:

1. Inserts a `public.profiles` row keyed on the new `auth.users.id`.
   Display name comes from `raw_user_meta_data.display_name` →
   `raw_user_meta_data.name` → email local-part → `"Member"`.
2. Updates any pending `client_portal_members` row whose
   `invite_email` (case-insensitively) matches the new user's email:
   sets `profile_id = NEW.id` and `joined_at = now()`. **This is the
   one-step client onboarding** — the operator only needs to insert a
   row with `invite_email = 'clientb@example.com'`; the next time that
   email signs in they're automatically a portal member.
3. Adds a deferred FK `profiles.id → auth.users(id) on delete cascade`
   (skipped if already present).

The migration also installs `app.current_email()` for Phase 1E to use
in a JWT-claim policy.

### Operator onboarding

For an MVP brand-new Supabase project the steps are:

1. Apply migrations 001, 002, 003 + `seed.sql`.
2. Sign up the operator via `/login` (or via the Supabase dashboard).
3. In the Supabase SQL editor, manually link the new user to the
   workspace:

   ```sql
   insert into public.workspace_members (workspace_id, profile_id, role)
   values ('11111111-1111-1111-1111-111111111111',
           '<auth.uid() of the newly-signed-in operator>',
           'owner');
   ```

   The placeholder operator profile (`22222222-…`) in seed.sql remains
   for reference but is no longer used once a real auth user exists.

### Client onboarding

For each client invited to a portal:

1. Operator (or seeded SQL) inserts a row into `client_portal_members`:

   ```sql
   insert into public.client_portal_members (portal_id, invite_email)
   values ('<portal_uuid>', 'client@brand.example');
   ```

2. Client receives the portal URL (`/client/<slug>`) — typically via
   email from the operator.
3. Client visits `/client/<slug>/login`, enters their email, clicks
   the magic-link in their inbox.
4. The `handle_new_user` trigger:
   - creates `profiles` row
   - links the pending `client_portal_members` row (sets `profile_id`,
     `joined_at`)
5. The portal layout's auth gate now allows them in.

If the client's email doesn't match a pending invite, they end up as
`unaffiliated` and the portal layout redirects them to `/login` — which
prevents a stranger who happens to know the portal slug from poking at
the data.

## Feedback write flow

Three server actions, all in `web/lib/actions/client-feedback.ts`:

```
approveContentAction({ portalSlug, contentId, note? })
requestChangesContentAction({ portalSlug, contentId, reason, body })
commentContentAction({ portalSlug, contentId, body })
```

Each one:

1. **Demo mode** (`DATA_SOURCE !== "supabase"`): push the entry into
   the in-memory store in `feedback.ts` and return a "demo mode" message.
   No network calls.
2. **Supabase mode**:
   a. Resolve the session via `getServerSupabase().auth.getUser()`.
   b. `isPortalMember(userId, portalSlug)` — service-role lookup.
   c. Service-role fetch of the content row, asserting the
      `content_items.campaign_id` → `campaigns.client_portal_id`
      matches the portal and `shared_with_client = true`.
   d. Service-role INSERT into `content_feedback` and/or
      `content_approvals`. On approve / request-changes, also UPDATE
      `content_items.status`.
   e. `revalidatePath` on the portal pages so the next render reflects
      the change.

### Why service-role for writes?

Phase 1B's RLS for the **client persona** only grants:

- `INSERT` on `content_feedback` and `content_approvals`
- `SELECT` on `content_items` (filtered to shared rows)
- **NO** `UPDATE` on `content_items`

So a client cannot legitimately flip `content_items.status` to
`approved_by_client` via a per-row policy in the current schema. Two
options:

1. (Phase 1E plan) Add a per-row UPDATE policy that grants the client
   the ability to set `status ∈ {approved_by_client,
   changes_requested_by_client}` IFF they're a portal member of the
   linked campaign.
2. (Phase 1D pragmatic) Validate ownership in server code, then write
   with service-role.

Phase 1D chose option 2 because:

- the validation logic for option 1 is non-trivial to express as a
  per-column CHECK in SQL, and
- the server-side ownership check is exactly the same gate the policy
  would express, just authored in TypeScript where it's easier to
  audit.

Phase 1E will install the policy and drop the service-role dependency
from these three actions.

### Structured reason

The `content_feedback` table doesn't have a dedicated `reason` column
in Phase 1B's schema, so Phase 1D encodes the reason as a `[reason:<key>]`
prefix on `content_feedback.body`. `feedback.ts::joinReasonBody` writes
it; `splitReasonBody` parses it back. Eight reasons:

```
wrong_tone, wrong_product, not_on_brand, bad_voice, bad_face_hands,
bad_caption, different_offer_needed, other
```

A future migration can split this into a real column without disturbing
the wire shape — `splitReasonBody` will simply find no prefix on new
rows and fall back to a separate `reason` column read.

## RLS assumptions

The Phase 1B + 1C RLS posture is unchanged. Phase 1D adds:

- a trigger on `auth.users` (security-definer; the only writer)
- the `app.current_email()` helper (consumed by Phase 1E)
- a FK `profiles.id → auth.users(id)`

What still relies on the operator using the service-role escape hatch:

- Any `INSERT` / `UPDATE` triggered by a server action on the client
  side (covered above).
- The persona-resolution `SELECT` against
  `workspace_members`/`client_portal_members` (recursive helpers
  prevent self-query).

What now works **without** the service-role:

- Client-portal reads of `client_content_items_v` (the existing client
  policy + the new portal-member linkage flow).
- Client-portal reads of `client_portals` for the signed-in member's
  portal slug.
- `content_feedback` / `content_approvals` SELECTs by the client (the
  existing policies are now satisfied because `auth.uid()` returns the
  real user id).

## Demo limitations

In demo mode (no Supabase config):

- `/login` shows a "demo mode" notice with copy/paste instructions.
- `/client/<slug>/login` shows the same; the demo portal is still
  reachable directly without auth.
- The client feedback panel works fully but writes go into an
  in-memory store (resets on server restart).
- Agency-side `ClientFeedbackSummary` reads that same in-memory store,
  so demo approvals appear on the agency outputs page during the
  current dev session.
- Magic-link emails: no email is sent. The `sendMagicLink` action
  returns an "ok: false" with the demo-mode error message.

## Local testing notes

Without the Supabase CLI installed, the new migration
(`003_auth_handle_new_user.sql`) has **not** been applied locally. To
apply it against a hosted project:

1. Open the Supabase SQL editor for your project.
2. Paste + run the contents of `003_auth_handle_new_user.sql`.
3. Verify the trigger exists:
   ```sql
   select tgname from pg_trigger where tgname = 'handle_new_user_trg';
   ```
4. Optionally test: insert a `client_portal_members` row with an
   `invite_email` matching a Supabase Auth user you plan to sign in
   as, then sign in via `/client/<slug>/login` and confirm the row
   linked (`profile_id` not null, `joined_at` populated).

## What remains for Phase 1E

| Item | Why |
|---|---|
| Per-policy UPDATE on `content_items.status` for the client persona | Drop service-role dependency from feedback actions |
| Wire the "what would you like next week" form to `content_requests` | The form is still inert |
| Operator-side reply-on-feedback (insert `content_feedback` with `author_kind='operator'`) | Two-way conversation |
| Owner-only enforcement on `workspace_members` writes | Currently any workspace_member can mutate the table |
| Switch client SELECT off the `content_items` base table and onto `client_content_items_v` exclusively | Tighten the schema-level guarantee |
| Realtime updates (Supabase subscriptions on `content_feedback`) | Conversation feels live without manual refresh |
| Optional share-token flow for clients who don't want to create a Supabase Auth account | UX for non-technical brand owners |

## Verification

```bash
cd web
npm run typecheck
npm run build

# Repo root
py -3.11 -m ruff check .
py -3.11 -m pytest tests/test_enhancor_providers.py tests/test_enhancor_smoke_payloads.py -v
```

No paid API calls. No deploy. No commit.
