from fastapi import APIRouter

from llm_tts_api.errors import not_implemented

router = APIRouter(prefix="/v1/realtime", tags=["realtime"])


def _raise_not_implemented(endpoint: str) -> None:
    """Raise the standard OpenAI-style not-implemented error for one endpoint."""
    raise not_implemented(f"Endpoint '{endpoint}' is not implemented yet")


@router.post("/client_secrets")
def create_client_secret() -> None:
    """Placeholder endpoint for creating realtime client secrets."""
    _raise_not_implemented("/v1/realtime/client_secrets")


@router.post("/calls/{call_id}/accept")
def accept_call(call_id: str) -> None:
    """Placeholder endpoint for accepting a realtime call."""
    _ = call_id
    _raise_not_implemented("/v1/realtime/calls/{call_id}/accept")


@router.post("/calls/{call_id}/hangup")
def hangup_call(call_id: str) -> None:
    """Placeholder endpoint for hanging up a realtime call."""
    _ = call_id
    _raise_not_implemented("/v1/realtime/calls/{call_id}/hangup")


@router.post("/calls/{call_id}/refer")
def refer_call(call_id: str) -> None:
    """Placeholder endpoint for transferring a realtime call."""
    _ = call_id
    _raise_not_implemented("/v1/realtime/calls/{call_id}/refer")


@router.post("/calls/{call_id}/reject")
def reject_call(call_id: str) -> None:
    """Placeholder endpoint for rejecting a realtime call."""
    _ = call_id
    _raise_not_implemented("/v1/realtime/calls/{call_id}/reject")


@router.post("/sessions")
def create_realtime_session() -> None:
    """Placeholder endpoint for creating realtime sessions."""
    _raise_not_implemented("/v1/realtime/sessions")


@router.post("/transcription_sessions")
def create_realtime_transcription_session() -> None:
    """Placeholder endpoint for creating realtime transcription sessions."""
    _raise_not_implemented("/v1/realtime/transcription_sessions")
