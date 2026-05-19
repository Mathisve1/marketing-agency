"""Provider-agnostic base layer for video-generation adapters.

This module owns the dataclasses, the status enum, the protocol every
concrete provider implements, and a small set of helpers (status-string
classifier, header redaction). It does NOT make HTTP calls and does NOT
depend on any concrete provider — concrete providers depend on it.

Design discipline
-----------------

1. **Status discipline.** Providers map their free-form status strings
   into a single 5-value enum (`ProviderStatus`). Anything we cannot
   confidently map lands in `UNKNOWN`; we never silently coerce an
   unknown string into `IN_PROGRESS` (which would risk a poll loop on a
   permanently failed job).

2. **Raw-payload preservation.** Every response dataclass carries the
   provider's raw JSON in `raw_request` / `raw_response` /
   `raw_status_response` / `raw_completed_response`. Adapters MUST NOT
   strip undocumented fields; the discovery loop (e.g. the undocumented
   `thumbnail` field Seedance returns on terminal-success) depends on
   raw preservation.

3. **No API key leakage.** `redact_api_key_headers` is the only public
   helper for header dumps; never log the raw key. Adapters do not
   override `__repr__` to leak the key either — the dataclasses below
   carry no key field, the key lives on the adapter instance.

4. **No CDN assumption.** Result / thumbnail URLs are typed as plain
   `str`; we never validate the host portion. Seedance currently emits
   CloudFront URLs but the Audio Fixer emits `v3b.fal.media`; future
   providers will emit other hosts. The mirror-to-Supabase step (out of
   scope here) handles host normalisation.

5. **No Supabase / no dashboard.** This module is wire-protocol only.
   The dashboard, the cost ledger, and the operator surfaces live one
   layer up and consume the dataclasses below.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

# --------------------------------------------------------------------------- #
# Status enum + classifier
# --------------------------------------------------------------------------- #


class ProviderStatus(str, enum.Enum):
    """The provider-agnostic status surface every adapter maps onto.

    `str` mixin so the value is JSON-serialisable as the literal short
    code (`"QUEUED"` etc.) without needing custom encoders. Comparison
    against the string forms is also valid:

        >>> ProviderStatus.QUEUED == "QUEUED"
        True
    """

    QUEUED = "QUEUED"
    """The job has been accepted by the provider but is waiting to start
    execution. Maps from Enhancor `PENDING` / `IN_QUEUE`, fal.ai
    `IN_QUEUE`, generic `queued`."""

    IN_PROGRESS = "IN_PROGRESS"
    """The job is actively running. Maps from Enhancor `IN_PROGRESS`,
    fal.ai `IN_PROGRESS`, generic `processing` / `running`."""

    COMPLETED = "COMPLETED"
    """Terminal success. Maps from Enhancor `COMPLETED` and generic
    `success` / `complete` / `done`."""

    FAILED = "FAILED"
    """Terminal failure. Maps from `FAILED` / `FAILURE` / `ERROR` /
    any string containing `error` or `fail`."""

    UNKNOWN = "UNKNOWN"
    """The provider returned a string we do not yet recognise, or no
    status at all. Callers MUST treat this as in-flight (keep polling
    or surface to operator) — never assume terminal success on
    `UNKNOWN`."""


# Token table for the classifier. Order matters: terminal states are
# checked before in-flight states so a string like `complete_with_warnings`
# resolves to `COMPLETED` rather than `UNKNOWN`. The classifier is case-
# insensitive and substring-based, never exact-match — providers ship
# variants (`Complete`, `complete`, `Completed`, `task_complete`) and we
# want one rule to absorb them all.
_TERMINAL_SUCCESS_TOKENS = ("complet", "success", "done", "succeeded")
_TERMINAL_FAILURE_TOKENS = ("fail", "error", "rejected", "cancelled", "canceled")
_QUEUED_TOKENS = ("queue", "pending", "waiting", "scheduled")
_IN_PROGRESS_TOKENS = ("progress", "running", "processing", "active", "started")


def classify_provider_status(raw: Optional[str]) -> ProviderStatus:
    """Map a free-form provider status string onto `ProviderStatus`.

    Token-based and case-insensitive. The terminal states (success +
    failure) are tested before the non-terminal ones so a string like
    `"completed"` cannot be misread as `"in_progress"` just because
    `"in"` is a substring of `"running"`. Empty / None / unrecognised
    strings return `UNKNOWN`; callers MUST treat that as "keep polling"
    or "ask operator", never as terminal success.

    Examples:
        >>> classify_provider_status("PENDING")
        <ProviderStatus.QUEUED: 'QUEUED'>
        >>> classify_provider_status("IN_PROGRESS")
        <ProviderStatus.IN_PROGRESS: 'IN_PROGRESS'>
        >>> classify_provider_status("COMPLETED")
        <ProviderStatus.COMPLETED: 'COMPLETED'>
        >>> classify_provider_status("Failed")
        <ProviderStatus.FAILED: 'FAILED'>
        >>> classify_provider_status("")
        <ProviderStatus.UNKNOWN: 'UNKNOWN'>
        >>> classify_provider_status(None)
        <ProviderStatus.UNKNOWN: 'UNKNOWN'>
        >>> classify_provider_status("WEIRD_PROVIDER_VALUE")
        <ProviderStatus.UNKNOWN: 'UNKNOWN'>
    """
    if not raw or not isinstance(raw, str):
        return ProviderStatus.UNKNOWN
    norm = raw.strip().lower()
    if not norm:
        return ProviderStatus.UNKNOWN
    for token in _TERMINAL_SUCCESS_TOKENS:
        if token in norm:
            return ProviderStatus.COMPLETED
    for token in _TERMINAL_FAILURE_TOKENS:
        if token in norm:
            return ProviderStatus.FAILED
    for token in _QUEUED_TOKENS:
        if token in norm:
            return ProviderStatus.QUEUED
    for token in _IN_PROGRESS_TOKENS:
        if token in norm:
            return ProviderStatus.IN_PROGRESS
    return ProviderStatus.UNKNOWN


# --------------------------------------------------------------------------- #
# Header redaction
# --------------------------------------------------------------------------- #


def redact_api_key_headers(
    headers: dict[str, str],
    *,
    api_key_header_names: tuple[str, ...] = (
        "x-api-key",
        "authorization",
        "api-key",
    ),
) -> dict[str, str]:
    """Return a copy of `headers` with any API-key-bearing header value
    replaced by `"***redacted***"`. Case-insensitive on header name.

    Used by every adapter before persisting / logging headers. Callers
    should never persist or log the raw output of `requests.Session.headers`
    directly.
    """
    redacted_set = {n.lower() for n in api_key_header_names}
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in redacted_set:
            out[k] = "***redacted***"
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------- #
# Media-asset descriptor
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProviderMediaAsset:
    """One image / video / audio reference the caller wants to feed to a
    provider call. The adapter is responsible for placing it into the
    correct field (e.g. `products[]` vs `influencers[]` vs `images[]`).

    Held by `ProviderJobRequest.assets`. Never carries inline base64 —
    Enhancor requires public HTTPS URLs and we mirror that as a hard
    constraint.
    """
    url: str
    """Public HTTPS URL of the asset. The adapter validates the scheme;
    callers may pass anything — the adapter raises `ProviderError` on
    a non-HTTPS URL rather than the provider returning a generic 400."""

    kind: str
    """One of `"image"`, `"video"`, `"audio"`. The adapter dispatches on
    this when building the provider-specific payload."""

    role: Optional[str] = None
    """Optional adapter-specific role tag — e.g. `"product"`,
    `"influencer"`, `"first_frame"`, `"motion_reference"`. The adapter
    interprets the role per its own field surface. When None, the
    adapter falls back to its default placement for the `kind`."""

    duration_sec: Optional[float] = None
    """Optional duration hint for video / audio assets. Lets adapters
    pre-validate against the per-array `combined_duration < 15s`
    constraints (Seedance) before submitting."""

    content_type: Optional[str] = None
    """Optional MIME type hint. Adapters MAY use this for pre-flight
    validation; they do not require it (the provider validates the
    actual fetched content)."""


# --------------------------------------------------------------------------- #
# Request / Response / Status / Result / Error
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProviderJobRequest:
    """A submission a caller hands to a provider's `submit_job()`.

    The adapter turns this into the provider-specific wire body. The
    adapter is also responsible for validating the request (URL scheme,
    array length caps, mode-specific required fields) BEFORE the HTTP
    call.

    `payload` holds the already-built wire body when the caller used an
    adapter-specific payload builder (e.g. `EnhancorSeedanceProvider.
    build_ugc_payload()`). In that case the adapter sends `payload` as-is
    and does not re-derive it from `assets`. This is the common path
    today; the `assets` / `job_type` fields are reserved for future
    higher-level callers that don't know each provider's body shape.
    """
    provider: str
    """Stable short id for the provider (`"enhancor_seedance"`,
    `"enhancor_audio_fixer"`). Becomes the `provider` column on any
    downstream job-tracking table."""

    job_type: str
    """Adapter-specific job kind: `"text-to-video"`, `"ugc"`,
    `"multi-reference"`, `"audio-fix"`. The adapter validates that
    `payload` matches this `job_type`."""

    payload: dict[str, Any]
    """The exact body the adapter sends to the provider's `/queue`
    endpoint. Built by the adapter's payload-builder helpers."""

    correlation_id: Optional[str] = None
    """Caller-side reference (e.g. a Pai Route 01 sprint id, a Supabase
    row id). The adapter does not interpret this — it just round-trips
    it onto every downstream dataclass so callers can stitch logs back
    together."""

    assets: tuple[ProviderMediaAsset, ...] = ()
    """Optional structured asset list. Most adapters today read directly
    from `payload`; this field is informational, mirroring what's in
    `payload`."""

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ProviderJobResponse:
    """What `submit_job()` returns: the provider's accepted-job
    handshake.

    `provider_job_id` is the opaque id the provider hands back (Enhancor
    calls it `requestId`; fal.ai calls it `request_id`; Kling calls it
    `task_id`). The base layer normalises the name; the raw shape lives
    on `raw_response`.
    """
    provider: str
    provider_job_id: str
    status: ProviderStatus
    raw_request: dict[str, Any]
    raw_response: dict[str, Any]
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: Optional[str] = None


