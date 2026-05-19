// Agency-side display of client feedback + approval history for one
// content item. Pure server component — no actions. Operators see the
// raw thread + the latest approval decision; clients never see this
// component.

import {
  FEEDBACK_REASON_LABELS,
  type ApprovalEntry,
  type FeedbackEntry,
} from "@/lib/data/feedback";
import { Badge } from "@/components/ui/badge";

interface Props {
  approvals: ApprovalEntry[];
  feedback: FeedbackEntry[];
}

const DECISION_LABEL: Record<ApprovalEntry["decision"], string> = {
  approved: "Approved",
  changes_requested: "Changes requested",
  rejected: "Rejected",
};

const DECISION_TONE: Record<ApprovalEntry["decision"], "success" | "warn" | "danger"> =
  {
    approved: "success",
    changes_requested: "warn",
    rejected: "danger",
  };

export function ClientFeedbackSummary({ approvals, feedback }: Props) {
  const latest = approvals[0] ?? null; // approvals come back desc

  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-4 space-y-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Client feedback
        </div>
        {latest ? (
          <span className="flex items-center gap-2">
            <Badge tone={DECISION_TONE[latest.decision]}>
              {DECISION_LABEL[latest.decision]}
            </Badge>
            <span className="text-xs text-[color:var(--color-ink-muted)]">
              {new Date(latest.decidedAt).toLocaleString("en-GB")}
            </span>
          </span>
        ) : (
          <span className="text-xs text-[color:var(--color-ink-muted)]">
            No decision yet
          </span>
        )}
      </div>

      {latest?.note && (
        <div className="text-sm leading-relaxed">
          <span className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)] mr-2">
            Note
          </span>
          {latest.note}
        </div>
      )}

      {feedback.length === 0 ? (
        <p className="text-xs text-[color:var(--color-ink-muted)]">
          No client comments on this item yet.
        </p>
      ) : (
        <ul className="space-y-2">
          {feedback.map((c) => (
            <li
              key={c.id}
              className="rounded-md border border-[color:var(--color-hairline)] p-2.5"
            >
              <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                <span>{c.author}</span>
                <span>·</span>
                <span>{new Date(c.createdAt).toLocaleString("en-GB")}</span>
                {c.reason && (
                  <>
                    <span>·</span>
                    <span>{FEEDBACK_REASON_LABELS[c.reason]}</span>
                  </>
                )}
              </div>
              <div className="mt-1 leading-relaxed text-sm">{c.body}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
