"""Exercise voice HTTP endpoints with mocked Google responses; no paid API calls."""
import base64
import io
import json
import unittest
import wave
from unittest.mock import patch
from urllib.error import HTTPError

import httpx

from blueprints import api
from blueprints.brain import voice


def output(part):
    return {"status": "completed", "steps": [{"type": "model_output", "content": [part]}]}


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.login = patch.object(api, "PASSWORD_HASH", None)
        self.login.start(); self.addCleanup(self.login.stop)
        self.env = patch.dict("os.environ", {"GEMINI_API_KEY": "test-only-secret"})
        self.env.start(); self.addCleanup(self.env.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="https://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_transcription_forwards_audio_and_language_without_answering(self):
        with patch.object(voice, "_post_google", return_value=output({"type": "text", "text": "Kuinka monta ovea?"})) as post:
            response = await self.client.post("/api/voice/transcribe?language=fi-FI", content=b"recording",
                                               headers={"Content-Type": "audio/webm;codecs=opus"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "Kuinka monta ovea?")
        payload = post.call_args.args[0]
        self.assertEqual(base64.b64decode(payload["input"][0]["data"]), b"recording")
        self.assertEqual(payload["generation_config"]["transcription_config"]["language_codes"], ["fi-FI"])

    async def test_rejects_empty_unsupported_and_oversized_audio_before_google(self):
        with patch.object(voice, "_post_google") as post, patch.object(voice, "MAX_AUDIO_BYTES", 5):
            for audio, mime, status in [(b"", "audio/webm", 400), (b"a", "image/png", 415), (b"123456", "audio/webm", 413)]:
                response = await self.client.post("/api/voice/transcribe", content=audio, headers={"Content-Type": mime})
                self.assertEqual(response.status_code, status)
            post.assert_not_called()

    async def test_no_recognised_speech_is_not_an_empty_question(self):
        with patch.object(voice, "_post_google", return_value=output({"type": "text", "text": ""})):
            response = await self.client.post("/api/voice/transcribe", content=b"silence", headers={"Content-Type": "audio/webm"})
        self.assertEqual(response.status_code, 422)

    async def test_speech_returns_wav_and_sends_exact_answer(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(b"\0\0" * 20)
        data = buffer.getvalue()
        with patch.object(voice, "_post_google", return_value=output({"type": "audio", "mime_type": "audio/wav",
            "data": base64.b64encode(data).decode()})) as post:
            response = await self.client.post("/api/voice/speak", json={"text": "Raportoitu vastaus. 19."})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, data)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(post.call_args.args[0]["input"][0]["content"][0]["text"], "Raportoitu vastaus. 19.")

    async def test_invalid_speech_cannot_be_sent_as_playable_audio(self):
        with patch.object(voice, "_post_google", return_value=output({"type": "audio", "mime_type": "audio/wav", "data": "not-base64"})):
            response = await self.client.post("/api/voice/speak", json={"text": "19 windows"})
        self.assertEqual(response.status_code, 502)

    async def test_existing_login_protects_voice_endpoints(self):
        with patch.object(api, "PASSWORD_HASH", "enabled"):
            for path in ("/api/voice/config", "/api/voice/transcribe", "/api/voice/speak"):
                response = await self.client.get(path) if path.endswith("config") else await self.client.post(path)
                self.assertEqual(response.status_code, 401)

    async def test_config_never_exposes_key(self):
        response = await self.client.get("/api/voice/config")
        self.assertTrue(response.json()["configured"])
        self.assertNotIn("test-only-secret", response.text)

    def test_google_errors_redact_key_and_disable_interaction_storage(self):
        failure = HTTPError("https://google.invalid", 403, "Forbidden", {}, io.BytesIO(json.dumps({
            "error": {"message": "Invalid test-only-secret"}}).encode()))
        with patch.object(voice, "urlopen", side_effect=failure) as call:
            with self.assertRaises(voice.HTTPException) as error:
                voice._post_google({"model": "test"})
        self.assertEqual(error.exception.status_code, 503)
        self.assertNotIn("test-only-secret", error.exception.detail)
        payload = json.loads(call.call_args.args[0].data)
        self.assertFalse(payload["store"])


if __name__ == "__main__":
    unittest.main()
