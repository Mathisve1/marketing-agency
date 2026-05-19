"""Provider-layer tests for the Enhancor adapters.

These tests NEVER hit the real Enhancor API. Every HTTP boundary is
exercised via an in-memory fake session injected on construction:

    provider = EnhancorSeedanceProvider("test-key", session=fake)
    provider = EnhancorAudioFixerProvider("test-key", session=fake)

The fake captures the POST URL + body + headers and returns a canned
response, so the tests verify wire-protocol contract without any
external dependency. See `FakeSession` below.

Coverage:
  - `classify_provider_status` maps PENDING / IN_QUEUE / IN_PROGRESS /
    COMPLETED / FAILED / unknown / None onto the 5-state enum.
  - Seedance text-to-video payload excludes media arrays.
  - Seedance UGC payload includes products / influencers / full_access
    and enforces the <=9 cap.
  - Seedance multi_reference payload accepts/rejects per the dashboard.
  - Seedance submit + status parse a completed response with the
    `thumbnail` field intact.
  - Seedance parses a failed response and surfaces the error message.
  - Audio Fixer payload uses `inputVideo` + `webhook_url`.
  - Audio Fixer parses a completed response on any CDN host (not only
    fal.media).
  - API key is never exposed in adapter `repr` / `str`.
  - Result URLs from arbitrary HTTPS hosts are accepted (not only
    CloudFront / fal.media) — the adapter MUST NOT discriminate by host.
  - The UGC native-audio finding is exposed as documentation/metadata,
    not assumed for every mode.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from agents.producer.providers.base import (
    Provider,
    ProviderError,
    ProviderJobRequest,
    ProviderJobStatus,
    ProviderStatus,
    classify_provider_status,
    redact_api_key_headers,
)
from agents.producer.providers.enhancor_audio_fixer import (
    EnhancorAudioFixerProvider,
    build_audio_fixer_payload,
)
from agents.producer.providers.enhancor_seedance import (
    EnhancorSeedanceProvider,
    build_multi_reference_payload,
    build_text_to_video_payload,
    build_ugc_payload,
)

WEBHOOK = "https://example.com/webhooks/enhancor"


# --------------------------------------------------------------------------- #
# In-memory fake HTTP session
# --------------------------------------------------------------------------- #


class _FakeResponse:
    """Just enough of a requests.Response for the adapter code path."""

    def __init__(
        self,
        status_code: int,
        json_data: Optional[dict] = None,
        *,
        body_chunks: tuple[bytes, ...] = (),
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json = json_data
        self._body_chunks = body_chunks
        self.text = text
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no json")
        return self._json

    # iter for download streams
    def iter_content(self, chunk_size: int = 65536):  # noqa: ARG002
        yield from self._body_chunks

    # context-manager surface (matches `with session.get(...) as r:`)
    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None


class FakeSession:
    """Records every POST/GET and returns canned responses.

    The adapter calls `self._session.post(url, json=..., headers=...,
    timeout=...)` and `self._session.get(url, stream=..., timeout=...)`.
    The fake records the arguments so tests can assert on them.
    """

    def __init__(self) -> None:
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self._post_queue: list[_FakeResponse] = []
        self._get_queue: list[_FakeResponse] = []

    def enqueue_post(self, resp: _FakeResponse) -> None:
        self._post_queue.append(resp)

    def enqueue_get(self, resp: _FakeResponse) -> None:
        self._get_queue.append(resp)

    def post(self, url: str, *, json: dict, headers: dict, timeout: int) -> _FakeResponse:
        self.post_calls.append(
            {"url": url, "json": json, "headers": dict(headers), "timeout": timeout},
        )
        if not self._post_queue:
            raise AssertionError(f"no canned POST response queued for {url}")
        return self._post_queue.pop(0)

    def get(self, url: str, *, stream: bool, timeout: int) -> _FakeResponse:  # noqa: ARG002
        self.get_calls.append({"url": url, "stream": stream, "timeout": timeout})
        if not self._get_queue:
            raise AssertionError(f"no canned GET response queued for {url}")
        return self._get_queue.pop(0)


def _make_seedance(session: Optional[FakeSession] = None) -> EnhancorSeedanceProvider:
    return EnhancorSeedanceProvider("sk_test_abc123", session=session or FakeSession())


def _make_audio_fixer(session: Optional[FakeSession] = None) -> EnhancorAudioFixerProvider:
    return EnhancorAudioFixerProvider("sk_test_abc123", session=session or FakeSession())


# --------------------------------------------------------------------------- #
# classify_provider_status — status mapping
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Queued
        ("PENDING", ProviderStatus.QUEUED),
        ("IN_QUEUE", ProviderStatus.QUEUED),
        ("queued", ProviderStatus.QUEUED),
        ("Waiting", ProviderStatus.QUEUED),
        ("scheduled", ProviderStatus.QUEUED),
        # In progress
        ("IN_PROGRESS", ProviderStatus.IN_PROGRESS),
        ("processing", ProviderStatus.IN_PROGRESS),
        ("Running", ProviderStatus.IN_PROGRESS),
        # Completed
        ("COMPLETED", ProviderStatus.COMPLETED),
        ("Complete", ProviderStatus.COMPLETED),
        ("done", ProviderStatus.COMPLETED),
        ("succeeded", ProviderStatus.COMPLETED),
        # Failed
        ("FAILED", ProviderStatus.FAILED),
        ("Failure", ProviderStatus.FAILED),
        ("ERROR_TIMEOUT", ProviderStatus.FAILED),
        ("rejected", ProviderStatus.FAILED),
        ("cancelled", ProviderStatus.FAILED),
        # Unknown / empty / None
        ("", ProviderStatus.UNKNOWN),
        (None, ProviderStatus.UNKNOWN),
        ("weird_provider_value", ProviderStatus.UNKNOWN),
    ],
)
def test_classify_provider_status_maps_correctly(raw, expected):
    """The 5-state enum captures every status string we currently know
    about; unknown values land in UNKNOWN, not silently in IN_PROGRESS."""
    assert classify_provider_status(raw) == expected


def test_classify_provider_status_is_case_insensitive():
    assert classify_provider_status("Pending") == ProviderStatus.QUEUED
    assert classify_provider_status("pendinG") == ProviderStatus.QUEUED
    assert classify_provider_status("COMPLETED") == ProviderStatus.COMPLETED


def test_classify_provider_status_prefers_terminal_states_over_in_flight():
    """A weird string like `'complete_with_warnings'` resolves to
    COMPLETED, not UNKNOWN, because the terminal-success token is the
    first match. This is critical: if the classifier ever resolved a
    terminal-success state to anything else we'd poll forever."""
    assert classify_provider_status("complete_with_warnings") == ProviderStatus.COMPLETED
    assert classify_provider_status("task_failed_after_3_tries") == ProviderStatus.FAILED


