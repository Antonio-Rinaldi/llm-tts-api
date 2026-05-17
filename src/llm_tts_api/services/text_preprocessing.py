from __future__ import annotations

import re
from datetime import date

try:
    from num2words import num2words  # type: ignore[import-untyped]
except Exception:  # pragma: no cover - validated by dependency management
    num2words = None

_PUNCT_SPACING_RE = re.compile(r"\s+")
_DUPLICATE_DOTS_RE = re.compile(r"\.{2,}")
_DUPLICATE_PUNCT_RE = re.compile(r"([,;:!?])\1+")
_LINEBREAK_AFTER_PUNCT_RE = re.compile(r"([,;:])\s*\n+")
_STANDALONE_NUMBER_RE = re.compile(r"\b\d+\b")
_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")

_LANGUAGE_ALIASES: dict[str, str] = {
    "italian": "it",
    "it": "it",
    "english": "en",
    "en": "en",
    "spanish": "es",
    "es": "es",
    "french": "fr",
    "fr": "fr",
    "german": "de",
    "de": "de",
    "portuguese": "pt",
    "pt": "pt",
}

_MONTH_NAMES: dict[str, dict[int, str]] = {
    "it": {
        1: "gennaio",
        2: "febbraio",
        3: "marzo",
        4: "aprile",
        5: "maggio",
        6: "giugno",
        7: "luglio",
        8: "agosto",
        9: "settembre",
        10: "ottobre",
        11: "novembre",
        12: "dicembre",
    },
    "en": {
        1: "January",
        2: "February",
        3: "March",
        4: "April",
        5: "May",
        6: "June",
        7: "July",
        8: "August",
        9: "September",
        10: "October",
        11: "November",
        12: "December",
    },
}


def resolve_number_language(language: str) -> str:
    """Resolve a user-facing language label to a ``num2words`` language code."""
    normalized = (language or "").strip().lower()
    return _LANGUAGE_ALIASES.get(normalized, "en")


def clean_punctuation(text: str) -> str:
    """Normalize punctuation and spacing to help prosody prediction."""
    collapsed = _PUNCT_SPACING_RE.sub(" ", text)
    no_duplicate_dots = _DUPLICATE_DOTS_RE.sub(".", collapsed)
    no_duplicate_marks = _DUPLICATE_PUNCT_RE.sub(r"\1", no_duplicate_dots)
    normalized_lines = _LINEBREAK_AFTER_PUNCT_RE.sub(r"\1 ", no_duplicate_marks)
    return normalized_lines.strip()


def _number_to_words(value: int, lang_code: str) -> str:
    """Convert an integer to words using ``num2words`` with fallback behavior."""
    if num2words is None:
        return str(value)
    try:
        return str(num2words(value, lang=lang_code))
    except NotImplementedError:
        return str(num2words(value, lang="en"))


def _date_to_words(day: int, month: int, year: int, lang_code: str) -> str:
    """Convert date components into spoken text for supported languages."""
    month_name = _MONTH_NAMES.get(lang_code, {}).get(month)
    if month_name is None:
        return f"{day}/{month}/{year}"
    day_words = _number_to_words(day, lang_code)
    year_words = _number_to_words(year, lang_code)
    return f"{day_words} {month_name} {year_words}"


def expand_dates(text: str, lang_code: str) -> str:
    """Expand dates in ``dd/mm/yyyy`` format into spoken words."""

    def _replace(match: re.Match[str]) -> str:
        """Transform one date regex match into a language-aware spoken form."""
        day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        # Keep two-digit years in the 2000 range for modern books/transcripts.
        normalized_year = year + 2000 if year < 100 else year
        try:
            _ = date(normalized_year, month, day)
        except ValueError:
            return match.group(0)
        return _date_to_words(day, month, normalized_year, lang_code)

    return _DATE_RE.sub(_replace, text)


def expand_numbers(text: str, lang_code: str) -> str:
    """Expand standalone numeric tokens into spoken words."""
    return _STANDALONE_NUMBER_RE.sub(lambda m: _number_to_words(int(m.group(0)), lang_code), text)


def preprocess_for_tts(text: str, language: str) -> str:
    """Apply punctuation cleanup plus date/number expansion for TTS stability."""
    lang_code = resolve_number_language(language)
    return clean_punctuation(expand_numbers(expand_dates(text, lang_code), lang_code))


def _paragraph_sentences(paragraph: str) -> list[str]:
    """Split one paragraph into non-empty sentences."""
    return [
        sentence.strip() for sentence in _SENTENCE_SPLIT_RE.split(paragraph) if sentence.strip()
    ]


def split_text_semantic(text: str, max_chars: int, max_sentences_per_chunk: int = 2) -> list[str]:
    """Split text by sentence boundaries while respecting chunk size limits.

    Args:
        text: Input text to split.
        max_chars: Hard character limit for each output chunk.
        max_sentences_per_chunk: Maximum sentence count grouped in one chunk.

    Returns:
        Ordered chunks suitable for chunk-by-chunk TTS generation.
    """

    cleaned = text.strip()
    if not cleaned:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n{2,}", cleaned) if p.strip()]
    sentences = [
        sentence
        for paragraph in paragraphs
        for sentence in (_paragraph_sentences(paragraph) or [paragraph])
    ]

    chunks: list[str] = []
    current_sentences: list[str] = []

    def flush() -> None:
        """Push the current sentence accumulator into output chunks."""
        if current_sentences:
            chunks.append(" ".join(current_sentences).strip())

    for sentence in sentences:
        if len(sentence) > max_chars:
            flush()
            current_sentences = []
            chunks.extend(
                sentence[i : i + max_chars].strip()
                for i in range(0, len(sentence), max_chars)
                if sentence[i : i + max_chars].strip()
            )
            continue

        candidate = " ".join([*current_sentences, sentence]).strip()
        chunk_is_full = len(current_sentences) >= max_sentences_per_chunk
        over_limit = len(candidate) > max_chars
        if current_sentences and (chunk_is_full or over_limit):
            flush()
            current_sentences = [sentence]
            continue

        current_sentences = [*current_sentences, sentence]

    if current_sentences:
        chunks.append(" ".join(current_sentences).strip())

    return chunks
