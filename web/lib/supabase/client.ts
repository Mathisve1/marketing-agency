// Yuvo Studio — Phase 1C Supabase client helpers.
//
// Two factories. Both are memoized per process to avoid spinning up a
// fresh fetch keepalive pool on every request.
//
//   getSupabaseAnonClient()
//     - Uses NEXT_PUBLIC_SUPABASE_ANON_KEY.
//     - Safe to use in both server and (future) browser contexts.
//     - Queries run under Row-Level-Security from migration 002. Phase 1C
//       does NOT wire Supabase Auth, so `auth.uid()` is null inside RLS
//       helpers, and policies will deny everything by default. This is
//       why the supabase branch returns empty results until either
//       (a) auth lands, or (b) the service-role key is provided below.
//
//   getSupabaseServerClient()
//     - Uses SUPABASE_SERVICE_ROLE_KEY when present (server-only env var,
//       NOT prefixed with NEXT_PUBLIC_). Service role BYPASSES RLS and is
//       only intended for the operator's own machine until Phase 1C wires
//       real auth. This unlocks the supabase branch end-to-end so the
//       operator can verify the wiring without auth.
//     - Falls back to the anon client when SUPABASE_SERVICE_ROLE_KEY is
//       absent. In that case operator pages will see empty arrays (RLS
//       denies everything without auth).
//     - MUST NOT be imported from a "use client" file. The factory checks
//       `typeof window` and throws if called in the browser to make a
//       leak loud.
//
// All three env vars are documented in web/.env.example.

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let _anonClient: SupabaseClient | null = null;
let _serverClient: SupabaseClient | null = null;

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
        `See web/.env.example for the full list. While ` +
        `NEXT_PUBLIC_DATA_SOURCE=demo (the default) this is not needed.`,
    );
  }
  return v;
}

/** Anon-key client. Honours RLS. Safe in the browser. */
export function getSupabaseAnonClient(): SupabaseClient {
  if (_anonClient) return _anonClient;
  const url = requireEnv("NEXT_PUBLIC_SUPABASE_URL");
  const anonKey = requireEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY");
  _anonClient = createClient(url, anonKey, {
    auth: {
      // Phase 1C does not wire Supabase Auth. Disable session storage so
      // the client never tries to write tokens to localStorage on the
      // (future) browser path.
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
  });
  return _anonClient;
}

/** Server-only client. Uses the service-role key when available
 *  (BYPASSES RLS); otherwise behaves like the anon client. NEVER call
 *  this from a "use client" module. */
export function getSupabaseServerClient(): SupabaseClient {
  if (typeof window !== "undefined") {
    throw new Error(
      "[yuvo-supabase] getSupabaseServerClient() must not run in the browser. " +
        "Use getSupabaseAnonClient() in client components instead.",
    );
  }
  if (_serverClient) return _serverClient;

  const url = requireEnv("NEXT_PUBLIC_SUPABASE_URL");
  const serviceKey = readEnv("SUPABASE_SERVICE_ROLE_KEY");

  if (serviceKey) {
    _serverClient = createClient(url, serviceKey, {
      auth: {
        persistSession: false,
        autoRefreshToken: false,
        detectSessionInUrl: false,
      },
    });
    return _serverClient;
  }

  // No service-role key → fall back to the anon client. Queries will be
  // RLS-denied without auth; this is intentional and documented.
  _serverClient = getSupabaseAnonClient();
  return _serverClient;
}

/** Test-only hook: reset the memoized clients. NOT exported from any
 *  data path; only test setup imports it. */
export function __resetSupabaseClientsForTests(): void {
  _anonClient = null;
  _serverClient = null;
}
