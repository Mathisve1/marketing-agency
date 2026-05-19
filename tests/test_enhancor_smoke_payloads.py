"""Pure-payload tests for the Enhancor smoke script.

These tests NEVER hit the Enhancor API. They exercise only the in-memory
payload builders + the status classifier, which is the contract the Phase-0
smoke test relies on.

Why: the smoke script's payloads have to obey the hard rules captured in
``docs/enhancor_dashboard_raw.md``. Catching a regression here costs zero
API credits; catching it from a real submission costs one job + a
debugging window.
"""
from __future__ import annotations

import json as _json

import pytest
import requests

import scripts.enhancor_smoke_test as smoke_mod
from scripts.enhancor_smoke_test import (
    API_KEY_HEADER,
    SmokeOptions,
    _redact_headers,
    build_audio_fixer_payload,
    build_multi_reference_payload,
    build_payload,
    build_text_to_video_payload,
    build_ugc_payload,
    classify_status,
)
from scripts.enhancor_smoke_test import (
    main as smoke_main,
)

# --------------------------------------------------------------------------- #
# Common fixtures
# --------------------------------------------------------------------------- #


WEBHOOK = "https://example.com/api/webhooks/enhancor/seedance"


def _t2v_opts(**kw) -> SmokeOptions:
    return SmokeOptions(mode="text-to-video", webhook_url=WEBHOOK, **kw)


def _ugc_opts(**kw) -> SmokeOptions:
    return SmokeOptions(
        mode="ugc",
        webhook_url=WEBHOOK,
        product_url="https://example.com/product.jpg",
        influencer_url="https://example.com/influencer.jpg",
        **kw,
    )


def _multi_opts(**kw) -> SmokeOptions:
    return SmokeOptions(
        mode="multi-reference",
        webhook_url=WEBHOOK,
        image_url="https://example.com/product.jpg",
        **kw,
    )


# --------------------------------------------------------------------------- #
# text-to-video payload — must NOT carry images / videos / audios / products / influencers
# --------------------------------------------------------------------------- #


def test_t2v_payload_has_no_media_arrays():
    """Dashboard rule (raw § 5): `text-to-video` should not send
    `images`/`videos`/`audios`."""
    p = build_text_to_video_payload(_t2v_opts())
    assert p["type"] == "text-to-video"
    for forbidden in ("images", "videos", "audios", "products", "influencers", "mode"):
        assert forbidden not in p, f"{forbidden!r} must not appear in a t2v payload"


def test_t2v_payload_carries_required_fields():
    p = build_text_to_video_payload(_t2v_opts())
    for required in ("type", "prompt", "webhook_url", "duration", "resolution", "aspect_ratio", "fast_mode"):
        assert required in p


def test_t2v_payload_default_is_cheapest():
    """Smoke defaults must stay on the cheap path."""
    p = build_text_to_video_payload(_t2v_opts())
    assert p["resolution"] == "480p"
    assert p["aspect_ratio"] == "9:16"
    assert p["duration"] == "4"
    assert p["fast_mode"] is True


# --------------------------------------------------------------------------- #
# UGC payload — must carry products + influencers + full_access=true
# --------------------------------------------------------------------------- #


def test_ugc_payload_includes_products_and_influencers():
    p = build_ugc_payload(_ugc_opts())
    assert p["type"] == "image-to-video"
    assert p["mode"] == "ugc"
    assert p["products"] == ["https://example.com/product.jpg"]
    assert p["influencers"] == ["https://example.com/influencer.jpg"]


def test_ugc_payload_forces_full_access_true():
    """Dashboard rule (raw § 5): `full_access: true` whenever a human face
    appears in the generation. UGC mode implies a human face."""
    p = build_ugc_payload(_ugc_opts())
    assert p["full_access"] is True


def test_ugc_payload_requires_product_and_influencer_urls():
    """The builder must refuse a UGC payload that is missing either ref."""
    with pytest.raises(ValueError, match="product-url"):
        build_ugc_payload(
            SmokeOptions(mode="ugc", webhook_url=WEBHOOK, influencer_url="https://x/i.jpg"),
        )
    with pytest.raises(ValueError, match="influencer-url"):
        build_ugc_payload(
            SmokeOptions(mode="ugc", webhook_url=WEBHOOK, product_url="https://x/p.jpg"),
        )


# --------------------------------------------------------------------------- #
# multi-reference payload
# --------------------------------------------------------------------------- #


def test_multi_reference_payload_only_includes_supplied_arrays():
    """Builder must not fabricate placeholder URLs."""
    p = build_multi_reference_payload(_multi_opts())
    assert p["mode"] == "multi_reference"
    assert p["images"] == ["https://example.com/product.jpg"]
    assert "videos" not in p
    assert "audios" not in p


def test_multi_reference_payload_requires_at_least_one_ref():
    with pytest.raises(ValueError, match="at least one"):
        build_multi_reference_payload(
            SmokeOptions(mode="multi-reference", webhook_url=WEBHOOK),
        )


