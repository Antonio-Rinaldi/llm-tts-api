from __future__ import annotations

import io
import wave

import numpy as np


def _dtype_for_width(sample_width: int) -> np.dtype[np.signedinteger] | None:
    return {2: np.int16, 4: np.int32}.get(sample_width)


def normalize_wav_rms(wav_bytes: bytes, target_db: float) -> bytes:
    if not wav_bytes:
        return wav_bytes

    with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
        channels = reader.getnchannels()
        sample_width = reader.getsampwidth()
        frame_rate = reader.getframerate()
        comptype = reader.getcomptype()
        compname = reader.getcompname()
        raw_frames = reader.readframes(reader.getnframes())

    dtype = _dtype_for_width(sample_width)
    if dtype is None:
        return wav_bytes

    int_audio = np.frombuffer(raw_frames, dtype=dtype)
    if int_audio.size == 0:
        return wav_bytes

    scale = float(np.iinfo(dtype).max)
    float_audio = int_audio.astype(np.float32) / scale
    rms = float(np.sqrt(np.mean(np.square(float_audio))))
    if rms <= 1e-9:
        return wav_bytes

    target_rms = float(10 ** (target_db / 20.0))
    normalized = np.clip(float_audio * (target_rms / rms), -1.0, 1.0)
    out_frames = (normalized * scale).astype(dtype).tobytes()

    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(frame_rate)
        writer.setcomptype(comptype, compname)
        writer.writeframes(out_frames)

    return out.getvalue()

