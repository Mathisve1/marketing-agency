"""AI-assisted competitor ad pattern classifier.

The regex classifier in `scripts/capture_competitor_ads.py` only sees
body text. It misses the patterns that live in the SCREENSHOT - review
overlays, creator/UGC framing, texture demos, editorial composition,
discount badges, before/after layouts, founder-on-camera shots.

This module adds an OPTIONAL vision-capable enhancement layer:

  1. Reads a competitor ad's screenshot + body / CTA / media-type metadata.
  2. Sends both to Claude Sonnet (vision-capable) with a strict prompt.
  3. Parses the strict JSON response into the schema below.
  4. Returns a per-ad dict the capture script can merge into the saved
     `competitor_ads.json` alongside the existing regex tags.

Design constraints (matches the task brief):
  - The regex classifier stays as the source-of-truth fallback. AI is
    an enhancement layer, never a replacement.
  - Cached per-ad results are reused unless `force=True` is passed -
    classifying 50 ads on a re-run shouldn't burn 50 API calls.
  - The classifier never invents facts beyond the screenshot / body
    text. The prompt explicitly forbids guessing.
  - If `ANTHROPIC_API_KEY` is missing OR the `anthropic` package is
    not installed, `classify_competitor_ad_with_ai()` returns a
    "skipped" status. The full run never crashes on a missing key.
  - JSON parsing tolerates the model returning extra prose around the
    JSON object (a common Claude failure mode) - we extract the first
    `{...}` block and validate against the schema.

Public API:
  - `AI_ALLOWED_PATTERNS`     -> the closed set of tags Section 05 supports
  - `AI_CLASSIFIER_VERSION`   -> bump when prompt / schema changes
  - `classify_competitor_ad_with_ai(...)` -> single-ad classifier
  - `classify_competitor_ads_batch(...)`  -> caching driver used by the
    capture script
  - `merge_ai_tags_with_regex(regex_tags, ai_result)` -> the rule the
    capture script uses to write the final `pattern_tags` list back to
    the JSON

The module is import-safe even when `anthropic` is not installed; the
import is lazy and only fires when an actual classification call is
made.
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

log = logging.getLogger(__name__)


# Bump this when the prompt, schema, or model changes so old cached
# entries are invalidated automatically by `classify_competitor_ads_batch`.
AI_CLASSIFIER_VERSION = "1.0.0"

# Default vision-capable model. Mirrors `core.models.SUPPORTED_MODEL_IDS`.
DEFAULT_AI_MODEL = "claude-sonnet-4-6"


# The closed set of pattern tags Section 05 understands. Mirrors the
# `_PATTERN_RULES` taxonomy in `scripts/capture_competitor_ads.py`.
AI_ALLOWED_PATTERNS: tuple[str, ...] = (
    "review_social_proof",
    "ingredient_proof",
    "sensitive_skin_reassurance",
    "founder_expert_credibility",
    "texture_application_demo",
    "routine_simplification",
    "offer_bundle",
    "editorial_luxury",
    "before_after_claim",
    "discount_led",
)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AdClassificationSchema:
    """Strict schema for the AI classifier output.

    Each top-level key in the model's JSON response is validated against
    this schema before merging. Unknown keys are dropped; missing
    optional keys default to safe values.
    """
    REQUIRED_KEYS: tuple[str, ...] = (
        "primary_pattern",
        "secondary_patterns",
        "rejected_patterns",
        "confidence",
        "evidence_notes",
        "visual_evidence",
        "text_evidence",
        "why_primary_pattern",
        "should_use_for_strategy",
        "caution",
    )
    CONFIDENCE_VALUES: tuple[str, ...] = ("high", "medium", "low")


SCHEMA = AdClassificationSchema()


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


def is_ai_classification_available() -> tuple[bool, Optional[str]]:
    """Return `(ok, reason_if_not_ok)`. Checks for:
      - `anthropic` Python package importable
      - `ANTHROPIC_API_KEY` set in the environment

    Never raises. Safe to call before every classification attempt.
    """
    try:
        import anthropic  # noqa: F401
    except Exception as exc:  # pragma: no cover - dep is in requirements
        return False, f"anthropic SDK not importable: {exc!r}"
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        return False, "ANTHROPIC_API_KEY not set"
    return True, None


def classify_competitor_ad_with_ai(
    ad: Mapping[str, Any],
    *,
    screenshot_path: Optional[Path],
    allowed_patterns: Sequence[str] = AI_ALLOWED_PATTERNS,
    brand_context: Optional[Mapping[str, Any]] = None,
    model: str = DEFAULT_AI_MODEL,
    timeout: float = 30.0,
    _client: Any = None,
) -> dict:
    """Run the AI classifier against a single competitor ad.

    Args:
      ad: dict-shaped ad row from `competitor_ads.json` carrying
        `ad_archive_id`, `body_excerpt`, `cta_text`, `media_type`,
        `competitor_name` (we infer this if not present), and
        `screenshot_path` (relative to the prospect root).
      screenshot_path: absolute path to the on-disk screenshot. None or
        a missing file is allowed (we then classify on body text alone
        and downgrade `confidence` to at most `medium`).
      allowed_patterns: closed set of pattern tags the model may pick from.
      brand_context: optional dict with `{"brand_name": str, "niche":
        str}` describing the prospect (NOT the competitor) so the model
        can frame the cautions in the prospect's voice.
      model: Anthropic model id. Defaults to the repo's recommended
        Sonnet 4.6.
      timeout: per-request timeout in seconds.
      _client: pre-built `anthropic.Anthropic` instance, for tests.

    Returns:
      `dict` always carrying these top-level keys (the capture script
      merges them straight into the saved JSON):

        ai_classification_status: "ok" | "skipped_no_key" |
          "skipped_no_screenshot_no_body" | "model_error" |
          "json_parse_error" | "schema_error"
        ai_primary_pattern: str | None
        ai_pattern_tags: list[str]      # primary + secondary, de-duped
        ai_confidence: "high" | "medium" | "low" | None
        ai_evidence_notes: list[str]
        ai_visual_evidence: list[str]
        ai_text_evidence: list[str]
        ai_caution: str | None
        ai_classifier_version: str
        ai_model: str | None             # the model id we asked
        ai_should_use_for_strategy: bool
        ai_raw_response: str | None      # the raw model text on error
        ai_error: str | None             # human-readable reason on error

    Never raises. Errors surface in `ai_classification_status` and the
    capture script's regex tags continue to drive Section 05.
    """
    ok, why = is_ai_classification_available()
    if not ok:
        return _skipped(reason=why or "unavailable", model=model)

    body = (ad.get("body_excerpt") or ad.get("body_text") or "").strip()
    has_screenshot = (
        screenshot_path is not None
        and Path(screenshot_path).is_file()
    )
    if not has_screenshot and not body:
        return _skipped(
            reason="no screenshot and no body text - nothing to classify",
            status="skipped_no_screenshot_no_body",
            model=model,
        )

    # Build the prompt content blocks. Vision goes first so the model
    # anchors on the screenshot, then the structured metadata block.
    blocks: list[dict] = []
    if has_screenshot:
        try:
            data, media_type = _encode_screenshot(Path(screenshot_path))
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data,
                },
            })
        except Exception as exc:
            log.warning(
                "ad_pattern_classifier: could not encode screenshot %s: %r",
                screenshot_path, exc,
            )
            has_screenshot = False

    prompt_text = _build_user_prompt(
        ad=ad,
        allowed_patterns=allowed_patterns,
        brand_context=brand_context or {},
        has_screenshot=has_screenshot,
    )
    blocks.append({"type": "text", "text": prompt_text})

    # Run the model.
    try:
        client = _client or _build_anthropic_client()
    except Exception as exc:
        return _skipped(
            reason=f"could not build Anthropic client: {exc!r}",
            status="skipped_no_key",
            model=model,
        )

    raw_text = ""
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=1024,
            temperature=0.0,
            timeout=timeout,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": blocks}],
        )
        raw_text = _extract_text(msg)
    except Exception as exc:
        log.warning(
            "ad_pattern_classifier: model call failed for ad %s: %r",
            ad.get("ad_archive_id"), exc,
        )
        return _error(
            status="model_error",
            error=_redact_secrets(repr(exc)),
            model=model,
        )

    # Parse the JSON.
    parsed = _safe_parse_json(raw_text)
    if parsed is None:
        return _error(
            status="json_parse_error",
            error="model response was not parseable JSON",
            model=model,
            raw_response=_redact_secrets(raw_text)[:2000],
        )

    # Validate the schema.
    try:
        normalised = _normalise_result(parsed, allowed_patterns=allowed_patterns)
    except _SchemaError as exc:
        return _error(
            status="schema_error",
            error=str(exc),
            model=model,
            raw_response=_redact_secrets(raw_text)[:2000],
        )

    normalised["ai_classification_status"] = "ok"
    normalised["ai_classifier_version"] = AI_CLASSIFIER_VERSION
    normalised["ai_model"] = model
    normalised["ai_error"] = None
    normalised["ai_raw_response"] = None
    return normalised


def classify_competitor_ads_batch(
    competitor_ads_payload: dict,
    *,
    prospect_root: Path,
    brand_context: Optional[Mapping[str, Any]] = None,
    force: bool = False,
    model: str = DEFAULT_AI_MODEL,
    timeout: float = 30.0,
    _client: Any = None,
    on_ad: Optional[Any] = None,
) -> dict:
    """Iterate every sampled ad in a `competitor_ads.json` payload and
    apply `classify_competitor_ad_with_ai`. Returns a small summary
    dict; mutates the input payload IN PLACE so the caller can write it
    back to disk.

    Caching policy:
      - Skip an ad if `ai_classification_status == "ok"` AND
        `ai_classifier_version == AI_CLASSIFIER_VERSION` AND `force` is
        False. Bumping `AI_CLASSIFIER_VERSION` invalidates the cache.
      - `--force-ai-classify` (force=True) re-runs every ad regardless.

    `on_ad` is an optional callback `(competitor_name, ad_archive_id,
    result_dict) -> None` for live logging during a long run.

    Returns:
      {
        "ads_total":      int,
        "ads_classified": int,   # this run
        "ads_cached":     int,
        "ads_skipped":    int,
        "ads_failed":     int,
        "model":          str,
        "status_counts":  dict[str, int],   # ai_classification_status histogram
        "skipped_reason": str | None,       # set when no key / no SDK
      }
    """
    summary = {
        "ads_total": 0,
        "ads_classified": 0,
        "ads_cached": 0,
        "ads_skipped": 0,
        "ads_failed": 0,
        "model": model,
        "status_counts": {},
        "skipped_reason": None,
    }

    available, why = is_ai_classification_available()
    if not available:
        summary["skipped_reason"] = why
        # Walk and mark skipped; do not invent classifications.
        for _name, blob in (competitor_ads_payload.get("competitors") or {}).items():
            for ad in (blob.get("sampled_ads") or []):
                summary["ads_total"] += 1
                if not ad.get("ai_classification_status"):
                    ad.update(_skipped(reason=why or "unavailable", model=model))
                summary["ads_skipped"] += 1
                _bump(summary["status_counts"], ad.get("ai_classification_status"))
        return summary

    for name, blob in (competitor_ads_payload.get("competitors") or {}).items():
        for ad in (blob.get("sampled_ads") or []):
            summary["ads_total"] += 1
            ad_id = str(ad.get("ad_archive_id") or "").strip()
            if not ad_id:
                summary["ads_skipped"] += 1
                _bump(summary["status_counts"], "skipped_no_ad_id")
                continue

            # Cache hit?
            cached_ok = (
                ad.get("ai_classification_status") == "ok"
                and ad.get("ai_classifier_version") == AI_CLASSIFIER_VERSION
            )
            if cached_ok and not force:
                summary["ads_cached"] += 1
                _bump(summary["status_counts"], "ok")
                if on_ad:
                    try:
                        on_ad(name, ad_id, ad)
                    except Exception:
                        pass
                continue

            shot_rel = ad.get("screenshot_path")
            shot_abs: Optional[Path] = None
            if shot_rel:
                shot_abs = (prospect_root / shot_rel)
                if not shot_abs.is_file():
                    shot_abs = None

            ad_with_name = dict(ad)
            ad_with_name.setdefault("competitor_name", name)

            result = classify_competitor_ad_with_ai(
                ad_with_name,
                screenshot_path=shot_abs,
                brand_context=brand_context,
                model=model,
                timeout=timeout,
                _client=_client,
            )
            # Merge into the ad dict in place.
            ad.update(result)
            status = result.get("ai_classification_status") or "unknown"
            _bump(summary["status_counts"], status)
            if status == "ok":
                summary["ads_classified"] += 1
            elif status.startswith("skipped"):
                summary["ads_skipped"] += 1
            else:
                summary["ads_failed"] += 1
            if on_ad:
                try:
                    on_ad(name, ad_id, ad)
                except Exception:
                    pass

    return summary


def merge_ai_tags_with_regex(
    regex_tags: Sequence[str],
    ai_result: Mapping[str, Any],
    *,
    accept_confidence: Sequence[str] = ("high", "medium"),
) -> dict:
    """Decide the final `pattern_tags` list for an ad based on regex +
    AI output.

    Policy:
      - If `ai_result` carries a usable classification (status == "ok",
        confidence in `accept_confidence`, AND
        `ai_should_use_for_strategy is True`), AI tags WIN: the final
        `pattern_tags` becomes the AI primary + secondary list.
      - Otherwise, the original regex tags are kept.
      - The raw regex tags are ALWAYS preserved on the ad under
        `raw_regex_tags` so the operator can audit the call.

    Returns a dict with:
        pattern_tags:    list[str]   # the final tags Section 05 consumes
        raw_regex_tags:  list[str]   # never lost
        tag_source:      "ai" | "regex" | "regex_fallback_low_confidence"
        tag_source_reason: str       # one-line audit trail
    """
    regex_tags_list = list(regex_tags or [])
    status = ai_result.get("ai_classification_status")
    confidence = (ai_result.get("ai_confidence") or "").lower()
    should_use = bool(ai_result.get("ai_should_use_for_strategy", False))

    if (
        status == "ok"
        and confidence in tuple(accept_confidence)
        and should_use
    ):
        primary = ai_result.get("ai_primary_pattern")
        ai_tags = list(ai_result.get("ai_pattern_tags") or [])
        if primary and primary not in ai_tags:
            ai_tags = [primary, *ai_tags]
        # De-dup keeping first occurrence.
        seen: set[str] = set()
        final_tags: list[str] = []
        for t in ai_tags:
            if t and t not in seen:
                seen.add(t)
                final_tags.append(t)
        return {
            "pattern_tags": final_tags,
            "raw_regex_tags": regex_tags_list,
            "tag_source": "ai",
            "tag_source_reason": (
                f"AI {confidence}-confidence classification preferred; "
                f"primary={primary!r}, n_secondary={len(final_tags) - 1}."
            ),
        }

    # AI was not usable - fall back to regex.
    if status == "ok" and confidence and confidence not in tuple(accept_confidence):
        reason = (
            f"AI returned {confidence}-confidence; below the "
            f"{'/'.join(accept_confidence)} acceptance bar - kept regex tags."
        )
        source = "regex_fallback_low_confidence"
    elif status and status != "ok":
        reason = f"AI status {status!r} - kept regex tags."
        source = "regex"
    else:
        reason = "No AI classification on this ad - kept regex tags."
        source = "regex"

    return {
        "pattern_tags": regex_tags_list,
        "raw_regex_tags": regex_tags_list,
        "tag_source": source,
        "tag_source_reason": reason,
    }


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #


_SYSTEM_PROMPT = """You are a careful creative-strategy analyst classifying paid social ads for an agency.