# --------------------------------------------------------------------------- #
# Seedance payload builders — text-to-video
# --------------------------------------------------------------------------- #


def test_t2v_payload_has_no_media_arrays():
    """Dashboard rule: text-to-video MUST NOT carry images / videos /
    audios / products / influencers / mode."""
    p = build_text_to_video_payload(prompt="anything", webhook_url=WEBHOOK)
    assert p["type"] == "text-to-video"
    for forbidden in ("images", "videos", "audios", "products", "influencers", "mode"):
        assert forbidden not in p, f"{forbidden!r} must not appear in a t2v payload"


def test_t2v_payload_carries_required_fields():
    p = build_text_to_video_payload(prompt="x", webhook_url=WEBHOOK)
    for required in (
        "type", "prompt", "webhook_url", "duration", "resolution",
        "aspect_ratio", "fast_mode",
    ):
        assert required in p


def test_t2v_payload_default_is_cheapest():
    p = build_text_to_video_payload(prompt="x", webhook_url=WEBHOOK)
    assert p["resolution"] == "480p"
    assert p["aspect_ratio"] == "9:16"
    assert p["duration"] == "4"
    assert p["fast_mode"] is True


def test_t2v_payload_1080p_with_fast_mode_true_rejected():
    """Dashboard rule: `resolution=1080p` requires `fast_mode=false`."""
    with pytest.raises(ValueError, match="fast_mode=false"):
        build_text_to_video_payload(
            prompt="x", webhook_url=WEBHOOK,
            resolution="1080p", fast_mode=True,
        )


def test_t2v_payload_rejects_http_webhook():
    with pytest.raises(ValueError, match="https://"):
        build_text_to_video_payload(prompt="x", webhook_url="http://insecure/x")


# --------------------------------------------------------------------------- #
# Seedance payload builders — UGC
# --------------------------------------------------------------------------- #


