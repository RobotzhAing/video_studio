import hashlib
import io
import json
import tempfile
import urllib.parse
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from server import main


class MediaMetadataTests(unittest.TestCase):
    def test_display_geometry_respects_rotation_and_pixel_aspect(self):
        self.assertEqual(main.display_dimensions({'width': 1920, 'height': 1080,
            'side_data_list': [{'rotation': -90}]}), (1080, 1920))
        self.assertEqual(main.display_dimensions({'width': 720, 'height': 576,
            'display_aspect_ratio': '16:9'}), (1024, 576))

    def test_old_media_index_gets_nullable_fields_without_rewrite(self):
        old_record = {"id": "a" * 32, "name": "old.mp4", "type": "video", "duration": 2.0}
        with tempfile.TemporaryDirectory() as folder:
            data_dir = Path(folder)
            media_dir = data_dir / "media"
            media_dir.mkdir()
            (media_dir / ("a" * 32)).write_bytes(b"old media")
            index_path = data_dir / "media.json"
            original = json.dumps({"a" * 32: old_record}).encode()
            index_path.write_bytes(original)

            with patch.object(main, "DATA", data_dir), patch.object(main, "MEDIA", media_dir), \
                    patch.object(main, "media", {}):
                main.load_media_index()
                loaded = main.media["a" * 32]

            self.assertIsNone(loaded["fps_num"])
            self.assertIsNone(loaded["fps_den"])
            self.assertIsNone(loaded["sha256"])
            self.assertEqual(index_path.read_bytes(), original)

    def test_probe_preserves_rational_video_frame_rate(self):
        result = SimpleNamespace(stdout=json.dumps({
            "streams": [{
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "duration": "4.0",
                "avg_frame_rate": "24000/1001",
            }],
            "format": {"duration": "4.0"},
        }).encode())

        with patch.object(main, "execute", return_value=result):
            metadata = main.probe(Path("sample.mp4"))

        self.assertEqual(metadata["fps_num"], 24000)
        self.assertEqual(metadata["fps_den"], 1001)

    def test_probe_leaves_frame_rate_empty_for_audio(self):
        result = SimpleNamespace(stdout=json.dumps({
            "streams": [{"codec_type": "audio", "duration": "2.0"}],
            "format": {"duration": "2.0"},
        }).encode())

        with patch.object(main, "execute", return_value=result):
            metadata = main.probe(Path("sample.wav"))

        self.assertIsNone(metadata["fps_num"])
        self.assertIsNone(metadata["fps_den"])

    def test_probe_assigns_a_fixed_editing_duration_to_still_images(self):
        result = SimpleNamespace(stdout=json.dumps({
            "streams": [{"codec_type": "video", "codec_name": "png", "width": 320,
                "height": 180, "avg_frame_rate": "0/0"}],
            "format": {"format_name": "png_pipe"},
        }).encode())

        with patch.object(main, "execute", return_value=result):
            metadata = main.probe(Path("frame.upload"), "frame.png")

        self.assertEqual(metadata["type"], "image")
        self.assertEqual(metadata["duration"], 5.0)
        self.assertEqual((metadata["width"], metadata["height"]), (320, 180))
        self.assertIsNone(metadata["fps_num"])
        self.assertIsNone(metadata["fps_den"])
        self.assertFalse(metadata["has_audio"])

    def test_upload_persists_image_metadata_without_changing_project_storage(self):
        sample = b"still-image-bytes"
        response = {}
        handler = SimpleNamespace(headers={"Content-Length": str(len(sample))}, rfile=io.BytesIO(sample))
        handler.send_json = lambda data: response.update(data)
        with tempfile.TemporaryDirectory() as folder:
            data_dir = Path(folder)
            media_dir = data_dir / "media"
            media_dir.mkdir()
            with patch.object(main, "DATA", data_dir), patch.object(main, "MEDIA", media_dir), \
                    patch.object(main, "media", {}), patch.object(main, "save_media_index") as save_index, \
                    patch.object(main, "probe", return_value={"type": "image", "duration": 5.0,
                        "width": 320, "height": 180, "fps_num": 30, "fps_den": 1, "has_audio": False}):
                main.Handler.upload(handler, urllib.parse.urlsplit("/api/media?name=frame.png"))
                media_id = response["id"]
                self.assertEqual(main.media[media_id]["type"], "image")
                self.assertEqual(main.media[media_id]["duration"], 5.0)
                self.assertEqual((media_dir / media_id).read_bytes(), sample)
                save_index.assert_called_once_with()

    def test_ai_image_sample_does_not_seek_past_the_still_frame(self):
        result = SimpleNamespace(stdout=b"jpeg")
        with patch.object(main, "MEDIA", Path("/tmp/media")), patch.object(main, "execute", return_value=result) as execute:
            sampled = main.extract_ai_sample("a" * 32, 3.75, "image")

        args = execute.call_args.args[0]
        self.assertNotIn("-ss", args)
        self.assertIn("/tmp/media/" + "a" * 32, args)
        self.assertEqual(sampled, b"jpeg")

    def test_ai_video_sample_keeps_time_seek(self):
        result = SimpleNamespace(stdout=b"jpeg")
        with patch.object(main, "MEDIA", Path("/tmp/media")), patch.object(main, "execute", return_value=result) as execute:
            main.extract_ai_sample("b" * 32, 1.5, "video")

        args = execute.call_args.args[0]
        self.assertEqual(args[args.index("-ss") + 1], "1.5")

    def test_sha256_file_hashes_in_chunks(self):
        payload = b"VideoFusion metadata test" * 100
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.bin"
            path.write_bytes(payload)

            digest = main.sha256_file(path)

        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())


if __name__ == "__main__":
    unittest.main()
