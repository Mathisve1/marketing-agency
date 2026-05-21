// Yuvo Studio — Phase 4C carousel preview template.
//
// Server component. Renders the parsed [creative brief] slides as a
// responsive grid of 4:5 cards (Instagram carousel ratio). Pure HTML
// + CSS, no external image, no PNG export.

import * as React from "react";
import Link from "next/link";
import { PreviewCard, PreviewMetaRow } from "./creative-preview-shell";
import type { VisualPreviewRenderInput } from "@/lib/creative/visual-preview-types";

interface Props {
  preview: VisualPreviewRenderInput;
  /** Phase 4D3 — 1-indexed slide focus; when set, that slide renders
   *  large above the grid and the grid scrolls. */
  focusedSlide?: number | null;
  /** Base path used to build slide-navigation links (e.g.
   *  `/agency/creative-briefs/<id>/preview`). */
  baseHref?: string;
}

export function CarouselPreviewTemplate({
  preview,
  focusedSlide = null,
  baseHref,
}: Props) {
  const slides = preview.asset.slides ?? [];
  if (slides.length === 0) {
    return (
      <EmptyState text="No slides in this brief. Re-draft the creative brief to populate carousel slides." />
    );
  }
  const focused =
    focusedSlide && focusedSlide >= 1 && focusedSlide <= slides.length
      ? slides[focusedSlide - 1]
      : null;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs text-[color:var(--color-ink-muted)]">
          {slides.length} slide{slides.length === 1 ? "" : "s"} · 4:5
          (Instagram carousel)
          {focused && (
            <span className="ml-2 font-semibold text-[color:var(--color-ink)]">
              · viewing slide {focused.slideNumber} of {slides.length}
            </span>
          )}
        </div>
        {baseHref && slides.length > 1 && (
          <SlideNav
            count={slides.length}
            current={focused?.slideNumber ?? null}
            baseHref={baseHref}
            param="slide"
          />
        )}
      </div>

      {focused && (
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,360px)] max-w-3xl">
          <div className="max-w-md">
            <PreviewCard aspect="portrait" themeId={preview.theme.themeId}>
              <div className="text-[10px] uppercase tracking-[0.18em] text-white/60">
                Slide {focused.slideNumber} — focused
              </div>
              <div className="mt-2 text-2xl font-semibold leading-tight">
                {focused.headline}
              </div>
              {focused.bodyCopy && (
                <div className="mt-3 text-sm text-white/85 leading-snug">
                  {focused.bodyCopy}
                </div>
              )}
              <div className="mt-auto pt-3 border-t border-white/15 text-[11px] text-white/55">
                {preview.theme.brandName ?? "Brand"} · slide{" "}
                {focused.slideNumber}
              </div>
            </PreviewCard>
          </div>
          <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-1.5">
            <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
              Slide {focused.slideNumber} details
            </div>
            <PreviewMetaRow
              label="visual"
              value={focused.visualDirection ?? undefined}
            />
            <PreviewMetaRow
              label="layout"
              value={focused.layoutNote ?? undefined}
            />
            {focused.bodyCopy && (
              <PreviewMetaRow label="body" value={focused.bodyCopy} />
            )}
          </div>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {slides.map((s) => {
          const isFocused = focused?.slideNumber === s.slideNumber;
          const card = (
            <PreviewCard
              aspect="portrait"
              themeId={preview.theme.themeId}
              exportSlide={s.slideNumber}
              className={
                isFocused ? "ring-2 ring-[color:var(--color-accent)]" : ""
              }
            >
              <div className="text-[10px] uppercase tracking-[0.18em] text-white/60">
                Slide {s.slideNumber}
              </div>
              <div className="mt-2 text-lg font-semibold leading-tight">
                {s.headline}
              </div>
              {s.bodyCopy && (
                <div className="mt-3 text-xs text-white/85 leading-snug">
                  {s.bodyCopy}
                </div>
              )}
              <div className="mt-auto pt-3 border-t border-white/15 text-[10px] text-white/55">
                {preview.theme.brandName ?? "Brand"} · slide {s.slideNumber}
              </div>
            </PreviewCard>
          );
          return (
            <div key={s.slideNumber} className="space-y-1.5">
              {baseHref ? (
                <Link
                  href={`${baseHref}?slide=${s.slideNumber}`}
                  className="block focus:outline-none focus:ring-2 focus:ring-[color:var(--color-accent)] rounded-lg"
                  aria-current={isFocused ? "true" : undefined}
                >
                  {card}
                </Link>
              ) : (
                card
              )}
              <div className="rounded-md border border-[color:var(--color-hairline)] bg-white px-2.5 py-1.5 space-y-0.5">
                <PreviewMetaRow label="visual" value={s.visualDirection ?? undefined} />
                <PreviewMetaRow label="layout" value={s.layoutNote ?? undefined} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SlideNav({
  count,
  current,
  baseHref,
  param,
}: {
  count: number;
  current: number | null;
  baseHref: string;
  param: string;
}) {
  const prev = current && current > 1 ? current - 1 : null;
  const next = current && current < count ? current + 1 : null;
  return (
    <div className="flex items-center gap-1">
      <Link
        href={prev ? `${baseHref}?${param}=${prev}` : baseHref}
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
        href={next ? `${baseHref}?${param}=${next}` : baseHref}
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
