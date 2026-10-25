#!/usr/bin/env python3
"""Turn Dheer's raw takes into the finished MP3 clips the app and firmware play.

No AI is involved. For every take in `fido.recording.TAKES` that has a
`recordings/<slot>/final.wav` this script:

1. cleans it (high-pass, trim silence, optional gentle denoise),
2. checks it (length, loudness, peak, clipping, noise floor) and gives a verdict,
3. loudness-normalises it, fades it, and encodes mono 22.05 kHz / 32 kbps CBR MP3
   (the same format `generate_audio.py` produces and the firmware expects),
4. writes `clips/<clip>.mp3` and `clips/ingredients/<ingredient>.mp3`,
5. updates `clips/manifest.json`,
6. writes cleaned, un-squashed samples for the voice clone to
   `recordings/clone-samples/<slot>.wav` (+ `index.json`).

Takes whose verdict is "redo" are never written (a bad take must not replace a
good clip). Nothing is ever deleted and takes without `final.wav` are never
touched. Needs `ffmpeg` and `ffprobe`; standard library only.

    python scripts/process_recordings.py --check        # validate only, write nothing
    python scripts/process_recordings.py                # process everything
    python scripts/process_recordings.py --only woof-1,dock-1 --force
"""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from fido.recording import TAKES, Take  # noqa: E402
from fido.speech import CAPTIONS, PLAYABLE, PLAYLIST  # noqa: E402

# --- constants --------------------------------------------------------------

LUFS_SPEECH = -16.0  # spoken lines
LUFS_DOG = -14.0  # woofs: a little louder so they cut through
TRUE_PEAK_DB = -1.5
LOUDNORM_LRA = 11.0

HIGHPASS_HZ = 70
DENOISE_FILTER = "afftdn=nr=8:tn=1"  # gentle; off unless --denoise
HEAD_SECONDS = 0.08  # silence kept before the first sound
TAIL_SECONDS = 0.20  # silence kept after the last sound
FADE_IN_SECONDS = 0.010
FADE_OUT_SECONDS = 0.060

OUTPUT_FORMAT = "mp3_22050_32"
MP3_RATE = 22050
MP3_KBPS = 32
CLONE_RATE = 44100
CLONE_PEAK_DB = -3.0
CLONE_MIN_SECONDS = 60.0  # ElevenLabs Instant Voice Cloning wants about 1-2+ minutes

WINDOW_SECONDS = 0.010  # level analysis window
FLOOR_DB = -120.0
CLIP_LEVEL = 0.999  # a sample at or above this fraction of full scale counts as clipped
FFMPEG_TIMEOUT = 120

FFMPEG_MISSING = (
    "ffmpeg (and ffprobe) are required but were not found. "
    "Install them (macOS: `brew install ffmpeg`; Debian/Ubuntu: `sudo apt install ffmpeg`) and run again."
)


class ProcessError(Exception):
    """A real error (missing tool, unreadable file): exit code 1."""


# --- ffmpeg helpers ---------------------------------------------------------


def find_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ProcessError(FFMPEG_MISSING)
    return path


def run_ffmpeg(ffmpeg: str, args: list[str], what: str, loglevel: str = "error") -> subprocess.CompletedProcess:
    cmd = [ffmpeg, "-y", "-hide_banner", "-nostdin", "-loglevel", loglevel, *args]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT, check=False)
    except FileNotFoundError:
        raise ProcessError(FFMPEG_MISSING) from None
    except subprocess.TimeoutExpired:
        raise ProcessError(f"ffmpeg timed out while {what}") from None
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()[-400:]
        raise ProcessError(f"ffmpeg failed while {what}: {detail or 'no details'}")
    return result


