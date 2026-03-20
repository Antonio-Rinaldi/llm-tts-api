from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from qwen_tts_api.dependencies import get_stt_service, get_tts_service
from qwen_tts_api.errors import not_implemented
from qwen_tts_api.schemas.speech import SpeechRequest
from qwen_tts_api.services.stt_service import STTService
from qwen_tts_api.services.tts_service import TTSService

router = APIRouter(prefix="/v1/audio", tags=["audio"])


@router.post("/speech")
def create_speech(req: SpeechRequest, tts_service: TTSService = Depends(get_tts_service)) -> FileResponse:
    return tts_service.create_speech(req)


@router.post("/transcriptions")
def create_transcription(stt_service: STTService = Depends(get_stt_service)):
    return stt_service.create_transcription()


@router.post("/translations")
def create_translation(stt_service: STTService = Depends(get_stt_service)):
    return stt_service.create_translation()


@router.post("/voices")
def create_voice():
    raise not_implemented("Endpoint '/v1/audio/voices' is not implemented yet")


@router.get("/voice_consents")
def list_voice_consents():
    raise not_implemented("Endpoint '/v1/audio/voice_consents' is not implemented yet")


@router.post("/voice_consents")
def create_voice_consents():
    raise not_implemented("Endpoint '/v1/audio/voice_consents' is not implemented yet")


@router.get("/voice_consents/{consent_id}")
def get_voice_consent(consent_id: str):
    _ = consent_id
    raise not_implemented("Endpoint '/v1/audio/voice_consents/{consent_id}' is not implemented yet")


@router.post("/voice_consents/{consent_id}")
def update_voice_consent(consent_id: str):
    _ = consent_id
    raise not_implemented("Endpoint '/v1/audio/voice_consents/{consent_id}' is not implemented yet")


@router.delete("/voice_consents/{consent_id}")
def delete_voice_consent(consent_id: str):
    _ = consent_id
    raise not_implemented("Endpoint '/v1/audio/voice_consents/{consent_id}' is not implemented yet")
