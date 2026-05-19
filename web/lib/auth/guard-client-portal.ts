// Yuvo Studio — Phase 1K client-portal auth guard.
//
// Extracted from the portal layout so the login page (which is a child
// of the same layout) is NOT gated by itself. Each *protected* portal
// page calls this helper at the top of its render; the login page does
// not.
//
// Behaviour:
//   - Demo mode               → no-op (returns immediately).
//   - Supabase env not wired  → no-op (Phase 1D safety: don't lock the
//                               app out when half-configured).
//   - Supabase mode + no session            → redirect to /client/<slug>/login
//   - Supabase mode + operator              → allowed (preview path)
//   - Supabase mode + client persona        → allowed (assumes layout's
//     portal lookup will produce notFound if the slug is unknown)
//   - Supabase mode + unaffiliated session  → redirect to login
//
// Future Phase 1L hardening: pin "client persona belongs to THIS slug"
// instead of "any client persona". For now the supabase branch of
// `getClientPortalBySlug` already gates by what the caller can see;
// the loose check below is the conservative pre-1L behaviour.

import { redirect } from "next/navigation";
import { getCurrentPersona } from "@/lib/auth/persona";
import { getDataSource } from "@/lib/data/_source";
import { hasSupabaseEnv } from "@/lib/supabase/server";

export interface GuardedAccess {
  /** Reported to pages so they can render persona-aware UI without
   *  re-calling the persona resolver. */
  persona: Awaited<ReturnType<typeof getCurrentPersona>>;
  /** True when this request is operating under real auth. */
  authEnabled: boolean;
}

export async function requireClientPortalAccess(
  portalSlug: string,
): Promise<GuardedAccess> {
  const authEnabled =
    getDataSource() === "supabase" && hasSupabaseEnv();
  if (!authEnabled) {
    return { persona: null, authEnabled: false };
  }

  const persona = await getCurrentPersona();
  if (!persona) {
    redirect(
      `/client/${portalSlug}/login?next=/client/${portalSlug}`,
    );
  }
  // Operators can preview any portal (sales / QC). Clients pass if they
  // have any portal membership — slug-specific binding hardens in 1L.
  if (persona.kind === "unaffiliated") {
    redirect(
      `/client/${portalSlug}/login?next=/client/${portalSlug}`,
    );
  }
  return { persona, authEnabled: true };
}
