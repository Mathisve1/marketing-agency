"""Provider-agnostic video-generation adapters.

Each concrete provider (Enhancor Seedance, Enhancor Audio Fixer, future
Kling rewrap, etc.) implements the same `Provider` protocol from `base`
and returns the same `ProviderJobRequest` / `ProviderJobResponse` /
`ProviderJobStatus` / `ProviderGenerationResult` shapes. Callers depend
on the protocol; the concrete provider stays swappable.

Public surface kept intentionally small:

    from agents.producer.providers.base import (
        ProviderStatus,
        ProviderJobRequest,
        ProviderJobResponse,
        ProviderJobStatus,
        ProviderGenerationResult,
        ProviderError,
        Provider,
        classify_provider_status,
        redact_api_key_headers,
    )

    from agents.producer.providers.enhancor_seedance import (
        EnhancorSeedanceProvider,
    )
    from agents.producer.providers.enhancor_audio_fixer import (
        EnhancorAudioFixerProvider,
    )

This package does NOT touch the existing Kling client (`agents/producer/
kling/client.py`) — that surface stays separate until a deliberate
migration pass.
"""
