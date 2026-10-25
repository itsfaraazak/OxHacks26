"""Flask app for the recording studio. Local only: bound to 127.0.0.1 by __main__."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, request, send_file

from fido.recording import BY_SLOT, TAKES

from . import analysis

CONSENT_STATEMENT = (
    "I agree that my voice recordings may be used in the Fido hackathon project, "
    "and to create an AI voice clone of my voice with ElevenLabs."
)
MAX_UPLOAD = 10 * 1024 * 1024
DEFAULT_DIR = Path(__file__).resolve().parent.parent / "recordings"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
TAKE_RE = re.compile(r"^take-(\d+)\.wav$")


def create_app(recordings_dir: Path | str | None = None) -> Flask:
    root = Path(recordings_dir or DEFAULT_DIR)
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD
    app.config["RECORDINGS_DIR"] = root

    # ---- helpers -------------------------------------------------------------------
    def read_consent() -> dict | None:
        try:
            c = json.loads((root / "consent.json").read_text("utf-8"))
            name, at = str(c["name"]).strip(), str(c["agreed_at"])
            datetime.fromisoformat(at)
            if name and c["statement"] == CONSENT_STATEMENT:
                return {"name": name, "agreed_at": at, "statement": CONSENT_STATEMENT}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def need_consent():
        if read_consent() is None:
            abort(403, "Consent is required before recording.")

    def atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def slot_dir(slot: str) -> Path:
        if slot not in BY_SLOT:  # the only source of folder names
            abort(404, "Unknown take.")
        return root / slot

    def take_numbers(slot: str) -> list[int]:
        d = slot_dir(slot)
        if not d.is_dir():
            return []
        return sorted(int(m.group(1)) for p in d.iterdir() if (m := TAKE_RE.match(p.name)))

    def room_rms() -> float | None:
        try:
            return analysis.analyse((root / "_room-tone.wav").read_bytes()).rms_dbfs
        except (OSError, analysis.WavError):
            return None

    def body_wav() -> tuple[bytes, analysis.Analysis]:
        data = request.get_data(cache=False)
        if not data:
            abort(400, "No audio received.")
        try:
            return data, analysis.analyse(data)
        except analysis.WavError as e:
            abort(400, str(e))

    # ---- security ------------------------------------------------------------------
    @app.before_request
    def local_only():
        host = urlsplit("//" + request.host).hostname
        if host is None or f"[{host}]" not in LOCAL_HOSTS and host not in LOCAL_HOSTS:
            abort(403, "Local access only.")  # also defeats DNS rebinding
        origin = request.headers.get("Origin")
        if origin is not None and urlsplit(origin).netloc != request.host:
            abort(403, "Cross-origin requests are not allowed.")

    @app.after_request
    def no_store(resp):
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def json_error(e):
        return jsonify(error=getattr(e, "description", str(e))), e.code

    # ---- pages and plan ------------------------------------------------------------
    @app.get("/")
    def index():
        return send_file(Path(app.static_folder) / "index.html")

    @app.get("/api/plan")
    def plan():
        takes = []
        for t in TAKES:
            nums = take_numbers(t.slot)
            takes.append({
                "slot": t.slot, "kind": t.kind, "text": t.text, "direction": t.direction,
                "seconds": list(t.seconds), "for_clone": t.for_clone,
                "takes": nums, "kept": (root / t.slot / "final.wav").is_file(),
            })
        return jsonify(takes=takes, consent=read_consent(), statement=CONSENT_STATEMENT)

    # ---- consent -------------------------------------------------------------------
    @app.post("/api/consent")
    def give_consent():
        j = request.get_json(silent=True) or {}
        name = str(j.get("name", "")).strip()[:100]
        if not name:
            abort(400, "Please enter your name.")
        if j.get("agreed") is not True:
            abort(400, "Please tick the box to agree.")
        c = {"name": name, "agreed_at": datetime.now(timezone.utc).isoformat(), "statement": CONSENT_STATEMENT}
        atomic_write(root / "consent.json", json.dumps(c, indent=2).encode())
        return jsonify(consent=c)

    @app.delete("/api/consent")
    def withdraw_consent():
        (root / "consent.json").unlink(missing_ok=True)
        return jsonify(consent=None)

    # ---- audio ---------------------------------------------------------------------
    @app.post("/api/room-tone")
    def room_tone():
        need_consent()
        data, a = body_wav()
        atomic_write(root / "_room-tone.wav", data)
        noisy = a.rms_dbfs > analysis.NOISY_ROOM_DBFS
        return jsonify(analysis=a.as_dict(), noisy=noisy)

    @app.post("/api/takes/<slot>")
    def upload(slot):
        need_consent()
        d = slot_dir(slot)
        data, a = body_wav()
        d.mkdir(parents=True, exist_ok=True)
        while True:  # exclusive create: never overwrite an earlier take
            n = max(take_numbers(slot), default=0) + 1
            try:
                with open(d / f"take-{n}.wav", "xb") as f:
                    f.write(data)
                break
            except FileExistsError:
                continue
        return jsonify(slot=slot, take=n, analysis=a.as_dict(), hints=analysis.hints(a, BY_SLOT[slot].seconds, room_rms()))

    @app.post("/api/keep")
    def keep():
        need_consent()
        j = request.get_json(silent=True) or {}
        slot, n = j.get("slot"), j.get("take")
        if not isinstance(slot, str) or type(n) is not int:
            abort(400, "Need a slot and a take number.")
        d = slot_dir(slot)
        if n not in take_numbers(slot):
            abort(404, "No such take.")
        atomic_write(d / "final.wav", (d / f"take-{n}.wav").read_bytes())
        (d / "final.txt").write_text(f"take-{n}.wav\n", "utf-8")
        return jsonify(slot=slot, take=n, kept=True)

    @app.get("/api/audio/<slot>/<which>")
    def audio(slot, which):
        need_consent()
        d = slot_dir(slot)
        if which == "final":
            path = d / "final.wav"
        elif which.isdigit() and int(which) in take_numbers(slot):
            path = d / f"take-{int(which)}.wav"
        else:
            abort(404, "No such recording.")
        if not path.is_file():
            abort(404, "No such recording.")
        return send_file(path, mimetype="audio/wav")

    return app
