// Yuvo Studio — Phase 1W Brand Analysis + UGC Prompt Planning agent.
//
// OPERATOR-ONLY route. Renders the agent form + preview-only result
// viewer. No DB write, no paid call, no website fetch — see
// web/lib/agents/brand-analysis.ts.

import Link from "next/link";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { BrandAnalysisForm } from "@/components/agents/brand-analysis-form";
import { DEMO_BRANDS } from "@/lib/demo-data";
import { getCurrentPersona } from "@/lib/auth/persona";
import { getDataSource, getDefaultWorkspaceId } from "@/lib/data/_source";
import {
  listCampaignsForWorkspace,
  listContentItemsForWorkspace,
} from "@/lib/data/owner-overview";
import {
  listAgentRunsForWorkspace,
  extractMatchedNiche,
  extractProductUrl,
} from "@/lib/data/agent-runs";

export default async function BrandAnalysisAgentPage() {
  // Demo mode lists every seeded brand; supabase mode would resolve via
  // workspace_members → brands. For Phase 1W we use the demo set because
  // the form's brand selector is just UX sugar; the planner only needs
  // a URL.
  let workspaceId = getDefaultWorkspaceId();
  if (getDataSource() === "supabase") {
    const persona = await getCurrentPersona();
    if (persona?.kind === "operator" && persona.workspaceIds.length > 0) {
      workspaceId = persona.workspaceIds[0];
    }
  }
  const brandOptions = DEMO_BRANDS.map((b) => ({
    id: b.id,
    name: b.name,
    niche: b.niche,
    tone: b.brandTone,
    audience: b.audienceAssumption,
    websiteUrl: b.websiteUrl,
  }));

  // Phase 1X — workspace-wide content items, grouped (brand → campaign →
  // item) so the operator can pick a target for the new draft prompt.
  // Phase 1Y — recent agent_runs for the same workspace. Fails-soft to
  // [] if migration 008 isn't applied / RLS denies, so this page keeps
  // rendering either way.
  const [campaigns, contentItems, recentRuns] = await Promise.all([
    listCampaignsForWorkspace(workspaceId),
    listContentItemsForWorkspace(workspaceId),
    listAgentRunsForWorkspace(workspaceId, {
      agentType: "brand_analysis_ugc_prompt_planning",
      limit: 10,
    }),
  ]);
  const campaignById = new Map(campaigns.map((c) => [c.id, c]));
  const brandNameById = new Map(DEMO_BRANDS.map((b) => [b.id, b.name]));
  const targetOptions = contentItems
    .map((ci) => {
      const camp = campaignById.get(ci.campaignId);
      const brandId = camp?.brandId ?? "";
      return {
        contentItemId: ci.id,
        contentTitle: ci.title,
        campaignId: camp?.id ?? "",
        campaignTitle: camp?.title ?? "(unknown campaign)",
        brandId,
        brandName: brandNameById.get(brandId) ?? "(unknown brand)",
      };
    })
    .sort((a, b) => {
      if (a.brandName !== b.brandName) return a.brandName.localeCompare(b.brandName);
      if (a.campaignTitle !== b.campaignTitle)
        return a.campaignTitle.localeCompare(b.campaignTitle);
      return a.contentTitle.localeCompare(b.contentTitle);
    });

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/agency"
          className="text-sm text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
        >
          ← Owner command center
        </Link>
        <div className="mt-1 flex items-center gap-2 flex-wrap">
          <h1 className="text-2xl font-semibold">
            Brand Analysis &amp; UGC Prompt Planning
          </h1>
          <Badge tone="success">Available</Badge>
        </div>
        <p className="mt-1 text-sm text-[color:var(--color-ink-muted)] max-w-3xl">
          Paste a product URL. Get a structured planning draft you can
          paste into the prompt editor. <strong>No website fetch.</strong>{" "}
          <strong>No LLM call.</strong> <strong>No paid call.</strong>{" "}
          Every section is a hypothesis — verify against the real source
          before any client share.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>New planning run</CardTitle>
        </CardHeader>
        <CardBody>
          <BrandAnalysisForm brands={brandOptions} targets={targetOptions} />
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Recent runs</CardTitle>
        </CardHeader>
        <CardBody>
          {recentRuns.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              No agent runs yet. The next time you click{" "}
              <em>Analyze product and create UGC plan</em> above, a row
              lands in <code className="font-mono">agent_runs</code> and
              shows up here.
            </p>
          ) : (
            <ul className="divide-y divide-[color:var(--color-hairline)]">
              {recentRuns.map((r) => {
                const tone =
                  r.status === "completed"
                    ? "success"
                    : r.status === "failed"
                      ? "danger"
                      : r.status === "running"
                        ? "warn"
                        : "neutral";
                const url = extractProductUrl(r.input);
                const niche = extractMatchedNiche(r.output);
                return (
                  <li key={r.id} className="py-3 text-sm space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={tone}>{r.status}</Badge>
                      <Badge tone="info">{niche.replaceAll("_", " ")}</Badge>
                      <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                        {new Date(r.createdAt).toLocaleString("en-GB")}
                      </span>
                      <code className="text-[10px] font-mono text-[color:var(--color-ink-muted)] break-all">
                        {r.id.slice(0, 8)}…
                      </code>
                    </div>
                    <div className="text-xs break-all text-[color:var(--color-ink-muted)]">
                      <span className="text-[color:var(--color-ink-faint)]">
                        product:{" "}
                      </span>
                      <span className="font-mono">{url}</span>
                    </div>
                    {r.errorMessage && (
                      <div className="text-xs text-[color:var(--color-danger)] break-all">
                        error: {r.errorMessage}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          <p className="mt-3 text-[10px] text-[color:var(--color-ink-faint)] italic">
            Runs are persisted to{" "}
            <code className="font-mono">agent_runs</code>. The same
            input deterministically produces the same output — re-runs
            are safe and free.
          </p>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>How this agent is safe</CardTitle>
        </CardHeader>
        <CardBody className="text-sm space-y-2">
          <ul className="list-disc list-inside text-[color:var(--color-ink-muted)] space-y-1">
            <li>
              <strong>No fetch.</strong> The product URL is{" "}
              <em>parsed</em> locally to pick a niche template; the page is
              never requested.
            </li>
            <li>
              <strong>No LLM call.</strong> Output comes from a small,
              auditable template bank in{" "}
              <code className="font-mono">web/lib/agents/brand-analysis.ts</code>.
            </li>
            <li>
              <strong>No paid API.</strong> Seedance / Enhancor / Audio
              Fixer are not touched.
            </li>
            <li>
              <strong>No DB write.</strong> Persistence waits for
              migration 008. The dashboard does not break without it.
            </li>
            <li>
              <strong>Hypothesis-labelled.</strong> Every claim is
              labelled either &ldquo;Hypothesis&rdquo; or &ldquo;Needs
              operator verification&rdquo;. Nothing here is factual about
              a specific brand.
            </li>
            <li>
              <strong>Client portal stays clean.</strong> Nothing this
              agent produces is exposed to{" "}
              <code className="font-mono">/client/[portalSlug]</code>.
            </li>
          </ul>
        </CardBody>
      </Card>
    </div>
  );
}
