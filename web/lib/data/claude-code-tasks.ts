// Yuvo Studio — Phase 2L claude_code_tasks readers.
//
// OPERATOR-ONLY. Never imported from /client/*. Schema lives in
// supabase/migrations/010_claude_code_tasks.sql (operator-RLS only).
//
// FAIL-SOFT: if migration 010 has not been applied yet, PostgREST
// returns a missing-relation error (42P01 / PGRST205). Every read here
// degrades to [] / null instead of throwing, so the dashboard keeps
// working and the Phase 2K copy-only handoff is unaffected.
//
// This module performs NO writes, NO Claude execution, NO provider
// call. Writes live in web/lib/actions/claude-code-tasks.ts.

import { getDataSource, SupabaseDataError } from "./_source";
import { getSupabaseServerClient } from "@/lib/supabase/client";

export type ClaudeCodeTaskStatus =
  | "draft"
  | "ready_for_claude"
  | "in_progress"
  | "completed"
  | "failed"
  | "cancelled";

export type ClaudeCodeTaskRiskLevel =
  | "info_only"
  | "read_only"
  | "draft_write"
  | "gated_paid";

export interface ClaudeCodeTaskRow {
  id: string;
  workspaceId: string;
  inboxItemKind: string;
  taskType: string;
  riskLevel: ClaudeCodeTaskRiskLevel;
  status: ClaudeCodeTaskStatus;
  title: string;
  instructions: string;
  safetyRules: string[];
  expectedOutputs: string[];
  relatedLinks: string[];
  context: unknown;
  resultSummary: string | null;
  errorMessage: string | null;
  createdBy: string | null;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
}

const SELECT_COLS =
  "id, workspace_id, inbox_item_kind, task_type, risk_level, status, " +
  "title, instructions, safety_rules, expected_outputs, related_links, " +
  "context, result_summary, error_message, created_by, created_at, " +
  "updated_at, completed_at";

function asStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

function rowToView(r: Record<string, unknown>): ClaudeCodeTaskRow {
  return {
    id: String(r.id),
    workspaceId: String(r.workspace_id),
    inboxItemKind: String(r.inbox_item_kind),
    taskType: String(r.task_type),
    riskLevel: r.risk_level as ClaudeCodeTaskRiskLevel,
    status: r.status as ClaudeCodeTaskStatus,
    title: String(r.title),
    instructions: String(r.instructions),
    safetyRules: asStringArray(r.safety_rules),
    expectedOutputs: asStringArray(r.expected_outputs),
    relatedLinks: asStringArray(r.related_links),
    context: r.context ?? {},
    resultSummary: (r.result_summary as string | null) ?? null,
    errorMessage: (r.error_message as string | null) ?? null,
    createdBy: (r.created_by as string | null) ?? null,
    createdAt: String(r.created_at),
    updatedAt: String(r.updated_at),
    completedAt: (r.completed_at as string | null) ?? null,
  };
}

/** True when the error indicates the table doesn't exist yet (migration
 *  010 not applied). PostgREST surfaces this as 42P01 / PGRST205. */
function isMissingRelation(err: unknown): boolean {
  const e = err as { code?: string; message?: string } | undefined;
  if (!e) return false;
  if (e.code === "42P01" || e.code === "PGRST205") return true;
  return Boolean(
    e.message &&
      /claude_code_tasks/i.test(e.message) &&
      /does not exist|not found|schema cache/i.test(e.message),
  );
}

/** Whether the claude_code_tasks table is reachable. Used by the UI to
 *  decide if the "Save task" affordance should be shown. Fail-soft:
 *  false on any error. */
export async function claudeCodeTasksTableReady(): Promise<boolean> {
  if (getDataSource() === "demo") return false;
  const supabase = getSupabaseServerClient();
  const { error } = await supabase
    .from("claude_code_tasks")
    .select("id")
    .limit(1);
  return !error;
}

export async function listClaudeCodeTasksForWorkspace(
  workspaceId: string,
  options: { limit?: number; status?: ClaudeCodeTaskStatus } = {},
): Promise<ClaudeCodeTaskRow[]> {
  if (getDataSource() === "demo") return [];
  const supabase = getSupabaseServerClient();
  let q = supabase
    .from("claude_code_tasks")
    .select(SELECT_COLS)
    .eq("workspace_id", workspaceId)
    .order("created_at", { ascending: false })
    .limit(options.limit ?? 50);
  if (options.status) q = q.eq("status", options.status);
  const { data, error } = await q;
  if (error) {
    if (isMissingRelation(error)) return [];
    throw new SupabaseDataError("listClaudeCodeTasksForWorkspace", error);
  }
  if (!data) return [];
  return (data as unknown as Record<string, unknown>[]).map(rowToView);
}

