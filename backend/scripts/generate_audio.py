#!/usr/bin/env python3
"""Generate Fido's audio clips offline with ElevenLabs.

Reads the fixed script in `fido/speech.py` and writes `clips/<clip>.mp3`, plus
`clips/voice.json` (the non-secret voice id) and `clips/manifest.json`. Only the fixed script text is sent to ElevenLabs: no
user input, ever. Standard library only.

    python scripts/generate_audio.py --dry-run     # cost, no network, no key needed
    python scripts/generate_audio.py               # generate whatever is missing
    python scripts/generate_audio.py --only nudge,run --force
    python scripts/generate_audio.py --audition    # hear candidate voices; saves no voice
    python scripts/generate_audio.py --choose <generated_voice_id>   # save the one you liked
    python scripts/generate_audio.py --clone --dry-run   # list the voice-clone samples; no network
    python scripts/generate_audio.py --clone             # create the Instant Voice Clone (needs consent)

Recorded woofs (`clips/ingredients/<name>.mp3`, see `fido.recording`) replace the
generated bark in "speech + bark" lines, and clips whose manifest entry says
`"source": "recorded"` are never overwritten unless `--overwrite-recorded`.

The API key is `ELEVENLABS_API_KEY` from the environment or `backend/.env`. It
is sent only in the `xi-api-key` header and is never printed.

Endpoints (docs verified 2026-05; they change, so re-check if a call fails):
  POST /v1/text-to-voice/design   https://elevenlabs.io/docs/api-reference/text-to-voice/design
  POST /v1/text-to-voice          https://elevenlabs.io/docs/api-reference/text-to-voice/create
  POST /v1/text-to-speech/{id}    https://elevenlabs.io/docs/api-reference/text-to-speech/convert
  POST /v1/sound-generation       https://elevenlabs.io/docs/api-reference/text-to-sound-effects/convert
  POST /v1/voices/add             https://elevenlabs.io/docs/api-reference/voices/ivc/create
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from fido.recording import BY_SLOT  # noqa: E402
from fido.speech import CLIPS, RECORDED_BARKS, SCRIPT, Line  # noqa: E402

API = "https://api.elevenlabs.io"
KEY_NAME = "ELEVENLABS_API_KEY"

# Eleven v3 understands audio tags such as [whimpers]; `eleven_ttv_v3` is the
# matching Voice Design model. Both ids are listed on the docs pages above.
TTS_MODEL = "eleven_v3"
# Eleven v4 is documented as the model with the most accurate Instant Voice Clones and the most
# reliable audio tags, so it is the default once voice.json says the voice is a clone.
# Override either with --model.
CLONE_TTS_MODEL = "eleven_v4"
DESIGN_MODEL = "eleven_ttv_v3"

# Smallest MP3 the API offers (22.05 kHz, 32 kbps): the clips end up in an
# ESP32's flash. Any `mp3_<rate>_<kbps>` format from the docs works via --format.
DEFAULT_FORMAT = "mp3_22050_32"

# Eleven v3 only has three stability positions: 0.0 "Creative", 0.5 "Natural",
# 1.0 "Robust". Creative follows audio tags best but can be erratic, so redo a
# single odd clip with `--only <cue> --force`.
VOICE_SETTINGS = {"stability": 0.0, "similarity_boost": 0.75, "use_speaker_boost": True}
# A clone follows audio tags less and drifts when stability is very low, so it gets a steadier,
# more similar setting. 0.5 is also one of v3's three positions, so it works with either model.
CLONE_VOICE_SETTINGS = {"stability": 0.5, "similarity_boost": 0.8, "style": 0.0, "use_speaker_boost": True}
CLONE_SOURCE = "instant-voice-clone"

SFX_SECONDS = 2.5
SFX_PROMPT_INFLUENCE = 0.5

# A barked line is one clip: the bark, a short pause, then the speech. Joining
# is done with ffmpeg (no Python dependency); only barked lines need it.
BARK_GAP_SECONDS = 0.15
FFMPEG_TIMEOUT_SECONDS = 60
FFMPEG_MISSING = (
    "ffmpeg is needed to join the bark and the speech into one clip but was not found. "
    "Install it (macOS: `brew install ffmpeg`; Debian/Ubuntu: `sudo apt install ffmpeg`) and run again."
)

# Documented prices: about 1 credit per TTS character, 40 per second of effect.
TTS_CREDITS_PER_CHAR = 1
SFX_CREDITS_PER_SECOND = 40

VOICE_NAME = "Fido"
# A big, deep, friendly cartoon dog. The first attempt asked for a young, squeaky puppy and came out
# too high-pitched to read as a dog, so this one asks explicitly for a low pitch and a gravelly,
# chesty character. Real barks come from the sound-effects model, not from this voice.
VOICE_DESCRIPTION = (
    "A big, friendly cartoon dog: deep, low-pitched, warm and slightly gravelly male voice with a "
    "chesty, playful, lovable character, like a goofy bulldog or retriever who can talk. "
    "Slow, relaxed, expressive delivery. Not squeaky, not high-pitched, not a puppy. "
    "Perfect audio quality."
)
# Fixed preview text (the API requires 100-1000 characters); it costs that many credits.
VOICE_PREVIEW_TEXT = (
    "Hey there, I'm Fido, your very own focus dog. I will sit right here, wag my tail, and cheer "
    "you on while you work. But if you wander off, I might just run away with your phone!"
)

CLONE_DEFAULT_NAME = "Fido (Dheer)"
CLONE_DESCRIPTION = "Fido the focus dog: a cartoon dog voice cloned from a consenting friend's recordings."
CLONE_MIN_SECONDS = 60  # docs: "at least 1 minute"; more than ~3 minutes helps little or hurts
CLONE_MAX_SECONDS = 180
CLONE_MIN_FILE_SECONDS = 5  # docs: each uploaded sample must be at least 5 s

RETRY_STATUSES = {409, 429, 500, 502, 503, 504}
RETRY_DELAY_SECONDS = 2.0
TIMEOUT_SECONDS = 120


class GenerateError(Exception):
    """A failure whose message is safe to print (never contains the key)."""


# --- configuration ---------------------------------------------------------


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse simple `KEY=VALUE` lines (optional `export`, quotes, `#` comments)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_api_key(environ: dict[str, str], env_file: Path) -> str:
    key = (environ.get(KEY_NAME) or parse_env_file(env_file).get(KEY_NAME) or "").strip()
    if not key:
        raise GenerateError(
            f"{KEY_NAME} is not set. Put `{KEY_NAME}=<your key>` in backend/.env (gitignored) "
            "or export it, then run again. Use --dry-run to see costs without a key."
        )
    return key


def bitrate_kbps(output_format: str) -> int | None:
    """Constant bitrate of an `mp3_<rate>_<kbps>` format, else None."""
    parts = output_format.split("_")
    if len(parts) == 3 and parts[0] == "mp3" and parts[2].isdigit():
        return int(parts[2])
    return None


def mp3_params(output_format: str) -> tuple[int, int]:
    """(sample rate Hz, kbps) of an `mp3_<rate>_<kbps>` format."""
    parts = output_format.split("_")
    if len(parts) == 3 and parts[0] == "mp3" and parts[1].isdigit() and parts[2].isdigit():
        return int(parts[1]), int(parts[2])
    raise GenerateError(f"can only join clips in an mp3_<rate>_<kbps> format, not {output_format!r}")


# --- plan and cost ----------------------------------------------------------


@dataclass(frozen=True)
class Item:
    name: str
    cue: str
    line: Line
    exists: bool
    bark_file: Path | None = None  # a recorded woof replaces the generated bark
    protected: bool = False  # exists and its manifest entry says source "recorded"

    @property
    def kind(self) -> str:
        return "speech" if self.line.voice is not None else "sfx"

    @property
    def has_bark(self) -> bool:
        """A spoken line with a real bark in front: two API calls, joined by ffmpeg."""
        return self.line.voice is not None and self.line.bark is not None

    @property
    def label(self) -> str:
        if self.has_bark:
            return "speech + recorded bark" if self.bark_file else "speech + bark"
        return self.kind

    @property
    def characters(self) -> int:
        return len(self.line.voice) if self.line.voice is not None else 0

    @property
    def credits(self) -> float:
        sfx = SFX_SECONDS * SFX_CREDITS_PER_SECOND
        if self.line.voice is None:
            return sfx
        generated_bark = self.has_bark and self.bark_file is None
        return self.characters * TTS_CREDITS_PER_CHAR + (sfx if generated_bark else 0)


def read_manifest(clips_dir: Path) -> dict:
    """The existing manifest.json, or {}. Unreadable is an error: it is never silently replaced."""
    path = clips_dir / "manifest.json"
    if not path.is_file():
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not isinstance(manifest.get("clips", []), list):
            raise TypeError
        return manifest
    except (ValueError, TypeError):
        raise GenerateError(f"{path} is not a valid manifest; fix or delete it.") from None


def manifest_sources(manifest: dict) -> dict[str, str]:
    return {
        e["name"]: e["source"]
        for e in manifest.get("clips", [])
        if isinstance(e, dict) and isinstance(e.get("name"), str) and isinstance(e.get("source"), str)
    }


def build_plan(
    clips_dir: Path, only: list[str] | None, force: bool, limit: int | None, overwrite_recorded: bool = False
) -> tuple[list[Item], list[Item]]:
    """Return (selected, to_generate).

    `selected` honours --only; `to_generate` drops clips that already exist
    (unless --force), drops recorded clips (unless --overwrite-recorded), and
    then applies --limit.
    """
    if only:
        unknown = [cue for cue in only if cue not in SCRIPT]
        if unknown:
            raise GenerateError(f"unknown cue(s): {', '.join(unknown)}. Cues: {', '.join(SCRIPT)}")
    sources = manifest_sources(read_manifest(clips_dir))
    selected = []
    for name, (cue, line) in CLIPS.items():
        if only and cue not in only:
            continue
        exists = (clips_dir / f"{name}.mp3").exists()
        ingredient = RECORDED_BARKS.get(line.bark) if line.bark else None
        bark_file = clips_dir / "ingredients" / f"{ingredient}.mp3" if ingredient else None
        if bark_file is not None and not bark_file.is_file():
            bark_file = None
        selected.append(
            Item(name, cue, line, exists, bark_file, protected=exists and sources.get(name) == "recorded")
        )
    todo = [
        item
        for item in selected
        if (force or not item.exists) and (overwrite_recorded or not item.protected)
    ]
    if limit is not None:
        todo = todo[: max(limit, 0)]
    return selected, todo


def totals(items: list[Item]) -> tuple[int, float]:
    return sum(i.characters for i in items), sum(i.credits for i in items)


def format_credits(credits: float) -> str:
    return f"{credits:,.0f}"


# --- HTTP -------------------------------------------------------------------


def _retry_delay(err: urllib.error.HTTPError) -> float:
    try:
        return min(max(float(err.headers.get("Retry-After", "")), 0.0), 30.0)
    except (TypeError, ValueError, AttributeError):
        return RETRY_DELAY_SECONDS


class Client:
    def __init__(self, api_key: str, base_url: str = API) -> None:
        self._key = api_key
        self._base = base_url

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "***")

    def _error_message(self, err: urllib.error.HTTPError) -> str:
        """The API's own message, never headers."""
        try:
            payload = json.loads(err.read().decode("utf-8", "replace"))
            detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("status") or detail
            message = detail if isinstance(detail, str) else json.dumps(detail)
        except Exception:  # not JSON, or unreadable
            message = err.reason if isinstance(err.reason, str) else "no details"
        return self._scrub(message)[:400]

    def post(self, path: str, body: dict, query: dict[str, str] | None = None) -> bytes:
        """POST JSON, retrying once on 409/429/5xx and network errors."""
        return self._send(path, json.dumps(body).encode("utf-8"), "application/json", query)

    def _send(
        self, path: str, data: bytes, content_type: str, query: dict[str, str] | None = None, retry: bool = True
    ) -> bytes:
        url = self._base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        for attempt in (1, 2) if retry else (2,):
            request = urllib.request.Request(
                url,
                data=data,
                method="POST",
                headers={"xi-api-key": self._key, "Content-Type": content_type, "Accept": "*/*"},
            )
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    return response.read()
            except urllib.error.HTTPError as err:
                if retry and err.code in RETRY_STATUSES and attempt == 1:
                    time.sleep(_retry_delay(err))
                    continue
                hint = " (check ELEVENLABS_API_KEY)" if err.code == 401 else ""
                raise GenerateError(f"POST {path} failed: HTTP {err.code}: {self._error_message(err)}{hint}") from None
            except urllib.error.URLError as err:
                if retry and attempt == 1:
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                raise GenerateError(f"POST {path} failed: {self._scrub(str(err.reason))}") from None
        raise AssertionError("unreachable")

    def post_json(self, path: str, body: dict) -> dict:
        return self._parse_json(path, self.post(path, body))

    def post_multipart(self, path: str, fields: dict[str, str], files: list[tuple[str, str, bytes]]) -> dict:
        """POST multipart/form-data once (no retry: a repeat could create a second voice)."""
        content_type, data = encode_multipart(fields, files)
        return self._parse_json(path, self._send(path, data, content_type, retry=False))

    @staticmethod
    def _parse_json(path: str, raw: bytes) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise GenerateError(f"POST {path} returned something that is not JSON") from None


