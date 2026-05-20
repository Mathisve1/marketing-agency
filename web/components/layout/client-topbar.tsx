import Link from "next/link";
import { LogoutButton } from "@/components/auth/logout-button";

/** Client-portal topbar. Deliberately minimal: brand name only, no
 *  "Operator" / "Yuvo OS" framing, no role-switch link. Sign-out lives
 *  here (when auth is live) so it is discoverable instead of buried in
 *  the footer. */
export function ClientTopbar({
  brandName,
  portalSlug,
  authEnabled = false,
}: {
  brandName: string;
  portalSlug: string;
  authEnabled?: boolean;
}) {
  return (
    <header className="h-14 border-b border-[color:var(--color-hairline)] bg-white px-6 flex items-center justify-between">
      <Link
        href={`/client/${portalSlug}`}
        className="font-semibold text-[color:var(--color-ink)]"
      >
        {brandName}
      </Link>
      <nav className="flex items-center gap-5 text-sm">
        <Link
          href={`/client/${portalSlug}`}
          className="text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
        >
          Overview
        </Link>
        <Link
          href={`/client/${portalSlug}/calendar`}
          className="text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
        >
          Content calendar
        </Link>
        {authEnabled && <LogoutButton />}
      </nav>
    </header>
  );
}
