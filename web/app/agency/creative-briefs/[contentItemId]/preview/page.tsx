// Yuvo Studio — Phase 4C/4E/4F internal visual preview route.
//
// OPERATOR-ONLY. Reads the live content item from Supabase, parses
// the [creative brief] + [creative brief approval] + [creative
// preview QA] blocks, runs the deterministic visual preview builder,
// and renders the format-specific React template. Pure HTML/CSS. NO
// PNG export. NO client share. NO paid call. NEVER reachable from
// /client/*.

import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { getCurrentPersona } from "@/lib/auth/persona";
import { getDataSource, getDefaultWorkspaceId } from "@/lib/data/_source";
import { getContentItemById } from "@/lib/data/owner-overview";
import { getCampaignById } from "@/lib/data/campaigns";
import { getBrandById } from "@/lib/data/brands";
import {
  parseCreativeBriefBlock,
  parseCreativeBriefApproval,
  parseCreativePreviewQA,
} from "@/lib/creative/creative-brief-parser";
import {
  buildVisualPreview,
  previewModeFromBriefMode,
} from "@/lib/creative/build-visual-preview";
import { isValidTemplateForMode, resolveTemplateId } from "@/lib/creative/templates";
import { isValidThemeId } from "@/lib/creative/themes";
import {
  buildExportManifest,
  renderExportManifestText,
} from "@/lib/creative/export-manifest";
import { QA_ITEMS } from "@/lib/creative/qa-items";
import { CreativePreviewShell } from "@/components/creative-preview/creative-preview-shell";
import { CarouselPreviewTemplate } from "@/components/creative-preview/carousel-preview-template";
import { StoryPreviewTemplate } from "@/components/creative-preview/story-preview-template";
import { FeedPostPreviewTemplate } from "@/components/creative-preview/feed-post-preview-template";
import { LinkedInPreviewTemplate } from "@/components/creative-preview/linkedin-preview-template";
import { ThumbnailPreviewTemplate } from "@/components/creative-preview/thumbnail-preview-template";
import { CreativeBriefApprovalPanel } from "@/components/agents/creative-brief-approval-panel";
import { CreativePreviewQAPanel } from "@/components/agents/creative-preview-qa-panel";
import { CopyExportBriefButton } from "@/components/creative-preview/copy-export-brief-button";
import { CopyExportCommandButton } from "@/components/creative-preview/copy-export-command-button";
import {
  ClientVisualPreviewPanel,
  ExportManifestPanel,
  ExportReadinessPanel,
  StorageStatusPanel,
  TemplateOptionsPanel,
  ThemeOptionsPanel,
  WhatHappensNextPanel,
} from "@/components/creative-preview/preview-side-panels";
import { checkVisualPreviewSchemaReadiness } from "@/lib/data/visual-preview-schema";

export const dynamic = "force-dynamic";

interface PageProps {
  params: Promise<{ contentItemId: string }>;
  searchParams: Promise<{
    slide?: string;
    frame?: string;
    template?: string;
    theme?: string;
  }>;
}

