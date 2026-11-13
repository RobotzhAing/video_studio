import array
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import main


class AudioTimelineValidationTests(unittest.TestCase):
    def test_audio_clip_requires_declared_audio_track_and_valid_audio_media(self):
        audio_id = "a" * 32
        video_id = "b" * 32
        with tempfile.TemporaryDirectory() as folder:
            media_dir = Path(folder)
            (media_dir / audio_id).write_bytes(b"audio")
            (media_dir / video_id).write_bytes(b"video")
            media = {
                audio_id: {"id": audio_id, "type": "audio", "duration": 3.0, "has_audio": True},
                video_id: {"id": video_id, "type": "video", "duration": 3.0, "has_audio": False},
            }
            tracks = [
                {"trackId": "video-main", "kind": "video", "order": 0},
                {"trackId": "audio-main", "kind": "audio", "order": 1},
            ]
            with patch.object(main, "MEDIA", media_dir), patch.object(main, "media", media):
                clean = main.validate_audio_clips([{"id": audio_id, "start": 0.5, "end": 2,
                    "trackId": "audio-main", "timelineStartTick": 360000, "volume": 0.6}], tracks)
                self.assertEqual(clean[0]["timeline_start_seconds"], 1.5)
                self.assertEqual(clean[0]["volume"], 0.6)
                with self.assertRaisesRegex(main.RequestError, "显式声明"):
                    main.validate_audio_clips([{"id": audio_id, "start": 0, "end": 1,
                        "trackId": "video-main"}], tracks)
                with self.assertRaisesRegex(main.RequestError, "显式声明的音频轨道"):
                    main.validate_audio_clips([{"id": audio_id, "start": 0, "end": 1,
                        "trackId": "audio-main"}], tracks[:1])
                with self.assertRaisesRegex(main.RequestError, "含有音轨"):
                    main.validate_audio_clips([{"id": video_id, "start": 0, "end": 1,
                        "trackId": "audio-main"}], tracks)
                with self.assertRaisesRegex(main.RequestError, "音量必须"):
                    main.validate_audio_clips([{"id": audio_id, "start": 0, "end": 1,
                        "trackId": "audio-main", "volume": 2.1}], tracks)
    def test_snake_case_and_legacy_sequential_positions_are_supported(self):
        audio_id = "a" * 32
        with tempfile.TemporaryDirectory() as folder:
            media_dir = Path(folder)
            (media_dir / audio_id).write_bytes(b"audio")
            media = {audio_id: {"id": audio_id, "type": "audio", "duration": 4.0, "has_audio": True}}
            tracks = [{"trackId": "audio-1", "kind": "audio", "order": 0}]
            with patch.object(main, "MEDIA", media_dir), patch.object(main, "media", media):
                clean = main.validate_audio_clips([
                    {"id": audio_id, "start": 0.25, "end": 1.25, "track_id": "audio-1"},
                    {"id": audio_id, "start": 1, "end": 2, "track_id": "audio-1"},
                ], tracks)
            self.assertEqual(clean[0]["timeline_start_tick"], 0)
            self.assertEqual(clean[1]["timeline_start_tick"], 240000)
            self.assertEqual(clean[1]["timeline_start_seconds"], 1.0)


class RenderPreviewRouteTests(unittest.TestCase):
    def test_completed_render_preview_supports_inline_byte_range_playback(self):
        render_id = "c" * 32
        cases = [("mp4", "result.mp4", "video/mp4"),
            ("webm-alpha", "result.webm", "video/webm")]
        for output_format, filename, content_type in cases:
            with self.subTest(output_format=output_format), tempfile.TemporaryDirectory() as folder:
                job_dir = Path(folder)
                (job_dir / render_id).mkdir()
                (job_dir / render_id / filename).write_bytes(b"preview-stream")
                handler = object.__new__(main.Handler)
                handler.path = f"/api/jobs/{render_id}/preview"
                handler.headers = {"Range": "bytes=3-8"}
                handler.wfile = io.BytesIO()
                status_codes, headers = [], {}
                handler.send_response = lambda status: status_codes.append(status)
                handler.send_header = lambda name, value: headers.__setitem__(name, value)
                handler.end_headers = lambda: None
                with patch.object(main, "JOBS_DIR", job_dir), patch.object(main, "jobs", {
                    render_id: {"id": render_id, "status": "done", "output_format": output_format}
                }):
                    handler.do_GET()
                self.assertEqual(status_codes, [206])
                self.assertEqual(handler.wfile.getvalue(), b"view-s")
                self.assertEqual(headers["Content-Type"], content_type)
                self.assertEqual(headers["Accept-Ranges"], "bytes")
                self.assertNotIn("Content-Disposition", headers)

