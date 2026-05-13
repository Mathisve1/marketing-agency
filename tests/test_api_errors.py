"""Tests for services/api_errors.py.

Pins the classifier behaviour so a future change to the substring lists
can't silently re-bucket "401 unauthorized" as PROVIDER_ERROR (or worse,
UNKNOWN).
"""
from __future__ import annotations

import pytest

from services.api_errors import ApiErrorClass, classify_exception, format_api_error

# --------------------------------------------------------------------------- #
# Synthetic exceptions - never call a real provider in tests.
# --------------------------------------------------------------------------- #


class _Fake401(RuntimeError):
    pass


class _FakeRateLimit(RuntimeError):
    pass


class _FakeProvider5xx(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# classify_exception
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("exc,expected", [
    # MISSING_KEY
    (_Fake401("401 Unauthorized"), ApiErrorClass.MISSING_KEY),
    (RuntimeError("TAVILY_API_KEY is not set. Add it to .env."), ApiErrorClass.MISSING_KEY),
    (RuntimeError("Authentication failed: invalid api key"), ApiErrorClass.MISSING_KEY),

    # RATE_LIMIT
    (_FakeRateLimit("429 Too Many Requests"), ApiErrorClass.RATE_LIMIT),
    (RuntimeError("rate limit exceeded for tavily"), ApiErrorClass.RATE_LIMIT),
    (RuntimeError("quota exceeded"), ApiErrorClass.RATE_LIMIT),

    # TIMEOUT
    (TimeoutError("read timed out after 60s"), ApiErrorClass.TIMEOUT),
    (RuntimeError("ConnectTimeout: backend"), ApiErrorClass.TIMEOUT),
    (ConnectionError("Connection reset by peer"), ApiErrorClass.TIMEOUT),

    # MALFORMED_RESPONSE
    (ValueError("JSONDecodeError: Expecting value: line 1 column 1"), ApiErrorClass.MALFORMED_RESPONSE),
    (RuntimeError("Apify actor returned no dataset."), ApiErrorClass.MALFORMED_RESPONSE),

    # NO_DATA
    (RuntimeError("no ads found in the Meta account for window=last_14d"), ApiErrorClass.NO_DATA),
    (RuntimeError("empty result"), ApiErrorClass.NO_DATA),

    # PROVIDER_ERROR
    (_FakeProvider5xx("503 Service Unavailable"), ApiErrorClass.PROVIDER_ERROR),
    (RuntimeError("Internal Server Error"), ApiErrorClass.PROVIDER_ERROR),
    (RuntimeError("Bad Gateway"), ApiErrorClass.PROVIDER_ERROR),

    # UNKNOWN
    (RuntimeError("something weird happened"), ApiErrorClass.UNKNOWN),
    (Exception(""), ApiErrorClass.UNKNOWN),
])
def test_classify_exception(exc: BaseException, expected: ApiErrorClass):
    assert classify_exception(exc) is expected


def test_classify_exception_priority_missing_key_beats_provider_error():
    """A '401 unauthorized' string contains both '401' (provider-error
    range historically) and 'unauthorized' (missing-key). The classifier
    must prefer MISSING_KEY because that's the actionable diagnosis -
    the operator should fix .env, not retry."""
    exc = RuntimeError("HTTP 401 unauthorized: API key invalid")
    assert classify_exception(exc) is ApiErrorClass.MISSING_KEY


def test_classify_never_raises_on_weird_exception():
    """Custom exceptions with __str__ overrides that crash must not
    take the classifier down with them."""
    class Weird(Exception):
        def __str__(self):
            raise ValueError("str() blew up")

    # str(Weird()) raises - but the classifier should fall through to
    # UNKNOWN, not propagate the inner exception.
    try:
        result = classify_exception(Weird())
    except Exception:
        pytest.fail("classify_exception must never raise; got an unhandled exception")
    assert result is ApiErrorClass.UNKNOWN


# --------------------------------------------------------------------------- #
# format_api_error
# --------------------------------------------------------------------------- #


def test_format_api_error_includes_provider_and_class_and_type_and_message():
    s = format_api_error("tavily", RuntimeError("429 Too Many Requests"))
    assert s.startswith("API ERROR [tavily / RATE_LIMIT]:")
    assert "RuntimeError" in s
    assert "429 Too Many Requests" in s


def test_format_api_error_unknown_class_for_unmatched_message():
    s = format_api_error("apify", RuntimeError("a thing"))
    assert "[apify / UNKNOWN]" in s


def test_format_api_error_missing_key_class_for_unset_env():
    s = format_api_error("tavily", RuntimeError("TAVILY_API_KEY is not set. Add it to .env."))
    assert "[tavily / MISSING_KEY]" in s


def test_format_api_error_handles_weird_str():
    """format_api_error inherits classify's resilience. If str(exc) raises,
    we should still return SOMETHING rather than crash the wrapper."""
    class Weird(Exception):
        def __str__(self):
            raise ValueError("str() blew up")

    try:
        s = format_api_error("kling", Weird())
    except Exception:
        # If we get here, format_api_error needs hardening too. Pin the
        # behaviour we want: never bubble.
        pytest.fail("format_api_error must never raise")
    # We don't assert exact content - the message portion may degrade -
    # but the class token must be present.
    assert "[kling /" in s
