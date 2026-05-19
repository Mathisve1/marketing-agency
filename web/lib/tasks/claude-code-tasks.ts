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
