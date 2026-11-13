#!/usr/bin/env python3
"""Verify a real render through an isolated backend and an HTTP provider fixture.

The fixture checks protocol/data flow and uses OS speech, not a real AI provider.
Pass --provider aliyun for Qwen Omni SSE and native Qwen-Audio TTS.
Pass --packaged-app to verify the same runtime shipped to macOS users.
Also verifies saved mix playback and a revision after restarting the backend.
"""
import argparse
import array
import base64
import io
import json
import math
import os
import pathlib
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(*args, stdin=None):
    result = subprocess.run([str(x) for x in args], input=stdin, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    return result.stdout


def runtime_tool(name):
    bundled = ROOT / 'build/runtime' / name
    return bundled if bundled.is_file() else shutil.which(name)


def verify(tmp, backend, ffmpeg, ffprobe, web, provider_kind):
    aliyun = provider_kind == 'aliyun'
    compatible_path = '/compatible-mode/v1' if aliyun else '/v1'
    audio_model = 'qwen3.5-omni-plus' if aliyun else 'audio-fixture'
    visual_model = 'qwen3.8-max' if aliyun else 'visual-fixture'
    speech_model = 'qwen-audio-3.1-tts-flash' if aliyun else 'speech-fixture'
    tts_path = ('/api/v1/services/audio/tts/SpeechSynthesizer' if aliyun
                else '/v1/audio/speech')

    def ff(*args):
        return run(ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', *args)

    source = tmp / 'source.mp4'
    reference = tmp / 'reference.mp4'
    ff('-f', 'lavfi', '-i', 'color=blue:s=1920x1080:r=24:d=4',
       '-f', 'lavfi', '-i', 'sine=440:r=48000:d=4', '-c:v', 'libx264',
       '-c:a', 'aac', source)
    ref_voice = tmp / 'ref.aiff'
    run('/usr/bin/say', '-v', 'Daniel', '-o', ref_voice, 'A little adventure awaits.')
    ff('-f', 'lavfi', '-i', 'color=red:s=180x320:r=24:d=4',
       '-i', ref_voice, '-f', 'lavfi', '-i', 'sine=660:r=48000:d=4',
       '-filter_complex', '[1:a][2:a]amix=inputs=2:duration=longest[a]',
       '-map', '0:v', '-map', '[a]', '-c:v', 'libx264', '-c:a', 'aac', reference)
    cases = [
        {'speech': 'A little adventure.', 'caption': 'A little adventure.',
         'locale': 'en-GB', 'sub': 'en-GB', 'style': 'cinematic', 'voice': 'Daniel'},
        {'speech': 'Un viaje breve.', 'caption': 'Un petit voyage.',
         'locale': 'es-MX', 'sub': 'fr-FR', 'style': 'jazz', 'voice': 'Paulina'},
    ]
    speech_audio = {}
    for index, case in enumerate(cases):
        aiff = tmp / f'voice-{index}.aiff'
        wav = tmp / f'voice-{index}.wav'
        run('/usr/bin/say', '-v', case['voice'], '-o', aiff, case['speech'])
        ff('-i', aiff, '-ar', '24000' if aliyun else '48000', '-ac', '1', '-c:a', 'pcm_s16le', wav)
        speech_audio[case['speech']] = wav.read_bytes()

    events = []
    original_prompt = 'Make a short narrated cut.'
    revision_instruction = ('Make the narration Mexican Spanish, use French captions, and render a landscape mix.\n'
                            '配音语言: es-MX\n字幕语言: fr-FR\n配乐风格: jazz\n成片画幅: 16:9')

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_fixture(self, output, content_type):
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.end_headers()
            self.wfile.write(output)

        def do_GET(self):
            try:
                assert aliyun
                assert self.headers.get('Authorization') is None
                match = self.path.split('?')
                assert len(match) == 2 and match[1] == 'signature=local-test'
                assert match[0] in {'/tts/0.wav', '/tts/1.wav'}
                index = int(match[0][-5])
                case = cases[index]
                events.append('download:' + case['locale'])
                self.send_fixture(speech_audio[case['speech']], 'audio/wav')
            except Exception as error:
                events.append(f'fixture-error: {error!r}')
                self.send_error(400)

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                assert self.headers['Authorization'] == 'Bearer local-test'
                if self.path == tts_path:
                    assert body['model'] == speech_model
                    if aliyun:
                        synthesis = body['input']
                        case = next(c for c in cases if c['speech'] == synthesis['text'])
                        assert synthesis['format'] == 'wav' and synthesis['sample_rate'] == 24000
                        if case['locale'] == 'en-GB':
                            assert synthesis['voice'] == 'Eric_v3.1'
                            assert synthesis['language_hints'] == ['en']
                        else:
                            assert synthesis['voice'] == 'qwen-audio-3.1-tts-flash-brisa_es_mx'
                            assert synthesis['language_hints'] == ['other']
                        assert case['locale'] in synthesis['instruction']
                        assert 'low-pitched' in synthesis['instruction']
                        url = f'http://127.0.0.1:{self.server.server_port}/tts/{cases.index(case)}.wav?signature=local-test'
                        output = json.dumps({'output': {'audio': {'url': url}},
                                             'request_id': 'fixture-request'}).encode()
                        content_type = 'application/json'
                    else:
                        case = next(c for c in cases if c['speech'] == body['input'])
                        assert case['locale'] in body['instructions']
                        assert 'low-pitched' in body['instructions'] and body['voice'] == 'onyx'
                        assert body['response_format'] == 'wav'
                        output, content_type = speech_audio[body['input']], 'audio/wav'
                    events.append('speech:' + case['locale'])
                else:
                    assert self.path == compatible_path + '/chat/completions'
                    if body['model'] == audio_model:
                        parts = body['messages'][0]['content']
                        audio = next(p['input_audio'] for p in parts if p['type'] == 'input_audio')
                        assert audio['format'] == 'wav'
                        audio_data = audio['data']
                        assert body['modalities'] == ['text']
                        if aliyun:
                            assert body['stream'] is True
                            assert audio_data.startswith('data:') and ';base64,' in audio_data
                            audio_data = audio_data.split(';base64,', 1)[1]
                        else:
                            assert not body.get('stream') and not audio_data.startswith('data:')
                        with wave.open(io.BytesIO(base64.b64decode(audio_data, validate=True)), 'rb') as wav:
                            assert wav.getnchannels() == 1 and wav.getnframes() / wav.getframerate() > 3
                            assert any(wav.readframes(wav.getnframes()))
                        value = {'transcript': 'A little adventure awaits.', 'has_speech': True,
                                 'speech_locale': 'en-GB', 'voice_style': 'warm low-pitched',
                                 'has_music': True, 'music_style': 'cinematic', 'music_bpm': 84}
                        events.append('analysis')
                    else:
                        assert body['model'] == visual_model
                        parts = body['messages'][1]['content']
                        context = json.loads(parts[0]['text'])
                        assert context['reference_audio']['transcript'] == 'A little adventure awaits.'
                        assert sum(p['type'] == 'image_url' for p in parts) == 5
                        candidate = context['candidates'][0]
                        is_revision = 'revision' in context
                        case = cases[int(is_revision)]
                        if is_revision:
                            revision = context['revision']
                            assert revision['instruction'] == revision_instruction
                            assert original_prompt in context['prompt']
                            assert 'Edited folder; do not use for revisions.' not in context['prompt']
                            previous = revision['previous_mix']
                            assert previous['clips'] == [{'id': candidate['id'], 'start': 0, 'end': 4, 'speed': 1}]
                            assert previous['captions'][0]['text'].replace('\n', ' ') == cases[0]['caption']
                            assert previous['audio']['musicStyle'] == 'cinematic'
                            assert (previous['canvas']['width'], previous['canvas']['height']) == (1080, 1920)
                        caption = '字幕' if 'reject-han' in context['prompt'] else case['caption']
                        value = {'clips': [{'id': candidate['id'], 'candidate_index': 0,
                                            'start': 0, 'end': 4, 'speed': 1}],
                                 'audio_script': {'speech_locale': 'en-GB', 'subtitle_locale': 'en-GB',
                                     'music_style': 'cinematic', 'cues': [{'speech': case['speech'],
                                                                          'subtitle': caption}]}}
                        events.append('visual')
                    if aliyun and body['model'] == audio_model:
                        script = json.dumps(value)
                        deltas = [
                            {'choices': [{'delta': {'role': 'assistant'}, 'finish_reason': None}]},
                            *({'choices': [{'delta': {'content': script[i:i+19]}, 'finish_reason': None}]}
                              for i in range(0, len(script), 19)),
                            {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
                            {'choices': [], 'usage': {'completion_tokens': 42}},
                        ]
                        output = ''.join(f'data: {json.dumps(chunk)}\n\n' for chunk in deltas).encode()
                        output += b'data: [DONE]\n\n'
                        content_type = 'text/event-stream'
                    else:
                        output = json.dumps({'choices': [{'message': {'content': json.dumps(value)}}]}).encode()
                        content_type = 'application/json'
                self.send_fixture(output, content_type)
            except Exception as error:
                events.append(f'fixture-error: {error!r}')
                self.send_error(400)

    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, no_proxy='127.0.0.1,localhost', NO_PROXY='127.0.0.1,localhost',
               VIDEOSTUDIO_DATA_DIR=str(tmp / 'data'), VIDEOSTUDIO_FFMPEG=str(ffmpeg),
               VIDEOSTUDIO_FFPROBE=str(ffprobe))
    backend_url = f'http://127.0.0.1:{port}'
    def launch():
        return subprocess.Popen([*backend, '--port', str(port), '--web-dir', web], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    process = launch()

    local_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, payload=None, method=None, raw=None, *, byte_range=None, expected_status=200):
        data = json.dumps(payload).encode() if payload is not None else raw
        headers = {'Content-Type': 'application/json'} if payload is not None else {}
        if byte_range is not None:
            headers['Range'] = byte_range
        req = urllib.request.Request(backend_url + path, data=data, method=method, headers=headers)
        try:
            with local_http.open(req, timeout=90) as response:
                assert response.status == expected_status, (path, response.status)
                contents = response.read()
                if byte_range is not None:
                    assert response.headers['Accept-Ranges'] == 'bytes'
                    assert response.headers['Content-Range'].startswith('bytes 0-63/')
                    assert response.headers.get_content_type() == 'video/mp4'
                return json.loads(contents) if response.headers.get_content_type() == 'application/json' else contents
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'{error.code}: {error.read().decode()} / {events}') from error

    def wait_for_backend():
        for _ in range(160):
            if process.poll() is not None:
                raise RuntimeError(process.stdout.read().decode())
            try:
                if request('/api/health')['ok']:
                    break
            except urllib.error.URLError:
                pass
            time.sleep(0.15)
        else:
            raise RuntimeError('Backend never became ready.')

    try:
        wait_for_backend()
        assert 'api-audio-model' in request('/').decode()
        src = request('/api/media?name=source.mp4', raw=source.read_bytes())
        ref = request('/api/media?name=reference.mp4', raw=reference.read_bytes())
        payload = {'base_url': f'http://127.0.0.1:{provider.server_port}{compatible_path}',
                   'model': visual_model, 'audio_model': audio_model, 'tts_model': speech_model,
                   'api_key': 'local-test', 'complete_mix': True, 'reference_video_id': ref['id'],
                   'clips': [{'id': src['id'], 'start': 0, 'end': 4, 'speed': 1}]}
        folder = request('/api/task-folders', {'name': 'full-mix-verify', 'prompt': original_prompt,
                           'media_ids': [src['id']], 'reference_video_id': ref['id']}, expected_status=201)
        original_job = None
        original_bytes = None
        for index, case in enumerate(cases):
            if index:
                # Persisted history must stream from disk and recover the original context, even
                # after the task folder is edited and the backend loses its in-memory job list.
                request(f"/api/task-folders/{folder['id']}",
                        {'prompt': 'Edited folder; do not use for revisions.'}, method='PATCH')
                process.terminate()
                process.communicate(timeout=15)
                process = launch()
                wait_for_backend()
                original = request('/api/completed-mixes')['jobs'][0]
                assert original['id'] == original_job and original['can_revise']
                assert request(original['preview_url'], byte_range='bytes=0-63', expected_status=206) == original_bytes[:64]
                settings = {key: value for key, value in payload.items() if key not in ('clips', 'reference_video_id')}
                plan = request('/api/ai-plan', {**settings,
                    'revision': {'jobId': original_job, 'instruction': revision_instruction}})
                assert plan['revisionMediaIds'] == [src['id']]
            else:
                plan = request('/api/ai-plan', {**payload, 'prompt': folder['prompt']})
            mix = plan['audioMix']
            assert (mix['speechLocale'], mix['subtitleLocale'], mix['musicStyle']) == (
                case['locale'], case['sub'], case['style'])
            assert plan['textClips'][0]['text'] == case['caption']
            assert mix['referenceAudio']['has_speech'] and mix['referenceAudio']['has_music']
            assert len({mix['narrationId'], mix['musicId'], src['id'], ref['id']}) == 4
            render = json.loads(run('node', '-e',
                """const api=require(process.argv[1]);const [folder,source,plan]=JSON.parse(require('fs').readFileSync(0));
                process.stdout.write(JSON.stringify(api.createRenderPayload(folder,[source],plan,{completeMix:true})))""",
                web / 'batch-tasks.js', stdin=json.dumps([folder, src, plan]).encode()))
            render.update(taskFolderId=folder['id'],
                          revisionPlan={key: plan[key] for key in ('clips', 'textClips', 'audioMix')})
            if index:
                render.update(revisionOf=original_job, revisionInstruction=revision_instruction)
            job = request('/api/render', render)['job_id']
            for _ in range(180):
                status = request('/api/jobs/' + job)
                if status['status'] in ('done', 'error'):
                    break
                time.sleep(0.2)
            assert status['status'] == 'done', status
            name = 'full-mix-verify.mp4' if index == 0 else 'full-mix-verify (2).mp4'
            assert status['output_filename'] == name, status
            result = tmp / name
            result.write_bytes(request(status['download_url']))
            if not index:
                original_job, original_bytes = job, result.read_bytes()
            else:
                assert job != original_job and result.read_bytes() != original_bytes
                history = request('/api/completed-mixes')['jobs']
                assert [entry['id'] for entry in history] == [job, original_job], history
                revised, original = history
                assert revised['task_folder_id'] == folder['id']
                assert revised['revision_of'] == original_job
                assert revised['revision_instruction'] == revision_instruction
                assert revised['can_revise'] and original['can_revise']
                assert request(revised['preview_url'], byte_range='bytes=0-63', expected_status=206) == result.read_bytes()[:64]
                assert request(original['download_url']) == original_bytes
            meta = json.loads(run(ffprobe, '-v', 'error', '-show_streams', '-of', 'json', result))
            video, audio = meta['streams']
            width, height = (1920, 1080) if index else (1080, 1920)
            assert (video['width'], video['height'], video['codec_name'], audio['codec_name']) == (width,height,'h264','aac')
            assert plan['outputCanvas']['canvasSource'] == ('prompt' if index else 'reference')
            # Captions really changed pixels at the bottom during narration.
            def patch_at(position):
                return ff('-ss', str(position), '-i', result, '-vf', f'crop={width}:{height//5}:0:{height*4//5}',
                          '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'gray', 'pipe:1')
            assert max(patch_at(0.5)) > 160 and max(patch_at(3.5)) < 100
            def pcm(path, *options, stdin=None):
                return array.array('h', run(ffmpeg, '-hide_banner', '-loglevel', 'error',
                    '-i', path, *options, '-vn', '-f', 's16le', '-ac', '1', '-ar', '8000',
                    'pipe:1', stdin=stdin))
            # Confirm the actual TTS waveform is present in the rendered audio.
            # Allow small codec delays, while requiring more than the BGM alone.
            expected_speech = pcm('pipe:0', '-t', '2.5', stdin=speech_audio[case['speech']])[80:-80:4]
            rendered_speech = pcm(result, '-t', '2.7')
            energy = sum(sample * sample for sample in expected_speech)
            correlation = 0.0
            for start in range(0, 161, 4):
                excerpt = rendered_speech[start:start + 4 * len(expected_speech):4]
                excerpt_energy = sum(sample * sample for sample in excerpt)
                dot = sum(a * b for a, b in zip(expected_speech, excerpt))
                if excerpt_energy:
                    correlation = max(correlation, dot / math.sqrt(energy * excerpt_energy))
            assert correlation > 0.55, correlation
            # Music persists after narration. Compare to silence and rule out the source's 440Hz tone.
            sound = ff('-ss', '3.0', '-i', result, '-t', '0.7', '-vn', '-f', 's16le',
                       '-ac', '1', '-ar', '8000', 'pipe:1')
            samples = array.array('h', sound)
            rms = math.sqrt(sum(v*v for v in samples) / len(samples))
            amplitude_440 = abs(sum(v * complex(math.cos(i*math.tau*440/8000),
                                    math.sin(i*math.tau*440/8000)) for i,v in enumerate(samples))) / len(samples)
            assert rms > 20 and amplitude_440 < 700, (rms, amplitude_440)
            print(f'{name}: {width}×{height} H.264 + AAC, burned captions, speech correlation={correlation:.2f}, '
                  f'trailing music RMS={rms:.1f}; {case}')
        try:
            request('/api/ai-plan', {**payload, 'prompt': 'reject-han'})
        except RuntimeError as error:
            assert str(error).startswith('400:') and '汉字' in str(error)
        else:
            raise AssertionError('Han caption was not rejected')
        expected = ['analysis', 'visual', 'speech:en-GB']
        if aliyun:
            expected.append('download:en-GB')
        expected.extend(['analysis', 'visual', 'speech:es-MX'])
        if aliyun:
            expected.append('download:es-MX')
        expected.extend(['analysis', 'visual'])
        assert events == expected, events
        print(f'PASS ({provider_kind}): actual sampled soundtrack transport, OS speech, generated BGM, '
              'synchronized rendered captions, revision precedence, persisted history, byte-range playback, '
              'unchanged original and Han rejection. AI responses were fixtures.')
    finally:
        provider.shutdown()
        provider.server_close()
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=15)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packaged-app', type=pathlib.Path)
    parser.add_argument('--provider', choices=('generic', 'aliyun'), default='generic')
    args = parser.parse_args()
    import sys
    with tempfile.TemporaryDirectory(prefix='vf-complete-e2e-') as work:
        if args.packaged_app:
            resources = args.packaged_app.resolve() / 'Contents/Resources'
            runtime = resources / 'runtime'
            verify(pathlib.Path(work), [runtime / 'VideoFusionBackend'], runtime / 'ffmpeg',
                   runtime / 'ffprobe', resources / 'web', args.provider)
        else:
            verify(pathlib.Path(work), [sys.executable, ROOT / 'server/main.py'],
                   runtime_tool('ffmpeg'), runtime_tool('ffprobe'), ROOT / 'web', args.provider)
