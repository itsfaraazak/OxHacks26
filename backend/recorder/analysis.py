"""Pure WAV analysis for the recorder: peak, clipping, loudness, length, and kind hints.

Only 16-bit PCM WAV is accepted (that is what the page produces). Everything here is
advice; nothing blocks a take.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from array import array
from dataclasses import dataclass

FULL_SCALE = 32768.0
CLIP_LEVEL = 32767  # a sample at (or within one step of) full scale counts as clipped
QUIET_PEAK_DBFS = -30.0
CLIP_PERCENT = 0.5
NOISY_ROOM_DBFS = -45.0
MIN_RATE, MAX_RATE = 8000, 192000


class WavError(ValueError):
    """The bytes are not a WAV file we can use."""


@dataclass(frozen=True)
class Analysis:
    duration: float
    sample_rate: int
    peak_dbfs: float
    rms_dbfs: float
    clipping_percent: float

    def as_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def _db(x: float) -> float:
    """Amplitude (0..1) to dBFS; silence is -120 rather than minus infinity."""
    return 20 * math.log10(x) if x > 1e-6 else -120.0


def decode(data: bytes) -> tuple[array, int]:
    """Return (mono 16-bit samples, sample rate). Multi-channel input uses the first channel."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("not a WAV file")
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if w.getcomptype() != "NONE" or width != 2:
                raise WavError("only 16-bit PCM WAV is supported")
            if not MIN_RATE <= rate <= MAX_RATE:
                raise WavError("unsupported sample rate")
            raw = w.readframes(frames)
            if len(raw) < frames * width * channels:
                raise WavError("the WAV file is cut short")
    except (wave.Error, EOFError, struct.error) as e:
        raise WavError(f"unreadable WAV: {e}") from e
    samples = array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % (2 * channels)])
    if struct.pack("=H", 1) != struct.pack("<H", 1):  # big-endian host
        samples.byteswap()
    if channels > 1:
        samples = samples[::channels]
    if not samples:
        raise WavError("the recording is empty")
    return samples, rate


def analyse_samples(samples, rate: int) -> Analysis:
    n = len(samples)
    peak = max(max(samples), -min(samples))
    clipped = sum(1 for s in samples if s >= CLIP_LEVEL or s <= -CLIP_LEVEL - 1)
    rms = math.sqrt(sum(s * s for s in samples) / n)
    return Analysis(n / rate, rate, _db(peak / FULL_SCALE), _db(rms / FULL_SCALE), 100.0 * clipped / n)


def analyse(data: bytes) -> Analysis:
    return analyse_samples(*decode(data))


def hints(a: Analysis, seconds: tuple[float, float], room_rms_dbfs: float | None = None) -> list[dict]:
    """Kind, plain-language advice: a list of {"level": "warn"|"ok", "text": ...}."""
    out: list[dict] = []
    lo, hi = seconds
    if a.peak_dbfs < QUIET_PEAK_DBFS:
        out.append({"level": "warn", "text": "That was quite quiet. Move a little closer to the mic (about 15 cm) or speak up."})
    if a.clipping_percent > CLIP_PERCENT:
        out.append({"level": "warn", "text": "That was too loud and the sound is distorting. Back away a little or go a bit softer, then redo."})
    if a.duration < lo:
        out.append({"level": "warn", "text": f"That was short ({a.duration:.1f}s); aim for {lo:g} to {hi:g} seconds."})
    elif a.duration > hi:
        out.append({"level": "warn", "text": f"That ran long ({a.duration:.1f}s); aim for {lo:g} to {hi:g} seconds. Trim by redoing if there's a long pause."})
    if room_rms_dbfs is not None and room_rms_dbfs > NOISY_ROOM_DBFS:
        out.append({"level": "warn", "text": "Your room sounds a bit noisy. A quieter room, with fans and speakers off, gives a cleaner voice."})
    if not out:
        out.append({"level": "ok", "text": "Sounds good. Play it back and keep it if you like it."})
    return out
