from __future__ import annotations

from llm_tts_api.services.text_preprocessing import preprocess_for_tts, split_text_semantic


def test_preprocess_for_tts_expands_dates_and_numbers_for_italian() -> None:
    text = "Evento il 15/04/2026 con 2 partecipanti..."

    out = preprocess_for_tts(text, "it")

    assert "quindici aprile" in out
    assert "duemila" in out
    assert "due" in out
    assert "..." not in out


def test_split_text_semantic_limits_sentences_per_chunk() -> None:
    text = "Prima frase. Seconda frase. Terza frase. Quarta frase."

    chunks = split_text_semantic(text, max_chars=120, max_sentences_per_chunk=2)

    assert chunks == ["Prima frase. Seconda frase.", "Terza frase. Quarta frase."]
