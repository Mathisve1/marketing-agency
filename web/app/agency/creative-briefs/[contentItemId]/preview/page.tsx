// Yuvo Studio — Phase 4C internal visual preview route.
//
// OPERATOR-ONLY. Reads the live content item from Supabase, parses
// the [creative brief] block (Phase 4A), runs the deterministic
// visual preview builder, and renders the format-specific React
// template. Pure HTML/CSS, no PNG export, no client share, no paid
// call. NEVER reachable from /client/*.

import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { getCurrentPersona } from "@/lib/auth/persona";
import { getDataSource, getDefaultWorkspaceId } from "@/lib/data/_source";
import {
  getContentItemById,
} from "@/lib/data/owner-overview";
import { getCampaignById } from "@/lib/data/campaigns";
import { getBrandById } from "@/lib/data/brands";
import {
  parseCreativeBriefBlock,
  parseCreativeBriefApproval,
} from "@/lib/creative/creative-brief-parser";
import { buildVisualPreview } from "@/lib/creative/build-visual-preview";
import { CreativePreviewShell } from "@/components/creative-preview/creative-preview-shell";
import { CarouselPreviewTemplate } from "@/components/creative-preview/carousel-preview-template";
import { StoryPreviewTemplate } from "@/components/creative-preview/story-preview-template";
import { FeedPostPreviewTemplate } from "@/components/creative-preview/feed-post-preview-template";
import { LinkedInPreviewTemplate } from "@/components/creative-preview/linkedin-preview-template";
import { ThumbnailPreviewTemplate } from "@/components/creative-preview/thumbnail-preview-template";
import { CreativeBriefApprovalPanel } from "@/components/agents/creative-brief-approval-panel";

export const dynamic = "force-dynamic";

interface PageProps {
  params: Promise<{ contentItemId: string }>;
  searchParams: Promise<{ slide?: string; frame?: string }>;
}

export default async function CreativePreviewPage({
  params,
  searchParams,
}: PageProps) {
  // Workspace resolution mirrors the rest of /agency/*. The persona
  // gate is enforced by the agency layout AND re-checked here.
  let workspaceId = getDefaultWorkspaceId();
  if (getDataSource() === "supabase") {
    const persona = await getCurrentPersona();
    if (!persona) {
      redirect("/login?next=/agency/creative-briefs");
    }
    if (persona.kind !== "operator") {
      redirect("/login?next=/agency/creative-briefs");
    }
    workspaceId = persona.workspaceIds[0] ?? getDefaultWorkspaceId();
  }
  void workspaceId; // used by the readers indirectly via RLS

  const { contentItemId } = await params;
  const content = await getContentItemById(contentItemId);
  if (!content) notFound();

  // Brand / campaign labels — same approach as /agency/jobs/[jobId].
  const campaign = await getCampaignById(content.campaignId);
  const brand = campaign ? await getBrandById(campaign.brandId) : null;

  const brief = parseCreativeBriefBlock(content.promptSummary);
  const approval = parseCreativeBriefApproval(content.promptSummary);

  const preview = buildVisualPreview({
    contentItemId: content.id,
    contentItemTitle: content.title,
    brandName: brand?.name ?? null,
    campaignName: campaign?.title ?? null,
    brandPrimaryColorHex: brand?.primaryColorHex ?? null,
    brandNiche: null,
    brief,
  });

  // Phase 4D3 — slide / frame focus from querystring.
  const sp = await searchParams;
  const focusedSlide = parsePositiveInt(sp.slide);
  const focusedFrame = parsePositiveInt(sp.frame);
  const baseHref = `/agency/creative-briefs/${content.id}/preview`;

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <Link
          href="/agency/creative-briefs"
          className="text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
        >
          ← Creative briefs
        </Link>
        <span className="text-[color:var(--color-ink-faint)]">/</span>
        <span className="font-semibold">{content.title}</span>
        <Badge tone="neutral">content status: {content.status}</Badge>
      </div>

      {!brief ? (
        <EmptyState contentItemId={content.id} />
      ) : (
        <>
          <CreativePreviewShell preview={preview}>
            <TemplateFor
              preview={preview}
              focusedSlide={focusedSlide}
              focusedFrame={focusedFrame}
              baseHref={baseHref}
            />
          </CreativePreviewShell>

          <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-2">
            <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
              Internal approval
            </div>
            <CreativeBriefApprovalPanel
              contentItemId={content.id}
              currentStatus={approval ? "approved_internal" : "none"}
              approvedAt={approval?.approvedAt ?? null}
            />
          </div>

          <div className="rounded-md border border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] p-3 text-xs text-[color:var(--color-ink-muted)] space-y-1">
            <div className="font-semibold text-[color:var(--color-ink)]">
              Next step (planned)
            </div>
            <div>
              <strong>Phase 4D</strong> will add a gated PNG/JPG export
              path (operator-run, never automatic). <strong>Phase 4E</strong>{" "}
              adds the client-safe visual preview lifecycle
              (<code className="font-mono">client_safe_visual_url</code>{" "}
              + prepare/share). Nothing is shared with the client yet —
              this page is purely an internal planning preview.
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function TemplateFor({
  preview,
  focusedSlide,
  focusedFrame,
  baseHref,
}: {
  preview: ReturnType<typeof buildVisualPreview>;
  focusedSlide: number | null;
  focusedFrame: number | null;
  baseHref: string;
}) {
  switch (preview.asset.mode) {
    case "carousel":
      return (
        <CarouselPreviewTemplate
          preview={preview}
          focusedSlide={focusedSlide}
          baseHref={baseHref}
        />
      );
    case "story":
      return (
        <StoryPreviewTemplate
          preview={preview}
          focusedFrame={focusedFrame}
          baseHref={baseHref}
        />
      );
    case "feed_post":
    case "static_image":
      return <FeedPostPreviewTemplate preview={preview} />;
    case "linkedin_image":
      return <LinkedInPreviewTemplate preview={preview} />;
    case "reel_thumbnail":
    case "video_thumbnail":
      return <ThumbnailPreviewTemplate preview={preview} />;
    case "unknown":
    default:
      return (
        <div className="rounded-md border border-dashed border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] p-6 text-sm text-[color:var(--color-ink-muted)]">
          This content item has a brief but no recognised visual mode
          (likely <code className="font-mono">copy_only</code> or a
          legacy format). Re-draft the creative brief from
          /agency/creative-briefs to populate visual fields.
        </div>
      );
  }
}

function parsePositiveInt(raw: string | undefined): number | null {
  if (!raw) return null;
  const n = parseInt(raw, 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function EmptyState({ contentItemId }: { contentItemId: string }) {
  void contentItemId;
  return (
    <div className="rounded-md border border-dashed border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] p-6 space-y-3">
      <div className="font-semibold">No creative brief yet</div>
      <div className="text-sm text-[color:var(--color-ink-muted)]">
        This content item does not have a{" "}
        <code className="font-mono">[creative brief]</code> block on
        its <code className="font-mono">prompt_summary</code>. Generate
        one first using the Social Creative Brief Agent.
      </div>
      <div>
        <Link
          href="/agency/creative-briefs"
          className="inline-block text-xs px-2.5 py-1.5 rounded-md border border-[color:var(--color-hairline)] hover:bg-[color:var(--color-hairline)]"
        >
          ← Back to creative briefs
        </Link>
      </div>
    </div>
  );
}
