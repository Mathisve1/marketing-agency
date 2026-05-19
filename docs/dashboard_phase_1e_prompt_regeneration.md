# Yuvo OS — Phase 1E: Prompt versioning + regenerate-request workflow

Phase 1E lands the operator-side surface needed to **act on client
feedback**: edit prompts, version them, and queue regeneration
requests — all without spending a single Enhancor credit. After Phase
1E:

- Every `content_items` row owns a stack of editable `prompt_versions`.
  The operator iterates in a dedicated editor route and marks one
  version "approved for generation" when they're ready to spend.
- Every client change-request automatically opens a structured
  `regeneration_request` the operator sees on the outputs page with
  Accept / Dismiss / Open prompt editor controls.
- The previously-inert "What would you like to see next week?" form
  on the client portal is finally wired to `content_requests`, and the
  agency dashboard surfaces the incoming queue.
- The Phase 1B client-safe boundary remains intact: clients see none of
  the prompt fields, none of the cost numbers, none of the operator
  workflow state.

Phase 1E does **NOT**:

- Trigger any paid Enhancor / Seedance / Audio Fixer call. "Mark
  approved for generation" only records operator intent.
- Drop the Phase 1D service-role escape hatch for the
  `content_items.status` flip — that's still a Phase 1F TODO.
- Remove Streamlit, the Kling code, or any agent-side production code.

## What ships in Phase 1E

| Asset | Path | Purpose |
|---|---|---|
| Migration | `supabase/migrations/004_prompt_versions_regeneration.sql` | `prompt_versions`, `regeneration_requests`, `app.workspace_id_for_content`, `latest_prompt_version_v`, RLS |
| Seed | `supabase/seed.sql` (extended) | Two `prompt_versions` rows: `d…` 1080p superseded + `e…` 720p `operator_editing` with stricter label-text guard |
| Reader | `web/lib/data/content-requests.ts` | `listContentRequestsForPortal`, `listContentRequestsForWorkspace`, demo store |
| Reader | `web/lib/data/regeneration-requests.ts` | `listRegenerationRequestsForContent`, demo store + mutator |
| Reader | `web/lib/data/prompt-versions.ts` | `listPromptVersions`, `getLatestPromptVersion`, `getActivePromptVersion`, `getPromptVersionById`, demo store seeded with two versions |
| Action | `web/lib/actions/content-requests.ts` | `createContentRequestAction` |
| Action | `web/lib/actions/regeneration-requests.ts` | `accept…`, `dismiss…`, `markRegenerationRequestFulfilled…` |
| Action | `web/lib/actions/prompt-versions.ts` | `savePromptVersionDraftAction`, `markPromptVersionApprovedForGenerationAction`, `createPromptVersionFromRegenerationRequestAction` |
| Action (extended) | `web/lib/actions/client-feedback.ts` | `requestChangesContentAction` now also opens a `regeneration_request` |
| Component | `web/components/content/next-week-request-form.tsx` | Client-side wired form for `createContentRequestAction` |
| Component | `web/components/content/regeneration-requests-panel.tsx` | Operator queue panel mounted on the outputs page |
| Component | `web/components/content/regeneration-request-controls.tsx` | Per-row Accept / Dismiss controls (client component) |
| Component | `web/components/content/prompt-version-editor.tsx` | The full operator editor form |
| Component | `web/components/content/fork-prompt-from-request-button.tsx` | One-click "Fork new version from this request" |
| Route | `web/app/agency/campaigns/[campaignId]/content/[contentId]/prompt/page.tsx` | Operator-only prompt-version editor |
| Updated pages | `web/app/agency/page.tsx`, `web/app/agency/campaigns/[campaignId]/outputs/page.tsx`, `web/app/client/[portalSlug]/page.tsx`, `web/app/client/[portalSlug]/content/[contentId]/page.tsx`, `web/components/content/content-card.tsx` | Mount the new panels + wired form, surface the queue, link to the editor |

## Data model

### `prompt_versions`

Operator-only versioned prompt history per content item.

