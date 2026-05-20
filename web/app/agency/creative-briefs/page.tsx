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

type FilterKey =
  | "all"
  | "needs_brief"
  | "drafted"
  | "approved_internal"
  | "ready_for_export";

const FILTERS: Array<{ key: FilterKey; label: string }> = [
  { key: "all", label: "All" },
  { key: "needs_brief", label: "Needs brief" },
  { key: "drafted", label: "Drafted" },
  { key: "approved_internal", label: "Approved internal" },
  { key: "ready_for_export", label: "Ready for export" },
];

interface PageProps {
  searchParams: Promise<{ filter?: string }>;
}

export default async function CreativeBriefsPage({ searchParams }: PageProps) {
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
  const sp = await searchParams;
  const requested = (sp.filter ?? "all") as string;
  const activeFilter: FilterKey = FILTERS.some((f) => f.key === requested)
    ? (requested as FilterKey)
    : "all";

  // Counts per filter (computed in-memory from the already-loaded
  // queue — no extra DB call).
  const counts = {
    all: items.length,
    needs_brief: items.filter((i) => i.creativeBriefStatus === "none").length,
    drafted: items.filter(
      (i) =>
        i.creativeBriefStatus === "drafted" &&
        i.creativeBriefApprovalStatus !== "approved_internal",
    ).length,
    approved_internal: items.filter(
      (i) => i.creativeBriefApprovalStatus === "approved_internal",
    ).length,
    ready_for_export: items.filter((i) => i.nextAction === "ready_for_export")
      .length,
  };

  const filtered = items.filter((i) => {
    switch (activeFilter) {
      case "all":
        return true;
      case "needs_brief":
        return i.creativeBriefStatus === "none";
      case "drafted":
        return (
          i.creativeBriefStatus === "drafted" &&
          i.creativeBriefApprovalStatus !== "approved_internal"
        );
      case "approved_internal":
        return i.creativeBriefApprovalStatus === "approved_internal";
      case "ready_for_export":
        return i.nextAction === "ready_for_export";
    }
  });

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

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
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
        <Stat
          label="Approved internal"
          value={counts.approved_internal}
          tone={counts.approved_internal > 0 ? "success" : "neutral"}
        />
        <Stat
          label="Ready for export"
          value={counts.ready_for_export}
          tone={counts.ready_for_export > 0 ? "success" : "neutral"}
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => {
          const isActive = f.key === activeFilter;
          const href = f.key === "all" ? "/agency/creative-briefs" : `/agency/creative-briefs?filter=${f.key}`;
          const count = counts[f.key];
          return (
            <Link
              key={f.key}
              href={href}
              className={
                isActive
                  ? "text-xs px-2.5 py-1 rounded-full bg-[color:var(--color-accent)]/15 text-[color:var(--color-accent)] font-semibold"
                  : "text-xs px-2.5 py-1 rounded-full bg-[color:var(--color-hairline)]/60 text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
              }
            >
              {f.label} <span className="opacity-60">({count})</span>
            </Link>
          );
        })}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            Queue{" "}
            <span className="ml-2 text-xs font-normal text-[color:var(--color-ink-faint)]">
              ({filtered.length} of {items.length})
            </span>
          </CardTitle>
        </CardHeader>
        <CardBody>
          {filtered.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              {items.length === 0
                ? "No items in this workspace need a creative brief yet. Use the Calendar Agent to plan multi-format content first."
                : "No items match this filter. Try a different one above."}
            </p>
          ) : (
            <ul className="divide-y divide-[color:var(--color-hairline)]">
              {filtered.map((it) => (
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
          {item.creativeBriefTemplateId && (
            <Badge tone="neutral">tpl: {item.creativeBriefTemplateId}</Badge>
          )}
          {item.creativeBriefApprovalStatus === "approved_internal" && (
            <Badge tone="success">approved internal</Badge>
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
            {item.creativeBriefApprovedAt && (
              <>
                {" "}· approved internally{" "}
                {new Date(item.creativeBriefApprovedAt).toLocaleString("en-GB")}
              </>
            )}
          </div>
        )}
        {item.creativeBriefStatus === "none" && (
          <div className="mt-1 text-xs text-[color:var(--color-ink-faint)] italic">
            Needs a creative brief before visuals can be previewed.
          </div>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <CreativeBriefPanel
            contentItemId={item.contentItemId}
            currentStatus={item.creativeBriefStatus}
          />
          {item.creativeBriefStatus === "drafted" && (
            <Link
              href={`/agency/creative-briefs/${item.contentItemId}/preview`}
              className="text-xs px-2.5 py-1.5 rounded-md border border-[color:var(--color-accent)]/40 bg-[color:var(--color-accent)]/8 text-[color:var(--color-accent)] hover:bg-[color:var(--color-accent)]/15 font-semibold"
            >
              Preview visuals →
            </Link>
          )}
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
