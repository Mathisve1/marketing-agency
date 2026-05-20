import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { AgencySidebar } from "@/components/layout/agency-sidebar";
import { AgencyTopbar } from "@/components/layout/agency-topbar";
import { getCurrentPersona } from "@/lib/auth/persona";
import { getDataSource } from "@/lib/data/_source";
import { hasSupabaseEnv } from "@/lib/supabase/server";

// Build the `?next=` value from the real requested path (injected by
// web/middleware.ts as `x-pathname` = pathname+search). The path stays
// literal so it satisfies the login page's `^\/[^\s]*$` check; only the
// query string is encoded so the whole value survives as one param,
// e.g. /agency/inbox?filter=urgent -> /agency/inbox%3Ffilter%3Durgent.
// Falls back to "/agency" if the header is absent (unchanged behaviour).
async function loginNextForCurrentPath(): Promise<string> {
  const pathWithSearch = (await headers()).get("x-pathname");
  if (!pathWithSearch || !pathWithSearch.startsWith("/agency")) {
    return "/agency";
  }
  const q = pathWithSearch.indexOf("?");
  if (q === -1) return pathWithSearch;
  return (
    pathWithSearch.slice(0, q) +
    encodeURIComponent(pathWithSearch.slice(q))
  );
}

export default async function AgencyLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Phase 1D auth gate. Demo mode opens the dashboard without auth so
  // the showcase keeps working. Supabase mode requires a session AND
  // that the signed-in user is in at least one workspace.
  const requireAuth = getDataSource() === "supabase" && hasSupabaseEnv();
  if (requireAuth) {
    const persona = await getCurrentPersona();
    if (!persona) {
      redirect(`/login?next=${await loginNextForCurrentPath()}`);
    }
    if (persona.kind !== "operator") {
      // Signed-in but not an operator (client member or unaffiliated).
      // Either way, bounce to the operator login, preserving the
      // originally requested deep path.
      redirect(`/login?next=${await loginNextForCurrentPath()}`);
    }
  }

  return (
    <div className="flex min-h-screen bg-[color:var(--color-cream-soft)]">
      <AgencySidebar />
      <div className="flex-1 min-w-0">
        <AgencyTopbar />
        <div className="px-6 py-6">{children}</div>
      </div>
    </div>
  );
}