def test_multi_reference_payload_passes_through_video_and_audio():
    p = build_multi_reference_payload(
        _multi_opts(
            video_url="https://example.com/m.mp4",
            audio_url="https://example.com/a.mp3",
        ),
    )
    assert p["videos"] == ["https://example.com/m.mp4"]
    assert p["audios"] == ["https://example.com/a.mp3"]


# --------------------------------------------------------------------------- #
# multi_frame mode — task brief calls out that no top-level duration is sent
# --------------------------------------------------------------------------- #


def test_multi_frame_payload_not_built_by_smoke_script():
    """The Phase-0 smoke test does NOT support `multi_frame` directly (it
    requires a nested `multi_frame_prompts` array that's outside the smoke
    scope). The router must refuse the mode rather than silently send a
    payload missing `multi_frame_prompts`.

    This is a forward-compat lock: if someone adds a `--mode multi_frame`
    CLI flag without also building the per-frame array, the test fails."""
    with pytest.raises(ValueError, match="unknown mode"):
        build_payload(SmokeOptions(mode="multi_frame", webhook_url=WEBHOOK))


# --------------------------------------------------------------------------- #
# webhook_url is required everywhere
# --------------------------------------------------------------------------- #


def test_webhook_url_is_required_on_t2v():
    with pytest.raises(ValueError, match="webhook_url is mandatory"):
        build_text_to_video_payload(SmokeOptions(mode="text-to-video", webhook_url=""))


def test_webhook_url_is_required_on_ugc():
    with pytest.raises(ValueError, match="webhook_url is mandatory"):
        build_ugc_payload(
            SmokeOptions(
                mode="ugc",
                webhook_url="",
                product_url="https://x/p.jpg",
                influencer_url="https://x/i.jpg",
            ),
        )


def test_webhook_url_is_required_on_multi_reference():
    with pytest.raises(ValueError, match="webhook_url is mandatory"):
        build_multi_reference_payload(
            SmokeOptions(
                mode="multi-reference",
                webhook_url="",
                image_url="https://x/i.jpg",
            ),
        )


def test_webhook_url_must_be_https():
    """Webhook contract baseline: HTTPS only."""
    with pytest.raises(ValueError, match="https://"):
        build_text_to_video_payload(
            SmokeOptions(mode="text-to-video", webhook_url="http://insecure.example/x"),
        )


# --------------------------------------------------------------------------- #
# Audio Fixer payload uses exactly inputVideo + webhook_url
# --------------------------------------------------------------------------- #


def test_audio_fixer_payload_uses_input_video_field():
    p = build_audio_fixer_payload(
        input_video_url="https://example.com/raw.mp4",
        webhook_url=WEBHOOK,
    )
    assert set(p.keys()) == {"inputVideo", "webhook_url"}, (
        "Audio Fixer queue payload must contain exactly inputVideo + webhook_url; "
        f"got keys={sorted(p.keys())}"
    )
    assert p["inputVideo"] == "https://example.com/raw.mp4"
    assert p["webhook_url"] == WEBHOOK


def test_audio_fixer_payload_requires_input_video_url():
    with pytest.raises(ValueError, match="input_video_url"):
        build_audio_fixer_payload(input_video_url="", webhook_url=WEBHOOK)


def test_audio_fixer_payload_requires_webhook_url():
    with pytest.raises(ValueError, match="webhook_url"):
        build_audio_fixer_payload(input_video_url="https://x/v.mp4", webhook_url="")


def test_audio_fixer_payload_requires_https_webhook():
    with pytest.raises(ValueError, match="https://"):
        build_audio_fixer_payload(
            input_video_url="https://x/v.mp4",
            webhook_url="http://insecure/x",
        )


# --------------------------------------------------------------------------- #
# Dashboard hard rules — 1080p requires fast_mode=false
# --------------------------------------------------------------------------- #


def test_1080p_with_fast_mode_true_is_rejected():
    """Dashboard rule (raw § 5): 1080p only supported when fast_mode=false."""
    with pytest.raises(ValueError, match="fast_mode=false"):
        build_text_to_video_payload(_t2v_opts(resolution="1080p", fast_mode=True))


def test_1080p_with_fast_mode_false_is_accepted():
    p = build_text_to_video_payload(_t2v_opts(resolution="1080p", fast_mode=False))
    assert p["resolution"] == "1080p"
    assert p["fast_mode"] is False


# --------------------------------------------------------------------------- #
# Dashboard hard rules — duration / resolution / aspect_ratio enums
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_dur", ["3", "16", "0", "", "five"])
def test_duration_out_of_range_is_rejected(bad_dur):
    with pytest.raises(ValueError, match="duration"):
        build_text_to_video_payload(_t2v_opts(duration_sec=bad_dur))


@pytest.mark.parametrize("bad_res", ["240p", "4k", "HD", ""])
def test_resolution_out_of_enum_is_rejected(bad_res):
    with pytest.raises(ValueError, match="resolution"):
        build_text_to_video_payload(_t2v_opts(resolution=bad_res))


