from __future__ import annotations

import logging

from llm_tts_api.app_logging import setup_logging


def test_setup_logging_uses_debug_level() -> None:
    level_name = setup_logging("DEBUG")

    assert level_name == "DEBUG"
    assert logging.getLogger("llm_tts_api").level == logging.DEBUG

