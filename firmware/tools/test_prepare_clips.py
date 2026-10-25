"""Stdlib-only CLI regression tests; no ffmpeg or hardware required."""
import contextlib
import importlib.util
import io
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "prepare_clips", Path(__file__).with_name("prepare_clips.py")
)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


class PrepareClipsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clips = self.root / "custom-clips"
        self.clips.mkdir()
        self.out = self.root / "clips.bin"
        self.out.write_bytes(b"previous image")
        # Import the actual runtime contract, not the generated-voice CLIPS.
        sys.path.insert(0, str(tool.ROOT / "backend"))
        self.addCleanup(sys.path.pop, 0)
        from fido.speech import PLAYABLE
        self.names = PLAYABLE
        for name in self.names:
            (self.clips / f"{name}.mp3").touch()

    def run_cli(self, *options):
        argv = ["prepare_clips.py", "--clips", str(self.clips), "--out", str(self.out), *options]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(tool, "mp3_to_pcm", return_value=[0, 100, -100]) as decode, \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            result = tool.main()
        return result, decode

    def test_selects_exactly_playable_and_excludes_extra_mp3s(self):
        for name in ("run-1", "unused", "x" * 30):
            (self.clips / f"{name}.mp3").touch()
        result, decode = self.run_cli()
        self.assertEqual(result, 0)
        blob = self.out.read_bytes()
        self.assertEqual(blob[:8], b"FIDOCLP1")
        rate, count, reserved = struct.unpack_from("<IHH", blob, 8)
        self.assertEqual((rate, count, reserved), (16000, len(self.names), 0))
        names = []
        offset = 16 + 32 * count
        for i in range(count):
            name, start, samples = struct.unpack_from("<24sII", blob, 16 + 32 * i)
            names.append(name.rstrip(b"\0").decode("ascii"))
            self.assertEqual((start, samples), (offset, 3))
            self.assertEqual(blob[start:start + 2], tool.adpcm_encode([0, 100, -100]))
            offset += 2
        self.assertEqual(set(names), set(self.names))
        self.assertEqual(len(blob), offset)
        self.assertEqual(
            {call.args[0] for call in decode.call_args_list},
            {self.clips / f"{name}.mp3" for name in self.names},
        )

    def test_missing_required_clip_preserves_output(self):
        (self.clips / f"{self.names[-1]}.mp3").unlink()
        result, decode = self.run_cli()
        self.assertNotEqual(result, 0)
        decode.assert_not_called()
        self.assertEqual(self.out.read_bytes(), b"previous image")

    def test_overflow_preserves_output(self):
        # All but one clip fit: writing a reduced image must still fail.
        budget = 16 + 34 * len(self.names) - 1
        result, _ = self.run_cli("--budget", str(budget))
        self.assertNotEqual(result, 0)
        self.assertEqual(self.out.read_bytes(), b"previous image")

    def test_overflow_does_not_create_output(self):
        self.out.unlink()
        result, _ = self.run_cli("--budget", "1")
        self.assertNotEqual(result, 0)
        self.assertFalse(self.out.exists())

    def test_exact_budget_succeeds(self):
        budget = 16 + 34 * len(self.names)
        result, _ = self.run_cli("--budget", str(budget))
        self.assertEqual(result, 0)
        self.assertEqual(self.out.stat().st_size, budget)

    def test_invalid_required_names_preserve_output(self):
        for name in ("x" * 24, "é" * 12, "../escape", "", "bad\0name", "bad/name"):
            with self.subTest(name=name), mock.patch.object(tool, "PLAYABLE", (name,)):
                result, decode = self.run_cli()
                self.assertNotEqual(result, 0)
                decode.assert_not_called()
                self.assertEqual(self.out.read_bytes(), b"previous image")

    def test_invalid_name_cannot_corrupt_blob_table(self):
        with self.assertRaises(ValueError):
            tool.build_blob({"x" * 24: b"\0"}, {"x" * 24: 2}, 16000)

    def test_conversion_failure_preserves_output(self):
        with mock.patch.object(tool.subprocess, "run", side_effect=FileNotFoundError("ffmpeg")):
            argv = ["prepare_clips.py", "--clips", str(self.clips), "--out", str(self.out)]
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(tool.main(), 0)
        self.assertEqual(self.out.read_bytes(), b"previous image")

    def test_failed_replace_preserves_output_and_removes_staged_file(self):
        with mock.patch.object(Path, "replace", side_effect=OSError("replace failed")):
            result, _ = self.run_cli()
        self.assertNotEqual(result, 0)
        self.assertEqual(self.out.read_bytes(), b"previous image")
        self.assertEqual(set(self.root.iterdir()), {self.clips, self.out})


if __name__ == "__main__":
    unittest.main()
