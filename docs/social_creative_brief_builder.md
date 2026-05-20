# Social Creative Brief Builder (Phase 4A)

Status: **shipped (code-only)** — agent, server action, queue reader,
operator page, and sidebar link. **Read-only with respect to the
client portal**. Internal planning artefact only.

## Purpose

Extend the dashboard beyond UGC video ads and copy drafts. For every
non-video social format the operator now has a deterministic, no-API
"Creative brief" pass that specs the **visual** side of a post:

- Instagram feed posts
- Instagram Stories
- Instagram carousels
- Facebook posts
- LinkedIn text posts (with optional companion image concept)
- static image creatives
- visual support briefs for organic Reels / short videos
- visual support briefs for paid UGC video ads

The brief is **planning only**. It never generates a final image, never
publishes, never shares with the client, never calls a paid API.

## Supported formats → brief modes

| Content format     | Brief mode             | Output shape (in addition to universal fields)            |
|--------------------|------------------------|-----------------------------------------------------------|
| `carousel`         | `carousel`             | `slides[]` (5×) — headline / body / visual / layout       |
| `story`            | `story`                | `frames[]` (3×) — overlay / visual / sticker              |
| `feed_post`        | `feed_post`            | `mainVisual` / `headlineOverlay` / `captionSupport`       |
| `static_image`     | `static_image`         | same as `feed_post`, copy-light variant                   |
| `text_post` (LI)   | `linkedin_text`        | `postHook` / `imageConcept?` / `professionalToneNotes`    |
| `organic_reel`     | `reel_support`         | `thumbnailConcept` / `onScreenTextBeats[]` / `brollCues[]`|
| `short_video`      | `reel_support`         | same as above                                             |
| `ugc_video_ad`     | `video_visual_support` | `thumbnailConcept` / `hookFrame` / `propBrief`            |
| `long_video`       | `video_visual_support` | same as above                                             |
| `email_snippet`    | (excluded)             | pure copy — no visual brief                               |
| `blog_snippet`     | (excluded)             | pure copy — no visual brief                               |

Universal fields on every brief:
`creativeDirection`, `visualConcept`, `layoutType`, `assetRequirements[]`,
`copyPlacement`, `shotOrDesignNotes`, `brandElements[]`, `doNotInclude[]`,
`callToAction`, `variants[]`.

## Output model

Defined in `web/lib/agents/social-creative-brief.ts`:

```ts
interface CreativeBrief {
  contentItemId: string;
  channel: ContentChannel;
  format: ContentFormat;
  mode: CreativeBriefMode;
  distributionType: string | null;
  contentGoal: string | null;
  title: string;
  // universal
  creativeDirection: string;
  visualConcept: string;
  layoutType: string;
  assetRequirements: string[];
  copyPlacement: string;
  shotOrDesignNotes: string;
  brandElements: string[];
  doNotInclude: string[];
  callToAction: string;
  variants: CreativeBriefVariant[];
  // format-specific (one is populated)
  slides?: CarouselSlide[];
  frames?: StoryFrame[];
  mainVisual?: string;
  headlineOverlay?: string;
  captionSupport?: string;
  compositionNotes?: string;
  imageConcept?: string;
  postHook?: string;
  professionalToneNotes?: string;
  thumbnailConcept?: string;
  onScreenTextBeats?: string[];
  brollCues?: string[];
  hookFrame?: string;
  propBrief?: string;
}
```

The agent returns `{ brief, markdown, caveats }`. `markdown` is a
deterministic, human-readable rendering used both for the UI preview and
as the body of the `[creative brief]` block.

## Storage strategy

**No new table, no migration.** Briefs are appended as a structured
provenance block on `content_items.prompt_summary`, following the exact
same idempotent strip-then-append pattern already used for
`[copy draft]`, `[copy approval]`, and `[client copy preview]`:

```
\n\n[creative brief]\n
creative_brief_status: drafted
creative_brief_source: social_creative_brief_agent
creative_brief_format: <ContentFormat>
creative_brief_channel: <ContentChannel>
creative_brief_mode: <CreativeBriefMode>
creative_brief_created_at: <ISO timestamp>
creative_brief_operator_note: <optional, ≤500 chars>

# Creative brief — <title>
…readable markdown…
_Planning brief only. No final asset has been generated. Internal — do not share with the client._
```

### Why this is safe

