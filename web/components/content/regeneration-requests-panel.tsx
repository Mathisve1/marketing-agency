// Pure server component — renders the operator-visible regeneration
// queue for one content item. Phase 1E.
//
// The interactive Accept / Dismiss / Open-prompt-editor controls are
// in a sibling client component; this file is just layout + data
// pass-through.

import { FEEDBACK_REASON_LABELS } from "@/lib/data/feedback";
import type { RegenerationRequest } from "@/lib/data/regeneration-requests";
import { Badge } from "@/components/ui/badge";
import { RegenerationRequestControls } from "./regeneration-request-controls";

interface Props {
  campaignId: string;
  contentId: string;
  requests: RegenerationRequest[];
}

const STATUS_TONE: Record<
  RegenerationRequest["status"],
  "info" | "warn" | "success" | "danger"
> = {
  open: "warn",
  accepted: "info",
  fulfilled: "success",
  dismissed: "danger",
};

const STATUS_LABEL: Record<RegenerationRequest["status"], string> = {
  open: "Open",
  accepted: "Accepted",
  fulfilled: "Fulfilled",
  dismissed: "Dismissed",
};

export function RegenerationRequestsPanel({
  campaignId,
  contentId,
  requests,
}: Props) {
  if (requests.length === 0) return null;

  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-4 space-y-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Regenerate requests
        </div>
        <span className="text-xs text-[color:var(--color-ink-muted)]">
          {requests.length}
        </span>
      </div>
      <ul className="space-y-3">
        {requests.map((r) => (
          <li
            key={r.id}
            className="rounded-md border border-[color:var(--color-hairline)] p-3 space-y-2"
          >
            <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
              <Badge tone={STATUS_TONE[r.status]}>
                {STATUS_LABEL[r.status]}
              </Badge>
              <span>·</span>
              <span>{r.requestedByKind}</span>
              <span>·</span>
              <span>{new Date(r.createdAt).toLocaleString("en-GB")}</span>
              {r.reason && (
                <>
                  <span>·</span>
                  <span>{FEEDBACK_REASON_LABELS[r.reason]}</span>
                </>
              )}
            </div>
            <div className="leading-relaxed">{r.body}</div>
            <RegenerationRequestControls
              requestId={r.id}
              campaignId={campaignId}
              contentId={contentId}
              status={r.status}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}
