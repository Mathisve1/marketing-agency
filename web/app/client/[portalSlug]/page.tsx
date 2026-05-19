import Link from "next/link";
import { notFound } from "next/navigation";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { ClientStatusChip } from "@/components/content/status-chip";
import { NextWeekRequestForm } from "@/components/content/next-week-request-form";
import {
  getClientPortalBySlug,
  getClientVisibleContentItems,
} from "@/lib/data/content";
import { listContentRequestsForPortal } from "@/lib/data/content-requests";
import { getDataSource } from "@/lib/data/_source";
import { hasSupabaseEnv } from "@/lib/supabase/server";
import { requireClientPortalAccess } from "@/lib/auth/guard-client-portal";

interface PageProps {
  params: Promise<{ portalSlug: string }>;
}

export default async function ClientHome({ params }: PageProps) {
  const { portalSlug } = await params;
  // Phase 1K — gate at the page (not the layout) so the sibling
  // /login route is reachable without auth.
  await requireClientPortalAccess(portalSlug);
  const portal = await getClientPortalBySlug(portalSlug);
  if (!portal) notFound();

  // Already client-safe DTOs — the data layer applies the
  // shared_with_client + status filter, and `toClientContentView` has
  // dropped every operator-only field.
  const visible = await getClientVisibleContentItems(portal.campaignId);
  // Phase 1E — show the client their own outstanding next-week requests
  // so they have a sense of what they've already sent us.
  const requests = await listContentRequestsForPortal(portal.portalId);
  const isLive = getDataSource() === "supabase" && hasSupabaseEnv();

  return (
    <div className="space-y-6">
      <div>
        <div className="text-xs uppercase tracking-[0.22em] text-[color:var(--color-ink-faint)]">
          Private review portal
        </div>
        <h1 className="mt-2 text-2xl sm:text-3xl font-semibold leading-tight">
          {portal.brand.name} · content review
        </h1>
        <p className="mt-2 text-[color:var(--color-ink-muted)] max-w-xl leading-relaxed">
          This is your private space to see what we&rsquo;re making, leave
          feedback, and request what you&rsquo;d like next week.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Ready for your review</CardTitle>
        </CardHeader>
        <CardBody>
          {visible.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              Nothing ready for review yet. We&rsquo;ll let you know.
            </p>
          ) : (
            <ul className="divide-y divide-[color:var(--color-hairline)]">
              {visible.map((view) => (
                <li
                  key={view.id}
                  className="py-4 flex items-center justify-between gap-3"
                >
                  <div className="min-w-0">
                    <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                      {new Date(view.scheduledFor + "T00:00:00Z").toLocaleDateString(
                        "en-GB",
                        { weekday: "short", day: "numeric", month: "short" },
                      )}{" "}
                      · {view.platforms.join(" · ")}
                    </div>
                    <div className="font-semibold mt-0.5">{view.title}</div>
                    <div className="text-xs text-[color:var(--color-ink-muted)] mt-0.5 line-clamp-1">
                      {view.hook}
                    </div>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <ClientStatusChip status={view.status} />
                    <Link
                      href={`/client/${portalSlug}/content/${view.id}`}
                      className="text-sm text-[color:var(--color-accent)] underline"
                    >
                      Review →
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Request something for next week</CardTitle>
        </CardHeader>
        <CardBody className="space-y-4">
          <p className="text-sm text-[color:var(--color-ink-muted)]">
            Anything you&rsquo;d like us to try? An angle, an ingredient, a
            different length — leave a short note and we&rsquo;ll review it
            this week.
          </p>
          <NextWeekRequestForm portalSlug={portalSlug} isLive={isLive} />
          {requests.length > 0 && (
            <div className="pt-3 border-t border-[color:var(--color-hairline)] space-y-2">
              <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                Your recent requests
              </div>
              <ul className="space-y-2">
                {requests.slice(0, 5).map((r) => (
                  <li
                    key={r.id}
                    className="rounded-md border border-[color:var(--color-hairline)] p-3 text-sm"
                  >
                    <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                      {new Date(r.createdAt).toLocaleString("en-GB")}
                    </div>
                    <div className="mt-1 leading-relaxed">{r.body}</div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Looking ahead</CardTitle>
        </CardHeader>
        <CardBody>
          <p className="text-sm text-[color:var(--color-ink-muted)]">
            Use the{" "}
            <Link
              href={`/client/${portalSlug}/calendar`}
              className="underline text-[color:var(--color-accent)]"
            >
              calendar
            </Link>{" "}
            to see what&rsquo;s planned. You can also leave a request on any
            specific content card.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}
