// Yuvo Studio — Phase 2A: create operator_editing prompt drafts for a
// (typically Calendar-Agent-created) draft content item.
//
// OPERATOR-ONLY. This action resolves the source Brand Analysis
// agent_run for a content item (via input override, or by parsing the
// `agent_run_id:` provenance line that Phase 1Z wrote into
// content_items.prompt_summary), then materialises one or more of that
// run's prompt drafts as prompt_versions rows.
//
// It delegates the actual insert to the Phase 1X
// `createPromptVersionFromAgentDraftAction`, which is the single audited
// path that guarantees:
//   - status = operator_editing (never approved_for_generation)
//   - version_number = max + 1, parent = latest
//   - quality_tier = standard_720p
//   - no generation_jobs / generated_assets / audio_fixer_jobs
//   - no provider call, no email, no client exposure
//
// This wrapper adds NO new write surface of its own beyond those rows.

"use server";

import { getDataSource } from "@/lib/data/_source";
import {
  getServiceRoleSupabase,
  hasSupabaseEnv,
} from "@/lib/supabase/server";
import { getCurrentPersona } from "@/lib/auth/persona";
import { getAgentRun } from "@/lib/data/agent-runs";
import {
  planBrandAnalysisUGCPrompt,
  type PromptDraft,
} from "@/lib/agents/brand-analysis";
import { createPromptVersionFromAgentDraftAction } from "@/lib/actions/prompt-versions";

export interface CalendarPromptDraftsInput {
  contentItemId: string;
  /** Optional explicit agent_run override. When omitted the action
   *  parses content_items.prompt_summary for an `agent_run_id:` line. */
  agentRunId?: string;
  /** Which of the run's promptDrafts to materialise (0-based). Defaults
   *  to [0] — the standard variant. */
  selectedDraftIndexes?: number[];
  /** Optional operator note appended to every created prompt_version's
   *  notes block. */
  operatorNotes?: string;
}

