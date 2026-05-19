"""Enhancor Seedance 2.0 Full Access provider.

Wraps the Seedance video-generation API at:

    https://apireq.enhancor.ai/api/enhancor-ugc-full-access/v1

Endpoints:
    POST /queue        - submit a generation job
    POST /status       - poll for status / result

Auth:
    header `x-api-key: <ENHANCOR_API_KEY>`

The wire-protocol details (allowed durations / resolutions / aspect
ratios, mode-specific required fields, the 1080p-requires-fast_mode=false
rule, etc.) are mirrored from `docs/enhancor_api_spec.md` and the
already-tested helpers in `scripts/enhancor_smoke_test.py`. The payload
builders here are independent copies (not imports) because:

 1. The smoke script is operator tooling under `scripts/`; the provider
    layer is library code under `agents/`. We don't want library code
    importing from `scripts/`.
 2. Keeping the validators here means `agents/producer/providers/` is a
    self-contained adapter set that can be lifted into a new repo
    without dragging in the smoke script.

The payload builders enforce the same dashboard rules the smoke test
already enforces, and the dedicated test file pins both sides to the
same contract.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agents.producer.providers.base import (
    Provider,
    ProviderError,
    ProviderGenerationResult,
    ProviderJobRequest,
    ProviderJobResponse,
    ProviderJobStatus,
    ProviderStatus,
    classify_provider_status,
    redact_api_key_headers,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants - pinned to the dashboard contract
# --------------------------------------------------------------------------- #

DEFAULT_BASE_URL = "https://apireq.enhancor.ai/api/enhancor-ugc-full-access/v1"
QUEUE_PATH = "/queue"
STATUS_PATH = "/status"

API_KEY_HEADER = "x-api-key"

ALLOWED_RESOLUTIONS: frozenset[str] = frozenset({"480p", "720p", "1080p"})
ALLOWED_ASPECT_RATIOS: frozenset[str] = frozenset(
    {"16:9", "9:16", "4:3", "3:4", "1:1", "21:9"}
)
ALLOWED_DURATIONS_SEC: frozenset[str] = frozenset(str(n) for n in range(4, 16))

# UGC mode: products + influencers + images must total <= 9.
UGC_TOTAL_ASSET_CAP = 9
# multi_reference: separate caps per array.
MAX_IMAGES = 9
MAX_VIDEOS = 3
MAX_AUDIOS = 3
COMBINED_VIDEO_DURATION_CAP_SEC = 15.0
COMBINED_AUDIO_DURATION_CAP_SEC = 15.0

# Smoke / Phase-0 defaults — cheapest viable path.
DEFAULT_DURATION_SEC = "4"
DEFAULT_RESOLUTION = "480p"
DEFAULT_ASPECT_RATIO = "9:16"
DEFAULT_FAST_MODE = True

# ----- Capability metadata (per-mode) ------------------------------------- #
# Surfaced as named constants so downstream callers (the dashboard, the
# operator review surface, the cost ledger) can branch on them without
# scraping docstrings. Findings confirmed by the 2026-05-16 smoke runs
# (see docs/enhancor_api_spec.md § A "Confirmed via …").

UGC_OUTPUT_INCLUDES_NATIVE_AUDIO = True
"""UGC mode (`type=image-to-video`, `mode=ugc`) generates a fully muxed
mp4 with a native audio track. Confirmed by smoke 2026-05-16
(requestId `6a08562a60cece3ba3062062`). The Audio Fixer becomes a
cleanup pass for UGC output, not an "add audio where there is none"
pass."""

TEXT_TO_VIDEO_OUTPUT_IS_SILENT = True
"""text-to-video output is silent (no audio track). Confirmed by smoke
2026-05-16 (requestId `6a0850e96c164b8f24cb7d05`). Callers must NOT
assume audio is present on `type=text-to-video` results."""

MULTI_REFERENCE_OUTPUT_AUDIO_BEHAVIOUR = "unknown"
"""multi_reference mode audio behaviour was not exercised in the
Phase-0 smoke. UNKNOWN/NEEDS TEST."""

# Polling defaults. Adapters never loop on `poll_status()`; this lives on
# the helper `wait_for_completion()`.
DEFAULT_POLL_INTERVAL_SEC = 10
DEFAULT_POLL_TIMEOUT_SEC = 10 * 60   # 10 minutes


# --------------------------------------------------------------------------- #
# Payload builders (PURE, tested in tests/test_enhancor_providers.py)
# --------------------------------------------------------------------------- #


def _validate_https(url: str, *, field_name: str) -> None:
    """Reject anything that isn't an HTTPS URL.

    Enhancor accepts public HTTPS URLs only — base64-inline is not
    supported on these endpoints. We fail fast in the library layer so
    callers don't burn an API call on a bad URL.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"{field_name} is required and must be a non-empty string")
    low = url.strip().lower()
    if not low.startswith("https://"):
        raise ValueError(f"{field_name} must be an https:// URL; got {url!r}")


