from fastapi import APIRouter

from qwen_tts_api.errors import not_implemented

router = APIRouter(prefix="/v1/realtime", tags=["realtime"])


@router.post("/client_secrets")
def create_client_secret():
    raise not_implemented("Endpoint '/v1/realtime/client_secrets' is not implemented yet")


@router.post("/calls/{call_id}/accept")
def accept_call(call_id: str):
    _ = call_id
    raise not_implemented("Endpoint '/v1/realtime/calls/{call_id}/accept' is not implemented yet")


@router.post("/calls/{call_id}/hangup")
def hangup_call(call_id: str):
    _ = call_id
    raise not_implemented("Endpoint '/v1/realtime/calls/{call_id}/hangup' is not implemented yet")


@router.post("/calls/{call_id}/refer")
def refer_call(call_id: str):
    _ = call_id
    raise not_implemented("Endpoint '/v1/realtime/calls/{call_id}/refer' is not implemented yet")


@router.post("/calls/{call_id}/reject")
def reject_call(call_id: str):
    _ = call_id
    raise not_implemented("Endpoint '/v1/realtime/calls/{call_id}/reject' is not implemented yet")


@router.post("/sessions")
def create_realtime_session():
    raise not_implemented("Endpoint '/v1/realtime/sessions' is not implemented yet")


@router.post("/transcription_sessions")
def create_realtime_transcription_session():
    raise not_implemented("Endpoint '/v1/realtime/transcription_sessions' is not implemented yet")
