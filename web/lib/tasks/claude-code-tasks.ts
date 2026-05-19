// Yuvo Studio — Phase 2D hybrid Claude Code task model.
//
// CONCEPT ONLY. This module defines the SHAPE of an operator task that
// the dashboard prepares and Claude Code (via MCP, in a session the
// operator runs) executes — then writes results back to Supabase.
//
// IMPORTANT: nothing here executes anything. There is:
//   - no Claude API call
//   - no local daemon
//   - no website→Claude Code bridge
//   - no auto-run
// The dashboard only *describes* the task. A human runs Claude Code.
//
// Persistence is intentionally NOT wired in this phase (no table). When
// it lands it would be a `claude_code_tasks` table mirroring this type.

export const CLAUDE_CODE_TASK_STATUSES = [
  "draft",
  "ready_for_claude",
  "in_progress",
  "completed",
  "failed",
] as const;
export type ClaudeCodeTaskStatus =
  (typeof CLAUDE_CODE_TASK_STATUSES)[number];

export const CLAUDE_CODE_TASK_KINDS = [
  "competitor_research",
  "organic_content_calendar",
  "social_copy_generation",
  "ugc_prompt_draft",
  "audit_failed_generation",
  "client_feedback_revision",
] as const;
export type ClaudeCodeTaskKind = (typeof CLAUDE_CODE_TASK_KINDS)[number];

export interface ClaudeCodeTask {
  /** Local id (uuid) — not persisted in Phase 2D. */
  id: string;
  workspaceId: string;
  kind: ClaudeCodeTaskKind;
  status: ClaudeCodeTaskStatus;
  /** Short operator-facing summary. */
  title: string;
  /** The brief the operator would paste into a Claude Code session. */
  instructions: string;
  /** Optional links into the dashboard the task concerns. */
  context?: {
    brandId?: string;
    campaignId?: string;
    contentItemId?: string;
    agentRunId?: string;
  };
  createdAt: string;
  updatedAt: string;
}

/** Human-readable labels (UI). */
export const CLAUDE_CODE_TASK_KIND_LABELS: Record<
  ClaudeCodeTaskKind,
  string
> = {
  competitor_research: "Run competitor research for this brand",
  organic_content_calendar: "Create 7-day organic content calendar",
  social_copy_generation: "Generate social/LinkedIn post copy",
  ugc_prompt_draft: "Prepare UGC prompt draft for a content item",
  audit_failed_generation: "Audit a failed generation job",
  client_feedback_revision: "Review client feedback & propose revision",
};

export const CLAUDE_CODE_TASK_STATUS_LABELS: Record<
  ClaudeCodeTaskStatus,
  string
> = {
  draft: "Draft (dashboard preparing)",
  ready_for_claude: "Ready for Claude Code",
  in_progress: "Claude Code running",
  completed: "Completed (results in Supabase)",
  failed: "Failed",
};

/** Example task templates the dashboard can surface as starting points.
 *  Pure data — copying the `instructions` into a Claude Code session is
 *  a manual operator step. */
export const CLAUDE_CODE_TASK_TEMPLATES: Array<{
  kind: ClaudeCodeTaskKind;
  title: string;
  instructions: string;
}> = [
  {
    kind: "competitor_research",
    title: "Competitor research for a brand",
    instructions:
      "Research 3–5 competitors for <brand>. Summarise their content "
      + "formats, posting cadence, and hook patterns. Write findings to "
      + "Supabase as an agent_run (read-only research, no paid calls).",
  },
  {
    kind: "organic_content_calendar",
    title: "7-day organic content calendar",
    instructions:
      "Using the latest Brand Analysis agent_run for <brand>, propose a "
      + "7-day organic calendar (reels, stories, carousels, posts). "
      + "Create draft content_items only — no generation, no client share.",
  },
  {
    kind: "social_copy_generation",
    title: "Social / LinkedIn post copy",
    instructions:
      "Draft post copy for the selected content_items (LinkedIn / IG / "
      + "FB). Write into content_items.caption_draft / prompt_summary as "
      + "operator-review drafts. No sending, no client share.",
  },
  {
    kind: "ugc_prompt_draft",
    title: "UGC prompt draft for a content item",
    instructions:
      "For <content_item>, create an operator_editing prompt_version "
      + "from the linked agent_run. Never approve_for_generation, never "
      + "submit a job.",
  },
  {
    kind: "audit_failed_generation",
    title: "Audit a failed generation job",
    instructions:
      "Inspect <generation_job>. Summarise the failure cause and a "
      + "safe remediation. Do NOT resubmit or call any provider.",
  },
  {
    kind: "client_feedback_revision",
    title: "Review client feedback & propose revision",
    instructions:
      "Read the client feedback / regeneration_request for <content_item>. "
      + "Propose a revised prompt as an operator_editing draft. No "
      + "generation, no client reply sent.",
  },
];

