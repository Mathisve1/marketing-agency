// Yuvo Studio — Phase 4A Social Creative Brief queue.
//
// OPERATOR-ONLY. Lists content items that benefit from a structured
// visual planning brief (carousel / story / feed_post / static_image /
// linkedin text / reel-support / video-support). The page itself is
// read-only; the per-item panel calls the deterministic Social
// Creative Brief Agent action.
//
// HARD RULES: no video generation, no image generation, no publish, no
// client share, no email, no paid call. The brief lives in
// prompt_summary, which the client portal view does not project.

import Link from "next/link";
import { redirect } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { getCurrentPersona } from "@/lib/auth/persona";
import { getDataSource, getDefaultWorkspaceId } from "@/lib/data/_source";
import { listCreativeBriefQueueForWorkspace } from "@/lib/data/owner-overview";
import { CreativeBriefPanel } from "@/components/agents/creative-brief-panel";

export const dynamic = "force-dynamic";

export default async function CreativeBriefsPage() {
  // Workspace resolution mirrors the rest of /agency/*.
  let workspaceId = getDefaultWorkspaceId();
  if (getDataSource() === "supabase") {
    const persona = await getCurrentPersona();
    if (!persona) redirect("/login?next=/agency/creative-briefs");
    if (persona.kind !== "operator") {
      redirect("/login?next=/agency/creative-briefs");
    }
    workspaceId = persona.workspaceIds[0] ?? getDefaultWorkspaceId();
  }

  const { items, summary } = await listCreativeBriefQueueForWorkspace(workspaceId);

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h1 className="text-2xl font-semibold">Creative briefs</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
          Structured planning briefs for social formats (carousel, story,
          feed post, static image, LinkedIn text, plus visual-support briefs
          for reels and UGC video). Internal only.
        </p>
        <p className="mt-2 text-xs text-[color:var(--color-ink-faint)] italic max-w-3xl">
          This page creates planning briefs only. It does NOT generate
          images, run Seedance/Enhancor/Audio Fixer, publish posts, send
          emails, or share anything with the client. The brief lives in
          <code className="font-mono px-1">content_items.prompt_summary</code>{" "}
          and is structurally invisible to the client portal.
        </p>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <Stat label="Items in queue" value={summary.total} />
        <Stat
          label="Need brief"
          value={summary.none}
          tone={summary.none > 0 ? "info" : "neutral"}
        />
        <Stat
          label="Drafted"
          value={summary.drafted}
          tone={summary.drafted > 0 ? "success" : "neutral"}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            Queue{" "}
            <span className="ml-2 text-xs font-normal text-[color:var(--color-ink-faint)]">
              ({items.length})
            </span>
          </CardTitle>
        </CardHeader>
        <CardBody>
          {items.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              No items in this workspace need a creative brief yet. Use the
              Calendar Agent to plan multi-format content first.
            </p>
          ) : (
            <ul className="divide-y divide-[color:var(--color-hairline)]">
              {items.map((it) => (
                <BriefRow key={it.contentItemId} item={it} />
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function BriefRow({
  item,
}: {
  item: Awaited<
    ReturnType<typeof listCreativeBriefQueueForWorkspace>
  >["items"][number];
}) {
  const statusTone: "info" | "success" | "neutral" =
    item.creativeBriefStatus === "drafted" ? "success" : "info";
  const statusLabel =
    item.creativeBriefStatus === "drafted" ? "drafted" : "needs brief";
  const channelLabel = item.channel ?? "—";
  const formatLabel = item.format ?? "—";
  return (
    <li className="py-4 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={statusTone}>{statusLabel}</Badge>
          <Badge tone="neutral">{formatLabel}</Badge>
          <Badge tone="neutral">{channelLabel}</Badge>
          {item.creativeBriefMode && (
            <Badge tone="neutral">mode: {item.creativeBriefMode}</Badge>
          )}
          <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
            {new Date(item.scheduledFor).toLocaleDateString("en-GB")}
          </span>
        </div>
        <div className="mt-1 font-semibold leading-snug">{item.title}</div>
        <div className="text-xs text-[color:var(--color-ink-muted)] leading-snug">
          {item.brandName ?? "—"} · {item.campaignName ?? "—"} · content
          status: {item.contentStatus}
        </div>
        {item.captionPreview && (
          <div className="mt-1 text-xs text-[color:var(--color-ink-muted)] italic">
            “{item.captionPreview}{item.captionPreview.length >= 140 ? "…" : ""}”
          </div>
        )}
        {item.creativeBriefCreatedAt && (
          <div className="mt-1 text-[10px] text-[color:var(--color-ink-faint)] font-mono">
            brief drafted {new Date(item.creativeBriefCreatedAt).toLocaleString("en-GB")}
          </div>
        )}
        <div className="mt-3">
          <CreativeBriefPanel
            contentItemId={item.contentItemId}
            currentStatus={item.creativeBriefStatus}
          />
        </div>
      </div>
      <Link
        href={`/agency/campaigns/${item.campaignId}/calendar`}
        className="text-xs px-2.5 py-1.5 rounded-md border border-[color:var(--color-hairline)] hover:bg-[color:var(--color-hairline)] self-start"
      >
        View in calendar →
      </Link>
    </li>
  );
}

function Stat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number | string;
  tone?: "neutral" | "info" | "success";
}) {
  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
        {label}
      </div>
      <div
        className={
          tone === "info"
            ? "text-xl font-semibold text-[color:var(--color-accent)]"
            : tone === "success"
              ? "text-xl font-semibold text-[color:var(--color-success)]"
              : "text-xl font-semibold"
        }
      >
        {value}
      </div>
    </div>
  );
}
