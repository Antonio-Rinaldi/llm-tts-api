from fastapi import APIRouter

from qwen_tts_api.errors import not_implemented

router = APIRouter(prefix="/v1/chat", tags=["chat"])


@router.post("/completions")
def create_chat_completion():
    raise not_implemented("Endpoint '/v1/chat/completions' is not implemented yet")


@router.get("/completions")
def list_chat_completions():
    raise not_implemented("Endpoint '/v1/chat/completions' is not implemented yet")


@router.get("/completions/{completion_id}")
def retrieve_chat_completion(completion_id: str):
    _ = completion_id
    raise not_implemented("Endpoint '/v1/chat/completions/{completion_id}' is not implemented yet")


@router.post("/completions/{completion_id}")
def update_chat_completion(completion_id: str):
    _ = completion_id
    raise not_implemented("Endpoint '/v1/chat/completions/{completion_id}' is not implemented yet")


@router.delete("/completions/{completion_id}")
def delete_chat_completion(completion_id: str):
    _ = completion_id
    raise not_implemented("Endpoint '/v1/chat/completions/{completion_id}' is not implemented yet")


@router.get("/completions/{completion_id}/messages")
def list_chat_completion_messages(completion_id: str):
    _ = completion_id
    raise not_implemented("Endpoint '/v1/chat/completions/{completion_id}/messages' is not implemented yet")
