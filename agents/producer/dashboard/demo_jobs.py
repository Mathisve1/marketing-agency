"""Demo-mode catalogue of `generation_jobs` rows.

Mirrors the demo store inside `web/lib/data/generation-jobs.ts` so the
Phase 1G operator job runner has a single Python-side source of truth
without needing a Supabase client.

Each entry carries the JOIN-ed fields the Seedance payload builder
needs to construct a UGC request:

  - quality_tier / resolution / duration_seconds from `generation_jobs`
  - prompt fields (hook / script / prompt_body / negative_prompt / etc.)
    from the linked `prompt_versions` row
  - title + audience hints from the parent `content_items` row

Direct Supabase reads from Python are deliberately deferred to Phase 1H.
This module is the explicit adapter boundary: callers ask for a job by
id, this module returns a `DemoGenerationJob`, and the rest of the
pipeline is provider-only Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional


@dataclass(frozen=True)
class DemoGenerationJob:
    """The slice of a `generation_jobs` row + its joins that the
    Seedance payload builder needs.

    Field names mirror the snake_case columns in
    `supabase/migrations/005_generation_jobs.sql` so a future Supabase
    adapter can return the same shape without renaming.
    """

    # generation_jobs columns
    id: str
    batch_id: str
    content_item_id: str
    prompt_version_id: str
    provider: str
    provider_mode: str
    quality_tier: str
    resolution: str
    duration_seconds: int
    status: str
    estimated_credits: int

    # joined prompt_versions columns (operator-only)
    prompt_label: Optional[str]
    prompt_hook: Optional[str]
    prompt_body: Optional[str]
    prompt_negative: Optional[str]
    prompt_scene_plan: Optional[str]
    prompt_creator_direction: Optional[str]
    prompt_product_constraints: Optional[str]

    # joined content_items columns
    content_title: Optional[str]
    content_caption_draft: Optional[str]

    # Optional default asset URLs. These are NOT real Pai URLs — they
    # exist so a `--dry-run` invocation with no --product-url /
    # --influencer-url override still produces a payload-shape that
    # passes the provider's HTTPS validator. The runner refuses to
    # --submit with these placeholders in place.
    placeholder_product_urls: tuple[str, ...] = field(default_factory=tuple)
    placeholder_influencer_urls: tuple[str, ...] = field(default_factory=tuple)


# ----------------------------------------------------------------------------- #
# Demo catalogue — mirrors the demo store inside
# web/lib/data/generation-jobs.ts + the relevant fields from the seeded
# prompt_versions / content_items entries.
# ----------------------------------------------------------------------------- #

_HISTORICAL_JOB_ID = "0a0a0a0a-0a0a-0a0a-0a0a-0a0a0a0a0a0a"
_MOCK_JOB_ID = "1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b"
_V2_REGEN_JOB_ID = "2c2c2c2c-2c2c-2c2c-2c2c-2c2c2c2c2c2c"  # Phase 1Q regen from Prompt v2
_V3_REGEN_JOB_ID = "3c3c3c3c-3c3c-3c3c-3c3c-3c3c3c3c3c3c"  # Phase 1O regen from Prompt v3
_V4_REGEN_JOB_ID = "4d4d4d4d-4d4d-4d4d-4d4d-4d4d4d4d4d4d"  # Phase 1O regen from Prompt v4 (FAILED at Enhancor billing gate)
_V4_REGEN_RETRY_JOB_ID = "4e4e4e4e-4e4e-4e4e-4e4e-4e4e4e4e4e4e"  # Phase 1O v4 retry after billing settle

_PLACEHOLDER_PRODUCT_URL = (
    "https://example.com/pai-skincare/PLACEHOLDER-product.jpg"
)
_PLACEHOLDER_INFLUENCER_URL = (
    "https://example.com/pai-skincare/PLACEHOLDER-influencer.jpg"
)


DEMO_GENERATION_JOBS: dict[str, DemoGenerationJob] = {
    _HISTORICAL_JOB_ID: DemoGenerationJob(
        id=_HISTORICAL_JOB_ID,
        batch_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        content_item_id="88888888-8888-8888-8888-888888888888",
        prompt_version_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        provider="enhancor_seedance",
        provider_mode="ugc",
        quality_tier="premium_1080p",
        resolution="1080p",
        duration_seconds=15,
        status="completed",
        estimated_credits=5940,
        prompt_label="1080p hero (historical)",
        prompt_hook=(
            "I like that this feels simple — one serum, a few ingredients "
            "I can actually understand."
        ),
        prompt_body=(
            "UGC product-talk, 15s. Single take. Bathroom or soft-window "
            "living room. One creator, 28–40, sensitive-skin profile. "
            "Serum bottle visible but not the hero of the frame. Calm "
            "British VO. Slow ambient music bed."
        ),
        prompt_negative=(
            "No exaggerated claims. No clinical-white studio. No on-screen "
            "logo overlays. No competing skincare visible in the frame. "
            "No glossy commercial lighting."
        ),
        prompt_scene_plan=(
            "0–2s hook on camera. 2–8s glance to product + brief read. "
            "8–14s reflection on routine. 14–15s soft close."
        ),
        prompt_creator_direction=(
            "Real-friend register, not influencer-energy. Slow blinks, "
            "occasional looks away. Body language: comfortable, not posed."
        ),
        prompt_product_constraints=(
            "Pai Skincare BioRegenerate Rosehip Oil. Label must be legible "
            "when in frame; do not invent ingredient claims. Brand name "
            'spelled "Pai" (not "Pái" / "Pie").'
        ),
        content_title="What I keep coming back to — 15s UGC product talk",
        content_caption_draft=(
            "One serum. Ingredients you can read. A routine that doesn't "
            "feel like too much. — for sensitive, reactive skin. "
            "#skincare #sensitiveskin"
        ),
        placeholder_product_urls=(_PLACEHOLDER_PRODUCT_URL,),
        placeholder_influencer_urls=(_PLACEHOLDER_INFLUENCER_URL,),
    ),
    _MOCK_JOB_ID: DemoGenerationJob(
        id=_MOCK_JOB_ID,
        batch_id="1a1a1a1a-1a1a-1a1a-1a1a-1a1a1a1a1a1a",
        content_item_id="99999999-9999-9999-9999-999999999999",
        prompt_version_id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        provider="enhancor_seedance",
        provider_mode="ugc",
        quality_tier="standard_720p",
        resolution="720p",
        duration_seconds=15,
        status="draft",
        estimated_credits=2646,
        prompt_label="720p stricter label-text guard",
        prompt_hook="Three ingredients. That's it.",
        prompt_body=(
            "Macro-led UGC variant, 15s, 720p. One creator's hands only "
            "— no face this take. Soft daylight. Two cuts maximum. Calm "
            "British VO. The story is the label."
        ),
        prompt_negative=(
            "No animated text, no graphics overlays, no warped or melted "
            "label text, no AI-typical extra fingers, no fake ingredient "
            "names, no jewellery, no nail polish, no competing brands."
        ),
        prompt_scene_plan=(
            "0–3s ingredient-led hook over a hand reaching for the bottle. "
            "3–9s slow rotation of the bottle so the label reads. 9–13s "
            "dropper close-up. 13–15s soft close, hand setting bottle down."
        ),
        prompt_creator_direction=(
            "Hands-only this take. Calm, deliberate motion. No fidgeting. "
            "Skin tone should match a sensitive-skin profile (no heavy "
            "makeup on hands)."
        ),
        prompt_product_constraints=(
            "Pai Skincare BioRegenerate Rosehip Oil. Label text MUST be "
            "legible and spelled exactly as on the real packaging. If the "
            "label cannot be rendered legibly, fall back to an out-of-focus "
            "pass rather than inventing text. No competing skincare visible."
        ),
        content_title="Next week — ingredient close-up variant",
        content_caption_draft=(
            "Rosehip BioRegenerate. The whole story in one bottle. — made "
            "for sensitive skin."
        ),
        placeholder_product_urls=(_PLACEHOLDER_PRODUCT_URL,),
        placeholder_influencer_urls=(_PLACEHOLDER_INFLUENCER_URL,),
    ),
    # ----------------------------------------------------------------- #
    # Phase 1Q — operator-approved regen from Prompt v2 (the label-text
    # blur fork of the Pai 720p take). Lives in Supabase as:
    #   generation_jobs.id          = 2c2c…
    #   generation_batches.id       = 2b2b…
    #   prompt_versions.id          = f0f0… (status=approved_for_generation)
    #   content_items.id            = 88888888… (the shared Pai item)
    #   regeneration_requests.id    = 26f5c49e… (accepted, points at v2)
    # No paid call has been made yet; status='draft' here mirrors the
    # generation_jobs row. The runner's --submit path remains gated
    # behind --confirm and explicit operator approval.
    # ----------------------------------------------------------------- #
    _V2_REGEN_JOB_ID: DemoGenerationJob(
        id=_V2_REGEN_JOB_ID,
        batch_id="2b2b2b2b-2b2b-2b2b-2b2b-2b2b2b2b2b2b",
        content_item_id="88888888-8888-8888-8888-888888888888",
        prompt_version_id="f0f0f0f0-f0f0-f0f0-f0f0-f0f0f0f0f0f0",
        provider="enhancor_seedance",
        provider_mode="ugc",
        quality_tier="standard_720p",
        resolution="720p",
        duration_seconds=15,
        status="draft",
        estimated_credits=2646,
        prompt_label="720p label-text blur (Phase 1P)",
        prompt_hook=(
            "I like that this feels simple — one serum, a few ingredients "
            "I can actually understand."
        ),
        prompt_body=(
            "UGC product-talk, 15s, 720p, 9:16 vertical. Single take or "
            "two cuts maximum. Soft daylight in a warm domestic interior "
            "(kitchen counter or bathroom shelf, no clinical-white studio). "
            "Creator early-30s, sensitive-skin profile, no heavy makeup. "
            "Calm real-friend register. Slow ambient bed mixed UNDER the "
            "dialogue. The Pai serum bottle is held but NEVER framed "
            "sharp on the small label — keep it slightly out of focus, "
            "at a tilt, or with natural motion-blur."
        ),
        # Tightened in Phase 1Q to fit the 500-char Seedance payload
        # cap. Catch-all leads so any future tightening still preserves
        # it. Matches the Supabase row exactly.
        prompt_negative=(
            "Sharp small text on the bottle label is forbidden — only "
            "the brand mark \"Pai\" may be readable. No readable "
            "\"TriPepttide\", \"TriPeptide\", \"AGE CONFIDENCE\", "
            "\"Renewal Serum\", \"Sérum Régénérant\", \"NAD+\", "
            "\"CLINICALLY PROVED\", \"FOR SENSITIVE SKIN\", ingredient "
            "lists, percentages, batch codes, barcodes. No warped or "
            "melted label text. No animated text, graphics overlays, "
            "subtitles, clinical-white studio, AI-typical extra fingers, "
            "jewellery, nail polish, competing skincare."
        ),
        prompt_scene_plan=(
            "0–3s creator open at chest height; bottle visible in soft "
            "focus only. 3–7s brief glance to product, slight tilt of the "
            "bottle so the small label sits OUT of the sharp focal plane. "
            "7–12s creator reflection beat, eyes back to camera. 12–15s "
            "soft close, hand setting the bottle down (still soft-focus "
            "on the label)."
        ),
        prompt_creator_direction=(
            "Real-friend register, not influencer-energy. Slow blinks, "
            "occasional looks away. Hands relaxed; if the bottle is held, "
            "hold it loosely so it tilts or drifts slightly — this is "
            "what blurs the small label naturally and reads as authentic "
            "UGC."
        ),
        prompt_product_constraints=(
            "Pai Skincare BioRegenerate Rosehip Oil — frosted/translucent "
            "white bottle, white pump cap, gentle green-curve \"Pai\" "
            "brand mark. READABLE-LABEL POLICY (inverted from v1 after "
            "the Phase 1H \"TriPepttide\" hallucination): only the brand "
            "mark \"Pai\" may be sharp and readable. All other label "
            "text MUST be softly blurred, motion-blurred, out of focus, "
            "or at a sharp angle that keeps small text unreadable. DO NOT "
            "invent any text on the packaging that isn't the brand mark "
            "— no ingredient names, no clinical claims, no measurement "
            "claims, no sub-headings. If a beat would force the small "
            "label into the sharp focal plane, instead shoot at a slight "
            "angle, add motion, or push the label out of focus. "
            "Brand-name spelling guard: when the word \"Pai\" is visible "
            "it MUST read exactly \"Pai\" (not \"Pái\", not \"Pie\", not "
            "\"PAI\" all-caps, not \"Pal\"). No competing skincare "
            "visible. No on-screen graphics or text overlays."
        ),
        content_title="What I keep coming back to — 15s UGC product talk",
        content_caption_draft=(
            "One serum. Ingredients you can read. A routine that doesn't "
            "feel like too much. — for sensitive, reactive skin. "
            "#skincare #sensitiveskin"
        ),
        placeholder_product_urls=(_PLACEHOLDER_PRODUCT_URL,),
        placeholder_influencer_urls=(_PLACEHOLDER_INFLUENCER_URL,),
    ),
    # ----------------------------------------------------------------- #
    # Phase 1O — operator-approved regen from Prompt v3 (the "product as
    # prop, label never readable" fork of v2). Lives in Supabase as:
    #   generation_jobs.id       = 3c3c…
    #   generation_batches.id    = 3b3b…
    #   prompt_versions.id       = b39e254b-1328-49ea-bf98-7366952a0b74
    #                              (status=approved_for_generation; v2
    #                              flipped to superseded in the same
    #                              transaction)
    #   content_items.id         = 88888888… (the shared Pai item)
    # Negative prompt was tightened to ≤500 chars in tmp/v3_patch.py to
    # match the wire cap enforced by payload_builder.NEGATIVE_PROMPT_MAX_CHARS.
    # No paid call has been made yet; status='draft' here mirrors the
    # generation_jobs row. The runner's --submit path is gated by
    # --confirm and explicit operator approval. Audio Fixer is NOT
    # planned for this regen — the defect is visual label
    # hallucination, not audio.
    # ----------------------------------------------------------------- #
    _V3_REGEN_JOB_ID: DemoGenerationJob(
        id=_V3_REGEN_JOB_ID,
        batch_id="3b3b3b3b-3b3b-3b3b-3b3b-3b3b3b3b3b3b",
        content_item_id="88888888-8888-8888-8888-888888888888",
        prompt_version_id="b39e254b-1328-49ea-bf98-7366952a0b74",
        provider="enhancor_seedance",
        provider_mode="ugc",
        quality_tier="standard_720p",
        resolution="720p",
        duration_seconds=15,
        status="draft",
        estimated_credits=2646,
        prompt_label="720p prop-only / no readable label (Phase 1O fork v3)",
        prompt_hook=(
            "I like that this feels simple - one serum, a few "
            "ingredients I can actually understand."
        ),
        prompt_body=(
            "UGC product-talk, 15s, 720p, 9:16 vertical. Single take. "
            "Soft daylight in a warm domestic interior (kitchen counter, "
            "bathroom shelf, living-room side-table - never a "
            "clinical-white studio or a product-shoot lightbox). Creator "
            "early-30s, sensitive-skin profile, no heavy makeup, "
            "real-friend register. PRODUCT-AS-PROP RULE: the Pai bottle "
            "is recognisable as a premium skincare bottle (frosted/"
            "translucent white glass, white pump cap, slim form factor), "
            "but the label is NEVER readable in any frame. The bottle "
            "is held low, partly turned away from the camera, or placed "
            "just out of the sharp focal plane. The bottle is NOT framed "
            "as a hero product shot. No label close-ups. No glossy "
            "pack-shot lighting. The feel is real UGC, not a commercial."
        ),
        # Mirrors the Supabase row (tightened to fit the 500-char wire
        # cap). Brand-spelling guard + full v1/v2 hallucination denylist
        # + composition denylist all preserved.
        prompt_negative=(
            "All small label text MUST be unreadable. Brand mark \"Pai\" "
            "must be exact title-case - never \"pai\", \"PAI\", "
            "\"Pal\", \"Pie\", or diacritic variants. Forbidden visible "
            "text: \"TriPepttide\", \"TriPeptide\", \"AGE CONFIDENCE\", "
            "\"Renewal Serum\", \"Serum Regenerant\", \"NAD+\", "
            "\"CLINICALLY PROVED\", any ingredient names, percentages, "
            "batch codes, French sub-lines. No label close-ups, no "
            "\"almost readable\" copy. No warped/melted text. No "
            "animated text, graphics overlays, subtitles, clinical-white "
            "studio."
        ),
        prompt_scene_plan=(
            "0-6s: creator talks to camera at chest height. Product "
            "visible in soft secondary presence only (on the counter "
            "behind, or held low in one hand, label turned partly "
            "away). The product is acknowledged but never the subject "
            "of the frame. "
            "6-11s: application / texture moment - close on the back of "
            "the hand or wrist as a small amount of oil is worked in. "
            "The bottle is OFF-screen or soft-focus blur in the "
            "background. NO packaging label visible in this section at "
            "all. "
            "11-15s: lifestyle close-out. The bottle sits on the "
            "counter, angled away or softly out of focus. Creator's "
            "eyes back to camera for the final beat. The clip ends as "
            "casual UGC, not a product-packshot ad."
        ),
        prompt_creator_direction=(
            "Real-friend register, not influencer-energy. Slow blinks, "
            "occasional looks away from camera. Hands relaxed. When the "
            "bottle is held, hold it loosely and low - shoulder/chest "
            "height or below - and let it tilt naturally so the front "
            "label is angled partly away. Treat the bottle like a thing "
            "on your desk, not like a product being demonstrated. Do "
            "not rotate it toward the lens. Do not 'present' it. If the "
            "script wants the bottle on-screen, the framing should "
            "always defocus or angle the label."
        ),
        prompt_product_constraints=(
            "Pai Skincare bottle - frosted/translucent white glass, "
            "white pump cap, slim cylindrical form. "
            "RECOGNISABLE-AS-PAI RULE (replaces v2's readable-Pai-mark "
            "rule): the silhouette and bottle finish should read as Pai "
            "to anyone who knows the brand, but no label text is "
            "required to be legible - not even the 'Pai' brand mark. If "
            "the brand mark happens to fall in the sharp focal plane, "
            "it must read exactly 'Pai' (title-case, no diacritics). If "
            "that level of precision is not achievable, defocus or "
            "angle the bottle so the brand mark is not sharp. The "
            "viewer's takeaway is 'a premium skincare bottle the "
            "creator uses', NOT 'a specific label with a specific "
            "ingredient claim'. DO NOT invent any text on the "
            "packaging. DO NOT show ingredient names, claims, "
            "percentages, certifications, or sub-lines. No competing "
            "skincare visible. No on-screen graphics or text overlays. "
            "The bottle is a prop, not a hero product."
        ),
        content_title="What I keep coming back to — 15s UGC product talk",
        content_caption_draft=(
            "One serum. Ingredients you can read. A routine that doesn't "
            "feel like too much. — for sensitive, reactive skin. "
            "#skincare #sensitiveskin"
        ),
        placeholder_product_urls=(_PLACEHOLDER_PRODUCT_URL,),
        placeholder_influencer_urls=(_PLACEHOLDER_INFLUENCER_URL,),
    ),
    # ----------------------------------------------------------------- #
    # Phase 1O — v4 catalogue entry for DRY-RUN validation ONLY.
    #
    # No Supabase generation_jobs row exists for this id (v4 prompt is
    # still operator_editing and the user explicitly forbade a paid
    # submit). The runner only needs the catalogue to build the wire
    # payload during --dry-run; it never touches Supabase in that path.
    #
    # When the operator eventually approves v4 + a blank-label reference
    # is hosted at a public HTTPS URL, the matching Supabase row gets
    # inserted (mirror of the 3c3c flow), v4 prompt flips to
    # approved_for_generation, then --submit becomes meaningful.
    # ----------------------------------------------------------------- #
    _V4_REGEN_JOB_ID: DemoGenerationJob(
        id=_V4_REGEN_JOB_ID,
        batch_id="4b4b4b4b-4b4b-4b4b-4b4b-4b4b4b4b4b4b",
        content_item_id="88888888-8888-8888-8888-888888888888",
        prompt_version_id="27465fbd-b699-44f9-9f79-6a738bfba8f8",
        provider="enhancor_seedance",
        provider_mode="ugc",
        quality_tier="standard_720p",
        resolution="720p",
        duration_seconds=15,
        status="draft",
        estimated_credits=2646,
        prompt_label="720p creator-led / unlabeled bottle (Phase 1O fork v4)",
        prompt_hook=(
            "I like that this feels simple - one serum, a few "
            "ingredients I can actually understand."
        ),
        prompt_body=(
            "UGC product-talk, 15s, 720p, 9:16 vertical, native audio. "
            "Single take in a warm domestic interior (kitchen counter, "
            "bathroom shelf, soft daylight - never a clinical-white "
            "studio). Creator early-30s, sensitive-skin profile, no "
            "heavy makeup, real-friend register. THE CREATOR CARRIES "
            "THE AD, NOT THE PACKAGING. Long beats on the creator's "
            "face and voice; the bottle is incidental. REFERENCE-IMAGE "
            "NOTE: the supplied product image is a label-stripped or "
            "silhouette frosted skincare bottle - keep the bottle "
            "looking like THAT reference, with NO printed copy at all. "
            "Do not add packaging text. Do not invent ingredient names, "
            "claims, sub-lines, or a brand mark. If the bottle is "
            "on-screen, frame it low, partly turned away, or softly "
            "out of focus. Hero shots are forbidden."
        ),
        prompt_negative=(
            "ZERO readable label text. The bottle MUST be label-free. "
            "Brand mark \"Pai\" if it appears must be exact title-case "
            "- never \"pai\", \"PAI\", \"Pal\", \"Pie\", diacritics. "
            "Forbidden visible text: \"TriPepttide\", \"TriPeptide\", "
            "\"AGE CONFIDENCE\", \"Renewal Serum\", \"Serum "
            "Regenerant\", \"NAD+\", \"CLINICALLY PROVED\", any "
            "ingredient names, percentages, batch codes, French "
            "sub-lines. No label close-ups, no \"almost readable\" "
            "copy. No pack-shot lighting, no clinical-white studio."
        ),
        prompt_scene_plan=(
            "0-5s: hook on the creator's face, eye-level, calm. Bottle "
            "is NOT in frame yet. "
            "5-9s: brief, low-key prop beat - the bottle is in the "
            "creator's hand at chest-level or sitting on the counter, "
            "partly turned away, soft-focus. The bottle is referenced "
            "but never the subject of the frame. "
            "9-13s: application/texture moment on the back of the hand "
            "or wrist; bottle is OFF-screen entirely. "
            "13-15s: return to the creator's face for the close, brief "
            "smile or look-away. Bottle may sit in soft background blur."
        ),
        prompt_creator_direction=(
            "Real-friend register, not influencer-energy. The creator "
            "carries every beat with face and voice. Slow blinks, "
            "occasional looks away. When the bottle is briefly held, "
            "hold it like a casual object - do not present it to the "
            "camera, do not rotate it. Most of the screen-time is on "
            "the creator, not on the product."
        ),
        prompt_product_constraints=(
            "Product reference for v4 is a LABEL-FREE or silhouette "
            "frosted-white skincare bottle (operator will swap the "
            "reference asset before the paid run - this draft assumes "
            "that swap has happened). The bottle is recognisable as a "
            "premium skincare prop by its frosted-glass finish and "
            "slim form, but it does NOT carry the real Pai packshot's "
            "label copy. NO label text is required to be legible. If "
            "text happens to appear on the bottle, it MUST be "
            "unreadable - no Pai brand mark required either. The "
            "viewer's takeaway is \"a calm creator who uses a simple, "
            "premium-looking skincare bottle\". Specific brand "
            "recognition is deferred to the caption/voiceover, not "
            "the visual. No competing skincare visible. No on-screen "
            "text overlays or graphics."
        ),
        content_title="What I keep coming back to — 15s UGC product talk",
        content_caption_draft=(
            "One serum. Ingredients you can read. A routine that "
            "doesn't feel like too much. — for sensitive, reactive "
            "skin. #skincare #sensitiveskin"
        ),
        placeholder_product_urls=(_PLACEHOLDER_PRODUCT_URL,),
        placeholder_influencer_urls=(_PLACEHOLDER_INFLUENCER_URL,),
    ),
}

# Phase 1O — v4 retry job (4e4e). Same prompt + same intent as 4d4d but
# a fresh generation_jobs row created after the Enhancor billing-balance
# was settled. Sharing the v4 catalogue body via dataclasses.replace()
# keeps the prompt fields in lockstep — only the job id changes.
DEMO_GENERATION_JOBS[_V4_REGEN_RETRY_JOB_ID] = replace(
    DEMO_GENERATION_JOBS[_V4_REGEN_JOB_ID],
    id=_V4_REGEN_RETRY_JOB_ID,
)


def find_demo_job(job_id: str) -> Optional[DemoGenerationJob]:
    """Return the demo job with `id == job_id`, or None.

    Phase 1H replaces this with a Supabase-backed lookup behind the same
    return shape.
    """
    return DEMO_GENERATION_JOBS.get(job_id)


def is_placeholder_url(url: str) -> bool:
    """True when `url` is one of the catalogue's placeholder defaults.

    The runner refuses to `--submit` when any product / influencer URL on
    the built payload is still a placeholder; this is the check.
    """
    return "/PLACEHOLDER-" in url
