// Yuvo Studio — Phase 2L Claude Code task persistence actions.
//
// OPERATOR-ONLY. These actions ONLY save / re-status rows in
// claude_code_tasks. They NEVER:
//   - execute Claude Code
//   - call the Claude / Anthropic API
//   - spawn a process (no child_process / spawn / exec)
//   - run a local worker
//   - call Seedance / Enhancor / Audio Fixer / any paid API
//   - send email or publish anything
//   - touch any table other than claude_code_tasks
//
// FAIL-SOFT: if migration 010 is not applied yet, the insert/update
// fails with a missing-relation error and the action returns a clear
// "apply migration 010" message WITHOUT throwing — the Phase 2K
// copy-only handoff keeps working regardless.

"use server";

import { revalidatePath } from "next/cache";
import { getDataSource } from "@/lib/data/_source";
import {
  getServiceRoleSupabase,
  hasSupabaseEnv,
} from "@/lib/supabase/server";
import { getCurrentPersona } from "@/lib/auth/persona";
import { getDefaultWorkspaceId } from "@/lib/data/_source";

export interface SaveClaudeCodeTaskInput {
  inboxItemKind: string;
  taskType: string;
  riskLevel: "info_only" | "read_only" | "draft_write" | "gated_paid";
  title: string;
  instructions: string;
  safetyRules: string[];
  expectedOutputs: string[];
  relatedLinks: string[];
  /** Source ids so the operator (and a later worker) can trace the
   *  task back to the inbox row. */
  context: {
    inboxItemId?: string;
    contentItemId?: string | null;
    generationJobId?: string;
    agentRunId?: string;
    regenerationRequestId?: string;
  };
}

export interface ClaudeCodeTaskActionResult {
  ok: boolean;
  error?: string;
  message?: string;
  taskId?: string;
  status?: string;
  /** True when the failure is specifically "migration 010 not applied". */
  migrationMissing?: boolean;
}

const RISK_LEVELS = new Set([
  "info_only",
  "read_only",
  "draft_write",
  "gated_paid",
]);
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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

function looksMissing(message: string): boolean {
  return /claude_code_tasks/i.test(message) &&
    /does not exist|not found|schema cache|42P01|PGRST205/i.test(message);
}

