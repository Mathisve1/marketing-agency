// Yuvo Studio — Phase 4C reel / video thumbnail preview template.
//
// Server component. Renders a 9:16 cover-frame preview for an organic
// reel or paid UGC video, plus the on-screen text beats / b-roll cues
// or the hook frame / prop brief. The agent does NOT produce video;
// this card only previews the COVER + the visual direction.

import * as React from "react";
import { PreviewCard, PreviewMetaRow } from "./creative-preview-shell";
import type { VisualPreviewRenderInput } from "@/lib/creative/visual-preview-types";

export function ThumbnailPreviewTemplate({
  preview,
}: {
  preview: VisualPreviewRenderInput;
}) {
  const a = preview.asset;
  const isReel = a.mode === "reel_thumbnail";
  return (
    <div className="space-y-2">
      <div className="text-xs text-[color:var(--color-ink-muted)]">
        1 × 9:16 ({isReel ? "reel" : "video"} cover frame, planning only)
      </div>
      <div className="grid gap-3 sm:grid-cols-[minmax(0,260px)_1fr] max-w-3xl items-start">
        <PreviewCard aspect="story">
          <div className="h-1.5 w-12 mx-auto bg-white/30 rounded-full" />
          <div className="mt-2 text-[10px] uppercase tracking-[0.18em] text-white/60 text-center">
            Cover frame
          </div>
          <div className="mt-auto mb-auto text-lg font-semibold leading-tight text-center px-2">
            {a.title}
          </div>
          <div className="border-t border-white/15 pt-2 text-[10px] text-white/55 text-center">
            {preview.theme.brandName ?? "Brand"} · {a.channel ?? (isReel ? "instagram" : "video")}
          </div>
        </PreviewCard>
        <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-2">
          <PreviewMetaRow label="thumbnail concept" value={a.thumbnailConcept ?? undefined} />
          {isReel ? (
            <>
              {a.onScreenTextBeats && a.onScreenTextBeats.length > 0 && (
                <div>
                  <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                    on-screen text beats
                  </div>
                  <ul className="text-[11px] text-[color:var(--color-ink-muted)] list-disc pl-4 space-y-0.5">
                    {a.onScreenTextBeats.map((b, i) => (
                      <li key={i}>{b}</li>
                    ))}
                  </ul>
                </div>
              )}
              {a.brollCues && a.brollCues.length > 0 && (
                <div>
                  <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                    b-roll cues
                  </div>
                  <ul className="text-[11px] text-[color:var(--color-ink-muted)] list-disc pl-4 space-y-0.5">
                    {a.brollCues.map((c, i) => (
                      <li key={i}>{c}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          ) : (
            <>
              <PreviewMetaRow label="hook frame" value={a.hookFrame ?? undefined} />
              <PreviewMetaRow label="prop brief" value={a.propBrief ?? undefined} />
            </>
          )}
          <PreviewMetaRow label="CTA" value={a.callToAction ?? undefined} />
        </div>
      </div>
    </div>
  );
}
