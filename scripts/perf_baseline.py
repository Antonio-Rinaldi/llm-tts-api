"""Measure synthesis latency on a running llm-tts-api instance.

Usage:
    # Start the service in another terminal:
    #   uv run uvicorn llm_tts_api.main:app --host 127.0.0.1 --port 8010
    # Then run this script against either endpoint:
    uv run python scripts/perf_baseline.py \\
        --url http://127.0.0.1:8010 \\
        --endpoint openai \\
        --voice alloy \\
        --model Qwen/Qwen3-TTS-12Hz-0.6B-Base \\
        --runs 11 \\
        --warmup 1 \\
        --input tests/perf/fixtures/baseline_input.txt

    # Drive the rich endpoint instead (S-021 T1):
    uv run python scripts/perf_baseline.py --endpoint rich ...

The ``--model`` value MUST be present in the server's allow-list
(``TTS_MLX_AUDIO_MODEL_ALLOWED`` etc.) or the request will be rejected with
400 ``invalid_request_error``. The default matches ``config.py``'s
``tts_mlx_audio_model_default``.

Produces a Markdown table on stdout that can be pasted into docs/perf/baseline.md.

S-002 / NFR-PF-01. Anchored to current pre-refactor code; S-021 re-runs against the
refactored path (both rich and OpenAI-adapter endpoints) and asserts <= +10%
regression on p50 and p95.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from urllib import error, request


def _read_input(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _git_sha(repo_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


_ENDPOINT_PATHS: dict[str, str] = {
    "openai": "/v1/audio/speech",
    "rich": "/v1/tts/synthesize",
}


def _one_request(
    url: str, model: str, voice: str, text: str, timeout: float, endpoint: str
) -> float:
    body = json.dumps(
        {"model": model, "input": text, "voice": voice, "response_format": "wav"}
    ).encode("utf-8")
    req = request.Request(
        url=f"{url.rstrip('/')}{_ENDPOINT_PATHS[endpoint]}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    with request.urlopen(req, timeout=timeout) as resp:
        # Drain the response body so we measure end-to-end synthesis,
        # not just time-to-first-byte.
        resp.read()
    return time.perf_counter() - start


def _percentile(samples: list[float], pct: float) -> float:
    # statistics.quantiles with n=100 gives 99 cut points; index pct-1.
    if not samples:
        return 0.0
    if len(samples) == 1:
        return samples[0]
    cuts = statistics.quantiles(sorted(samples), n=100, method="inclusive")
    return cuts[int(pct) - 1]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:8010", help="Service base URL")
    p.add_argument("--voice", default="alloy", help="Voice id (must exist in voice map)")
    p.add_argument(
        "--model",
        default="Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        help="TTS model id (must be in the server's TTS_*_MODEL_ALLOWED list)",
    )
    p.add_argument("--runs", type=int, default=11, help="Measured runs (after warmup)")
    p.add_argument("--warmup", type=int, default=1, help="Warmup runs (discarded)")
    p.add_argument(
        "--input",
        type=Path,
        default=Path("tests/perf/fixtures/baseline_input.txt"),
        help="Path to reference input text",
    )
    p.add_argument("--timeout", type=float, default=600.0, help="Per-request timeout seconds")
    p.add_argument(
        "--endpoint",
        choices=sorted(_ENDPOINT_PATHS.keys()),
        default="openai",
        help=(
            "Which surface to drive: 'openai' = POST /v1/audio/speech "
            "(S-002 anchor + S-021 T2); 'rich' = POST /v1/tts/synthesize (S-021 T1). "
            "Both share the synthesize_core pipeline (S-017) so numbers should match "
            "within measurement noise."
        ),
    )
    args = p.parse_args(argv)

    text = _read_input(args.input)
    char_count = len(text)
    repo_root = Path(__file__).resolve().parents[1]
    sha = _git_sha(repo_root)

    print(
        f"# perf-baseline run  sha={sha}  endpoint={args.endpoint}  "
        f"chars={char_count}  runs={args.runs}",
        file=sys.stderr,
    )
    print(f"# host={platform.platform()}  python={platform.python_version()}", file=sys.stderr)

    # Warmup: model load + cache prime
    for i in range(args.warmup):
        try:
            elapsed = _one_request(
                args.url, args.model, args.voice, text, args.timeout, args.endpoint
            )
            print(f"  warmup {i + 1}/{args.warmup}: {elapsed:.3f}s", file=sys.stderr)
        except error.URLError as exc:
            print(f"warmup request failed: {exc}", file=sys.stderr)
            return 1

    samples: list[float] = []
    for i in range(args.runs):
        try:
            elapsed = _one_request(
                args.url, args.model, args.voice, text, args.timeout, args.endpoint
            )
        except error.URLError as exc:
            print(f"run {i + 1} failed: {exc}", file=sys.stderr)
            return 1
        samples.append(elapsed)
        print(f"  run {i + 1}/{args.runs}: {elapsed:.3f}s", file=sys.stderr)

    p50 = _percentile(samples, 50)
    p95 = _percentile(samples, 95)
    p_min = min(samples)
    p_max = max(samples)

    # Markdown row ready to paste into docs/perf/baseline.md
    print()
    print(
        f"| {sha[:12]} | {args.endpoint} | {platform.platform()} | alloy | "
        f"{char_count} chars | {args.runs} | {p50 * 1000:.0f} ms | "
        f"{p95 * 1000:.0f} ms | {p_min * 1000:.0f} ms | {p_max * 1000:.0f} ms |"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
