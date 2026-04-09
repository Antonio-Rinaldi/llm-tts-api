from fastapi import APIRouter

from llm_tts_api.errors import not_implemented

router = APIRouter(prefix="/v1/chat", tags=["chat"])


def _raise_not_implemented(endpoint: str) -> None:
    """Raise the standard OpenAI-style not-implemented error for one endpoint."""
    raise not_implemented(f"Endpoint '{endpoint}' is not implemented yet")


@router.post("/completions")
def create_chat_completion() -> None:
    """Placeholder endpoint for creating chat completions."""
    _raise_not_implemented("/v1/chat/completions")


@router.get("/completions")
def list_chat_completions() -> None:
    """Placeholder endpoint for listing chat completions."""
    _raise_not_implemented("/v1/chat/completions")


@router.get("/completions/{completion_id}")
def retrieve_chat_completion(completion_id: str) -> None:
    """Placeholder endpoint for retrieving one chat completion."""
    _ = completion_id
    _raise_not_implemented("/v1/chat/completions/{completion_id}")


@router.post("/completions/{completion_id}")
def update_chat_completion(completion_id: str) -> None:
    """Placeholder endpoint for updating one chat completion."""
    _ = completion_id
    _raise_not_implemented("/v1/chat/completions/{completion_id}")


@router.delete("/completions/{completion_id}")
def delete_chat_completion(completion_id: str) -> None:
    """Placeholder endpoint for deleting one chat completion."""
    _ = completion_id
    _raise_not_implemented("/v1/chat/completions/{completion_id}")


@router.get("/completions/{completion_id}/messages")
def list_chat_completion_messages(completion_id: str) -> None:
    """Placeholder endpoint for listing messages of one chat completion."""
    _ = completion_id
    _raise_not_implemented("/v1/chat/completions/{completion_id}/messages")
