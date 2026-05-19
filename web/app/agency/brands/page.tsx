import Link from "next/link";
import Image from "next/image";
import { Card, CardBody } from "@/components/ui/card";
import { DEMO_BRANDS } from "@/lib/demo-data";

export default function BrandsList() {
  return (
    <div className="space-y-5 max-w-5xl">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Brands</h1>
        <span className="text-sm text-[color:var(--color-ink-muted)]">
          {DEMO_BRANDS.length} brand{DEMO_BRANDS.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {DEMO_BRANDS.map((b) => (
          <Card key={b.id}>
            <CardBody>
              <div className="flex gap-3">
                {b.thumbnailPath && (
                  <div className="relative w-16 h-16 rounded-md overflow-hidden bg-[color:var(--color-cream-soft)] flex-shrink-0">
                    <Image
                      src={b.thumbnailPath}
                      alt={b.name}
                      fill
                      sizes="64px"
                      className="object-cover"
                    />
                  </div>
                )}
                <div className="min-w-0">
                  <Link
                    href={`/agency/brands/${b.id}`}
                    className="font-semibold hover:underline"
                  >
                    {b.name}
                  </Link>
                  <div className="text-xs text-[color:var(--color-ink-muted)] mt-0.5 truncate">
                    {b.niche}
                  </div>
                  <div className="text-xs text-[color:var(--color-ink-faint)] mt-1.5 line-clamp-2">
                    {b.brandTone}
                  </div>
                </div>
              </div>
            </CardBody>
          </Card>
        ))}
      </div>
    </div>
  );
}
