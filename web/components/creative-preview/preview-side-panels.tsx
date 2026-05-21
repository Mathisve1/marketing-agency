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
// Phase 5C — Client visual preview status panel (read-only).
//
// Server component. Renders the lifecycle gate the future Phase 5D
// client-share UI will plug into. ALWAYS disabled in Phase 5C: no
// share button, no prepare button, no file picker, no server-action
// form submit. The panel only explains what would be required for
// real sharing to become available.

import type { VisualPreviewSchemaStatus } from "@/lib/data/visual-preview-schema";

export function ClientVisualPreviewPanel({
  schema,
  hasInternalApproval,
  hasExportManifestReady,
  hasUploadedAsset,
}: {
  schema: VisualPreviewSchemaStatus;
  hasInternalApproval: boolean;
  hasExportManifestReady: boolean;
  hasUploadedAsset: boolean;
}) {
  // The next-required-step ladder. Order matters — show the FIRST
  // missing prerequisite, not all of them, so the operator has one
  // unambiguous action.
  let nextStep: string;
  if (schema.strategy === "not_configured") {
    nextStep = "Apply migration 012 + configure R2 binding (Phase 5C+).";
  } else if (schema.strategy === "content_items_extension") {
    nextStep = "Migrate to creative_assets (migration 012) — Option A is superseded.";
  } else if (!hasInternalApproval) {
    nextStep = "Approve the creative brief internally before sharing.";
  } else if (!hasExportManifestReady) {
    nextStep = "Resolve the export manifest blockers (see manifest panel).";
  } else if (!hasUploadedAsset) {
    nextStep =
      "Export + upload a PNG/JPG (Phase 5A real export + Phase 5C upload pipe).";
  } else {
    nextStep = "Phase 5D ships the actual prepare/share flow.";
  }

  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Client visual preview
        </div>
        <Badge tone="neutral">phase 5C</Badge>
      </div>
      <div className="text-xs text-[color:var(--color-ink-muted)]">
        Visual sharing is <strong>not enabled yet</strong>. This
        preview is internal-only. Migration 012 and R2 storage must be
        enabled before client visual sharing.
      </div>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-[11px]">
        <dt className="text-[color:var(--color-ink-faint)]">schema</dt>
        <dd className="font-mono">
          {schema.strategy}
          {schema.ready ? (
            <Badge tone="success">ready</Badge>
          ) : (
            <Badge tone="warn">not ready</Badge>
          )}
        </dd>
        <dt className="text-[color:var(--color-ink-faint)]">approval</dt>
        <dd>
          {hasInternalApproval ? (
            <Badge tone="success">approved internal</Badge>
          ) : (
            <Badge tone="neutral">not approved</Badge>
          )}
        </dd>
        <dt className="text-[color:var(--color-ink-faint)]">manifest</dt>
        <dd>
          {hasExportManifestReady ? (
            <Badge tone="success">ready</Badge>
          ) : (
            <Badge tone="warn">blockers</Badge>
          )}
        </dd>
        <dt className="text-[color:var(--color-ink-faint)]">uploaded asset</dt>
        <dd>
          {hasUploadedAsset ? (
            <Badge tone="success">present</Badge>
          ) : (
            <Badge tone="neutral">not yet</Badge>
          )}
        </dd>
      </dl>
      <div className="text-[11px] rounded-md border border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] px-2 py-1.5">
        <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          next step
        </span>{" "}
        {nextStep}
      </div>

      {/*
        DISABLED PHASE 5C controls — pure UI, never wired. The future
        Phase 5D panel will mount real client components in their
        place, each gated on `schema.ready` + approval + uploaded
        asset.
      */}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button
          type="button"
          disabled
          aria-disabled="true"
          title="Disabled in Phase 5C. Phase 5D ships the real prepare action."
          className="text-xs px-2.5 py-1.5 rounded-md border border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] text-[color:var(--color-ink-muted)] cursor-not-allowed opacity-70"
        >
          Prepare client preview (Phase 5D)
        </button>
        <button
          type="button"
          disabled
          aria-disabled="true"
          title="Disabled in Phase 5C. Sharing flips a row in creative_assets — migration 012 must be applied first."
          className="text-xs px-2.5 py-1.5 rounded-md border border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] text-[color:var(--color-ink-muted)] cursor-not-allowed opacity-70"
        >
          Share with client (Phase 5D)
        </button>
        <span className="text-[11px] text-[color:var(--color-ink-faint)] italic">
          No share button is wired. The client portal cannot see this
          preview.
        </span>
      </div>

      {schema.missing.length > 0 && (
        <details className="text-[11px]">
          <summary className="cursor-pointer text-[color:var(--color-ink-muted)]">
            Missing prerequisites ({schema.missing.length})
          </summary>
          <ul className="mt-1 space-y-0.5 text-[color:var(--color-ink-muted)]">
            {schema.missing.map((m) => (
              <li key={m}>· {m}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Phase 5B — Storage / Upload status panel (placeholder only).
//
// Pure display. No buttons, no file picker, no server action, no
// fetch. Explains the storage decision (R2, planned) and where the
// future upload command will plug in.

export function StorageStatusPanel() {
  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Storage · Upload status
        </div>
        <Badge tone="neutral">phase 5B</Badge>
      </div>
      <div className="text-xs text-[color:var(--color-ink-muted)] space-y-1">
        <div>
          <strong>Upload is not enabled yet.</strong> Phase 5B only
          defines the storage path and upload plan — no file is sent
          anywhere from this page.
        </div>
        <div>
          <strong>Planned storage:</strong> Cloudflare R2 bucket{" "}
          <code className="font-mono">yuvo-visual-assets</code>{" "}
          (binding <code className="font-mono">VISUAL_ASSETS_BUCKET</code>).
        </div>
        <div>
          <strong>Object key shape:</strong>{" "}
          <code className="font-mono text-[10px]">
            visual-assets/&#123;workspace_id&#125;/&#123;content_item_id&#125;/&#123;template_id&#125;/&#123;theme_id&#125;/&#123;filename&#125;
          </code>
        </div>
        <div>
          <strong>Lifecycle today:</strong> local export only (Phase
          5A). Upload + <code className="font-mono">creative_assets</code>{" "}
          row + client share land in Phase 5C under explicit operator
          approval.
        </div>
      </div>
      <div className="text-[10px] text-[color:var(--color-ink-faint)] italic">
        No file picker, no upload button, no client share on this page.
      </div>
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