@dataclass(frozen=True)
class ProviderJobStatus:
    """One poll cycle. Adapters return this from `poll_status()` on every
    call. Terminal states carry `result_url` (and possibly
    `thumbnail_url` / `cost`); in-flight states leave those as None.

    `error_message` is populated only when `status == FAILED`.
    """
    provider: str
    provider_job_id: str
    status: ProviderStatus
    raw_status_response: dict[str, Any]
    polled_at: datetime
    result_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    cost: Optional[float] = None
    error_message: Optional[str] = None
    correlation_id: Optional[str] = None


@dataclass(frozen=True)
class ProviderGenerationResult:
    """Terminal-success bundle. A caller observing `ProviderJobStatus
    .status == COMPLETED` can synthesise one of these for downstream
    consumers (mirror-to-Supabase, watermark, embed-on-microsite).

    Distinct from `ProviderJobStatus` because the result is the
    permanent record of the successful generation — `ProviderJobStatus`
    is one observation in a poll loop and can be re-issued on every
    cycle.
    """
    provider: str
    provider_job_id: str
    result_url: str
    """The provider's terminal-success media URL. Hosted on whatever
    CDN the provider chose; callers MUST mirror to their own storage
    before linking from any operator-facing surface (the URL has an
    unknown TTL)."""

    thumbnail_url: Optional[str] = None
    """Optional poster-frame URL the provider returns on terminal-success.
    Observed on the Seedance `/status` payload as the undocumented
    `thumbnail` field."""

    cost: Optional[float] = None
    """Provider-reported cost. Units are provider-specific (Enhancor
    reports an integer whose unit is currently UNKNOWN/NEEDS TEST per
    docs/enhancor_api_spec.md). Callers SHOULD store both `cost` and
    the provider id so the unit can be reconciled later."""

    raw_completed_response: Optional[dict[str, Any]] = None
    """The full terminal-success payload, preserved verbatim. Lets us
    discover undocumented fields without re-running paid jobs."""

    correlation_id: Optional[str] = None
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ProviderError(Exception):
    """Raised by every adapter on a non-recoverable provider error.

    Carries structured fields so callers can log + decide retry policy
    without re-parsing strings. The exception is also the canonical
    type adapters raise from `submit_job` / `poll_status` /
    `download_result` — callers catch `ProviderError` once.

    Designed for safe logging: `__str__` never includes the API key
    (the constructor accepts a `raw_response` dict, but the adapter is
    expected to redact headers before passing it in via
    `redact_api_key_headers()`).
    """
    def __init__(
        self,
        message: str,
        *,
        provider: str,
        code: Optional[str] = None,
        http_status: Optional[int] = None,
        raw_response: Optional[dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.code = code
        self.http_status = http_status
        self.raw_response = raw_response
        self.correlation_id = correlation_id

    def __repr__(self) -> str:
        return (
            f"ProviderError(provider={self.provider!r}, code={self.code!r}, "
            f"http_status={self.http_status!r}, message={self.message!r})"
        )


# --------------------------------------------------------------------------- #
# Provider protocol
# --------------------------------------------------------------------------- #


@runtime_checkable
class Provider(Protocol):
    """The minimum surface every adapter implements.

    Methods below describe the contract; concrete adapters may add
    builder helpers (e.g. `build_ugc_payload`) and additional polling
    conveniences (e.g. `wait_for_completion`). The protocol is the
    floor, not the ceiling.
    """
    name: str
    """Stable short id, e.g. `"enhancor_seedance"`. Used in audit logs
    and on every dataclass."""

    def submit_job(self, request: ProviderJobRequest) -> ProviderJobResponse:
        """POST the request to the provider's queue endpoint.

        Adapters MUST validate the request before the HTTP call and
        raise `ProviderError` on a validation failure (no quota burnt).
        """
        ...

    def poll_status(
        self,
        provider_job_id: str,
        *,
        correlation_id: Optional[str] = None,
    ) -> ProviderJobStatus:
        """One status poll. Returns the structured status snapshot.

        Adapters MUST NOT loop here — looping policy is the caller's
        decision. Adapters MAY expose a separate `wait_for_completion`
        helper that loops on top of `poll_status`.
        """
        ...

    def download_result(self, result_url: str, dest_path: Path) -> Path:
        """Stream the terminal-success media to disk.

        Adapters MUST NOT assume a specific CDN host. The result URL
        is whatever the provider returned in its terminal-success
        payload; the streaming HTTP client must follow redirects and
        accept any HTTPS host.
        """
        ...