// ===========================================================================
// Phase 2K — inbox row → Claude Code task prompt (COPY-ONLY).
//
// Pure functions. No I/O, no DB, no Claude API, no child_process. Given
// an InboxItem (type-only import — does NOT pull the server data layer
// into a client bundle), produce a ready-to-paste Claude Code brief.
// The operator copies `copyText` into their own Claude Code session;
// the dashboard never executes anything.
// ===========================================================================

import type { InboxItem, InboxItemKind } from "@/lib/data/owner-overview";

const PROJECT_PATH = "C:\\Users\\mathi\\Desktop\\Marketing_Agency";
const PROJECT_BRANCH = "phase-1-owner-dashboard-agents";

export type ClaudeTaskRiskLevel =
  | "read_only"
  | "draft_write"
  | "gated_paid"
  | "info_only";

export interface PreparedClaudeCodeTask {
  title: string;
  taskType: ClaudeCodeTaskKind;
  riskLevel: ClaudeTaskRiskLevel;
  /** The specific action the operator wants Claude Code to perform. */
  instructions: string;
  /** Explicit do-NOTs surfaced to the operator and embedded in copyText. */
  safetyRules: string[];
  expectedOutputs: string[];
  /** Dashboard hrefs relevant to the task. */
  relatedLinks: string[];
  /** The full, paste-ready prompt for a Claude Code session. */
  copyText: string;
}

/** Universal safety rules pasted into EVERY task. These mirror the
 *  hard rules the operator gives Claude at the start of every phase. */
const UNIVERSAL_SAFETY_RULES: string[] = [
  "Do NOT run Seedance / Enhancor / Audio Fixer or any paid API.",
  "Do NOT submit a generation job or flip a prompt to approved_for_generation unless I give the exact approval phrase.",
  "Do NOT send email, publish to any social platform, or share anything with the client.",
  "Do NOT commit or push. Work on branch " + PROJECT_BRANCH + ".",
  "Do NOT make irreversible or destructive changes. Halt and report first.",
  "Report what you inspected, what (if anything) you changed, and the recommended next operator action.",
];

interface KindRecipe {
  taskType: ClaudeCodeTaskKind;
  riskLevel: ClaudeTaskRiskLevel;
  title: (i: InboxItem) => string;
  instructions: (i: InboxItem) => string;
  extraSafety: string[];
  expectedOutputs: string[];
}