```
id                              uuid pk
content_item_id                 uuid → content_items(id) on delete cascade
version_number                  integer (unique per content_item_id)
label                           text
hook | script | prompt_body | negative_prompt | scene_plan |
  creator_direction | product_constraints   text
quality_tier                    'draft_480p' | 'standard_720p' | 'premium_1080p'
status                          'draft' | 'operator_editing' |
                                'approved_for_generation' | 'superseded'
notes                           text         (operator change-log)
parent_version_id               uuid (self)  nullable
source_regeneration_request_id  uuid → regeneration_requests(id) nullable
created_by                      uuid → profiles(id) nullable
created_at, updated_at          timestamptz
```

Constraints:

- `unique (content_item_id, version_number)` — version numbers are
  1-indexed per item.
- Partial unique `(content_item_id) where status = 'approved_for_generation'`
  — at most one approved-for-generation row per item at a time. The
  approve flow first supersedes any sibling.

RLS:

- **Operator full access** — `app.is_workspace_member(app.workspace_id_for_content(content_item_id))`.
- **No client policy at all.** Clients physically cannot read prompt
  versions.

### `regeneration_requests`

```
id                          uuid pk
content_item_id             uuid → content_items(id) on delete cascade
source_feedback_id          uuid → content_feedback(id) nullable
source_approval_id          uuid → content_approvals(id) nullable
requested_by_profile_id     uuid → profiles(id) nullable
requested_by_kind           'client' | 'operator'
reason                      same enum keys as FEEDBACK_REASON (nullable)
body                        text NOT NULL
status                      'open' | 'accepted' | 'dismissed' | 'fulfilled'
accepted_prompt_version_id  uuid → prompt_versions(id) nullable
resolved_at                 timestamptz
resolved_by_profile_id      uuid → profiles(id) nullable
created_at, updated_at      timestamptz
```

RLS:

- **Operator full access** — same workspace-membership predicate.
- **Client SELECT-only on rows they themselves requested**
  (`requested_by_kind='client' and requested_by_profile_id = app.current_profile_id()`).
  Clients do NOT INSERT — `requestChangesContentAction` writes via
  service-role after proving portal ownership.

### Helper function

```sql
app.workspace_id_for_content(target uuid) returns uuid
  -- walks content_items → campaigns → brands.workspace_id
  -- security definer; keeps RLS policies readable
```

### View

`public.latest_prompt_version_v` (`security_invoker = true`) — operator
convenience for "highest version_number per content item". Phase 1F
will swap in `content_items.active_prompt_version_id`.

## Workflows

### Client → operator (change request that opens a regeneration_request)

```
Client                          requestChangesContentAction
  |  Request changes + reason  ─────────────────────────────►  content_feedback
  |                                                            content_approvals
  |                                                            regeneration_requests (status=open)
  |                                                            bumpStatus → 'changes_requested_by_client'
  └─ revalidates portal page

Operator                        outputs page
  └─ sees Regenerate requests panel with the open row
     ├─ Accept                       → status='accepted'
     ├─ Dismiss + optional note      → status='dismissed' + operator content_feedback row back to the thread
     └─ Open prompt editor           → /agency/campaigns/<id>/content/<contentId>/prompt
```

### Operator iterates on a prompt version

```
Prompt editor route
  ├─ loads latest prompt_version (highest version_number)
  ├─ Save draft           → status='operator_editing' (sticky on 'approved_for_generation')
  ├─ Mark approved for    → supersedes any sibling 'approved_for_generation'
  │  generation              flips this row to 'approved_for_generation'
  │                          NO paid call is made.
  └─ Fork new version     → from an open regeneration_request:
     from this request       copies fields from the parent version
                             increments version_number
                             links source_regeneration_request_id
                             marks the request 'accepted'
```

### Client → operator (next-week intake)

```
Client                          createContentRequestAction
  |  "Could we test a 9-second cut..." ──────────────────►  content_requests row
  |                                                          (insert via service-role after portal ownership check)
  └─ revalidates portal home + agency home

Operator                        agency dashboard
  └─ Recent client requests panel surfaces the new row
```

## Service-role footprint

Phase 1E **adds** service-role writes to:

- `regeneration_requests` (every action: insert / update)
- `prompt_versions` (insert / update)
- `content_requests` (insert)