@pytest.mark.parametrize("bad_ar", ["2:1", "5:4", "16:10", ""])
def test_aspect_ratio_out_of_enum_is_rejected(bad_ar):
    with pytest.raises(ValueError, match="aspect_ratio"):
        build_text_to_video_payload(_t2v_opts(aspect_ratio=bad_ar))


# --------------------------------------------------------------------------- #
# Header redaction — x-api-key is never printed
# --------------------------------------------------------------------------- #


def test_redact_headers_strips_api_key():
    raw = {API_KEY_HEADER: "sk_live_abc123_secret", "Content-Type": "application/json"}
    redacted = _redact_headers(raw)
    assert redacted[API_KEY_HEADER] == "***redacted***"
    assert "sk_live_abc123_secret" not in str(redacted)
    assert redacted["Content-Type"] == "application/json"


def test_redact_headers_is_case_insensitive():
    raw = {"X-API-Key": "sk_live_abc", "x-API-KEY": "sk_live_def"}
    redacted = _redact_headers(raw)
    for k in redacted:
        assert redacted[k] == "***redacted***", f"key {k!r} was not redacted"


# --------------------------------------------------------------------------- #
# classify_status — terminal vs in-flight
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "s",
    ["COMPLETED", "completed", "Complete", "task_complete", "is_complete"],
)
def test_classify_status_success_variants(s):
    assert classify_status(s) == "success"


@pytest.mark.parametrize(
    "s",
    ["FAILED", "failed", "Failure", "error", "ERROR_TIMEOUT"],
)
def test_classify_status_failure_variants(s):
    assert classify_status(s) == "failure"


@pytest.mark.parametrize(
    "s",
    ["PENDING", "IN_QUEUE", "IN_PROGRESS", "queued", "processing", "", None, "weird"],
)
def test_classify_status_in_flight_variants(s):
    assert classify_status(s) == "in_flight"


# --------------------------------------------------------------------------- #
# build_payload dispatch
# --------------------------------------------------------------------------- #


def test_build_payload_dispatches_to_t2v():
    p = build_payload(_t2v_opts())
    assert p["type"] == "text-to-video"


def test_build_payload_dispatches_to_ugc():
    p = build_payload(_ugc_opts())
    assert p["type"] == "image-to-video"
    assert p["mode"] == "ugc"


def test_build_payload_dispatches_to_multi_reference():
    p = build_payload(_multi_opts())
    assert p["type"] == "image-to-video"
    assert p["mode"] == "multi_reference"


def test_build_payload_rejects_unknown_mode():
    with pytest.raises(ValueError, match="unknown mode"):
        build_payload(SmokeOptions(mode="totally-unsupported", webhook_url=WEBHOOK))


# --------------------------------------------------------------------------- #
# --audio-fixer-only CLI branch
# --------------------------------------------------------------------------- #


def test_audio_fixer_only_requires_input_video_url(capsys):
    """`--audio-fixer-only` without `--input-video-url` must abort with a
    clear FATAL message and exit code 2 (no API call made)."""
    rc = smoke_main([
        "--audio-fixer-only",
        "--webhook-url", WEBHOOK,
    ])
    err = capsys.readouterr().err
    assert rc == 2
    assert "FATAL" in err
    assert "input-video-url" in err


def test_audio_fixer_only_rejects_run_audio_fixer_combo(capsys):
    """`--audio-fixer-only` plus `--run-audio-fixer` is contradictory; the
    CLI must refuse the combination rather than silently run Seedance."""
    rc = smoke_main([
        "--audio-fixer-only",
        "--run-audio-fixer",
        "--webhook-url", WEBHOOK,
        "--input-video-url", "https://example.com/raw.mp4",
    ])
    err = capsys.readouterr().err
    assert rc == 2
    assert "mutually exclusive" in err


def test_audio_fixer_only_dry_run_does_not_call_api(capsys, monkeypatch, tmp_path):
    """`--audio-fixer-only --dry-run` must build + print the payload and
    return 0 without making any HTTP call. We monkey-patch ``requests`` so
    any accidental network call would explode loudly; the test passes only
    if no call was attempted."""
    # Route the artifact dir into a temp path so we don't dirty tmp/.
    monkeypatch.setattr(smoke_mod, "ARTIFACTS_ROOT", tmp_path)

    def _fail(*a, **kw):  # noqa: ANN001, ANN002
        raise AssertionError("dry-run must not make HTTP calls")
    monkeypatch.setattr(requests, "post", _fail)
    monkeypatch.setattr(requests, "get", _fail)
    monkeypatch.setattr(requests, "head", _fail)

    rc = smoke_main([
        "--audio-fixer-only",
        "--webhook-url", WEBHOOK,
        "--input-video-url", "https://example.com/raw.mp4",
        "--dry-run",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN" in out
    assert "Audio Fixer payload" in out
    dumped = list(tmp_path.rglob("dry_run_audio_fixer_payload.json"))
    assert len(dumped) == 1
    body = _json.loads(dumped[0].read_text(encoding="utf-8"))
    assert body == {"inputVideo": "https://example.com/raw.mp4", "webhook_url": WEBHOOK}
