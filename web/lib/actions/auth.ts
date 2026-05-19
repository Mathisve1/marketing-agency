// Yuvo Studio — Phase 1D/1N auth server actions.
//
// Phase 1N: email + password is the primary login. Magic-link (Phase 1D)
// stays as the secondary fallback. Both flows reuse the same Supabase
// session-cookie writes via @supabase/ssr (see web/lib/supabase/server.ts).

"use server";

import { redirect } from "next/navigation";
import { getServerSupabase, hasSupabaseEnv } from "@/lib/supabase/server";

export interface SignInResult {
  ok: boolean;
  error?: string;
  message?: string;
  /** When success requires a redirect, the server action sets this so
   *  the client component can `router.push` (we cannot call redirect()
   *  inside a try block reliably). */
  redirectTo?: string;
}

// Match `^/[^\s]*$` — same shape as the auth/callback validator.
const SAFE_NEXT = /^\/[^\s]*$/;
function safeNext(input: string | undefined | null, fallback: string): string {
  if (input && SAFE_NEXT.test(input)) return input;
  return fallback;
}

function getSiteUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_SITE_URL;
  if (fromEnv && fromEnv.trim() !== "") return fromEnv.replace(/\/$/, "");
  // Sensible local fallback. In production this MUST be set so magic-
  // link emails point back at the real host.
  return "http://localhost:3000";
}

/** Sends a magic-link email. `redirectTo` is the path the user lands on
 *  AFTER clicking the link and the cookie is set. */
export async function sendMagicLink(
  email: string,
  redirectTo: string = "/",
): Promise<SignInResult> {
  if (!hasSupabaseEnv()) {
    return {
      ok: false,
      error:
        "Demo mode — Supabase auth is not configured. Set " +
        "NEXT_PUBLIC_SUPABASE_URL + NEXT_PUBLIC_SUPABASE_ANON_KEY and " +
        "NEXT_PUBLIC_DATA_SOURCE=supabase in web/.env.local to use auth.",
    };
  }

  const normalizedEmail = email.trim().toLowerCase();
  if (!normalizedEmail || !normalizedEmail.includes("@")) {
    return { ok: false, error: "Please enter a valid email address." };
  }

  const supabase = await getServerSupabase();
  const site = getSiteUrl();
  // Pass `next` as a query param to the callback so we can route the
  // user to their persona-appropriate home after exchanging the code.
  const callbackUrl = `${site}/auth/callback?next=${encodeURIComponent(redirectTo)}`;

  const { error } = await supabase.auth.signInWithOtp({
    email: normalizedEmail,
    options: {
      emailRedirectTo: callbackUrl,
      // Don't auto-create users from the public login form for the
      // operator route — operators are seeded explicitly. For the
      // client portal we DO want auto-create because invitees come in
      // cold via the magic-link they got in their inbox.
      shouldCreateUser: true,
    },
  });

  if (error) {
    return { ok: false, error: error.message };
  }
  return {
    ok: true,
    message:
      "Check your inbox — we just sent a sign-in link. It expires in 60 minutes.",
  };
}

/** Signs the current user out and redirects home. */
export async function signOut(): Promise<void> {
  if (hasSupabaseEnv()) {
    const supabase = await getServerSupabase();
    await supabase.auth.signOut();
  }
  redirect("/");
}

/** Phase 1N — primary login path: email + password.
 *
 *  On success: writes the Supabase session cookie via @supabase/ssr's
 *  setAll handler (server-action context is writeable for cookies) and
 *  returns `{ ok: true, redirectTo }` so the client component can route
 *  the user to their persona-appropriate landing page.
 *
 *  Never returns the password back to the caller. Never logs it. Error
 *  messages are intentionally Supabase-verbatim ("Invalid login
 *  credentials" etc.) — short, non-leaking, googleable.
 */
export async function signInWithPasswordAction(
  email: string,
  password: string,
  rawNext?: string,
): Promise<SignInResult> {
  if (!hasSupabaseEnv()) {
    return {
      ok: false,
      error:
        "Demo mode — Supabase auth is not configured. Set " +
        "NEXT_PUBLIC_SUPABASE_URL + NEXT_PUBLIC_SUPABASE_ANON_KEY and " +
        "NEXT_PUBLIC_DATA_SOURCE=supabase to use password sign-in.",
    };
  }

  const normalizedEmail = (email || "").trim().toLowerCase();
  if (!normalizedEmail || !normalizedEmail.includes("@")) {
    return { ok: false, error: "Please enter a valid email address." };
  }
  if (!password || password.length < 8) {
    return { ok: false, error: "Password must be at least 8 characters." };
  }

  const supabase = await getServerSupabase();
  const { error } = await supabase.auth.signInWithPassword({
    email: normalizedEmail,
    password,
  });

  if (error) {
    // Supabase returns "Invalid login credentials" for both wrong email
    // and wrong password — surface as-is (no extra leakage).
    return { ok: false, error: error.message };
  }

  return {
    ok: true,
    message: "Signed in.",
    redirectTo: safeNext(rawNext, "/"),
  };
}
