import unittest

from server import main


class AiPlanCandidateMappingTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            {"id": "media-a", "start": 0.0, "end": 8.0, "speed": 1.5},
            {"id": "media-b", "start": 2.0, "end": 6.0, "speed": 0.75},
        ]

    def map(self, raw_clip, planned_clip, candidates=None):
        return main.associate_ai_plan_candidates(
            [raw_clip], [planned_clip], candidates or self.candidates
        )[0]

    def test_explicit_candidate_index_inherits_speed_when_model_omits_it(self):
        result = self.map(
            {"candidate_index": 1},
            {"id": "media-b", "start": 3.0, "end": 5.0},
        )

        self.assertEqual(result["source_candidate_index"], 1)
        self.assertEqual(result["speed"], 0.75)

    def test_unique_legacy_match_infers_source_and_speed(self):
        result = self.map(
            {},
            {"id": "media-b", "start": 3.0, "end": 5.0},
        )

        self.assertEqual(result["source_candidate_index"], 1)
        self.assertEqual(result["speed"], 0.75)

    def test_ambiguous_legacy_match_does_not_guess_or_inherit_speed(self):
        overlapping_candidates = [
            {"id": "media-a", "start": 0.0, "end": 8.0, "speed": 1.5},
            {"id": "media-a", "start": 2.0, "end": 6.0, "speed": 2.0},
        ]

        result = self.map(
            {},
            {"id": "media-a", "start": 3.0, "end": 5.0},
            overlapping_candidates,
        )

        self.assertIsNone(result["source_candidate_index"])
        self.assertNotIn("speed", result)

    def test_invalid_candidate_index_is_rejected(self):
        for candidate_index in (-1, 2, True, "1"):
            with self.subTest(candidate_index=candidate_index):
                with self.assertRaises(main.RequestError):
                    self.map(
                        {"candidate_index": candidate_index},
                        {"id": "media-a", "start": 1.0, "end": 2.0},
                    )

    def test_clip_outside_referenced_candidate_range_is_rejected(self):
        with self.assertRaises(main.RequestError):
            self.map(
                {"candidate_index": 0},
                {"id": "media-a", "start": 7.0, "end": 9.0},
            )


if __name__ == "__main__":
    unittest.main()
