// Yuvo Studio — Phase 5C client-safe visual preview server actions.
//
// FAIL-SOFT STUBS. The action shape the future Phase 5D real
// implementation will use is locked HERE so the dashboard + CLI
// + tests agree on the contract today. Until migration 012 + the R2
// binding land, every action:
//   - requires operator persona
//   - calls the schema readiness detector
//   - if the schema isn't applied, returns a clear failure
//   - if the schema IS applied (future state), STILL refuses with
//     "not implemented in Phase 5C" so a misconfigured environment
//     never accidentally writes a row or flips a share flag
//
// HARD RULES:
//   - NEVER writes a creative_assets row.
//   - NEVER writes client_safe_visual_url (any column).
//   - NEVER flips a share flag / shared_with_visual_client.
//   - NEVER uploads a file.
//   - NEVER calls Seedance / Enhancor / Audio Fixer / OpenAI /
//     Anthropic / any image-gen API / paid API / fetch() to a
//     storage provider.
//   - NEVER touches anything in /client/*.
//   - NEVER throws on missing table/column; uses the schema detector.

"use server";

import { getDataSource } from "@/lib/data/_source";
import { hasSupabaseEnv } from "@/lib/supabase/server";
import { getCurrentPersona } from "@/lib/auth/persona";
import {
  checkVisualPreviewSchemaReadiness,
  type VisualPreviewSchemaStatus,
} from "@/lib/data/visual-preview-schema";

export interface ClientVisualPreviewActionResult {
  ok: boolean;
  error?: string;
  /** Human-readable message the UI shows next to the disabled
   *  controls. Always populated, even on success-shaped responses. */
  message: string;
  /** Snapshot of the schema-readiness state at action invocation
   *  time. Lets the panel re-render its disabled copy without a
   *  second round-trip. */
  schema: VisualPreviewSchemaStatus | null;
  /** Phase tag so a future caller knows whether to retry. */
  phase: "5c";
}

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

function refuseSchemaNotReady(
  schema: VisualPreviewSchemaStatus,
): ClientVisualPreviewActionResult {
  return {
    ok: false,
    error: schema.message,
    message:
      "Visual client sharing is not enabled yet. Migration 012 and " +
      "R2 storage must be applied first.",
    schema,
    phase: "5c",
  };
}

function refuseNotImplementedYet(
  schema: VisualPreviewSchemaStatus,
): ClientVisualPreviewActionResult {
  return {
    ok: false,
    error:
      "Action is not implemented in Phase 5C. The real write path " +
      "lands in Phase 5D (or later) behind operator approval of " +
      "migration 012 + the R2 binding + the upload pipe.",
    message:
      "Schema is detected, but the dashboard does not write yet. " +
      "Phase 5C is fail-soft scaffold only.",
    schema,
    phase: "5c",
  };
}

async function gate(): Promise<
  ClientVisualPreviewActionResult | { schema: VisualPreviewSchemaStatus }
> {
  const auth = await requireOperator();
  if (auth !== true) {
    return {
      ok: false,
      error: auth.error,
      message: auth.error,
      schema: null,
      phase: "5c",
    };
  }
  // The schema detector is fail-soft; on any error it returns
  // `not_configured`. Never throws.
  const schema = await checkVisualPreviewSchemaReadiness();
  if (!schema.ready) {
    return refuseSchemaNotReady(schema);
  }
  // Schema is ready, but Phase 5C does NOT enable writes.
  return { schema };
}

export interface PrepareClientVisualPreviewInput {
  contentItemId: string;
  /** Future shape: the operator-resolved storage URL the dashboard
   *  uploaded the PNG to. Phase 5C ignores this — only validates
   *  enough to surface a useful refusal message. */
  visualUrl?: string;
  thumbnailUrl?: string;
  notes?: string;
}

export async function prepareClientVisualPreviewAction(
  input: PrepareClientVisualPreviewInput,
): Promise<ClientVisualPreviewActionResult> {
  // Cheap arg validation before the schema probe — keeps the
  // refusal message specific.
  if (!UUID_RE.test(input.contentItemId ?? "")) {
    return {
      ok: false,
      error: "Invalid content item id.",
      message: "Invalid content item id.",
      schema: null,
      phase: "5c",
    };
  }
  const gated = await gate();
  if ("ok" in gated) return gated;
  return refuseNotImplementedYet(gated.schema);
}

export interface ShareVisualPreviewInput {
  contentItemId: string;
}

export async function shareVisualPreviewWithClientAction(
  input: ShareVisualPreviewInput,
): Promise<ClientVisualPreviewActionResult> {
  if (!UUID_RE.test(input.contentItemId ?? "")) {
    return {
      ok: false,
      error: "Invalid content item id.",
      message: "Invalid content item id.",
      schema: null,
      phase: "5c",
    };
  }
  const gated = await gate();
  if ("ok" in gated) return gated;
  return refuseNotImplementedYet(gated.schema);
}

export interface ResetClientVisualPreviewInput {
  contentItemId: string;
}

export async function resetClientVisualPreviewAction(
  input: ResetClientVisualPreviewInput,
): Promise<ClientVisualPreviewActionResult> {
  if (!UUID_RE.test(input.contentItemId ?? "")) {
    return {
      ok: false,
      error: "Invalid content item id.",
      message: "Invalid content item id.",
      schema: null,
      phase: "5c",
    };
  }
  const gated = await gate();
  if ("ok" in gated) return gated;
  return refuseNotImplementedYet(gated.schema);
}