export async function savePreparedClaudeCodeTaskAction(
  input: SaveClaudeCodeTaskInput,
): Promise<ClaudeCodeTaskActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };
  const createdBy = "profileId" in auth ? auth.profileId : null;

  if (!input.title?.trim() || !input.instructions?.trim()) {
    return { ok: false, error: "Task title and instructions are required." };
  }
  if (!RISK_LEVELS.has(input.riskLevel)) {
    return { ok: false, error: "Invalid risk level." };
  }
  if (getDataSource() === "demo") {
    return {
      ok: false,
      error:
        "Demo mode does not persist Claude Code tasks. Switch "
        + "NEXT_PUBLIC_DATA_SOURCE=supabase.",
    };
  }

  try {
    const admin = getServiceRoleSupabase();
    const workspaceId = getDefaultWorkspaceId();
    const { data, error } = await admin
      .from("claude_code_tasks")
      .insert({
        workspace_id: workspaceId,
        inbox_item_kind: input.inboxItemKind,
        task_type: input.taskType,
        risk_level: input.riskLevel,
        status: "draft",
        title: input.title,
        instructions: input.instructions,
        safety_rules: input.safetyRules ?? [],
        expected_outputs: input.expectedOutputs ?? [],
        related_links: input.relatedLinks ?? [],
        context: input.context ?? {},
        created_by: createdBy,
      })
      .select("id, status")
      .maybeSingle();
    if (error) {
      if (looksMissing(error.message)) {
        return {
          ok: false,
          migrationMissing: true,
          error:
            "claude_code_tasks not found — apply migration 010 "
            + "(supabase/migrations/010_claude_code_tasks.sql) first. The "
            + "copy-only handoff still works without it.",
        };
      }
      return { ok: false, error: error.message };
    }
    const row = data as { id: string; status: string } | null;
    if (!row) return { ok: false, error: "Insert returned no id." };
    revalidatePath("/agency/claude-tasks");
    revalidatePath("/agency/inbox");
    return {
      ok: true,
      taskId: row.id,
      status: row.status,
      message:
        "Task saved as draft. This did NOT execute Claude Code — paste "
        + "the prompt into your own session, then mark the task ready.",
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export async function markClaudeCodeTaskReadyAction(input: {
  taskId: string;
}): Promise<ClaudeCodeTaskActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };
  if (!UUID_RE.test(input.taskId ?? "")) {
    return { ok: false, error: "Invalid task id." };
  }
  if (getDataSource() === "demo") {
    return { ok: false, error: "Demo mode does not persist tasks." };
  }
  try {
    const admin = getServiceRoleSupabase();
    // Only a draft can be promoted to ready_for_claude. This is a
    // status transition only — it executes nothing.
    const { data, error } = await admin
      .from("claude_code_tasks")
      .update({ status: "ready_for_claude" })
      .eq("id", input.taskId)
      .eq("status", "draft")
      .select("id, status")
      .maybeSingle();
    if (error) {
      if (looksMissing(error.message)) {
        return {
          ok: false,
          migrationMissing: true,
          error: "claude_code_tasks not found — apply migration 010.",
        };
      }
      return { ok: false, error: error.message };
    }
    const row = data as { id: string; status: string } | null;
    if (!row) {
      return {
        ok: false,
        error: "Task not found or not in 'draft' status.",
      };
    }
    revalidatePath("/agency/claude-tasks");
    return {
      ok: true,
      taskId: row.id,
      status: row.status,
      message:
        "Marked ready_for_claude. Still NOT executed — run Claude Code "
        + "yourself, then mark completed/failed when done.",
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

// ===========================================================================
// Phase 2M — manual complete / fail / cancel.
//
// These ONLY record an outcome the operator already produced by running
// Claude Code in their own session. They write status / result_summary
// / error_message / completed_at on claude_code_tasks ONLY. They never
// execute Claude Code, call the Claude API, spawn a process, run a
// worker, call a paid API, email, publish, or touch any other table.
// ===========================================================================

export async function completeClaudeCodeTaskAction(input: {
  taskId: string;
  resultSummary: string;
}): Promise<ClaudeCodeTaskActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };
  if (!UUID_RE.test(input.taskId ?? "")) {
    return { ok: false, error: "Invalid task id." };
  }
  const summary = (input.resultSummary ?? "").trim();
  if (!summary) {
    return { ok: false, error: "A result summary is required." };
  }
  if (summary.length > 8000) {
    return { ok: false, error: "Result summary too long (8000 cap)." };
  }
  if (getDataSource() === "demo") {
    return { ok: false, error: "Demo mode does not persist tasks." };
  }
  try {
    const admin = getServiceRoleSupabase();
    // Only ready_for_claude / in_progress tasks can be completed. This
    // is a status + text write — it executes nothing.
    const { data, error } = await admin
      .from("claude_code_tasks")
      .update({
        status: "completed",
        result_summary: summary,
        error_message: null,
        completed_at: new Date().toISOString(),
      })
      .eq("id", input.taskId)
      .in("status", ["ready_for_claude", "in_progress"])
      .select("id, status")
      .maybeSingle();
    if (error) {
      if (looksMissing(error.message)) {
        return {
          ok: false,
          migrationMissing: true,
          error: "claude_code_tasks not found — apply migration 010.",
        };
      }
      return { ok: false, error: error.message };
    }
    const row = data as { id: string; status: string } | null;
    if (!row) {
      return {
        ok: false,
        error:
          "Task not found or not in a completable status "
          + "(ready_for_claude / in_progress).",
      };
    }
    revalidatePath("/agency/claude-tasks");
    return {
      ok: true,
      taskId: row.id,
      status: row.status,
      message:
        "Recorded as completed. This only logged the outcome — nothing "
        + "was executed by the dashboard.",
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export async function failClaudeCodeTaskAction(input: {
  taskId: string;
  errorMessage: string;
}): Promise<ClaudeCodeTaskActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };
  if (!UUID_RE.test(input.taskId ?? "")) {
    return { ok: false, error: "Invalid task id." };
  }
  const msg = (input.errorMessage ?? "").trim();
  if (!msg) {
    return { ok: false, error: "A failure reason is required." };
  }
  if (msg.length > 8000) {
    return { ok: false, error: "Failure reason too long (8000 cap)." };
  }
  if (getDataSource() === "demo") {
    return { ok: false, error: "Demo mode does not persist tasks." };
  }
  try {
    const admin = getServiceRoleSupabase();
    const { data, error } = await admin
      .from("claude_code_tasks")
      .update({
        status: "failed",
        error_message: msg,
        completed_at: new Date().toISOString(),
      })
      .eq("id", input.taskId)
      .in("status", ["ready_for_claude", "in_progress"])
      .select("id, status")
      .maybeSingle();
    if (error) {
      if (looksMissing(error.message)) {
        return {
          ok: false,
          migrationMissing: true,
          error: "claude_code_tasks not found — apply migration 010.",
        };
      }
      return { ok: false, error: error.message };
    }
    const row = data as { id: string; status: string } | null;
    if (!row) {
      return {
        ok: false,
        error:
          "Task not found or not in a failable status "
          + "(ready_for_claude / in_progress).",
      };
    }
    revalidatePath("/agency/claude-tasks");
    return {
      ok: true,
      taskId: row.id,
      status: row.status,
      message:
        "Recorded as failed. This only logged the outcome — nothing "
        + "was executed by the dashboard.",
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export async function cancelClaudeCodeTaskAction(input: {
  taskId: string;
}): Promise<ClaudeCodeTaskActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };
  if (!UUID_RE.test(input.taskId ?? "")) {
    return { ok: false, error: "Invalid task id." };
  }
  if (getDataSource() === "demo") {
    return { ok: false, error: "Demo mode does not persist tasks." };
  }
  try {
    const admin = getServiceRoleSupabase();
    // Soft-delete: only an un-started task (draft / ready_for_claude)
    // can be cancelled. Never a physical delete.
    const { data, error } = await admin
      .from("claude_code_tasks")
      .update({ status: "cancelled" })
      .eq("id", input.taskId)
      .in("status", ["draft", "ready_for_claude"])
      .select("id, status")
      .maybeSingle();
    if (error) {
      if (looksMissing(error.message)) {
        return {
          ok: false,
          migrationMissing: true,
          error: "claude_code_tasks not found — apply migration 010.",
        };
      }
      return { ok: false, error: error.message };
    }
    const row = data as { id: string; status: string } | null;
    if (!row) {
      return {
        ok: false,
        error:
          "Task not found or not cancellable "
          + "(only draft / ready_for_claude).",
      };
    }
    revalidatePath("/agency/claude-tasks");
    return {
      ok: true,
      taskId: row.id,
      status: row.status,
      message: "Task cancelled (soft-delete; the row is kept for audit).",
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}
