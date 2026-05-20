import Link from "next/link";
import { DEMO_BRANDS, DEMO_CAMPAIGNS, DEMO_WORKSPACE } from "@/lib/demo-data";

export function AgencySidebar() {
  return (
    <aside className="w-64 shrink-0 border-r border-[color:var(--color-hairline)] bg-white min-h-screen">
      <div className="p-5 border-b border-[color:var(--color-hairline)]">
        <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Workspace
        </div>
        <div className="font-semibold mt-1">{DEMO_WORKSPACE.name}</div>
        <div className="text-xs text-[color:var(--color-ink-muted)] mt-0.5">
          Operator console
        </div>
      </div>

      <nav className="p-3 text-sm">
        <Link
          href="/agency"
          className="block px-3 py-2 rounded-md hover:bg-[color:var(--color-hairline)]"
        >
          Dashboard
        </Link>
        <Link
          href="/agency/brands"
          className="block px-3 py-2 rounded-md hover:bg-[color:var(--color-hairline)]"
        >
          Brands
        </Link>
        <Link
          href="/agency/inbox"
          className="block px-3 py-2 rounded-md hover:bg-[color:var(--color-hairline)] font-semibold"
        >
          Inbox
        </Link>
        <Link
          href="/agency/claude-tasks"
          className="block px-3 py-2 rounded-md hover:bg-[color:var(--color-hairline)]"
        >
          Claude Code tasks
        </Link>
        <Link
          href="/agency/prompt-review"
          className="block px-3 py-2 rounded-md hover:bg-[color:var(--color-hairline)]"
        >
          Prompt review queue
        </Link>
        <Link
          href="/agency/copy-drafts"
          className="block px-3 py-2 rounded-md hover:bg-[color:var(--color-hairline)]"
        >
          Copy drafts (non-video)
        </Link>
        <Link
          href="/agency/creative-briefs"
          className="block px-3 py-2 rounded-md hover:bg-[color:var(--color-hairline)]"
        >
          Creative briefs (social)
        </Link>
        <Link
          href="/agency/jobs"
          className="block px-3 py-2 rounded-md hover:bg-[color:var(--color-hairline)]"
        >
          Generation jobs
        </Link>
        <div className="mt-4 mb-1 px-3 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Recent brands
        </div>
        {DEMO_BRANDS.map((b) => (
          <Link
            key={b.id}
            href={`/agency/brands/${b.id}`}
            className="block px-3 py-1.5 rounded-md text-[color:var(--color-ink-muted)] hover:bg-[color:var(--color-hairline)] hover:text-[color:var(--color-ink)]"
          >
            {b.name}
          </Link>
        ))}
        <div className="mt-4 mb-1 px-3 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Recent campaigns
        </div>
        {DEMO_CAMPAIGNS.map((c) => (
          <div key={c.id} className="px-3 py-1.5">
            <div className="text-[color:var(--color-ink-muted)] text-xs leading-snug">
              {c.title}
            </div>
            <div className="flex gap-3 mt-1">
              <Link
                href={`/agency/campaigns/${c.id}/calendar`}
                className="text-xs underline text-[color:var(--color-accent)]"
              >
                Calendar
              </Link>
              <Link
                href={`/agency/campaigns/${c.id}/outputs`}
                className="text-xs underline text-[color:var(--color-accent)]"
              >
                Outputs
              </Link>
            </div>
          </div>
        ))}
      </nav>
    </aside>
  );
}