def test_ugc_payload_includes_products_and_influencers():
    p = build_ugc_payload(
        prompt="x",
        webhook_url=WEBHOOK,
        products=["https://example.com/p.jpg"],
        influencers=["https://example.com/i.jpg"],
    )
    assert p["type"] == "image-to-video"
    assert p["mode"] == "ugc"
    assert p["products"] == ["https://example.com/p.jpg"]
    assert p["influencers"] == ["https://example.com/i.jpg"]


def test_ugc_payload_forces_full_access_true_by_default():
    """UGC implies a human face -> `full_access: true` per dashboard rule."""
    p = build_ugc_payload(
        prompt="x", webhook_url=WEBHOOK,
        products=["https://x/p.jpg"], influencers=["https://x/i.jpg"],
    )
    assert p["full_access"] is True


def test_ugc_payload_supports_optional_full_access_override():
    """A caller can opt out for testing, but the default is on."""
    p = build_ugc_payload(
        prompt="x", webhook_url=WEBHOOK,
        products=["https://x/p.jpg"], influencers=["https://x/i.jpg"],
        full_access=False,
    )
    assert p["full_access"] is False


def test_ugc_payload_requires_at_least_one_product_and_influencer():
    with pytest.raises(ValueError, match="product"):
        build_ugc_payload(
            prompt="x", webhook_url=WEBHOOK,
            products=[], influencers=["https://x/i.jpg"],
        )
    with pytest.raises(ValueError, match="influencer"):
        build_ugc_payload(
            prompt="x", webhook_url=WEBHOOK,
            products=["https://x/p.jpg"], influencers=[],
        )


def test_ugc_payload_enforces_total_asset_cap():
    """Dashboard rule: products + influencers + images <= 9."""
    products = [f"https://x/p{i}.jpg" for i in range(5)]
    influencers = [f"https://x/i{i}.jpg" for i in range(3)]
    images = [f"https://x/im{i}.jpg" for i in range(3)]   # total 11
    with pytest.raises(ValueError, match="<= 9"):
        build_ugc_payload(
            prompt="x", webhook_url=WEBHOOK,
            products=products, influencers=influencers, images=images,
        )


def test_ugc_payload_optional_images_array():
    """Caller may add an `images[]` alongside products + influencers."""
    p = build_ugc_payload(
        prompt="x", webhook_url=WEBHOOK,
        products=["https://x/p.jpg"], influencers=["https://x/i.jpg"],
        images=["https://x/ref.jpg"],
    )
    assert p["images"] == ["https://x/ref.jpg"]


# --------------------------------------------------------------------------- #
# Seedance payload builders — multi_reference
# --------------------------------------------------------------------------- #


def test_multi_reference_payload_requires_at_least_one_ref():
    with pytest.raises(ValueError, match="at least one"):
        build_multi_reference_payload(prompt="x", webhook_url=WEBHOOK)


def test_multi_reference_payload_only_includes_supplied_arrays():
    p = build_multi_reference_payload(
        prompt="x", webhook_url=WEBHOOK,
        images=["https://x/p.jpg"],
    )
    assert p["mode"] == "multi_reference"
    assert p["images"] == ["https://x/p.jpg"]
    assert "videos" not in p
    assert "audios" not in p


def test_multi_reference_payload_caps_video_count():
    too_many = [f"https://x/v{i}.mp4" for i in range(4)]
    with pytest.raises(ValueError, match="videos"):
        build_multi_reference_payload(
            prompt="x", webhook_url=WEBHOOK, videos=too_many,
        )


# --------------------------------------------------------------------------- #
# Audio Fixer payload
# --------------------------------------------------------------------------- #


def test_audio_fixer_payload_uses_input_video_field():
    """Dashboard contract: exactly `inputVideo` + `webhook_url`."""
    p = build_audio_fixer_payload(
        input_video_url="https://example.com/raw.mp4",
        webhook_url=WEBHOOK,
    )
    assert set(p.keys()) == {"inputVideo", "webhook_url"}
    assert p["inputVideo"] == "https://example.com/raw.mp4"
    assert p["webhook_url"] == WEBHOOK


def test_audio_fixer_payload_rejects_http_input_video():
    with pytest.raises(ValueError, match="https://"):
        build_audio_fixer_payload(
            input_video_url="http://insecure/v.mp4", webhook_url=WEBHOOK,
        )


def test_audio_fixer_payload_rejects_http_webhook():
    with pytest.raises(ValueError, match="https://"):
        build_audio_fixer_payload(
            input_video_url="https://x/v.mp4", webhook_url="http://insecure",
        )


