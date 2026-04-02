from __future__ import annotations

from llm_tts_api.services.tts_service import split_text_semantic


def test_split_text_semantic_keeps_chunks_under_limit() -> None:
    text = "\n\n".join(
        [
            "TITOLO",
            "Prima frase lunga. Seconda frase lunga. Terza frase lunga.",
            "Quarto paragrafo con altre frasi. Quinta frase.",
        ]
    )

    chunks = split_text_semantic(text, 40)

    assert chunks
    assert all(len(chunk) <= 40 for chunk in chunks)


def test_split_text_semantic_prefers_sentence_boundaries() -> None:
    text = "Prima frase. Seconda frase. Terza frase."

    chunks = split_text_semantic(text, 22)

    assert chunks == ["Prima frase.", "Seconda frase.", "Terza frase."]
