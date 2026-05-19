"use client";

// Approval row used on both sides. The `audience` prop swaps copy +
// behaviour so the same component can render on the operator side AND on
// the client portal. The component intentionally does NOT show operator
// internals (cost, provider names) — those live elsewhere.

import * as React from "react";
import { Button } from "@/components/ui/button";
import { RequestChangesModal } from "./request-changes-modal";

interface Props {
  audience: "client" | "operator";
  /** Initial visual state — for Phase 1A this is local only. */
  initialState?: "pending" | "approved" | "changes_requested";
  /** Operator-side extra: ability to mark as shared with the client. */
  onShareWithClient?: () => void;
  /** Operator-side extra: regenerate the take. */
  onRegenerate?: () => void;
}

export function ApprovalControls({
  audience,
  initialState = "pending",
  onShareWithClient,
  onRegenerate,
}: Props) {
  const [state, setState] = React.useState(initialState);
  const [modalOpen, setModalOpen] = React.useState(false);
  const [requestBody, setRequestBody] = React.useState<string | null>(null);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Button
          variant="success"
          size="md"
          disabled={state === "approved"}
          onClick={() => setState("approved")}
        >
          {state === "approved" ? "✓ Approved" : "Approve"}
        </Button>
        <Button
          variant="secondary"
          size="md"
          onClick={() => setModalOpen(true)}
        >
          Request changes
        </Button>
        {audience === "operator" && onRegenerate && (
          <Button variant="ghost" size="md" onClick={onRegenerate}>
            Regenerate
          </Button>
        )}
        {audience === "operator" && onShareWithClient && (
          <Button variant="primary" size="md" onClick={onShareWithClient}>
            Share with client
          </Button>
        )}
      </div>

      {state === "changes_requested" && requestBody && (
        <div className="rounded-md bg-[color:var(--color-warn)]/10 border border-[color:var(--color-warn)]/30 px-3 py-2 text-sm">
          <span className="font-semibold text-[color:var(--color-warn)]">
            Changes requested:
          </span>{" "}
          <span className="text-[color:var(--color-ink)]">{requestBody}</span>
        </div>
      )}

      <RequestChangesModal
        audience={audience}
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSubmit={(body) => {
          setRequestBody(body);
          setState("changes_requested");
          setModalOpen(false);
        }}
      />
    </div>
  );
}
