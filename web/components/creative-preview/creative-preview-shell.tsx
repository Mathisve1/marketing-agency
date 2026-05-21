// Yuvo Studio — Phase 4C/4E preview shell.
//
// Server component. Wraps a per-mode template with consistent chrome:
// header (title / subtitle / mode + format + channel + template +
// theme chips), warnings footer, and the unambiguous "internal
// preview only" banner. NEVER imported by /client/*.
//
// The theme is applied at the `PreviewCard` surface via an explicit
// `themeId` prop. Server components can't use React.createContext, so
// each template threads `preview.theme.themeId` into its PreviewCards
// directly.

import * as React from "react";
import { Badge } from "@/components/ui/badge";
import type { VisualPreviewRenderInput } from "@/lib/creative/visual-preview-types";
import { getThemePreset } from "@/lib/creative/themes";

const MODE_LABEL: Record<string, string> = {
  carousel: "Carousel",
  story: "Story",
  feed_post: "Feed post",
  static_image: "Static image",
  linkedin_image: "LinkedIn image concept",
  reel_thumbnail: "Reel thumbnail / cover",
  video_thumbnail: "Video thumbnail / cover",
  unknown: "Unknown",
};

export function CreativePreviewShell({
  preview,
  children,
}: {
  preview: VisualPreviewRenderInput;
  children: React.ReactNode;
}) {
  const { asset, theme, warnings } = preview;
  return (
    <div className="space-y-5">
      <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="neutral">{MODE_LABEL[asset.mode] ?? asset.mode}</Badge>
          {asset.format && <Badge tone="neutral">format: {asset.format}</Badge>}
          {asset.channel && <Badge tone="neutral">channel: {asset.channel}</Badge>}
          {asset.templateId && (
            <Badge tone="neutral">template: {asset.templateId}</Badge>
          )}
          <Badge tone="neutral">theme: {theme.themeId}</Badge>
          {theme.brandName && <Badge tone="neutral">brand: {theme.brandName}</Badge>}
          {theme.niche && <Badge tone="neutral">niche: {theme.niche}</Badge>}
          <Badge tone="warn">internal preview only</Badge>
        </div>
        <h2 className="mt-2 text-xl font-semibold leading-tight">
          {asset.title}
        </h2>
        {asset.subtitle && (
          <div className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
            {asset.subtitle}
          </div>
        )}
        {asset.callToAction && (
          <div className="mt-1 text-xs text-[color:var(--color-ink-muted)] italic">
            CTA: {asset.callToAction}
          </div>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {/*
           * Phase 4D4 — disabled export placeholder. Pure UI; clicks
           * nothing. The real export pipe arrives in Phase 4D
           * (recommended: local operator Puppeteer script) and is
           * gated like Seedance.
           */}
          <button
            type="button"
            disabled
            title="Coming in Phase 4D — operator-gated PNG/JPG export. Will mirror the Seedance gate (confirmation phrase + cost estimate)."
            aria-disabled="true"
            className="text-xs px-2.5 py-1.5 rounded-md border border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] text-[color:var(--color-ink-muted)] cursor-not-allowed opacity-70"
          >
            Export PNG (Phase 4D — coming soon)
          </button>
          <span className="text-[11px] text-[color:var(--color-ink-faint)] italic">
            No render runs from this page. Phase 4D adds an
            operator-gated export; Phase 4E adds the client-safe
            visual preview lifecycle.
          </span>
        </div>
      </div>

      {/* Phase 5A — stable export-root wrapper for the future local
          screenshot pipe. Carries `data-export-root` and
          `data-export-mode` so a Playwright/Puppeteer locator can
          scope into a single preview surface. Pure DOM marker; no
          visual change. */}
      <div data-export-root data-export-mode={asset.mode}>{children}</div>

      {warnings.length > 0 && (
        <div className="rounded-md border border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] p-3 text-[11px] text-[color:var(--color-ink-muted)] space-y-0.5">
          {warnings.map((w, i) => (
            <div key={i}>· {w}</div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Reusable themed card surface shared across templates. The theme
 *  is passed explicitly so this can stay a server component (no
 *  `React.createContext`, which is client-only).
 *
 *  Phase 5A — optional `exportSlide` / `exportFrame` props become
 *  `data-export-slide` / `data-export-frame` on the rendered
 *  surface. The future local exporter uses these as locators (see
 *  `_export_target_selectors` in
 *  `scripts/export_visual_preview_stub.py`). No visual change. */
export function PreviewCard({
  aspect = "square",
  themeId = "neutral",
  exportSlide,
  exportFrame,
  children,
  className = "",
}: {
  aspect?: "square" | "portrait" | "story";
  themeId?: string;
  exportSlide?: number;
  exportFrame?: number;
  children: React.ReactNode;
  className?: string;
}) {
  const preset = getThemePreset(themeId);
  const aspectClass =
    aspect === "story"
      ? "aspect-[9/16]"
      : aspect === "portrait"
        ? "aspect-[4/5]"
        : "aspect-square";
  const exportAttrs: Record<string, string> = {};
  if (typeof exportSlide === "number" && exportSlide > 0) {
    exportAttrs["data-export-slide"] = String(exportSlide);
  }
  if (typeof exportFrame === "number" && exportFrame > 0) {
    exportAttrs["data-export-frame"] = String(exportFrame);
  }
  return (
    <div
      className={`relative w-full ${aspectClass} rounded-lg overflow-hidden shadow-sm ${preset.surfaceClass} ${className}`}
      {...exportAttrs}
    >
      <div className={`absolute inset-0 opacity-60 ${preset.highlightClass}`} />
      <div className="relative h-full w-full p-4 flex flex-col">{children}</div>
    </div>
  );
}

export function PreviewMetaRow({
  label,
  value,
}: {
  label: string;
  value: string | null | undefined;
}) {
  if (!value) return null;
  return (
    <div className="text-[11px] text-[color:var(--color-ink-muted)] leading-snug">
      <span className="uppercase tracking-[0.18em] text-[10px] text-[color:var(--color-ink-faint)]">
        {label}
      </span>{" "}
      {value}
    </div>
  );
}
