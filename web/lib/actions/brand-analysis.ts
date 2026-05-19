// Yuvo Studio — Phase 1W/1Y Brand Analysis + UGC Prompt Planning action.
//
// OPERATOR-ONLY. This action calls the deterministic planner and
// returns the structured plan to the caller. It NEVER:
//   - calls Seedance / Enhancor / Audio Fixer / any paid API
//   - issues an HTTP fetch against the supplied product URL
//
// Phase 1Y addition: when migration 008 is applied, the action ALSO
// persists one `agent_runs` row per invocation. If the table isn't
// reachable (rollback, RLS denial, etc.) the action degrades to
// preview-only and surfaces a warning — the plan is still returned so
// the UI keeps working.

"use server";

import { randomUUID } from "crypto";
import { getDataSource, getDefaultWorkspaceId } from "@/lib/data/_source";
import {
  getServiceRoleSupabase,
  hasSupabaseEnv,
} from "@/lib/supabase/server";
import { getCurrentPersona } from "@/lib/auth/persona";
import {
  planBrandAnalysisUGCPrompt,
  validateBrandAnalysisInput,
  type BrandAnalysisInput,
  type BrandAnalysisPlan,
} from "@/lib/agents/brand-analysis";
import {
  _demoPushAgentRun,
  type AgentRun,
} from "@/lib/data/agent-runs";

export interface BrandAnalysisActionResult {
  ok: boolean;
  plan?: BrandAnalysisPlan;
  /** Phase 1Y — id of the persisted agent_runs row, when persistence
   *  succeeded. Threaded into the prompt-version handoff so the prompt
   *  notes can reference the source run. */
  agentRunId?: string;
  /** Soft warning surfaced to the UI when persistence failed but the
   *  plan was produced successfully. Never blocks the result. */
  persistenceWarning?: string;
  error?: string;
}

async function requireOperator(): Promise<
  { profileId: string | null } | { error: string }
> {
  if (getDataSource() === "demo") return { profileId: null };
  if (!hasSupabaseEnv()) return { error: "Supabase auth is not configured." };
  const persona = await getCurrentPersona();
  if (!persona) return { error: "Please sign in first." };
  if (persona.kind !== "operator") {
    return { error: "Operator access required." };
  }
  return { profileId: persona.userId };
}

function resolveWorkspaceId(input: BrandAnalysisInput): string {
  // Phase 1Y: the form does not pass workspace_id explicitly — the page
  // resolves it from the persona / default. We re-resolve here so the
  // persisted row has the right tenancy even when called from a server
  // context (e.g. background task in the future).
  void input;
  return getDefaultWorkspaceId();
}

export async function runBrandAnalysisAgentAction(
  input: BrandAnalysisInput,
): Promise<BrandAnalysisActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };
  const operatorProfileId = "profileId" in auth ? auth.profileId : null;

  const v = validateBrandAnalysisInput(input);
  if (!v.ok) return { ok: false, error: v.error };

  // Normalised input — also persisted verbatim into agent_runs.input.
  const normInput: BrandAnalysisInput = {
    productUrl: input.productUrl.trim(),
    brandName: input.brandName?.trim() || undefined,
    brandNiche: input.brandNiche?.trim() || undefined,
    brandTone: input.brandTone?.trim() || undefined,
    audienceAssumption: input.audienceAssumption?.trim() || undefined,
    operatorNotes: input.operatorNotes?.trim() || undefined,
  };

  // The planner is pure + synchronous. Wrap so we can still log a
  // failed agent_run when something regresses.
  let plan: BrandAnalysisPlan;
  try {
    plan = planBrandAnalysisUGCPrompt(normInput);
  } catch (err) {
    const message = (err as Error).message;
    await persistAgentRun({
      input: normInput,
      output: null,
      status: "failed",
      errorMessage: message,
      operatorProfileId,
    }).catch(() => {
      /* persistence errors are silent on the failure path */
    });
    return { ok: false, error: message };
  }

  // Persist a completed run. Failure here NEVER masks the plan.
  const persistResult = await persistAgentRun({
    input: normInput,
    output: plan,
    status: "completed",
    errorMessage: null,
    operatorProfileId,
  });

  return {
    ok: true,
    plan,
    agentRunId: persistResult.agentRunId,
    persistenceWarning: persistResult.warning,
  };
}

// --------------------------------------------------------------------------- #
// Persistence helper

interface PersistInput {
  input: BrandAnalysisInput;
  output: BrandAnalysisPlan | null;
  status: "completed" | "failed";
  errorMessage: string | null;
  operatorProfileId: string | null;
}

async function persistAgentRun(
  p: PersistInput,
): Promise<{ agentRunId?: string; warning?: string }> {
  const workspaceId = resolveWorkspaceId(p.input);

  if (getDataSource() === "demo") {
    const now = new Date().toISOString();
    const id = randomUUID();
    const row: AgentRun = {
      id,
      workspaceId,
      brandId: null,
      campaignId: null,
      contentItemId: null,
      agentType: "brand_analysis_ugc_prompt_planning",
      status: p.status,
      input: p.input,
      output: p.output,
      errorMessage: p.errorMessage,
      createdBy: p.operatorProfileId,
      createdAt: now,
      updatedAt: now,
    };
    _demoPushAgentRun(row);
    return { agentRunId: id };
  }

  try {
    const admin = getServiceRoleSupabase();
    const { data, error } = await admin
      .from("agent_runs")
      .insert({
        workspace_id: workspaceId,
        agent_type: "brand_analysis_ugc_prompt_planning",
        status: p.status,
        input: p.input,
        output: p.output,
        error_message: p.errorMessage,
        created_by: p.operatorProfileId,
      })
      .select("id")
      .maybeSingle();
    if (error) {
      // Fail-soft: agent still works as preview-only.
      const msg = error.message ?? "agent_runs insert failed";
      const looksMissing = /agent_runs.*does not exist|relation .*agent_runs.*does not exist|PGRST205|42P01/i.test(
        msg,
      );
      return {
        warning: looksMissing
          ? "agent_runs persistence skipped — apply migration 008."
          : `agent_runs persistence skipped — ${msg}`,
      };
    }
    const id = (data as { id?: string } | null)?.id;
    return { agentRunId: id };
  } catch (err) {
    return {
      warning:
        "agent_runs persistence skipped — " +
        ((err as Error).message ?? "unknown error"),
    };
  }
}
