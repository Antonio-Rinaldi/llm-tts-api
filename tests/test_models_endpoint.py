def test_models_endpoint_returns_list_shape(client) -> None:
    response = client.get("/v1/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert isinstance(payload["data"], list)
    assert len(payload["data"]) >= 1

    first = payload["data"][0]
    assert first["object"] == "model"
    assert "id" in first
    assert "owned_by" in first
