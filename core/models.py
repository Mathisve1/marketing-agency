"""Canonical registry of Anthropic models the agency supports.

Centralized so UI, CLI, and agent code stay in sync — adding a new model
means one edit here, not three.
"""
from __future__ import annotations

# Friendly label -> model_id, in display order. The label appears in the
# Streamlit selectbox; the id is passed to ChatAnthropic.
SUPPORTED_MODELS: dict[str, str] = {
    "Claude Sonnet 4.6 - Recommended (faster, ~3x cheaper)": "claude-sonnet-4-6",
    "Claude Opus 4.7 - Premium (deeper reasoning)": "claude-opus-4-7",
}

SUPPORTED_MODEL_IDS = tuple(SUPPORTED_MODELS.values())


def validate_model_id(model_id: str) -> None:
    if model_id not in SUPPORTED_MODEL_IDS:
        raise ValueError(
            f"Unsupported model {model_id!r}. "
            f"Choose one of: {', '.join(SUPPORTED_MODEL_IDS)}"
        )
