// Yuvo Studio — Phase 1Z Calendar Agent action.
//
// OPERATOR-ONLY. Turns a completed Brand Analysis agent run into N draft
// content_items rows (one per selected calendar idea). The action NEVER:
//   - flips a content_items.status away from 'draft'
//   - sets shared_with_client = true
//   - writes any client_safe_* / internal_* / cost_* field
//   - creates a generation_jobs / generated_assets / audio_fixer_jobs row
//   - creates or modifies a prompt_versions row
//   - calls Seedance / Enhancor / Audio Fixer / any paid API
//   - sends email / posts to any external service
//
// Idempotency is NOT guaranteed by the database (content_items has no
// unique key on `(campaign_id, scheduled_for, title)`), but the action
// surfaces the inserted ids so a re-click can be diffed by the operator.

"use server";

import { revalidatePath } from "next/cache";
import { getDataSource } from "@/lib/data/_source";
import {
  getServiceRoleSupabase,
  hasSupabaseEnv,
} from "@/lib/supabase/server";
import { getCurrentPersona } from "@/lib/auth/persona";

export interface CalendarAgentInput {
  agentRunId: string;
  campaignId: string;
  /** Day-offsets (D+N) the operator wants to materialise as draft
   *  content_items. If empty, no rows are created. */
  selectedDayOffsets: number[];
  /** ISO yyyy-MM-dd; defaults to today (UTC). Each idea's
   *  scheduled_for = startDate + idea.dayOffset days. */
  startDate?: string;
  /** Optional one-line note appended to every created row's
   *  prompt_summary. */
  operatorNotes?: string;
}

export interface CalendarAgentResult {
  ok: boolean;
  error?: string;
  message?: string;
  /** New content_items ids in the same order as the selected day offsets. */
  contentItemIds?: string[];
  /** The campaign URL the operator can navigate to. */
  calendarHref?: string;
}

// Phase 2D — calendar ideas now carry multi-format metadata. We stay
// tolerant of older runs (pre-2D) that only had {dayOffset,label,brief}
// by normalising on read: title falls back to label, and the format
// fields default to a non-video copy item so nothing is pushed toward
// Seedance by accident.
interface AgentCalendarIdea {
  dayOffset: number;
  title: string;
  brief: string;
  suggestedChannel: string;
  suggestedFormat: string;
  distributionType: string;
  contentGoal: string;
  recommendedAssetType: string;
  needsGeneration: boolean;
  needsPromptVersion: boolean;
  operatorNotes: string;
}

function normalizeIdea(raw: Record<string, unknown>): AgentCalendarIdea | null {
  const dayOffset = raw.dayOffset;
  const brief = raw.brief;
  const title =
    typeof raw.title === "string"
      ? raw.title
      : typeof raw.label === "string"
        ? raw.label
        : null;
  if (
    typeof dayOffset !== "number" ||
    typeof brief !== "string" ||
    title === null
  ) {
    return null;
  }
  const str = (v: unknown, d: string) =>
    typeof v === "string" && v.length > 0 ? v : d;
  return {
    dayOffset,
    title,
    brief,
    // Defaults are deliberately the SAFEST option (non-video copy item)
    // so a legacy run can never imply a paid generation.
    suggestedChannel: str(raw.suggestedChannel, "other"),
    suggestedFormat: str(raw.suggestedFormat, "text_post"),
    distributionType: str(raw.distributionType, "client_review_only"),
    contentGoal: str(raw.contentGoal, "awareness"),
    recommendedAssetType: str(raw.recommendedAssetType, "copy_only"),
    needsGeneration: raw.needsGeneration === true,
    needsPromptVersion: raw.needsPromptVersion === true,
    operatorNotes: str(raw.operatorNotes, ""),
  };
}

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function requireOperator(): Promise<true | { error: string }> {
  if (getDataSource() === "demo") return true;
  if (!hasSupabaseEnv()) return { error: "Supabase auth is not configured." };
  const persona = await getCurrentPersona();
  if (!persona) return { error: "Please sign in first." };
  if (persona.kind !== "operator") {
    return { error: "Operator access required." };
  }
  return true;
}

function todayUtcIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Add N days to a yyyy-MM-dd date and return yyyy-MM-dd. UTC-safe. */
function addDaysIso(startIso: string, days: number): string {
  const t = Date.UTC(
    Number(startIso.slice(0, 4)),
    Number(startIso.slice(5, 7)) - 1,
    Number(startIso.slice(8, 10)),
  );
  const shifted = new Date(t + days * 86_400_000);
  return shifted.toISOString().slice(0, 10);
}