const KIND_RECIPES: Record<InboxItemKind, KindRecipe> = {
  failed_generation_job: {
    taskType: "audit_failed_generation",
    riskLevel: "read_only",
    title: (i) => `Audit failed generation job — ${i.title}`,
    instructions: () =>
      "Inspect the failed generation_job: read its error_message, "
      + "raw_request_json / raw_response_json, and the provider logs. "
      + "Diagnose whether the cause is payload, provider, billing, or "
      + "asset. Do NOT retry or resubmit. Propose the safe remediation.",
    extraSafety: [
      "Do NOT resubmit the job or call the provider.",
      "Do NOT mutate the generation_job row.",
    ],
    expectedOutputs: [
      "Root-cause summary (payload | provider | billing | asset).",
      "A safe remediation the operator can approve later.",
    ],
  },
  generated_video_needs_review: {
    taskType: "audit_failed_generation",
    riskLevel: "read_only",
    title: (i) => `Review generated clip — ${i.title}`,
    instructions: () =>
      "Inspect the completed generation output: verify resolution / "
      + "duration / audio presence and label readability. If an ingest "
      + "dry-run helps, run it READ-ONLY. Halt before any apply / "
      + "share / status flip.",
    extraSafety: [
      "Dry-run only — do NOT apply the result or change content status.",
      "Do NOT share with the client.",
    ],
    expectedOutputs: [
      "Pass/fail on resolution, duration, audio, label readability.",
      "Whether the clip is good enough to apply (operator decides).",
    ],
  },
  prompt_draft_needs_review: {
    taskType: "ugc_prompt_draft",
    riskLevel: "draft_write",
    title: (i) => `Review & tighten prompt draft — ${i.title}`,
    instructions: () =>
      "Inspect the latest prompt_version. Tighten hook / script / "
      + "scene_plan / negative_prompt for clarity and the label-safety "
      + "lesson. Keep status = operator_editing. Do NOT approve.",
    extraSafety: [
      "Keep prompt_versions.status = operator_editing.",
      "Do NOT set approved_for_generation.",
    ],
    expectedOutputs: [
      "A tightened operator_editing prompt_version (or a concrete diff).",
      "Note any label-hallucination risks.",
    ],
  },
  approved_prompt_ready_for_generation: {
    taskType: "ugc_prompt_draft",
    riskLevel: "gated_paid",
    title: (i) => `Prepare generation dry-run — ${i.title}`,
    instructions: () =>
      "Prepare the generation dry-run for the approved prompt: build "
      + "the payload, estimate credits, and surface the exact CLI the "
      + "operator would run. Do NOT submit a paid job.",
    extraSafety: [
      "NO paid submit. Dry-run + estimate only.",
      "Only proceed to a paid run if I paste the exact approval phrase.",
    ],
    expectedOutputs: [
      "Validated payload preview + credit estimate.",
      "The exact gated CLI command (not executed).",
    ],
  },
  copy_draft_needs_review: {
    taskType: "social_copy_generation",
    riskLevel: "draft_write",
    title: (i) => `Review copy draft — ${i.title}`,
    instructions: () =>
      "Inspect content_items.caption_draft for this non-video item. "
      + "Tighten it to real brand voice. Write the improved copy back "
      + "to caption_draft as an operator-review draft only.",
    extraSafety: [
      "caption_draft only — do NOT flip status or share_with_client.",
      "Do NOT prepare or share a client preview.",
    ],
    expectedOutputs: [
      "Improved caption_draft (operator-review).",
      "What changed and why.",
    ],
  },
  copy_client_requested_changes: {
    taskType: "client_feedback_revision",
    riskLevel: "draft_write",
    title: (i) => `Revise copy from client feedback — ${i.title}`,
    instructions: () =>
      "Read the latest client content_feedback / open "
      + "regeneration_request. Revise the copy internally to address "
      + "it. Keep it internal — status returns to draft, not shared.",
    extraSafety: [
      "Internal revision only — do NOT re-share with the client.",
      "Internal approval + client preview must be re-run by the operator.",
    ],
    expectedOutputs: [
      "Revised internal caption_draft addressing the feedback.",
      "Confirmation the old approval/preview state was reset.",
    ],
  },
  copy_approved_by_client: {
    taskType: "social_copy_generation",
    riskLevel: "info_only",
    title: (i) => `Client approved copy — ${i.title}`,
    instructions: () =>
      "No action needed. Publishing is manual / out of scope. "
      + "Optionally summarise the approved copy for the operator's "
      + "records.",
    extraSafety: [
      "Do NOT publish or send anything.",
      "No DB writes required.",
    ],
    expectedOutputs: ["A one-line confirmation summary (optional)."],
  },
  copy_preview_ready_to_share: {
    taskType: "social_copy_generation",
    riskLevel: "info_only",
    title: (i) => `Copy preview ready to share — ${i.title}`,
    instructions: () =>
      "The copy is internally approved and a preview is prepared. "
      + "Verify the preview reads cleanly. The actual share is an "
      + "operator-gated dashboard action — do NOT perform it.",
    extraSafety: [
      "Do NOT share with the client (operator does this in the UI).",
      "Verification only.",
    ],
    expectedOutputs: ["Preview verification notes."],
  },
  video_client_requested_changes: {
    taskType: "client_feedback_revision",
    riskLevel: "draft_write",
    title: (i) => `Revise video prompt from client feedback — ${i.title}`,
    instructions: () =>
      "Read the client feedback / open regeneration_request for this "
      + "video item. Propose a revised operator_editing prompt_version. "
      + "Do NOT generate or share.",
    extraSafety: [
      "Keep prompt_versions.status = operator_editing.",
      "Do NOT submit a generation job or reply to the client.",
    ],
    expectedOutputs: [
      "A revised operator_editing prompt_version (or a diff).",
    ],
  },
  video_approved_by_client: {
    taskType: "audit_failed_generation",
    riskLevel: "info_only",
    title: (i) => `Client approved video — ${i.title}`,
    instructions: () =>
      "No action needed. Publishing path is manual / out of scope. "
      + "Optionally confirm the final asset is on file.",
    extraSafety: ["Do NOT publish. No DB writes required."],
    expectedOutputs: ["Optional one-line confirmation."],
  },
  open_regeneration_request: {
    taskType: "client_feedback_revision",
    riskLevel: "draft_write",
    title: (i) => `Resolve open client request — ${i.title}`,
    instructions: () =>
      "Read the open regeneration_request. Decide whether it is a copy "
      + "or video item and propose the internal revision (draft prompt "
      + "or draft copy). Do NOT auto-accept or reply to the client.",
    extraSafety: [
      "Internal draft only — no client reply, no generation.",
      "Leave the regeneration_request status for the operator to set.",
    ],
    expectedOutputs: [
      "A proposed internal revision + which path (copy/video) applies.",
    ],
  },
  agent_run_completed: {
    taskType: "organic_content_calendar",
    riskLevel: "read_only",
    title: () => `Inspect completed agent run & propose next step`,
    instructions: () =>
      "Inspect the completed agent_run output. Propose the best next "
      + "operator action: create a content calendar, create prompt "
      + "drafts, or create copy drafts. Do NOT execute any of them.",
    extraSafety: [
      "Proposal only — do NOT create content_items / prompt_versions.",
    ],
    expectedOutputs: [
      "A ranked list of safe next actions for the operator.",
    ],
  },
  content_item_missing_prompt: {
    taskType: "ugc_prompt_draft",
    riskLevel: "draft_write",
    title: (i) => `Draft a prompt for content item — ${i.title}`,
    instructions: () =>
      "Create an operator_editing prompt_version for this content "
      + "item (use the linked agent_run if present). Never approve.",
    extraSafety: [
      "prompt_versions.status = operator_editing only.",
      "Do NOT submit a generation job.",
    ],
    expectedOutputs: ["A new operator_editing prompt_version."],
  },
  calendar_item_needs_copy: {
    taskType: "social_copy_generation",
    riskLevel: "draft_write",
    title: (i) => `Draft copy for calendar item — ${i.title}`,
    instructions: () =>
      "Create the deterministic copy draft for this non-video calendar "
      + "item (write to caption_draft). Do NOT publish or share.",
    extraSafety: [
      "caption_draft only — no publish, no client share.",
    ],
    expectedOutputs: ["A caption_draft for the item."],
  },
};

