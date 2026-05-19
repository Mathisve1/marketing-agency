// Yuvo Studio — Phase 1D browser-side Supabase client.
//
// Used by "use client" components that need to sign in / sign out via
// magic link. Cookies are managed by `@supabase/ssr` so the session
// stays in sync with the server.
//
// Does NOT have access to the service-role key (would be a critical
// security bug — never reference SUPABASE_SERVICE_ROLE_KEY here).

"use client";

import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

let _browserClient: SupabaseClient | null = null;

export function getBrowserSupabase(): SupabaseClient {
  if (_browserClient) return _browserClient;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) {
    throw new Error(
      "[yuvo-supabase] NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY " +
        "is missing. Browser Supabase client cannot be created.",
    );
  }
  _browserClient = createBrowserClient(url, anonKey);
  return _browserClient;
}
