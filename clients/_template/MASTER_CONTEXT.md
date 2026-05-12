---
schema_version: 1
client:
  id: _template
  name: "[Client Name]"
  locale: nl-BE
  last_updated: 2026-01-01T00:00:00Z

brand:
  voice_attributes: []
  forbidden_terms: []
  primary_products: []
  brand_safety_notes: null

winning_hooks: []
referral_motions: []
negative_constraints: []

performance_benchmarks:
  roas_target: null
  ctr_target: null
  last_analyst_run: null

asset_inventory:
  products_dir: references/products/
  characters_dir: references/characters/
  referral_videos_dir: references/referral_videos/
---

# Master Context — [Client Name]

## Brand Narrative

To be populated by the Strategist's initial brand audit, or filled in manually
before the first research run. This section is read verbatim into LLM context
windows — keep it focused on voice, positioning, and key differentiators.

## Audience Insights

Demographics, psychographics, language preferences, and common objections.

## Recent Strategic Notes

Append-only log of findings from each Strategist research run (most recent first).
