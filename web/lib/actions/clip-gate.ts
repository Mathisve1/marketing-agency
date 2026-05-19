// Yuvo Studio — Phase 1S per-clip submit gate (DRY-RUN ONLY).
//
// OPERATOR-ONLY. This action NEVER calls Enhancor / Seedance / Audio
// Fixer and NEVER writes to the database. It shells the read-only
// scripts/clip_dry_run.py, which by construction:
//   - has no --submit mode and never builds an EnhancorSeedanceProvider
//   - issues only read-only PostgREST GETs
//   - forces PLACEHOLDER product/influencer URLs (non-submittable)
//   - enforces the clip-sequencing rule (clip N blocked until clip N-1
//     is completed)
//
// Paid submission is intentionally NOT wired here. For multi-clip
// Supabase jobs it remains a separate, explicit, operator-driven CLI
// step (documented in the UI panel). Stopping at dry-run is the
// approved Phase 1S scope.

"use server";

import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import { getDataSource } from "@/lib/data/_source";
import { hasSupabaseEnv } from "@/lib/supabase/server";
import { getCurrentPersona } from "@/lib/auth/persona";

const execFileAsync = promisify(execFile);

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export interface ClipDryRunResult {
  ok: boolean;
  /** Parsed JSON summary from the script (first JSON object printed). */
  summary?: unknown;
  /** "ok" | "checks_failed" | "blocked" | "error" — from the script. */
  result?: string;
  blocked?: boolean;
  message?: string;
  /** Full script stdout for the operator to audit. */
  stdout?: string;
  error?: string;
}

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

/**
 * Build + validate the Seedance wire payload for ONE draft clip job.
 * No paid call. No DB write. Returns the script's JSON summary + the
 * raw stdout. Exit codes from clip_dry_run.py:
 *   0 = ok (all checks pass)
 *   2 = error (job/prompt not found, payload invalid)
 *   3 = blocked (not draft, or prior clip not completed)
 *   4 = checks_failed (payload built but a validation check failed)
 */
export async function prepareClipDryRunAction(input: {
  jobId: string;
}): Promise<ClipDryRunResult> {
  const auth = await requireOperator();
  if (auth !== true) return { ok: false, error: auth.error };

  const jobId = (input.jobId ?? "").trim();
  if (!UUID_RE.test(jobId)) {
    return { ok: false, error: "Invalid job id." };
  }

  // The Next app runs with cwd = <repo>/web; the script lives at the
  // repo root. execFile with an arg array (no shell) — jobId is also
  // UUID-validated above, so there is no command-injection surface.
  const repoRoot = path.resolve(process.cwd(), "..");
  const scriptPath = path.join("scripts", "clip_dry_run.py");

  let stdout = "";
  let exitCode = 0;
  try {
    const r = await execFileAsync(
      "py",
      ["-3.11", scriptPath, "--job-id", jobId],
      { cwd: repoRoot, timeout: 60_000, maxBuffer: 4 * 1024 * 1024 },
    );
    stdout = r.stdout;
  } catch (err) {
    const e = err as NodeJS.ErrnoException & {
      stdout?: string;
      code?: number | string;
    };
    // Non-zero exit (blocked / checks_failed / error) still prints a
    // JSON summary to stdout — capture it rather than failing hard.
    stdout = e.stdout ?? "";
    exitCode = typeof e.code === "number" ? e.code : 1;
    if (!stdout) {
      return {
        ok: false,
        error:
          "Could not run the dry-run script. Ensure Python 3.11 (`py "
          + "-3.11`) is available on the dashboard host.",
      };
    }
  }

  // The script prints a JSON object first, then a payload preview. Parse
  // the first balanced JSON block for a structured summary.
  let summary: unknown;
  let resultKind: string | undefined;
  let message: string | undefined;
  const start = stdout.indexOf("{");
  if (start !== -1) {
    let depth = 0;
    let end = -1;
    for (let i = start; i < stdout.length; i++) {
      const c = stdout[i];
      if (c === "{") depth++;
      else if (c === "}") {
        depth--;
        if (depth === 0) {
          end = i;
          break;
        }
      }
    }
    if (end !== -1) {
      try {
        summary = JSON.parse(stdout.slice(start, end + 1));
        const s = summary as { result?: string; message?: string };
        resultKind = s.result;
        message = s.message;
      } catch {
        /* leave summary undefined; raw stdout is still returned */
      }
    }
  }

  const blocked = resultKind === "blocked";
  return {
    ok: exitCode === 0 && resultKind === "ok",
    summary,
    result: resultKind,
    blocked,
    message,
    stdout,
  };
}
