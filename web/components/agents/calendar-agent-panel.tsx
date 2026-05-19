"use client";

// Phase 1Z — Calendar Agent panel.
//
// Renders the contentCalendarIdeas list from a Brand Analysis agent
// output and lets the operator materialise them as draft content_items.
// NEVER:
//   - approves a prompt
//   - submits a generation job
//   - shares with the client
//   - sends email

import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  createDraftContentCalendarFromAgentRunAction,
  type CalendarAgentResult,
} from "@/lib/actions/calendar-agent";
import {
  createPromptDraftsForCalendarItemsBulkAction,
  type BulkPromptDraftsResult,
} from "@/lib/actions/calendar-prompt-drafts";

// Phase 2D — calendar ideas are multi-format. `label` kept as an
// optional alias so any legacy {dayOffset,label,brief} payload still
// renders; `title` is canonical.
export interface CalendarIdea {
  dayOffset: number;
  title: string;
  label?: string;
  brief: string;
  suggestedChannel?: string;
  suggestedFormat?: string;
  distributionType?: string;
  contentGoal?: string;
  needsGeneration?: boolean;
  needsPromptVersion?: boolean;
}

export interface CampaignTarget {
  campaignId: string;
  campaignTitle: string;
  brandId: string;
  brandName: string;
}

