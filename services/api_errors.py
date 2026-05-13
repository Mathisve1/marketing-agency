"""Shared classifier for external-API failures.

Why: every external integration (Tavily, Apify, Meta, Kling) was raising
something different and our wrappers each invented their own
"API ERROR: <str>" format. The operator (and the LLM reading tool
results) had to read English to figure out whether a failure was
auth, rate-limit, timeout, or a real provider crash.

This module centralises that judgement. Each wrapper now produces:

    "API ERROR [tavily / RATE_LIMIT]: 429 too many requests"

The class token is machine-grep-able, sortable in the audit JSONL, and
gives the operator an immediate triage signal without having to read
the trailing message.

Producer's Kling failure path (`agents/producer/agent.py`) is
intentionally NOT changed by this module - it has its own
`_classify_kling_failure` with extra timeout-warning behaviour that
predates this. Consolidating those is a separate pass.

NEVER calls a provider. Pure heuristics on exception type + str(exc).
"""
from __future__ import annotations

from enum import Enum


class ApiErrorClass(str, Enum):
    MISSING_KEY = "MISSING_KEY"             # config issue: env var unset / 401
    RATE_LIMIT = "RATE_LIMIT"               # 429 / explicit "rate limit"
    TIMEOUT = "TIMEOUT"                     # network read/connect timeout
    PROVIDER_ERROR = "PROVIDER_ERROR"       # 5xx / transient backend failure
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"  # JSON decode / unexpected shape
    NO_DATA = "NO_DATA"                     # call succeeded but no rows / empty
    UNKNOWN = "UNKNOWN"                     # fall-through


# String fragments are matched case-insensitively against
# (type(exc).__name__ + " " + str(exc)). Order matters:
# the first matching class wins, so MISSING_KEY beats PROVIDER_ERROR
# when "401 unauthorized: api_key invalid" appears.
_PATTERNS: tuple[tuple[ApiErrorClass, tuple[str, ...]], ...] = (
    (ApiErrorClass.MISSING_KEY, (
        "missing api key", "api key not set", "no api key",
        "tavily_api_key", "anthropic_api_key", "apify_api_token",
        "meta_access_token", "kling_api_key",
        "401", "unauthorized", "authentication failed",
        "invalid api key", "invalid_api_key", "invalid token",
    )),
    (ApiErrorClass.RATE_LIMIT, (
        "rate limit", "rate-limit", "rate_limit", "ratelimit",
        "too many requests", "429",
        "quota exceeded", "exceeded quota",
    )),
    (ApiErrorClass.TIMEOUT, (
        "timeout", "timed out",
        "connecttimeout", "readtimeout",
        "connectionerror", "connection error", "connection reset",
        "remote disconnected", "remotedisconnected",
        "incomplete read", "incompleteread",
    )),
    (ApiErrorClass.MALFORMED_RESPONSE, (
        "json decode", "jsondecodeerror", "expecting value",
        "unexpected response", "could not parse",
        "no dataset", "empty dataset id",  # apify-flavoured malformed
        "no task_id",                       # kling-flavoured malformed
    )),
    (ApiErrorClass.NO_DATA, (
        "no ads found", "no results",
        "empty result", "zero rows", "no data returned",
    )),
    (ApiErrorClass.PROVIDER_ERROR, (
        "5xx", "500", "502", "503", "504",
        "internal server error", "service unavailable",
        "bad gateway", "gateway timeout",
        "provider error",
    )),
)


def _safe_str(exc: BaseException) -> str:
    """Some pathological exceptions raise from __str__. We must not let
    that take down a wrapper's error path - it would replace a known
    failure with a *different* unhandled exception higher up the stack."""
    try:
        return str(exc)
    except Exception:
        return "(exception's __str__ raised)"


def classify_exception(exc: BaseException) -> ApiErrorClass:
    """Return the best-fit ApiErrorClass for `exc`.

    Heuristic only: matches case-insensitive substrings against the
    exception type name + str(exc). Returns UNKNOWN when nothing
    matches - never raises, even on weird custom exceptions whose
    __str__ blows up.
    """
    needle = (type(exc).__name__ + " " + _safe_str(exc)).lower()
    for cls, patterns in _PATTERNS:
        if any(p in needle for p in patterns):
            return cls
    return ApiErrorClass.UNKNOWN


def format_api_error(provider: str, exc: BaseException) -> str:
    """Operator-facing error string. Format:

        "API ERROR [<provider> / <CLASS>]: <type>: <message>"

    The class token is the value the operator should triage on:
      MISSING_KEY     -> check .env, the call never reached the provider
      RATE_LIMIT      -> back off; provider rejected for volume
      TIMEOUT         -> may have reached the provider; verify before retry
      PROVIDER_ERROR  -> 5xx; usually transient, safe to retry once
      MALFORMED_RESPONSE -> provider returned garbage; file a bug
      NO_DATA         -> nothing went wrong, just an empty result set
      UNKNOWN         -> read the message
    """
    cls = classify_exception(exc)
    return f"API ERROR [{provider} / {cls.value}]: {type(exc).__name__}: {_safe_str(exc)}"
