// Yuvo Studio — Phase 1D server-side Supabase clients.
//
// Two factories specific to Next.js App Router server contexts (server
// components, server actions, route handlers). Both wire `@supabase/ssr`
// so Supabase session cookies are read + written via Next.js's
// `cookies()` helper. The previous Phase 1C client
// (`web/lib/supabase/client.ts`) is still used for non-auth fallback
// paths and tests.
//
//   getServerSupabase()
//     - Anon key + per-request cookies. `auth.uid()` returns the real
//       session user once a magic-link has been completed. RLS applies.
//     - Use this from server components / actions that read on behalf of
//       the signed-in user (operator or client).
//
//   getServiceRoleSupabase()
//     - Service-role key, NO cookies. BYPASSES RLS. Use ONLY from server
//       actions / route handlers that have already validated the caller's
//       persona + ownership in app code. Throws if
//       SUPABASE_SERVICE_ROLE_KEY is unset.
//     - Required because Phase 1D writes (approve, request-changes,
//       comment) update statuses on rows the client persona can't
//       UPDATE directly under the Phase 1B RLS posture. We re-prove
//       authorization in code, then write with service-role.

import { cookies } from "next/headers";
import { createServerClient } from "@supabase/ssr";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

function readEnv(name: string): string | undefined {
  if (typeof process === "undefined") return undefined;
  const v = process.env[name];
  return v && v.trim() !== "" ? v : undefined;
}

function requireEnv(name: string): string {
  const v = readEnv(name);
  if (!v) {
    throw new Error(
      `[yuvo-supabase] Missing required env var ${name}. ` +
        `See web/.env.example. Phase 1D Supabase auth requires it.`,
    );
  }
  return v;
}

/** Returns true when both Supabase env vars are present. Lets callers
 *  short-circuit before throwing in places where missing vars are
 *  expected (e.g. demo mode pages that incidentally import this). */
export function hasSupabaseEnv(): boolean {
  return (
    !!readEnv("NEXT_PUBLIC_SUPABASE_URL") &&
    !!readEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
  );
}

/** Server-side Supabase client wired to Next.js cookies. */
export async function getServerSupabase(): Promise<SupabaseClient> {
  const url = requireEnv("NEXT_PUBLIC_SUPABASE_URL");
  const anonKey = requireEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY");
  const cookieStore = await cookies();

  return createServerClient(url, anonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        // Server components cannot set cookies directly; the cookie
        // writes are committed by server actions / route handlers.
        // The try/catch makes the read path safe everywhere.
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          /* read-only context — ignore */
        }
      },
    },
  });
}

/** Service-role client. RLS-bypassing. Server-only. */
export function getServiceRoleSupabase(): SupabaseClient {
  const url = requireEnv("NEXT_PUBLIC_SUPABASE_URL");
  const serviceKey = readEnv("SUPABASE_SERVICE_ROLE_KEY");
  if (!serviceKey) {
    throw new Error(
      "[yuvo-supabase] SUPABASE_SERVICE_ROLE_KEY is required for this write " +
        "path. See web/.env.example. Until per-row UPDATE policies for the " +
        "client persona land in Phase 1E, server actions need the service-role " +
        "to flip content_items.status.",
    );
  }
  return createClient(url, serviceKey, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
  });
}
