"use client";

// Phase 1W — Brand Analysis form + result viewer.
//
// Operator pastes a product URL (plus optional brand/tone/audience/notes)
// and the deterministic planner returns a structured draft. NO paid call.
// NO website fetch. NO DB write. Output is preview-only this phase.

import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import {
  runBrandAnalysisAgentAction,
  type BrandAnalysisActionResult,
} from "@/lib/actions/brand-analysis";
import { createPromptVersionFromAgentDraftAction } from "@/lib/actions/prompt-versions";
import type {
  BrandAnalysisPlan,
  PromptDraft,
} from "@/lib/agents/brand-analysis";

interface BrandOption {
  id: string;
  name: string;
  niche: string;
  tone: string;
  audience: string;
  websiteUrl?: string;
}

export interface TargetOption {
  contentItemId: string;
  contentTitle: string;
  campaignId: string;
  campaignTitle: string;
  brandId: string;
  brandName: string;
}

interface Props {
  brands: BrandOption[];
  targets: TargetOption[];
}

export function BrandAnalysisForm({ brands, targets }: Props) {
  const [productUrl, setProductUrl] = React.useState("");
  const [brandId, setBrandId] = React.useState<string>("");
  const [notes, setNotes] = React.useState("");
  const [overrideName, setOverrideName] = React.useState("");
  const [overrideNiche, setOverrideNiche] = React.useState("");
  const [pending, startTransition] = React.useTransition();
  const [result, setResult] = React.useState<BrandAnalysisActionResult | null>(
    null,
  );

  const selectedBrand = brands.find((b) => b.id === brandId) ?? null;

  // When the operator picks a brand, pre-fill the URL with their site
  // (still editable). This is purely UX sugar; nothing is fetched.
  React.useEffect(() => {
    if (selectedBrand?.websiteUrl && !productUrl) {
      setProductUrl(selectedBrand.websiteUrl);
    }
  }, [selectedBrand, productUrl]);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setResult(null);
    startTransition(async () => {
      const r = await runBrandAnalysisAgentAction({
        productUrl,
        brandName:
          overrideName.trim() || selectedBrand?.name || undefined,
        brandNiche:
          overrideNiche.trim() || selectedBrand?.niche || undefined,
        brandTone: selectedBrand?.tone,
        audienceAssumption: selectedBrand?.audience,
        operatorNotes: notes.trim() || undefined,
      });
      setResult(r);
    });
  }

  return (
    <div className="space-y-5 max-w-5xl">
      <form onSubmit={onSubmit} className="space-y-4">
        <Field
          label="Product URL"
          required
          help="The page that describes the product. http:// or https://. Nothing is fetched — the URL is parsed locally to pick the matching niche template."
        >
          <input
            type="url"
            value={productUrl}
            onChange={(e) => setProductUrl(e.target.value)}
            placeholder="https://paiskincare.com/products/rosehip-bioregenerate-oil"
            required
            className="w-full h-10 px-3 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm font-mono outline-none focus:border-[color:var(--color-accent)]"
          />
        </Field>

        <div className="grid sm:grid-cols-2 gap-4">
          <Field label="Brand (optional)" help="Pre-fills tone + audience.">
            <select
              value={brandId}
              onChange={(e) => setBrandId(e.target.value)}
              className="w-full h-10 px-3 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm outline-none focus:border-[color:var(--color-accent)]"
            >
              <option value="">— No brand selected —</option>
              {brands.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name} · {b.niche}
                </option>
              ))}
            </select>
          </Field>

          <Field
            label="Brand name override (optional)"
            help="Used in prompt draft labels + negative-prompt brand-spelling guard."
          >
            <input
              type="text"
              value={overrideName}
              onChange={(e) => setOverrideName(e.target.value)}
              placeholder={selectedBrand?.name ?? "e.g. Pai Skincare"}
              maxLength={200}
              className="w-full h-10 px-3 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm outline-none focus:border-[color:var(--color-accent)]"
            />
          </Field>
        </div>

        <Field
          label="Niche override (optional)"
          help="Force a specific niche template. Leave blank to let the URL decide. Known: skincare · supplements · fitness_apparel · coffee_beverages · saas_b2b · fashion_apparel · home_kitchen."
        >
          <input
            type="text"
            value={overrideNiche}
            onChange={(e) => setOverrideNiche(e.target.value)}
            placeholder={selectedBrand?.niche ?? "e.g. skincare"}
            className="w-full h-10 px-3 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm outline-none focus:border-[color:var(--color-accent)]"
          />
        </Field>

        <Field
          label="Operator notes (optional)"
          help="Anything you want the plan to echo back — angle ideas, risks, prior failures. Free text. Echoed verbatim in the output; never interpreted as a claim."
        >
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. Avoid clinical-white studios. Prior take had label hallucination — keep label off-camera."
            maxLength={4000}
            rows={4}
            className="w-full px-3 py-2 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm outline-none focus:border-[color:var(--color-accent)]"
          />
        </Field>

        <div
          role="note"
          className="rounded-md border border-[color:var(--color-accent)]/30 bg-[color:var(--color-accent)]/8 px-3 py-2 text-xs"
        >
          <strong>This creates a planning draft only.</strong> It does not
          generate videos, does not spend credits, does not contact the
          client, and does not write to the database. Every output field is
          a hypothesis — the operator must verify against the real source
          before any client share.
        </div>

        <div className="flex items-center gap-3">
          <Button
            variant="primary"
            type="submit"
            disabled={pending || productUrl.trim() === ""}
          >
            {pending ? "Analyzing…" : "Analyze product and create UGC plan"}
          </Button>
          {result?.error && (
            <span className="text-sm text-[color:var(--color-danger)]">
              {result.error}
            </span>
          )}
        </div>
      </form>

      {result?.ok && result.persistenceWarning && (
        <div
          role="status"
          className="rounded-md border border-[color:var(--color-warn)]/40 bg-[color:var(--color-warn)]/10 px-3 py-2 text-xs"
        >
          <Badge tone="warn">preview only</Badge>{" "}
          {result.persistenceWarning}
        </div>
      )}
      {result?.ok && result.plan && (
        <PlanView
          plan={result.plan}
          targets={targets}
          agentRunId={result.agentRunId}
        />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- #
// Result viewer

function PlanView({
  plan,
  targets,
  agentRunId,
}: {
  plan: BrandAnalysisPlan;
  targets: TargetOption[];
  agentRunId?: string;
}) {
  return (
    <div className="space-y-5 pt-3 border-t border-[color:var(--color-hairline)]">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone="info">matched niche</Badge>
        <span className="text-sm font-medium">
          {plan.matchedNiche.replaceAll("_", " ")}
        </span>
        {agentRunId ? (
          <span className="text-[10px] font-mono text-[color:var(--color-ink-muted)] break-all">
            · agent_run {agentRunId.slice(0, 8)}…
          </span>
        ) : (
          <span className="text-xs text-[color:var(--color-ink-muted)]">
            · preview only · no DB write
          </span>
        )}
        <span className="text-xs text-[color:var(--color-ink-muted)]">
          · no paid call
        </span>
      </div>

      <SectionCard title="Brand brief (hypothesis)">
        <HypothesisVerify
          hypotheses={plan.brandBrief.hypotheses}
          verify={plan.brandBrief.verify}
        />
      </SectionCard>

      <SectionCard title="Product summary (hypothesis)">
        <div className="text-sm space-y-1.5">
          <Line label="Inferred category" value={plan.productSummary.inferredCategory} />
          <Line label="Inferred form" value={plan.productSummary.inferredForm} />
        </div>
        <HypothesisVerify
          hypotheses={plan.productSummary.hypotheses}
          verify={plan.productSummary.verify}
        />
      </SectionCard>

      <SectionCard title="Target audience (hypothesis)">
        <div className="text-sm space-y-1.5">
          <Line label="Primary" value={plan.targetAudience.primary} />
          <Line label="Secondary" value={plan.targetAudience.secondary} />
        </div>
        <HypothesisVerify
          hypotheses={plan.targetAudience.hypotheses}
          verify={plan.targetAudience.verify}
        />
      </SectionCard>

      <SectionCard title="Key selling points">
        <ul className="text-sm space-y-2">
          {plan.keySellingPoints.map((p) => (
            <li key={p.title} className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3">
              <div className="font-semibold">{p.title}</div>
              <div className="text-xs text-[color:var(--color-ink-muted)] mt-0.5">
                {p.rationale}
              </div>
            </li>
          ))}
        </ul>
      </SectionCard>

      <SectionCard title="Objections / trust issues">
        <ul className="text-sm space-y-2">
          {plan.objections.map((o) => (
            <li key={o.title} className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3">
              <div className="font-semibold">{o.title}</div>
              <div className="text-xs text-[color:var(--color-ink-muted)] mt-0.5">
                {o.rebuttal}
              </div>
            </li>
          ))}
        </ul>
      </SectionCard>

      <SectionCard title="Content angles">
        <ul className="text-sm space-y-2">
          {plan.contentAngles.map((a) => (
            <li key={a.title} className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3">
              <div className="font-semibold">{a.title}</div>
              <div className="text-xs text-[color:var(--color-ink-muted)] mt-0.5">
                {a.idea}
              </div>
            </li>
          ))}
        </ul>
      </SectionCard>

      <SectionCard title="UGC scenes (15s)">
        <ul className="text-sm space-y-2">
          {plan.ugcScenes.map((s) => (
            <li key={s.title} className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3">
              <div className="flex items-center gap-2">
                <Badge tone="neutral">{s.durationSec}s</Badge>
                <span className="font-semibold">{s.title}</span>
              </div>
              <div className="text-xs text-[color:var(--color-ink-muted)] mt-1">
                {s.scene}
              </div>
            </li>
          ))}
        </ul>
      </SectionCard>

      <SectionCard title="Prompt draft suggestions">
        <div className="space-y-4">
          {plan.promptDrafts.map((d, i) => (
            <PromptDraftCard
              key={i}
              draft={d}
              targets={targets}
              sourceMetadata={{
                productUrl: plan.inputs.productUrl,
                agentType: "brand_analysis_ugc_prompt_planning",
                matchedNiche: plan.matchedNiche,
                agentRunId,
              }}
            />
          ))}
          <p className="text-[10px] text-[color:var(--color-ink-faint)] italic">
            &ldquo;Create draft prompt version&rdquo; writes a new
            <code className="font-mono"> prompt_versions</code> row with
            <strong> status = operator_editing</strong> only. It never
            sets <em>approved_for_generation</em>, never creates a
            generation job, never calls a paid API.
          </p>
        </div>
      </SectionCard>

      <SectionCard title="Content calendar ideas">
        <ul className="text-sm space-y-2">
          {plan.contentCalendarIdeas.map((d) => (
            <li
              key={d.label}
              className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3"
            >
              <div className="flex items-center gap-2">
                <Badge tone="info">D+{d.dayOffset}</Badge>
                <span className="font-semibold">{d.label}</span>
              </div>
              <div className="text-xs text-[color:var(--color-ink-muted)] mt-1">
                {d.brief}
              </div>
            </li>
          ))}
        </ul>
      </SectionCard>

      <SectionCard title="Caveats">
        <ul className="text-xs space-y-1 list-disc list-inside text-[color:var(--color-ink-muted)]">
          {plan.caveats.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      </SectionCard>
    </div>
  );
}

function PromptDraftCard({
  draft,
  targets,
  sourceMetadata,
}: {
  draft: PromptDraft;
  targets: TargetOption[];
  sourceMetadata: {
    productUrl: string;
    agentType: "brand_analysis_ugc_prompt_planning";
    matchedNiche: string;
    agentRunId?: string;
  };
}) {
  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone="info">prompt draft</Badge>
        <span className="text-sm font-semibold">{draft.label}</span>
      </div>
      <CopyBlock label="Hook" value={draft.hook} />
      <CopyBlock label="Script" value={draft.script} />
      <CopyBlock label="Prompt body" value={draft.promptBody} />
      <CopyBlock label="Scene plan" value={draft.scenePlan} />
      <CopyBlock label="Creator direction" value={draft.creatorDirection} />
      <CopyBlock label="Product constraints" value={draft.productConstraints} />
      <CopyBlock
        label={`Negative prompt (${draft.negativePrompt.length}/500 chars)`}
        value={draft.negativePrompt}
      />
      <CreatePromptVersionPanel
        draft={draft}
        targets={targets}
        sourceMetadata={sourceMetadata}
      />
    </div>
  );
}

interface CreateResult {
  ok: boolean;
  promptVersionId?: string;
  editorHref?: string;
  message?: string;
  error?: string;
}

function CreatePromptVersionPanel({
  draft,
  targets,
  sourceMetadata,
}: {
  draft: PromptDraft;
  targets: TargetOption[];
  sourceMetadata: {
    productUrl: string;
    agentType: "brand_analysis_ugc_prompt_planning";
    matchedNiche: string;
    agentRunId?: string;
  };
}) {
  const [open, setOpen] = React.useState(false);
  const [contentItemId, setContentItemId] = React.useState<string>(
    targets[0]?.contentItemId ?? "",
  );
  const [callerNotes, setCallerNotes] = React.useState("");
  const [pending, startTransition] = React.useTransition();
  const [res, setRes] = React.useState<CreateResult | null>(null);

  const selectedTarget = targets.find((t) => t.contentItemId === contentItemId);

  function onCreate() {
    setRes(null);
    if (!contentItemId) {
      setRes({ ok: false, error: "Pick a content item first." });
      return;
    }
    startTransition(async () => {
      const r = await createPromptVersionFromAgentDraftAction({
        contentItemId,
        label: draft.label,
        hook: draft.hook,
        script: draft.script,
        promptBody: draft.promptBody,
        scenePlan: draft.scenePlan,
        creatorDirection: draft.creatorDirection,
        productConstraints: draft.productConstraints,
        negativePrompt: draft.negativePrompt,
        callerNotes: callerNotes.trim() || undefined,
        sourceMetadata,
      });
      setRes(r);
    });
  }

  if (targets.length === 0) {
    return (
      <div className="rounded-md border border-[color:var(--color-warn)]/40 bg-[color:var(--color-warn)]/10 px-3 py-2 text-xs">
        <Badge tone="warn">No targets</Badge>{" "}
        No content items exist in this workspace yet. Create a campaign +
        content item first, then come back to attach this draft.
      </div>
    );
  }

  return (
    <div className="rounded-md border border-[color:var(--color-accent)]/30 bg-[color:var(--color-accent)]/8 p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone="info">handoff</Badge>
        <span className="text-xs font-semibold">
          Create draft prompt version
        </span>
        <span className="text-[10px] text-[color:var(--color-ink-muted)]">
          status = operator_editing · never approved for generation
        </span>
      </div>
      {!open ? (
        <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
          Create draft prompt version
        </Button>
      ) : (
        <div className="space-y-2">
          <label className="block space-y-1">
            <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
              Target content item
            </span>
            <select
              value={contentItemId}
              onChange={(e) => setContentItemId(e.target.value)}
              className="w-full h-9 px-2 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm"
            >
              {targets.map((t) => (
                <option key={t.contentItemId} value={t.contentItemId}>
                  {t.brandName} · {t.campaignTitle} · {t.contentTitle}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
              Operator note (optional, appended to prompt_versions.notes)
            </span>
            <input
              type="text"
              value={callerNotes}
              onChange={(e) => setCallerNotes(e.target.value)}
              placeholder="why this draft / what to verify"
              maxLength={500}
              className="w-full h-9 px-2 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm"
            />
          </label>
          <div
            role="note"
            className="rounded-md border border-[color:var(--color-hairline)] bg-white px-2 py-1.5 text-[11px] leading-relaxed text-[color:var(--color-ink-muted)]"
          >
            This creates a draft prompt only. It does not generate
            videos. It does not flip <em>approved_for_generation</em>.
            It does not create a generation job. It does not call any
            paid API.
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              onClick={onCreate}
              disabled={pending || !contentItemId}
            >
              {pending ? "Saving draft…" : "Save as draft prompt version"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            {selectedTarget && (
              <span className="text-[10px] text-[color:var(--color-ink-muted)]">
                will attach to: {selectedTarget.brandName} ·{" "}
                {selectedTarget.contentTitle}
              </span>
            )}
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
              {res.promptVersionId && (
                <div className="mt-0.5 font-mono break-all">
                  prompt_version: {res.promptVersionId}
                </div>
              )}
              {res.editorHref && (
                <a
                  href={res.editorHref}
                  className="text-[color:var(--color-accent)] underline"
                >
                  Open in prompt editor →
                </a>
              )}
            </>
          ) : (
            <div>
              <span className="font-semibold">Couldn&rsquo;t save: </span>
              {res.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CopyBlock({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = React.useState(false);
  async function onCopy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      /* operator can still select+copy manually */
    }
  }
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          {label}
        </span>
        <button
          type="button"
          onClick={onCopy}
          className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-accent)] underline"
        >
          {copied ? "copied" : "copy"}
        </button>
      </div>
      <pre className="text-[11px] leading-snug bg-[color:var(--color-cream-soft)] border border-[color:var(--color-hairline)] rounded-md p-2 overflow-x-auto whitespace-pre-wrap break-words">
        {value}
      </pre>
    </div>
  );
}

function SectionCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardBody className="space-y-3">{children}</CardBody>
    </Card>
  );
}

function HypothesisVerify({
  hypotheses,
  verify,
}: {
  hypotheses: string[];
  verify: string[];
}) {
  return (
    <div className="grid sm:grid-cols-2 gap-3 text-sm">
      <div>
        <div className="flex items-center gap-2 mb-1">
          <Badge tone="warn">Hypothesis</Badge>
          <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
            do not use as final claim
          </span>
        </div>
        <ul className="space-y-1 list-disc list-inside text-[color:var(--color-ink-muted)]">
          {hypotheses.map((h, i) => (
            <li key={i}>{h}</li>
          ))}
        </ul>
      </div>
      <div>
        <div className="flex items-center gap-2 mb-1">
          <Badge tone="info">Needs operator verification</Badge>
        </div>
        <ul className="space-y-1 list-disc list-inside text-[color:var(--color-ink-muted)]">
          {verify.map((v, i) => (
            <li key={i}>{v}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function Field({
  label,
  help,
  required = false,
  children,
}: {
  label: string;
  help?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
        {label}
        {required && <span className="ml-1 text-[color:var(--color-danger)]">*</span>}
      </div>
      {children}
      {help && (
        <p className="text-[10px] text-[color:var(--color-ink-faint)] italic leading-relaxed">
          {help}
        </p>
      )}
    </label>
  );
}

function Line({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[color:var(--color-ink-muted)]">{label}</span>
      <span className="text-right">{value}</span>
    </div>
  );
}
