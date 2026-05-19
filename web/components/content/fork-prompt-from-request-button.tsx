"use client";

// Phase 1E — operator one-click action to fork a new prompt_version
// from an open regeneration_request. Mounted on the prompt editor
// page alongside the inline list of open requests.

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { createPromptVersionFromRegenerationRequestAction } from "@/lib/actions/prompt-versions";

interface Props {
  regenerationRequestId: string;
  parentVersionId: string;
  campaignId: string;
  contentId: string;
}

export function ForkPromptFromRequestButton({
  regenerationRequestId,
  parentVersionId,
  campaignId,
  contentId,
}: Props) {
  const router = useRouter();
  const [error, setError] = React.useState<string | null>(null);
  const [pending, startTransition] = React.useTransition();

  function onClick() {
    setError(null);
    startTransition(async () => {
      const result = await createPromptVersionFromRegenerationRequestAction({
        regenerationRequestId,
        parentVersionId,
        campaignId,
        contentId,
      });
      if (!result.ok) {
        setError(result.error ?? "Could not fork.");
        return;
      }
      router.refresh();
    });
  }

  return (
    <div className="space-y-1">
      <Button variant="primary" size="sm" onClick={onClick} disabled={pending}>
        {pending ? "Forking…" : "Fork new version from this request"}
      </Button>
      {error && (
        <div className="text-xs text-[color:var(--color-danger)]">{error}</div>
      )}
    </div>
  );
}
