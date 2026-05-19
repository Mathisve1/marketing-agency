"use client";

// Phase 1E — operator prompt-version editor.
//
// OPERATOR-ONLY. Renders editable text areas for the prompt fields, a
// quality-tier picker (default 720p per the locked-in strategic
// decision), and Save draft + Mark approved-for-generation buttons.
//
// IMPORTANT: this component DOES NOT trigger any paid Enhancor /
// Seedance / Audio Fixer call. "Mark approved for generation" only
// records intent; the actual generation handoff lives later in the
// roadmap.

import * as React from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  savePromptVersionDraftAction,
  markPromptVersionApprovedForGenerationAction,
} from "@/lib/actions/prompt-versions";
import type {
  PromptVersion,
  PromptVersionQualityTier,
} from "@/lib/data/prompt-versions";

interface Props {
  promptVersion: PromptVersion;
  campaignId: string;
  contentId: string;
}

interface FormState {
  label: string;
  hook: string;
  script: string;
  promptBody: string;
  negativePrompt: string;
  scenePlan: string;
  creatorDirection: string;
  productConstraints: string;
  qualityTier: PromptVersionQualityTier;
  notes: string;
}

function initialState(pv: PromptVersion): FormState {
  return {
    label: pv.label ?? "",
    hook: pv.hook ?? "",
    script: pv.script ?? "",
    promptBody: pv.promptBody ?? "",
    negativePrompt: pv.negativePrompt ?? "",
    scenePlan: pv.scenePlan ?? "",
    creatorDirection: pv.creatorDirection ?? "",
    productConstraints: pv.productConstraints ?? "",
    qualityTier: pv.qualityTier,
    notes: pv.notes ?? "",
  };
}

const TIER_LABEL: Record<PromptVersionQualityTier, string> = {
  draft_480p: "Draft · 480p",
  standard_720p: "Standard · 720p (default)",
  premium_1080p: "Premium · 1080p",
};