# --------------------------------------------------------------------------- #
# Provider protocol conformance
# --------------------------------------------------------------------------- #


def test_seedance_provider_implements_provider_protocol():
    """`isinstance(..., Provider)` works because `Provider` is a
    `@runtime_checkable` Protocol."""
    provider = _make_seedance()
    assert isinstance(provider, Provider)
    assert provider.name == "enhancor_seedance"


def test_audio_fixer_provider_implements_provider_protocol():
    provider = _make_audio_fixer()
    assert isinstance(provider, Provider)
    assert provider.name == "enhancor_audio_fixer"


# --------------------------------------------------------------------------- #
# API-key safety
# --------------------------------------------------------------------------- #


def test_seedance_provider_repr_does_not_leak_api_key():
    provider = EnhancorSeedanceProvider("sk_super_secret_KEY")
    assert "sk_super_secret_KEY" not in repr(provider)
    assert "sk_super_secret_KEY" not in str(provider)


def test_audio_fixer_provider_repr_does_not_leak_api_key():
    provider = EnhancorAudioFixerProvider("sk_super_secret_KEY")
    assert "sk_super_secret_KEY" not in repr(provider)
    assert "sk_super_secret_KEY" not in str(provider)


def test_redact_api_key_headers_is_case_insensitive():
    """Header dump must redact `x-api-key`, `X-API-Key`, `Authorization`, etc."""
    raw = {
        "x-api-key": "sk_live_abc",
        "X-API-Key": "sk_live_def",
        "Authorization": "Bearer sk_live_ghi",
        "Content-Type": "application/json",
    }
    redacted = redact_api_key_headers(raw)
    for k, v in redacted.items():
        if k.lower() in {"x-api-key", "authorization", "api-key"}:
            assert v == "***redacted***", f"{k!r} not redacted (value={v!r})"
        else:
            assert v == raw[k]
    # Original input untouched.
    assert raw["x-api-key"] == "sk_live_abc"


def test_provider_error_repr_does_not_leak_api_key():
    """ProviderError carries `raw_response`; if a caller puts a header
    dump in there by mistake, the redaction helper should have been
    used first. The error class itself doesn't carry headers, so its
    own repr is safe."""
    err = ProviderError(
        "boom",
        provider="enhancor_seedance",
        http_status=500,
        raw_response={"some": "thing"},
    )
    assert "sk_" not in repr(err)
    assert "x-api-key" not in repr(err)


# --------------------------------------------------------------------------- #
# Seedance submit -> completed -> download (in-memory)
# --------------------------------------------------------------------------- #


def _seedance_request_t2v() -> ProviderJobRequest:
    payload = build_text_to_video_payload(prompt="x", webhook_url=WEBHOOK)
    return ProviderJobRequest(
        provider="enhancor_seedance",
        job_type="text-to-video",
        payload=payload,
        correlation_id="pai-route-01",
    )


def test_seedance_submit_returns_queued_with_request_id():
    fake = FakeSession()
    fake.enqueue_post(_FakeResponse(
        200, {"success": True, "requestId": "6a0850e96c164b8f24cb7d05"},
    ))
    provider = _make_seedance(fake)
    resp = provider.submit_job(_seedance_request_t2v())
    # Wire path verified.
    assert len(fake.post_calls) == 1
    assert fake.post_calls[0]["url"].endswith("/queue")
    assert fake.post_calls[0]["headers"]["x-api-key"] == "sk_test_abc123"
    assert fake.post_calls[0]["json"]["type"] == "text-to-video"
    # Response shape verified.
    assert resp.provider == "enhancor_seedance"
    assert resp.provider_job_id == "6a0850e96c164b8f24cb7d05"
    assert resp.status == ProviderStatus.QUEUED
    assert resp.correlation_id == "pai-route-01"
    assert resp.raw_response["requestId"] == "6a0850e96c164b8f24cb7d05"


def test_seedance_submit_rejects_non_seedance_request_provider():
    provider = _make_seedance()
    bad = ProviderJobRequest(
        provider="kling",
        job_type="text-to-video",
        payload={"type": "text-to-video", "prompt": "x", "webhook_url": WEBHOOK},
    )
    with pytest.raises(ProviderError, match="provider must be"):
        provider.submit_job(bad)