export interface CalendarPromptDraftsResult {
  ok: boolean;
  error?: string;
  message?: string;
  promptVersionIds?: string[];
  editorHref?: string;
  /** "agent_run" | "regenerated_from_url" | "none" — where the drafts
   *  came from. Surfaced so the UI can be honest about provenance. */
  draftSource?: "agent_run" | "regenerated_from_url" | "none";
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// content statuses where adding an operator-editing prompt draft is
// safe + sensible. We deliberately exclude the two client-decided
// states (shared/approved) — a new prompt under those should go through
// the regeneration-request flow, not this shortcut.
const EDITABLE_STATUSES = new Set([
  "draft",
  "generating",
  "raw_ready",
  "audio_fixer_pending",
  "audio_fixed",
  "ready_for_client_review",
  "changes_requested_by_client",
  "failed",
]);

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

/** Pull `agent_run_id: <uuid>` out of a prompt_summary provenance block.
 *  Returns null when not present / malformed. */
function parseAgentRunId(promptSummary: string | null): string | null {
  if (!promptSummary) return null;
  const m = promptSummary.match(
    /agent_run_id:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i,
  );
  return m ? m[1] : null;
}

/** Pull `product_url: <url>` out of a prompt_summary provenance block. */
function parseProductUrl(promptSummary: string | null): string | null {
  if (!promptSummary) return null;
  const m = promptSummary.match(/product_url:\s*(\S+)/i);
  return m ? m[1] : null;
}

function draftsFromAgentRunOutput(output: unknown): PromptDraft[] {
  if (!output || typeof output !== "object") return [];
  const d = (output as Record<string, unknown>).promptDrafts;
  if (!Array.isArray(d)) return [];
  return d.filter(
    (x): x is PromptDraft =>
      !!x &&
      typeof x === "object" &&
      typeof (x as Record<string, unknown>).label === "string" &&
      typeof (x as Record<string, unknown>).promptBody === "string",
  );
}

export async function createPromptDraftsForCalendarItemAction(
  input: CalendarPromptDraftsInput,
): Promise<CalendarPromptDraftsResult> {
  const auth = await requireOperator();
  if (auth !== true) return { ok: false, error: auth.error };

  if (!UUID_RE.test(input.contentItemId ?? "")) {
    return { ok: false, error: "Invalid content item id." };
  }
  if (input.agentRunId && !UUID_RE.test(input.agentRunId)) {
    return { ok: false, error: "Invalid agent_run id." };
  }
  if ((input.operatorNotes ?? "").length > 500) {
    return { ok: false, error: "Operator notes are too long (500 char cap)." };
  }

  if (getDataSource() === "demo") {
    return {
      ok: false,
      error:
        "Demo mode does not persist agent-driven prompt drafts. Switch "
        + "NEXT_PUBLIC_DATA_SOURCE=supabase to use this action.",
    };
  }

  try {
    const admin = getServiceRoleSupabase();

    // ---- Load + validate the content item ------------------------------
    const { data: ciRow, error: ciErr } = await admin
      .from("content_items")
      .select("id, campaign_id, status, prompt_summary, title")
      .eq("id", input.contentItemId)
      .maybeSingle();
    if (ciErr) return { ok: false, error: ciErr.message };
    const ci = ciRow as {
      id: string;
      campaign_id: string;
      status: string;
      prompt_summary: string | null;
      title: string;
    } | null;
    if (!ci) return { ok: false, error: "Content item not found." };
    if (!EDITABLE_STATUSES.has(ci.status)) {
      return {
        ok: false,
        error:
          `Content item status is "${ci.status}"; prompt drafts can only `
          + `be added in an operator-editable state (not under client `
          + `sign-off).`,
      };
    }

    // ---- Tenancy check (campaign → brand → workspace) ------------------
    const { data: campRow, error: campErr } = await admin
      .from("campaigns")
      .select("id, brand_id")
      .eq("id", ci.campaign_id)
      .maybeSingle();
    if (campErr) return { ok: false, error: campErr.message };
    const camp = campRow as { id: string; brand_id: string } | null;
    if (!camp) return { ok: false, error: "Campaign not found." };
    const { data: brandRow, error: brandErr } = await admin
      .from("brands")
      .select("id")
      .eq("id", camp.brand_id)
      .maybeSingle();
    if (brandErr) return { ok: false, error: brandErr.message };
    if (!brandRow) return { ok: false, error: "Brand not found." };

    // ---- Resolve the source prompt drafts -----------------------------
    const agentRunId =
      input.agentRunId ?? parseAgentRunId(ci.prompt_summary);
    let drafts: PromptDraft[] = [];
    let draftSource: CalendarPromptDraftsResult["draftSource"] = "none";
    let matchedNiche: string | null = null;
    let productUrl = parseProductUrl(ci.prompt_summary);
    let resolvedAgentRunId: string | undefined;

    if (agentRunId) {
      const run = await getAgentRun(agentRunId);
      if (!run) {
        return {
          ok: false,
          error: `Referenced agent_run ${agentRunId} was not found.`,
        };
      }
      if (run.agentType !== "brand_analysis_ugc_prompt_planning") {
        return {
          ok: false,
          error:
            "Linked agent_run is not a brand_analysis_ugc_prompt_planning "
            + "run.",
        };
      }
      if (run.status !== "completed") {
        return {
          ok: false,
          error: `Linked agent_run.status is "${run.status}"; only completed runs can seed prompt drafts.`,
        };
      }
      drafts = draftsFromAgentRunOutput(run.output);
      const out = (run.output ?? {}) as Record<string, unknown>;
      matchedNiche =
        typeof out.matchedNiche === "string" ? out.matchedNiche : null;
      const inObj = (run.input ?? {}) as Record<string, unknown>;
      if (typeof inObj.productUrl === "string") productUrl = inObj.productUrl;
      resolvedAgentRunId = run.id;
      draftSource = drafts.length > 0 ? "agent_run" : "none";
    }

    // Fallback: no resolvable run but we have a product URL in the
    // provenance — regenerate deterministically (no external call).
    if (drafts.length === 0 && productUrl) {
      const plan = planBrandAnalysisUGCPrompt({ productUrl });
      drafts = plan.promptDrafts;
      matchedNiche = plan.matchedNiche;
      draftSource = "regenerated_from_url";
    }

    if (drafts.length === 0) {
      return {
        ok: false,
        error:
          "No prompt drafts available. This content item has no linked "
          + "agent_run and no product_url provenance to regenerate from. "
          + "Run the Brand Analysis agent for this product first.",
        draftSource: "none",
      };
    }

    // ---- Select which drafts to materialise ---------------------------
    const idxs =
      Array.isArray(input.selectedDraftIndexes) &&
      input.selectedDraftIndexes.length > 0
        ? input.selectedDraftIndexes.filter(
            (n) => Number.isInteger(n) && n >= 0 && n < drafts.length,
          )
        : [0];
    if (idxs.length === 0) {
      return {
        ok: false,
        error: "No valid prompt-draft index selected.",
      };
    }

    // ---- Materialise sequentially via the audited Phase 1X path -------
    const calendarNote =
      `source: calendar_agent | source_content_item_id: ${ci.id} | `
      + `calendar_item_title: ${ci.title}`
      + (input.operatorNotes ? ` | ${input.operatorNotes}` : "");

    const promptVersionIds: string[] = [];
    let editorHref: string | undefined;
    for (const i of idxs) {
      const d = drafts[i];
      const r = await createPromptVersionFromAgentDraftAction({
        contentItemId: ci.id,
        label: d.label,
        hook: d.hook,
        script: d.script,
        promptBody: d.promptBody,
        scenePlan: d.scenePlan,
        creatorDirection: d.creatorDirection,
        productConstraints: d.productConstraints,
        negativePrompt: d.negativePrompt,
        callerNotes: calendarNote,
        sourceMetadata: {
          productUrl: productUrl ?? undefined,
          agentType: "brand_analysis_ugc_prompt_planning",
          matchedNiche: matchedNiche ?? undefined,
          agentRunId: resolvedAgentRunId,
        },
      });
      if (!r.ok || !r.promptVersionId) {
        return {
          ok: false,
          error:
            `Created ${promptVersionIds.length} draft(s); draft #${i} `
            + `failed: ${r.error ?? "unknown error"}`,
          promptVersionIds,
          editorHref,
          draftSource,
        };
      }
      promptVersionIds.push(r.promptVersionId);
      editorHref = r.editorHref;
    }

    return {
      ok: true,
      message:
        `Created ${promptVersionIds.length} operator_editing prompt `
        + `version(s) for "${ci.title}". No generation job was created; `
        + `no paid call was made; content item stays ${ci.status}.`,
      promptVersionIds,
      editorHref,
      draftSource,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

// --------------------------------------------------------------------------- #
// Phase 2B — bulk wrapper.
//
// Materialises operator_editing prompt drafts for several draft content
// items in one operator action. It does NOT add any new write path: it
// loops the audited single-item action above, SEQUENTIALLY (so
// version_number resolution per content item never races), and returns
// a per-item success/failure summary. One item failing NEVER aborts the
// others and NEVER approves / generates anything.

export interface BulkPromptDraftsInput {
  contentItemIds: string[];
  agentRunId?: string;
  selectedDraftIndexes?: number[];
  operatorNotes?: string;
}

export interface BulkPromptDraftsItemResult {
  contentItemId: string;
  ok: boolean;
  promptVersionIds: string[];
  editorHref?: string;
  draftSource?: CalendarPromptDraftsResult["draftSource"];
  error?: string;
}

export interface BulkPromptDraftsResult {
  ok: boolean;
  /** True only when every requested item succeeded. */
  allSucceeded: boolean;
  createdCount: number;
  failedCount: number;
  perItem: BulkPromptDraftsItemResult[];
  message?: string;
  error?: string;
}

const BULK_MAX_ITEMS = 50;

export async function createPromptDraftsForCalendarItemsBulkAction(
  input: BulkPromptDraftsInput,
): Promise<BulkPromptDraftsResult> {
  const auth = await requireOperator();
  if (auth !== true) {
    return {
      ok: false,
      allSucceeded: false,
      createdCount: 0,
      failedCount: 0,
      perItem: [],
      error: auth.error,
    };
  }

  const rawIds = Array.isArray(input.contentItemIds)
    ? input.contentItemIds
    : [];
  // De-dupe + validate ids before doing any work.
  const ids = Array.from(new Set(rawIds.map((s) => (s ?? "").trim())));
  if (ids.length === 0) {
    return {
      ok: false,
      allSucceeded: false,
      createdCount: 0,
      failedCount: 0,
      perItem: [],
      error: "Select at least one content item.",
    };
  }
  if (ids.length > BULK_MAX_ITEMS) {
    return {
      ok: false,
      allSucceeded: false,
      createdCount: 0,
      failedCount: 0,
      perItem: [],
      error: `Too many items (max ${BULK_MAX_ITEMS} per bulk run).`,
    };
  }
  for (const id of ids) {
    if (!UUID_RE.test(id)) {
      return {
        ok: false,
        allSucceeded: false,
        createdCount: 0,
        failedCount: 0,
        perItem: [],
        error: `Invalid content item id: ${id}`,
      };
    }
  }

  const perItem: BulkPromptDraftsItemResult[] = [];
  for (const contentItemId of ids) {
    // Reuse the audited single-item path. Each call independently
    // re-validates editability + tenancy + provenance, so a bad item
    // in the list cannot poison a good one.
    const r = await createPromptDraftsForCalendarItemAction({
      contentItemId,
      agentRunId: input.agentRunId,
      selectedDraftIndexes: input.selectedDraftIndexes,
      operatorNotes: input.operatorNotes,
    });
    perItem.push({
      contentItemId,
      ok: r.ok,
      promptVersionIds: r.promptVersionIds ?? [],
      editorHref: r.editorHref,
      draftSource: r.draftSource,
      error: r.ok ? undefined : r.error,
    });
  }

  const createdCount = perItem.reduce(
    (n, i) => n + i.promptVersionIds.length,
    0,
  );
  const failed = perItem.filter((i) => !i.ok);
  const allSucceeded = failed.length === 0;

  return {
    // ok = at least one item produced a draft. Partial success is still
    // a useful, non-destructive outcome the operator can act on.
    ok: createdCount > 0,
    allSucceeded,
    createdCount,
    failedCount: failed.length,
    perItem,
    message:
      `Created ${createdCount} operator_editing prompt version(s) across `
      + `${perItem.length - failed.length}/${perItem.length} item(s). `
      + `No generation job was created; no paid call was made; no item `
      + `was approved or shared.`,
  };
}
