import Link from "next/link";
import { notFound } from "next/navigation";
import { ApprovalControls } from "@/components/content/approval-controls";
import { AudioFixerControl } from "@/components/content/audio-fixer-control";
import { ClientFeedbackSummary } from "@/components/content/client-feedback-summary";
import { ContentCard } from "@/components/content/content-card";
import { QualityTierPicker } from "@/components/content/quality-tier-picker";
import { RegenerationRequestsPanel } from "@/components/content/regeneration-requests-panel";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { getBrand, getCampaign, listContentForCampaign } from "@/lib/demo-data";
import { getContentApprovals, getContentFeedback } from "@/lib/data/feedback";
import { listRegenerationRequestsForContent } from "@/lib/data/regeneration-requests";

interface PageProps {
  params: Promise<{ campaignId: string }>;
}

export default async function CampaignOutputs({ params }: PageProps) {
  const { campaignId } = await params;
  const campaign = getCampaign(campaignId);
  if (!campaign) notFound();
  const brand = getBrand(campaign.brandId);
  const items = listContentForCampaign(campaign.id);

  // Load feedback + approvals per item in parallel. demo mode returns
  // the in-memory store; supabase mode reads from content_feedback +
  // content_approvals. Either way the shape is stable.
  const feedbackByItem = await Promise.all(
    items.map(async (item) => {
      const [feedback, approvals, regenerationRequests] = await Promise.all([
        getContentFeedback(item.id),
        getContentApprovals(item.id),
        listRegenerationRequestsForContent(item.id),
      ]);
      return { itemId: item.id, feedback, approvals, regenerationRequests };
    }),
  );
  const lookup = new Map(
    feedbackByItem.map((row) => [row.itemId, row]),
  );

  return (
    <div className="space-y-6 max-w-6xl">
      <div>
        <Link
          href={`/agency/brands/${campaign.brandId}`}
          className="text-sm text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
        >
          ← {brand?.name}
        </Link>
        <h1 className="mt-1 text-2xl font-semibold">{campaign.title} · Outputs</h1>
        <div className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
          Operator console for raw + audio-fixed Pai outputs. Quality tier
          and Audio Fixer are operator-only.
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Generate a new take</CardTitle>
        </CardHeader>
        <CardBody className="space-y-4">
          <QualityTierPicker durationSec={15} />
          <div className="text-xs text-[color:var(--color-ink-muted)]">
            Phase 1A: the Generate button is local-state only. No paid API call.
          </div>
        </CardBody>
      </Card>

      <div className="space-y-6">
        {items.map((c) => {
          const fb = lookup.get(c.id);
          return (
            <section key={c.id} id={c.id} className="space-y-3 scroll-mt-20">
              <ContentCard content={c} campaignId={campaign.id} />
              <div className="grid sm:grid-cols-2 gap-3">
                <AudioFixerControl completed={c.audioFixer.completed} />
                <ApprovalControls
                  audience="operator"
                  initialState={
                    c.status === "approved_by_client"
                      ? "approved"
                      : c.status === "changes_requested_by_client"
                      ? "changes_requested"
                      : "pending"
                  }
                  onRegenerate={() => {
                    // Phase 1A: local action only.
                  }}
                  onShareWithClient={() => {
                    // Phase 1A: local action only.
                  }}
                />
              </div>
              <ClientFeedbackSummary
                approvals={fb?.approvals ?? []}
                feedback={fb?.feedback ?? []}
              />
              <RegenerationRequestsPanel
                campaignId={campaign.id}
                contentId={c.id}
                requests={fb?.regenerationRequests ?? []}
              />
            </section>
          );
        })}
      </div>
    </div>
  );
}
