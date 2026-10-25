"""PlatformIO extra script: `pio run -d firmware -t uploadaudio` flashes build/clips.bin to the
`audio` partition (offset must match partitions.csv). Run tools/prepare_clips.py first."""
import os
import sys

Import("env")  # noqa: F821  (provided by PlatformIO)

AUDIO_OFFSET = "0x270000"
BLOB = os.path.join(env.subst("$PROJECT_DIR"), "build", "clips.bin")  # noqa: F821


def upload_audio(target, source, env):  # noqa: ARG001
    if not os.path.exists(BLOB):
        sys.stderr.write("build/clips.bin missing: run `python3 firmware/tools/prepare_clips.py` first\n")
        env.Exit(1)
    esptool = os.path.join(env.PioPlatform().get_package_dir("tool-esptoolpy"), "esptool.py")
    port = env.subst("$UPLOAD_PORT")
    cmd = [env.subst("$PYTHONEXE"), esptool, "--chip", "esp32", "--baud", "460800"]
    if port:
        cmd += ["--port", port]
    cmd += ["write_flash", AUDIO_OFFSET, BLOB]
    env.Execute(" ".join('"%s"' % c if " " in c else c for c in cmd))


env.AddCustomTarget(  # noqa: F821
    "uploadaudio", None, upload_audio, title="Upload audio clips", description="Flash build/clips.bin to the audio partition"
)
