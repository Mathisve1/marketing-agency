"use client";

// Phase 4F — copies a future-safe local export command to clipboard.
// The command points at the Phase 4E stub script and includes the
// content item id + the dashboard preview URL. **Does not execute
// from the website.** Clipboard only. Stub script today does nothing
// either — it just prints intent and exits 0.

import * as React from "react";
import { Button } from "@/components/ui/button";

interface Props {
  contentItemId: string;
  previewUrl: string;
  /** Optional template/theme overrides surfaced via querystring. */
  templateId?: string | null;
  themeId?: string | null;
  /** Inline reason to disable the button (e.g. "Resolve manifest
   *  blockers first"). When set the button is unclickable. */
  disabledReason?: string | null;
}

export function CopyExportCommandButton({
  contentItemId,
  previewUrl,
  templateId,
  themeId,
  disabledReason,
}: Props) {
  const [copied, setCopied] = React.useState(false);
  const disabled = Boolean(disabledReason);

  const params = new URLSearchParams();
  if (templateId) params.set("template", templateId);
  if (themeId) params.set("theme", themeId);
  const qs = params.toString();
  const fullUrl = qs ? `${previewUrl}?${qs}` : previewUrl;

  const command = [
    "# Phase 4F — local export command handoff (stub today; Phase 4D ships the real script).",
    "# This is a future-safe example. Today it prints intent and exits 0.",
    `py -3.11 scripts/export_visual_preview_stub.py \\`,
    `  --content-item-id "${contentItemId}" \\`,
    `  --preview-url     "${fullUrl}"`,
    "",
    "# When the real script lands in Phase 4D:",
    "#   - it will require an explicit confirmation phrase to run",
    "#   - it will refuse to run if export_readiness != ready",
    "#   - it will never auto-upload / publish / share",
  ].join("\n");

  function onCopy() {
    if (disabled) return;
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(command).then(
        () => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1800);
        },
        () => {},
      );
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        size="sm"
        variant="secondary"
        onClick={onCopy}
        disabled={disabled}
        title={
          disabledReason ??
          "Copy a local export command (Phase 4E stub — does not execute from the website)"
        }
        aria-disabled={disabled}
      >
        {copied ? "Copied!" : "Copy local export command"}
      </Button>
      <span className="text-[11px] text-[color:var(--color-ink-faint)] italic">
        Clipboard only. Stub today; Phase 4D replaces it with the real
        local script.
      </span>
    </div>
  );
}
