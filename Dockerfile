# syntax=docker/dockerfile:1.7
# Default image: CPU/MPS-friendly Python 3.13 slim. Multi-stage so the final
# image ships no compiler toolchain (NFR-OP-02). See Dockerfile.cuda for the
# GPU variant (OQ-5 — two image variants).

# Pinned by digest (multi-arch manifest list for python:3.13-slim) so the
# build is reproducible — bump via image-digest pinning policy, not by tag.
FROM python:3.13-slim@sha256:dc1546eefcbe8caaa1f004f16ab76b204b5e1dbd58ff81b899f21cd40541232f AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Compiler toolchain lives only in the builder stage; libsndfile1-dev gives
# soundfile its headers at build time. The runtime stage gets just libsndfile1.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libsndfile1-dev \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies into an isolated prefix so we can COPY just /opt/venv
# into the final image without dragging apt artifacts along.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip \
    && pip install .


FROM python:3.13-slim@sha256:dc1546eefcbe8caaa1f004f16ab76b204b5e1dbd58ff81b899f21cd40541232f AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_LOG_FORMAT=json \
    TTS_VOICE_MAP_FILE=/app/config/voice_map.container.json \
    TTS_VOICE_STORE_DIR=/var/lib/llm-tts-api/voices \
    TTS_SHUTDOWN_DRAIN_SECONDS=30

# tini reaps zombies and forwards SIGTERM unchanged so the lifespan shutdown
# handler (src/llm_tts_api/main.py — _drain_concurrency) gets a clean signal
# and honours TTS_SHUTDOWN_DRAIN_SECONDS (S-010, FR-HL-04).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libsndfile1 \
        tini \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 app \
    && useradd  --system --uid 1000 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app /var/lib/llm-tts-api/voices \
    && chown -R app:app /app /var/lib/llm-tts-api

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app src ./src
COPY --chown=app:app config ./config

USER app

# Mountable surfaces: voice metadata (FR-VM-01) and the voice blob store
# (FR-VS-01). Both are configuration-by-volume so operators swap them at
# deploy time without rebuilding.
VOLUME ["/var/lib/llm-tts-api/voices", "/app/config"]

EXPOSE 8010

# /health is the cheap, lock-free liveness probe (S-010, NFR-PF-02). Give
# uvicorn a generous start-period to load the default provider (S-003 warmup).
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:8010/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "llm_tts_api.main:app", \
     "--host", "0.0.0.0", "--port", "8010", \
     "--no-use-colors"]
