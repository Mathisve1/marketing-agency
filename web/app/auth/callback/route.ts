// Yuvo Studio — Phase 1D magic-link callback handler.
//
// Receives the `?code=<otp>` from Supabase Auth after the user clicks
// their magic-link email, exchanges it for a session, sets the cookie,
// then redirects to the `?next=` path embedded by the sign-in action.
//
// Route handlers (vs server actions) are the supported place to write
// auth cookies in the Next.js App Router.

import { NextResponse, type NextRequest } from "next/server";
import { getServerSupabase, hasSupabaseEnv } from "@/lib/supabase/server";

const SAFE_NEXT = /^\/[^\s]*$/;

export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  const rawNext = searchParams.get("next");
  const next = rawNext && SAFE_NEXT.test(rawNext) ? rawNext : "/";

  if (!hasSupabaseEnv() || !code) {
    return NextResponse.redirect(
      `${origin}/login?error=missing_code_or_env`,
    );
  }

  const supabase = await getServerSupabase();
  const { error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) {
    return NextResponse.redirect(
      `${origin}/login?error=${encodeURIComponent(error.message)}`,
    );
  }
  return NextResponse.redirect(`${origin}${next}`);
}
