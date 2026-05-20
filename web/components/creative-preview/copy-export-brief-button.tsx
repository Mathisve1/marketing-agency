"use client";

// Phase 4E — copies the rendered export brief plaintext to clipboard.
// No server action; no DB write; no fetch; purely a clipboard helper.

import * as React from "react";
import { Button } from "@/components/ui/button";

export function CopyExportBriefButton({
  briefText,
  disabledReason,
}: {
  briefText: string;
  /** If set, the button is rendered disabled with this reason as a
   *  tooltip / inline note (e.g. "Resolve blockers first"). */
  disabledReason?: string | null;
}) {
  const [copied, setCopied] = React.useState(false);
  const disabled = Boolean(disabledReason);

  function onCopy() {
    if (disabled) return;
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(briefText).then(
        () => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1800);
        },
        () => {
          // Fallback: do nothing visible; the operator can still
          // select + copy the manifest panel manually.
        },
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
        title={disabledReason ?? "Copy the planning brief to your clipboard"}
        aria-disabled={disabled}
      >
        {copied ? "Copied!" : "Copy export brief"}
      </Button>
      {disabledReason && (
        <span className="text-[11px] text-[color:var(--color-ink-faint)] italic">
          {disabledReason}
        </span>
      )}
    </div>
  );
}
