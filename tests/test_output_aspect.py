import unittest

from server.complete_mix import output_canvas


class OutputAspectTests(unittest.TestCase):
    PORTRAIT = {'width': 720, 'height': 1280}
    LANDSCAPE = {'width': 1920, 'height': 1080}

    def assert_canvas(self, actual, width, height, aspect, origin):
        self.assertEqual(actual, {'width': width, 'height': height,
                                  'aspectRatio': aspect, 'canvasSource': origin})

    def test_prompt_overrides_portrait_reference_with_landscape(self):
        prompt = '参考视频为 9:16，最终导出为 16:9 横屏'
        self.assert_canvas(output_canvas(prompt, self.PORTRAIT),
                           1920, 1080, '16:9', 'prompt')

    def test_numbered_reference_analysis_is_not_a_requested_aspect(self):
        prompt = '一、视频整体结构分析\n画面比例：16:9（横屏构图）。'
        self.assert_canvas(output_canvas(prompt, self.PORTRAIT, self.LANDSCAPE),
                           1080, 1920, '9:16', 'reference')

    def test_prompt_aspect_after_analysis_heading(self):
        prompt = '一、视频整体结构分析\n画面比例：9:16\n二、输出要求\n成片比例：16:9'
        self.assert_canvas(output_canvas(prompt, self.PORTRAIT),
                           1920, 1080, '16:9', 'prompt')

    def test_descriptive_metadata_is_not_selected_over_target(self):
        prompt = '参考视频是 9:16 但请输出 16:9\n原视频的画幅：9:16'
        self.assert_canvas(output_canvas(prompt, self.PORTRAIT),
                           1920, 1080, '16:9', 'prompt')

    def test_portrait_reference_wins_over_landscape_source(self):
        self.assert_canvas(output_canvas('导出 30 秒成片', self.PORTRAIT, self.LANDSCAPE),
                           1080, 1920, '9:16', 'reference')

    def test_display_dimensions_win_over_storage_dimensions(self):
        stored_portrait = {**self.LANDSCAPE, 'display_width': 1080, 'display_height': 1920}
        self.assert_canvas(output_canvas('', stored_portrait),
                           1080, 1920, '9:16', 'reference')

    def test_chinese_fullwidth_label_and_vertical_dimensions(self):
        for prompt in ('画幅：９：１６', '输出尺寸 1080×1920', '请做成竖版', '请按 9:16 比例剪辑'):
            with self.subTest(prompt=prompt):
                self.assert_canvas(output_canvas(prompt, self.LANDSCAPE),
                                   1080, 1920, '9:16', 'prompt')

    def test_english_aspect_and_square(self):
        self.assert_canvas(output_canvas('Export in a 4:5 aspect ratio.', self.LANDSCAPE),
                           1080, 1350, '4:5', 'prompt')
        self.assert_canvas(output_canvas('Output: square', self.LANDSCAPE),
                           1080, 1080, '1:1', 'prompt')

    def test_negations_and_timecodes_are_not_aspect_requests(self):
        self.assert_canvas(output_canvas('请不要导出横屏；剪成00:12秒', self.PORTRAIT),
                           1080, 1920, '9:16', 'reference')

    def test_source_then_safe_default(self):
        self.assert_canvas(output_canvas('', {'width': True, 'height': 0}, self.PORTRAIT),
                           1080, 1920, '9:16', 'source')
        self.assert_canvas(output_canvas(''), 1920, 1080, '16:9', 'default')


if __name__ == '__main__':
    unittest.main()