def parse_loudnorm_json(stderr: bytes) -> dict:
    text = stderr.decode("utf-8", "replace")
    start, end = text.rfind("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ProcessError("could not read ffmpeg's loudness measurement")
    try:
        return json.loads(text[start : end + 1])
    except ValueError:
        raise ProcessError("could not read ffmpeg's loudness measurement") from None


def _num(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def measure_loudness(ffmpeg: str, path: Path, target: float = LUFS_SPEECH) -> dict:
    """EBU R128 measurement of a file (loudnorm's analysis pass); values may be None."""
    flt = f"loudnorm=I={target}:TP={TRUE_PEAK_DB}:LRA={LOUDNORM_LRA}:print_format=json"
    result = run_ffmpeg(ffmpeg, ["-i", str(path), "-af", flt, "-f", "null", "-"], "measuring loudness", "info")
    data = parse_loudnorm_json(result.stderr)
    return {
        "lufs": _num(data.get("input_i")),
        "true_peak": _num(data.get("input_tp")),
        "lra": _num(data.get("input_lra")),
        "thresh": _num(data.get("input_thresh")),
        "offset": _num(data.get("target_offset")),
    }


def probe_duration(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, timeout=30, check=False,
        )  # fmt: skip
        return float(result.stdout.decode().strip()) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


# --- PCM helpers ------------------------------------------------------------


def read_pcm16(path: Path, ffmpeg: str) -> tuple[array.array, int]:
    """Samples (first channel, 16-bit) and sample rate. Odd WAV flavours are decoded by ffmpeg."""
    try:
        return _read_wave(path)
    except (wave.Error, EOFError):
        pass
    except OSError as exc:
        raise ProcessError(f"cannot read {path}: {exc}") from None
    with tempfile.TemporaryDirectory(prefix="fido-pcm-") as tmp:
        out = Path(tmp) / "pcm.wav"
        try:
            run_ffmpeg(ffmpeg, ["-i", str(path), "-ac", "1", "-c:a", "pcm_s16le", str(out)], f"decoding {path.name}")
            return _read_wave(out)
        except (ProcessError, wave.Error, EOFError, OSError):
            raise ProcessError(f"{path} is not a readable audio file") from None


def _read_wave(path: Path) -> tuple[array.array, int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise wave.Error("not 16-bit")
        channels, rate = wav.getnchannels(), wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) - len(frames) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if channels > 1:
        samples = samples[::channels]
    return samples, rate


def write_pcm16(path: Path, samples: array.array, rate: int) -> None:
    data = array.array("h", samples)
    if sys.byteorder == "big":
        data.byteswap()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(data.tobytes())


def to_db(linear: float) -> float:
    return max(FLOOR_DB, 20 * math.log10(linear)) if linear > 0 else FLOOR_DB


def window_levels(samples: array.array, rate: int) -> tuple[int, list[float]]:
    """RMS level in dBFS of consecutive 10 ms windows."""
    size = max(1, round(rate * WINDOW_SECONDS))
    levels = []
    for i in range(0, len(samples) - size + 1, size):
        chunk = samples[i : i + size]
        mean_sq = sum(x * x for x in chunk) / size
        levels.append(to_db(math.sqrt(mean_sq) / 32768))
    return size, levels


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return FLOOR_DB
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


# --- analysis ---------------------------------------------------------------


@dataclass
class Analysis:
    slot: str
    seconds: float = 0.0  # length after trimming
    lufs: float | None = None  # loudness of the cleaned take, before normalising
    peak_db: float = FLOOR_DB  # raw sample peak, dBFS
    clipped: int = 0
    clipped_fraction: float = 0.0
    noise_db: float = FLOOR_DB
    snr_db: float = 0.0
    speech: bool = True
    verdict: str = "ok"
    reasons: list[str] = field(default_factory=list)
    trimmed: array.array | None = None  # cleaned + trimmed samples at CLONE_RATE
    rms_db: float = FLOOR_DB


def judge(take: Take, a: Analysis) -> None:
    """Fill in `a.verdict` and `a.reasons` in plain English."""
    redo: list[str] = []
    warn: list[str] = []
    if not a.speech:
        redo.append("no speech detected (silence or only background noise): record it again")
    else:
        if a.peak_db < -36:
            redo.append(f"too quiet (peak {a.peak_db:.0f} dBFS): move closer to the microphone")
        elif a.peak_db < -24:
            warn.append(f"quiet (peak {a.peak_db:.0f} dBFS): boosting it will also boost the room noise")
        if a.clipped_fraction > 0.001:
            redo.append(f"clipping ({a.clipped} samples at full scale): too loud, lower the input level or back off")
        elif a.clipped >= 3:
            warn.append(f"clipping ({a.clipped} samples at full scale): a little too loud, you may hear crackle")
        if a.snr_db < 15:
            redo.append(f"noisy room: background noise is only {a.snr_db:.0f} dB below the voice")
        elif a.snr_db < 30:
            warn.append(
                f"noisy room: background noise ({a.noise_db:.0f} dBFS) is only {a.snr_db:.0f} dB below the voice; "
                "try somewhere quieter, or --denoise"
            )
        low, high = take.seconds
        if a.seconds < low * 0.5:
            redo.append(f"far too short ({a.seconds:.1f}s; expected {low:g}-{high:g}s): was it cut off?")
        elif a.seconds < low:
            warn.append(f"short ({a.seconds:.1f}s; expected {low:g}-{high:g}s)")
        elif a.seconds > high * 1.5:
            redo.append(f"far too long ({a.seconds:.1f}s; expected {low:g}-{high:g}s)")
        elif a.seconds > high:
            warn.append(f"long ({a.seconds:.1f}s; expected {low:g}-{high:g}s)")
    a.verdict = "redo" if redo else "warn" if warn else "ok"
    a.reasons = redo + warn


def analyse_take(take: Take, wav_path: Path, ffmpeg: str, denoise: bool, tmp: Path) -> Analysis:
    a = Analysis(take.slot)
    raw, _ = read_pcm16(wav_path, ffmpeg)
    if not raw:
        raise ProcessError(f"{wav_path} contains no audio")
    peak = max(max(raw), -min(raw))
    a.peak_db = to_db(peak / 32768)
    limit = CLIP_LEVEL * 32767
    a.clipped = sum(1 for x in raw if x >= limit or x <= -limit)
    a.clipped_fraction = a.clipped / len(raw)

    # High-pass (and optional denoise) at the clone rate; everything below works on this.
    clean_path = tmp / f"{take.slot}-clean.wav"
    chain = f"highpass=f={HIGHPASS_HZ}" + (f",{DENOISE_FILTER}" if denoise else "")
    run_ffmpeg(
        ffmpeg,
        ["-i", str(wav_path), "-af", chain, "-ac", "1", "-ar", str(CLONE_RATE), "-c:a", "pcm_s16le", str(clean_path)],
        f"cleaning {take.slot}",
    )
    clean, rate = _read_wave(clean_path)
    size, levels = window_levels(clean, rate)
    a.noise_db = percentile(levels, 0.05)
    loudest = max(levels, default=FLOOR_DB)
    threshold = max(a.noise_db + 12, -55.0)
    active = [i for i, level in enumerate(levels) if level > threshold]
    a.speech = bool(active) and loudest > -50 and loudest - a.noise_db >= 10
    if not a.speech:
        a.trimmed = clean
        a.seconds = len(clean) / rate
        judge(take, a)
        return a
    start = max(0, active[0] * size - round(HEAD_SECONDS * rate))
    end = min(len(clean), (active[-1] + 1) * size + round(TAIL_SECONDS * rate))
    a.trimmed = clean[start:end]
    a.seconds = len(a.trimmed) / rate
    a.snr_db = loudest - a.noise_db
    a.rms_db = to_db(math.sqrt(sum(x * x for x in a.trimmed) / len(a.trimmed)) / 32768)
    trimmed_path = tmp / f"{take.slot}-trimmed.wav"
    write_pcm16(trimmed_path, a.trimmed, rate)
    a.lufs = measure_loudness(ffmpeg, trimmed_path)["lufs"]
    judge(take, a)
    return a


# --- encoding ---------------------------------------------------------------


def loudness_target(take: Take) -> float:
    return LUFS_DOG if take.kind == "woof" else LUFS_SPEECH


def encode_clip(take: Take, a: Analysis, ffmpeg: str, tmp: Path) -> tuple[Path, float | None, str]:
    """Normalise + fade + encode the trimmed take. Returns (mp3 path, measured LUFS, filter chain used)."""
    target = loudness_target(take)
    trimmed_path = tmp / f"{take.slot}-trimmed.wav"
    measured = measure_loudness(ffmpeg, trimmed_path, target)
    if measured["lufs"] is not None and measured["thresh"] is not None:
        # Two-pass, linear: one constant gain, no pumping.
        norm = (
            f"loudnorm=I={target}:TP={TRUE_PEAK_DB}:LRA={LOUDNORM_LRA}"
            f":measured_I={measured['lufs']}:measured_LRA={measured['lra']}"
            f":measured_TP={measured['true_peak']}:measured_thresh={measured['thresh']}"
            f":offset={measured['offset']}:linear=true"
        )
    else:
        # Too short for an R128 reading: use plain RMS gain and a peak limiter.
        gain = target - a.rms_db
        norm = f"volume={gain:.2f}dB,alimiter=limit={10 ** (TRUE_PEAK_DB / 20):.3f}"
    fade_out_at = max(0.0, a.seconds - FADE_OUT_SECONDS)
    chain = (
        f"{norm},aresample={MP3_RATE},"
        f"afade=t=in:st=0:d={FADE_IN_SECONDS},afade=t=out:st={fade_out_at:.3f}:d={FADE_OUT_SECONDS}"
    )
    out = tmp / f"{take.slot}.mp3"
    run_ffmpeg(
        ffmpeg,
        [
            "-i", str(trimmed_path), "-af", chain, "-ac", "1", "-ar", str(MP3_RATE),
            "-c:a", "libmp3lame", "-b:a", f"{MP3_KBPS}k",
            "-map_metadata", "-1", "-id3v2_version", "0", "-write_xing", "0", "-f", "mp3", str(out),
        ],
        f"encoding {take.slot}",
    )  # fmt: skip
    if not out.is_file() or out.stat().st_size == 0:
        raise ProcessError(f"ffmpeg produced an empty file for {take.slot}")
    result = measure_loudness(ffmpeg, out, target)["lufs"]
    return out, result, chain


def clone_sample(a: Analysis) -> array.array:
    """Cleaned and trimmed, peak-normalised to CLONE_PEAK_DB (no loudness squashing)."""
    assert a.trimmed is not None
    peak = max(max(a.trimmed), -min(a.trimmed), 1)
    gain = 10 ** (CLONE_PEAK_DB / 20) * 32768 / peak
    return array.array("h", (max(-32768, min(32767, round(x * gain))) for x in a.trimmed))


# --- files ------------------------------------------------------------------


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)  # only ever our own half-written temp file


def write_json(path: Path, payload: dict) -> None:
    write_atomic(path, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def write_manifest(clips_dir: Path, recorded: set[str]) -> int:
    """Describe every clip the app can play (fido.speech.PLAYLIST) that is on disk.

    Existing entries and unknown fields are kept. `cue` lists the moments the clip plays at, since a
    clip may be reused for several.
    """
    manifest_path = clips_dir / "manifest.json"
    existing: dict = {}
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        except ValueError:
            existing = {}
    old = {e.get("name"): e for e in existing.get("clips", []) if isinstance(e, dict)}
    entries = []
    for name in PLAYABLE:
        path = clips_dir / f"{name}.mp3"
        if not path.is_file():
            continue
        data = path.read_bytes()
        cues = [cue for cue, clips in PLAYLIST.items() if name in clips]
        entry = dict(old.get(name, {}))
        entry.update({"name": name, "cue": cues[0], "cues": cues, "caption": CAPTIONS[name], "bytes": len(data)})
        duration = probe_duration(path) or len(data) * 8 / (MP3_KBPS * 1000)
        entry["duration_seconds"] = round(duration, 2)
        entry["sha256"] = hashlib.sha256(data).hexdigest()
        if name in recorded:
            entry["source"] = "recorded"
        entries.append(entry)
    manifest = {**existing, "output_format": existing.get("output_format", OUTPUT_FORMAT), "clips": entries}
    write_json(manifest_path, manifest)
    return len(entries)


def up_to_date(outputs: list[Path], source: Path) -> bool:
    return bool(outputs) and all(p.is_file() and p.stat().st_mtime >= source.stat().st_mtime for p in outputs)


def check_consent(recordings_dir: Path) -> None:
    consent = recordings_dir / "consent.json"
    problem = (
        f"{consent} is missing."
        if not consent.is_file()
        else None
    )
    if not problem:
        try:
            data = json.loads(consent.read_text(encoding="utf-8"))
            if not (isinstance(data, dict) and data.get("agreed_at")):
                problem = f"{consent} has no `agreed_at`."
        except ValueError:
            problem = f"{consent} is not valid JSON."
    if problem:
        raise ProcessError(
            f"{problem} The performer's consent must be recorded (by the recorder tool) before "
            "anything is processed, so nothing was done."
        )


# --- reporting --------------------------------------------------------------


def table(rows: list[list[str]]) -> str:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def report_row(take: Take, a: Analysis) -> list[str]:
    low, high = take.seconds
    lufs = f"{a.lufs:.1f}" if a.lufs is not None else "n/a"
    return [
        take.slot, f"{a.seconds:.1f}s ({low:g}-{high:g})", lufs, f"{a.peak_db:.1f}",
        f"{a.clipped}" if a.clipped else "0", f"{a.noise_db:.0f}", a.verdict.upper(),
    ]  # fmt: skip


# --- main -------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Clean, validate and encode the raw recordings into clips.")
    p.add_argument("--check", action="store_true", help="validate only; write nothing")
    p.add_argument("--dry-run", action="store_true", help="analyse and list what would be written; write nothing")
    p.add_argument("--only", help="comma-separated slots to process, e.g. woof-1,dock-1")
    p.add_argument("--force", action="store_true", help="re-process takes whose outputs are newer than final.wav")
    p.add_argument("--denoise", action="store_true", help=f"gentle noise reduction ({DENOISE_FILTER}); off by default")
    p.add_argument(
        "--accept-redo",
        metavar="SLOTS",
        help="comma-separated slots whose REDO verdict you accept on purpose (for example a woof that "
        "clips a little); they are processed anyway. Do not use it for a take you have not listened to.",
    )
    p.add_argument("--recordings-dir", type=Path, default=BACKEND / "recordings")
    p.add_argument("--clips-dir", type=Path, default=BACKEND / "clips")
    return p.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    recordings_dir: Path = args.recordings_dir
    clips_dir: Path = args.clips_dir
    check_consent(recordings_dir)
    ffmpeg = find_tool("ffmpeg")
    find_tool("ffprobe")
    read_only = args.check or args.dry_run

    takes = list(TAKES)
    if args.only:
        wanted = [s.strip() for s in args.only.split(",") if s.strip()]
        unknown = [s for s in wanted if s not in {t.slot for t in TAKES}]
        if unknown:
            raise ProcessError(f"unknown slot(s): {', '.join(unknown)}. Known: {', '.join(t.slot for t in TAKES)}")
        takes = [t for t in takes if t.slot in wanted]

    clone_dir = recordings_dir / "clone-samples"
    rows = [["take", "length (expected)", "LUFS", "peak dBFS", "clipped", "noise dBFS", "verdict"]]
    details: list[str] = []
    recorded: set[str] = set()
    clone_seconds: dict[str, float] = {}
    accepted = {s.strip() for s in (args.accept_redo or "").split(",") if s.strip()}
    unknown = accepted - {t.slot for t in takes}
    if unknown:
        raise SystemExit(f"error: --accept-redo names unknown take(s): {', '.join(sorted(unknown))}")
    missing: list[Take] = []
    with tempfile.TemporaryDirectory(prefix="fido-process-") as tmp_name:
        tmp = Path(tmp_name)
        for take in takes:
            wav_path = recordings_dir / take.slot / "final.wav"
            if not wav_path.is_file():
                missing.append(take)
                continue
            a = analyse_take(take, wav_path, ffmpeg, args.denoise, tmp)
            rows.append(report_row(take, a))
            for reason in a.reasons:
                details.append(f"  {take.slot}: {a.verdict.upper()}: {reason}")
            outputs = [clips_dir / f"{c}.mp3" for c in take.clips]
            if take.ingredient:
                outputs.append(clips_dir / "ingredients" / f"{take.ingredient}.mp3")
            clone_out = clone_dir / f"{take.slot}.wav"
            if a.verdict == "redo" and take.slot in accepted:
                details.append(f"  {take.slot}: REDO accepted on purpose (--accept-redo); processing it anyway")
            elif a.verdict == "redo":
                details.append(f"  {take.slot}: not written (record it again, or accept it with --accept-redo {take.slot})")
                continue
            if take.for_clone:
                clone_seconds[take.slot] = a.seconds
            if read_only:
                for path in outputs + ([clone_out] if take.for_clone else []):
                    details.append(f"  {take.slot}: would write {path}")
                continue
            if outputs and (args.force or not up_to_date(outputs, wav_path)):
                mp3, out_lufs, _chain = encode_clip(take, a, ffmpeg, tmp)
                data = mp3.read_bytes()
                for path in outputs:
                    write_atomic(path, data)
                    details.append(f"  {take.slot}: wrote {path} ({len(data)} bytes, {out_lufs if out_lufs is None else round(out_lufs, 1)} LUFS)")
                recorded.update(take.clips)
            elif outputs:
                details.append(f"  {take.slot}: clips are newer than final.wav; skipped (use --force)")
            if take.for_clone and (args.force or not up_to_date([clone_out], wav_path)):
                clone_dir.mkdir(parents=True, exist_ok=True)
                tmp_wav = clone_dir / f".{take.slot}.wav.tmp"
                try:
                    write_pcm16(tmp_wav, clone_sample(a), CLONE_RATE)
                    os.replace(tmp_wav, clone_out)
                finally:
                    tmp_wav.unlink(missing_ok=True)
                details.append(f"  {take.slot}: wrote {clone_out}")

    print(table(rows))
    if details:
        print()
        print("\n".join(details))
    for take in missing:
        print(f"  {take.slot}: no final.wav yet (skipped)")

    # Voice-clone material: count every take with a clone sample on disk, not just this run's.
    for take in TAKES:
        if take.for_clone and take.slot not in clone_seconds:
            existing = clone_dir / f"{take.slot}.wav"
            if existing.is_file():
                try:
                    with wave.open(str(existing), "rb") as w:
                        clone_seconds[take.slot] = w.getnframes() / w.getframerate()
                except (wave.Error, OSError):
                    pass
    total = sum(clone_seconds.values())
    print(f"\nVoice-clone material: {total:.0f}s from {len(clone_seconds)} takes (want {CLONE_MIN_SECONDS:.0f}s or more, ideally 1-2+ minutes).")
    if total < CLONE_MIN_SECONDS:
        todo = sorted(
            (t for t in TAKES if t.for_clone and t.slot not in clone_seconds),
            key=lambda t: -(t.seconds[0] + t.seconds[1]) / 2,
        )
        if todo:
            print("  Too little for a good clone. Recording these would add the most: " + ", ".join(
                f"{t.slot} (~{(t.seconds[0] + t.seconds[1]) / 2:.0f}s)" for t in todo[:4]))
        else:
            print("  Too little for a good clone. Re-record the passages (clone-*) a bit longer, or add more takes.")

    if not read_only:
        if recorded or (clips_dir / "manifest.json").is_file():
            count = write_manifest(clips_dir, recorded) if clips_dir.is_dir() else 0
            if count:
                print(f"Manifest: {count} clips described in {clips_dir / 'manifest.json'}")
        if clone_seconds:
            samples = [
                {"slot": slot, "file": f"{slot}.wav", "seconds": round(sec, 2)} for slot, sec in clone_seconds.items()
            ]
            write_json(
                clone_dir / "index.json",
                {"sample_rate": CLONE_RATE, "total_seconds": round(total, 2),
                 "minimum_seconds": CLONE_MIN_SECONDS, "enough": total >= CLONE_MIN_SECONDS, "samples": samples},
            )  # fmt: skip
    redo = [r[0] for r in rows[1:] if r[-1] == "REDO"]
    if redo:
        print(f"\nRe-record: {', '.join(redo)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except ProcessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