- `prompt_summary` is operator-only. The client portal view
  (`client_content_items_v`, migration 009) does NOT project
  `prompt_summary`, so the brief is structurally invisible to clients —
  RLS does not even need to gate it.
- The action writes **only** `prompt_summary` on **one** row. It does
  NOT touch `caption_draft`, `shared_with_client`,
  `client_safe_copy_preview`, `status`, or any other column on
  `content_items`.
- It does NOT touch any other table.
- Re-running the action strips the prior `[creative brief]` block and
  appends a fresh one (idempotent).

### When to migrate

If briefs grow large or operators frequently A/B variants, propose a
**future** migration (not in Phase 4A):

```sql
-- supabase/migrations/0XX_creative_briefs.sql (PROPOSAL — not applied)
create table public.creative_briefs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  content_item_id uuid not null references public.content_items(id) on delete cascade,
  status text not null check (status in ('drafted','superseded')),
  brief_json jsonb not null,
  brief_markdown text not null,
  created_by uuid references public.profiles(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
-- operator-only RLS via app.is_workspace_member(workspace_id)
```

Phase 4A deliberately ships without it; do not apply unless explicitly
approved.

## Safety boundaries

The action `createSocialCreativeBriefAction(input)`
(`web/lib/actions/social-creative-brief.ts`) is constrained by hard
rules:

- Operator-only persona gate (`getCurrentPersona().kind === "operator"`).
- Content item must be in an operator-editable status — never under
  client sign-off (mirrors the copy-draft `EDITABLE_STATUSES` set).
- Single column written: `content_items.prompt_summary` (PATCH on one
  row, idempotent strip-then-append).
- NO image generation. NO Seedance/Enhancor/Audio Fixer. NO Anthropic /
  OpenAI / image API. NO `fetch()` to anything. NO `child_process` /
  `spawn` / `exec`. NO email. NO publish. NO worker.
- NO change to `caption_draft`, `shared_with_client`,
  `client_safe_copy_preview`, `status`, or to any other table.

The agent itself (`planSocialCreativeBrief`) is **pure** — same input
always produces the same output; no clock dependency other than the
caller-supplied timestamps in the action.

## How this differs from final asset generation

| Concern                | Creative brief (this phase)            | Final asset generation (future)         |
|------------------------|----------------------------------------|------------------------------------------|
| Output                 | structured planning brief (text)       | image / carousel / story assets          |
| Generates pixels?      | **no**                                 | yes                                      |
| Calls paid API?        | **no**                                 | likely                                   |
| Client visible?        | **no** (lives in `prompt_summary`)     | only after explicit operator share       |
| Mutates Supabase?      | `prompt_summary` on one row            | inserts to a future assets table         |
| Operator click count   | 1 (Create creative brief)              | ≥2 (preview → approve → submit gated)    |

## Operator UI

- New route `/agency/creative-briefs` lists workspace content items that
  benefit from a visual brief. Stats: total / need brief / drafted.
  Each row shows current state plus a `CreativeBriefPanel` with a single
  "Create creative brief" button and an optional ≤500-char note.
- Sidebar nav entry "Creative briefs (social)" under
  "Copy drafts (non-video)".
- The drafted brief is previewed inline (collapsible markdown). It is
  **not** posted to a `[client copy preview]` block, **not** marked
  shared, and **not** copied into `caption_draft`.

## Phase 4B — what comes next (not in scope)

Phase 4A intentionally stops at the planning brief. Phase 4B will
decide how the brief becomes pixels:

1. **Image generation** (DALL·E / Imagen / Stable Diffusion): operator
   submits the brief through a gated server action; preview only;
   never auto-publish; never auto-share with the client. Cost
   estimation behind an explicit confirmation phrase, like Seedance.
2. **HTML/CSS templates**: deterministic, programmatic export of
   carousel slides / story frames / static posts via a headless
   renderer. No paid API; faster; lower fidelity than image gen.
3. **Canvas-based exports**: server-side canvas → JPG/PNG for the
   stricter brand-locked layouts.
4. **Client preview for visuals**: a new `client_safe_visual_preview`
   field (or new table) so a designed mock can be reviewed before a
   final export — same shape as the existing
   `client_safe_copy_preview` lifecycle (`prepared` →
   `shared_with_client`), never automatic.

Phase 4B will propose its migration as a separate document.
