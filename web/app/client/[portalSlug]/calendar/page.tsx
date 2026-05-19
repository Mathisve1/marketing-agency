import Link from "next/link";
import { notFound } from "next/navigation";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { ClientStatusChip } from "@/components/content/status-chip";
import {
  getClientPortalBySlug,
  getClientVisibleContentItems,
} from "@/lib/data/content";
import { requireClientPortalAccess } from "@/lib/auth/guard-client-portal";

interface PageProps {
  params: Promise<{ portalSlug: string }>;
}

export default async function ClientCalendar({ params }: PageProps) {
  const { portalSlug } = await params;
  // Phase 1K — gate at the page so the sibling /login is reachable.
  await requireClientPortalAccess(portalSlug);
  const portal = await getClientPortalBySlug(portalSlug);
  if (!portal) notFound();
  const visible = await getClientVisibleContentItems(portal.campaignId);

  return (
    <div className="space-y-5">
      <div>
        <Link
          href={`/client/${portalSlug}`}
          className="text-sm text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
        >
          ← Overview
        </Link>
        <h1 className="mt-1 text-2xl font-semibold">Content calendar</h1>
        <p className="text-sm text-[color:var(--color-ink-muted)] mt-1">
          Items currently shared with you. Drafts in progress are hidden until
          they&rsquo;re ready for review.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Upcoming</CardTitle>
        </CardHeader>
        <CardBody>
          {visible.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              Nothing scheduled yet.
            </p>
          ) : (
            <ul className="divide-y divide-[color:var(--color-hairline)]">
              {visible.map((c) => (
                <li
                  key={c.id}
                  className="py-4 flex items-center justify-between gap-3"
                >
                  <div className="min-w-0">
                    <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                      {new Date(c.scheduledFor + "T00:00:00Z").toLocaleDateString(
                        "en-GB",
                        { weekday: "long", day: "numeric", month: "long" },
                      )}
                    </div>
                    <div className="font-semibold mt-0.5">{c.title}</div>
                    <div className="text-xs text-[color:var(--color-ink-muted)] mt-0.5">
                      {c.platforms.join(" · ")} · {c.durationSec}s
                    </div>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <ClientStatusChip status={c.status} />
                    <Link
                      href={`/client/${portalSlug}/content/${c.id}`}
                      className="text-sm text-[color:var(--color-accent)] underline"
                    >
                      Open →
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
