// Yuvo Studio — Phase 4C LinkedIn image concept preview template.
//
// Server component. Renders a 1:1 LinkedIn-companion image concept
// card plus the post hook + tone notes. Pure HTML/CSS, no PNG.

import * as React from "react";
import { PreviewCard, PreviewMetaRow } from "./creative-preview-shell";
import type { VisualPreviewRenderInput } from "@/lib/creative/visual-preview-types";

export function LinkedInPreviewTemplate({
  preview,
}: {
  preview: VisualPreviewRenderInput;
}) {
  const a = preview.asset;
  return (
    <div className="space-y-2">
      <div className="text-xs text-[color:var(--color-ink-muted)]">
        1 × 1:1 (LinkedIn companion image, optional)
      </div>
      <div className="grid gap-3 sm:grid-cols-[1fr_minmax(0,260px)] max-w-3xl items-start">
        <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-4 space-y-3">
          <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
            LinkedIn post hook
          </div>
          <div className="text-base font-semibold leading-snug">
            {a.postHook ?? a.title}
          </div>
          <div className="space-y-1 pt-1 border-t border-[color:var(--color-hairline)]">
            <PreviewMetaRow label="image concept" value={a.imageConcept ?? undefined} />
            <PreviewMetaRow label="tone notes" value={a.professionalToneNotes ?? undefined} />
            <PreviewMetaRow label="CTA" value={a.callToAction ?? undefined} />
          </div>
        </div>
        <PreviewCard aspect="square">
          <div className="text-[10px] uppercase tracking-[0.18em] text-white/60">
            Optional 1:1 companion
          </div>
          <div className="mt-2 text-base font-semibold leading-tight">
            {a.title}
          </div>
          {a.imageConcept && (
            <div className="mt-3 text-xs text-white/85 leading-snug">
              {a.imageConcept}
            </div>
          )}
          <div className="mt-auto pt-2 border-t border-white/15 text-[10px] text-white/55">
            {preview.theme.brandName ?? "Brand"} · LinkedIn
          </div>
        </PreviewCard>
      </div>
    </div>
  );
}
