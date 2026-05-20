"use client";

// Yuvo Studio — Phase 4G copy-local-export-command button.
//
// Clipboard-only handoff. Builds the command via the shared
// `buildVisualExportCommand` helper (web/lib/creative/export-command.ts)
// so the dashboard, the local stub script, and the script-level tests
// all read the same contract.
//
// HARD RULES:
//   - No server action. No fetch. No exec / spawn / child_process.
//   - Never executes the command from the website.
//   - Refuses to write to the clipboard when `disabledReason` is set
//     (e.g. manifest blockers exist) — same gate as
//     `CopyExportBriefButton`.
//   - Stub script today refuses `--execute`, so even if an operator
//     pastes the command into a terminal nothing renders pixels.

import * as React from "react";
import { Button } from "@/components/ui/button";
import {
  buildVisualExportCommand,
  type ExportFormat,
} from "@/lib/creative/export-command";

interface Props {
  contentItemId: string;
  previewUrl: string;
  templateId?: string | null;
  themeId?: string | null;
  /** Resolved preview mode (carousel / story / feed_post / …). */
  mode?: string | null;
  /** Template's recommended export size — falls back to "?" when
   *  unknown, the stub treats missing dimensions as a soft warning. */
  width?: number | null;
  height?: number | null;
  /** `"png"` is the default — `"jpg"` is accepted only as a hint for
   *  the future real implementation. */
  format?: ExportFormat;
  /** Operator-friendly default output directory. The stub sanitises
   *  this and never creates the directory itself. */
  outputDir?: string;
  /** Inline reason to disable the button (e.g. "Resolve manifest
   *  blockers first"). When set the button is unclickable. */
  disabledReason?: string | null;
}

export function CopyExportCommandButton({
  contentItemId,
  previewUrl,
  templateId,
  themeId,
  mode,
  width,
  height,
  format = "png",
  outputDir,
  disabledReason,
}: Props) {
  const [copied, setCopied] = React.useState(false);
  const disabled = Boolean(disabledReason);

  // Build the deterministic command once per render. The helper
  // guarantees the same operator-pasted text on the dashboard, the
  // stub script, and the tests.
  const cmd = React.useMemo(
    () =>
      buildVisualExportCommand({
        contentItemId,
        previewUrl,
        mode: mode ?? "unknown",
        templateId: templateId ?? null,
        themeId: themeId ?? "neutral",
        width: typeof width === "number" ? width : null,
        height: typeof height === "number" ? height : null,
        format,
        outputDir,
        // Always default to a dry-run. The stub additionally refuses
        // --execute today — see scripts/export_visual_preview_stub.py.
        dryRun: true,
      }),
    [
      contentItemId,
      previewUrl,
      mode,
      templateId,
      themeId,
      width,
      height,
      format,
      outputDir,
    ],
  );

  function onCopy() {
    if (disabled) return;
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(cmd.command).then(
        () => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1800);
        },
        () => {
          // Fallback: do nothing visible. Operator can open the
          // disclosure below and copy manually.
        },
      );
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="secondary"
          onClick={onCopy}
          disabled={disabled}
          title={
            disabledReason ??
            "Copy a local export command. Dry-run only — does not execute from the website."
          }
          aria-disabled={disabled}
        >
          {copied ? "Copied!" : "Copy local export command"}
        </Button>
        <span className="text-[11px] text-[color:var(--color-ink-faint)] italic">
          Clipboard only. Dry-run by default. The local stub refuses
          <code className="font-mono px-1">--execute</code> until the
          real export pipe ships (Phase 4H).
        </span>
      </div>
      <details className="text-[11px]">
        <summary className="cursor-pointer text-[color:var(--color-ink-muted)]">
          Show command preview · {cmd.argv.length} args · planned path{" "}
          <code className="font-mono">{cmd.plannedOutputPath}</code>
        </summary>
        <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] p-2 text-[10px] leading-relaxed font-mono">
          {cmd.command}
        </pre>
      </details>
    </div>
  );
}
