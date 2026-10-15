"""Real FFmpeg regression checks; run with python -m unittest discover -s tests."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

from processVideo import generate_ffmpeg_command


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class H265ThreadBudgetTest(unittest.TestCase):
    def test_h265_encodes_with_auto_small_and_large_thread_budgets(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                 "testsrc2=size=320x240:rate=15", "-t", "0.4",
                 "-c:v", "libx264", str(source)],
                check=True, capture_output=True, timeout=30,
            )
            # 64 reproduced an encoder initialization failure in Debian's x265.
            # Verify actual outputs, including auto detection and smaller budgets.
            for threads in (0, 1, 16, 64):
                with self.subTest(threads=threads):
                    output = Path(folder) / f"output-{threads}.mp4"
                    command = generate_ffmpeg_command(
                        str(source), str(output), format_type="h265",
                        fixed_watermark_enabled=False,
                        dynamic_watermark_enabled=False,
                        encoder_threads=threads, encoder_preset="ultrafast",
                    )
                    self.assertIsNotNone(command)
                    result = subprocess.run(
                        command if os.name == "nt" else shlex.split(command),
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    probe = subprocess.run(
                        ["ffprobe", "-v", "error", "-show_streams",
                         "-of", "json", str(output)],
                        check=True, capture_output=True, text=True, timeout=10,
                    )
                    video = next(s for s in json.loads(probe.stdout)["streams"]
                                 if s["codec_type"] == "video")
                    self.assertEqual(video["codec_name"], "hevc")
                    self.assertEqual((video["width"], video["height"]), (320, 240))
                    self.assertGreater(float(video["duration"]), 0)


if __name__ == "__main__":
    unittest.main()
