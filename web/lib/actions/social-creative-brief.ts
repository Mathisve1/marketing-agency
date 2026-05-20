// Yuvo Studio — Phase 4A Social Creative Brief Agent action.
//
// OPERATOR-ONLY. Generates a deterministic, structured **planning
// brief** for the visual side of a social asset (carousel / story /
// feed_post / static_image / linkedin / reel-support / video-support)
// and writes it to a `[creative brief]` block in
// content_items.prompt_summary.
//
// HARD RULES:
//   - It NEVER generates a final image / video / asset.
//   - It NEVER calls Seedance / Enhancor / Audio Fixer / OpenAI / a
//     paid API / an image-gen API / a fetch().
//   - It NEVER changes `content_items.caption_draft`,
//     `client_safe_copy_preview`, or `shared_with_client`.
//   - It NEVER creates or modifies `prompt_versions`,
//     `generation_jobs`, `generated_assets`, or any other table.
//   - It NEVER sends email or publishes anything.
//   - The block lives in `prompt_summary`, which the client portal view
//     (`client_content_items_v`) does NOT project — so the brief is
//     structurally invisible to the client.

"use server";

import { revalidatePath } from "next/cache";
import { getDataSource } from "@/lib/data/_source";
import {
  getServiceRoleSupabase,
  hasSupabaseEnv,
} from "@/lib/supabase/server";
import { getCurrentPersona } from "@/lib/auth/persona";
import {
  isContentChannel,
  isContentFormat,
  type ContentChannel,
  type ContentFormat,
} from "@/lib/content/taxonomy";
import {
  planSocialCreativeBrief,
  type CreativeBrief,
} from "@/lib/agents/social-creative-brief";

export interface CreativeBriefActionInput {
  contentItemId: string;
  operatorNotes?: string;
}

