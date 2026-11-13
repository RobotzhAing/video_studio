import array
import base64
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from server import ai_audio


def provider_answer(analysis):
    return json.dumps({'choices': [{'message': {'content': json.dumps(analysis)}}]}).encode()


def omni_events(*payloads):
    return b''.join(b'data: ' + json.dumps(payload).encode() + b'\n\n' for payload in payloads)


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is required')
class AudioMediaTests(unittest.TestCase):
    def test_analysis_transmits_real_bounded_excerpts_and_keeps_locale_and_tempo(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'reference.m4a'
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                '-i', 'sine=frequency=440:sample_rate=24000:duration=40', str(source)], check=True)

            def inspect_request(base_url, endpoint, key, body, *_):
                self.assertEqual(endpoint, '/chat/completions')
                self.assertEqual(body['modalities'], ['text'])
                self.assertEqual(body['model'], 'audio-capable-test-model')
                excerpts = [part['input_audio'] for part in body['messages'][0]['content']
                            if part['type'] == 'input_audio']
                self.assertEqual(len(excerpts), 3)
                for excerpt in excerpts:
                    self.assertEqual(excerpt['format'], 'wav')
                    with wave.open(io.BytesIO(base64.b64decode(excerpt['data']))) as audio:
                        self.assertEqual(audio.getnchannels(), 1)
                        self.assertEqual(audio.getframerate(), 24000)
                        self.assertLessEqual(audio.getnframes(), 12 * 24000)
                        self.assertGreater(audio.getnframes(), 11.95 * 24000)
                        pcm = array.array('h')
                        pcm.frombytes(audio.readframes(2400))
                        self.assertGreater(max(pcm), 1000)
                return provider_answer({'has_speech': True, 'has_music': True,
                    'transcript': 'Good morning.', 'speech_locale': 'EN-gb', 'voice_style': 'deep, relaxed',
                    'music_style': 'Lo-Fi', 'music_bpm': 78.4})

            with patch.object(ai_audio, '_post_json', side_effect=inspect_request):
                result = ai_audio.analyze_reference_audio(source=source, duration=40.0, ffmpeg='ffmpeg',
                    base_url='https://provider.test/v1', api_key='test-key', audio_model='audio-capable-test-model')
            self.assertEqual(result['transcript'], 'Good morning.')
            self.assertEqual(result['speech_locale'], 'en-GB')
            self.assertEqual(result['music_style'], 'lofi')
            self.assertEqual(result['music_bpm'], 78)
            self.assertAlmostEqual(result['sampled_seconds'], 36.0, delta=0.1)
            self.assertTrue(result['sampling_limited'])

    def test_qwen_omni_uses_streamed_text_and_data_uri_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'reference.wav'
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                '-i', 'sine=frequency=440:duration=1', str(source)], check=True)

            def provider(_url, endpoint, _key, body, *_):
                self.assertEqual(endpoint, '/chat/completions')
                self.assertTrue(body['stream'])
                self.assertEqual(body['modalities'], ['text'])
                excerpt = body['messages'][0]['content'][2]['input_audio']
                scheme, data = excerpt['data'].split(',', 1)
                self.assertEqual(scheme, 'data:audio/wav;base64')
                self.assertEqual(body['stream_options'], {'include_usage': True})
                with wave.open(io.BytesIO(base64.b64decode(data))) as audio:
                    self.assertEqual(audio.getnframes(), 24000)
                result = json.dumps({'has_speech': True, 'has_music': True, 'transcript': 'Hello.',
                    'speech_locale': 'en-GB', 'voice_style': 'warm', 'music_style': 'jazz', 'music_bpm': 93})
                return omni_events(
                    {'choices': [{'delta': {'reasoning_content': 'ignored', 'content': None}}]},
                    {'choices': [{'delta': {'content': result[:30]}}]},
                    {'choices': [{'delta': {'content': result[30:]}, 'finish_reason': 'stop'}]},
                    {'choices': [], 'usage': {'completion_tokens': 100}}) + b'data: [DONE]\n\n'

            with patch.object(ai_audio, '_post_json', side_effect=provider):
                result = ai_audio.analyze_reference_audio(source=source, duration=1.0, ffmpeg='ffmpeg',
                    base_url='https://example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1',
                    api_key='test-key', audio_model='qwen3.5-omni-plus')
            self.assertEqual(result['speech_locale'], 'en-GB')
            self.assertEqual(result['music_style'], 'jazz')
            self.assertEqual(result['music_bpm'], 93)

    def test_tts_preserves_input_and_requests_native_voice_guidance_and_rejects_silence(self):
        def wav_at_24k(amplitude):
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(array.array('h', [amplitude, amplitude, -amplitude, -amplitude] * 3000).tobytes())
            return buf.getvalue()

        def provider(base_url, endpoint, key, body, *_):
            self.assertEqual(endpoint, '/audio/speech')
            self.assertEqual(body['input'], 'Olá, viajantes.')
            self.assertEqual(body['voice'], 'onyx')
            self.assertEqual(body['response_format'], 'wav')
            self.assertIn('pt-BR', body['instructions'])
            self.assertIn('deep, relaxed', body['instructions'])
            # Return a waveform below the Nyquist frequency, leaving resampling verifiable.
            return wav_at_24k(16000)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'line.wav'
            def synthesize():
                ai_audio.synthesize_speech(text='Olá, viajantes.', locale='pt-BR',
                    voice_style='deep, relaxed', base_url='https://provider.test/v1',
                    api_key='test-key', tts_model='provider-tts', tts_voice='',
                    ffmpeg='ffmpeg', output=output)
            with patch.object(ai_audio, '_post_json', side_effect=provider):
                synthesize()
            with wave.open(str(output)) as audio:
                self.assertEqual(audio.getframerate(), 48000)
                self.assertEqual(audio.getnframes(), 24000)
            with patch.object(ai_audio, '_post_json', return_value=wav_at_24k(0)):
                with self.assertRaisesRegex(ai_audio.AudioError, '静音'):
                    synthesize()
            self.assertFalse(output.exists())


