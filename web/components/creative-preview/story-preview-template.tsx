// Yuvo Studio — Phase 4C story preview template.
//
// Server component. Renders the parsed story frames as 9:16 cards in
// a grid. Pure HTML/CSS, no external image, no PNG export.

import * as React from "react";
import Link from "next/link";
import { PreviewCard, PreviewMetaRow } from "./creative-preview-shell";
import type { VisualPreviewRenderInput } from "@/lib/creative/visual-preview-types";

interface Props {
  preview: VisualPreviewRenderInput;
  focusedFrame?: number | null;
  baseHref?: string;
}

export function StoryPreviewTemplate({
  preview,
  focusedFrame = null,
  baseHref,
}: Props) {
  const frames = preview.asset.frames ?? [];
  if (frames.length === 0) {
    return (
      <EmptyState text="No story frames in this brief. Re-draft the creative brief to populate frames." />
    );
  }
  const focused =
    focusedFrame && focusedFrame >= 1 && focusedFrame <= frames.length
      ? frames[focusedFrame - 1]
      : null;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs text-[color:var(--color-ink-muted)]">
          {frames.length} frame{frames.length === 1 ? "" : "s"} · 9:16
          (vertical story)
          {focused && (
            <span className="ml-2 font-semibold text-[color:var(--color-ink)]">
              · viewing frame {focused.frameNumber} of {frames.length}
            </span>
          )}
        </div>
        {baseHref && frames.length > 1 && (
          <FrameNav
            count={frames.length}
            current={focused?.frameNumber ?? null}
            baseHref={baseHref}
          />
        )}
      </div>

      {focused && (
        <div className="grid gap-3 sm:grid-cols-[minmax(0,260px)_minmax(0,1fr)] max-w-3xl">
          <PreviewCard aspect="story">
            <div className="h-1.5 w-12 mx-auto bg-white/30 rounded-full" />
            <div className="mt-2 text-[10px] uppercase tracking-[0.18em] text-white/60 text-center">
              Frame {focused.frameNumber} — focused
            </div>
            <div className="mt-auto mb-auto text-lg font-semibold leading-tight text-center px-2">
              {focused.textOverlay ?? "(overlay text)"}
            </div>
            <div className="border-t border-white/15 pt-2 text-[10px] text-white/55 text-center">
              {preview.theme.brandName ?? "Brand"} ·{" "}
              {preview.asset.channel ?? "story"}
            </div>
          </PreviewCard>
          <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-1.5">
            <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
              Frame {focused.frameNumber} details
            </div>
            <PreviewMetaRow
              label="visual"
              value={focused.visualDirection ?? undefined}
            />
            <PreviewMetaRow
              label="sticker"
              value={focused.interactionStickerSuggestion ?? undefined}
            />
          </div>
        </div>
      )}

      <div className="grid gap-3 grid-cols-2 sm:grid-cols-3 md:grid-cols-4 max-w-3xl">
        {frames.map((f) => {
          const isFocused = focused?.frameNumber === f.frameNumber;
          const card = (
            <PreviewCard
              aspect="story"
              className={
                isFocused ? "ring-2 ring-[color:var(--color-accent)]" : ""
              }
            >
              <div className="h-1.5 w-12 mx-auto bg-white/30 rounded-full" />
              <div className="mt-2 text-[10px] uppercase tracking-[0.18em] text-white/60 text-center">
                Frame {f.frameNumber}
              </div>
              <div className="mt-auto mb-auto text-base font-semibold leading-tight text-center px-2">
                {f.textOverlay ?? "(overlay text)"}
              </div>
              <div className="border-t border-white/15 pt-2 text-[10px] text-white/55 text-center">
                {preview.theme.brandName ?? "Brand"} ·{" "}
                {preview.asset.channel ?? "story"}
              </div>
            </PreviewCard>
          );
          return (
            <div key={f.frameNumber} className="space-y-1.5">
              {baseHref ? (
                <Link
                  href={`${baseHref}?frame=${f.frameNumber}`}
                  className="block focus:outline-none focus:ring-2 focus:ring-[color:var(--color-accent)] rounded-lg"
                  aria-current={isFocused ? "true" : undefined}
                >
                  {card}
                </Link>
              ) : (
                card
              )}
              <div className="rounded-md border border-[color:var(--color-hairline)] bg-white px-2.5 py-1.5 space-y-0.5">
                <PreviewMetaRow label="visual" value={f.visualDirection ?? undefined} />
                <PreviewMetaRow label="sticker" value={f.interactionStickerSuggestion ?? undefined} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FrameNav({
  count,
  current,
  baseHref,
}: {
  count: number;
  current: number | null;
  baseHref: string;
}) {
  const prev = current && current > 1 ? current - 1 : null;
  const next = current && current < count ? current + 1 : null;
  return (
    <div className="flex items-center gap-1">
      <Link
        href={prev ? `${baseHref}?frame=${prev}` : baseHref}
        className="text-xs px-2 py-1 rounded-md border border-[color:var(--color-hairline)] hover:bg-[color:var(--color-hairline)] aria-disabled:opacity-40"
        aria-disabled={!prev}
      >
        ← prev
      </Link>
      <Link
        href={baseHref}
        className="text-xs px-2 py-1 rounded-md border border-[color:var(--color-hairline)] hover:bg-[color:var(--color-hairline)]"
      >
        all
      </Link>
      <Link
        href={next ? `${baseHref}?frame=${next}` : baseHref}
        className="text-xs px-2 py-1 rounded-md border border-[color:var(--color-hairline)] hover:bg-[color:var(--color-hairline)] aria-disabled:opacity-40"
        aria-disabled={!next}
      >
        next →
      </Link>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-md border border-dashed border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] p-6 text-sm text-[color:var(--color-ink-muted)]">
      {text}
    </div>
  );
}
