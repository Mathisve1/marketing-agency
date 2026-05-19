// Yuvo Studio — Phase 1O client-feedback route handler.
//
// WHY THIS EXISTS
// ----------------
// The client feedback writes were originally invoked as Next.js Server
// Actions. Under OpenNext on Cloudflare Workers, Server Actions do NOT
// reliably receive the request's session cookie, so
// `getServerSupabase().auth.getUser()` returned no user, and
// `authorizeClientWrite` threw `auth: not signed in` — the write never
// happened (Phase 1L / 1N "comment didn't land" bug). Server COMPONENTS
// and ROUTE HANDLERS do get the cookie on OpenNext (gated pages render;
// the Phase 1N /api/diag route handler read env + cookies fine).
//
// A Phase 1O local probe proved the entire write chain
// (auth.getUser → isPortalMember → content_items ownership →
// content_feedback insert) is healthy when a valid client identity is
// present. So the fix is purely transport: move the writes to this
// route handler. The existing action functions still hold all the
// logic (demo mode, regeneration_requests, status bump, tagged errors);
// this handler just calls them from a context where cookies work, and
// the client panel fetches this endpoint instead of invoking the
// actions as RPC.

import { NextResponse, type NextRequest } from "next/server";
import {
  approveContentAction,
  commentContentAction,
  requestChangesContentAction,
  type ActionResult,
} from "@/lib/actions/client-feedback";
import type { FeedbackReason } from "@/lib/data/feedback";

export const dynamic = "force-dynamic";

interface Payload {
  action?: "comment" | "request_changes" | "approve";
  portalSlug?: string;
  contentId?: string;
  body?: string;
  reason?: FeedbackReason;
  note?: string;
}

const SLUG_RE = /^[a-z0-9-]{1,64}$/;
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function POST(request: NextRequest) {
  let payload: Payload;
  try {
    payload = (await request.json()) as Payload;
  } catch {
    return NextResponse.json(
      { ok: false, error: "Malformed request body." } satisfies ActionResult,
      { status: 400 },
    );
  }

  const { action, portalSlug, contentId } = payload;
  if (!portalSlug || !SLUG_RE.test(portalSlug)) {
    return NextResponse.json(
      { ok: false, error: "Invalid portal." } satisfies ActionResult,
      { status: 400 },
    );
  }
  if (!contentId || !UUID_RE.test(contentId)) {
    return NextResponse.json(
      { ok: false, error: "Invalid content id." } satisfies ActionResult,
      { status: 400 },
    );
  }

  let result: ActionResult;
  switch (action) {
    case "comment":
      result = await commentContentAction({
        portalSlug,
        contentId,
        body: payload.body ?? "",
      });
      break;
    case "request_changes":
      result = await requestChangesContentAction({
        portalSlug,
        contentId,
        reason: payload.reason ?? null,
        body: payload.body ?? "",
      });
      break;
    case "approve":
      result = await approveContentAction({
        portalSlug,
        contentId,
        note: payload.note,
      });
      break;
    default:
      return NextResponse.json(
        { ok: false, error: "Unknown action." } satisfies ActionResult,
        { status: 400 },
      );
  }

  // Always HTTP 200 — the ActionResult.ok flag carries success/failure
  // so the client renders the existing flash UI unchanged.
  return NextResponse.json(result, { status: 200 });
}