export async function getClaudeCodeTask(
  taskId: string,
): Promise<ClaudeCodeTaskRow | null> {
  if (getDataSource() === "demo") return null;
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("claude_code_tasks")
    .select(SELECT_COLS)
    .eq("id", taskId)
    .maybeSingle();
  if (error) {
    if (isMissingRelation(error)) return null;
    throw new SupabaseDataError("getClaudeCodeTask", error);
  }
  if (!data) return null;
  return rowToView(data as unknown as Record<string, unknown>);
}

// ---------------------------------------------------------------------------
// Phase 2N — read-only queue health summary for the Owner Command
// Center. Pure derivation over listClaudeCodeTasksForWorkspace; no
// writes, no Claude execution, no provider call. Fail-soft: empty
// summary when migration 010 isn't applied.
// ---------------------------------------------------------------------------

export interface ClaudeCodeTaskSummary {
  tableReady: boolean;
  total: number;
  draftCount: number;
  readyForClaudeCount: number;
  inProgressCount: number;
  completedCount: number;
  failedCount: number;
  cancelledCount: number;
  /** ready_for_claude whose updatedAt is older than the overdue
   *  threshold (a prepared task nobody has run yet). */
  overdueReadyCount: number;
  /** Hours after which a ready_for_claude task counts as overdue. */
  overdueThresholdHours: number;
  /** Newest task (any status), for the card's "latest" line. */
  latest: {
    id: string;
    title: string;
    status: ClaudeCodeTaskStatus;
    updatedAt: string;
  } | null;
  recentTasks: ClaudeCodeTaskRow[];
  failedRecentTasks: ClaudeCodeTaskRow[];
}

const OVERDUE_READY_HOURS = 24;

export async function getClaudeCodeTaskSummaryForWorkspace(
  workspaceId: string,
): Promise<ClaudeCodeTaskSummary> {
  const empty: ClaudeCodeTaskSummary = {
    tableReady: false,
    total: 0,
    draftCount: 0,
    readyForClaudeCount: 0,
    inProgressCount: 0,
    completedCount: 0,
    failedCount: 0,
    cancelledCount: 0,
    overdueReadyCount: 0,
    overdueThresholdHours: OVERDUE_READY_HOURS,
    latest: null,
    recentTasks: [],
    failedRecentTasks: [],
  };
  if (getDataSource() === "demo") return empty;

  const ready = await claudeCodeTasksTableReady();
  if (!ready) return empty;

  // listClaudeCodeTasksForWorkspace is already fail-soft + read-only.
  const tasks = await listClaudeCodeTasksForWorkspace(workspaceId, {
    limit: 500,
  });

  const now = Date.now();
  const overdueMs = OVERDUE_READY_HOURS * 60 * 60 * 1000;
  let draftCount = 0;
  let readyForClaudeCount = 0;
  let inProgressCount = 0;
  let completedCount = 0;
  let failedCount = 0;
  let cancelledCount = 0;
  let overdueReadyCount = 0;

  for (const t of tasks) {
    switch (t.status) {
      case "draft":
        draftCount++;
        break;
      case "ready_for_claude":
        readyForClaudeCount++;
        if (now - new Date(t.updatedAt).getTime() > overdueMs) {
          overdueReadyCount++;
        }
        break;
      case "in_progress":
        inProgressCount++;
        break;
      case "completed":
        completedCount++;
        break;
      case "failed":
        failedCount++;
        break;
      case "cancelled":
        cancelledCount++;
        break;
    }
  }

  // tasks are already created_at DESC from the reader.
  const latest = tasks[0]
    ? {
        id: tasks[0].id,
        title: tasks[0].title,
        status: tasks[0].status,
        updatedAt: tasks[0].updatedAt,
      }
    : null;

  return {
    tableReady: true,
    total: tasks.length,
    draftCount,
    readyForClaudeCount,
    inProgressCount,
    completedCount,
    failedCount,
    cancelledCount,
    overdueReadyCount,
    overdueThresholdHours: OVERDUE_READY_HOURS,
    latest,
    recentTasks: tasks.slice(0, 5),
    failedRecentTasks: tasks
      .filter((t) => t.status === "failed")
      .slice(0, 5),
  };
}