class AudioTimelineRenderIntegrationTests(unittest.TestCase):
    def _run_ffmpeg(self, ffmpeg, *args):
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args],
            check=True, capture_output=True, timeout=90)

    def _rms(self, ffmpeg, path, at):
        result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(at),
            "-t", "0.12", "-i", str(path), "-vn", "-ac", "1", "-ar", "48000",
            "-f", "s16le", "pipe:1"], capture_output=True, check=True, timeout=90)
        samples = array.array("h")
        samples.frombytes(result.stdout[:len(result.stdout) // 2 * 2])
        return (sum(sample * sample for sample in samples) / max(1, len(samples))) ** 0.5

    def test_timeline_audio_is_trimmed_delayed_gained_and_extends_render(self):
        root = Path(__file__).resolve().parents[1]
        bundled_ffmpeg = root / "node_modules" / "ffmpeg-static" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        ffmpeg = str(bundled_ffmpeg) if bundled_ffmpeg.is_file() else shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            self.skipTest("FFmpeg or FFprobe is unavailable")

        video_id, audio_id, render_id = "a" * 32, "b" * 32, "c" * 32
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media_dir, jobs_dir = root / "media", root / "jobs"
            media_dir.mkdir(); jobs_dir.mkdir()
            image = root / "frame.ppm"
            image.write_bytes(b"P6\n64 64\n255\n" + bytes([16, 16, 16]) * (64 * 64))
            tone = root / "tone.wav"
            self._run_ffmpeg(ffmpeg, "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-af", "volume=0.3", "-c:a", "pcm_s16le", str(tone))
            (media_dir / video_id).write_bytes(image.read_bytes())
            (media_dir / audio_id).write_bytes(tone.read_bytes())
            media = {
                video_id: {"id": video_id, "name": "frame.ppm", "type": "image", "duration": 5.0,
                    "has_audio": False},
                audio_id: {"id": audio_id, "name": "tone.wav", "type": "audio", "duration": 3.0,
                    "has_audio": True},
            }
            jobs = {render_id: {"id": render_id, "status": "queued"}}
            tracks = [
                {"trackId": "video-main", "kind": "video", "order": 0},
                {"trackId": "audio-main", "kind": "audio", "order": 1},
            ]
            with patch.object(main, "MEDIA", media_dir), patch.object(main, "JOBS_DIR", jobs_dir), \
                    patch.object(main, "media", media), patch.object(main, "jobs", jobs), \
                    patch.object(main, "FFMPEG", ffmpeg), patch.object(main, "FFPROBE", ffprobe):
                clips = main.validate_clips([{"id": video_id, "start": 0, "end": 0.5,
                    "trackId": "video-main", "timelineStartTick": 0}], tracks)
                audio_clips = main.validate_audio_clips([
                    {"id": audio_id, "start": 0.5, "end": 1.5, "trackId": "audio-main",
                        "timelineStartTick": 240000, "volume": 1.0},
                    {"id": audio_id, "start": 0, "end": 1, "trackId": "audio-main",
                        "timelineStartTick": 360000, "volume": 0.5},
                    {"id": audio_id, "start": 1.5, "end": 2.5, "trackId": "audio-main",
                        "timelineStartTick": 720000, "volume": 1.0},
                ], tracks)
                main.render_job(render_id, clips, 640, 360, None, 0.25, audio_clips)
                self.assertEqual(jobs[render_id]["status"], "done", jobs[render_id])

            output = jobs_dir / render_id / "result.mp4"
            info = json.loads(subprocess.run([ffprobe, "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,width,height", "-of", "json", str(output)],
                capture_output=True, check=True, timeout=90).stdout)
            self.assertAlmostEqual(float(info["format"]["duration"]), 4.0, delta=0.1)
            self.assertIn("audio", [stream["codec_type"] for stream in info["streams"]])
            video_stream = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
            self.assertEqual((video_stream["width"], video_stream["height"]), (640, 360))

            rms_before = self._rms(ffmpeg, output, 0.2)
            rms_first = self._rms(ffmpeg, output, 1.2)
            rms_overlap = self._rms(ffmpeg, output, 1.7)
            rms_second_only = self._rms(ffmpeg, output, 2.2)
            rms_last = self._rms(ffmpeg, output, 3.3)
            self.assertLess(rms_before, 80, f"audio should be delayed, got RMS {rms_before}")
            self.assertGreater(rms_first, 200, f"trimmed source audio should be present, got RMS {rms_first}")
            self.assertGreater(rms_overlap, rms_first * 1.15,
                f"overlapping clips should mix, got {rms_first} then {rms_overlap}")
            self.assertGreater(rms_second_only, 80, f"second clip should continue to its timeline end, got {rms_second_only}")
            self.assertLess(rms_first, rms_second_only * 2.8,
                f"the 0.5 gain should lower the second-only clip level, got {rms_first} vs {rms_second_only}")
            self.assertGreater(rms_last, 200, "delayed narration should reach its last segment")
