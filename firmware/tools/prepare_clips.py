#!/usr/bin/env python3
"""Convert fido.speech.PLAYABLE recordings into the firmware flash blob.

    python3 firmware/tools/prepare_clips.py            # -> firmware/build/clips.bin
    pio run -d firmware -t uploadaudio                 # writes it to the `audio` partition

Why not play MP3 on the ESP32? The A2DP source library only accepts 44.1 kHz 16-bit stereo
PCM (it SBC-encodes that itself). Decoding MP3 on the chip would need a decoder library, extra
RAM and CPU on the core that also serves Bluetooth. IMA ADPCM costs ~20 lines of integer maths
per sample and 4 bits/sample, so it is converted here, on the laptop, once. The firmware
decodes it and upsamples to 44.1 kHz stereo in the audio callback (see clip_player.cpp).

Blob format (little endian), shared with lib/fido_core/src/clip_store.h:
    header  "FIDOCLP1", u32 sample_rate, u16 clip_count, u16 0              (16 bytes)
    entry   char name[24], u32 offset, u32 samples                          (32 bytes each)
    data    mono IMA ADPCM, 4 bits/sample, low nibble first, predictor/index start at 0

Only required recordings are read from --clips (default backend/clips); extra MP3s
are ignored. Missing recordings, invalid names, or budget overflow fail without
replacing the output. The complete image is written atomically, never truncated
to fit. Needs ffmpeg on PATH. Pure Python otherwise.
"""
from __future__ import annotations

import argparse
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PARTITION_BYTES = 0x180000  # keep in sync with partitions.csv (`audio`)

# speech is stdlib-only; do not import the backend app or audio generator.
sys.path.insert(0, str(ROOT / "backend"))
from fido.speech import PLAYABLE

STEP = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73,
    80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494,
    544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499,
    2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487,
    12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
]
INDEX = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]


def adpcm_encode(samples: list[int]) -> bytes:
    """IMA ADPCM, matching lib/fido_core/src/adpcm.cpp bit for bit."""
    pred, idx = 0, 0
    nibbles = []
    for s in samples:
        step = STEP[idx]
        diff = s - pred
        nib = 0
        if diff < 0:
            nib = 8
            diff = -diff
        delta = step >> 3
        if diff >= step:
            nib |= 4
            diff -= step
            delta += step
        step >>= 1
        if diff >= step:
            nib |= 2
            diff -= step
            delta += step
        step >>= 1
        if diff >= step:
            nib |= 1
            delta += step
        # Reconstruct exactly as the decoder does, so both sides stay in lock step.
        step = STEP[idx]
        d = step >> 3
        if nib & 1:
            d += step >> 2
        if nib & 2:
            d += step >> 1
        if nib & 4:
            d += step
        pred += -d if nib & 8 else d
        pred = max(-32768, min(32767, pred))
        idx = max(0, min(88, idx + INDEX[nib]))
        nibbles.append(nib)
    if len(nibbles) % 2:
        nibbles.append(0)
    return bytes(nibbles[i] | (nibbles[i + 1] << 4) for i in range(0, len(nibbles), 2))


def adpcm_decode(data: bytes, count: int) -> list[int]:
    pred, idx, out = 0, 0, []
    for i in range(count):
        nib = (data[i >> 1] >> 4) if i & 1 else (data[i >> 1] & 15)
        step = STEP[idx]
        d = step >> 3
        if nib & 1:
            d += step >> 2
        if nib & 2:
            d += step >> 1
        if nib & 4:
            d += step
        pred += -d if nib & 8 else d
        pred = max(-32768, min(32767, pred))
        idx = max(0, min(88, idx + INDEX[nib]))
        out.append(pred)
    return out


def mp3_to_pcm(path: Path, rate: int, gain_db: float) -> list[int]:
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(rate)]
    # Small speakers: roll off rumble, then normalise peak to about -1 dBFS.
    cmd += ["-af", f"highpass=f=100,volume={gain_db}dB,alimiter=limit=0.89", "-f", "s16le", "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return list(struct.unpack(f"<{len(raw) // 2}h", raw))


def validate_name(name: str) -> None:
    """Names must be safe file stems and fit the firmware's NUL-terminated field."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,23}", name):
        raise ValueError(f"invalid clip name {name!r}: use 1-23 ASCII letters, digits, '_' or '-'")


def required_mp3s(directory: Path) -> list[Path]:
    paths = []
    for name in PLAYABLE:
        validate_name(name)
        paths.append(directory / f"{name}.mp3")
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"missing required recordings: {', '.join(missing)}")
    return paths


def build_blob(clips: dict[str, bytes], counts: dict[str, int], rate: int) -> bytes:
    head = b"FIDOCLP1" + struct.pack("<IHH", rate, len(clips), 0)
    offset = 16 + 32 * len(clips)
    table, data = b"", b""
    for name, enc in clips.items():
        validate_name(name)
        table += name.encode().ljust(24, b"\0") + struct.pack("<II", offset + len(data), counts[name])
        data += enc
    return head + table + data


def write_atomic(path: Path, blob: bytes) -> None:
    """Stage beside the destination so replacement is on the same filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as out:
            staged = Path(out.name)
            out.write(blob)
        staged.replace(path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clips", type=Path, default=ROOT / "backend" / "clips")
    ap.add_argument("--out", type=Path, default=ROOT / "firmware" / "build" / "clips.bin")
    ap.add_argument("--rate", type=int, default=16000, help="stored sample rate (default 16000)")
    ap.add_argument("--budget", type=int, default=PARTITION_BYTES, help="max blob bytes (the partition size)")
    ap.add_argument("--gain-db", type=float, default=0.0)
    args = ap.parse_args()

    try:
        mp3s = required_mp3s(args.clips)
        clips, counts = {}, {}
        used = 16 + 32 * len(mp3s)
        if used > args.budget:
            raise ValueError(f"required clip table exceeds {args.budget}-byte budget")
        for mp3 in mp3s:
            pcm = mp3_to_pcm(mp3, args.rate, args.gain_db)
            enc = adpcm_encode(pcm)
            used += len(enc)
            if used > args.budget:
                raise ValueError(f"required clips exceed {args.budget}-byte budget at {mp3.stem}; "
                                 "no image written (try a lower --rate)")
            clips[mp3.stem], counts[mp3.stem] = enc, len(pcm)
            print(f"  {mp3.stem:<12} {len(pcm) / args.rate:5.1f} s  {len(enc):>7} bytes")

        blob = build_blob(clips, counts, args.rate)
        write_atomic(args.out, blob)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"cannot prepare clips: {exc}", file=sys.stderr)
        return 1

    seconds = sum(counts.values()) / args.rate
    print(f"{len(clips)} clips, {seconds:.1f} s of audio at {args.rate} Hz -> {len(blob)} bytes "
          f"({100 * len(blob) / args.budget:.0f}% of the {args.budget}-byte audio partition) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
