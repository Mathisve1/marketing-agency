import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { LogoutButton } from "@/components/auth/logout-button";
import { getCurrentPersona } from "@/lib/auth/persona";
import { hasSupabaseEnv } from "@/lib/supabase/server";
import { getDataSource } from "@/lib/data/_source";

export async function AgencyTopbar({ subtitle }: { subtitle?: string }) {
  const requireAuth = getDataSource() === "supabase" && hasSupabaseEnv();
  const persona = requireAuth ? await getCurrentPersona() : null;
  const signedIn = persona !== null;
  const email = persona && "email" in persona ? persona.email : null;

  return (
    <header className="h-14 border-b border-[color:var(--color-hairline)] bg-white px-6 flex items-center justify-between">
      <div className="flex items-center gap-3">
        <span className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Yuvo OS
        </span>
        <Badge tone="info">Operator</Badge>
        {subtitle && (
          <span className="text-sm text-[color:var(--color-ink-muted)]">
            {subtitle}
          </span>
        )}
      </div>
      <div className="flex items-center gap-4 text-sm">
        <Link
          href="/agency"
          className="text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
        >
          Agency
        </Link>
        <Link
          href="/client/pai-skincare-demo"
          className="text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
        >
          Preview client portal
        </Link>
        {requireAuth ? (
          signedIn ? (
            <span className="flex items-center gap-3">
              {email && (
                <span className="text-xs text-[color:var(--color-ink-faint)]">
                  {email}
                </span>
              )}
              <LogoutButton />
            </span>
          ) : (
            <Link
              href="/login?next=/agency"
              className="text-[color:var(--color-accent)] underline"
            >
              Sign in
            </Link>
          )
        ) : null}
      </div>
    </header>
  );
}