function buildProvenanceSummary(args: {
  agentRunId: string;
  matchedNiche: string | null;
  productUrl: string | null;
  dayOffset: number;
  brief: string;
  idea: AgentCalendarIdea;
  operatorNotes?: string;
}): string {
  const i = args.idea;
  const lines = [
    "[agent draft] source: brand_analysis_ugc_prompt_planning",
    `agent_run_id: ${args.agentRunId}`,
    `source_agent_run_id: ${args.agentRunId}`,
    args.matchedNiche ? `matched_niche: ${args.matchedNiche}` : null,
    args.productUrl ? `product_url: ${args.productUrl}` : null,
    `calendar_day_offset: ${args.dayOffset}`,
    // Phase 2D multi-format metadata (no schema column yet).
    `channel: ${i.suggestedChannel}`,
    `format: ${i.suggestedFormat}`,
    `distribution_type: ${i.distributionType}`,
    `content_goal: ${i.contentGoal}`,
    `recommended_asset_type: ${i.recommendedAssetType}`,
    `needs_generation: ${i.needsGeneration}`,
    `needs_prompt_version: ${i.needsPromptVersion}`,
    "status: draft — created from a deterministic agent run.",
    "operator must verify, schedule, and approve before any client share.",
    i.operatorNotes ? `\n[agent note] ${i.operatorNotes}` : null,
    "",
    args.brief,
    args.operatorNotes ? `\n[operator note] ${args.operatorNotes}` : null,
  ].filter((s): s is string => s !== null);
  return lines.join("\n");
}

