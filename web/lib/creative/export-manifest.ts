// Yuvo Studio — Phase 4E export manifest helper.
//
// Pure, deterministic. No I/O, no fetch, no DB call, no PNG output.
// Builds a planning-only "export manifest" the operator can read on
// the preview page and copy as a brief for the future local export
// script (`scripts/export_visual_preview_stub.py` placeholder).

import type { VisualPreviewRenderInput } from "./visual-preview-types";
import { findTemplate, type CreativeBriefTemplate } from "./templates";

export type ExportReadiness = "ready" | "not_ready";

export interface ExportManifest {
  contentItemId: string;
  mode: string;
  templateId: string | null;
  templateLabel: string | null;
  templateStatus: "active" | "planned" | null;
  themeId: string;
  recommendedWidth: number | null;
  recommendedHeight: number | null;
  aspectRatio: string | null;
  /** For carousels: slide count. */
  slideCount: number | null;
  /** For stories: frame count. */
  frameCount: number | null;
  exportFormat: "png";
  /** Suggested filenames the operator can use when saving the local
   *  PNG export (Phase 4D / 4E future). Deterministic, slug-safe. */
  assetNamingSuggestion: string[];
  /** "ready" iff there are zero blockers. Note: "ready" only means
   *  the *manifest* is complete enough to start a local export — it
   *  does NOT mean any export has run. */
  exportReadiness: ExportReadiness;
  blockers: string[];
  notes: string[];
}

export interface BuildManifestInput {
  preview: VisualPreviewRenderInput;
  /** Whether the operator has already approved the brief internally
   *  (Phase 4D1). Approval is required for "ready" readiness. */
  approvedInternal: boolean;
}

function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "content";
}

export function buildExportManifest(input: BuildManifestInput): ExportManifest {
  const { preview, approvedInternal } = input;
  const tpl: CreativeBriefTemplate | null = findTemplate(
    preview.asset.mode,
    preview.asset.templateId,
  );

  const slideCount =
    preview.asset.mode === "carousel"
      ? preview.asset.slides?.length ?? 0
      : null;
  const frameCount =
    preview.asset.mode === "story"
      ? preview.asset.frames?.length ?? 0
      : null;

  const slug = slugify(preview.asset.title);
  const baseName =
    preview.asset.templateId ?? `${preview.asset.mode}_default`;

  const assetNamingSuggestion: string[] = [];
  if (slideCount && slideCount > 0) {
    for (let i = 1; i <= slideCount; i++) {
      assetNamingSuggestion.push(
        `${baseName}-${slug}-slide${String(i).padStart(2, "0")}.png`,
      );
    }
  } else if (frameCount && frameCount > 0) {
    for (let i = 1; i <= frameCount; i++) {
      assetNamingSuggestion.push(
        `${baseName}-${slug}-frame${String(i).padStart(2, "0")}.png`,
      );
    }
  } else {
    assetNamingSuggestion.push(`${baseName}-${slug}.png`);
  }

  const blockers: string[] = [];
  if (preview.asset.mode === "unknown") {
    blockers.push(
      "Preview mode is unknown — re-draft the creative brief so a recognised format/mode lands.",
    );
  }
  if (!preview.asset.templateId) {
    blockers.push("No template id resolved for this mode.");
  }
  if (tpl && tpl.status === "planned") {
    blockers.push(
      `Template "${tpl.id}" is registered but not yet active — preview renders via the default for the mode.`,
    );
  }
  if (slideCount === 0) {
    blockers.push(
      "Carousel has zero parsed slides — re-draft the brief to populate Slides 1–5.",
    );
  }
  if (frameCount === 0) {
    blockers.push(
      "Story has zero parsed frames — re-draft the brief to populate frames.",
    );
  }
  if (!approvedInternal) {
    blockers.push(
      'No internal approval yet — click "Approve internal" before exporting.',
    );
  }

  const notes: string[] = [
    "Manifest is planning-only — no export has run.",
    "Future local export script: scripts/export_visual_preview_stub.py (placeholder).",
    "Client share lives behind a separate Phase 4E migration (client_safe_visual_url).",
  ];

  return {
    contentItemId: preview.contentItemId,
    mode: preview.asset.mode,
    templateId: preview.asset.templateId,
    templateLabel: tpl?.label ?? null,
    templateStatus: tpl?.status ?? null,
    themeId: preview.theme.themeId,
    recommendedWidth: tpl?.exportSize.width ?? null,
    recommendedHeight: tpl?.exportSize.height ?? null,
    aspectRatio: tpl?.aspectRatio ?? null,
    slideCount,
    frameCount,
    exportFormat: "png",
    assetNamingSuggestion,
    exportReadiness: blockers.length === 0 ? "ready" : "not_ready",
    blockers,
    notes,
  };
}

/** Plaintext rendering used by the "Copy export brief" button. Pure
 *  string composition — no clipboard call here (that lives in the
 *  client component). */
export function renderExportManifestText(
  manifest: ExportManifest,
  options: { previewUrl?: string } = {},
): string {
  const lines: string[] = [];
  lines.push("# Yuvo Studio — Visual preview export brief");
  lines.push("");
  lines.push(`content_item_id: ${manifest.contentItemId}`);
  if (options.previewUrl) lines.push(`preview_url:     ${options.previewUrl}`);
  lines.push(`mode:            ${manifest.mode}`);
  lines.push(`template_id:     ${manifest.templateId ?? "(none)"}`);
  if (manifest.templateLabel) {
    lines.push(`template_label:  ${manifest.templateLabel}`);
  }
  lines.push(`theme_id:        ${manifest.themeId}`);
  lines.push(
    `recommended_size: ${manifest.recommendedWidth ?? "?"}x${manifest.recommendedHeight ?? "?"} (${manifest.aspectRatio ?? "?"})`,
  );
  if (manifest.slideCount !== null) lines.push(`slide_count:     ${manifest.slideCount}`);
  if (manifest.frameCount !== null) lines.push(`frame_count:     ${manifest.frameCount}`);
  lines.push(`export_format:   ${manifest.exportFormat}`);
  lines.push(`export_readiness:${manifest.exportReadiness}`);
  lines.push("");
  lines.push("## Suggested filenames");
  for (const f of manifest.assetNamingSuggestion) lines.push(`- ${f}`);
  if (manifest.blockers.length > 0) {
    lines.push("");
    lines.push("## Blockers (resolve before exporting)");
    for (const b of manifest.blockers) lines.push(`- ${b}`);
  }
  lines.push("");
  lines.push("## Manual export instructions (Phase 4D — local script)");
  lines.push("1. Open the preview URL above in a Chromium-based browser.");
  lines.push("2. Wait for fonts + radial highlight to render.");
  lines.push("3. Use the page's full-page screenshot (DevTools or a script).");
  lines.push("4. Save each slide/frame using the suggested filenames.");
  lines.push("5. DO NOT upload, publish, or share with the client.");
  lines.push("6. Hand the PNGs to the operator who runs the client share.");
  lines.push("");
  lines.push("## Safety reminder");
  lines.push("- No paid API was called to build this brief.");
  lines.push("- Internal only — do not share with the client portal.");
  return lines.join("\n");
}