def encode_multipart(fields: dict[str, str], files: list[tuple[str, str, bytes]]) -> tuple[str, bytes]:
    """Build a multipart/form-data body by hand. `files` are (field name, file name, content)."""
    boundary = "fido-" + hashlib.sha256(os.urandom(16)).hexdigest()[:24]
    out = bytearray()
    for name, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    for name, filename, content in files:
        out += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode()
        out += content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", bytes(out)


# --- files ------------------------------------------------------------------


def write_atomic(path: Path, data: bytes) -> None:
    """Write via a temp file and rename, so a failure never leaves a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path: Path, payload: dict) -> None:
    write_atomic(path, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


# --- voice ------------------------------------------------------------------


def design_voice(client: Client) -> str:
    """Voice Design, then save the first preview as a voice. Returns its voice_id."""
    designed = client.post_json(
        "/v1/text-to-voice/design",
        {"voice_description": VOICE_DESCRIPTION, "model_id": DESIGN_MODEL, "text": VOICE_PREVIEW_TEXT},
    )
    previews = designed.get("previews") or []
    if not previews or not previews[0].get("generated_voice_id"):
        raise GenerateError("Voice Design returned no previews")
    created = client.post_json(
        "/v1/text-to-voice",
        {
            "voice_name": VOICE_NAME,
            "voice_description": VOICE_DESCRIPTION,
            "generated_voice_id": previews[0]["generated_voice_id"],
        },
    )
    if not created.get("voice_id"):
        raise GenerateError("creating the voice returned no voice_id")
    return created["voice_id"]


def resolve_voice(client: Client, clips_dir: Path, voice_id: str | None, redesign: bool) -> str:
    """--voice-id wins; else the saved clips/voice.json; else design (and save) a new voice."""
    if voice_id:
        return voice_id
    voice_file = clips_dir / "voice.json"
    if voice_file.is_file() and not redesign:
        saved = json.loads(voice_file.read_text(encoding="utf-8")).get("voice_id")
        if saved:
            return saved
    if redesign:
        print("Designing a new voice; this uses another custom voice slot (free plan: 3).")
    else:
        print("Designing Fido's voice (first run; uses one custom voice slot, free plan: 3)...")
    new_id = design_voice(client)
    write_json(voice_file, {"voice_id": new_id, "voice_name": VOICE_NAME, "description": VOICE_DESCRIPTION})
    print(f"Saved the voice id to {voice_file.name} (not a secret; commit it).")
    return new_id


def read_voice(clips_dir: Path) -> dict:
    """The saved clips/voice.json, or {}."""
    path = clips_dir / "voice.json"
    try:
        voice = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        raise GenerateError(f"{path} is not valid JSON; fix or delete it.") from None
    return voice if isinstance(voice, dict) else {}


def voice_settings_for(source: str | None) -> dict:
    """Steadier settings for a cloned voice, the low-stability tag-following ones otherwise."""
    return dict(CLONE_VOICE_SETTINGS if source == CLONE_SOURCE else VOICE_SETTINGS)


# --- voice clone ------------------------------------------------------------
#
# `--clone` sends the cleaned spoken takes (written by process_recordings.py) to
# `POST /v1/voices/add` and saves the new voice id in clips/voice.json.


def read_consent(recordings_dir: Path) -> dict:
    path = recordings_dir / "consent.json"
    try:
        consent = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(consent, dict) or not consent.get("agreed_at"):
            raise ValueError
    except FileNotFoundError:
        raise GenerateError(
            f"not cloning: {path} not found. The voice actor must consent before their recordings are "
            "uploaded to ElevenLabs (record it with the recorder app)."
        ) from None
    except ValueError:
        raise GenerateError(f"not cloning: {path} has no `agreed_at`, so consent is not recorded.") from None
    return consent


def find_clone_samples(recordings_dir: Path) -> list[Path]:
    """clone-samples/*.wav, else recordings/<slot>/final.wav of takes made for the clone."""
    folder = recordings_dir / "clone-samples"
    if folder.is_dir():
        samples = sorted(folder.glob("*.wav"))
    else:
        samples = sorted(
            p
            for p in recordings_dir.glob("*/final.wav")
            if p.parent.name in BY_SLOT and BY_SLOT[p.parent.name].for_clone
        )
    if not samples:
        raise GenerateError(
            f"no clone samples: put cleaned .wav files in {folder} (run scripts/process_recordings.py) "
            "or accept takes under recordings/<slot>/final.wav."
        )
    return samples