The pattern matches Phase 1D's: validate the persona + ownership in
app code (operator membership for prompt/regen, portal membership for
content_requests) **before** the service-role write.

This is conscious. Phase 1D's reason — "client UPDATE on
content_items.status requires a per-row policy we haven't written
yet" — applies symmetrically to prompt_versions / regeneration_requests
because their RLS predicates depend on the same workspace-membership
helper. Phase 1F may refactor these toward auth-cookie writes once a
real owner/operator/viewer role split lands.

## Demo limitations

In demo mode (`DATA_SOURCE !== "supabase"`):

- `prompt_versions` is seeded in-memory in `web/lib/data/prompt-versions.ts`
  with the two versions mirroring `supabase/seed.sql` so the prompt
  editor renders v1 immediately for both Pai content items.
- `regeneration_requests` and `content_requests` start empty. The
  client change-request flow + the next-week form push into the
  in-memory store; both reset when the dev server restarts.
- All operator-side actions on prompt versions + regeneration requests
  succeed without auth in demo mode (the persona resolver returns
  null and the actions skip the operator check). This is documented
  inline in each action.
- The agency dashboard reads the same in-memory store as the client
  portal, so a submitted next-week request appears across both views
  in the same dev process.

## RLS posture

What now works **without** the service-role:

- Operator `SELECT` on `prompt_versions` and `regeneration_requests`
  via `app.is_workspace_member(app.workspace_id_for_content(...))`.
- Client `SELECT` on their own `regeneration_requests` rows (if they
  signed in via magic-link).

What still relies on the service-role (unchanged from Phase 1D):

- Every WRITE in Phase 1D + Phase 1E (covered above).
- `getCurrentPersona` membership lookup.

## Local testing notes

Without the Supabase CLI installed, `004_prompt_versions_regeneration.sql`
has **not** been applied locally. To apply it against a hosted project:

1. Open the Supabase SQL editor.
2. Paste + run `004_prompt_versions_regeneration.sql`.
3. Verify the tables + view:
   ```sql
   select count(*) from public.prompt_versions;          -- 0 on fresh apply
   select count(*) from public.regeneration_requests;    -- 0
   select * from public.latest_prompt_version_v limit 1; -- empty
   ```
4. Re-run `seed.sql` to backfill the two `prompt_versions` rows. The
   `on conflict (id) do nothing` guards make this safe.

## End-to-end demo script

```bash
cd web
npm run dev
```

1. Visit `/agency/campaigns/camp_pai_route01/content/content_pai_route02_draft/prompt`.
   The 720p stricter-label-text version v1 loads in `operator_editing`.
2. Edit the negative prompt, click **Save draft** — flash confirms
   "Draft saved (demo mode — in-memory only)".
3. Click **Mark approved for generation** — the flash explicitly
   confirms NO paid call was made; the badge flips to
   "Approved for generation".
4. Visit `/client/pai-skincare-demo/content/content_pai_route01_v1`.
   Click **Request changes**, pick "Wrong tone", submit.
5. Visit `/agency/campaigns/camp_pai_route01/outputs`. Below content
   item 1's feedback summary, the new **Regenerate requests** panel
   shows the open row.
6. Click **Open prompt editor →**. The new prompt-editor route lists
   the open regeneration request at the top; click **Fork new version
   from this request**. The request flips to `accepted`, a v2 prompt
   version appears in the editor for iteration.
7. Back on `/agency` — the "Recent client requests" panel shows any
   next-week submissions you made from `/client/pai-skincare-demo`.

## What remains for Phase 1F

| Item | Why |
|---|---|
| `content_items.active_prompt_version_id` FK | Lets the operator pin a non-latest version as the one to generate with |
| Per-row UPDATE policy on `content_items.status` for the client persona | Drop the service-role dependency for the Phase 1D + 1E writes |
| `generation_runs` table linking `prompt_versions` → Enhancor request ids | Real generation handoff |
| Owner-only enforcement on `workspace_members` writes | Currently any workspace_member can mutate the table |
| Optional: split `content_feedback.reason` out of the `[reason:<key>]` prefix into a real column | Phase 1D codec is still in place |
| Realtime updates on `regeneration_requests` | Operator queue feels live without manual refresh |

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