export async function createDraftContentCalendarFromAgentRunAction(
  input: CalendarAgentInput,
): Promise<CalendarAgentResult> {
  const auth = await requireOperator();
  if (auth !== true) return { ok: false, error: auth.error };

  // ---- Input validation ------------------------------------------------
  if (!UUID_RE.test(input.agentRunId ?? "")) {
    return { ok: false, error: "Invalid agent_run id." };
  }
  if (!UUID_RE.test(input.campaignId ?? "")) {
    return { ok: false, error: "Invalid campaign id." };
  }
  const offsets = Array.isArray(input.selectedDayOffsets)
    ? input.selectedDayOffsets.filter(
        (n) => Number.isInteger(n) && n >= 0 && n <= 365,
      )
    : [];
  if (offsets.length === 0) {
    return {
      ok: false,
      error: "Pick at least one calendar idea to materialise.",
    };
  }
  const startDate = (input.startDate ?? "").trim() || todayUtcIso();
  if (!ISO_DATE_RE.test(startDate)) {
    return {
      ok: false,
      error: "startDate must be yyyy-MM-dd or empty (defaults to today).",
    };
  }
  if ((input.operatorNotes ?? "").length > 500) {
    return { ok: false, error: "Operator notes are too long (500 char cap)." };
  }

  // Demo mode is not currently used by the dashboard's recent-runs flow;
  // we keep a stub branch that returns a clear message rather than
  // pretending to mutate the demo content store.
  if (getDataSource() === "demo") {
    return {
      ok: false,
      error:
        "Demo mode does not persist agent-driven draft calendar items yet. "
        + "Switch NEXT_PUBLIC_DATA_SOURCE=supabase to use this action.",
    };
  }

  try {
    const admin = getServiceRoleSupabase();

    // ---- Load + validate the agent_run ---------------------------------
    const { data: runRow, error: runErr } = await admin
      .from("agent_runs")
      .select(
        "id, workspace_id, agent_type, status, input, output, error_message",
      )
      .eq("id", input.agentRunId)
      .maybeSingle();
    if (runErr) return { ok: false, error: runErr.message };
    const run = runRow as {
      id: string;
      workspace_id: string;
      agent_type: string;
      status: string;
      input: unknown;
      output: unknown;
      error_message: string | null;
    } | null;
    if (!run) return { ok: false, error: "agent_run not found." };
    if (run.agent_type !== "brand_analysis_ugc_prompt_planning") {
      return {
        ok: false,
        error:
          "Only brand_analysis_ugc_prompt_planning runs can produce a "
          + "calendar today.",
      };
    }
    if (run.status !== "completed") {
      return {
        ok: false,
        error: `agent_run.status is "${run.status}"; only 'completed' runs can be materialised.`,
      };
    }

    const out = (run.output ?? {}) as Record<string, unknown>;
    const ideas: AgentCalendarIdea[] = Array.isArray(out.contentCalendarIdeas)
      ? (out.contentCalendarIdeas as unknown[])
          .flatMap((i) =>
            i && typeof i === "object"
              ? [normalizeIdea(i as Record<string, unknown>)]
              : [],
          )
          .filter((i): i is AgentCalendarIdea => i !== null)
      : [];
    if (ideas.length === 0) {
      return {
        ok: false,
        error:
          "This agent_run has no contentCalendarIdeas to materialise. "
          + "Re-run the agent so the latest planner output is stored.",
      };
    }
    const ideaByOffset = new Map<number, AgentCalendarIdea>();
    for (const i of ideas) {
      if (!ideaByOffset.has(i.dayOffset)) ideaByOffset.set(i.dayOffset, i);
    }
    const wantedIdeas: AgentCalendarIdea[] = [];
    for (const off of offsets) {
      const i = ideaByOffset.get(off);
      if (!i) {
        return {
          ok: false,
          error: `Selected day-offset D+${off} is not present in this agent_run's output.`,
        };
      }
      wantedIdeas.push(i);
    }

    const matchedNiche =
      typeof out.matchedNiche === "string" ? out.matchedNiche : null;
    const inputObj = (run.input ?? {}) as Record<string, unknown>;
    const productUrl =
      typeof inputObj.productUrl === "string" ? inputObj.productUrl : null;

    // ---- Resolve campaign + workspace tenancy --------------------------
    const { data: campaignRow, error: campErr } = await admin
      .from("campaigns")
      .select("id, brand_id, title")
      .eq("id", input.campaignId)
      .maybeSingle();
    if (campErr) return { ok: false, error: campErr.message };
    const campaign = campaignRow as
      | { id: string; brand_id: string; title: string }
      | null;
    if (!campaign) return { ok: false, error: "Campaign not found." };

    const { data: brandRow, error: brandErr } = await admin
      .from("brands")
      .select("id, workspace_id")
      .eq("id", campaign.brand_id)
      .maybeSingle();
    if (brandErr) return { ok: false, error: brandErr.message };
    const brand = brandRow as
      | { id: string; workspace_id: string }
      | null;
    if (!brand) return { ok: false, error: "Brand not found." };
    if (brand.workspace_id !== run.workspace_id) {
      return {
        ok: false,
        error:
          "agent_run.workspace_id does not match the selected campaign's "
          + "workspace.",
      };
    }

    // ---- Insert one content_items row per idea -------------------------
    const insertRows = wantedIdeas.map((i) => ({
      campaign_id: campaign.id,
      title: i.title,
      status: "draft" as const,
      scheduled_for: addDaysIso(startDate, i.dayOffset),
      // Phase 2D: store the suggested channel in the existing
      // text[] platforms column (no migration). All other multi-format
      // metadata goes into prompt_summary. Defaults preserved:
      // quality_tier='standard_720p', shared_with_client=false,
      // audio_fixer_*=false. No internal_*, no client_safe_*,
      // no cost_* — all stay null.
      platforms: [i.suggestedChannel],
      caption_draft: i.brief,
      prompt_summary: buildProvenanceSummary({
        agentRunId: run.id,
        matchedNiche,
        productUrl,
        dayOffset: i.dayOffset,
        brief: i.brief,
        idea: i,
        operatorNotes: input.operatorNotes,
      }),
      hook_source: "ai_suggested" as const,
    }));

    const { data: inserted, error: insertErr } = await admin
      .from("content_items")
      .insert(insertRows)
      .select("id");
    if (insertErr) return { ok: false, error: insertErr.message };
    const ids = (inserted as { id: string }[] | null)?.map((r) => r.id) ?? [];
    if (ids.length !== insertRows.length) {
      return {
        ok: false,
        error: `Insert returned ${ids.length} ids but expected ${insertRows.length}.`,
        contentItemIds: ids,
      };
    }

    revalidatePath(`/agency/campaigns/${campaign.id}/calendar`);
    revalidatePath(`/agency/campaigns/${campaign.id}/outputs`);
    revalidatePath(`/agency/agents/brand-analysis`);
    revalidatePath(`/agency`);

    return {
      ok: true,
      message:
        `Created ${ids.length} draft content item(s). status = draft, `
        + `shared_with_client = false. No generation job was created; `
        + `no paid call was made.`,
      contentItemIds: ids,
      calendarHref: `/agency/campaigns/${campaign.id}/calendar`,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}