interface Props {
  agentRunId: string;
  ideas: CalendarIdea[];
  campaigns: CampaignTarget[];
  /** When true, the panel renders open by default (used inside the
   *  fresh PlanView right after running the agent). */
  openByDefault?: boolean;
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export function CalendarAgentPanel({
  agentRunId,
  ideas,
  campaigns,
  openByDefault = false,
}: Props) {
  const [open, setOpen] = React.useState(openByDefault);
  const [campaignId, setCampaignId] = React.useState(
    campaigns[0]?.campaignId ?? "",
  );
  const [startDate, setStartDate] = React.useState(todayIso());
  const [selected, setSelected] = React.useState<Set<number>>(
    () => new Set(ideas.map((i) => i.dayOffset)),
  );
  const [notes, setNotes] = React.useState("");
  const [pending, startTransition] = React.useTransition();
  const [res, setRes] = React.useState<CalendarAgentResult | null>(null);

  function toggle(off: number) {
    const next = new Set(selected);
    if (next.has(off)) next.delete(off);
    else next.add(off);
    setSelected(next);
  }

  function onSubmit() {
    setRes(null);
    if (!campaignId) {
      setRes({ ok: false, error: "Pick a campaign first." });
      return;
    }
    if (selected.size === 0) {
      setRes({ ok: false, error: "Select at least one calendar idea." });
      return;
    }
    startTransition(async () => {
      const r = await createDraftContentCalendarFromAgentRunAction({
        agentRunId,
        campaignId,
        selectedDayOffsets: Array.from(selected).sort((a, b) => a - b),
        startDate,
        operatorNotes: notes.trim() || undefined,
      });
      setRes(r);
    });
  }

  if (ideas.length === 0) {
    return (
      <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 text-xs text-[color:var(--color-ink-muted)]">
        This agent run has no contentCalendarIdeas to materialise.
      </div>
    );
  }

  if (campaigns.length === 0) {
    return (
      <div className="rounded-md border border-[color:var(--color-warn)]/40 bg-[color:var(--color-warn)]/10 px-3 py-2 text-xs">
        <Badge tone="warn">No campaigns</Badge>{" "}
        Create a campaign first; calendar items must attach to a campaign.
      </div>
    );
  }

  return (
    <div className="rounded-md border border-[color:var(--color-accent)]/30 bg-[color:var(--color-accent)]/8 p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone="info">calendar handoff</Badge>
        <span className="text-xs font-semibold">
          Create draft content calendar
        </span>
        <span className="text-[10px] text-[color:var(--color-ink-muted)]">
          status = draft · shared_with_client = false · no paid call
        </span>
      </div>
      {!open ? (
        <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
          Create draft content calendar
        </Button>
      ) : (
        <div className="space-y-3">
          <div
            role="note"
            className="rounded-md border border-[color:var(--color-hairline)] bg-white px-2 py-1.5 text-[11px] leading-relaxed text-[color:var(--color-ink-muted)]"
          >
            This creates draft content items only. It does not generate
            videos. It does not approve a prompt. It does not create a
            generation job. It does not share with the client. It does
            not send any email.
          </div>

          <div className="grid sm:grid-cols-2 gap-3">
            <label className="block space-y-1">
              <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                Target campaign
              </span>
              <select
                value={campaignId}
                onChange={(e) => setCampaignId(e.target.value)}
                className="w-full h-9 px-2 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm"
              >
                {campaigns.map((c) => (
                  <option key={c.campaignId} value={c.campaignId}>
                    {c.brandName} · {c.campaignTitle}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1">
              <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                Start date (D+0)
              </span>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full h-9 px-2 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm font-mono"
              />
            </label>
          </div>

          <div className="space-y-1.5">
            <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
              Calendar ideas to materialise ({selected.size} of {ideas.length} selected)
            </div>
            <ul className="space-y-1.5">
              {ideas.map((i) => (
                <li
                  key={i.dayOffset}
                  className="rounded-md border border-[color:var(--color-hairline)] bg-white p-2 text-xs"
                >
                  <label className="flex items-start gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selected.has(i.dayOffset)}
                      onChange={() => toggle(i.dayOffset)}
                      className="mt-0.5"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge tone="info">D+{i.dayOffset}</Badge>
                        <span className="font-semibold">
                          {i.title ?? i.label}
                        </span>
                        {i.suggestedChannel && (
                          <Badge tone="neutral">{i.suggestedChannel}</Badge>
                        )}
                        {i.suggestedFormat && (
                          <Badge tone="neutral">{i.suggestedFormat}</Badge>
                        )}
                        {i.distributionType && (
                          <Badge
                            tone={
                              i.distributionType === "paid"
                                ? "warn"
                                : "neutral"
                            }
                          >
                            {i.distributionType}
                          </Badge>
                        )}
                        <Badge
                          tone={i.needsGeneration ? "warn" : "success"}
                        >
                          {i.needsGeneration
                            ? "needs video gen later"
                            : "no video gen"}
                        </Badge>
                      </div>
                      <div className="mt-0.5 text-[color:var(--color-ink-muted)] leading-relaxed">
                        {i.brief}
                      </div>
                    </div>
                  </label>
                </li>
              ))}
            </ul>
          </div>

          <label className="block space-y-1">
            <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
              Operator note (optional, appended to each row&rsquo;s prompt_summary)
            </span>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              maxLength={500}
              placeholder="e.g. focus on creator-led variants this cycle"
              className="w-full h-9 px-2 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm"
            />
          </label>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              onClick={onSubmit}
              disabled={pending || selected.size === 0 || !campaignId}
            >
              {pending
                ? "Creating drafts…"
                : `Create ${selected.size} draft content item${selected.size === 1 ? "" : "s"}`}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {res && (
        <div
          role="status"
          className={
            res.ok
              ? "rounded-md border border-[color:var(--color-success)]/30 bg-[color:var(--color-success)]/10 px-3 py-2 text-xs"
              : "rounded-md border border-[color:var(--color-danger)]/30 bg-[color:var(--color-danger)]/10 px-3 py-2 text-xs"
          }
        >
          {res.ok ? (
            <>
              <div className="font-semibold">{res.message ?? "Created."}</div>
              {res.contentItemIds && res.contentItemIds.length > 0 && (
                <BulkPromptDraftsPanel
                  contentItemIds={res.contentItemIds}
                />
              )}
              {res.calendarHref && (
                <a
                  href={res.calendarHref}
                  className="text-[color:var(--color-accent)] underline mt-2 inline-block"
                >
                  Open campaign calendar →
                </a>
              )}
            </>
          ) : (
            <div>
              <span className="font-semibold">Couldn&rsquo;t create: </span>
              {res.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- #
// Phase 2B — bulk "Create prompt drafts for selected items". The
// Calendar Agent wrote the agent_run_id into each new content item's
// prompt_summary, so the bulk action auto-resolves the source run per
// item. This NEVER approves a prompt, creates a generation job, calls a
// provider, or shares with the client. One item failing does not stop
// the others (partial success is surfaced).

function BulkPromptDraftsPanel({
  contentItemIds,
}: {
  contentItemIds: string[];
}) {
  const [selected, setSelected] = React.useState<Set<string>>(
    () => new Set(contentItemIds),
  );
  const [pending, startTransition] = React.useTransition();
  const [res, setRes] = React.useState<BulkPromptDraftsResult | null>(null);

  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  }

  function onCreate() {
    setRes(null);
    startTransition(async () => {
      const r = await createPromptDraftsForCalendarItemsBulkAction({
        contentItemIds: Array.from(selected),
      });
      setRes(r);
    });
  }

  const resultById = new Map(
    (res?.perItem ?? []).map((i) => [i.contentItemId, i]),
  );

  return (
    <div className="mt-2 rounded-md border border-[color:var(--color-hairline)] bg-white p-2 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone="info">prompt drafts</Badge>
        <span className="text-[11px] font-semibold">
          Create prompt drafts for selected items
        </span>
        <span className="text-[10px] text-[color:var(--color-ink-muted)]">
          {selected.size} of {contentItemIds.length} selected
        </span>
      </div>
      <p className="text-[10px] text-[color:var(--color-ink-faint)] italic">
        This creates operator_editing prompt drafts only. It does not
        generate videos, does not approve, does not call any paid API,
        does not share with the client.
      </p>
      <ul className="space-y-1">
        {contentItemIds.map((id) => {
          const r = resultById.get(id);
          return (
            <li
              key={id}
              className="rounded-md border border-[color:var(--color-hairline)] px-2 py-1.5 text-[11px]"
            >
              <label className="flex items-start gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={selected.has(id)}
                  onChange={() => toggle(id)}
                  disabled={pending}
                  className="mt-0.5"
                />
                <div className="min-w-0 flex-1">
                  <div className="font-mono break-all text-[10px] text-[color:var(--color-ink-muted)]">
                    {id}
                  </div>
                  {r && (
                    <div
                      className={
                        r.ok
                          ? "mt-1 text-[color:var(--color-success)]"
                          : "mt-1 text-[color:var(--color-danger)]"
                      }
                    >
                      {r.ok ? (
                        <>
                          <span className="font-semibold">
                            {r.promptVersionIds.length} draft(s)
                          </span>
                          {r.draftSource && (
                            <span className="text-[color:var(--color-ink-muted)]">
                              {" "}
                              · {r.draftSource}
                            </span>
                          )}
                          <ul className="font-mono break-all text-[10px]">
                            {r.promptVersionIds.map((p) => (
                              <li key={p}>· {p}</li>
                            ))}
                          </ul>
                          {r.editorHref && (
                            <a
                              href={r.editorHref}
                              className="text-[color:var(--color-accent)] underline"
                            >
                              Open prompt editor →
                            </a>
                          )}
                        </>
                      ) : (
                        <span>failed: {r.error}</span>
                      )}
                    </div>
                  )}
                </div>
              </label>
            </li>
          );
        })}
      </ul>
      <Button
        size="sm"
        variant="secondary"
        onClick={onCreate}
        disabled={pending || selected.size === 0}
      >
        {pending
          ? "Creating prompt drafts…"
          : `Create prompt drafts for ${selected.size} item${selected.size === 1 ? "" : "s"}`}
      </Button>
      {res && (
        <div
          role="status"
          className={
            res.allSucceeded
              ? "rounded-md border border-[color:var(--color-success)]/30 bg-[color:var(--color-success)]/10 px-2 py-1.5 text-[11px]"
              : res.ok
                ? "rounded-md border border-[color:var(--color-warn)]/40 bg-[color:var(--color-warn)]/10 px-2 py-1.5 text-[11px]"
                : "rounded-md border border-[color:var(--color-danger)]/30 bg-[color:var(--color-danger)]/10 px-2 py-1.5 text-[11px]"
          }
        >
          <div className="font-semibold">
            {res.message ?? res.error ?? "Done."}
          </div>
          <div className="text-[10px] text-[color:var(--color-ink-muted)]">
            created {res.createdCount} · failed {res.failedCount}
            {res.allSucceeded ? " · all succeeded" : ""}
          </div>
        </div>
      )}
    </div>
  );
}
