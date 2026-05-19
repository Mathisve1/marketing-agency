import { redirect } from "next/navigation";
import { AgencySidebar } from "@/components/layout/agency-sidebar";
import { AgencyTopbar } from "@/components/layout/agency-topbar";
import { getCurrentPersona } from "@/lib/auth/persona";
import { getDataSource } from "@/lib/data/_source";
import { hasSupabaseEnv } from "@/lib/supabase/server";

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
    if (!persona) redirect("/login?next=/agency");
    if (persona.kind !== "operator") {
      // Signed-in but not an operator. If they're a client member,
      // bounce them to their portal; otherwise to login.
      if (persona.kind === "client" && persona.portalIds.length > 0) {
        redirect("/login?next=/agency");
      } else {
        redirect("/login?next=/agency");
      }
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