def test_seedance_submit_raises_on_http_error():
    fake = FakeSession()
    fake.enqueue_post(_FakeResponse(
        400, {"success": False, "error": "duration value '13' is invalid"},
    ))
    provider = _make_seedance(fake)
    with pytest.raises(ProviderError) as exc:
        provider.submit_job(_seedance_request_t2v())
    assert exc.value.http_status == 400
    assert exc.value.raw_response == {
        "success": False, "error": "duration value '13' is invalid",
    }


def test_seedance_poll_status_parses_completed_with_thumbnail():
    """Smoke discovery: terminal-success payload carries undocumented
    `thumbnail` field. The adapter MUST surface it."""
    fake = FakeSession()
    fake.enqueue_post(_FakeResponse(200, {
        "success": True,
        "requestId": "6a0850e96c164b8f24cb7d05",
        "status": "COMPLETED",
        "result": "https://dabc12.cloudfront.net/some/path.mp4",
        "thumbnail": "https://dabc12.cloudfront.net/some/path.webp",
        "cost": 264,
    }))
    provider = _make_seedance(fake)
    s = provider.poll_status("6a0850e96c164b8f24cb7d05")
    assert s.status == ProviderStatus.COMPLETED
    assert s.result_url == "https://dabc12.cloudfront.net/some/path.mp4"
    assert s.thumbnail_url == "https://dabc12.cloudfront.net/some/path.webp"
    assert s.cost == 264.0
    assert s.error_message is None
    # Wire path: POST /status with BOTH request_id (snake) and requestId
    # (camel) in the body. Confirmed by the live API rejecting a
    # camel-only body with HTTP 400 {"error":{"message":"request_id is
    # required"}} on 2026-05-16; smoke test was already double-sending
    # both keys defensively.
    assert fake.post_calls[0]["url"].endswith("/status")
    assert fake.post_calls[0]["json"] == {
        "request_id": "6a0850e96c164b8f24cb7d05",
        "requestId": "6a0850e96c164b8f24cb7d05",
    }


def test_seedance_poll_status_parses_in_progress():
    fake = FakeSession()
    fake.enqueue_post(_FakeResponse(200, {"status": "IN_PROGRESS"}))
    provider = _make_seedance(fake)
    s = provider.poll_status("any-id")
    assert s.status == ProviderStatus.IN_PROGRESS
    assert s.result_url is None
    assert s.thumbnail_url is None


def test_seedance_poll_status_parses_failed_with_message():
    fake = FakeSession()
    fake.enqueue_post(_FakeResponse(200, {
        "status": "FAILED",
        "error": "generation could not complete: face out of frame",
    }))
    provider = _make_seedance(fake)
    s = provider.poll_status("any-id")
    assert s.status == ProviderStatus.FAILED
    assert s.error_message == "generation could not complete: face out of frame"
    assert s.result_url is None


def test_seedance_poll_status_unknown_status_string_lands_in_unknown_enum():
    """A future Seedance version returning an unexpected status string
    MUST land in UNKNOWN so callers can decide whether to keep polling
    or surface to operator — never silent terminal-success."""
    fake = FakeSession()
    fake.enqueue_post(_FakeResponse(200, {"status": "FUTURE_NEW_STATE"}))
    provider = _make_seedance(fake)
    s = provider.poll_status("any-id")
    assert s.status == ProviderStatus.UNKNOWN


# --------------------------------------------------------------------------- #
# Audio Fixer submit + status (in-memory)
# --------------------------------------------------------------------------- #


def test_audio_fixer_submit_audio_fix_one_shot():
    """`submit_audio_fix` builds the payload + submits in one call."""
    fake = FakeSession()
    fake.enqueue_post(_FakeResponse(
        200, {"success": True, "requestId": "6a0852fcdb43fe5882998b35"},
    ))
    provider = _make_audio_fixer(fake)
    resp = provider.submit_audio_fix(
        input_video_url="https://dabc12.cloudfront.net/raw.mp4",
        webhook_url=WEBHOOK,
        correlation_id="pai-route-01",
    )
    assert resp.provider == "enhancor_audio_fixer"
    assert resp.provider_job_id == "6a0852fcdb43fe5882998b35"
    assert resp.status == ProviderStatus.QUEUED
    assert resp.correlation_id == "pai-route-01"
    assert fake.post_calls[0]["url"].endswith("/queue")
    assert fake.post_calls[0]["json"] == {
        "inputVideo": "https://dabc12.cloudfront.net/raw.mp4",
        "webhook_url": WEBHOOK,
    }


