import json
import unittest
from unittest.mock import patch

from server import main


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.payload


class AiPlanSamplingTests(unittest.TestCase):
    def run_plan(self, media_type, clip):
        raw_plan = {
            "explanation": "采样测试",
            "clips": [{
                "candidate_index": 0,
                "id": clip["id"],
                "start": clip["start"],
                "end": clip["end"],
                "speed": clip["speed"],
            }],
            "text_clips": [{"text": "旅程开始", "start": 0.5, "end": 2.5}],
        }
        response_bytes = json.dumps({"choices": [{"message": {
            "content": json.dumps(raw_plan, ensure_ascii=False)
        }}]}).encode()
        item = {"id": clip["id"], "name": "fixture", "duration": clip["end"], "type": media_type}
        with patch.object(main, "validate_clips", side_effect=lambda values: [dict(value) for value in values]), \
                patch.object(main, "get_item", return_value=item), \
                patch.object(main, "extract_ai_sample", return_value=b"jpeg") as extract, \
                patch.object(main.urllib.request, "urlopen", return_value=_Response(response_bytes)):
            result = main.ai_plan({
                "clips": [clip],
                "prompt": "按素材内容生成短片",
                "api_key": "test-key",
                "model": "vision-test",
                "base_url": "https://example.invalid/v1",
            })
        return result, extract.call_args_list

    def test_static_image_sends_one_representative_frame(self):
        clip = {"id": "a" * 32, "start": 0.0, "end": 5.0, "speed": 1.0}

        result, calls = self.run_plan("image", clip)

        self.assertEqual(len(result["clips"]), 1)
        self.assertEqual(result["textClips"], [{"text": "旅程开始", "start": 0.5, "end": 2.5}])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args, (clip["id"], 2.5, "image"))

    def test_video_sends_two_temporal_samples(self):
        clip = {"id": "b" * 32, "start": 2.0, "end": 10.0, "speed": 1.0}

        result, calls = self.run_plan("video", clip)

        self.assertEqual(len(result["clips"]), 1)
        self.assertEqual([call.args for call in calls], [
            (clip["id"], 4.0, "video"),
            (clip["id"], 8.0, "video"),
        ])


if __name__ == "__main__":
    unittest.main()
