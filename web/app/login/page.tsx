import { LoginForm } from "@/components/auth/login-form";
import { hasSupabaseEnv } from "@/lib/supabase/server";

interface PageProps {
  searchParams: Promise<{ next?: string }>;
}

export default async function OperatorLogin({ searchParams }: PageProps) {
  const { next } = await searchParams;
  const redirectTo = next && next.startsWith("/") ? next : "/agency";
  const supabaseConfigured = hasSupabaseEnv();

  return (
    <main className="min-h-screen bg-[color:var(--color-cream-soft)] flex items-center justify-center px-6 py-12">
      <div className="max-w-md w-full">
        {supabaseConfigured ? (
          <LoginForm audience="operator" redirectTo={redirectTo} />
        ) : (
          <DemoNotice />
        )}
      </div>
    </main>
  );
}

function DemoNotice() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Demo mode</h1>
      <p className="text-sm text-[color:var(--color-ink-muted)] leading-relaxed">
        Supabase auth is not configured in this environment, so login is
        disabled. The app is running off the local Pai demo seed —
        everything is visible without signing in.
      </p>
      <ul className="text-sm text-[color:var(--color-ink-muted)] list-disc pl-5 space-y-1">
        <li>
          To enable real auth, copy <code className="font-mono">web/.env.example</code>{" "}
          to <code className="font-mono">.env.local</code> and fill in
          <code className="font-mono"> NEXT_PUBLIC_SUPABASE_URL </code>
          and <code className="font-mono">NEXT_PUBLIC_SUPABASE_ANON_KEY</code>.
        </li>
        <li>
          Set <code className="font-mono">NEXT_PUBLIC_DATA_SOURCE=supabase</code>.
        </li>
        <li>
          Apply the migrations and the Phase 1D{" "}
          <code className="font-mono">003_auth_handle_new_user.sql</code>{" "}
          migration in your Supabase project.
        </li>
      </ul>
      <a href="/agency" className="inline-block underline text-[color:var(--color-accent)] text-sm">
        Continue to the demo dashboard →
      </a>
    </div>
  );
}
