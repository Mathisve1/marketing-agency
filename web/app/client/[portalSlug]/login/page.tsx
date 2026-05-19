import Link from "next/link";
import { notFound } from "next/navigation";
import { LoginForm } from "@/components/auth/login-form";
import { hasSupabaseEnv } from "@/lib/supabase/server";
import { getClientPortalBySlug } from "@/lib/data/content";

interface PageProps {
  params: Promise<{ portalSlug: string }>;
  searchParams: Promise<{ next?: string }>;
}

export default async function ClientPortalLogin({ params, searchParams }: PageProps) {
  const { portalSlug } = await params;
  const { next } = await searchParams;
  const redirectTo =
    next && next.startsWith(`/client/${portalSlug}`)
      ? next
      : `/client/${portalSlug}`;

  // We still want to surface the brand name on the login page even
  // before the user is signed in. The supabase getClientPortalBySlug
  // path will RLS-deny without auth — when that happens we fall back
  // to a generic heading rather than 404ing the user.
  let brandName: string | undefined;
  try {
    const portal = await getClientPortalBySlug(portalSlug);
    brandName = portal?.brand.name;
  } catch {
    brandName = undefined;
  }

  // We only 404 when we are CONFIDENT the portal doesn't exist — which
  // is only true in demo mode (no RLS to confuse us).
  if (!brandName && !hasSupabaseEnv()) {
    notFound();
  }

  return (
    <main className="min-h-screen bg-[color:var(--color-cream-soft)] flex items-center justify-center px-6 py-12">
      <div className="max-w-md w-full">
        {hasSupabaseEnv() ? (
          <LoginForm
            audience="client"
            redirectTo={redirectTo}
            brandName={brandName}
          />
        ) : (
          <DemoNotice portalSlug={portalSlug} />
        )}
      </div>
    </main>
  );
}

function DemoNotice({ portalSlug }: { portalSlug: string }) {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Demo portal</h1>
      <p className="text-sm text-[color:var(--color-ink-muted)] leading-relaxed">
        This Pai demo portal does not require authentication — Supabase
        auth is wired but not enabled in the current environment. Open
        the portal directly to preview the client-side flow.
      </p>
      <Link
        href={`/client/${portalSlug}`}
        className="inline-block underline text-[color:var(--color-accent)] text-sm"
      >
        Open the demo portal →
      </Link>
    </div>
  );
}