def wav_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / w.getframerate()
    except (wave.Error, EOFError, OSError, ZeroDivisionError):
        return None


def run_clone(args: argparse.Namespace, environ: dict[str, str]) -> int:
    consent = read_consent(args.recordings_dir)
    samples = find_clone_samples(args.recordings_dir)
    durations = {p: wav_seconds(p) for p in samples}
    known = [d for d in durations.values() if d is not None]
    total = sum(known)
    size = sum(p.stat().st_size for p in samples)
    print(f"{len(samples)} clone sample(s), {total:.0f} s total, {size / 1024:.0f} KB, consent: {consent.get('name', '?')} "
          f"({consent['agreed_at']}):")
    for p in samples:
        d = durations[p]
        short = d is not None and d < CLONE_MIN_FILE_SECONDS
        print(f"  {p.name:<28} {'?' if d is None else f'{d:.1f}'} s{'  (under 5 s: ElevenLabs may reject it)' if short else ''}")
    if len(known) < len(samples):
        print("warning: some files are not readable WAVs, so their length is unknown.")
    if total < CLONE_MIN_SECONDS:
        print(f"warning: {total:.0f} s of audio is under the recommended {CLONE_MIN_SECONDS} s; the clone may be weak.")
    elif total > CLONE_MAX_SECONDS:
        print(f"warning: over {CLONE_MAX_SECONDS} s of audio; more than 2-3 minutes helps little and can hurt.")
    if args.dry_run:
        print(f"Dry run (no network calls, no key needed): would create voice {args.clone_name!r} "
              "(Instant Voice Cloning needs a paid ElevenLabs plan; uses one voice slot).")
        return 0

    client = Client(load_api_key(environ, args.env_file))
    fields = {
        "name": args.clone_name,
        "description": CLONE_DESCRIPTION,
        "remove_background_noise": "true" if args.remove_background_noise else "false",
    }
    created = client.post_multipart("/v1/voices/add", fields, [("files[]", p.name, p.read_bytes()) for p in samples])
    voice_id = created.get("voice_id")
    if not voice_id:
        raise GenerateError("creating the voice clone returned no voice_id")
    write_json(
        args.clips_dir / "voice.json",
        {
            "voice_id": voice_id,
            "voice_name": args.clone_name,
            "source": CLONE_SOURCE,
            "cloned_from": [p.name for p in samples],
            "consent_name": consent.get("name"),
            "consent_agreed_at": consent["agreed_at"],
        },
    )
    print(f"Created the voice clone; voice_id {voice_id} written to voice.json (not a secret; commit it).")
    if created.get("requires_verification"):
        print("note: ElevenLabs says this voice requires verification before it can be used.")
    print("Try it on a few clips, then the rest: generate_audio.py --limit 3   then   generate_audio.py")
    print("Clips generated earlier keep the old voice: regenerate them with `--force`.")
    return 0