def _validate_common_video_params(
    *,
    duration_sec: str,
    resolution: str,
    aspect_ratio: str,
    fast_mode: bool,
) -> None:
    """Field-shape rules pinned to the Enhancor dashboard.

    Same rules the operator smoke script enforces; duplicated here so
    the provider library never depends on `scripts/`.
    """
    if duration_sec not in ALLOWED_DURATIONS_SEC:
        raise ValueError(
            f"duration must be one of {sorted(ALLOWED_DURATIONS_SEC, key=int)!r}; "
            f"got {duration_sec!r}"
        )
    if resolution not in ALLOWED_RESOLUTIONS:
        raise ValueError(
            f"resolution must be one of {sorted(ALLOWED_RESOLUTIONS)!r}; "
            f"got {resolution!r}"
        )
    if aspect_ratio not in ALLOWED_ASPECT_RATIOS:
        raise ValueError(
            f"aspect_ratio must be one of {sorted(ALLOWED_ASPECT_RATIOS)!r}; "
            f"got {aspect_ratio!r}"
        )
    if resolution == "1080p" and fast_mode:
        raise ValueError(
            "resolution=1080p requires fast_mode=false "
            "(dashboard rule; see docs/enhancor_api_spec.md § A)"
        )


def build_text_to_video_payload(
    *,
    prompt: str,
    webhook_url: str,
    duration_sec: str = DEFAULT_DURATION_SEC,
    resolution: str = DEFAULT_RESOLUTION,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    fast_mode: bool = DEFAULT_FAST_MODE,
) -> dict[str, Any]:
    """Build the cheapest probe payload.

    `text-to-video` MUST NOT carry `images` / `videos` / `audios` /
    `products` / `influencers` (dashboard rule).
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required")
    _validate_https(webhook_url, field_name="webhook_url")
    _validate_common_video_params(
        duration_sec=duration_sec,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        fast_mode=fast_mode,
    )
    return {
        "type": "text-to-video",
        "prompt": prompt,
        "webhook_url": webhook_url,
        "duration": duration_sec,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "fast_mode": fast_mode,
    }


def build_ugc_payload(
    *,
    prompt: str,
    webhook_url: str,
    products: list[str],
    influencers: list[str],
    duration_sec: str = "5",
    resolution: str = DEFAULT_RESOLUTION,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    fast_mode: bool = DEFAULT_FAST_MODE,
    images: Optional[list[str]] = None,
    full_access: bool = True,
) -> dict[str, Any]:
    """Build an `image-to-video / ugc` payload.

    Dashboard rules enforced:
      - `len(products) + len(influencers) + len(images) <= 9`
      - `full_access=true` defaulted on (UGC implies a human face;
        callers can pass `full_access=False` only if they accept the
        risk that Enhancor will reject the call)
      - every URL must be HTTPS
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required")
    _validate_https(webhook_url, field_name="webhook_url")
    _validate_common_video_params(
        duration_sec=duration_sec,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        fast_mode=fast_mode,
    )
    if not isinstance(products, list) or not products:
        raise ValueError("UGC mode requires at least one product URL")
    if not isinstance(influencers, list) or not influencers:
        raise ValueError("UGC mode requires at least one influencer URL")
    for u in products:
        _validate_https(u, field_name="products[*]")
    for u in influencers:
        _validate_https(u, field_name="influencers[*]")
    images = list(images or [])
    for u in images:
        _validate_https(u, field_name="images[*]")
    total_assets = len(products) + len(influencers) + len(images)
    if total_assets > UGC_TOTAL_ASSET_CAP:
        raise ValueError(
            f"UGC mode: products+influencers+images must be <= {UGC_TOTAL_ASSET_CAP}; "
            f"got {total_assets}"
        )
    payload: dict[str, Any] = {
        "type": "image-to-video",
        "mode": "ugc",
        "prompt": prompt,
        "webhook_url": webhook_url,
        "duration": duration_sec,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "fast_mode": fast_mode,
        "full_access": full_access,
        "products": list(products),
        "influencers": list(influencers),
    }
    if images:
        payload["images"] = images
    return payload


