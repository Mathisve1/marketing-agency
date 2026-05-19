// Yuvo Studio — Phase 1E content_requests action.
//
// One action: createContentRequestAction. Wires the "What would you
// like to see next week?" form on the client portal. Phase 1D shipped
// this form as inert; Phase 1E wires it.
//
// Demo mode pushes the row into the in-memory store in
// web/lib/data/content-requests.ts so the agency dashboard's recent-
// requests panel renders even without Supabase. Supabase mode validates
// the caller is a member of the named portal, then writes via service-
// role for the same reasons as the Phase 1D client-feedback actions
// (the client INSERT policy exists on content_requests, but the
// resolution chain that figures out which brand_id to write is easier
// to express in app code with service-role than as a per-row policy).

"use server";

import { revalidatePath } from "next/cache";
import { randomUUID } from "crypto";
import {
  _demoPushContentRequest,
  _DEMO_PORTAL_BRAND_RESOLVER,
} from "@/lib/data/content-requests";
import { getDataSource } from "@/lib/data/_source";
import {
  getServerSupabase,
  getServiceRoleSupabase,
  hasSupabaseEnv,
} from "@/lib/supabase/server";
import { isPortalMember } from "@/lib/auth/persona";

export interface ContentRequestActionResult {
  ok: boolean;
  message?: string;
  error?: string;
}

const MAX_BODY = 2000;

export async function createContentRequestAction(input: {
  portalSlug: string;
  body: string;
}): Promise<ContentRequestActionResult> {
  const body = (input.body ?? "").trim();
  if (!body) return { ok: false, error: "Please write a short note first." };
  if (body.length > MAX_BODY) {
    return {
      ok: false,
      error: `That's quite long — keep it under ${MAX_BODY} characters.`,
    };
  }

  if (getDataSource() === "demo") {
    const brandId = _DEMO_PORTAL_BRAND_RESOLVER(input.portalSlug) ?? "brand_unknown";
    _demoPushContentRequest({
      id: randomUUID(),
      clientPortalId: input.portalSlug, // demo: slug == id
      brandId,
      profileId: null,
      body,
      createdAt: new Date().toISOString(),
    });
    revalidatePath(`/client/${input.portalSlug}`);
    revalidatePath(`/agency`);
    return {
      ok: true,
      message: "Demo mode — request saved locally (resets on server restart).",
    };
  }

  if (!hasSupabaseEnv()) {
    return { ok: false, error: "Supabase auth is not configured." };
  }

  try {
    const supabase = await getServerSupabase();
    const { data: userData } = await supabase.auth.getUser();
    const user = userData?.user;
    if (!user) return { ok: false, error: "Please sign in first." };

    const isMember = await isPortalMember(user.id, input.portalSlug);
    if (!isMember) {
      return { ok: false, error: "You are not a member of this portal." };
    }

    const admin = getServiceRoleSupabase();

    // Resolve portal_id + brand_id from the portal slug. We walk
    // client_portals → clients → brands rather than denormalising the
    // brand_id onto the portal table.
    const { data: portalRow, error: portalErr } = await admin
      .from("client_portals")
      .select("id, client_id")
      .eq("slug", input.portalSlug)
      .maybeSingle();
    if (portalErr || !portalRow) return { ok: false, error: "Portal not found." };
    const portal = portalRow as { id: string; client_id: string };

    const { data: clientRow, error: clientErr } = await admin
      .from("clients")
      .select("brand_id")
      .eq("id", portal.client_id)
      .maybeSingle();
    if (clientErr || !clientRow) return { ok: false, error: "Client not found." };
    const client = clientRow as { brand_id: string };

    const { error: insertErr } = await admin.from("content_requests").insert({
      client_portal_id: portal.id,
      brand_id: client.brand_id,
      profile_id: user.id,
      body,
    });
    if (insertErr) return { ok: false, error: insertErr.message };

    revalidatePath(`/client/${input.portalSlug}`);
    revalidatePath(`/agency`);
    return { ok: true, message: "Request sent. We'll review it this week." };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}
