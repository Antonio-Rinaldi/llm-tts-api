from __future__ import annotations

import logging


def setup_logging(level_name: str = "INFO") -> str:
    level = getattr(logging, (level_name or "INFO").upper(), logging.INFO)
    fmt = "%(asctime)s %(levelname)-5s %(name)s | %(message)s"

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=level, format=fmt)
    else:
        root_logger.setLevel(level)
        for handler in root_logger.handlers:
            handler.setLevel(level)

    # Keep uvicorn logs aligned with app verbosity.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "llm_tts_api"):
        logging.getLogger(name).setLevel(level)

    return logging.getLevelName(level)
