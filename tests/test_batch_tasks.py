import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import main


class BatchTaskFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.media = {
            '1' * 32: {'id': '1' * 32, 'name': 'one.mp4', 'type': 'video'},
            '2' * 32: {'id': '2' * 32, 'name': 'two.png', 'type': 'image'},
            '3' * 32: {'id': '3' * 32, 'name': 'music.wav', 'type': 'audio'},
            '4' * 32: {'id': '4' * 32, 'name': 'reference.mp4', 'type': 'video'},
        }
        self.folders = []
        self.jobs = {}
        self.jobs_dir = root / 'jobs'
        self.jobs_dir.mkdir()
        self.patches = [
            patch.object(main, 'media', self.media),
            patch.object(main, 'task_folders', self.folders),
            patch.object(main, 'jobs', self.jobs),
            patch.object(main, 'JOBS_DIR', self.jobs_dir),
            patch.object(main, 'TASK_FOLDERS_FILE', root / 'task-folders.json'),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_task_folder_fields_accept_video_and_image_and_reject_audio(self):
        fields = main.validate_task_folder_fields({
            'name': '周末旅行', 'prompt': '剪成 10 秒快节奏短片',
            'media_ids': ['1' * 32, '2' * 32],
            'reference_video_id': '4' * 32,
        })
        self.assertEqual(fields['name'], '周末旅行')
        self.assertEqual(len(fields['media_ids']), 2)
        self.assertEqual(fields['reference_video_id'], '4' * 32)
        with self.assertRaises(main.RequestError):
            main.validate_task_folder_fields({'name': '音频任务', 'prompt': '', 'media_ids': ['3' * 32]})

    def test_reference_video_is_optional_and_must_be_video(self):
        fields = main.validate_task_folder_fields({
            'name': '无参考', 'prompt': '剪辑', 'media_ids': ['1' * 32],
            'reference_video_id': None,
        })
        self.assertIsNone(fields['reference_video_id'])
        with self.assertRaises(main.RequestError):
            main.validate_task_folder_fields({
                'name': '图片参考', 'prompt': '剪辑', 'media_ids': ['1' * 32],
                'reference_video_id': '2' * 32,
            })
        with self.assertRaises(main.RequestError):
            main.validate_task_folder_fields({
                'name': '音频参考', 'prompt': '剪辑', 'media_ids': ['1' * 32],
                'reference_video_id': '3' * 32,
            })

    def test_reference_video_cannot_also_be_a_source_clip(self):
        with self.assertRaises(main.RequestError):
            main.validate_task_folder_fields({
                'name': '重复引用', 'prompt': '剪辑', 'media_ids': ['1' * 32],
                'reference_video_id': '1' * 32,
            })

    def test_task_folder_fields_reject_duplicate_unknown_and_empty_name(self):
        invalid = [
            {'name': ' ', 'prompt': '', 'media_ids': []},
            {'name': '重复', 'prompt': '', 'media_ids': ['1' * 32, '1' * 32]},
            {'name': '缺素材', 'prompt': '', 'media_ids': ['4' * 32]},
        ]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(main.RequestError):
                main.validate_task_folder_fields(fields)

    def test_folder_index_is_atomically_persisted_and_reloadable(self):
        folder = {'id': 'a' * 32, **main.validate_task_folder_fields({
            'name': '任务 A', 'prompt': '先建立节奏，再收尾', 'media_ids': ['1' * 32],
            'reference_video_id': '4' * 32,
        })}
        self.folders.append(folder)
        main.save_task_folders()
        self.folders.clear()
        main.load_task_folders()
        self.assertEqual(self.folders, [folder])

    def test_export_names_are_safe_unique_and_persisted_across_runs(self):
        self.assertEqual(main.sanitize_output_name('../旅行/素材<>'), '素材__')
        first = 'b' * 32
        self.jobs[first] = {'id': first, 'batch_task': True,
                            'output_filename': main.reserve_output_filename('同名任务', first)}
        main.save_job_manifest(first)
        second = 'c' * 32
        self.assertEqual(main.reserve_output_filename('同名任务', second), '同名任务 (2).mp4')
        self.jobs.clear()
        self.assertEqual(main.reserve_output_filename('同名任务', second), '同名任务 (2).mp4')
        manifest = json.loads((self.jobs_dir / first / 'job.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['output_filename'], '同名任务.mp4')
        failed = 'd' * 32
        self.jobs[failed] = {'id': failed, 'batch_task': True, 'status': 'error',
                             'output_filename': '失败任务.mp4'}
        main.save_job_manifest(failed)
        self.assertEqual(main.reserve_output_filename('失败任务', second), '失败任务.mp4')

    def test_task_folder_http_handlers_create_edit_list_and_delete(self):
        class HandlerProbe:
            def __init__(self, path, body=None):
                self.path = path
                self.body = body
                self.response = None

            def same_origin(self):
                return None

            def read_json(self):
                return self.body

            def send_json(self, payload, status=200):
                self.response = (payload, status)

        create = HandlerProbe('/api/task-folders', {
            'name': '行程 A', 'prompt': '剪成 3 秒预告', 'media_ids': ['1' * 32],
            'reference_video_id': '4' * 32,
        })
        main.Handler.do_POST(create)
        folder, status = create.response
        self.assertEqual(status, 201)

        listing = HandlerProbe('/api/task-folders')
        main.Handler.do_GET(listing)
        self.assertEqual(listing.response[0]['folders'], [folder])

        rename = HandlerProbe(f"/api/task-folders/{folder['id']}", {'name': '行程 B'})
        main.Handler.do_PATCH(rename)
        self.assertEqual(rename.response[0]['name'], '行程 B')
        self.assertEqual(rename.response[0]['prompt'], '剪成 3 秒预告')
        self.assertEqual(rename.response[0]['reference_video_id'], '4' * 32)

        delete = HandlerProbe(f"/api/task-folders/{folder['id']}")
        main.Handler.do_DELETE(delete)
        self.assertEqual(delete.response[0]['deleted'], folder['id'])
        self.assertEqual(self.folders, [])

    def test_ai_plan_samples_reference_video_and_sends_it_as_style_guidance(self):
        candidate_id = '1' * 32
        reference_id = '4' * 32
        items = {
            candidate_id: {'id': candidate_id, 'name': 'source.mp4', 'type': 'video',
                           'duration': 4.0, 'width': 320, 'height': 180,
                           'fps_num': 24, 'fps_den': 1},
            reference_id: {'id': reference_id, 'name': 'reference.mp4', 'type': 'video',
                           'duration': 8.0, 'width': 1920, 'height': 1080,
                           'fps_num': 30, 'fps_den': 1},
        }

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return json.dumps({'choices': [{'message': {'content': json.dumps({
                    'clips': [{'candidate_index': 0, 'id': candidate_id,
                               'start': 0, 'end': 2, 'speed': 1}],
                    'text_clips': [], 'explanation': 'matched reference rhythm',
                })}}]}).encode()

        captured = {}

        def fake_urlopen(request, timeout):
            captured['payload'] = json.loads(request.data)
            captured['timeout'] = timeout
            return Response()

        with patch.object(main, 'get_item', side_effect=lambda media_id: items[media_id]), \
                patch.object(main, 'extract_ai_sample', return_value=b'jpeg') as sample, \
                patch.object(main.urllib.request, 'urlopen', side_effect=fake_urlopen):
            result = main.ai_plan({
                'base_url': 'https://ai.example/v1', 'model': 'vision-editor',
                'api_key': 'test-key', 'prompt': '模仿参考视频的快节奏剪辑',
                'reference_video_id': reference_id,
                'clips': [{'id': candidate_id, 'start': 0, 'end': 4}],
            })

        self.assertEqual(result['clips'][0]['id'], candidate_id)
        self.assertEqual(sample.call_count, 5)  # two source samples and three reference samples
        messages = captured['payload']['messages']
        self.assertIn('不能被当作成片素材引用', messages[0]['content'])
        user_text = messages[1]['content'][0]['text']
        context = json.loads(user_text)
        self.assertEqual(context['reference_video']['id'], reference_id)
        self.assertEqual(context['reference_video']['duration'], 8.0)
        self.assertEqual(sum(1 for item in messages[1]['content'] if item['type'] == 'image_url'), 5)

    def test_local_planner_uses_reference_duration_and_discloses_visual_limit(self):
        result = main.local_ai_plan('剪一个和参考同长度的片段', [{
            'candidate_index': 0, 'id': '1' * 32, 'start': 0.0,
            'end': 4.0, 'speed': 1.0,
        }], {'duration': 2.0})
        self.assertEqual(result['clips'][0]['end'], 2.0)
        self.assertEqual(result['textClips'], [])
        self.assertIn('不会理解参考画面', result['explanation'])


if __name__ == '__main__':
    unittest.main()
