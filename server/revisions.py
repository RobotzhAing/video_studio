"""Local completed mixes and their revision lineage."""
import json
import re

ID = re.compile(r'[0-9a-f]{32}\Z')
PUBLIC = ('id', 'status', 'output_filename', 'output_format', 'width', 'height',
          'frame_rate', 'task_folder_id', 'revision_of', 'revision_instruction')


def load(jobs_dir, job_id):
    if not isinstance(job_id, str) or not ID.fullmatch(job_id):
        return None
    folder = jobs_dir / job_id
    try:
        with (folder / 'job.json').open(encoding='utf-8') as handle:
            job = json.loads(handle.read(512 * 1024 + 1))
        if (not isinstance(job, dict) or job.get('id') != job_id or job.get('status') != 'done'
                or not job.get('batch_task') or not (folder / 'result.mp4').is_file()):
            return None
        return job
    except (OSError, ValueError):
        return None


def public(job):
    job_id = job['id']
    return ({key: job[key] for key in PUBLIC if key in job}
        | {'download_url': f'/api/jobs/{job_id}/download',
           'preview_url': f'/api/jobs/{job_id}/preview',
           'can_revise': bool(job.get('revision_context') and job.get('task_folder_id'))})


def list_completed(jobs_dir, assets_present=None):
    if not jobs_dir.is_dir():
        return []
    result = []
    for folder in jobs_dir.iterdir():
        job = load(jobs_dir, folder.name)
        if job:
            try:
                entry = public(job)
                if entry['can_revise'] and assets_present is not None:
                    entry['can_revise'] = assets_present(job)
                result.append(((folder / 'job.json').stat().st_mtime_ns, entry))
            except OSError:
                continue
    return [job for _, job in sorted(result, key=lambda entry: (entry[0], entry[1]['id']), reverse=True)]