You will be shown a competitor ad: a screenshot (if available) plus the body text, CTA, media type, and competitor name. Your job is to label the ad with the creative pattern it most clearly demonstrates, from a closed list of allowed patterns.

Rules you MUST follow:
  - Only pick a pattern if there is direct evidence in the screenshot or the body text. Never guess from brand name, niche, or general positioning.
  - Pick ONE primary pattern per ad. Add secondary patterns only when they are independently visible.
  - When the ad's evidence is weak, ambiguous, or off-list, set confidence to "low" and should_use_for_strategy to false.
  - When the ad is a Dynamic Catalog (DCO) placeholder, a generic product still life with no other signal, or carries no body copy AND no visible pattern cue, set primary_pattern to null and confidence to "low".
  - You must respond with a single JSON object and NOTHING else - no markdown fence, no prose before or after.
  - Never invent reviews, ingredients, claims, or competitor names that are not visible in the ad.
"""


def _build_user_prompt(
    *,
    ad: Mapping[str, Any],
    allowed_patterns: Sequence[str],
    brand_context: Mapping[str, Any],
    has_screenshot: bool,
) -> str:
    body = (ad.get("body_excerpt") or ad.get("body_text") or "").strip()
    cta = (ad.get("cta_text") or "").strip()
    media = (ad.get("media_type") or "").strip().upper()
    competitor = (ad.get("competitor_name") or ad.get("page_name") or "Unknown").strip()
    brand_name = (brand_context.get("brand_name") or "").strip()
    niche = (brand_context.get("niche") or "").strip()

    allowed_list = "\n".join(f"  - {p}" for p in allowed_patterns)
    screenshot_note = (
        "A SCREENSHOT of the ad is attached above this message. "
        "Read the visible overlay text, layout, framing, and any badges."
        if has_screenshot
        else "No screenshot is available - classify from the body text only "
             "and downgrade confidence accordingly."
    )

    prospect_block = ""
    if brand_name or niche:
        prospect_block = (
            f"\nClient context (the prospect, NOT the competitor):\n"
            f"  - Brand: {brand_name or 'unknown'}\n"
            f"  - Niche: {niche or 'unknown'}\n"
            f"This context is for framing the caution field only; do NOT "
            f"let it influence pattern selection.\n"
        )

    return f"""{screenshot_note}

