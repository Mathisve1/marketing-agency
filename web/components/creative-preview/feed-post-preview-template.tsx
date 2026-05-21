// Yuvo Studio — Phase 4C feed / static post preview template.
//
// Server component. Renders a single 4:5 preview card for an
// Instagram or Facebook feed post / static image. Pure HTML/CSS.

import * as React from "react";
import { PreviewCard, PreviewMetaRow } from "./creative-preview-shell";
import type { VisualPreviewRenderInput } from "@/lib/creative/visual-preview-types";

export function FeedPostPreviewTemplate({
  preview,
}: {
  preview: VisualPreviewRenderInput;
}) {
  const a = preview.asset;
  return (
    <div className="space-y-2">
      <div className="text-xs text-[color:var(--color-ink-muted)]">
        1 × 4:5 ({a.channel ?? "feed"} post)
      </div>
      <div className="grid gap-3 max-w-md">
        <PreviewCard
          aspect="portrait"
          themeId={preview.theme.themeId}
          exportSlide={1}
        >
          <div className="text-[10px] uppercase tracking-[0.18em] text-white/60">
            {a.channel ?? "feed"} · {a.format ?? "feed_post"}
          </div>
          <div className="mt-2 text-2xl font-semibold leading-tight">
            {a.headlineOverlay ?? a.title}
          </div>
          {a.mainVisual && (
            <div className="mt-4 text-xs text-white/80 leading-snug">
              <span className="text-white/55 uppercase tracking-[0.18em] text-[10px]">
                visual
              </span>{" "}
              {a.mainVisual}
            </div>
          )}
          <div className="mt-auto pt-3 border-t border-white/15 text-[10px] text-white/55">
            {preview.theme.brandName ?? "Brand"}
            {a.callToAction ? ` · CTA: ${a.callToAction}` : ""}
          </div>
        </PreviewCard>
        <div className="rounded-md border border-[color:var(--color-hairline)] bg-white px-2.5 py-1.5 space-y-0.5">
          <PreviewMetaRow label="composition" value={a.compositionNotes ?? undefined} />
          <PreviewMetaRow label="caption support" value={a.captionSupport ?? undefined} />
        </div>
      </div>
    </div>
  );
}
