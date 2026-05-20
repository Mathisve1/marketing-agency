import { notFound } from "next/navigation";
import { ClientTopbar } from "@/components/layout/client-topbar";
import { getClientPortalBySlug } from "@/lib/data/content";
import { getDataSource } from "@/lib/data/_source";
import { hasSupabaseEnv } from "@/lib/supabase/server";

interface LayoutProps {
  children: React.ReactNode;
  params: Promise<{ portalSlug: string }>;
}

export default async function ClientPortalLayout({ children, params }: LayoutProps) {
  const { portalSlug } = await params;

  // Phase 1K — the auth gate moved out of this layout into
  // `web/lib/auth/guard-client-portal.ts` so the LOGIN page (which is
  // also a child of this layout) is not gated against itself. Each
  // protected page calls `requireClientPortalAccess(portalSlug)` at
  // the top of its render; the login page deliberately does not.
  // The layout only renders chrome below.
  const authEnabled =
    getDataSource() === "supabase" && hasSupabaseEnv();

  const portal = await getClientPortalBySlug(portalSlug);
  if (!portal) notFound();

  return (
    <div className="min-h-screen bg-[color:var(--color-cream-soft)]">
      <ClientTopbar
        brandName={portal.brand.name}
        portalSlug={portalSlug}
        authEnabled={authEnabled}
      />
      <main className="max-w-4xl mx-auto px-6 py-6">{children}</main>
      <footer className="max-w-4xl mx-auto px-6 py-8 text-xs text-[color:var(--color-ink-faint)]">
        <span>
          Private review portal for {portal.brand.name}. Do not share this link
          publicly.
        </span>
      </footer>
    </div>
  );
}