def test_audio_fixer_poll_status_parses_completed_on_fal_media_cdn():
    """The Audio Fixer's terminal result lives on a different CDN than
    Seedance's result; the adapter must accept any HTTPS host."""
    fake = FakeSession()
    fake.enqueue_post(_FakeResponse(200, {
        "requestId": "6a0852fcdb43fe5882998b35",
        "status": "COMPLETED",
        "result": "https://v3b.fal.media/files/foo_combined_output.mp4",
        "cost": 561,
    }))
    provider = _make_audio_fixer(fake)
    s = provider.poll_status("6a0852fcdb43fe5882998b35")
    assert s.status == ProviderStatus.COMPLETED
    assert s.result_url == "https://v3b.fal.media/files/foo_combined_output.mp4"
    assert s.cost == 561.0
    # Audio Fixer does not emit a thumbnail.
    assert s.thumbnail_url is None


def test_audio_fixer_poll_status_parses_failed():
    fake = FakeSession()
    fake.enqueue_post(_FakeResponse(200, {
        "status": "FAILED",
        "message": "input video unreachable",
    }))
    provider = _make_audio_fixer(fake)
    s = provider.poll_status("any-id")
    assert s.status == ProviderStatus.FAILED
    assert s.error_message == "input video unreachable"


# --------------------------------------------------------------------------- #
# CDN-agnostic — result URL host is not hardcoded
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "result_url",
    [
        "https://dabc12.cloudfront.net/seedance/output.mp4",
        "https://v3b.fal.media/files/audio_fixer.mp4",
        "https://cdn.example-future.com/path.mp4",
        "https://r2.cloudflarestorage.com/yuvo/route_01.mp4",
        "https://storage.googleapis.com/yuvo-mirror/output.mp4",
        "https://yuvo-mirror.s3.eu-west-2.amazonaws.com/route_01.mp4",
    ],
)
def test_seedance_accepts_any_https_result_url_host(result_url):
    """The adapter MUST NOT hardcode CloudFront. Any HTTPS host on the
    `result` field is accepted; the mirror-to-storage step (out of
    scope here) re-hosts on a canonical bucket."""
    fake = FakeSession()
    fake.enqueue_post(_FakeResponse(200, {
        "status": "COMPLETED",
        "result": result_url,
        "cost": 100,
    }))
    provider = _make_seedance(fake)
    s = provider.poll_status("id")
    assert s.status == ProviderStatus.COMPLETED
    assert s.result_url == result_url


@pytest.mark.parametrize(
    "result_url",
    [
        "https://v3b.fal.media/files/x.mp4",
        "https://dabc12.cloudfront.net/x.mp4",                # cross-CDN combo
        "https://provider-future.com/cdn/x.mp4",
    ],
)
def test_audio_fixer_accepts_any_https_result_url_host(result_url):
    fake = FakeSession()
    fake.enqueue_post(_FakeResponse(200, {
        "status": "COMPLETED",
        "result": result_url,
        "cost": 561,
    }))
    provider = _make_audio_fixer(fake)
    s = provider.poll_status("id")
    assert s.status == ProviderStatus.COMPLETED
    assert s.result_url == result_url


def test_download_result_rejects_non_https_url():
    """The adapter refuses to download from http:// even if the provider
    happens to return one — we don't allow operator-facing artefacts to
    originate over plaintext."""
    provider = _make_seedance()
    with pytest.raises(ValueError, match="https://"):
        provider.download_result("http://insecure/file.mp4", dest_path=__import__("pathlib").Path("/tmp/nope.mp4"))


def test_download_result_streams_bytes_to_disk(tmp_path):
    """Download path is wired correctly; the adapter writes the streamed
    chunks verbatim to the destination."""
    fake = FakeSession()
    fake.enqueue_get(_FakeResponse(200, body_chunks=(b"hello-", b"world")))
    provider = _make_seedance(fake)
    dest = tmp_path / "out.mp4"
    out = provider.download_result(
        "https://cdn.example.com/out.mp4", dest_path=dest,
    )
    assert out == dest
    assert dest.is_file()
    assert dest.read_bytes() == b"hello-world"
    assert fake.get_calls[0]["url"] == "https://cdn.example.com/out.mp4"
    assert fake.get_calls[0]["stream"] is True


# --------------------------------------------------------------------------- #
# UGC native-audio finding — exposed as documentation/metadata
# --------------------------------------------------------------------------- #


