import { Badge } from "@/components/ui/badge";
import type { ContentStatus } from "@/lib/types";

const OPERATOR_LABEL: Record<ContentStatus, { label: string; tone: "neutral" | "success" | "warn" | "danger" | "info" }> = {
  draft: { label: "Draft", tone: "neutral" },
  generating: { label: "Generating", tone: "info" },
  raw_ready: { label: "Raw ready", tone: "info" },
  audio_fixer_pending: { label: "Audio Fixer pending", tone: "warn" },
  audio_fixed: { label: "Audio fixed", tone: "info" },
  ready_for_client_review: { label: "Ready for client", tone: "info" },
  shared_with_client: { label: "Shared with client", tone: "info" },
  approved_by_client: { label: "Approved", tone: "success" },
  changes_requested_by_client: { label: "Changes requested", tone: "warn" },
  failed: { label: "Failed", tone: "danger" },
};

const CLIENT_LABEL: Record<
  "ready_for_review" | "approved" | "changes_requested",
  { label: string; tone: "neutral" | "success" | "warn" | "info" }
> = {
  ready_for_review: { label: "Ready for review", tone: "info" },
  approved: { label: "Approved", tone: "success" },
  changes_requested: { label: "Changes requested", tone: "warn" },
};

export function OperatorStatusChip({ status }: { status: ContentStatus }) {
  const v = OPERATOR_LABEL[status];
  return <Badge tone={v.tone}>{v.label}</Badge>;
}

export function ClientStatusChip({
  status,
}: {
  status: "ready_for_review" | "approved" | "changes_requested";
}) {
  const v = CLIENT_LABEL[status];
  return <Badge tone={v.tone}>{v.label}</Badge>;
}