# --- clips ------------------------------------------------------------------


def find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise GenerateError(FFMPEG_MISSING)
    return path


def join_mp3(parts: list[bytes], output_format: str) -> bytes:
    """Concatenate MP3s with ffmpeg: `parts[0]`, a short silence, then the rest.

    Every part is resampled to the target rate and mono, and the result is
    re-encoded at the target constant bitrate, so it is one ordinary MP3 (no ID3
    tag, no Xing frame) and its size still gives its duration. Temp files live in
    a temp directory, never in `clips/`.
    """
    rate, kbps = mp3_params(output_format)
    ffmpeg = find_ffmpeg()
    norm = f"aresample={rate},aformat=channel_layouts=mono"
    with tempfile.TemporaryDirectory(prefix="fido-join-") as tmp:
        inputs: list[str] = []
        for n, data in enumerate(parts):
            path = Path(tmp) / f"part{n}.mp3"
            path.write_bytes(data)
            inputs += ["-i", str(path)]
        out = Path(tmp) / "joined.mp3"
        graph = [f"[0:a]{norm},apad=pad_dur={BARK_GAP_SECONDS}[a0]"]
        graph += [f"[{n}:a]{norm}[a{n}]" for n in range(1, len(parts))]
        graph.append("".join(f"[a{n}]" for n in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[out]")
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *inputs,
            "-filter_complex", ";".join(graph), "-map", "[out]",
            "-c:a", "libmp3lame", "-b:a", f"{kbps}k", "-ar", str(rate), "-ac", "1",
            "-map_metadata", "-1", "-id3v2_version", "0", "-write_xing", "0", "-f", "mp3", str(out),
        ]  # fmt: skip
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=False)
        except FileNotFoundError:
            raise GenerateError(FFMPEG_MISSING) from None
        except subprocess.TimeoutExpired:
            raise GenerateError("ffmpeg timed out while joining a clip") from None
        if result.returncode != 0:
            detail = (result.stderr or b"").decode("utf-8", "replace").strip()[-400:]
            raise GenerateError(f"ffmpeg could not join a clip: {detail or 'no details'}")
        joined = out.read_bytes() if out.is_file() else b""
    if not joined:
        raise GenerateError("ffmpeg produced an empty file")
    return joined


