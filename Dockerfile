FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TTS_VOICE_MAP_FILE=/app/config/voice_map.container.json

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY voices ./voices

RUN pip install --no-cache-dir .

EXPOSE 8010

CMD ["uvicorn", "llm_tts_api.main:app", "--host", "0.0.0.0", "--port", "8010"]



