import pytest


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/v1/chat/completions"),
        ("get", "/v1/chat/completions"),
        ("get", "/v1/chat/completions/test-id"),
        ("post", "/v1/chat/completions/test-id"),
        ("delete", "/v1/chat/completions/test-id"),
        ("get", "/v1/chat/completions/test-id/messages"),
        ("post", "/v1/audio/voices"),
        ("get", "/v1/audio/voice_consents"),
        ("post", "/v1/audio/voice_consents"),
        ("get", "/v1/audio/voice_consents/cons_123"),
        ("post", "/v1/audio/voice_consents/cons_123"),
        ("delete", "/v1/audio/voice_consents/cons_123"),
        ("post", "/v1/realtime/client_secrets"),
        ("post", "/v1/realtime/calls/call_123/accept"),
        ("post", "/v1/realtime/calls/call_123/hangup"),
        ("post", "/v1/realtime/calls/call_123/refer"),
        ("post", "/v1/realtime/calls/call_123/reject"),
        ("post", "/v1/realtime/sessions"),
        ("post", "/v1/realtime/transcription_sessions"),
    ],
)
def test_not_implemented_routes_return_openai_error(client, method: str, path: str) -> None:
    response = getattr(client, method)(path)

    assert response.status_code == 501
    payload = response.json()
    assert "error" in payload
    assert payload["error"]["type"] == "validation_error"
    assert payload["error"]["code"] == "not_implemented"
    assert response.headers.get("X-Error-Code") == "not_implemented"
    assert "request_id" in payload["error"]
