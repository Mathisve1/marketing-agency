import Link from "next/link";
import { notFound } from "next/navigation";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { OperatorStatusChip } from "@/components/content/status-chip";
import { getBrand, getCampaign, listContentForCampaign } from "@/lib/demo-data";

interface PageProps {
  params: Promise<{ campaignId: string }>;
}

const DAY_LABEL = (iso: string) =>
  new Date(iso + "T00:00:00Z").toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });

export default async function CampaignCalendar({ params }: PageProps) {
  const { campaignId } = await params;
  const campaign = getCampaign(campaignId);
  if (!campaign) notFound();
  const brand = getBrand(campaign.brandId);
  const items = listContentForCampaign(campaign.id);

  return (
    <div className="space-y-5 max-w-5xl">
      <div>
        <Link
          href={`/agency/brands/${campaign.brandId}`}
          className="text-sm text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
        >
          ← {brand?.name}
        </Link>
        <h1 className="mt-1 text-2xl font-semibold">{campaign.title}</h1>
        <div className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
          Content calendar · pattern{" "}
          <code className="font-mono">{campaign.strategicPattern}</code>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Scheduled items</CardTitle>
        </CardHeader>
        <CardBody>
          {items.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              No scheduled items yet.
            </p>
          ) : (
            <ul className="divide-y divide-[color:var(--color-hairline)]">
              {items.map((c) => (
                <li
                  key={c.id}
                  className="py-4 flex items-center justify-between gap-3"
                >
                  <div className="min-w-0">
                    <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                      {DAY_LABEL(c.scheduledFor)}
                    </div>
                    <div className="font-semibold mt-0.5">{c.title}</div>
                    <div className="text-xs text-[color:var(--color-ink-muted)] mt-0.5 truncate">
                      {c.platforms.join(" · ")} · {c.durationSec}s · {c.resolution}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <OperatorStatusChip status={c.status} />
                    <Link
                      href={`/agency/campaigns/${campaign.id}/outputs#${c.id}`}
                      className="text-sm text-[color:var(--color-accent)] underline"
                    >
                      Open →
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
