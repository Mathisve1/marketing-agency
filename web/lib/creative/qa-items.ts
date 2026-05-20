// Yuvo Studio — Phase 4F shared QA checklist item set.
//
// Pure constants. Lives in lib/ so the persisted client-side save panel
// (`CreativePreviewQAPanel`, mounted on the preview page) and the
// server action whitelist (`web/lib/actions/creative-preview-qa.ts`)
// read the same source of truth.
//
// Item ids are persisted in the `[creative preview QA]` block
// (parser key `qa_items: id=pass,id=fail,…`). The whitelist in
// `web/lib/actions/creative-preview-qa.ts` MUST stay in sync with
// the ids here. Phase 4E's read-only `PreviewQAChecklist` placeholder
// has been retired; the persisted panel is now the single source of
// QA truth on the preview page.

export interface QAItem {
  id: string;
  label: string;
}

export const QA_ITEMS: QAItem[] = [
  { id: "text_readable", label: "Text is readable at preview size" },
  { id: "cta_visible", label: "CTA is visible / present" },
  { id: "layout_fits", label: "Layout fits the format (4:5 / 9:16 / 1:1)" },
  { id: "no_forbidden_text", label: "No forbidden text (medical claims, competitor brands)" },
  { id: "no_internal_notes_visible", label: "No internal / operator notes visible on the surface" },
  { id: "brand_tone_ok", label: "Brand tone matches the brand guide" },
  { id: "claim_safe", label: "Product / offer claim is safe and verifiable" },
  { id: "ready_for_export_later", label: "Ready for export later (Phase 4D pipe)" },
];