class AudioPolicyTests(unittest.TestCase):
    def test_token_plan_rejects_omni_without_extracting_or_sending_audio(self):
        with patch.object(ai_audio, '_run_ffmpeg') as ffmpeg, patch.object(ai_audio, '_post_json') as post:
            with self.assertRaisesRegex(ai_audio.AudioError, 'Token Plan'):
                ai_audio.analyze_reference_audio(source=Path('reference.mp4'), duration=5, ffmpeg='ffmpeg',
                    base_url='https://cn-beijing.maas.qwenaiapi.com/compatible-mode/v1',
                    api_key='sk-sp-test', audio_model='qwen3.5-omni-plus')
            ffmpeg.assert_not_called()
            post.assert_not_called()

    def test_stream_error_rejects_truncation_and_hides_untrusted_messages(self):
        incomplete = omni_events({'choices': [{'delta': {'content': '{}'}}]})
        with self.assertRaisesRegex(ai_audio.AudioError, '未完成'):
            ai_audio._streamed_chat_json(incomplete)
        error = omni_events({'error': {'code': 'ModelNotFound', 'message': 'secret raw response'}})
        with self.assertRaisesRegex(ai_audio.AudioError, 'ModelNotFound') as raised:
            ai_audio._streamed_chat_json(error)
        self.assertNotIn('secret', str(raised.exception))

    def test_qwen_speech_native_route_and_signed_download_without_key(self):
        base = 'https://cn-beijing.maas.qwenaiapi.com/compatible-mode/v1'
        response = json.dumps({'output': {'finish_reason': 'stop', 'audio': {'url':
            'http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/pre/speech.wav?signature=ephemeral'}}}).encode()
        with patch.object(ai_audio.urllib.request, 'build_opener') as make:
            opener = make.return_value
            from contextlib import nullcontext
            opener.open.side_effect = [nullcontext(io.BytesIO(response)), nullcontext(io.BytesIO(b'speech'))]
            audio = ai_audio._qwen_speech(base_url=base, api_key='test-key',
                model='qwen-audio-3.1-tts-flash', text='Viaje.', locale='es-MX', voice_style='warm',
                voice='', instructions='Speak in es-MX with a warm voice.')
        self.assertEqual(audio, b'speech')
        post, get = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(post.full_url,
            'https://cn-beijing.maas.qwenaiapi.com/api/v1/services/audio/tts/SpeechSynthesizer')
        self.assertEqual(post.get_header('Authorization'), 'Bearer test-key')
        self.assertEqual(json.loads(post.data)['input'], {'text': 'Viaje.',
            'voice': 'qwen-audio-3.1-tts-flash-brisa_es_mx', 'format': 'wav', 'sample_rate': 24000,
            'instruction': 'Speak in es-MX with a warm voice.', 'language_hints': ['other']})
        self.assertEqual(get.full_url,
            'https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/pre/speech.wav?signature=ephemeral')
        self.assertIsNone(get.get_header('Authorization'))
        for url in ('http://127.0.0.1:8080/private', 'https://elsewhere.test/speech.wav',
                    'https://cn-beijing.maas.qwenaiapi.com:8080/wav',
                    'https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com.attacker.test/wav'):
            with self.assertRaisesRegex(ai_audio.AudioError, '未受信任'):
                ai_audio._download_qwen_audio(url, base)

    def test_music_outputs_non_silent_distinct_styles_and_respects_length_and_tempo(self):
        with tempfile.TemporaryDirectory() as tmp:
            digests = set()
            for index, (style, bpm) in enumerate([('classical', None), ('lofi', None), ('lofi', 96)]):
                out = Path(tmp) / f'track-{index}.wav'
                ai_audio.generate_music(style=style, duration=1.375, output=out, bpm=bpm)
                with wave.open(str(out)) as track:
                    self.assertEqual(track.getframerate(), 48000)
                    self.assertEqual(track.getnframes(), 66000)
                    data = track.readframes(track.getnframes())
                self.assertNotEqual(data, b'\0' * len(data))
                self.assertEqual(data[:2] + data[-2:], b'\0' * 4)
                digests.add(hashlib.sha256(data).digest())
            self.assertEqual(len(digests), 3)
            with self.assertRaisesRegex(ai_audio.AudioError, '无法生成该配乐风格'):
                ai_audio.generate_music(style='unknown', duration=2, output=out)

    def test_diagnostic_classifies_model_rejection_without_echoing_provider_text(self):
        detail = ai_audio._safe_provider_fields({'error': {'code': 'invalid_request_error',
            'message': 'Model does not exist or you have no access: private-account-data'}})
        self.assertIn('invalid_request_error', detail)
        self.assertIn('模型不可用', detail)
        self.assertNotIn('private-account-data', detail)

    def test_api_errors_exclude_response_body_and_secrets(self):
        mock_response = io.BytesIO(b'{"error":{"code":"InvalidApiKey","message":"sensitive-api-body"}}')
        exc = HTTPError('https://provider.test/v1/audio/speech', 401, 'secret reason', {}, mock_response)
        with patch.object(ai_audio.urllib.request, 'build_opener') as make_opener:
            make_opener.return_value.open.side_effect = exc
            with self.assertRaisesRegex(ai_audio.AudioError, 'HTTP 401.*InvalidApiKey') as raised:
                ai_audio._post_json('https://provider.test/v1/chat/completions',
                    '/audio/speech', 'api-secret', {}, '语音合成', 1024)
        self.assertNotIn('secret', str(raised.exception))
        self.assertTrue(mock_response.closed)


if __name__ == '__main__':
    unittest.main()
