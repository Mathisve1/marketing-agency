import Link from "next/link";
import { notFound } from "next/navigation";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CreateMockGenerationJobButton } from "@/components/content/create-mock-generation-job-button";
import { DurationPlanner } from "@/components/content/duration-planner";
import { ForkPromptFromRequestButton } from "@/components/content/fork-prompt-from-request-button";
import { PromptVersionEditor } from "@/components/content/prompt-version-editor";
import {
  getCampaign,
  getContent,
  getBrand,
} from "@/lib/demo-data";
import {
  getLatestPromptVersion,
  listPromptVersions,
  type PromptVersion,
  type PromptVersionStatus,
} from "@/lib/data/prompt-versions";
import { listGenerationJobsForPromptVersion } from "@/lib/data/generation-jobs";
import {
  listOpenRegenerationRequestsForContent,
} from "@/lib/data/regeneration-requests";
import { FEEDBACK_REASON_LABELS } from "@/lib/data/feedback";

// Phase 1E — operator-only prompt-version editor.
//
// Route: /agency/campaigns/[campaignId]/content/[contentId]/prompt
//
// What this page does:
//   1. Loads every prompt_version for the content item.
//   2. Renders the latest (highest version_number) in an editable form.
//   3. Lists every other version as a read-only changelog beneath.
//   4. Surfaces every OPEN regeneration_request so the operator can
//      one-click "Fork new version from this request".
//
// What this page DOES NOT do:
//   - It NEVER triggers a paid Enhancor / Seedance / Audio Fixer call.
//     "Mark approved for generation" only records intent.
//   - It is never reachable from the /client/* tree — operator-only.

interface PageProps {
  params: Promise<{ campaignId: string; contentId: string }>;
}

const STATUS_LABEL: Record<PromptVersionStatus, string> = {
  draft: "Draft",
  operator_editing: "Operator editing",
  approved_for_generation: "Approved for generation",
  superseded: "Superseded",
};
const STATUS_TONE: Record<
  PromptVersionStatus,
  "info" | "warn" | "success" | "danger"
> = {
  draft: "info",
  operator_editing: "warn",
  approved_for_generation: "success",
  superseded: "danger",
};

