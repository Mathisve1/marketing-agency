"""Enhancor UGC Audio Fixer provider.

Wraps the Audio Fixer API at:

    https://apireq.enhancor.ai/api/fix-audio/v1

Endpoints:
    POST /queue        - submit an audio-repair job
    POST /status       - poll for status / result

Auth:
    header `x-api-key: <ENHANCOR_API_KEY>`

The Audio Fixer accepts the Seedance terminal-success `result` URL
directly (confirmed by the 2026-05-16 smoke run); the typical chained
flow is:

    seedance_result = seedance.wait_for_completion(...)
    fixer_resp = audio_fixer.submit_audio_fix(
        input_video_url=seedance_result.result_url,
        webhook_url=webhook_url,
    )
    fixer_result = audio_fixer.wait_for_completion(fixer_resp.provider_job_id)
    audio_fixer.download_result(fixer_result.result_url, dest_path)

The downloaded artefact is a single fully-muxed MP4 (video + audio,
audio repaired). The smoke run observed the result hosted on
`v3b.fal.media`; the adapter accepts any HTTPS host on the result URL.
"""
from __future__ import annotations

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

DEFAULT_BASE_URL = "https://apireq.enhancor.ai/api/fix-audio/v1"
QUEUE_PATH = "/queue"
STATUS_PATH = "/status"

API_KEY_HEADER = "x-api-key"

# The Audio Fixer takes longer per-second than the cheapest Seedance
# probe (smoke run showed cost=561 for ~4s of input vs Seedance t2v
# cost=264 for the same duration). Tune polling defaults accordingly.
DEFAULT_POLL_INTERVAL_SEC = 10
DEFAULT_POLL_TIMEOUT_SEC = 10 * 60


# --------------------------------------------------------------------------- #
# Payload builder (pure / tested)
# --------------------------------------------------------------------------- #


