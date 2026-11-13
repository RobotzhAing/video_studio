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


class TextTimelineTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        bundled = root / "node_modules" / "ffmpeg-static" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        self.ffmpeg = str(bundled) if bundled.is_file() else shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")

    def test_validates_text_clip_track_timing_and_appearance(self):
        tracks = [
            {"trackId": "video-main", "kind": "video", "order": 0},
            {"trackId": "text-subtitles", "kind": "text", "order": 1},
        ]
        clip = {"clipId": "title-1", "trackId": "text-subtitles", "text": "Hello",
            "timelineStartTick": 24000, "durationTicks": 720000, "fontSize": 42,
            "x": 50, "y": 85, "color": "#abcdef", "outlineColor": "#123456",
            "outlineWidth": 1.5, "align": "center"}
        cleaned = main.validate_text_clips([clip], tracks)
        self.assertEqual(cleaned[0]["timeline_start_seconds"], 0.1)
        self.assertEqual(cleaned[0]["timeline_end_seconds"], 3.1)
        self.assertEqual(cleaned[0]["track_order"], 1)
        self.assertEqual(cleaned[0]["color"], "#abcdef")
        for invalid in [
            {**clip, "trackId": "audio-main"},
            {**clip, "fontSize": 0},
            {**clip, "color": "red"},
            {**clip, "x": 101},
            {**clip, "durationTicks": 11999},
        ]:
            with self.assertRaises(main.RequestError):
                main.validate_text_clips([invalid], tracks)
        with self.assertRaisesRegex(main.RequestError, "编号重复"):
            main.validate_text_clips([clip, clip], tracks)

    def test_resolves_platform_unicode_font_before_generic_sans(self):
        expected = Path("/System/Library/Fonts/STHeiti Medium.ttc")
        with patch.object(main.sys, "platform", "darwin"), \
                patch.object(Path, "is_file", autospec=True,
                    side_effect=lambda candidate: candidate == expected):
            self.assertEqual(main.resolve_text_fontfile(), expected)

    def test_draws_text_only_inside_its_timeline_interval(self):
        if not self.ffmpeg or not self.ffprobe:
            self.skipTest("FFmpeg or FFprobe is unavailable")
        encoders = subprocess.run([self.ffmpeg, "-hide_banner", "-filters"], capture_output=True,
            text=True, check=True).stdout
        if "drawtext" not in encoders:
            self.skipTest("FFmpeg has no drawtext filter")

        video_id, job_id = "a" * 32, "b" * 32
        tracks = [
            {"trackId": "video-main", "kind": "video", "name": "Main", "order": 0},
            {"trackId": "text-subtitles", "kind": "text", "name": "Subtitles", "order": 1},
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media_dir, jobs_dir = root / "media", root / "jobs"
            media_dir.mkdir(); jobs_dir.mkdir()
            source = root / "black.mp4"
            subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:r=30:d=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "1", str(source)],
                check=True, capture_output=True, timeout=60)
            metadata = main.probe(source, source.name)
            (media_dir / video_id).write_bytes(source.read_bytes())
            media = {video_id: {"id": video_id, "name": source.name, **metadata}}
            jobs = {job_id: {"id": job_id, "status": "queued"}}
            text = "中文字幕测试\nA:B[1], test 'quotes'"
            raw_text_clip = {"clipId": "unicode-title-01", "trackId": "text-subtitles", "text": text,
                "timelineStartTick": 240000 // 5, "durationTicks": 240000 // 2,
                "fontSize": 18, "x": 50, "y": 50, "color": "#00ff00",
                "outlineColor": "#000000", "outlineWidth": 0, "align": "center"}
            with patch.object(main, "MEDIA", media_dir), patch.object(main, "JOBS_DIR", jobs_dir), \
                    patch.object(main, "media", media), patch.object(main, "jobs", jobs), \
                    patch.object(main, "FFMPEG", self.ffmpeg), patch.object(main, "FFPROBE", self.ffprobe):
                clips = main.validate_clips([{"id": video_id, "start": 0, "end": 1,
                    "trackId": "video-main", "timelineStartTick": 0}], tracks)
                text_clips = main.validate_text_clips([raw_text_clip], tracks)
                main.render_job(job_id, clips, 64, 64, None, 0.25, [], Fraction(30, 1), text_clips)
                self.assertEqual(jobs[job_id]["status"], "done", jobs[job_id])
                output = jobs_dir / job_id / "result.mp4"
                self.assertEqual((jobs_dir / job_id / "text-0000.txt").read_text(encoding="utf-8"), text)

                def green_pixel_count(at):
                    frame = subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(at),
                        "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                        capture_output=True, check=True, timeout=60).stdout
                    return sum(1 for i in range(0, len(frame), 3)
                        if frame[i] < 100 and frame[i + 1] > 150 and frame[i + 2] < 100)

                self.assertEqual(green_pixel_count(0.1), 0)
                self.assertGreater(green_pixel_count(0.5), 10)
                self.assertEqual(green_pixel_count(0.9), 0)


if __name__ == "__main__":
    unittest.main()
