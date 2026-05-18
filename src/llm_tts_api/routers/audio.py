"""OpenAI-compatible audio endpoints (S-017).

``POST /v1/audio/speech`` is a thin translator over the rich-endpoint
service-layer entry point :func:`synthesize_core`. The handler maps the
OpenAI ``SpeechRequest`` field set onto the rich :class:`SynthesizeRequest`,
delegates synthesis, then strips rich-endpoint-only headers so the
response shape stays OpenAI-identical (FR-OA-01..03).

The handler MUST NOT import :class:`SpeechSynthesizer` or
:mod:`llm_tts_api.routers.synthesize`. UAT-OA-03 pins this via a static
check in ``tests/test_openai_adapter.py``.
"""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Query, Request, Response
from starlette.responses import StreamingResponse

from llm_tts_api.config import Settings
from llm_tts_api.dependencies import (
    get_device_profile,
    get_provider_selection,
    get_settings,
    get_stt_service,
    get_tts_provider_registry,
    get_voice_blob_repo,
    get_voice_metadata_repo,
)
from llm_tts_api.engine import DeviceProfile
from llm_tts_api.errors import invalid_request, raise_not_implemented
from llm_tts_api.observability import current_request_id
from llm_tts_api.schemas.speech import SpeechRequest
from llm_tts_api.schemas.synthesis import SynthesizeRequest
from llm_tts_api.services.stt_service import STTService
from llm_tts_api.services.synthesize_service import synthesize_core
from llm_tts_api.services.tts_providers.auto_select import ProviderSelection
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry
from llm_tts_api.services.voice_store import (
    VoiceBlobRepository,
    VoiceMetadataRepository,
)

router = APIRouter(prefix="/v1/audio", tags=["audio"])

STTDependency = Annotated[STTService, Depends(get_stt_service)]

# Rich-endpoint-only response headers — stripped from the OpenAI adapter
# path so the response shape stays OpenAI-identical (user constraint).
# Kept in sync with `synthesize_service._RICH_ONLY_HEADER_KEYS` by the
# byte-identity parity UAT (tests/test_openai_adapter_parity.py).
_RICH_ONLY_HEADERS: frozenset[str] = frozenset(
    {
        "x-provider",
        "x-model",
        "x-device",
        "x-dtype",
        "x-voice-source",
        "x-voice-id",
        "x-chunks",
        "x-total-duration-ms",
    }
)


def _translate_openai_request(req: SpeechRequest, *, stream: bool) -> SynthesizeRequest:
    """OpenAI ``SpeechRequest`` → rich ``SynthesizeRequest`` (S-017 T1).

    Field mapping table (also pinned in ``S-017-impl.md`` Service Interface):

    | OpenAI field      | Rich field      | Notes                              |
    |-------------------|-----------------|------------------------------------|
    | ``model``         | ``model``       | passed through; allow-list check   |
    | ``input``         | ``input``       | passed through                     |
    | ``voice``         | ``voice``       | passed through (voice-store id)    |
    | ``provider``      | ``provider``    | passed through (non-OpenAI ext)    |
    | ``response_format``| ``response_format`` | only ``wav`` accepted          |
    | ``normalize_db``  | ``normalize_db``| passed through (non-OpenAI ext)    |
    | (``?stream=`` qs) | ``stream``      | streaming flag                     |
    | ``instructions``  | —               | ignored (no rich equivalent yet)   |
    | ``speed``         | —               | ignored (no rich equivalent yet)   |
    | ``stream_format`` | —               | ignored (no rich equivalent yet)   |
    """
    if req.response_format and req.response_format.lower() != "wav":
        raise invalid_request(
            "Only 'wav' response_format is currently supported",
            param="response_format",
        )
    return SynthesizeRequest(
        input=req.input,
        voice=req.voice,
        provider=req.provider,
        model=req.model,
        response_format="wav",
        stream=stream,
        normalize_db=req.normalize_db,
    )


def _openai_response(inner: Response) -> Response:
    """Strip rich-endpoint-only headers from a synthesis response.

    For streaming, rewrap the body iterator in a plain
    :class:`StreamingResponse` so the rich endpoint's trailer-emission
    logic (X-Chunks / X-Total-Duration-Ms) cannot leak to the OpenAI
    client.
    """
    if isinstance(inner, StreamingResponse):
        return StreamingResponse(
            inner.body_iterator,
            status_code=inner.status_code,
            media_type="audio/wav",
            headers={"X-Request-ID": current_request_id()},
        )
    for key in list(inner.headers.keys()):
        if key.lower() in _RICH_ONLY_HEADERS:
            del inner.headers[key]
    return inner


@router.post("/speech", response_model=None)
async def create_speech(
    req: SpeechRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    provider_registry: Annotated[TTSProviderRegistry, Depends(get_tts_provider_registry)],
    provider_selection: Annotated[ProviderSelection, Depends(get_provider_selection)],
    device_profile: Annotated[DeviceProfile, Depends(get_device_profile)],
    metadata_repo: Annotated[VoiceMetadataRepository, Depends(get_voice_metadata_repo)],
    blob_repo: Annotated[VoiceBlobRepository, Depends(get_voice_blob_repo)],
    stream: bool = Query(False, description="If true, stream audio from memory instead of file"),
) -> Response:
    """OpenAI-compatible speech endpoint — thin translator over the rich pipeline."""
    payload = _translate_openai_request(req, stream=stream)
    inner = await synthesize_core(
        payload,
        request=request,
        settings=settings,
        provider_registry=provider_registry,
        provider_selection=provider_selection,
        device_profile=device_profile,
        metadata_repo=metadata_repo,
        blob_repo=blob_repo,
    )
    return _openai_response(inner)


@router.post("/transcriptions", response_model=None)
def create_transcription(stt_service: STTDependency) -> NoReturn:
    """Placeholder endpoint for speech-to-text transcription."""
    stt_service.create_transcription()


@router.post("/translations", response_model=None)
def create_translation(stt_service: STTDependency) -> NoReturn:
    """Placeholder endpoint for speech translation."""
    stt_service.create_translation()


@router.post("/voices")
def create_voice() -> None:
    """Placeholder endpoint for voice enrollment workflows."""
    raise_not_implemented("/v1/audio/voices")


@router.get("/voice_consents")
def list_voice_consents() -> None:
    """Placeholder endpoint for listing voice consent records."""
    raise_not_implemented("/v1/audio/voice_consents")


@router.post("/voice_consents")
def create_voice_consents() -> None:
    """Placeholder endpoint for creating voice consent records."""
    raise_not_implemented("/v1/audio/voice_consents")


@router.get("/voice_consents/{consent_id}")
def get_voice_consent(consent_id: str) -> None:
    """Placeholder endpoint for retrieving one voice consent record."""
    _ = consent_id
    raise_not_implemented("/v1/audio/voice_consents/{consent_id}")


@router.post("/voice_consents/{consent_id}")
def update_voice_consent(consent_id: str) -> None:
    """Placeholder endpoint for updating one voice consent record."""
    _ = consent_id
    raise_not_implemented("/v1/audio/voice_consents/{consent_id}")


@router.delete("/voice_consents/{consent_id}")
def delete_voice_consent(consent_id: str) -> None:
    """Placeholder endpoint for deleting one voice consent record."""
    _ = consent_id
    raise_not_implemented("/v1/audio/voice_consents/{consent_id}")