/** Parse the inbox item id (e.g. `failed_job:<uuid>`) into a labelled
 *  reference the operator can hand Claude Code. */
function refsFor(item: InboxItem): string[] {
  const refs: string[] = [];
  const colon = item.id.indexOf(":");
  if (colon !== -1) {
    const prefix = item.id.slice(0, colon);
    const suffix = item.id.slice(colon + 1);
    const label =
      prefix === "failed_job" || prefix === "video_review"
        ? "generation_job_id"
        : prefix === "regen"
          ? "regeneration_request_id"
          : prefix === "agent"
            ? "agent_run_id"
            : "ref";
    refs.push(`${label}: ${suffix}`);
  }
  if (item.contentItemId) refs.push(`content_item_id: ${item.contentItemId}`);
  return refs.length > 0 ? refs : ["(no id refs)"];
}

export function buildClaudeCodeTaskForInboxItem(
  item: InboxItem,
): PreparedClaudeCodeTask {
  const recipe = KIND_RECIPES[item.kind];
  const safetyRules = [...recipe.extraSafety, ...UNIVERSAL_SAFETY_RULES];
  const refs = refsFor(item);
  const relatedLinks = [item.href];

  const title = recipe.title(item);
  const instructions = recipe.instructions(item);

  const copyText = [
    "# Yuvo Studio — Claude Code task (prepared by the dashboard, NOT executed)",
    "",
    `Project: ${PROJECT_PATH}`,
    `Branch:  ${PROJECT_BRANCH}`,
    `Task type: ${recipe.taskType}`,
    `Risk level: ${recipe.riskLevel}`,
    `Inbox item: ${item.kind} — ${item.title}`,
    item.brandName || item.campaignName
      ? `Context: ${item.brandName ?? "—"} · ${item.campaignName ?? "—"}` +
        (item.status ? ` · status ${item.status}` : "")
      : null,
    "Refs:",
    ...refs.map((r) => `  ${r}`),
    "",
    "## Requested action",
    instructions,
    "",
    "## Hard safety rules",
    ...safetyRules.map((r) => `- ${r}`),
    "",
    "## Expected outputs",
    ...recipe.expectedOutputs.map((o) => `- ${o}`),
    "",
    "## Reporting",
    "Report: what you inspected, what you changed (if anything), and the",
    "recommended next operator action. Halt before any paid or",
    "irreversible step and ask for explicit approval.",
  ]
    .filter((s): s is string => s !== null)
    .join("\n");

  return {
    title,
    taskType: recipe.taskType,
    riskLevel: recipe.riskLevel,
    instructions,
    safetyRules,
    expectedOutputs: recipe.expectedOutputs,
    relatedLinks,
    copyText,
  };
}

export const CLAUDE_TASK_RISK_LABELS: Record<ClaudeTaskRiskLevel, string> = {
  read_only: "Read-only",
  draft_write: "Draft write (no approve/share)",
  gated_paid: "Gated paid (dry-run only)",
  info_only: "Info only",
};
