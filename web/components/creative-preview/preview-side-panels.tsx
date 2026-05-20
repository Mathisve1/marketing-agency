// Yuvo Studio — Phase 4E side panels for the preview page.
//
// Server components only. All render deterministically from
// preview/manifest/template input — no client state, no DB calls,
// no fetch. Read-only QA checklist; template options panel; export
// manifest panel; "what happens next" panel.

import * as React from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import type { VisualPreviewRenderInput } from "@/lib/creative/visual-preview-types";
import type { ExportManifest } from "@/lib/creative/export-manifest";
import {
  CREATIVE_BRIEF_TEMPLATES,
  type CreativeBriefTemplate,
} from "@/lib/creative/templates";
import { VISUAL_THEMES } from "@/lib/creative/themes";

// ---------------------------------------------------------------------------
// QA checklist — Phase 4F: persistence is now handled by the
// `CreativePreviewQAPanel` client component (which mounts on the
// preview page directly). The Phase 4E read-only `PreviewQAChecklist`
// previously lived here as a planning placeholder; it has been
// retired so the preview page surfaces only one QA panel and there
// is no duplicate rendering. `QA_ITEMS` (the shared item set) still
// lives in `web/lib/creative/qa-items.ts` for the persisted panel.
// ---------------------------------------------------------------------------
// Template options panel — lists every template for the mode, links to
// `?template=…` for non-current ids.

