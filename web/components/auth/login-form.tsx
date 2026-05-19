"use client";

// Phase 1N login form. Email + password is primary; "send magic link"
// is a secondary fallback button. Both flows write the Supabase session
// cookie server-side via @supabase/ssr (see web/lib/supabase/server.ts).
//
// Used by:
//   - /login                                  audience="operator"
//   - /client/[portalSlug]/login              audience="client"

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { sendMagicLink, signInWithPasswordAction } from "@/lib/actions/auth";

interface Props {
  /** Where to send the user after they sign in (password path) or click
   *  the magic-link in their email. */
  redirectTo?: string;
  /** Audience-specific copy. */
  audience: "operator" | "client";
  /** Optional brand name for the client portal variant. */
  brandName?: string;
}

type Status =
  | { kind: "idle" }
  | { kind: "ok"; message: string }
  | { kind: "err"; error: string };

export function LoginForm({ redirectTo = "/", audience, brandName }: Props) {
  const router = useRouter();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [pending, startTransition] = React.useTransition();
  const [magicPending, startMagicTransition] = React.useTransition();
  const [status, setStatus] = React.useState<Status>({ kind: "idle" });

  const heading =
    audience === "operator"
      ? "Sign in to Yuvo Studio"
      : `Sign in to ${brandName ?? "your"} review portal`;
  const subheading =
    audience === "operator"
      ? "Use the email + password set by your admin."
      : "Use the email + password we sent you when we set up your portal.";

  function onPasswordSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setStatus({ kind: "idle" });
    startTransition(async () => {
      const result = await signInWithPasswordAction(email, password, redirectTo);
      if (result.ok && result.redirectTo) {
        // Cookie has been written by the server action; navigate.
        // router.replace + router.refresh ensures the next render reads
        // the new session.
        router.replace(result.redirectTo);
        router.refresh();
      } else {
        setStatus({ kind: "err", error: result.error ?? "Sign-in failed." });
      }
    });
  }

  function onMagicLink() {
    setStatus({ kind: "idle" });
    if (!email || !email.includes("@")) {
      setStatus({ kind: "err", error: "Enter your email above first." });
      return;
    }
    startMagicTransition(async () => {
      const result = await sendMagicLink(email, redirectTo);
      if (result.ok) {
        setStatus({ kind: "ok", message: result.message ?? "Email sent." });
      } else {
        setStatus({ kind: "err", error: result.error ?? "Sign-in failed." });
      }
    });
  }

  const anyPending = pending || magicPending;

  return (
    <form onSubmit={onPasswordSubmit} className="space-y-4 max-w-sm w-full">
      <div>
        <h1 className="text-2xl font-semibold">{heading}</h1>
        <p className="text-sm text-[color:var(--color-ink-muted)] mt-1.5">
          {subheading}
        </p>
      </div>
      <label className="block">
        <span className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Email
        </span>
        <input
          type="email"
          required
          autoFocus
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          className="mt-1.5 w-full h-10 px-3 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm outline-none focus:border-[color:var(--color-accent)]"
        />
      </label>
      <label className="block">
        <span className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Password
        </span>
        <input
          type="password"
          required
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="••••••••"
          className="mt-1.5 w-full h-10 px-3 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm outline-none focus:border-[color:var(--color-accent)]"
        />
      </label>
      <div className="flex flex-col gap-2">
        <Button type="submit" variant="primary" size="md" disabled={anyPending}>
          {pending ? "Signing in..." : "Sign in"}
        </Button>
        <button
          type="button"
          onClick={onMagicLink}
          disabled={anyPending}
          className="text-xs text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)] underline disabled:opacity-50 text-left"
        >
          {magicPending
            ? "Sending magic link…"
            : "Forgot your password? Send me a magic link instead"}
        </button>
      </div>
      {status.kind === "ok" && (
        <div className="text-sm rounded-md bg-[color:var(--color-success)]/10 border border-[color:var(--color-success)]/30 text-[color:var(--color-ink)] px-3 py-2">
          {status.message}
        </div>
      )}
      {status.kind === "err" && (
        <div className="text-sm rounded-md bg-[color:var(--color-danger)]/10 border border-[color:var(--color-danger)]/30 text-[color:var(--color-ink)] px-3 py-2">
          {status.error}
        </div>
      )}
    </form>
  );
}
