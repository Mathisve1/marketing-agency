import Link from "next/link";
import { DEMO_CAMPAIGNS, DEMO_WORKSPACE } from "@/lib/demo-data";
import { hasSupabaseEnv } from "@/lib/supabase/server";
import { getDataSource } from "@/lib/data/_source";

export default function Landing() {
  const paiPortalSlug = DEMO_CAMPAIGNS[0].clientPortalSlug;
  const supabaseLive = getDataSource() === "supabase" && hasSupabaseEnv();

  return (
    <main className="min-h-screen bg-[color:var(--color-cream-soft)] flex items-center justify-center px-6 py-12">
      <div className="max-w-2xl w-full text-center">
        <div className="text-xs uppercase tracking-[0.22em] text-[color:var(--color-ink-faint)]">
          {DEMO_WORKSPACE.agencyName} · AI Creative OS
        </div>
        <h1 className="mt-3 text-4xl sm:text-5xl font-semibold leading-tight">
          Operator dashboard <br /> + private client approval portal
        </h1>
        <p className="mt-5 text-[color:var(--color-ink-muted)] leading-relaxed max-w-xl mx-auto">
          {supabaseLive
            ? "Phase 1D — Supabase auth + persisted client approvals are live. Sign in to enter."
            : "Phase 1A scaffold — visual shell only. Local Pai Skincare seed data, no Supabase yet, no paid API calls. Pick a side to enter."}
        </p>

        <div className="mt-10 grid sm:grid-cols-2 gap-4 text-left">
          <Link
            href={supabaseLive ? "/login?next=/agency" : "/agency"}
            className="block rounded-lg border border-[color:var(--color-hairline)] bg-white p-6 hover:bg-white/80 transition"
          >
            <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-accent)] font-semibold">
              Agency / operator
            </div>
            <div className="mt-2 text-lg font-semibold">
              Yuvo Studio console →
            </div>
            <div className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
              Manage brands, campaigns, raw + audio-fixed outputs. Quality
              tiers, manual Audio Fixer, internal prompts.
            </div>
          </Link>
          <Link
            href={
              supabaseLive
                ? `/client/${paiPortalSlug}/login?next=/client/${paiPortalSlug}`
                : `/client/${paiPortalSlug}`
            }
            className="block rounded-lg border border-[color:var(--color-hairline)] bg-white p-6 hover:bg-white/80 transition"
          >
            <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-accent)] font-semibold">
              Client portal {supabaseLive ? "" : "preview"}
            </div>
            <div className="mt-2 text-lg font-semibold">Pai Skincare →</div>
            <div className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
              See the client-safe view: calendar, content, approve / request
              changes / request next week. No costs. No internals.
            </div>
          </Link>
        </div>
      </div>
    </main>
  );
}
