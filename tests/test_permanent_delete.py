import json
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from server import main


class PermanentDeleteTests(TestCase):
    def test_media_deletes_only_managed_copy_and_clears_both_references(self):
        first, second, missing = 'a' * 32, 'b' * 32, 'c' * 32
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            managed = root / 'media'
            managed.mkdir()
            original = root / 'original.mp4'
            original.write_bytes(b'original')
            for mid in (first, second):
                (managed / mid).write_bytes(b'managed')
            catalog = {mid: {'name': original.name} for mid in (first, second)}
            folders = [{'media_ids': [first], 'reference_video_id': second}]
            with patch.multiple(main, DATA=root, MEDIA=managed, media=catalog, jobs={},
                    task_folders=folders, TASK_FOLDERS_FILE=root / 'task-folders.json'):
                result = main.permanently_delete({'ids': [first, second, missing, first]}, 'media')
            self.assertEqual(result['deleted'], [first, second])
            self.assertEqual(result['errors'][0]['id'], missing)
            self.assertEqual(list(managed.iterdir()), [])
            self.assertEqual(original.read_bytes(), b'original')
            self.assertEqual(json.loads((root / 'media.json').read_text()), {})
            self.assertEqual(json.loads((root / 'task-folders.json').read_text()),
                             [{'media_ids': [], 'reference_video_id': None}])

    def test_mix_deletion_removes_manifest_and_files_without_touching_other_jobs(self):
        first, second = 'a' * 32, 'b' * 32
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = {}
            for mid, status in ((first, 'done'), (second, 'rendering')):
                folder = root / mid
                folder.mkdir()
                jobs[mid] = {'id': mid, 'status': status, 'batch_task': True}
                (folder / 'job.json').write_text(json.dumps(jobs[mid]))
                (folder / 'result.mp4').write_bytes(b'video')
                (folder / 'text-0.txt').write_text('caption')
            with patch.multiple(main, JOBS_DIR=root, jobs=jobs):
                result = main.permanently_delete({'ids': [first, second]}, 'mix')
                with self.assertRaises(main.RequestError):
                    main.permanently_delete({'ids': ['../original.mp4']}, 'mix')
            self.assertEqual(result['deleted'], [first])
            self.assertEqual(result['errors'][0]['id'], second)
            self.assertFalse((root / first).exists())
            self.assertNotIn(first, jobs)
            self.assertTrue((root / second / 'result.mp4').is_file())
