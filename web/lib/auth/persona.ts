// Yuvo Studio — Phase 1D persona resolver.
//
// Reads the current Supabase session and classifies the caller into one
// of three personas:
//   - operator   : member of at least one workspace
//   - client     : member of at least one client_portal (and not an operator)
//   - unaffiliated: signed in but neither
//   - null       : not signed in
//
// "operator" wins when a single user is both — operators always see the
// agency console first in Phase 1D. A future preference toggle can flip
// this.
//
// Resolution uses the SERVICE-ROLE client so the persona lookup itself
// is not subject to the very RLS policies it is about to enable. This is
// safe because: (a) we only read the row identified by the verified
// session user id, and (b) the lookup output is the boolean "are you
// a member" — nothing leaks beyond what the user is about to access
// anyway.

import { getServerSupabase, getServiceRoleSupabase, hasSupabaseEnv } from "@/lib/supabase/server";

export type Persona =
  | { kind: "operator"; userId: string; email: string | null; workspaceIds: string[] }
  | { kind: "client"; userId: string; email: string | null; portalIds: string[] }
  | { kind: "unaffiliated"; userId: string; email: string | null }
  | null;

/** Returns the current signed-in user + their persona, or null if no
 *  session exists. In demo mode (no Supabase env vars) returns null. */
export async function getCurrentPersona(): Promise<Persona> {
  if (!hasSupabaseEnv()) return null;

  const supabase = await getServerSupabase();
  const { data: userData, error: userErr } = await supabase.auth.getUser();
  if (userErr || !userData?.user) return null;

  const userId = userData.user.id;
  const email = userData.user.email ?? null;

  // Use service-role for the membership lookup so it isn't blocked by
  // policy recursion (workspace_members + client_portal_members both
  // depend on app.is_workspace_member / app.is_portal_member which read
  // auth.uid()). Service-role is server-only.
  let admin;
  try {
    admin = getServiceRoleSupabase();
  } catch {
    // No service-role configured. Fall back to the user-session client
    // — this works once Phase 1B's RLS lets profile-self reads through,
    // but for membership tables it will return empty until per-policy
    // self-reads are added. Phase 1D documents this limit.
    admin = supabase;
  }

  const [{ data: workspaceRows }, { data: portalRows }] = await Promise.all([
    admin
      .from("workspace_members")
      .select("workspace_id")
      .eq("profile_id", userId),
    admin
      .from("client_portal_members")
      .select("portal_id")
      .eq("profile_id", userId),
  ]);

  const workspaceIds = (workspaceRows ?? [])
    .map((r) => (r as { workspace_id?: string }).workspace_id)
    .filter((id): id is string => typeof id === "string");

  const portalIds = (portalRows ?? [])
    .map((r) => (r as { portal_id?: string }).portal_id)
    .filter((id): id is string => typeof id === "string");

  if (workspaceIds.length > 0) {
    return { kind: "operator", userId, email, workspaceIds };
  }
  if (portalIds.length > 0) {
    return { kind: "client", userId, email, portalIds };
  }
  return { kind: "unaffiliated", userId, email };
}

/** Returns true if the given signed-in user is a member of the given
 *  portal slug. Used by server actions to authorize writes. Uses
 *  service-role to bypass RLS for the lookup itself. */
export async function isPortalMember(
  userId: string,
  portalSlug: string,
): Promise<boolean> {
  if (!hasSupabaseEnv()) return false;
  let admin;
  try {
    admin = getServiceRoleSupabase();
  } catch {
    return false;
  }
  const { data: portalRow } = await admin
    .from("client_portals")
    .select("id")
    .eq("slug", portalSlug)
    .maybeSingle();
  const portalId = (portalRow as { id?: string } | null)?.id;
  if (!portalId) return false;

  const { data: memberRow } = await admin
    .from("client_portal_members")
    .select("portal_id")
    .eq("portal_id", portalId)
    .eq("profile_id", userId)
    .maybeSingle();
  return !!memberRow;
}