def generate_clip(
    client: Client,
    item: Item,
    voice_id: str,
    output_format: str,
    model: str = TTS_MODEL,
    voice_settings: dict = VOICE_SETTINGS,
) -> bytes:
    query = {"output_format": output_format}
    if item.line.voice is not None:
        if item.has_bark:
            find_ffmpeg()  # fail before spending credits
            if item.bark_file is not None:
                bark = item.bark_file.read_bytes()  # a real recording: no sound-effects credits
            else:
                bark = client.post("/v1/sound-generation", sfx_body(item.line.bark), query)
        audio = client.post(
            f"/v1/text-to-speech/{urllib.parse.quote(voice_id, safe='')}",
            {"text": item.line.voice, "model_id": model, "voice_settings": voice_settings},
            query,
        )
        if item.has_bark:
            if not bark or not audio:
                raise GenerateError(f"{item.name}: the API returned an empty file")
            audio = join_mp3([bark, audio], output_format)
    else:
        audio = client.post("/v1/sound-generation", sfx_body(item.line.sfx), query)
    if not audio:
        raise GenerateError(f"{item.name}: the API returned an empty file")
    return audio


def sfx_body(prompt: str) -> dict:
    return {"text": prompt, "duration_seconds": SFX_SECONDS, "prompt_influence": SFX_PROMPT_INFLUENCE}