export interface CreativeBriefActionResult {
  ok: boolean;
  error?: string;
  message?: string;
  contentItemId?: string;
  format?: string;
  channel?: string;
  mode?: string;
  brief?: CreativeBrief;
  /** Markdown rendering of the brief — for the UI preview only. The
   *  same string is also embedded into prompt_summary. */
  markdown?: string;
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Same operator-editable set used by the prompt-draft + copy-draft
// actions. A creative brief is a planning artefact, so we allow it on
// any item the operator can still edit (anything except client-signed-off
// states). Mirrors web/lib/actions/copy-draft.ts EDITABLE_STATUSES.
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

const BRIEF_BLOCK_MARKER = "\n\n[creative brief]\n";

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

function parseTag(promptSummary: string | null, tag: string): string | null {
  if (!promptSummary) return null;
  const m = promptSummary.match(
    new RegExp(`(?:^|\\n)${tag}:\\s*([a-z0-9_\\-]+)`, "i"),
  );
  return m ? m[1] : null;
}

/** Drop any prior `[creative brief]` block so re-runs are idempotent.
 *  The brief block is intentionally appended LAST in the provenance
 *  trail (after any `[copy draft]` / `[copy approval]` /
 *  `[client copy preview]` blocks), so a tail-slice is safe. */
function stripCreativeBriefBlock(promptSummary: string): string {
  const idx = promptSummary.indexOf(BRIEF_BLOCK_MARKER);
  return idx === -1 ? promptSummary : promptSummary.slice(0, idx);
}

export async function createSocialCreativeBriefAction(
  input: CreativeBriefActionInput,
): Promise<CreativeBriefActionResult> {
  const auth = await requireOperator();
  if (auth !== true) return { ok: false, error: auth.error };

  if (!UUID_RE.test(input.contentItemId ?? "")) {
    return { ok: false, error: "Invalid content item id." };
  }
  if ((input.operatorNotes ?? "").length > 500) {
    return { ok: false, error: "Operator notes are too long (500 cap)." };
  }

  if (getDataSource() === "demo") {
    return {
      ok: false,
      error:
        "Demo mode does not persist creative briefs. Switch "
        + "NEXT_PUBLIC_DATA_SOURCE=supabase to use this action.",
    };
  }

  try {
    const admin = getServiceRoleSupabase();

    const { data: ciRow, error: ciErr } = await admin
      .from("content_items")
      .select(
        "id, campaign_id, status, prompt_summary, title, caption_draft, platforms",
      )
      .eq("id", input.contentItemId)
      .maybeSingle();
    if (ciErr) return { ok: false, error: ciErr.message };
    const ci = ciRow as {
      id: string;
      campaign_id: string;
      status: string;
      prompt_summary: string | null;
      title: string;
      caption_draft: string | null;
      platforms: string[] | null;
    } | null;
    if (!ci) return { ok: false, error: "Content item not found." };
    if (!EDITABLE_STATUSES.has(ci.status)) {
      return {
        ok: false,
        error:
          `Content item status is "${ci.status}"; creative briefs can only be `
          + `created in an operator-editable state (not under client sign-off).`,
      };
    }

    // Resolve format + channel from prompt_summary, falling back to
    // platforms[0] for channel and a safe non-video default for format.
    const fmtRaw = parseTag(ci.prompt_summary, "format");
    const chRaw =
      parseTag(ci.prompt_summary, "channel") ??
      (Array.isArray(ci.platforms) ? ci.platforms[0] ?? null : null);
    const format: ContentFormat = isContentFormat(fmtRaw)
      ? fmtRaw
      : "feed_post";
    const channel: ContentChannel = isContentChannel(chRaw)
      ? chRaw
      : "other";

    const matchedNiche = parseTag(ci.prompt_summary, "matched_niche");
    const productUrl = parseTag(ci.prompt_summary, "product_url");
    const contentGoal = parseTag(ci.prompt_summary, "content_goal");

    const result = planSocialCreativeBrief(ci.id, {
      format,
      channel,
      title: ci.title,
      brief: ci.caption_draft?.trim() || ci.title,
      brandContext: {
        productUrl: productUrl ?? undefined,
        matchedNiche: matchedNiche ?? undefined,
      },
      captionDraft: ci.caption_draft ?? undefined,
      contentGoal: contentGoal ?? undefined,
      operatorNotes: input.operatorNotes,
    });
    const { brief, markdown } = result;

    // Build the structured block. Strip any prior brief block first so
    // re-runs are idempotent. The readable markdown is appended after
    // the keys (separated by a blank line) — kept inside the same
    // block so a future `stripCreativeBriefBlock` removes everything
    // cleanly.
    const nowIso = new Date().toISOString();
    const baseSummary = stripCreativeBriefBlock(ci.prompt_summary ?? "");
    const briefBlock =
      BRIEF_BLOCK_MARKER +
      [
        "creative_brief_status: drafted",
        "creative_brief_source: social_creative_brief_agent",
        `creative_brief_format: ${format}`,
        `creative_brief_channel: ${channel}`,
        `creative_brief_mode: ${brief.mode}`,
        `creative_brief_created_at: ${nowIso}`,
        input.operatorNotes
          ? `creative_brief_operator_note: ${input.operatorNotes}`
          : null,
      ]
        .filter((s): s is string => s !== null)
        .join("\n") +
      "\n\n" +
      markdown;
    const newSummary = baseSummary + briefBlock;

    const { error: updErr } = await admin
      .from("content_items")
      .update({
        // ONLY prompt_summary is written. caption_draft,
        // shared_with_client, client_safe_copy_preview, status, and
        // every other column are deliberately NOT in this update.
        prompt_summary: newSummary,
      })
      .eq("id", ci.id);
    if (updErr) return { ok: false, error: updErr.message };

    revalidatePath("/agency/creative-briefs");
    revalidatePath("/agency/copy-drafts");
    revalidatePath(`/agency/campaigns/${ci.campaign_id}/calendar`);
    revalidatePath("/agency");

    return {
      ok: true,
      message: "Creative brief drafted.",
      contentItemId: ci.id,
      format,
      channel,
      mode: brief.mode,
      brief,
      markdown,
    };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}
