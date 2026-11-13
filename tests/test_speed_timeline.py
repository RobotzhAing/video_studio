import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import main


class SpeedTimelineValidationTests(unittest.TestCase):
    def setUp(self):
        self.media_id = "a" * 32

    def test_clip_speed_defaults_to_one_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            media_dir = Path(folder)
            (media_dir / self.media_id).write_bytes(b"video")
            media = {self.media_id: {"id": self.media_id, "type": "video",
                                     "duration": 4.0, "has_audio": False}}
            tracks = [{"trackId": "video-main", "kind": "video", "order": 0}]
            with patch.object(main, "MEDIA", media_dir), patch.object(main, "media", media):
                clean = main.validate_clips([{"id": self.media_id, "start": 0, "end": 2}], tracks)
                self.assertEqual(clean[0]["speed"], 1.0)
                fast = main.validate_clips([{"id": self.media_id, "start": 0, "end": 2,
                                             "speed": 2}], tracks)
                self.assertEqual(fast[0]["speed"], 2.0)
                for value in (0.1, 4.1, "fast", None):
                    with self.subTest(speed=value):
                        with self.assertRaises(main.RequestError):
                            main.validate_clips([{"id": self.media_id, "start": 0, "end": 2,
                                                  "speed": value}], tracks)

    def test_explicit_timeline_end_preserves_rounding_and_rejects_unrelated_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            media_dir = Path(folder)
            (media_dir / self.media_id).write_bytes(b"video")
            media = {self.media_id: {"id": self.media_id, "type": "video",
                                     "duration": 4.0, "has_audio": False}}
            tracks = [{"trackId": "video-main", "kind": "video", "order": 0}]
            with patch.object(main, "MEDIA", media_dir), patch.object(main, "media", media):
                payload = {"id": self.media_id, "start": 0, "end": 2, "speed": 1,
                           "timelineStartTick": 1000, "timelineEndTick": 481001,
                           "trackId": "video-main"}
                clean = main.validate_clips([payload], tracks)[0]
                self.assertEqual(clean["timeline_end_tick"] - clean["timeline_start_tick"], 480001)
                self.assertAlmostEqual(clean["timeline_duration_seconds"] * 240000, 480001)

                for invalid_end in (481002, 999, True, "481000"):
                    with self.subTest(timeline_end_tick=invalid_end), self.assertRaises(main.RequestError):
                        main.validate_clips([{**payload, "timelineEndTick": invalid_end}], tracks)


class SpeedTimelineRenderIntegrationTests(unittest.TestCase):
    def _run_ffmpeg(self, ffmpeg, *args):
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args],
                       check=True, capture_output=True, timeout=90)

    def _tools(self):
        root = Path(__file__).resolve().parents[1]
        bundled = root / "node_modules" / "ffmpeg-static" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        ffmpeg = str(bundled) if bundled.is_file() else shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        return ffmpeg, ffprobe

    def test_two_x_speed_shortens_video_and_audio_together(self):
        ffmpeg, ffprobe = self._tools()
        if not ffmpeg or not ffprobe:
            self.skipTest("FFmpeg or FFprobe is unavailable")

        video_id, render_id = "b" * 32, "c" * 32
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media_dir, jobs_dir = root / "media", root / "jobs"
            media_dir.mkdir(); jobs_dir.mkdir()
            source = root / "source.mp4"
            self._run_ffmpeg(ffmpeg,
                "-f", "lavfi", "-i", "testsrc=size=64x64:rate=30:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "48000", "-ac", "2", str(source))
            (media_dir / video_id).write_bytes(source.read_bytes())
            media = {video_id: {"id": video_id, "name": "source.mp4", "type": "video",
                                "duration": 2.0, "has_audio": True}}
            jobs = {render_id: {"id": render_id, "status": "queued"}}
            tracks = [{"trackId": "video-main", "kind": "video", "order": 0}]
            with patch.object(main, "MEDIA", media_dir), patch.object(main, "JOBS_DIR", jobs_dir), \
                    patch.object(main, "media", media), patch.object(main, "jobs", jobs), \
                    patch.object(main, "FFMPEG", ffmpeg), patch.object(main, "FFPROBE", ffprobe):
                clips = main.validate_clips([{"id": video_id, "start": 0, "end": 2,
                                              "speed": 2, "timelineStartTick": 0}], tracks)
                self.assertEqual(clips[0]["speed"], 2.0)
                self.assertAlmostEqual(clips[0]["timeline_duration_seconds"], 1.0, delta=0.001)
                main.render_job(render_id, clips, 64, 64, None, 1.0)
                self.assertEqual(jobs[render_id]["status"], "done", jobs[render_id])

            output = jobs_dir / render_id / "result.mp4"
            info = json.loads(subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                 "-of", "json", str(output)], capture_output=True, check=True, timeout=90).stdout)
            duration = float(info["format"]["duration"])
            self.assertAlmostEqual(duration, 1.0, delta=0.12)
            self.assertIn("audio", [stream["codec_type"] for stream in info["streams"]])


if __name__ == "__main__":
    unittest.main()