export function PromptVersionEditor({
  promptVersion,
  campaignId,
  contentId,
}: Props) {
  const [form, setForm] = React.useState<FormState>(initialState(promptVersion));
  const [flash, setFlash] = React.useState<
    { kind: "ok"; message: string } | { kind: "err"; error: string } | null
  >(null);
  const [pending, startTransition] = React.useTransition();

  function set<K extends keyof FormState>(field: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  function onSaveDraft(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setFlash(null);
    startTransition(async () => {
      const result = await savePromptVersionDraftAction({
        promptVersionId: promptVersion.id,
        campaignId,
        contentId,
        ...form,
      });
      if (result.ok) setFlash({ kind: "ok", message: result.message ?? "Saved." });
      else setFlash({ kind: "err", error: result.error ?? "Save failed." });
    });
  }

  function onMarkApproved() {
    setFlash(null);
    startTransition(async () => {
      // Save the in-progress edits first so the approved version
      // reflects the latest text — UX expectation.
      const saveResult = await savePromptVersionDraftAction({
        promptVersionId: promptVersion.id,
        campaignId,
        contentId,
        ...form,
      });
      if (!saveResult.ok) {
        setFlash({
          kind: "err",
          error: saveResult.error ?? "Save before approve failed.",
        });
        return;
      }
      const result = await markPromptVersionApprovedForGenerationAction({
        promptVersionId: promptVersion.id,
        campaignId,
        contentId,
      });
      if (result.ok) setFlash({ kind: "ok", message: result.message ?? "Approved." });
      else setFlash({ kind: "err", error: result.error ?? "Approve failed." });
    });
  }

  return (
    <form className="space-y-5" onSubmit={onSaveDraft}>
      <div className="grid sm:grid-cols-[1fr_240px] gap-3">
        <Field
          label="Version label"
          hint="Operator nickname. Not shown to clients."
        >
          <input
            value={form.label}
            onChange={(e) => set("label", e.target.value)}
            className="w-full h-10 px-3 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm outline-none focus:border-[color:var(--color-accent)]"
            placeholder="e.g. 720p stricter label-text"
          />
        </Field>
        <Field label="Quality tier" hint="Default 720p.">
          <select
            value={form.qualityTier}
            onChange={(e) =>
              set("qualityTier", e.target.value as PromptVersionQualityTier)
            }
            className="w-full h-10 px-3 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm outline-none focus:border-[color:var(--color-accent)]"
          >
            {(
              [
                "draft_480p",
                "standard_720p",
                "premium_1080p",
              ] as PromptVersionQualityTier[]
            ).map((t) => (
              <option key={t} value={t}>
                {TIER_LABEL[t]}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Field label="Hook" hint="Spoken line that opens the video.">
        <Textarea
          value={form.hook}
          onChange={(e) => set("hook", e.target.value)}
          rows={2}
        />
      </Field>
      <Field label="Script" hint="Full VO / on-camera script.">
        <Textarea
          value={form.script}
          onChange={(e) => set("script", e.target.value)}
          rows={5}
        />
      </Field>
      <Field label="Prompt body" hint="Main provider prompt.">
        <Textarea
          value={form.promptBody}
          onChange={(e) => set("promptBody", e.target.value)}
          rows={5}
        />
      </Field>
      <Field
        label="Negative prompt"
        hint="What the model should avoid. Strong guardrails go here."
      >
        <Textarea
          value={form.negativePrompt}
          onChange={(e) => set("negativePrompt", e.target.value)}
          rows={4}
        />
      </Field>
      <Field label="Scene plan" hint="Beat-by-beat shot timing.">
        <Textarea
          value={form.scenePlan}
          onChange={(e) => set("scenePlan", e.target.value)}
          rows={4}
        />
      </Field>
      <Field label="Creator direction" hint="Acting, wardrobe, body language.">
        <Textarea
          value={form.creatorDirection}
          onChange={(e) => set("creatorDirection", e.target.value)}
          rows={4}
        />
      </Field>
      <Field
        label="Product constraints"
        hint="Label, packaging, brand-name spelling, claim guards."
      >
        <Textarea
          value={form.productConstraints}
          onChange={(e) => set("productConstraints", e.target.value)}
          rows={4}
        />
      </Field>
      <Field
        label="Operator notes"
        hint="Change-log: what you changed and why. Never shown to the client."
      >
        <Textarea
          value={form.notes}
          onChange={(e) => set("notes", e.target.value)}
          rows={3}
        />
      </Field>

      {flash?.kind === "ok" && (
        <div className="text-sm rounded-md bg-[color:var(--color-success)]/10 border border-[color:var(--color-success)]/30 px-3 py-2">
          {flash.message}
        </div>
      )}
      {flash?.kind === "err" && (
        <div className="text-sm rounded-md bg-[color:var(--color-danger)]/10 border border-[color:var(--color-danger)]/30 px-3 py-2">
          {flash.error}
        </div>
      )}

      <div className="flex flex-wrap justify-end gap-2 pt-2 border-t border-[color:var(--color-hairline)]">
        <Button type="submit" variant="secondary" disabled={pending}>
          {pending ? "Saving…" : "Save draft"}
        </Button>
        <Button
          type="button"
          variant="success"
          disabled={
            pending || promptVersion.status === "approved_for_generation"
          }
          onClick={onMarkApproved}
        >
          {promptVersion.status === "approved_for_generation"
            ? "✓ Approved for generation"
            : "Mark approved for generation"}
        </Button>
      </div>
      <p className="text-xs text-[color:var(--color-ink-faint)] italic">
        Marking approved records intent only — no paid generation call is
        triggered in Phase 1E.
      </p>
    </form>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label className="block text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
        {label}
      </label>
      {hint && (
        <p className="text-xs text-[color:var(--color-ink-muted)]">{hint}</p>
      )}
      {children}
    </div>
  );
}
