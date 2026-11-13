import unittest

from server import main


class AudioProviderSettingsTests(unittest.TestCase):
    def test_same_origin_uses_primary_key_even_with_explicit_default_port(self):
        result = main.audio_provider_settings(
            {'audio_base_url': 'https://gateway.example:443/v1'},
            'audio', 'https://gateway.example/compatible-mode/v1', 'primary-test')
        self.assertEqual(result, ('https://gateway.example:443/v1', 'primary-test'))

    def test_different_origin_requires_its_own_key(self):
        data = {'tts_base_url': 'https://speech.example/api/v1'}
        with self.assertRaisesRegex(main.RequestError, '自己的 API 密钥'):
            main.audio_provider_settings(data, 'tts', 'https://visual.example/v1', 'primary-test')
        data['tts_api_key'] = 'speech-test'
        result = main.audio_provider_settings(data, 'tts', 'https://visual.example/v1', 'primary-test')
        self.assertEqual(result, ('https://speech.example/api/v1', 'speech-test'))
