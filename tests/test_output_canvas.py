import json
import os
import shutil
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from server import main


class OutputCanvasValidationTests(unittest.TestCase):
    def test_accepts_supported_integer_and_rational_frame_rates(self):
        self.assertEqual(main.render_frame_rate({"numerator": 30000, "denominator": 1001}),
                         Fraction(30000, 1001))
        self.assertEqual(main.render_frame_rate("24000/1001"), Fraction(24000, 1001))
        self.assertEqual(main.render_frame_rate(25), Fraction(25, 1))

    def test_rejects_unsupported_or_malformed_frame_rates(self):
        for value in ({"numerator": 0, "denominator": 1}, {"numerator": 25.5, "denominator": 1},
                      {"numerator": True, "denominator": 1}, "29.5", "30/0", "fast"):
            with self.subTest(value=value), self.assertRaises(main.RequestError):
                main.render_frame_rate(value)

    def test_rejects_fractional_and_boolean_dimensions(self):
        for value in (128.5, True, 63, 7682):
            with self.subTest(value=value), self.assertRaises(main.RequestError):
                main.dimension(value, 1280)


class OutputCanvasRenderIntegrationTests(unittest.TestCase):
    def test_export_uses_requested_dimensions_and_frame_rate(self):
        root = Path(__file__).resolve().parents[1]
        bundled = root / "node_modules" / "ffmpeg-static" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        ffmpeg = str(bundled) if bundled.is_file() else shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            self.skipTest("FFmpeg or FFprobe is unavailable")

        media_id, render_id = "8" * 32, "9" * 32
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media_dir, jobs_dir = root / "media", root / "jobs"
            media_dir.mkdir(); jobs_dir.mkdir()
            source = root / "source.mp4"
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=64x64:rate=30:duration=1.2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                check=True, capture_output=True, timeout=90)
            (media_dir / media_id).write_bytes(source.read_bytes())
            media = {media_id: {"id": media_id, "name": "source.mp4", "type": "video",
                                "duration": 1.2, "has_audio": False}}
            jobs = {render_id: {"id": render_id, "status": "queued"}}
            tracks = [{"trackId": "video-main", "kind": "video", "order": 0}]
            with patch.object(main, "MEDIA", media_dir), patch.object(main, "JOBS_DIR", jobs_dir), \
                    patch.object(main, "media", media), patch.object(main, "jobs", jobs), \
                    patch.object(main, "FFMPEG", ffmpeg), patch.object(main, "FFPROBE", ffprobe):
                clips = main.validate_clips([{"id": media_id, "start": 0, "end": 1.2,
                                              "timelineStartTick": 0}], tracks)
                main.render_job(render_id, clips, 128, 96, None, 1.0,
                                frame_rate=Fraction(25, 1))
                self.assertEqual(jobs[render_id]["status"], "done", jobs[render_id])
                self.assertEqual(jobs[render_id]["crf"], 18)
                self.assertEqual((jobs[render_id]["width"], jobs[render_id]["height"]), (128, 96))
                self.assertEqual(jobs[render_id]["video_codec"], "h264")

            output = jobs_dir / render_id / "result.mp4"
            info = json.loads(subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames",
                "-of", "json", str(output)], check=True, capture_output=True, timeout=90).stdout)
            stream = info["streams"][0]
            self.assertEqual((stream["width"], stream["height"]), (128, 96))
            self.assertEqual(stream["r_frame_rate"], "25/1")
            self.assertEqual(int(stream["nb_frames"]), 30)


if __name__ == "__main__":
    unittest.main()