def build_multi_reference_payload(
    *,
    prompt: str,
    webhook_url: str,
    images: Optional[list[str]] = None,
    videos: Optional[list[str]] = None,
    audios: Optional[list[str]] = None,
    duration_sec: str = "5",
    resolution: str = DEFAULT_RESOLUTION,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    fast_mode: bool = DEFAULT_FAST_MODE,
) -> dict[str, Any]:
    """Build an `image-to-video / multi_reference` payload.

    Caller supplies any combination of `images`, `videos`, `audios`. At
    least one must be non-empty. Dashboard caps:
      - images <= 9
      - videos <= 3 (combined duration < 15s)
      - audios <= 3 (combined duration < 15s)
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required")
    _validate_https(webhook_url, field_name="webhook_url")
    _validate_common_video_params(
        duration_sec=duration_sec,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        fast_mode=fast_mode,
    )
    images = list(images or [])
    videos = list(videos or [])
    audios = list(audios or [])
    if not (images or videos or audios):
        raise ValueError(
            "multi_reference mode requires at least one images/videos/audios entry"
        )
    if len(images) > MAX_IMAGES:
        raise ValueError(f"images must be <= {MAX_IMAGES}; got {len(images)}")
    if len(videos) > MAX_VIDEOS:
        raise ValueError(f"videos must be <= {MAX_VIDEOS}; got {len(videos)}")
    if len(audios) > MAX_AUDIOS:
        raise ValueError(f"audios must be <= {MAX_AUDIOS}; got {len(audios)}")
    for u in images:
        _validate_https(u, field_name="images[*]")
    for u in videos:
        _validate_https(u, field_name="videos[*]")
    for u in audios:
        _validate_https(u, field_name="audios[*]")
    payload: dict[str, Any] = {
        "type": "image-to-video",
        "mode": "multi_reference",
        "prompt": prompt,
        "webhook_url": webhook_url,
        "duration": duration_sec,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "fast_mode": fast_mode,
    }
    if images:
        payload["images"] = images
    if videos:
        payload["videos"] = videos
    if audios:
        payload["audios"] = audios
    return payload


# --------------------------------------------------------------------------- #
# Response-shape helpers
# --------------------------------------------------------------------------- #


def _extract_request_id(resp: dict[str, Any]) -> str:
    """Pick the `requestId` out of a Seedance queue response.

    The dashboard contract is `{"success": true, "requestId": "..."}`
    but defensively also accepts `request_id` / `id` so a Kling-style
    shape doesn't crash this adapter.
    """
    for key in ("requestId", "request_id", "id"):
        v = resp.get(key)
        if isinstance(v, str) and v:
            return v
    raise ProviderError(
        "Enhancor /queue response carried no requestId",
        provider="enhancor_seedance",
        raw_response=resp,
    )


def _extract_status_string(resp: dict[str, Any]) -> Optional[str]:
    """Pick the free-form status string out of a Seedance /status
    response. Returns None if absent."""
    s = resp.get("status")
    if isinstance(s, str):
        return s
    return None


def _extract_result_url(resp: dict[str, Any]) -> Optional[str]:
    """Pick the terminal-success result URL. Seedance uses `result`."""
    v = resp.get("result")
    if isinstance(v, str) and v:
        return v
    return None


def _extract_thumbnail_url(resp: dict[str, Any]) -> Optional[str]:
    """Pick the optional thumbnail URL Seedance returns on terminal-success.

    Undocumented in the dashboard contract but observed in the
    2026-05-16 smoke run (see docs/enhancor_api_spec.md § A).
    """
    v = resp.get("thumbnail")
    if isinstance(v, str) and v:
        return v
    return None


def _extract_cost(resp: dict[str, Any]) -> Optional[float]:
    """Pick the provider-reported cost. Seedance returns an integer; we
    coerce to float so the dataclass typing stays consistent across
    providers that may emit floats."""
    v = resp.get("cost")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def _extract_error_message(resp: dict[str, Any]) -> Optional[str]:
    """Pick any error message out of a status response. Enhancor's exact
    field name on failure is UNKNOWN/NEEDS TEST per the spec; we look at
    every plausible key and return the first non-empty string."""
    for key in ("error", "message", "error_message", "failureReason", "reason"):
        v = resp.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


# --------------------------------------------------------------------------- #
# Provider
# --------------------------------------------------------------------------- #


class EnhancorSeedanceProvider(Provider):
    """Adapter for Seedance 2.0 Full Access."""

    name = "enhancor_seedance"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        session: Any = None,            # requests.Session-like; injected for tests
        request_timeout_sec: int = 60,
    ) -> None:
        if not api_key or not isinstance(api_key, str):
            raise ValueError("api_key is required (string)")
        # Never persist the key on a printable attribute. Stash it on a
        # private attribute and provide no getter / no __repr__ override
        # that would leak it.
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._request_timeout_sec = request_timeout_sec

    # ---- public payload builders (delegate to module-level pure fns) ----- #

    def build_text_to_video_payload(self, **kw: Any) -> dict[str, Any]:
        return build_text_to_video_payload(**kw)

    def build_ugc_payload(self, **kw: Any) -> dict[str, Any]:
        return build_ugc_payload(**kw)

    def build_multi_reference_payload(self, **kw: Any) -> dict[str, Any]:
        return build_multi_reference_payload(**kw)

    @staticmethod
    def classify_status(raw: Optional[str]) -> ProviderStatus:
        """Public wrapper around the base classifier — adapter-style
        accessor so callers can write `provider.classify_status(...)`."""
        return classify_provider_status(raw)

    # ---- HTTP plumbing --------------------------------------------------- #

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            API_KEY_HEADER: self._api_key,
        }

    def _post_json(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Single POST with JSON body. Returns (status_code, parsed_json).

        Lazy-imports `requests` so test paths that monkey-patch a fake
        session never need the real dependency.
        """
        url = f"{self._base_url}{path}"
        sess = self._session
        if sess is None:
            import requests  # noqa: PLC0415 - lazy
            sess = requests
        resp = sess.post(
            url,
            json=body,
            headers=self._headers(),
            timeout=self._request_timeout_sec,
        )
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {"_raw_text": getattr(resp, "text", "")[:4096]}
        log.debug(
            "enhancor_seedance %s -> %s (headers redacted=%s)",
            path,
            resp.status_code,
            redact_api_key_headers(dict(self._headers())),
        )
        return resp.status_code, data

    # ---- Provider protocol ---------------------------------------------- #

    def submit_job(self, request: ProviderJobRequest) -> ProviderJobResponse:
        """POST `request.payload` to `/queue`.

        Validates that `request.provider == self.name` and that the
        payload at least carries the fields the dashboard says are
        mandatory. Raises `ProviderError` on any non-2xx response or on
        a malformed response body.
        """
        if request.provider != self.name:
            raise ProviderError(
                f"request.provider must be {self.name!r}; got {request.provider!r}",
                provider=self.name,
                correlation_id=request.correlation_id,
            )
        # Defensive minimal payload check; the adapter-level builders
        # validate exhaustively, but submit_job is the public entry point
        # so a caller building a payload by hand still gets the floor.
        for required in ("type", "prompt", "webhook_url"):
            if required not in request.payload:
                raise ProviderError(
                    f"payload is missing required field {required!r}",
                    provider=self.name,
                    correlation_id=request.correlation_id,
                )
        try:
            status_code, body = self._post_json(QUEUE_PATH, request.payload)
        except Exception as e:
            raise ProviderError(
                f"POST {QUEUE_PATH} raised {type(e).__name__}: {e}",
                provider=self.name,
                correlation_id=request.correlation_id,
            ) from e
        if status_code < 200 or status_code >= 300:
            raise ProviderError(
                f"Enhancor /queue returned HTTP {status_code}",
                provider=self.name,
                http_status=status_code,
                raw_response=body,
                correlation_id=request.correlation_id,
            )
        if body.get("success") is False:
            raise ProviderError(
                "Enhancor /queue returned success=false",
                provider=self.name,
                http_status=status_code,
                raw_response=body,
                correlation_id=request.correlation_id,
            )
        provider_job_id = _extract_request_id(body)
        return ProviderJobResponse(
            provider=self.name,
            provider_job_id=provider_job_id,
            status=ProviderStatus.QUEUED,
            raw_request=dict(request.payload),
            raw_response=dict(body),
            submitted_at=datetime.now(timezone.utc),
            correlation_id=request.correlation_id,
        )

    def poll_status(
        self,
        provider_job_id: str,
        *,
        correlation_id: Optional[str] = None,
    ) -> ProviderJobStatus:
        """POST `{"requestId": ...}` to `/status` and parse the response.

        Returns a `ProviderJobStatus` with `result_url` / `thumbnail_url`
        / `cost` populated when the provider reports a terminal state.
        On `FAILED`, `error_message` is also populated.
        """
        if not provider_job_id:
            raise ProviderError(
                "provider_job_id is required to poll status",
                provider=self.name,
                correlation_id=correlation_id,
            )
        # The Enhancor `/status` endpoint expects `request_id` (snake_case)
        # in the body. The `/queue` response returns the id as `requestId`
        # (camelCase). We send BOTH keys so the wire body works whether
        # the API key contract is snake_case or camelCase — confirmed
        # against the live Seedance smoke (`scripts/enhancor_smoke_test.py`
        # line 488) and against a 400 `"request_id is required"` error
        # observed 2026-05-16T12:06:53Z when sending only camelCase.
        try:
            status_code, body = self._post_json(
                STATUS_PATH,
                {"request_id": provider_job_id, "requestId": provider_job_id},
            )
        except Exception as e:
            raise ProviderError(
                f"POST {STATUS_PATH} raised {type(e).__name__}: {e}",
                provider=self.name,
                correlation_id=correlation_id,
            ) from e
        if status_code < 200 or status_code >= 300:
            raise ProviderError(
                f"Enhancor /status returned HTTP {status_code}",
                provider=self.name,
                http_status=status_code,
                raw_response=body,
                correlation_id=correlation_id,
            )
        raw_status = _extract_status_string(body)
        status = classify_provider_status(raw_status)
        return ProviderJobStatus(
            provider=self.name,
            provider_job_id=provider_job_id,
            status=status,
            raw_status_response=dict(body),
            polled_at=datetime.now(timezone.utc),
            result_url=_extract_result_url(body),
            thumbnail_url=_extract_thumbnail_url(body),
            cost=_extract_cost(body),
            error_message=_extract_error_message(body) if status == ProviderStatus.FAILED else None,
            correlation_id=correlation_id,
        )

    def download_result(self, result_url: str, dest_path: Path) -> Path:
        """Stream the terminal-success media to `dest_path`.

        Accepts any HTTPS host — Seedance currently emits CloudFront URLs
        but the contract is "wherever the provider tells us"; the Audio
        Fixer (separate provider) emits `v3b.fal.media`. Callers MUST
        mirror to their own storage before linking from operator surfaces
        because the result URL has an unknown TTL.
        """
        _validate_https(result_url, field_name="result_url")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        sess = self._session
        if sess is None:
            import requests  # noqa: PLC0415 - lazy
            sess = requests
        try:
            with sess.get(result_url, stream=True, timeout=self._request_timeout_sec) as r:
                if r.status_code < 200 or r.status_code >= 300:
                    raise ProviderError(
                        f"download_result GET returned HTTP {r.status_code}",
                        provider=self.name,
                        http_status=r.status_code,
                    )
                with dest_path.open("wb") as f:
                    for chunk in r.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            f.write(chunk)
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(
                f"download_result raised {type(e).__name__}: {e}",
                provider=self.name,
            ) from e
        return dest_path

    # ---- Optional convenience helper ------------------------------------ #

    def wait_for_completion(
        self,
        provider_job_id: str,
        *,
        correlation_id: Optional[str] = None,
        poll_interval_sec: int = DEFAULT_POLL_INTERVAL_SEC,
        timeout_sec: int = DEFAULT_POLL_TIMEOUT_SEC,
    ) -> ProviderGenerationResult:
        """Block until the job lands in a terminal state.

        Loops over `poll_status()` until `COMPLETED` (returns a
        `ProviderGenerationResult`) or `FAILED` (raises `ProviderError`)
        or `timeout_sec` elapses (raises `ProviderError`).

        `UNKNOWN` is treated as in-flight so the loop continues — the
        operator can `Ctrl+C` if they think the job is stuck.
        """
        deadline = time.time() + timeout_sec
        last_status: Optional[ProviderJobStatus] = None
        while True:
            last_status = self.poll_status(
                provider_job_id, correlation_id=correlation_id,
            )
            if last_status.status == ProviderStatus.COMPLETED:
                if not last_status.result_url:
                    raise ProviderError(
                        "Enhancor reported COMPLETED but no result URL",
                        provider=self.name,
                        raw_response=last_status.raw_status_response,
                        correlation_id=correlation_id,
                    )
                return ProviderGenerationResult(
                    provider=self.name,
                    provider_job_id=provider_job_id,
                    result_url=last_status.result_url,
                    thumbnail_url=last_status.thumbnail_url,
                    cost=last_status.cost,
                    raw_completed_response=last_status.raw_status_response,
                    correlation_id=correlation_id,
                )
            if last_status.status == ProviderStatus.FAILED:
                raise ProviderError(
                    last_status.error_message or "Enhancor reported FAILED",
                    provider=self.name,
                    raw_response=last_status.raw_status_response,
                    correlation_id=correlation_id,
                )
            if time.time() >= deadline:
                raise ProviderError(
                    f"wait_for_completion timed out after {timeout_sec}s; "
                    f"last status={last_status.status}",
                    provider=self.name,
                    raw_response=last_status.raw_status_response if last_status else None,
                    correlation_id=correlation_id,
                )
            time.sleep(poll_interval_sec)

    # ---- Dataclass round-trip helpers ----------------------------------- #

    @staticmethod
    def serialise_status(status: ProviderJobStatus) -> dict[str, Any]:
        """Turn a `ProviderJobStatus` into a JSON-friendly dict (used by
        downstream tooling that persists poll snapshots to disk).

        We bypass `dataclasses.asdict` here for the `polled_at`
        datetime; otherwise `asdict` would emit a non-serialisable
        `datetime` object inside the dict.
        """
        d = asdict(status)
        d["polled_at"] = status.polled_at.isoformat()
        d["status"] = status.status.value
        return d

    @staticmethod
    def with_correlation_id(
        request: ProviderJobRequest, correlation_id: str,
    ) -> ProviderJobRequest:
        """Return a new ProviderJobRequest with `correlation_id` set.
        Frozen-dataclass-friendly mutator."""
        return replace(request, correlation_id=correlation_id)


# --------------------------------------------------------------------------- #
# Module re-exports (kept narrow on purpose)
# --------------------------------------------------------------------------- #


__all__ = [
    "ALLOWED_ASPECT_RATIOS",
    "ALLOWED_DURATIONS_SEC",
    "ALLOWED_RESOLUTIONS",
    "API_KEY_HEADER",
    "DEFAULT_ASPECT_RATIO",
    "DEFAULT_BASE_URL",
    "DEFAULT_DURATION_SEC",
    "DEFAULT_FAST_MODE",
    "DEFAULT_RESOLUTION",
    "EnhancorSeedanceProvider",
    "QUEUE_PATH",
    "STATUS_PATH",
    "UGC_TOTAL_ASSET_CAP",
    "build_multi_reference_payload",
    "build_text_to_video_payload",
    "build_ugc_payload",
]


# Keep `json` import used (it shows up if a caller wraps the adapter
# and wants to pretty-print payloads via the adapter module).
_ = json