export default async function CreativePreviewPage({
  params,
  searchParams,
}: PageProps) {
  let workspaceId = getDefaultWorkspaceId();
  if (getDataSource() === "supabase") {
    const persona = await getCurrentPersona();
    if (!persona) redirect("/login?next=/agency/creative-briefs");
    if (persona.kind !== "operator") {
      redirect("/login?next=/agency/creative-briefs");
    }
    workspaceId = persona.workspaceIds[0] ?? getDefaultWorkspaceId();
  }
  void workspaceId;

  const { contentItemId } = await params;
  const content = await getContentItemById(contentItemId);
  if (!content) notFound();

  const campaign = await getCampaignById(content.campaignId);
  const brand = campaign ? await getBrandById(campaign.brandId) : null;

  const brief = parseCreativeBriefBlock(content.promptSummary);
  const approval = parseCreativeBriefApproval(content.promptSummary);
  const qa = parseCreativePreviewQA(content.promptSummary);

  // Validate ?template= / ?theme= overrides; never throw on bad input.
  const sp = await searchParams;
  const inferredMode = brief ? previewModeFromBriefMode(brief.mode) : "unknown";
  const queryTemplateRaw = sp.template ?? null;
  const queryThemeRaw = sp.theme ?? null;
  const queryTemplateValid =
    queryTemplateRaw && isValidTemplateForMode(inferredMode, queryTemplateRaw)
      ? queryTemplateRaw
      : null;
  const queryTemplateInvalid = Boolean(queryTemplateRaw && !queryTemplateValid);
  const queryThemeValid = isValidThemeId(queryThemeRaw) ? queryThemeRaw : null;
  const queryThemeInvalid = Boolean(queryThemeRaw && !queryThemeValid);

  const preview = buildVisualPreview({
    contentItemId: content.id,
    contentItemTitle: content.title,
    brandName: brand?.name ?? null,
    campaignName: campaign?.title ?? null,
    brandPrimaryColorHex: brand?.primaryColorHex ?? null,
    brandNiche: null,
    brief,
    queryTemplateId: queryTemplateValid,
    queryThemeId: queryThemeValid,
  });

  const focusedSlide = parsePositiveInt(sp.slide);
  const focusedFrame = parsePositiveInt(sp.frame);
  const baseHref = `/agency/creative-briefs/${content.id}/preview`;
  const currentTemplateId =
    preview.asset.templateId ?? resolveTemplateId(preview.asset.mode, null);

  const manifest = brief
    ? buildExportManifest({ preview, approvedInternal: Boolean(approval) })
    : null;

  // Phase 5C — fail-soft schema readiness probe for the client-share
  // panel. The detector NEVER throws; it returns "not_configured"
  // on any missing-relation error, so the panel always renders.
  const visualSchema = await checkVisualPreviewSchemaReadiness();
  const hasInternalApproval = Boolean(approval);
  const hasExportManifestReady =
    manifest != null && manifest.exportReadiness === "ready";
  // Phase 5C has no upload pipe yet; this stays false by definition.
  const hasUploadedAsset = false;
  const exportBriefText = manifest
    ? renderExportManifestText(manifest, { previewUrl: baseHref })
    : "";

  const exportNotReadyReason =
    manifest && manifest.exportReadiness === "ready"
      ? null
      : "Resolve manifest blockers before exporting.";

  // Phase 4G — stable preview-URL for the local export command. The
  // dashboard is hosted on Cloudflare Workers; server components
  // cannot always derive the request origin without an explicit
  // X-Forwarded-Host. We therefore emit a relative dashboard path
  // and document on the command that the operator must prepend their
  // dashboard origin when pasting it into a terminal. The stub
  // validates BOTH relative and absolute URLs in dry-run mode.
  const previewQuery = new URLSearchParams();
  if (currentTemplateId) previewQuery.set("template", currentTemplateId);
  if (preview.theme.themeId) previewQuery.set("theme", preview.theme.themeId);
  const previewQs = previewQuery.toString();
  const previewUrlForCommand = previewQs
    ? `${baseHref}?${previewQs}`
    : baseHref;

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

      {(queryTemplateInvalid || queryThemeInvalid) && (
        <div className="rounded-md border border-[color:var(--color-warn)]/40 bg-[color:var(--color-warn)]/8 p-3 text-xs space-y-1">
          {queryTemplateInvalid && (
            <div>
              ⚠️ Unknown template id{" "}
              <code className="font-mono">{queryTemplateRaw}</code> for mode{" "}
              <code className="font-mono">{inferredMode}</code> — falling
              back to the default template. See the template options panel.
            </div>
          )}
          {queryThemeInvalid && (
            <div>
              ⚠️ Unknown theme id{" "}
              <code className="font-mono">{queryThemeRaw}</code> — falling
              back to the template default or{" "}
              <code className="font-mono">neutral</code>.
            </div>
          )}
        </div>
      )}

      {!brief ? (
        <EmptyState />
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,320px)]">
          <div className="space-y-5 min-w-0">
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
                exportReadiness={manifest?.exportReadiness ?? null}
                blockers={manifest?.blockers ?? []}
              />
            </div>

            {manifest && (
              <div className="space-y-2">
                <ExportReadinessPanel manifest={manifest} />
                <ExportManifestPanel manifest={manifest} />
                <CopyExportBriefButton
                  briefText={exportBriefText}
                  disabledReason={exportNotReadyReason}
                />
                <CopyExportCommandButton
                  contentItemId={content.id}
                  previewUrl={previewUrlForCommand}
                  templateId={currentTemplateId}
                  themeId={preview.theme.themeId}
                  mode={preview.asset.mode}
                  width={manifest.recommendedWidth}
                  height={manifest.recommendedHeight}
                  disabledReason={exportNotReadyReason}
                />
              </div>
            )}
            {!manifest && (
              <ExportReadinessPanel manifest={null} />
            )}

            <WhatHappensNextPanel />
          </div>

          <div className="space-y-4 min-w-0">
            <CreativePreviewQAPanel
              contentItemId={content.id}
              items={QA_ITEMS}
              initialDecisions={qa?.items ?? {}}
              initialStatus={qa?.status ?? "none"}
              initialCheckedAt={qa?.checkedAt ?? null}
            />
            <TemplateOptionsPanel
              preview={preview}
              baseHref={baseHref}
              currentTemplateId={currentTemplateId}
            />
            <ThemeOptionsPanel
              baseHref={baseHref}
              preserveTemplateId={currentTemplateId}
              currentThemeId={preview.theme.themeId}
            />
            <StorageStatusPanel />
            <ClientVisualPreviewPanel
              schema={visualSchema}
              hasInternalApproval={hasInternalApproval}
              hasExportManifestReady={hasExportManifestReady}
              hasUploadedAsset={hasUploadedAsset}
            />
          </div>
        </div>
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

function EmptyState() {
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