def probe_duration(path: Path) -> float | None:
    """Real duration via ffprobe when it is installed, else None."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
        )
        return float(result.stdout.decode().strip()) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def write_manifest(clips_dir: Path, output_format: str, generated: frozenset[str] = frozenset()) -> int:
    """Describe every scripted clip that exists on disk. Returns how many.

    The existing manifest is merged, not replaced: entries for other clips and
    keys this script does not know (e.g. the recording script's `source:
    "recorded"`) are kept. Clips in `generated` get `source: "generated"`.
    """
    kbps = bitrate_kbps(output_format)
    manifest = read_manifest(clips_dir)
    entries: list[dict] = [e for e in manifest.get("clips", []) if isinstance(e, dict)]
    by_name = {e.get("name"): e for e in entries}
    count = 0
    for name, (cue, line) in CLIPS.items():
        path = clips_dir / f"{name}.mp3"
        if not path.is_file():
            continue
        count += 1
        data = path.read_bytes()
        entry = by_name.get(name)
        if entry is None:
            entry = by_name[name] = {}
            entries.append(entry)
        entry.update({"name": name, "cue": cue, "caption": line.caption, "bytes": len(data)})
        # ffprobe's number when available, else the size estimate (constant bitrate,
        # which holds for API clips and for joined clips re-encoded by join_mp3).
        duration = probe_duration(path) or (len(data) * 8 / (kbps * 1000) if kbps else None)
        if duration:
            entry["duration_seconds"] = round(duration, 2)
        entry["sha256"] = hashlib.sha256(data).hexdigest()
        if name in generated:
            entry["source"] = "generated"
    write_json(clips_dir / "manifest.json", {**manifest, "output_format": output_format, "clips": entries})
    return count


# --- voice audition ---------------------------------------------------------
#
# `--audition` asks Voice Design for several candidate voices and saves every
# returned preview locally so a person can listen and pick; it saves nothing
# on ElevenLabs. `--choose <generated_voice_id>` then saves the one picked.

AUDITION_DESCRIPTIONS = {
    "bulldog": (
        "A deep, warm, gravelly, friendly cartoon bulldog: a big chunky dog with a low-pitched, "
        "booming, comical voice, speaking slowly and lovably. Perfect audio quality."
    ),
    "golden": (
        "A big goofy golden-retriever-like dog with a low, slow, lovable voice: a deep, mellow, "
        "easygoing male voice, endlessly happy and a little clumsy, like a cartoon sidekick. "
        "Perfect audio quality."
    ),
    "sheepdog": (
        "A gruff but soft-hearted old sheepdog: a very low-pitched, raspy, rumbling male voice, "
        "wise and gentle underneath, speaking at a calm, unhurried pace. Perfect audio quality."
    ),
}


def audition_credits(designs: int = len(AUDITION_DESCRIPTIONS)) -> int:
    """One design request per candidate, each charged once for the preview text."""
    return designs * len(VOICE_PREVIEW_TEXT)


def read_audition_index(auditions_dir: Path) -> dict:
    index_file = auditions_dir / "index.json"
    if not index_file.is_file():
        raise GenerateError(f"{index_file} not found. Run --audition first.")
    try:
        index = json.loads(index_file.read_text(encoding="utf-8"))
        if not isinstance(index["previews"], list):
            raise TypeError
        return index
    except (ValueError, KeyError, TypeError):
        raise GenerateError(f"{index_file} is not a valid audition index. Run --audition again.") from None


def decode_preview(candidate: str, number: int, preview: dict) -> tuple[bytes, str]:
    voice_id = preview.get("generated_voice_id")
    try:
        audio = base64.b64decode(preview.get("audio_base_64") or "", validate=True)
    except (binascii.Error, ValueError, TypeError):
        audio = b""
    if not voice_id or not audio:
        raise GenerateError(f"{candidate}: preview {number} has no usable generated_voice_id or audio")
    return audio, voice_id


def run_audition(args: argparse.Namespace, environ: dict[str, str]) -> int:
    auditions_dir = args.clips_dir / "auditions"
    per_design = len(VOICE_PREVIEW_TEXT)
    if args.dry_run:
        print(f"Dry run (no network calls, no key needed): would audition {len(AUDITION_DESCRIPTIONS)} candidate voices")
        for name in AUDITION_DESCRIPTIONS:
            print(f"  design   {name:<9} {per_design} chars, ~{per_design} credits  -> auditions/{name}-<n>.mp3")
        print(
            f"Total: ~{format_credits(audition_credits())} credits. "
            "Saves no voice (no slot used); voice.json is untouched."
        )
        return 0

    client = Client(load_api_key(environ, args.env_file))
    try:
        old_files = {p.get("file") for p in read_audition_index(auditions_dir)["previews"]}
    except GenerateError:
        old_files = set()
    entries: list[dict] = []
    designed_count = 0
    failure: GenerateError | None = None
    try:
        for name, description in AUDITION_DESCRIPTIONS.items():
            print(f"Designing {name} ...", end=" ", flush=True)
            designed = client.post_json(
                "/v1/text-to-voice/design",
                {"voice_description": description, "model_id": DESIGN_MODEL, "text": VOICE_PREVIEW_TEXT},
            )
            designed_count += 1
            previews = designed.get("previews") or []
            if not previews:
                raise GenerateError(f"{name}: Voice Design returned no previews")
            decoded = [decode_preview(name, n, preview) for n, preview in enumerate(previews, 1)]
            for n, (audio, voice_id) in enumerate(decoded, 1):
                file = f"{name}-{n}.mp3"
                write_atomic(auditions_dir / file, audio)
                entries.append(
                    {"file": file, "candidate": name, "description": description, "generated_voice_id": voice_id}
                )
            # Rewritten after every candidate, so a later failure keeps what was paid for.
            write_json(auditions_dir / "index.json", {"preview_text": VOICE_PREVIEW_TEXT, "previews": entries})
            print(f"ok, {len(decoded)} previews")
    except GenerateError as err:
        failure = err
        print("failed")

    if entries:
        # Drop files of an older audition that this one did not overwrite (index.json no longer lists them).
        for file in old_files - {e["file"] for e in entries}:
            if isinstance(file, str) and Path(file).name == file:
                (auditions_dir / file).unlink(missing_ok=True)
        print(f"\nSaved {len(entries)} previews in {auditions_dir}. Listen with (macOS):")
        for entry in entries:
            print(f"  afplay {auditions_dir / entry['file']}    # {entry['candidate']}")
    print(
        f"\nCost: ~{format_credits(audition_credits(designed_count))} credits (estimate: {per_design} chars "
        f"per design request, {designed_count} request(s); charged once however many previews come back)."
    )
    if entries:
        print("Nothing was saved on ElevenLabs. Pick one with: generate_audio.py --choose <generated_voice_id>")
    if failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1
    return 0


def run_choose(args: argparse.Namespace, environ: dict[str, str]) -> int:
    auditions_dir = args.clips_dir / "auditions"
    entry = next(
        (p for p in read_audition_index(auditions_dir)["previews"] if p.get("generated_voice_id") == args.choose), None
    )
    if entry is None:
        raise GenerateError(f"{args.choose!r} is not in {auditions_dir / 'index.json'}. Run --audition for fresh ids.")
    voice_file = args.clips_dir / "voice.json"
    replaces = " (replaces the current voice.json)" if voice_file.is_file() else ""
    if args.dry_run:
        print("Dry run (no network calls, no key needed):")
        print(f"  would save {entry['file']} as voice {VOICE_NAME!r} and write {voice_file.name}{replaces}.")
        print("  uses one custom voice slot (free plan: 3); no credits.")
        return 0

    client = Client(load_api_key(environ, args.env_file))
    created = client.post_json(
        "/v1/text-to-voice",
        {"voice_name": VOICE_NAME, "voice_description": entry["description"], "generated_voice_id": args.choose},
    )
    if not created.get("voice_id"):
        raise GenerateError("creating the voice returned no voice_id")
    write_json(
        voice_file, {"voice_id": created["voice_id"], "voice_name": VOICE_NAME, "description": entry["description"]}
    )
    print(f"Saved {entry['file']} as a new voice; voice_id {created['voice_id']} written to {voice_file.name}{replaces}.")
    print("Existing clips keep the old voice: regenerate them with `generate_audio.py --force`.")
    return 0


# --- command line -----------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Fido's audio clips with ElevenLabs.")
    p.add_argument("--dry-run", action="store_true", help="print what would be generated and its cost; no network")
    p.add_argument("--force", action="store_true", help="regenerate clips that already exist")
    p.add_argument("--only", help="comma-separated cues, e.g. nudge,run")
    p.add_argument("--limit", type=int, help="generate at most N clips (after skipping existing ones)")
    p.add_argument("--voice-id", help="use this ElevenLabs voice id (overrides clips/voice.json)")
    p.add_argument("--redesign", action="store_true", help="design a new voice (uses another voice slot)")
    p.add_argument(
        "--format", default=DEFAULT_FORMAT, dest="output_format", help=f"MP3 output format (default {DEFAULT_FORMAT})"
    )
    p.add_argument("--audition", action="store_true", help="save every preview of a few candidate voices to clips/auditions/ (no voice is saved)")
    p.add_argument("--choose", metavar="GENERATED_VOICE_ID", help="save this audition preview as Fido's voice (writes clips/voice.json)")
    p.add_argument("--clone", action="store_true", help="create an Instant Voice Clone from recordings/clone-samples/*.wav (needs recordings/consent.json and a paid plan); writes clips/voice.json")
    p.add_argument("--clone-name", default=CLONE_DEFAULT_NAME, help=f"name of the cloned voice (default {CLONE_DEFAULT_NAME!r})")
    p.add_argument("--remove-background-noise", action="store_true", help="clean the clone samples with ElevenLabs' isolation model (default off: it can make clean samples worse)")
    p.add_argument("--model", help=f"text-to-speech model id (default {TTS_MODEL}; {CLONE_TTS_MODEL} when voice.json is an Instant Voice Clone)")
    p.add_argument("--overwrite-recorded", action="store_true", help="let --force replace clips whose manifest entry says source=recorded (skipped by default)")
    p.add_argument("--recordings-dir", type=Path, default=BACKEND / "recordings", help=argparse.SUPPRESS)
    p.add_argument("--clips-dir", type=Path, default=BACKEND / "clips", help=argparse.SUPPRESS)
    p.add_argument("--env-file", type=Path, default=BACKEND / ".env", help=argparse.SUPPRESS)
    return p.parse_args(argv)


def print_plan(selected: list[Item], todo: list[Item], voice_credits: int) -> None:
    todo_names = {i.name for i in todo}
    for item in selected:
        if item.name in todo_names:
            what = f"{item.characters} chars" if item.kind == "speech" else f"{SFX_SECONDS:g}s effect"
            if item.has_bark:
                what += " + recorded bark" if item.bark_file else f" + {SFX_SECONDS:g}s bark"
            credits = format_credits(item.credits)
            print(f"  generate {item.name:<10} {item.label:<13} {what:<24} ~{credits:>4} credits  {item.line.caption}")
        else:
            why = (
                "recorded clip; --force --overwrite-recorded replaces it"
                if item.protected
                else "exists, or beyond --limit; --force regenerates"
            )
            print(f"  skip     {item.name:<10} ({why})")
    if voice_credits:
        print(f"  voice    design preview: {voice_credits} chars, ~{voice_credits} credits (first run only)")


def run(args: argparse.Namespace, environ: dict[str, str]) -> int:
    only = [c.strip() for c in args.only.split(",") if c.strip()] if args.only else None
    selected, todo = build_plan(args.clips_dir, only, args.force, args.limit, args.overwrite_recorded)
    voice_source = None if args.voice_id else read_voice(args.clips_dir).get("source")
    model = args.model or (CLONE_TTS_MODEL if voice_source == CLONE_SOURCE else TTS_MODEL)
    voice_settings = voice_settings_for(voice_source)
    chars, credits = totals(todo)
    speech_needed = any(i.kind == "speech" for i in todo)
    voice_saved = (args.clips_dir / "voice.json").is_file() and not args.redesign
    needs_design = speech_needed and not args.voice_id and not voice_saved
    voice_credits = len(VOICE_PREVIEW_TEXT) if needs_design else 0

    if args.dry_run:
        print(f"Dry run (no network calls, no key needed): {len(todo)} of {len(selected)} clips, "
              f"format {args.output_format}, model {model}")
        print_plan(selected, todo, voice_credits)
        print(
            f"Total: {chars} characters, ~{format_credits(credits + voice_credits)} credits "
            f"({format_credits(credits)} clips + {format_credits(voice_credits)} voice design)"
        )
        return 0

    if any(i.has_bark for i in todo):
        find_ffmpeg()  # before the key or the voice: nothing is spent if it is missing
    client = Client(load_api_key(environ, args.env_file))
    failure: GenerateError | None = None
    done: list[Item] = []
    try:
        voice_id = resolve_voice(client, args.clips_dir, args.voice_id, args.redesign) if speech_needed else ""
        for n, item in enumerate(todo, 1):
            print(f"[{n}/{len(todo)}] {item.name} ({item.label}) ...", end=" ", flush=True)
            try:
                audio = generate_clip(client, item, voice_id, args.output_format, model, voice_settings)
                write_atomic(args.clips_dir / f"{item.name}.mp3", audio)
            except GenerateError:
                print("failed")
                raise
            done.append(item)
            print(f"ok, {len(audio) / 1024:.1f} KB")
    except GenerateError as err:
        failure = err

    count = write_manifest(args.clips_dir, args.output_format, frozenset(i.name for i in done))
    done_chars, done_credits = totals(done)
    print(
        f"Generated {len(done)} clip(s): {done_chars} characters, ~{format_credits(done_credits + (voice_credits if not failure else 0))} "
        f"credits (estimate). manifest.json lists {count} clip(s)."
    )
    if failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> int:
    args = parse_args(argv)
    environ = dict(os.environ if environ is None else environ)
    try:
        if sum(map(bool, (args.audition, args.choose, args.clone))) > 1:
            raise GenerateError("use only one of --audition, --choose and --clone")
        if args.clone:
            return run_clone(args, environ)
        if args.audition:
            return run_audition(args, environ)
        if args.choose:
            return run_choose(args, environ)
        return run(args, environ)
    except GenerateError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