Competitor ad metadata:
  - Competitor: {competitor}
  - Body text:  {body or '(no body text - DCO placeholder or image-only ad)'}
  - CTA:        {cta or '(none)'}
  - Media type: {media or '(unknown)'}
{prospect_block}
Allowed pattern tags (closed list - choose from this set only):
{allowed_list}

Return a single JSON object with this exact shape, no prose:

{{
  "primary_pattern": "<one of the allowed tags or null>",
  "secondary_patterns": ["<tag>", ...],
  "rejected_patterns": ["<tag>", ...],
  "confidence": "high" | "medium" | "low",
  "evidence_notes": ["<short note tying the call to specific evidence>", ...],
  "visual_evidence": ["<what you saw in the screenshot, or empty>", ...],
  "text_evidence":   ["<phrase from the body text or CTA>", ...],
  "why_primary_pattern": "<one sentence saying why this is the primary>",
  "should_use_for_strategy": true | false,
  "caution": "<optional one-line risk note or null>"
}}
"""


# --------------------------------------------------------------------------- #
# Helpers - encoding, parsing, errors
# --------------------------------------------------------------------------- #


def _encode_screenshot(path: Path) -> tuple[str, str]:
    """Base64-encode an image file. Returns (base64_string, media_type).
    Falls back to image/png when mimetypes cannot guess."""
    raw = path.read_bytes()
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError(
            f"screenshot too large for vision call: {len(raw)} bytes (>5MB)"
        )
    mt, _ = mimetypes.guess_type(str(path))
    if mt not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        # Sniff PNG magic; Anthropic API accepts png / jpeg / webp / gif.
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            mt = "image/png"
        elif raw[:3] == b"\xff\xd8\xff":
            mt = "image/jpeg"
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            mt = "image/webp"
        else:
            mt = "image/png"
    return base64.b64encode(raw).decode("ascii"), mt


def _build_anthropic_client() -> Any:
    """Lazy-build an `anthropic.Anthropic` client. Raises if the env var
    is missing - callers wrap this in their own try/except."""
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")
    return anthropic.Anthropic(api_key=api_key)


def _extract_text(msg: Any) -> str:
    """Pull the assistant text out of an Anthropic Messages response.
    Handles both the new SDK shape (`msg.content` = list of blocks)
    and any defensive fallback."""
    try:
        blocks = getattr(msg, "content", None) or []
        out: list[str] = []
        for b in blocks:
            t = getattr(b, "text", None)
            if t:
                out.append(t)
            elif isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text") or "")
        return "\n".join(out).strip()
    except Exception:
        return ""


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _safe_parse_json(text: str) -> Optional[dict]:
    """Parse the first JSON object found in `text`, tolerating prose
    around it. Returns None if no parseable object is found."""
    if not text:
        return None
    # First, try the whole string.
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Fallback: find the largest {...} block.
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


class _SchemaError(ValueError):
    """Raised when the parsed JSON does not match the expected schema."""


def _normalise_result(
    raw: dict,
    *,
    allowed_patterns: Sequence[str],
) -> dict:
    """Validate the parsed model response and return the `ai_*`-prefixed
    keys the capture script merges into the saved JSON.

    Tolerates:
      - missing optional keys (gets safe defaults)
      - extra keys (dropped silently)
      - tag values outside `allowed_patterns` (dropped, recorded in
        `rejected_patterns`)
      - case differences on confidence ("HIGH" -> "high")
    """
    allowed = set(allowed_patterns)

    def _str_list(key: str) -> list[str]:
        v = raw.get(key)
        if not isinstance(v, list):
            return []
        return [str(s).strip() for s in v if isinstance(s, str) and s.strip()]

    primary = raw.get("primary_pattern")
    if isinstance(primary, str):
        primary = primary.strip().lower() or None
    else:
        primary = None
    if primary is not None and primary not in allowed:
        primary = None  # drop out-of-set primary - never invent

    secondary_raw = _str_list("secondary_patterns")
    secondary = [s.lower() for s in secondary_raw if s.lower() in allowed and s.lower() != primary]

    rejected = [s for s in _str_list("rejected_patterns")]

    confidence = (raw.get("confidence") or "").strip().lower()
    if confidence not in SCHEMA.CONFIDENCE_VALUES:
        raise _SchemaError(
            f"confidence must be one of {SCHEMA.CONFIDENCE_VALUES}, got {confidence!r}"
        )

    pattern_tags: list[str] = []
    if primary:
        pattern_tags.append(primary)
    for s in secondary:
        if s not in pattern_tags:
            pattern_tags.append(s)

    return {
        "ai_primary_pattern": primary,
        "ai_pattern_tags": pattern_tags,
        "ai_confidence": confidence,
        "ai_evidence_notes": _str_list("evidence_notes")[:6],
        "ai_visual_evidence": _str_list("visual_evidence")[:6],
        "ai_text_evidence": _str_list("text_evidence")[:6],
        "ai_caution": _coerce_caution(raw.get("caution")),
        "ai_should_use_for_strategy": bool(raw.get("should_use_for_strategy", False)),
        "ai_rejected_patterns": rejected[:10],
        "ai_why_primary_pattern": _coerce_str(raw.get("why_primary_pattern")),
    }


def _coerce_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    return str(v)


def _coerce_caution(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return None


def _skipped(
    *,
    reason: str,
    status: str = "skipped_no_key",
    model: str = DEFAULT_AI_MODEL,
) -> dict:
    """Return a per-ad result dict for the skipped path."""
    return {
        "ai_classification_status": status,
        "ai_primary_pattern": None,
        "ai_pattern_tags": [],
        "ai_confidence": None,
        "ai_evidence_notes": [],
        "ai_visual_evidence": [],
        "ai_text_evidence": [],
        "ai_caution": None,
        "ai_classifier_version": AI_CLASSIFIER_VERSION,
        "ai_model": model,
        "ai_should_use_for_strategy": False,
        "ai_raw_response": None,
        "ai_error": _redact_secrets(reason),
    }


def _error(
    *,
    status: str,
    error: str,
    model: str = DEFAULT_AI_MODEL,
    raw_response: Optional[str] = None,
) -> dict:
    return {
        "ai_classification_status": status,
        "ai_primary_pattern": None,
        "ai_pattern_tags": [],
        "ai_confidence": None,
        "ai_evidence_notes": [],
        "ai_visual_evidence": [],
        "ai_text_evidence": [],
        "ai_caution": None,
        "ai_classifier_version": AI_CLASSIFIER_VERSION,
        "ai_model": model,
        "ai_should_use_for_strategy": False,
        "ai_raw_response": raw_response,
        "ai_error": _redact_secrets(error),
    }


def _bump(counter: dict, key: Optional[str]) -> None:
    if not key:
        return
    counter[key] = counter.get(key, 0) + 1


_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{20,}|"
    r"AIzaSy[A-Za-z0-9_\-]{20,}|"
    r"AKIA[A-Z0-9]{16,}|"
    r"apify_api_[A-Za-z0-9_\-]{20,}|"
    r"ANTHROPIC_API_KEY=[^\s]+)",
    re.IGNORECASE,
)


def _redact_secrets(text: str) -> str:
    """Conservatively redact common credential shapes from error / log
    text. Belt-and-braces - the classifier never logs the key directly,
    but exception strings can contain anything.
    """
    if not text:
        return text
    return _SECRET_RE.sub("<redacted>", str(text))
