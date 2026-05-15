"""Capture competitor ad proof for a prospect's strategy page.

Usage:
  py -3.11 scripts/capture_competitor_ads.py <prospect_id> \
      [--country GB] [--max-ads-per-competitor 8] [--max-competitors 8]

For each competitor in the prospect's strategy `competitors` set, this
script:

  1. Scrapes the Meta Ads Library via Apify (`active_only=True`,
     capped at `--max-ads-per-competitor` ads per page).
  2. Picks the top N ads by `days_active` (longevity signal).
  3. Captures local screenshots via `agents.outreach.ad_screenshots`,
     routing them into
     `prospects/<id>/assets/competitors/<slug>/ad_*.png`.
  4. Writes a structured `prospects/<id>/strategy/competitor_ads.json`
     for the strategy builder to consume.

Default `--max-ads-per-competitor` is 8 (was 5 / 2). Proof-density on
the strategy page reads thin when each competitor has only two ads;
eight gives the renderer enough material to show 3-5 thumbnails per
card and keep an overflow "+N more sampled ads" pool. Absolute cap is
10 - past that the capture pass starts running into Apify-side rate
limits without adding visible strategy value.

This script never deploys, never commits, never touches the live
Cloudflare microsite. It is safe to re-run; existing competitor
screenshot files are overwritten in place.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.outreach.ad_screenshots import capture_ad_screenshots  # noqa: E402
from agents.outreach.reporting.strategy_brief import (  # noqa: E402
    COMPETITOR_ADS_FILENAME,
    StrategyBrief,
    _competitor_slug,
)
from agents.strategist.analysis.longevity_scorer import score_ads  # noqa: E402
from agents.strategist.tools.apify_fb_ads import make_fb_ads_search_tool  # noqa: E402

log = logging.getLogger("capture_competitor_ads")


# Pattern-tag rules: each tag is matched against the ad's body text /
# title / CTA / media type. The matcher emits BOTH the tag and a short
# `why_this_pattern` reason that the strategy renderer surfaces under
# the pattern's ad-evidence card. Multiple tags can fire on the same
# ad ONLY when each is independently supported by evidence in the ad
# (we require a separate regex hit per tag - we never tag generically).
#
# Tag taxonomy matches the strategy_brief CreativePattern.tag set:
#   review_social_proof, ingredient_proof, sensitive_skin_reassurance,
#   founder_expert_credibility, texture_application_demo,
#   routine_simplification, offer_bundle, editorial_luxury,
#   before_after_claim, discount_led
_PATTERN_RULES: list[tuple[str, "re.Pattern[str]", str]] = [
    (
        "review_social_proof",
        re.compile(
            r"\b(\d{1,5}\+?\s*(?:five|5)[\s\-]?star)\b"
            r"|\b(review|reviews|rated|testimonial|customers say)\b"
            r"|\bI'?ve been using\b|\bI tried\b|\bfavou?rite\b"
            # First-person lived experience: ad opens with the speaker
            # describing their own skin / routine / story. Caught by
            # an "I [verb]" + "my skin" / "my routine" / "my hair"
            # combination so we don't fire on every "I" in copy.
            r"|\bmy skin\b|\bmy routine\b|\bmy hair\b"
            r"|\blet'?s talk\b|\bhonest(?:ly)? review\b",
            re.IGNORECASE,
        ),
        "Ad copy quotes a review or lived-experience customer voice.",
    ),
    (
        "ingredient_proof",
        re.compile(
            r"\b("
            r"vitamin\s*[a-e]|niacinamide|hyaluronic|rosehip|retinol|peptides?|"
            r"squalane|ceramides?|salicylic|glycolic|bakuchiol|probiotics?|"
            # Plural forms matter: Apify bodies routinely say "botanicals"
            # / "ingredients" / "extracts" / "actives" - the old regex
            # required word-boundary on the singular and quietly dropped
            # those tags.
            r"ingredients?|actives?|formula(?:ted|tion)?|extracts?|botanicals?|"
            r"organic|biome|microbiome"
            r")\b",
            re.IGNORECASE,
        ),
        "Ad copy leads with a named ingredient / formulation claim.",
    ),
    (
        "sensitive_skin_reassurance",
        re.compile(
            r"\b(sensitive|reactive|redness|gentle|soothe|soothing|barrier|"
            r"fragrance.?free|hypoallergenic|allergy)\b",
            re.IGNORECASE,
        ),
        "Ad copy reassures sensitive / reactive skin buyers.",
    ),
    (
        "founder_expert_credibility",
        re.compile(
            r"\b(founder|founded|why we|behind the|our story|we built|"
            r"derm(?:atologist)?|expert|cosmetologist|chemist|formulator)\b",
            re.IGNORECASE,
        ),
        "Ad copy leans on founder / expert credibility.",
    ),
    (
        "texture_application_demo",
        re.compile(
            r"\b(texture|absorb|absorbs|melts|silky|glide|cream|serum|"
            r"oil|balm)\b",
            re.IGNORECASE,
        ),
        "Ad text references texture / application moment.",
    ),
    (
        "routine_simplification",
        re.compile(
            r"\b(routine|three.?step|simple routine|minimalist|everyday|"
            r"morning|nightly|easy)\b",
            re.IGNORECASE,
        ),
        "Ad copy frames the product around a routine moment.",
    ),
    (
        "offer_bundle",
        re.compile(
            r"\b(bundle|set|kit|starter|trio|duo|gift\s*(?:set|with))\b",
            re.IGNORECASE,
        ),
        "Ad copy promotes a bundle / starter set.",
    ),
    (
        "editorial_luxury",
        re.compile(
            r"\b(luxur(?:y|ious)|editorial|heritage|estate|exclusive|"
            r"craftsmanship|hand[\s\-]?blended|hand[\s\-]?crafted)\b",
            re.IGNORECASE,
        ),
        "Ad copy leans on editorial / heritage / luxury cues.",
    ),
    (
        "before_after_claim",
        re.compile(
            r"\b(before\s*(?:and|&)\s*after|transformation|results in|"
            r"in\s+\d+\s+days|reducing wrinkles|combating|lifting)\b",
            re.IGNORECASE,
        ),
        "Ad copy makes a before/after or transformation claim.",
    ),
    (
        "discount_led",
        re.compile(
            r"\b(\d{1,2}%\s*off|save\s*\d+%|discount|deal|limited\s+time|"
            r"flash\s+sale|sale\s+now)\b",
            re.IGNORECASE,
        ),
        "Ad copy leads with a discount / urgency cue.",
    ),
]


def _classify_ad(ad: dict) -> tuple[list[str], dict[str, str]]:
    """Return (pattern_tags, why_by_tag).

    Only fires a tag when the body / title / CTA text carries a
    matching regex - we never tag on a media-type guess alone, and
    we never tag DCO placeholder body text (the `{{product.brand}}`
    templates) because there's no real copy to back any pattern.

    `why_by_tag[tag]` is a short string describing *why* that ad
    backs that pattern; the strategy renderer surfaces it under
    each evidence tile so the client can audit the call.
    """
    body = (ad.get("body_text") or "")
    if "{{" in body and "}}" in body:
        # DCO placeholder - no real copy to classify.
        return [], {}
    title = (ad.get("title") or "")
    cta = (ad.get("cta_text") or "")
    blob = " ".join((body, title, cta))
    if not blob.strip():
        return [], {}
    tags: list[str] = []
    why_by_tag: dict[str, str] = {}
    for tag, rx, reason in _PATTERN_RULES:
        if rx.search(blob):
            tags.append(tag)
            why_by_tag[tag] = reason
    return tags, why_by_tag


_BRAND_STOP_WORDS = {
    "the", "a", "an", "by", "of", "and", "for", "co", "ltd", "inc", "uk",
    "usa", "us", "gb", "skincare", "skin", "beauty", "london", "organic",
    "clean", "natural",
}


def _significant_brand_tokens(brand_name: str) -> list[str]:
    """Return the most-distinctive single-word tokens from `brand_name`.

    Drops generic category words ("Skincare", "Beauty", "London",
    "Organic", ...) so that a competitor like "Evolve Organic Beauty"
    matches the live Meta page "Evolve Beauty" on the distinctive
    "evolve" token, while "REN Clean Skincare" only matches if the
    page actually carries "REN".

    Tokens are lowercased; entries shorter than 3 characters are
    dropped UNLESS the brand name is entirely short (e.g. "REN") in
    which case the bare brand stays.
    """
    raw = re.findall(r"[A-Za-z0-9]+", brand_name)
    if not raw:
        return []
    out: list[str] = []
    for tok in raw:
        low = tok.lower()
        if low in _BRAND_STOP_WORDS:
            continue
        if len(low) < 3 and len(raw) > 1:
            continue
        out.append(low)
    return out or [raw[0].lower()]


def _meta_ads_search_url(competitor_page: str, country: str) -> str:
    from urllib.parse import quote_plus
    return (
        "https://www.facebook.com/ads/library/"
        f"?active_status=active&ad_type=all&country={country}"
        f"&q={quote_plus(competitor_page)}&search_type=keyword_unordered"
    )


def _capture_one_competitor(
    *,
    prospect_id: str,
    competitor_name: str,
    country: str,
    max_ads: int,
    fb_tool,
    prospects_root: Path,
) -> dict:
    """Returns a dict shaped like:
        {
            "meta_ads_url": "...",
            "sampled_ads": [...],
            "capture_status": "captured"
                              | "no_active_ads_found"
                              | "false_positive_filtered"
                              | "playwright_failed"
                              | "blocked"
                              | "scrape_failed",
            "errors": [...],
        }
    `screenshot_path` values are POSIX-relative to the prospect root.

    `capture_status` is a single high-level outcome the strategy
    renderer chips on the competitor card so the operator can see at
    a glance why a competitor has zero proof:
      - "captured"              -> >=1 ad with screenshot stored.
      - "no_active_ads_found"   -> Apify returned ads but none matched
                                   the brand's page (false positives
                                   from the keyword search).
      - "false_positive_filtered" -> all returned ads were filtered as
                                   page-name mismatches (kept separate
                                   from genuine empty results so the
                                   operator knows the keyword search
                                   noise was the cause).
      - "playwright_failed"     -> ads found, screenshots all failed.
      - "blocked"               -> Apify itself errored / Meta blocked.
      - "scrape_failed"         -> network / unhandled exception.
    """
    slug = _competitor_slug(competitor_name)
    meta_ads_url = _meta_ads_search_url(competitor_page=competitor_name, country=country)

    out: dict = {
        "meta_ads_url": meta_ads_url,
        "sampled_ads": [],
        "capture_status": "no_active_ads_found",
        "errors": [],
    }

    try:
        raw = fb_tool.invoke({
            "competitor_pages": [competitor_name],
            "country": country,
            "max_ads_per_page": 25,
            "active_only": True,
        })
    except Exception as e:
        out["errors"].append(f"apify_scrape_failed: {type(e).__name__}: {e}")
        out["capture_status"] = "scrape_failed"
        return out

    if isinstance(raw, str):
        # The strategist tool returns an error string on Apify failure.
        out["errors"].append(f"apify_scrape_error: {raw[:200]}")
        out["capture_status"] = "blocked"
        return out

    if not raw:
        out["errors"].append("apify_no_ads_returned")
        out["capture_status"] = "no_active_ads_found"
        return out

    # The Apify actor does a KEYWORD search on the Ads Library URL,
    # not a page-id search - so a query for 'By Sarah London' can
    # match an unrelated brand whose copy contains those words.
    # Filter strictly to ads whose `page_name` is the brand we asked
    # for. We accept any of:
    #   - case-insensitive full / substring match on the full name
    #   - case-insensitive match on the FIRST significant token of
    #     the name (so "Evolve Organic Beauty" matches the live page
    #     "Evolve Beauty", but generic stop words like "by" / "the"
    #     don't open the gate)
    # Discarding noise here is much safer than rendering a competitor
    # card pointing at someone else's ad.
    name_lc = competitor_name.lower().strip()
    significant = set(_significant_brand_tokens(competitor_name))
    filtered = []
    discarded_for_page_mismatch = 0
    for ad in raw:
        pn = (ad.get("page_name") or "").lower().strip()
        if not pn:
            discarded_for_page_mismatch += 1
            continue
        if pn == name_lc:
            filtered.append(ad)
            continue
        # Tokenise the page_name and reject any "extra" non-generic
        # word that isn't in the search brand (e.g. 'chapman' in
        # 'sarah chapman london' when we asked for 'by sarah london'
        # is a different brand even though the 'sarah' overlap looked
        # promising).
        pn_significant = set(_significant_brand_tokens(pn))
        if not pn_significant:
            # Page name was only stop-words; can't verify, drop.
            discarded_for_page_mismatch += 1
            continue
        # All significant tokens in the page_name must appear in the
        # competitor name (no foreign tokens).
        foreign = pn_significant - significant
        if foreign:
            discarded_for_page_mismatch += 1
            continue
        # And at least one significant token from the search must
        # appear in the page_name (avoid empty-overlap acceptance).
        if not (significant & pn_significant):
            discarded_for_page_mismatch += 1
            continue
        filtered.append(ad)
    if discarded_for_page_mismatch:
        out["errors"].append(
            f"discarded_for_page_mismatch: {discarded_for_page_mismatch} "
            f"ad(s) whose page_name did not match {competitor_name!r}"
        )
    if not filtered:
        out["errors"].append("no_ads_after_page_name_filter")
        # We DID get raw ads back, they just all turned out to be
        # someone else - that's a "false_positive_filtered" outcome,
        # not "no active ads at all".
        out["capture_status"] = (
            "false_positive_filtered" if discarded_for_page_mismatch
            else "no_active_ads_found"
        )
        return out

    scored = score_ads(filtered, min_days=0)[:max_ads]
    if not scored:
        out["errors"].append("apify_no_ads_after_scoring")
        out["capture_status"] = "no_active_ads_found"
        return out

    # Run captures into prospects/<id>/assets/competitors/<slug>/
    cap = capture_ad_screenshots(
        prospect_id,
        scored,
        prospects_root=prospects_root,
        max_screenshots=max_ads,
        assets_subdir=f"competitors/{slug}",
        filename_prefix="ad",
        timeout=30.0,
    )
    captured_ads = cap.get("ads", []) or []
    for err in cap.get("errors") or []:
        out["errors"].append(err)

    n_with_shot = 0
    for ad in captured_ads:
        tags, why_by_tag = _classify_ad(ad)
        sampled_entry = {
            "ad_archive_id": str(ad.get("ad_archive_id") or "").strip(),
            "ad_library_url": (ad.get("ad_library_url") or "").strip() or None,
            "screenshot_path": ad.get("ad_screenshot_path"),
            "body_excerpt": _trim_body(ad.get("body_text")),
            "cta_text": (ad.get("cta_text") or "").strip() or None,
            "days_active": ad.get("days_active") if isinstance(ad.get("days_active"), (int, float)) else None,
            "media_type": (ad.get("media_type") or "").strip().upper() or None,
            "pattern_tags": tags,
            "why_by_tag": why_by_tag,
            "evidence_level": (
                "competitor_ad_evidence"
                if (ad.get("ad_screenshot_path") or ad.get("ad_library_url"))
                else "needs_validation"
            ),
            "capture_status": ad.get("capture_status"),
            "capture_error": ad.get("capture_error"),
        }
        out["sampled_ads"].append(sampled_entry)
        if ad.get("ad_screenshot_path"):
            n_with_shot += 1

    if n_with_shot > 0:
        out["capture_status"] = "captured"
    elif scored:
        # We found ads, screenshots all failed (Playwright timeout, no
        # direct image URL, etc.). The ads themselves are still useful
        # proof (the ad_library_url is real) but no thumbnail strip.
        out["capture_status"] = "playwright_failed"
    return out


def _trim_body(text: Optional[str], *, max_chars: int = 220) -> Optional[str]:
    if not text:
        return None
    s = re.sub(r"\s+", " ", str(text)).strip()
    # Strip raw DCO templating that frequently lands as the body text.
    if "{{" in s and "}}" in s:
        return None
    return s[:max_chars]


def _reclassify_existing(prospect_root: Path) -> dict:
    """Re-run the pattern classifier against the already-captured
    `competitor_ads.json`. Only ads with a non-empty `body_excerpt` AND
    an empty current `pattern_tags` list are re-evaluated - we never
    overwrite an existing classifier decision, and we never invent tags
    for DCO ads without body copy. Returns a small report dict.

    This is the safe "reclassify, don't rescrape" path the strategy
    workflow asks for: if the regex misses a plural form (e.g.
    `botanicals` under the old `\\bbotanical\\b` rule), the next
    reclassify pass picks it up without a fresh Apify call.
    """
    path = prospect_root / "strategy" / COMPETITOR_ADS_FILENAME
    if not path.is_file():
        return {"path": str(path), "status": "missing", "ads_retagged": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"path": str(path), "status": f"parse_error: {e}", "ads_retagged": 0}
    competitors = payload.get("competitors") or {}
    n_retagged = 0
    retagged_by_competitor: dict[str, list[str]] = {}
    for name, blob in competitors.items():
        if not isinstance(blob, dict):
            continue
        for ad in blob.get("sampled_ads") or []:
            if not isinstance(ad, dict):
                continue
            tags_existing = ad.get("pattern_tags") or []
            if tags_existing:
                continue   # never overwrite a previous classifier decision
            body = (ad.get("body_excerpt") or "").strip()
            if not body:
                continue
            new_tags, new_why = _classify_ad({"body_text": body})
            if not new_tags:
                continue
            ad["pattern_tags"] = new_tags
            ad["why_by_tag"] = new_why
            n_retagged += 1
            retagged_by_competitor.setdefault(name, []).append(
                str(ad.get("ad_archive_id") or "")
            )
    if n_retagged > 0:
        payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
            newline="\n",
        )
    return {
        "path": str(path),
        "status": "reclassified" if n_retagged else "no_change",
        "ads_retagged": n_retagged,
        "by_competitor": retagged_by_competitor,
    }


def _run_ai_classification(
    *,
    prospect_root: Path,
    force: bool,
    model: Optional[str] = None,
) -> dict:
    """Read the saved competitor_ads.json, run the AI classifier across
    every ad, merge AI tags with the regex tags using
    `merge_ai_tags_with_regex`, and write the JSON back.

    Returns a small summary dict for the CLI to print.
    """
    from agents.outreach.reporting.ad_pattern_classifier import (
        AI_CLASSIFIER_VERSION,
        DEFAULT_AI_MODEL,
        classify_competitor_ads_batch,
        merge_ai_tags_with_regex,
    )

    out_path = prospect_root / "strategy" / COMPETITOR_ADS_FILENAME
    if not out_path.is_file():
        return {
            "status": "missing_json",
            "path": str(out_path),
            "ads_classified": 0,
            "ads_cached": 0,
            "ads_skipped": 0,
            "ads_failed": 0,
            "ads_total": 0,
            "ads_promoted_to_ai_tags": 0,
        }

    try:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {
            "status": f"parse_error: {e}",
            "path": str(out_path),
            "ads_classified": 0,
            "ads_cached": 0,
            "ads_skipped": 0,
            "ads_failed": 0,
            "ads_total": 0,
            "ads_promoted_to_ai_tags": 0,
        }

    # Brand context (best-effort) - lets the prompt frame cautions in
    # the prospect's voice. Failure is non-fatal.
    brand_context: dict = {}
    audit_path = prospect_root / "audit.json"
    try:
        audit_blob = json.loads(audit_path.read_text(encoding="utf-8"))
        brand_context = {
            "brand_name": audit_blob.get("prospect_name") or "",
            "niche": audit_blob.get("niche") or "",
        }
    except Exception:
        pass

    summary = classify_competitor_ads_batch(
        payload,
        prospect_root=prospect_root,
        brand_context=brand_context,
        force=force,
        model=model or DEFAULT_AI_MODEL,
    )

    # Merge AI tags into the per-ad `pattern_tags` field using the
    # repo's merge policy. `raw_regex_tags` is preserved so the
    # reclassify-only path can still audit the regex call.
    n_promoted = 0
    for _name, blob in (payload.get("competitors") or {}).items():
        for ad in (blob.get("sampled_ads") or []):
            regex_tags = ad.get("raw_regex_tags") or ad.get("pattern_tags") or []
            merge = merge_ai_tags_with_regex(regex_tags, ad)
            ad["pattern_tags"] = merge["pattern_tags"]
            ad["raw_regex_tags"] = merge["raw_regex_tags"]
            ad["tag_source"] = merge["tag_source"]
            ad["tag_source_reason"] = merge["tag_source_reason"]
            if merge["tag_source"] == "ai":
                n_promoted += 1

    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload.setdefault("ai_classifier", {})
    payload["ai_classifier"] = {
        "version": AI_CLASSIFIER_VERSION,
        "model": summary.get("model"),
        "last_run_at": payload["generated_at"],
        "skipped_reason": summary.get("skipped_reason"),
        "ads_total": summary["ads_total"],
        "ads_classified_this_run": summary["ads_classified"],
        "ads_cached": summary["ads_cached"],
        "ads_skipped": summary["ads_skipped"],
        "ads_failed": summary["ads_failed"],
        "status_counts": summary["status_counts"],
    }

    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )

    return {
        "status": "ok" if not summary.get("skipped_reason") else "skipped",
        "path": str(out_path),
        "ads_classified": summary["ads_classified"],
        "ads_cached": summary["ads_cached"],
        "ads_skipped": summary["ads_skipped"],
        "ads_failed": summary["ads_failed"],
        "ads_total": summary["ads_total"],
        "ads_promoted_to_ai_tags": n_promoted,
        "model": summary.get("model"),
        "skipped_reason": summary.get("skipped_reason"),
        "status_counts": summary["status_counts"],
    }


def _print_ai_report(report: dict) -> None:
    """Pretty-print the AI classification summary."""
    print("\nAI classifier pass:")
    print(f"  json file        : {report.get('path')}")
    print(f"  status           : {report.get('status')}")
    print(f"  model            : {report.get('model')}")
    if report.get("skipped_reason"):
        print(f"  skipped_reason   : {report['skipped_reason']}")
    print(f"  ads total        : {report.get('ads_total')}")
    print(f"  ads classified   : {report.get('ads_classified')}")
    print(f"  ads cached       : {report.get('ads_cached')}")
    print(f"  ads skipped      : {report.get('ads_skipped')}")
    print(f"  ads failed       : {report.get('ads_failed')}")
    print(f"  promoted to AI   : {report.get('ads_promoted_to_ai_tags')}")
    sc = report.get("status_counts") or {}
    if sc:
        print(f"  status histogram : {sc}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("prospect_id")
    ap.add_argument("--country", default="GB")
    ap.add_argument(
        "--max-ads-per-competitor",
        type=int,
        default=8,
        help=(
            "Number of ads to keep per competitor (default 8, hard cap 10). "
            "The renderer caps the visible strip at 3-5 thumbnails; the "
            "remaining ads stay in the JSON as overflow proof."
        ),
    )
    ap.add_argument("--max-competitors", type=int, default=8)
    ap.add_argument("--prospects-root", type=Path, default=None)
    ap.add_argument(
        "--competitors",
        nargs="*",
        default=None,
        help=(
            "Optional explicit list of competitor names. Overrides the "
            "StrategyBrief default set. Use double quotes for multi-word names."
        ),
    )
    ap.add_argument(
        "--reclassify-only",
        action="store_true",
        help=(
            "Skip scraping; re-run the regex classifier against the "
            "already-saved competitor_ads.json (only ads with empty "
            "pattern_tags are touched). Use when the classifier rules "
            "changed and you want to pick up freshly-supported tags "
            "without burning Apify credits."
        ),
    )
    ap.add_argument(
        "--ai-classify",
        action="store_true",
        help=(
            "After capture (or in --reclassify-only mode), run the AI "
            "vision classifier against each ad's screenshot. Reads "
            "ANTHROPIC_API_KEY from env or .env. Cached results are "
            "reused unless --force-ai-classify is passed. When no API "
            "key is set, the AI step is silently skipped and the regex "
            "tags continue to drive Section 05."
        ),
    )
    ap.add_argument(
        "--force-ai-classify",
        action="store_true",
        help=(
            "Re-run the AI classifier even on ads that already carry a "
            "cached ai_classification_status == 'ok'. Implies "
            "--ai-classify."
        ),
    )
    ap.add_argument(
        "--no-ai-classify",
        action="store_true",
        help=(
            "Force-disable AI classification even if --ai-classify is "
            "also passed. Useful in CI / cost-sensitive runs."
        ),
    )
    ap.add_argument(
        "--ai-model",
        default=None,
        help=(
            "Override the AI classifier model id (default: "
            "claude-sonnet-4-6). Must be a vision-capable Claude model."
        ),
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    # Hard cap. Past 10 ads/competitor the Apify pass starts dragging on
    # rate limits without producing visible strategy value (the renderer
    # only shows 3-5 thumbnails per card).
    if args.max_ads_per_competitor > 10:
        log.warning(
            "capture_competitor_ads: --max-ads-per-competitor=%d > 10; "
            "clamping to 10",
            args.max_ads_per_competitor,
        )
        args.max_ads_per_competitor = 10

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Lazy-load dotenv so the script works with .env-managed creds.
    # `override=True`: a Windows shell that has the var pre-set to
    # empty would otherwise win over the populated .env value.
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
        load_dotenv(_REPO_ROOT / ".env", override=True)
    except Exception:
        pass

    if not os.environ.get("APIFY_API_TOKEN"):
        print("FATAL: APIFY_API_TOKEN is not set in env or .env", file=sys.stderr)
        return 2

    prospects_root = args.prospects_root or (_REPO_ROOT / "prospects")
    prospect_root = (prospects_root / args.prospect_id).resolve()
    if not (prospect_root / "audit.json").exists():
        print(
            f"FATAL: no audit.json at {prospect_root}/audit.json - "
            "did you run the outreach audit yet?",
            file=sys.stderr,
        )
        return 3

    # Resolve effective AI classification mode early so reclassify-only
    # can also benefit from the AI pass.
    ai_enabled = (args.ai_classify or args.force_ai_classify) and not args.no_ai_classify
    ai_force = args.force_ai_classify and not args.no_ai_classify
    ai_model = (args.ai_model or "").strip() or None

    # Pure reclassify mode: no scraping, no Apify cost.
    if args.reclassify_only:
        report = _reclassify_existing(prospect_root)
        print(f"\nReclassify-only pass for {args.prospect_id}:")
        print(f"  json file        : {report['path']}")
        print(f"  status           : {report['status']}")
        print(f"  ads retagged     : {report['ads_retagged']}")
        for comp, ids in (report.get("by_competitor") or {}).items():
            print(f"     {comp}: {', '.join(ids)}")
        if ai_enabled:
            ai_report = _run_ai_classification(
                prospect_root=prospect_root,
                force=ai_force,
                model=ai_model,
            )
            _print_ai_report(ai_report)
        return 0 if report["status"] in {"reclassified", "no_change"} else 1

    # Resolve competitor names. Default: pull from a fresh StrategyBrief
    # (which already knows niche-aware defaults).
    if args.competitors:
        competitor_names = list(args.competitors)
    else:
        brief = StrategyBrief.from_audit(args.prospect_id, prospects_root=prospects_root)
        competitor_names = [c.name for c in brief.competitors][: args.max_competitors]

    print(f"\nCapturing competitor ads for prospect: {args.prospect_id}")
    print(f"  country         : {args.country}")
    print(f"  max ads / comp  : {args.max_ads_per_competitor}")
    print(f"  competitors     : {competitor_names}\n")

    fb_tool = make_fb_ads_search_tool()

    out_path = prospect_root / "strategy" / COMPETITOR_ADS_FILENAME
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Merge-with-existing: preserve competitor entries we didn't capture
    # in this pass so re-running the script with a focused
    # `--competitors X Y` doesn't wipe a previous full sweep. Per-
    # competitor entries that ARE re-captured get overwritten with the
    # fresh result (intended: a re-run is a refresh).
    existing_payload: dict = {"competitors": {}}
    if out_path.is_file():
        try:
            existing_payload = json.loads(out_path.read_text(encoding="utf-8"))
            if not isinstance(existing_payload, dict):
                existing_payload = {"competitors": {}}
            if not isinstance(existing_payload.get("competitors"), dict):
                existing_payload["competitors"] = {}
        except (OSError, json.JSONDecodeError):
            existing_payload = {"competitors": {}}

    payload: dict = {"competitors": dict(existing_payload.get("competitors") or {})}
    for name in competitor_names:
        print(f"--- {name} ---")
        result = _capture_one_competitor(
            prospect_id=args.prospect_id,
            competitor_name=name,
            country=args.country,
            max_ads=args.max_ads_per_competitor,
            fb_tool=fb_tool,
            prospects_root=prospects_root,
        )
        n_ads = len(result["sampled_ads"])
        n_with_shot = sum(1 for a in result["sampled_ads"] if a.get("screenshot_path"))
        print(
            f"  -> sampled {n_ads} ad(s); {n_with_shot} with local screenshot"
        )
        if result["errors"]:
            for e in result["errors"]:
                print(f"     ERROR: {e}")
        payload["competitors"][name] = result

    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )
    print(f"\nWrote {out_path}")

    # Optional AI vision classification pass. Reads the JSON we just
    # wrote and mutates it in place, then re-writes the file with merged
    # tag decisions.
    if ai_enabled:
        ai_report = _run_ai_classification(
            prospect_root=prospect_root,
            force=ai_force,
            model=ai_model,
        )
        _print_ai_report(ai_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
