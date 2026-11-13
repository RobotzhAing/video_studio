import unittest

from server import main


class AiPlanTextClipTests(unittest.TestCase):
    def test_accepts_optional_editable_titles_with_tick_aligned_times(self):
        result = main.validate_ai_plan_text_clips([
            {"text": "开场", "start": 0.100001, "end": 1.000001},
            {"text": "结尾", "start": 2, "end": 3},
        ], 3)

        self.assertEqual(result, [
            {"text": "开场", "start": 0.1, "end": 1.0},
            {"text": "结尾", "start": 2.0, "end": 3.0},
        ])

    def test_accepts_missing_titles_as_empty_list(self):
        self.assertEqual(main.validate_ai_plan_text_clips([], 4), [])

    def test_rejects_titles_outside_plan_or_with_invalid_text_and_duration(self):
        invalid = [
            ([{"text": "越界", "start": 3, "end": 4}], 3),
            ([{"text": "太短", "start": 0, "end": 0.04}], 3),
            ([{"text": "太长", "start": 0, "end": 31}], 40),
            ([{"text": " " , "start": 0, "end": 1}], 3),
            ([{"text": "坏时间", "start": True, "end": 1}], 3),
            ([{"text": "非有限", "start": float("nan"), "end": 1}], 3),
            ([{"text": "控制\u0001符", "start": 0, "end": 1}], 3),
            ([{"text": "太长" * 81, "start": 0, "end": 1}], 3),
            ([None], 3),
            ([{"text": "x", "start": 0, "end": 1}] * 51, 3),
        ]
        for titles, duration in invalid:
            with self.subTest(titles=titles[:1]):
                with self.assertRaises(main.RequestError):
                    main.validate_ai_plan_text_clips(titles, duration)


if __name__ == "__main__":
    unittest.main()
