// Yuvo Studio — Phase 1C data-source feature flag.
//
// All reads in the UI go through `web/lib/data/*` instead of importing
// `web/lib/demo-data` directly. That gives us one switch — the
// `NEXT_PUBLIC_DATA_SOURCE` env var — that decides whether the app is
// fed by the local Phase 1A demo seed or the Supabase schema landed in
// `supabase/migrations/001_initial_dashboard_schema.sql` (+ 002).
//
// Phase 1C wires BOTH branches:
//   - `demo`     → in-memory data from `web/lib/demo-data.ts` (DEFAULT)
//   - `supabase` → live reads via `@supabase/supabase-js`
//                  (see web/lib/supabase/client.ts and ./mappers.ts)
//
// Supabase Auth is NOT wired in Phase 1C. RLS denies everything when
// `auth.uid()` is null, so the supabase branch returns empty data
// UNLESS the operator provides `SUPABASE_SERVICE_ROLE_KEY` (server-only,
// bypasses RLS). See docs/dashboard_phase_1c_supabase_data_access.md.

export type DataSource = "demo" | "supabase";

/** Reads NEXT_PUBLIC_DATA_SOURCE. Defaults to "demo" so a fresh checkout
 *  with no env file renders Pai content out of the box. */
export function getDataSource(): DataSource {
  const raw =
    typeof process !== "undefined" ? process.env.NEXT_PUBLIC_DATA_SOURCE : undefined;
  if (raw === "supabase") return "supabase";
  return "demo";
}

/** Thrown by data fns when a Supabase query errors out. Wrapped in a
 *  custom Error subclass so callers / tests can distinguish provider
 *  errors from logic errors. */
export class SupabaseDataError extends Error {
  constructor(
    public readonly fn: string,
    public readonly cause: unknown,
  ) {
    const causeMsg =
      cause && typeof cause === "object" && "message" in cause
        ? (cause as { message: string }).message
        : String(cause);
    super(`[yuvo-data] ${fn}() failed against Supabase: ${causeMsg}`);
    this.name = "SupabaseDataError";
  }
}

/** Legacy guard from Phase 1B. Phase 1C no longer reaches this; it is
 *  kept as a defensive backstop in case a new data fn is added without
 *  a supabase branch. */
export function unsupportedSupabasePath(fn: string): never {
  throw new Error(
    `[yuvo-data] ${fn}() does not have a Supabase branch yet. ` +
      `Add one alongside the demo branch in web/lib/data/*. ` +
      `Set NEXT_PUBLIC_DATA_SOURCE=demo to fall back to the demo seed.`,
  );
}

/** Seeded Yuvo Studio workspace uuid — matches the literal in
 *  supabase/seed.sql (workspaces row id). Kept as a single source of
 *  truth so future migrations that re-seed the workspace can update one
 *  place. */
export const SEEDED_WORKSPACE_ID =
  "11111111-1111-1111-1111-111111111111" as const;

/** Pre-auth fallback workspace id. Returns:
 *    - `"ws_yuvo"` in demo mode (string id used by web/lib/demo-data.ts)
 *    - `SEEDED_WORKSPACE_ID` in supabase mode (the seed.sql uuid)
 *
 *  Use this whenever an operator-side page needs *a* workspace id but
 *  the persona-resolver returned null (e.g. Phase 1D auth is not yet
 *  configured because the anon key is missing, or the page renders
 *  before sign-in). Pages that have a real signed-in operator should
 *  prefer `getCurrentPersona().workspaceIds[0]` and only fall back to
 *  this helper when that lookup is null.
 *
 *  TODO(phase-1k): once the operator can manage multiple workspaces,
 *  replace this fallback with a workspace-picker stored in a cookie.
 */
export function getDefaultWorkspaceId(): string {
  return getDataSource() === "supabase" ? SEEDED_WORKSPACE_ID : "ws_yuvo";
}
