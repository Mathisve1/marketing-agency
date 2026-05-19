// Operator-side content card. Shows internal prompt summary, cost, and
// internal asset paths. NEVER used inside /client/[portalSlug].

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { OperatorStatusChip } from "./status-chip";
import { VideoCard } from "./video-card";
import { formatCredits } from "@/lib/quality-tiers";
import type { ContentItem } from "@/lib/types";

interface Props {
  content: ContentItem;
  campaignId: string;
}

const PLATFORM_LABEL = {
  instagram_reels: "Instagram Reels",
  tiktok: "TikTok",
  meta_ads: "Meta Ads",
  youtube_shorts: "YouTube Shorts",
} as const;

export function ContentCard({ content, campaignId }: Props) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle>{content.title}</CardTitle>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {content.platforms.map((p) => (
                <Badge key={p} tone="neutral">
                  {PLATFORM_LABEL[p]}
                </Badge>
              ))}
            </div>
          </div>
          <OperatorStatusChip status={content.status} />
        </div>
      </CardHeader>
      <CardBody>
        <div className="grid sm:grid-cols-[280px_1fr] gap-5">
          <VideoCard
            posterUrl={content.clientSafePosterUrl}
            title={content.title}
            durationSec={content.durationSec}
            audience="operator"
            internalRawPath={content.internalAssetPaths.rawMp4}
            internalAudioFixedPath={content.internalAssetPaths.audioFixedMp4}
          />
          <div className="space-y-3 text-sm">
            <div>
              <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                Hook
              </div>
              <div className="mt-1">{content.hook.text}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                Caption draft
              </div>
              <div className="mt-1 text-[color:var(--color-ink-muted)] leading-relaxed">
                {content.captionDraft}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                Prompt summary
              </div>
              <div className="mt-1 text-[color:var(--color-ink-muted)] leading-relaxed">
                {content.promptSummary}
              </div>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2">
              <Meta label="Tier" value={content.qualityTier.replace("_", " ")} />
              <Meta
                label="Resolution"
                value={`${content.resolution} · ${content.durationSec}s`}
              />
              <Meta
                label="Estimate"
                value={`${formatCredits(content.costEstimateCredits)} cr`}
              />
              <Meta
                label="Audio Fixer"
                value={
                  content.audioFixer.completed
                    ? `done · ${formatCredits(content.audioFixer.creditsActual ?? 0)} cr`
                    : "manual / not run"
                }
              />
            </div>
            <div className="pt-2 flex flex-wrap gap-4">
              <Link
                href={`/agency/campaigns/${campaignId}/outputs#${content.id}`}
                className="text-[color:var(--color-accent)] text-sm underline"
              >
                Open output detail
              </Link>
              <Link
                href={`/agency/campaigns/${campaignId}/content/${content.id}/prompt`}
                className="text-[color:var(--color-accent)] text-sm underline"
              >
                Prompt editor →
              </Link>
            </div>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
        {label}
      </div>
      <div className="mt-0.5 text-sm tabular-nums">{value}</div>
    </div>
  );
}
