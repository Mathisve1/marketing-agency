// Yuvo Studio — Phase 2E Copy Drafts (non-video content).
//
// OPERATOR-ONLY. Lists non-video draft content items and lets the
// operator generate deterministic copy into caption_draft. The page
// itself is read-only; the per-item panel calls the Copy Draft Agent
// action. NO video generation, NO publish, NO client share, NO email.

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { getCurrentPersona } from "@/lib/auth/persona";
import { getDataSource, getDefaultWorkspaceId } from "@/lib/data/_source";
import { listCopyDraftQueueForWorkspace } from "@/lib/data/owner-overview";
import { CopyDraftPanel } from "@/components/agents/copy-draft-panel";
import { CopyApprovalPanel } from "@/components/agents/copy-approval-panel";
import { ClientPreviewPanel } from "@/components/agents/client-preview-panel";
import { CopyRevisionPanel } from "@/components/agents/copy-revision-panel";
import { CLAUDE_CODE_TASK_STATUS_LABELS } from "@/lib/tasks/claude-code-tasks";

export const dynamic = "force-dynamic";

export default async function CopyDraftsPage() {
  let workspaceId = getDefaultWorkspaceId();
  if (getDataSource() === "supabase") {
    const persona = await getCurrentPersona();
    if (persona?.kind === "operator" && persona.workspaceIds.length > 0) {
      workspaceId = persona.workspaceIds[0];
    }
  }

  const { items, total } = await listCopyDraftQueueForWorkspace(workspaceId);
  const drafted = items.filter((i) => i.copyDraftStatus === "drafted").length;
  const approved = items.filter(
    (i) => i.copyApprovalStatus === "approved_internal",
  ).length;
  const prepared = items.filter(
    (i) =>
      i.clientPreviewStatus === "prepared" ||
      i.clientPreviewStatus === "shared_with_client",
  ).length;
  const shared = items.filter(
    (i) => i.clientPreviewStatus === "shared_with_client",
  ).length;

  return (
    <div className="space-y-6 max-w-5xl">
      <header>
        <h1 className="text-2xl font-semibold">Copy drafts (non-video)</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
          Operator-only. Deterministic copy for non-video formats
          (LinkedIn / posts / stories / carousels / email / blog).
          Generating a draft writes to{" "}
          <code className="font-mono">caption_draft</code> only — it never
          generates a video, creates a prompt version, publishes, sends an
          email, or shares with the client.
        </p>
      </header>

      <section className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <Stat label="Non-video items" value={total} />
        <Stat label="Copy drafted" value={drafted} tone="success" />
        <Stat label="Awaiting copy" value={total - drafted} tone="warn" />
        <Stat label="Approved internally" value={approved} tone="success" />
        <Stat label="Preview prepared" value={prepared} tone="success" />
        <Stat label="Shared with client" value={shared} tone="success" />
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Queue · {total} item{total === 1 ? "" : "s"}</CardTitle>
        </CardHeader>
        <CardBody>
          {items.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              No non-video draft content items yet. Run the Brand Analysis
              + Calendar Agent to produce multi-format drafts.
            </p>
          ) : (
            <ul className="divide-y divide-[color:var(--color-hairline)]">
              {items.map((it) => (
                <li key={it.contentItemId} className="py-3 space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{it.title}</span>
                    {it.channel && (
                      <Badge tone="neutral">{it.channel}</Badge>
                    )}
                    {it.format && (
                      <Badge tone="neutral">{it.format}</Badge>
                    )}
                    <Badge tone="neutral">{it.contentStatus}</Badge>
                    <Badge
                      tone={
                        it.copyDraftStatus === "drafted"
                          ? "success"
                          : "warn"
                      }
                    >
                      {it.copyDraftStatus === "drafted"
                        ? "copy drafted"
                        : "needs copy"}
                    </Badge>
                    {it.copyApprovalStatus === "approved_internal" && (
                      <Badge tone="success">approved internally</Badge>
                    )}
                    {it.clientPreviewStatus === "prepared" && (
                      <Badge tone="info">preview prepared</Badge>
                    )}
                    {it.clientPreviewStatus === "shared_with_client" && (
                      <Badge tone="success">shared with client</Badge>
                    )}
                  </div>
                  <div className="text-xs text-[color:var(--color-ink-muted)]">
                    {it.brandName ?? "—"} ·{" "}
                    {it.campaignName ?? it.campaignId.slice(0, 8)} ·
                    scheduled{" "}
                    {it.scheduledFor
                      ? new Date(it.scheduledFor).toLocaleDateString(
                          "en-GB",
                        )
                      : "—"}
                  </div>
                  {it.captionPreview && (
                    <div className="text-[11px] text-[color:var(--color-ink-faint)] italic line-clamp-2">
                      {it.captionPreview}
                    </div>
                  )}
                  <CopyRevisionPanel
                    contentItemId={it.contentItemId}
                    contentStatus={it.contentStatus}
                    latestClientFeedback={it.latestClientFeedback}
                    openRegenerationRequest={it.openRegenerationRequest}
                  />
                  <CopyDraftPanel
                    contentItemId={it.contentItemId}
                    alreadyDrafted={it.copyDraftStatus === "drafted"}
                  />
                  <CopyApprovalPanel
                    contentItemId={it.contentItemId}
                    hasDraft={it.copyDraftStatus === "drafted"}
                    approvalStatus={it.copyApprovalStatus}
                    approvedAt={it.copyApprovedAt}
                    approvalNotes={it.copyApprovalNotes}
                    captionDraft={it.captionDraftFull}
                  />
                  <ClientPreviewPanel
                    contentItemId={it.contentItemId}
                    isApprovedInternally={
                      it.copyApprovalStatus === "approved_internal"
                    }
                    previewStatus={it.clientPreviewStatus}
                    previewAt={it.clientPreviewAt}
                    previewText={it.clientPreviewText}
                    captionDraft={it.captionDraftFull}
                  />
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      {/* TASK 7 — Claude Code handoff placeholder for advanced copy. */}
      <Card>
        <CardHeader>
          <CardTitle>Advanced copy via Claude Code (concept)</CardTitle>
        </CardHeader>
        <CardBody className="space-y-2">
          <p className="text-xs text-[color:var(--color-ink-muted)]">
            The deterministic draft above is enough for most posts. For
            high-stakes or long-form copy, the dashboard can later
            <em> prepare</em> a Claude Code task that an operator runs in
            their own session; results are written back to Supabase.{" "}
            <strong>
              Nothing here runs Claude Code, calls the Claude API, or
              spends credits.
            </strong>{" "}
            See{" "}
            <code className="font-mono">
              docs/hybrid_claude_code_execution_model.md
            </code>
            .
          </p>
          <div className="flex flex-wrap gap-2 text-[10px]">
            {(
              Object.values(CLAUDE_CODE_TASK_STATUS_LABELS) as string[]
            ).map((label) => (
              <Badge key={label} tone="neutral">
                {label}
              </Badge>
            ))}
          </div>
          <Button variant="ghost" size="sm" disabled className="self-start">
            Prepare Claude Code copy task (coming soon)
          </Button>
        </CardBody>
      </Card>

      <p className="text-[10px] text-[color:var(--color-ink-faint)] italic">
        Copy drafts are operator-review artefacts. Approving non-video
        posts for client review / publishing is a separate, future,
        explicitly-gated step — this page never does it.
      </p>
      <Link
        href="/agency"
        className="text-sm text-[color:var(--color-accent)] underline"
      >
        ← Owner command center
      </Link>
    </div>
  );
}

function Stat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number;
  tone?: "neutral" | "warn" | "success";
}) {
  const cls =
    tone === "success"
      ? "text-[color:var(--color-success)]"
      : tone === "warn"
        ? "text-[color:var(--color-warn)]"
        : "text-[color:var(--color-ink)]";
  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3">
      <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${cls}`}>
        {value}
      </div>
    </div>
  );
}
