def test_transcriptions_endpoint_exists(client) -> None:
    response = client.post("/v1/audio/transcriptions")

    assert response.status_code in {400, 422, 501}



def test_translations_endpoint_exists(client) -> None:
    response = client.post("/v1/audio/translations")

    assert response.status_code in {400, 422, 501}
