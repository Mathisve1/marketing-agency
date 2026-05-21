// Yuvo Studio — Phase 5C visual-preview schema readiness detector.
//
// READ-ONLY, FAIL-SOFT. Detects whether the schema (migration 012 or
// the fallback 011) is applied so the dashboard can disable the
// client-share lifecycle gracefully when it isn't. Probes through
// PostgREST with `.select('id').limit(1)`; on a missing-relation /
// missing-column error (42P01 / PGRST205 / PGRST204) the detector
// returns a "not configured" status instead of throwing.
//
// HARD RULES:
//   - No DB writes. No `.insert/.update/.delete/.upsert/.rpc`.
//   - No table/view DDL. No migration apply.
//   - No fetch outside the existing Supabase reader.
//   - In demo mode (`getDataSource() === "demo"`) the detector
//     short-circuits to `not_configured` with a clear message; no
//     PostgREST round-trip happens.

import { getSupabaseServerClient } from "@/lib/supabase/client";
import { getDataSource } from "./_source";

export type VisualPreviewSchemaStrategy =
  | "creative_assets"
  | "content_items_extension"
  | "not_configured";

export interface VisualPreviewSchemaStatus {
  ready: boolean;
  strategy: VisualPreviewSchemaStrategy;
  /** Human-readable list of things that are still missing. Empty
   *  when `ready === true`. */
  missing: string[];
  /** Single human-readable summary line the UI can render. */
  message: string;
  /** Internals — useful for tests and the storage-status panel. */
  detection: {
    checkedCreativeAssets: boolean;
    creativeAssetsExists: boolean;
    checkedContentItemsVisualColumns: boolean;
    contentItemsVisualColumnsExist: boolean;
    checkedClientView: boolean;
    clientViewExists: boolean;
  };
}

/** True when the PostgREST error code indicates the table or column
 *  simply does not exist. Treat as a "schema not applied" signal,
 *  never throw. Mirrors `isMissingRelation` in
 *  `web/lib/data/claude-code-tasks.ts`. */
function isMissingRelationOrColumn(err: unknown): boolean {
  const e = err as { code?: string; message?: string } | undefined;
  if (!e) return false;
  // Postgres: 42P01 (undefined_table) / 42703 (undefined_column).
  // PostgREST: PGRST205 (schema cache miss) / PGRST204 (column not
  // found in schema cache).
  if (
    e.code === "42P01" ||
    e.code === "42703" ||
    e.code === "PGRST205" ||
    e.code === "PGRST204"
  ) {
    return true;
  }
  if (!e.message) return false;
  const lower = e.message.toLowerCase();
  return (
    lower.includes("does not exist") ||
    lower.includes("not found") ||
    lower.includes("schema cache")
  );
}

async function tableExists(tableName: string): Promise<boolean> {
  const supabase = getSupabaseServerClient();
  const { error } = await supabase.from(tableName).select("id").limit(1);
  if (!error) return true;
  if (isMissingRelationOrColumn(error)) return false;
  // For any *other* error (RLS denied, network blip, etc.) we
  // conservatively return false — the dashboard then shows "not
  // configured" and the operator can investigate.
  return false;
}

async function contentItemsHasVisualColumns(): Promise<boolean> {
  const supabase = getSupabaseServerClient();
  // Use the dedicated column list so a missing column returns the
  // PostgREST PGRST204 / undefined_column error rather than the
  // table-existence error.
  const { error } = await supabase
    .from("content_items")
    .select("client_safe_visual_url, shared_with_visual_client")
    .limit(1);
  if (!error) return true;
  if (isMissingRelationOrColumn(error)) return false;
  return false;
}

async function clientCreativeAssetsViewExists(): Promise<boolean> {
  // PostgREST exposes views the same way as tables. If 012 is applied
  // but the view block at the end of the migration was skipped, the
  // table query above will succeed but this one will fail with
  // 42P01 / PGRST205 — exactly the signal we want.
  return tableExists("client_creative_assets_v");
}

/** Full readiness check. Read-only. Never throws. */
export async function checkVisualPreviewSchemaReadiness(): Promise<VisualPreviewSchemaStatus> {
  const empty: VisualPreviewSchemaStatus = {
    ready: false,
    strategy: "not_configured",
    missing: [],
    message: "",
    detection: {
      checkedCreativeAssets: false,
      creativeAssetsExists: false,
      checkedContentItemsVisualColumns: false,
      contentItemsVisualColumnsExist: false,
      checkedClientView: false,
      clientViewExists: false,
    },
  };

  // Demo mode never has either schema — short-circuit so we don't
  // even hit PostgREST.
  if (getDataSource() === "demo") {
    return {
      ...empty,
      missing: [
        "Demo data source — visual sharing requires Supabase mode.",
      ],
      message:
        "Demo mode: client visual sharing is disabled by design.",
    };
  }

  // Probe 012 first (the canonical model).
  const creativeAssetsExists = await tableExists("creative_assets");
  empty.detection.checkedCreativeAssets = true;
  empty.detection.creativeAssetsExists = creativeAssetsExists;

  if (creativeAssetsExists) {
    const clientViewExists = await clientCreativeAssetsViewExists();
    empty.detection.checkedClientView = true;
    empty.detection.clientViewExists = clientViewExists;
    const missing: string[] = [];
    if (!clientViewExists) {
      missing.push(
        "client_creative_assets_v view (apply the view block at the " +
          "end of migration 012)",
      );
    }
    // Even with both pieces, Phase 5C does NOT enable writes. The
    // detector reports `ready: true` for *schema* readiness; the
    // server actions then refuse with "not implemented in Phase 5C".
    return {
      ready: missing.length === 0,
      strategy: "creative_assets",
      missing,
      message:
        missing.length === 0
          ? "creative_assets table + client view detected. Server " +
            "actions still refuse writes in Phase 5C."
          : "creative_assets table detected, but the client view is " +
            "missing — apply migration 012's view block.",
      detection: empty.detection,
    };
  }

  // Fall back to 011 (content_items extension).
  const visualColsExist = await contentItemsHasVisualColumns();
  empty.detection.checkedContentItemsVisualColumns = true;
  empty.detection.contentItemsVisualColumnsExist = visualColsExist;

  if (visualColsExist) {
    return {
      ready: false, // Phase 5C does not unlock writes under either path.
      strategy: "content_items_extension",
      missing: [
        "Phase 5C does not enable writes against the 011 fallback. " +
          "Migrate to 012 for the canonical model.",
      ],
      message:
        "content_items visual columns detected (migration 011 / " +
        "Option A). Phase 5C recommends migrating to 012.",
      detection: empty.detection,
    };
  }

  return {
    ready: false,
    strategy: "not_configured",
    missing: [
      "creative_assets table (migration 012 — apply in Phase 5C+)",
      "client_creative_assets_v view (Phase 5C+)",
      "R2 bucket binding `VISUAL_ASSETS_BUCKET` (Phase 5C+)",
    ],
    message:
      "Visual sharing is not enabled yet. Migration 012 and R2 storage " +
      "must be applied first.",
    detection: empty.detection,
  };
}

/** Compact wrapper for UI panels that only need the strategy +
 *  ready flag (no PostgREST round-trip if the caller already has a
 *  status). */
export function getVisualPreviewSchemaStatus(
  status: VisualPreviewSchemaStatus,
): { ready: boolean; strategy: VisualPreviewSchemaStrategy } {
  return { ready: status.ready, strategy: status.strategy };
}