def _validate_https(url: str, *, field_name: str) -> None:
    """Reject anything that isn't an HTTPS URL.

    The Audio Fixer pulls `inputVideo` over the wire from whatever URL
    we send it — the public CloudFront URL Seedance returned works, but
    we reject http:// pre-flight so we don't leak a non-HTTPS link.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"{field_name} is required and must be a non-empty string")
    if not url.strip().lower().startswith("https://"):
        raise ValueError(f"{field_name} must be an https:// URL; got {url!r}")


def build_audio_fixer_payload(
    *,
    input_video_url: str,
    webhook_url: str,
) -> dict[str, Any]:
    """The /queue payload for the Audio Fixer.

    Exactly two required fields per `docs/enhancor_api_spec.md § B`:
    `inputVideo` (URL of the raw mp4 to repair) and `webhook_url`
    (mandatory on every Enhancor submission). The builder rejects any
    extra/unknown field implicitly by returning a fixed-shape dict; the
    Audio Fixer dashboard does not document additional knobs today.
    """
    _validate_https(input_video_url, field_name="input_video_url")
    _validate_https(webhook_url, field_name="webhook_url")
    return {
        "inputVideo": input_video_url,
        "webhook_url": webhook_url,
    }


# --------------------------------------------------------------------------- #
# Response-shape helpers
# --------------------------------------------------------------------------- #


def _extract_request_id(resp: dict[str, Any]) -> str:
    for key in ("requestId", "request_id", "id"):
        v = resp.get(key)
        if isinstance(v, str) and v:
            return v
    raise ProviderError(
        "Enhancor Audio Fixer /queue response carried no requestId",
        provider="enhancor_audio_fixer",
        raw_response=resp,
    )


def _extract_status_string(resp: dict[str, Any]) -> Optional[str]:
    s = resp.get("status")
    if isinstance(s, str):
        return s
    return None


def _extract_result_url(resp: dict[str, Any]) -> Optional[str]:
    v = resp.get("result")
    if isinstance(v, str) and v:
        return v
    return None


def _extract_cost(resp: dict[str, Any]) -> Optional[float]:
    v = resp.get("cost")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def _extract_error_message(resp: dict[str, Any]) -> Optional[str]:
    for key in ("error", "message", "error_message", "failureReason", "reason"):
        v = resp.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


# --------------------------------------------------------------------------- #
# Provider
# --------------------------------------------------------------------------- #


class EnhancorAudioFixerProvider(Provider):
    """Adapter for the UGC Audio Fixer endpoint."""

    name = "enhancor_audio_fixer"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        session: Any = None,
        request_timeout_sec: int = 60,
    ) -> None:
        if not api_key or not isinstance(api_key, str):
            raise ValueError("api_key is required (string)")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._request_timeout_sec = request_timeout_sec

    # ---- public payload builder (delegates) ------------------------------ #

    def build_payload(self, *, input_video_url: str, webhook_url: str) -> dict[str, Any]:
        return build_audio_fixer_payload(
            input_video_url=input_video_url, webhook_url=webhook_url,
        )

    @staticmethod
    def classify_status(raw: Optional[str]) -> ProviderStatus:
        return classify_provider_status(raw)

    # ---- HTTP plumbing --------------------------------------------------- #

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            API_KEY_HEADER: self._api_key,
        }

    def _post_json(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
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
            "enhancor_audio_fixer %s -> %s (headers redacted=%s)",
            path,
            resp.status_code,
            redact_api_key_headers(dict(self._headers())),
        )
        return resp.status_code, data

    # ---- Provider protocol ---------------------------------------------- #

    def submit_job(self, request: ProviderJobRequest) -> ProviderJobResponse:
        """POST `request.payload` to `/queue`.

        Validates that `request.provider == self.name` and that the
        payload carries the two mandatory fields.
        """
        if request.provider != self.name:
            raise ProviderError(
                f"request.provider must be {self.name!r}; got {request.provider!r}",
                provider=self.name,
                correlation_id=request.correlation_id,
            )
        for required in ("inputVideo", "webhook_url"):
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
                f"Enhancor Audio Fixer /queue returned HTTP {status_code}",
                provider=self.name,
                http_status=status_code,
                raw_response=body,
                correlation_id=request.correlation_id,
            )
        if body.get("success") is False:
            raise ProviderError(
                "Enhancor Audio Fixer /queue returned success=false",
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

    def submit_audio_fix(
        self,
        *,
        input_video_url: str,
        webhook_url: str,
        correlation_id: Optional[str] = None,
    ) -> ProviderJobResponse:
        """Higher-level entry point: build the payload + submit in one call.

        Caller provides the public HTTPS URL of the raw mp4 to repair and
        a webhook URL. The Audio Fixer accepts the Seedance terminal-
        success `result` URL directly — the smoke confirmed this — so
        the typical chained flow is one Seedance success -> one Audio
        Fixer submit with that exact URL.
        """
        payload = build_audio_fixer_payload(
            input_video_url=input_video_url, webhook_url=webhook_url,
        )
        request = ProviderJobRequest(
            provider=self.name,
            job_type="audio-fix",
            payload=payload,
            correlation_id=correlation_id,
        )
        return self.submit_job(request)

    def poll_status(
        self,
        provider_job_id: str,
        *,
        correlation_id: Optional[str] = None,
    ) -> ProviderJobStatus:
        """One status poll. Same shape as the Seedance provider."""
        if not provider_job_id:
            raise ProviderError(
                "provider_job_id is required to poll status",
                provider=self.name,
                correlation_id=correlation_id,
            )
        # Send BOTH `request_id` and `requestId` — same defensive
        # pattern as the Seedance provider (see its poll_status() for
        # the full rationale: the live `/status` endpoint expects
        # snake_case while the `/queue` response key is camelCase).
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
                f"Enhancor Audio Fixer /status returned HTTP {status_code}",
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
            thumbnail_url=None,        # Audio Fixer doesn't return a thumbnail
            cost=_extract_cost(body),
            error_message=_extract_error_message(body) if status == ProviderStatus.FAILED else None,
            correlation_id=correlation_id,
        )

    def download_result(self, result_url: str, dest_path: Path) -> Path:
        """Stream the muxed mp4 to disk. Accepts any HTTPS host."""
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
        """Block until the job lands in a terminal state."""
        deadline = time.time() + timeout_sec
        last_status: Optional[ProviderJobStatus] = None
        while True:
            last_status = self.poll_status(
                provider_job_id, correlation_id=correlation_id,
            )
            if last_status.status == ProviderStatus.COMPLETED:
                if not last_status.result_url:
                    raise ProviderError(
                        "Enhancor Audio Fixer reported COMPLETED but no result URL",
                        provider=self.name,
                        raw_response=last_status.raw_status_response,
                        correlation_id=correlation_id,
                    )
                return ProviderGenerationResult(
                    provider=self.name,
                    provider_job_id=provider_job_id,
                    result_url=last_status.result_url,
                    thumbnail_url=None,
                    cost=last_status.cost,
                    raw_completed_response=last_status.raw_status_response,
                    correlation_id=correlation_id,
                )
            if last_status.status == ProviderStatus.FAILED:
                raise ProviderError(
                    last_status.error_message or "Enhancor Audio Fixer reported FAILED",
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
        d = asdict(status)
        d["polled_at"] = status.polled_at.isoformat()
        d["status"] = status.status.value
        return d

    @staticmethod
    def with_correlation_id(
        request: ProviderJobRequest, correlation_id: str,
    ) -> ProviderJobRequest:
        return replace(request, correlation_id=correlation_id)


__all__ = [
    "API_KEY_HEADER",
    "DEFAULT_BASE_URL",
    "EnhancorAudioFixerProvider",
    "QUEUE_PATH",
    "STATUS_PATH",
    "build_audio_fixer_payload",
]