export function TemplateOptionsPanel({
  preview,
  baseHref,
  currentTemplateId,
}: {
  preview: VisualPreviewRenderInput;
  baseHref: string;
  currentTemplateId: string | null;
}) {
  const list: CreativeBriefTemplate[] =
    CREATIVE_BRIEF_TEMPLATES[preview.asset.mode] ?? [];
  if (list.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-[color:var(--color-hairline)] p-3 text-xs text-[color:var(--color-ink-muted)]">
        No templates registered for mode <code className="font-mono">{preview.asset.mode}</code>.
      </div>
    );
  }
  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-2">
      <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
        Template options · {preview.asset.mode}
      </div>
      <ul className="space-y-1.5">
        {list.map((t) => {
          const isCurrent = t.id === currentTemplateId;
          const href = `${baseHref}?template=${t.id}`;
          return (
            <li
              key={t.id}
              className={
                isCurrent
                  ? "rounded-md border border-[color:var(--color-accent)]/40 bg-[color:var(--color-accent)]/8 px-2 py-1.5"
                  : "rounded-md border border-[color:var(--color-hairline)] px-2 py-1.5 hover:bg-[color:var(--color-cream-soft)]"
              }
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-xs font-semibold">{t.label}</span>
                    {t.status === "active" ? (
                      <Badge tone="success">active</Badge>
                    ) : (
                      <Badge tone="neutral">planned</Badge>
                    )}
                    {isCurrent && <Badge tone="info">current</Badge>}
                  </div>
                  <div className="text-[11px] text-[color:var(--color-ink-muted)] leading-snug">
                    {t.description}
                  </div>
                  <div className="text-[10px] text-[color:var(--color-ink-faint)] mt-0.5">
                    best for: {t.bestFor} · {t.aspectRatio} ·{" "}
                    {t.exportSize.width}×{t.exportSize.height}
                  </div>
                </div>
                {!isCurrent && (
                  <Link
                    href={href}
                    className="text-xs px-2 py-1 rounded-md border border-[color:var(--color-hairline)] hover:bg-[color:var(--color-hairline)]"
                  >
                    try →
                  </Link>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <div className="text-[10px] text-[color:var(--color-ink-faint)] italic">
        Choice is preview-only (querystring). Nothing is persisted to
        Supabase from this panel.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Theme options panel — same shape as the template panel.

export function ThemeOptionsPanel({
  baseHref,
  preserveTemplateId,
  currentThemeId,
}: {
  baseHref: string;
  preserveTemplateId: string | null;
  currentThemeId: string;
}) {
  function hrefFor(themeId: string): string {
    const params = new URLSearchParams();
    if (preserveTemplateId) params.set("template", preserveTemplateId);
    params.set("theme", themeId);
    return `${baseHref}?${params.toString()}`;
  }
  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-2">
      <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
        Theme presets
      </div>
      <ul className="space-y-1.5">
        {VISUAL_THEMES.map((th) => {
          const isCurrent = th.id === currentThemeId;
          return (
            <li
              key={th.id}
              className={
                isCurrent
                  ? "rounded-md border border-[color:var(--color-accent)]/40 bg-[color:var(--color-accent)]/8 px-2 py-1.5"
                  : "rounded-md border border-[color:var(--color-hairline)] px-2 py-1.5 hover:bg-[color:var(--color-cream-soft)]"
              }
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-xs font-semibold">{th.name}</span>
                    <Badge tone="neutral">{th.id}</Badge>
                    {isCurrent && <Badge tone="info">current</Badge>}
                  </div>
                  <div className="text-[11px] text-[color:var(--color-ink-muted)] leading-snug">
                    {th.description}
                  </div>
                  <div className="text-[10px] text-[color:var(--color-ink-faint)] mt-0.5">
                    best for: {th.bestFor}
                  </div>
                </div>
                {!isCurrent && (
                  <Link
                    href={hrefFor(th.id)}
                    className="text-xs px-2 py-1 rounded-md border border-[color:var(--color-hairline)] hover:bg-[color:var(--color-hairline)]"
                  >
                    try →
                  </Link>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <div className="text-[10px] text-[color:var(--color-ink-faint)] italic">
        Theme is preview-only. Not persisted.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Export manifest panel — read-only display of the manifest helper output.

export function ExportManifestPanel({ manifest }: { manifest: ExportManifest }) {
  const readinessTone: "success" | "warn" =
    manifest.exportReadiness === "ready" ? "success" : "warn";
  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Export manifest (planning only)
        </div>
        <Badge tone={readinessTone}>{manifest.exportReadiness}</Badge>
      </div>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-[11px]">
        <dt className="text-[color:var(--color-ink-faint)]">mode</dt>
        <dd className="font-mono">{manifest.mode}</dd>
        <dt className="text-[color:var(--color-ink-faint)]">template</dt>
        <dd className="font-mono">
          {manifest.templateId ?? "—"}{" "}
          {manifest.templateStatus === "planned" && (
            <Badge tone="neutral">planned</Badge>
          )}
        </dd>
        <dt className="text-[color:var(--color-ink-faint)]">theme</dt>
        <dd className="font-mono">{manifest.themeId}</dd>
        <dt className="text-[color:var(--color-ink-faint)]">size</dt>
        <dd className="font-mono">
          {manifest.recommendedWidth ?? "?"}×{manifest.recommendedHeight ?? "?"} ·{" "}
          {manifest.aspectRatio ?? "?"}
        </dd>
        {manifest.slideCount !== null && (
          <>
            <dt className="text-[color:var(--color-ink-faint)]">slides</dt>
            <dd className="font-mono">{manifest.slideCount}</dd>
          </>
        )}
        {manifest.frameCount !== null && (
          <>
            <dt className="text-[color:var(--color-ink-faint)]">frames</dt>
            <dd className="font-mono">{manifest.frameCount}</dd>
          </>
        )}
        <dt className="text-[color:var(--color-ink-faint)]">format</dt>
        <dd className="font-mono">{manifest.exportFormat}</dd>
      </dl>
      {manifest.assetNamingSuggestion.length > 0 && (
        <details className="text-[11px]">
          <summary className="cursor-pointer text-[color:var(--color-ink-muted)]">
            Suggested filenames ({manifest.assetNamingSuggestion.length})
          </summary>
          <ul className="mt-1 space-y-0.5 font-mono text-[color:var(--color-ink-muted)]">
            {manifest.assetNamingSuggestion.map((f) => (
              <li key={f}>· {f}</li>
            ))}
          </ul>
        </details>
      )}
      {manifest.blockers.length > 0 && (
        <div className="rounded-md border border-[color:var(--color-warn)]/40 bg-[color:var(--color-warn)]/8 px-2 py-1.5 text-[11px] text-[color:var(--color-ink)]">
          <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-warn)] mb-1">
            Blockers
          </div>
          <ul className="space-y-0.5">
            {manifest.blockers.map((b) => (
              <li key={b}>· {b}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Export readiness panel — Phase 4G. Single, plain-English summary of
// whether the future local export script will accept this preview.
// "ready" / "blocked" / "unknown" map cleanly to the manifest's three
// observable states. Always carries the same safety reminders so the
// operator never has to guess whether clicking something on this page
// will render pixels (it will not — there is no execution surface).

export type ExportReadinessUiState = "ready" | "blocked" | "unknown";

export function exportReadinessUiState(
  manifest: ExportManifest | null,
): ExportReadinessUiState {
  if (!manifest) return "unknown";
  if (manifest.exportReadiness === "ready") return "ready";
  return "blocked";
}

export function ExportReadinessPanel({
  manifest,
}: {
  manifest: ExportManifest | null;
}) {
  const state = exportReadinessUiState(manifest);
  const tone: "success" | "warn" | "neutral" =
    state === "ready" ? "success" : state === "blocked" ? "warn" : "neutral";
  const heading =
    state === "ready"
      ? "Ready to export locally"
      : state === "blocked"
        ? "Export blocked"
        : "Export readiness unknown";
  const explainer =
    state === "ready"
      ? "Every manifest blocker is resolved. The local stub script will accept the planned command in dry-run mode."
      : state === "blocked"
        ? "Resolve the blockers in the export manifest panel before copying the local command."
        : "No creative brief on this item yet — the manifest can't be computed.";
  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Export readiness
        </div>
        <Badge tone={tone}>{state}</Badge>
      </div>
      <div className="text-[11px] text-[color:var(--color-ink)] leading-snug">
        <div className="font-semibold">{heading}</div>
        <div className="text-[color:var(--color-ink-muted)] mt-0.5">
          {explainer}
        </div>
      </div>
      <ul className="text-[11px] text-[color:var(--color-ink-muted)] space-y-0.5 pl-3 list-disc">
        <li>
          Not executable from the website. Export runs only on the
          operator&rsquo;s machine.
        </li>
        <li>
          The local export script is{" "}
          <strong>dry-run only</strong> in Phase 4G (it refuses{" "}
          <code className="font-mono">--execute</code>).
        </li>
        <li>
          No files are created yet. No upload. No client share. No paid
          API.
        </li>
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// "What happens next?" panel — explains 4D / 4E / 4F roadmap in-place.

export function WhatHappensNextPanel() {
  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] p-3 text-xs text-[color:var(--color-ink-muted)] space-y-2">
      <div className="text-[color:var(--color-ink)] font-semibold">
        What happens next?
      </div>
      <ol className="list-decimal pl-4 space-y-1">
        <li>
          <strong>Phase 4D</strong> — local operator export script
          turns this preview into PNG/JPG. Gated like Seedance
          (confirmation phrase + cost estimate where paid). Nothing
          runs from this page.
        </li>
        <li>
          <strong>Phase 4E</strong> — client-safe visual preview
          lifecycle (<code className="font-mono">client_safe_visual_url</code>{" "}
          + prepare/share actions). Migration proposed in{" "}
          <code className="font-mono">docs/client_safe_visual_preview_plan.md</code>
          , NOT applied yet.
        </li>
        <li>
          <strong>Phase 4F</strong> — optional AI background layer
          opt-in (template owns text + layout; AI owns background
          only). Defer until 4E ships and is trusted.
        </li>
      </ol>
    </div>
  );
}
