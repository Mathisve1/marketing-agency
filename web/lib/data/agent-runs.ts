// Yuvo Studio — Phase 1Y agent_runs readers.
//
// OPERATOR-ONLY. Never imported from /client/*. Schema lives in
// supabase/migrations/008_agent_runs.sql, which is operator-RLS only
// (every policy goes through app.is_workspace_member). The client portal
// has no SELECT policy on this table.
//
// Fail-soft pattern: if PostgREST returns "relation does not exist"
// (PGRST205 / 42P01), every read returns [] / null and every write path
// surfaces a clear "apply migration 008" warning instead of throwing.
// This lets the rest of the dashboard keep working even if the migration
// is rolled back.

import { getDataSource, SupabaseDataError } from "./_source";
import { getSupabaseServerClient } from "@/lib/supabase/client";

export type AgentRunStatus = "draft" | "running" | "completed" | "failed";

export type AgentType = "brand_analysis_ugc_prompt_planning";

export interface AgentRun {
  id: string;
  workspaceId: string;
  brandId: string | null;
  campaignId: string | null;
  contentItemId: string | null;
  agentType: AgentType;
  status: AgentRunStatus;
  input: unknown;
  output: unknown;
  errorMessage: string | null;
  createdBy: string | null;
  createdAt: string;
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// Demo-mode store. In Phase 1Y the agent runs entirely in the browser
// for demo mode (no Supabase round-trip), so the store mirrors that:
// rows are appended in-memory and survive only the dev process. The
// /agency/agents/brand-analysis page reads it back to surface "recent
// runs" without needing Supabase.
// ---------------------------------------------------------------------------
const DEMO_AGENT_RUNS: AgentRun[] = [];

export function _demoPushAgentRun(row: AgentRun): void {
  DEMO_AGENT_RUNS.unshift(row);
  if (DEMO_AGENT_RUNS.length > 200) DEMO_AGENT_RUNS.length = 200;
}

export function _demoListAgentRuns(): AgentRun[] {
  return [...DEMO_AGENT_RUNS];
}

// ---------------------------------------------------------------------------
// Row → view mapper
// ---------------------------------------------------------------------------
const SELECT_COLS =
  "id, workspace_id, brand_id, campaign_id, content_item_id, " +
  "agent_type, status, input, output, error_message, created_by, " +
  "created_at, updated_at";

function rowToView(r: {
  id: string;
  workspace_id: string;
  brand_id: string | null;
  campaign_id: string | null;
  content_item_id: string | null;
  agent_type: AgentType;
  status: AgentRunStatus;
  input: unknown;
  output: unknown;
  error_message: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}): AgentRun {
  return {
    id: r.id,
    workspaceId: r.workspace_id,
    brandId: r.brand_id,
    campaignId: r.campaign_id,
    contentItemId: r.content_item_id,
    agentType: r.agent_type,
    status: r.status,
    input: r.input,
    output: r.output,
    errorMessage: r.error_message,
    createdBy: r.created_by,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

/** Soft "does this look like the agent_runs table doesn't exist?" check.
 *  PostgREST surfaces missing-relation errors as code 42P01 or PGRST205. */
function isMissingRelationError(err: unknown): boolean {
  const e = err as { code?: string; message?: string } | undefined;
  if (!e) return false;
  if (e.code === "42P01" || e.code === "PGRST205") return true;
  return Boolean(e.message && /agent_runs/i.test(e.message) && /does not exist|not found/i.test(e.message));
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

/** Every agent run in the workspace, newest first. Phase 1Y. */
export async function listAgentRunsForWorkspace(
  workspaceId: string,
  options: { limit?: number; agentType?: AgentType } = {},
): Promise<AgentRun[]> {
  const limit = options.limit ?? 20;
  if (getDataSource() === "demo") {
    void workspaceId;
    let rows = _demoListAgentRuns();
    if (options.agentType) {
      rows = rows.filter((r) => r.agentType === options.agentType);
    }
    return rows.slice(0, limit);
  }
  const supabase = getSupabaseServerClient();
  let q = supabase
    .from("agent_runs")
    .select(SELECT_COLS)
    .eq("workspace_id", workspaceId)
    .order("created_at", { ascending: false })
    .limit(limit);
  if (options.agentType) q = q.eq("agent_type", options.agentType);
  const { data, error } = await q;
  if (error) {
    if (isMissingRelationError(error)) return [];
    throw new SupabaseDataError("listAgentRunsForWorkspace", error);
  }
  if (!data) return [];
  return (data as unknown as Parameters<typeof rowToView>[0][]).map(rowToView);
}

export async function getAgentRun(id: string): Promise<AgentRun | null> {
  if (getDataSource() === "demo") {
    return _demoListAgentRuns().find((r) => r.id === id) ?? null;
  }
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("agent_runs")
    .select(SELECT_COLS)
    .eq("id", id)
    .maybeSingle();
  if (error) {
    if (isMissingRelationError(error)) return null;
    throw new SupabaseDataError("getAgentRun", error);
  }
  if (!data) return null;
  return rowToView(data as unknown as Parameters<typeof rowToView>[0]);
}

// ---------------------------------------------------------------------------
// Light helpers re-used by both the action layer and the UI.
// ---------------------------------------------------------------------------

/** Detect the typical "matched niche" string from a stored output without
 *  fully type-asserting the jsonb shape. Returns "(unknown)" on any miss. */
export function extractMatchedNiche(output: unknown): string {
  if (!output || typeof output !== "object") return "(unknown)";
  const v = (output as Record<string, unknown>).matchedNiche;
  return typeof v === "string" ? v : "(unknown)";
}

/** Pluck the product URL out of a stored input jsonb. */
export function extractProductUrl(input: unknown): string {
  if (!input || typeof input !== "object") return "(unknown)";
  const v = (input as Record<string, unknown>).productUrl;
  return typeof v === "string" ? v : "(unknown)";
}
