import Link from "next/link";
import { notFound } from "next/navigation";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  DEMO_CAMPAIGNS,
  DEMO_CONTENT,
  getBrand,
} from "@/lib/demo-data";

interface PageProps {
  params: Promise<{ brandId: string }>;
}

export default async function BrandDetail({ params }: PageProps) {
  const { brandId } = await params;
  const brand = getBrand(brandId);
  if (!brand) notFound();

  const campaigns = DEMO_CAMPAIGNS.filter((c) => c.brandId === brand.id);

  return (
    <div className="space-y-5 max-w-5xl">
      <div>
        <Link
          href="/agency/brands"
          className="text-sm text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
        >
          ← All brands
        </Link>
        <h1 className="mt-1 text-2xl font-semibold">{brand.name}</h1>
        <div className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
          {brand.niche} · {brand.websiteUrl}
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Brand profile</CardTitle>
        </CardHeader>
        <CardBody>
          <dl className="grid sm:grid-cols-2 gap-4 text-sm">
            <Row label="Tone of voice" value={brand.brandTone} />
            <Row label="Audience assumption" value={brand.audienceAssumption} />
            <Row label="Website" value={brand.websiteUrl} />
            <Row
              label="Primary colour"
              value={
                <span className="inline-flex items-center gap-2">
                  <span
                    className="w-4 h-4 rounded-full border border-[color:var(--color-hairline)]"
                    style={{ backgroundColor: brand.primaryColorHex }}
                  />
                  <span className="font-mono">{brand.primaryColorHex}</span>
                </span>
              }
            />
          </dl>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Campaigns</CardTitle>
        </CardHeader>
        <CardBody>
          {campaigns.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              No campaigns yet.
            </p>
          ) : (
            <ul className="divide-y divide-[color:var(--color-hairline)]">
              {campaigns.map((c) => {
                const count = DEMO_CONTENT.filter(
                  (x) => x.campaignId === c.id,
                ).length;
                return (
                  <li
                    key={c.id}
                    className="py-3 flex items-center justify-between gap-3"
                  >
                    <div>
                      <div className="font-semibold">{c.title}</div>
                      <div className="flex gap-2 mt-1 items-center">
                        <Badge tone="info">{c.strategicPattern}</Badge>
                        <span className="text-xs text-[color:var(--color-ink-muted)]">
                          {count} content item{count === 1 ? "" : "s"} · client portal slug{" "}
                          <code className="font-mono text-[color:var(--color-accent)]">
                            {c.clientPortalSlug}
                          </code>
                        </span>
                      </div>
                    </div>
                    <div className="flex gap-3 text-sm">
                      <Link
                        href={`/agency/campaigns/${c.id}/calendar`}
                        className="text-[color:var(--color-accent)] underline"
                      >
                        Calendar
                      </Link>
                      <Link
                        href={`/agency/campaigns/${c.id}/outputs`}
                        className="text-[color:var(--color-accent)] underline"
                      >
                        Outputs
                      </Link>
                      <Link
                        href={`/client/${c.clientPortalSlug}`}
                        className="text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)] underline"
                      >
                        Client view
                      </Link>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
        {label}
      </div>
      <div className="mt-1">{value}</div>
    </div>
  );
}