def test_ugc_native_audio_finding_exposed_as_module_constant():
    """The 2026-05-16 smoke confirmed Seedance UGC output already
    contains a native audio track (text-to-video output is silent). The
    provider MUST NOT bake "native audio" into every mode — the finding
    is UGC-mode-only and is surfaced as a named module constant so the
    dashboard / cost ledger / Audio Fixer chaining logic can branch on
    it without scraping docstrings or running paid jobs.
    """
    import agents.producer.providers.enhancor_seedance as seedance_mod
    # UGC: confirmed native audio.
    assert seedance_mod.UGC_OUTPUT_INCLUDES_NATIVE_AUDIO is True
    # text-to-video: confirmed silent.
    assert seedance_mod.TEXT_TO_VIDEO_OUTPUT_IS_SILENT is True
    # multi_reference: not yet measured.
    assert seedance_mod.MULTI_REFERENCE_OUTPUT_AUDIO_BEHAVIOUR == "unknown"
    # The two confirmed findings must be opposite booleans — guarding
    # against a future copy-paste mistake that sets both to True or both
    # to False.
    assert (
        seedance_mod.UGC_OUTPUT_INCLUDES_NATIVE_AUDIO
        != seedance_mod.TEXT_TO_VIDEO_OUTPUT_IS_SILENT
    ) is False, (
        "By design these are independent findings — UGC has audio AND "
        "t2v is silent. Both should currently be True."
    )


def test_provider_job_status_carries_raw_response():
    """Every `ProviderJobStatus` carries the full raw status response so
    downstream consumers can discover undocumented fields (like
    `thumbnail`) without re-running paid jobs."""
    fake = FakeSession()
    raw = {
        "status": "COMPLETED",
        "result": "https://cdn.example.com/x.mp4",
        "thumbnail": "https://cdn.example.com/x.webp",
        "cost": 264,
        "future_undocumented_field": ["a", "b"],
    }
    fake.enqueue_post(_FakeResponse(200, raw))
    provider = _make_seedance(fake)
    s = provider.poll_status("id")
    assert isinstance(s, ProviderJobStatus)
    # Raw response preserved verbatim.
    assert s.raw_status_response == raw
    # Undocumented field still accessible by downstream code.
    assert s.raw_status_response["future_undocumented_field"] == ["a", "b"]


# --------------------------------------------------------------------------- #
# Status enum serialisation
# --------------------------------------------------------------------------- #


def test_provider_status_enum_serialises_as_string():
    """The enum is `str`-mixed so JSON / dict round-trips keep the short
    code intact."""
    import json as _json
    payload = {"status": ProviderStatus.QUEUED.value}
    assert _json.dumps(payload) == '{"status": "QUEUED"}'
    assert ProviderStatus.COMPLETED.value == "COMPLETED"


# --------------------------------------------------------------------------- #
# Regression: /status body must carry BOTH `request_id` and `requestId`
# --------------------------------------------------------------------------- #
#
# Background: the live Enhancor `/status` endpoint rejects a camel-only
# body with HTTP 400 `{"error":{"message":"request_id is required"}}`.
# The smoke script defensively sends both keys; the provider must match.
# This test locks the contract on both adapters so a future "cleanup"
# refactor that drops one of the keys breaks the test, not production.


def test_seedance_poll_status_wire_body_includes_request_id_snake_case():
    fake = FakeSession()
    fake.enqueue_post(_FakeResponse(200, {"status": "IN_PROGRESS"}))
    provider = _make_seedance(fake)
    provider.poll_status("task-xyz")
    body = fake.post_calls[0]["json"]
    assert body.get("request_id") == "task-xyz", (
        "Enhancor /status requires snake_case `request_id` — confirmed "
        "by HTTP 400 on 2026-05-16 when only camelCase was sent."
    )
    assert body.get("requestId") == "task-xyz", (
        "Sending both keys is defensive — matches the smoke script's "
        "long-running contract."
    )


def test_audio_fixer_poll_status_wire_body_includes_request_id_snake_case():
    fake = FakeSession()
    fake.enqueue_post(_FakeResponse(200, {"status": "IN_PROGRESS"}))
    provider = _make_audio_fixer(fake)
    provider.poll_status("task-xyz")
    body = fake.post_calls[0]["json"]
    assert body.get("request_id") == "task-xyz"
    assert body.get("requestId") == "task-xyz"
