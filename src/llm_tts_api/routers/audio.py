from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, StreamingResponse

from llm_tts_api.dependencies import get_stt_service, get_tts_service
from llm_tts_api.errors import not_implemented
from llm_tts_api.schemas.speech import SpeechRequest
from llm_tts_api.services.stt_service import STTService
from llm_tts_api.services.tts_service import TTSService

router = APIRouter(prefix="/v1/audio", tags=["audio"])
TTSDependency = Annotated[TTSService, Depends(get_tts_service)]
STTDependency = Annotated[STTService, Depends(get_stt_service)]


def _raise_not_implemented(endpoint: str) -> None:
    """Raise the standard OpenAI-style not-implemented error for one endpoint."""
    raise not_implemented(f"Endpoint '{endpoint}' is not implemented yet")


@router.post("/speech")
def create_speech(
    req: SpeechRequest,
    tts_service: TTSDependency,
    stream: bool = Query(False, description="If true, stream audio from memory instead of file"),
) -> FileResponse:
    """Generate speech audio from text using the configured TTS pipeline."""
    return tts_service.create_speech(req, stream=stream)


@router.post("/transcriptions")
def create_transcription(stt_service: STTDependency):
    """Placeholder endpoint for speech-to-text transcription."""
    return stt_service.create_transcription()


@router.post("/translations")
def create_translation(stt_service: STTDependency):
    """Placeholder endpoint for speech translation."""
    return stt_service.create_translation()


@router.post("/voices")
def create_voice() -> None:
    """Placeholder endpoint for voice enrollment workflows."""
    _raise_not_implemented("/v1/audio/voices")


@router.get("/voice_consents")
def list_voice_consents() -> None:
    """Placeholder endpoint for listing voice consent records."""
    _raise_not_implemented("/v1/audio/voice_consents")


@router.post("/voice_consents")
def create_voice_consents() -> None:
    """Placeholder endpoint for creating voice consent records."""
    _raise_not_implemented("/v1/audio/voice_consents")


@router.get("/voice_consents/{consent_id}")
def get_voice_consent(consent_id: str) -> None:
    """Placeholder endpoint for retrieving one voice consent record."""
    _ = consent_id
    _raise_not_implemented("/v1/audio/voice_consents/{consent_id}")


@router.post("/voice_consents/{consent_id}")
def update_voice_consent(consent_id: str) -> None:
    """Placeholder endpoint for updating one voice consent record."""
    _ = consent_id
    _raise_not_implemented("/v1/audio/voice_consents/{consent_id}")


@router.delete("/voice_consents/{consent_id}")
def delete_voice_consent(consent_id: str) -> None:
    """Placeholder endpoint for deleting one voice consent record."""
    _ = consent_id
    _raise_not_implemented("/v1/audio/voice_consents/{consent_id}")