export default async function PromptEditorPage({ params }: PageProps) {
  const { campaignId, contentId } = await params;
  const campaign = getCampaign(campaignId);
  if (!campaign) notFound();
  const content = getContent(contentId);
  if (!content) notFound();
  if (content.campaignId !== campaign.id) notFound();
  const brand = getBrand(campaign.brandId);

  const [allVersions, latest, openRequests] = await Promise.all([
    listPromptVersions(contentId),
    getLatestPromptVersion(contentId),
    listOpenRegenerationRequestsForContent(contentId),
  ]);
  // Phase 1F — when the latest version is approved-for-generation, show
  // the "Create mock generation job" button + any existing jobs.
  const latestJobs = latest
    ? await listGenerationJobsForPromptVersion(latest.id)
    : [];

  return (
    <div className="space-y-6 max-w-4xl">
      <div>
        <Link
          href={`/agency/campaigns/${campaign.id}/outputs`}
          className="text-sm text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
        >
          ← {brand?.name} · {campaign.title}
        </Link>
        <h1 className="mt-1 text-2xl font-semibold">
          Prompt editor · {content.title}
        </h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
          Operator-only. The fields here are NEVER shown to the client. No
          paid generation call is triggered by Save draft or Mark approved
          for generation.
        </p>
      </div>

      {openRequests.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Open client requests for this content</CardTitle>
          </CardHeader>
          <CardBody className="space-y-3">
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              Each open regeneration request can be turned into a new prompt
              version with one click. Forking will set the request to{" "}
              <em>accepted</em>; the operator then iterates in the editor below.
            </p>
            <ul className="space-y-3">
              {openRequests.map((r) => (
                <li
                  key={r.id}
                  className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-2 text-sm"
                >
                  <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                    {new Date(r.createdAt).toLocaleString("en-GB")} ·{" "}
                    {r.requestedByKind}
                    {r.reason && <> · {FEEDBACK_REASON_LABELS[r.reason]}</>}
                  </div>
                  <div className="leading-relaxed">{r.body}</div>
                  {latest && (
                    <ForkPromptFromRequestButton
                      regenerationRequestId={r.id}
                      parentVersionId={latest.id}
                      campaignId={campaign.id}
                      contentId={content.id}
                    />
                  )}
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>
            {latest ? (
              <>
                v{latest.versionNumber}
                {latest.label ? ` · ${latest.label}` : ""}
              </>
            ) : (
              "No prompt version yet"
            )}
          </CardTitle>
        </CardHeader>
        <CardBody>
          {latest ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={STATUS_TONE[latest.status]}>
                  {STATUS_LABEL[latest.status]}
                </Badge>
                <span className="text-xs text-[color:var(--color-ink-muted)]">
                  Last updated{" "}
                  {new Date(latest.updatedAt).toLocaleString("en-GB")}
                </span>
              </div>
              <PromptVersionEditor
                promptVersion={latest}
                campaignId={campaign.id}
                contentId={content.id}
              />
            </div>
          ) : (
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              This content item doesn&rsquo;t have a prompt version yet. Phase
              1F will add an &ldquo;Initialise prompt&rdquo; flow; for now the
              demo seed creates v1 automatically for the two seeded items.
            </p>
          )}
        </CardBody>
      </Card>

      {latest && (
        <Card>
          <CardHeader>
            <CardTitle>Ad duration plan (15 / 20 / 25 / 30s)</CardTitle>
          </CardHeader>
          <CardBody>
            <DurationPlanner
              negativePrompt={latest.negativePrompt}
              qualityTier={latest.qualityTier}
              promptVersionId={latest.id}
              campaignId={campaign.id}
              contentId={content.id}
              promptStatus={latest.status}
            />
          </CardBody>
        </Card>
      )}

      {latest && (
        <Card>
          <CardHeader>
            <CardTitle>Generation jobs</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            {latest.status === "approved_for_generation" ? (
              <>
                <p className="text-sm text-[color:var(--color-ink-muted)]">
                  This prompt version is approved for generation. Creating a
                  mock job records operator intent in the
                  <code className="font-mono text-xs px-1">generation_jobs</code>
                  table — no paid Enhancor / Seedance call is made.
                </p>
                <CreateMockGenerationJobButton
                  promptVersionId={latest.id}
                  campaignId={campaign.id}
                  contentId={content.id}
                />
              </>
            ) : (
              <p className="text-sm text-[color:var(--color-ink-muted)]">
                Mark this version <em>approved for generation</em> first to
                unlock the mock job creation button. Phase 1F never auto-
                generates jobs.
              </p>
            )}
            {latestJobs.length > 0 && (
              <div className="pt-3 border-t border-[color:var(--color-hairline)] space-y-2">
                <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                  Jobs from this prompt version
                </div>
                <ul className="space-y-2 text-sm">
                  {latestJobs.map((j) => (
                    <li
                      key={j.id}
                      className="flex items-center justify-between gap-3 rounded-md border border-[color:var(--color-hairline)] px-3 py-2"
                    >
                      <div className="min-w-0">
                        <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                          {new Date(j.createdAt).toLocaleString("en-GB")} ·{" "}
                          {j.provider}
                          {j.providerMode ? ` · ${j.providerMode}` : ""}
                        </div>
                        <div className="mt-0.5">
                          <Badge tone="neutral">{j.status}</Badge>
                        </div>
                      </div>
                      <Link
                        href={`/agency/jobs/${j.id}`}
                        className="text-sm text-[color:var(--color-accent)] underline shrink-0"
                      >
                        Open job →
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </CardBody>
        </Card>
      )}

      {allVersions.length > 1 && (
        <Card>
          <CardHeader>
            <CardTitle>Version history</CardTitle>
          </CardHeader>
          <CardBody>
            <ul className="divide-y divide-[color:var(--color-hairline)]">
              {allVersions
                .filter((v) => v.id !== latest?.id)
                .map((v) => (
                  <VersionHistoryRow key={v.id} version={v} />
                ))}
            </ul>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

function VersionHistoryRow({ version }: { version: PromptVersion }) {
  return (
    <li className="py-3 text-sm">
      <div className="flex items-center gap-2">
        <Badge tone={STATUS_TONE[version.status]}>
          {STATUS_LABEL[version.status]}
        </Badge>
        <span className="font-semibold">v{version.versionNumber}</span>
        {version.label && (
          <span className="text-[color:var(--color-ink-muted)]">
            · {version.label}
          </span>
        )}
        <span className="ml-auto text-xs text-[color:var(--color-ink-faint)]">
          {new Date(version.updatedAt).toLocaleString("en-GB")}
        </span>
      </div>
      {version.notes && (
        <div className="mt-1 text-xs text-[color:var(--color-ink-muted)] leading-relaxed">
          {version.notes}
        </div>
      )}
    </li>
  );
}
