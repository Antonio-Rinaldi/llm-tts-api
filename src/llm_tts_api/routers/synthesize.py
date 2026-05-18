"""Rich endpoint ``POST /v1/tts/synthesize`` (S-013, S-015, S-016).

Post-S-017 this handler is a thin wrapper over
:func:`llm_tts_api.services.synthesize_service.synthesize_core` — the
shared service-layer entry point that the OpenAI adapter
(:mod:`llm_tts_api.routers.audio`) also calls. There is exactly one
synthesis pipeline (BR-9), eliminating dual-code-path drift between the
two endpoints.

Helpers (:func:`_run_synthesis`, :class:`_TrailerStreamingResponse`,
:func:`_client_advertises_trailers`) are re-exported here so existing
unit tests (``tests/test_synthesize.py``) keep their import paths.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from llm_tts_api.config import Settings
from llm_tts_api.dependencies import (
    get_device_profile,
    get_provider_selection,
    get_settings,
    get_tts_provider_registry,
    get_voice_blob_repo,
    get_voice_metadata_repo,
)
from llm_tts_api.engine import DeviceProfile
from llm_tts_api.schemas.synthesis import SynthesizeRequest
from llm_tts_api.services.synthesize_service import (
    _client_advertises_trailers,
    _run_synthesis,
    _TrailerStreamingResponse,
    synthesize_core,
)
from llm_tts_api.services.tts_providers.auto_select import ProviderSelection
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry
from llm_tts_api.services.voice_store import (
    VoiceBlobRepository,
    VoiceMetadataRepository,
)

__all__ = [
    "_TrailerStreamingResponse",
    "_client_advertises_trailers",
    "_run_synthesis",
    "router",
    "synthesize",
]

router = APIRouter(prefix="/v1/tts", tags=["synthesize"])


@router.post("/synthesize", response_model=None)
async def synthesize(
    payload: SynthesizeRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    provider_registry: Annotated[TTSProviderRegistry, Depends(get_tts_provider_registry)],
    provider_selection: Annotated[ProviderSelection, Depends(get_provider_selection)],
    device_profile: Annotated[DeviceProfile, Depends(get_device_profile)],
    metadata_repo: Annotated[VoiceMetadataRepository, Depends(get_voice_metadata_repo)],
    blob_repo: Annotated[VoiceBlobRepository, Depends(get_voice_blob_repo)],
) -> Response:
    """Rich-endpoint synthesis handler — delegates to the shared pipeline."""
    return await synthesize_core(
        payload,
        request=request,
        settings=settings,
        provider_registry=provider_registry,
        provider_selection=provider_selection,
        device_profile=device_profile,
        metadata_repo=metadata_repo,
        blob_repo=blob_repo,
    )
