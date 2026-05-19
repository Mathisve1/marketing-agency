"use client";

import * as React from "react";
import { Dialog } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";

interface Props {
  open: boolean;
  onClose: () => void;
  onSubmit: (body: string) => void;
  /** Different copy for the client portal vs operator side. */
  audience: "client" | "operator";
}

export function RequestChangesModal({ open, onClose, onSubmit, audience }: Props) {
  const [body, setBody] = React.useState("");

  const title =
    audience === "client" ? "What would you like changed?" : "Request operator-side changes";
  const placeholder =
    audience === "client"
      ? "e.g. The intro feels a bit too soft — can we lead with the rosehip ingredient line?"
      : "e.g. Tighten the hook to one sentence; swap shot 3 to a different smoothing pass.";

  return (
    <Dialog open={open} onClose={onClose} title={title}>
      <div className="space-y-3">
        <p className="text-sm text-[color:var(--color-ink-muted)]">
          {audience === "client"
            ? "Share what you'd like changed. The team will see this directly."
            : "Capture the change request in the audit trail; this also notifies the operator on call."}
        </p>
        <Textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder={placeholder}
          rows={5}
        />
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" size="md" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="md"
            disabled={!body.trim()}
            onClick={() => {
              onSubmit(body.trim());
              setBody("");
            }}
          >
            Send
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
