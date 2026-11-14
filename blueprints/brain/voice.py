"""Gemini transcription and speech generation. Credentials remain on the server."""
import asyncio
import base64
import binascii
import json
import os
import socket
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request as URLRequest, urlopen

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/voice", tags=["voice"])
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 24 * 1024 * 1024
GOOGLE_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/wav", "audio/mp3", "audio/mpeg",
               "audio/aac", "audio/flac", "audio/m4a", "audio/mp4"}


def _model(kind):
    return os.environ.get("BLUEPRINTS_TRANSCRIBE_MODEL" if kind == "transcribe" else "BLUEPRINTS_TTS_MODEL",
                          "gemini-3.5-transcribe" if kind == "transcribe" else "gemini-3.8-flash-tts")


def _post_google(payload, *, auth_token=False):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise HTTPException(503, "Google voice is not configured. Set GEMINI_API_KEY on the server.")
    url = "https://generativelanguage.googleapis.com/v1beta/auth_tokens" if auth_token else GOOGLE_URL
    body = payload if auth_token else {**payload, "store": False}
    request = URLRequest(url, data=json.dumps(body).encode(), method="POST",
                         headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urlopen(request, timeout=90) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise HTTPException(502, "Google returned an oversized voice response.")
        return json.loads(raw)
    except HTTPError as error:
        # Only a redacted provider message is returned, never request headers or keys.
        try:
            message = json.loads(error.read(8192)).get("error", {}).get("message", "Voice request failed")
        except (ValueError, AttributeError):
            message = "Voice request failed"
        message = str(message).replace(key, "[REDACTED]")[:500]
        status = 429 if error.code == 429 else 503 if error.code in (401, 403) else 502
        raise HTTPException(status, f"Google voice: {message}") from None
    except (TimeoutError, socket.timeout):
        raise HTTPException(504, "Google voice timed out. Try a shorter recording or type your question.") from None
    except URLError:
        raise HTTPException(502, "Google voice could not connect. Try again or use typed questions.") from None
    except (ValueError, TypeError):
        raise HTTPException(502, "Google returned an unreadable voice response.") from None


def _output_content(reply):
    # Current REST Interactions output uses model_output steps; older revisions use outputs.
    steps = reply.get("steps", [])
    if steps:
        return [part for step in steps if step.get("type") == "model_output" for part in step.get("content", [])]
    return reply.get("outputs", reply.get("output", []))


@router.get("/config")
def config():
    return {"configured": bool(os.environ.get("GEMINI_API_KEY")),
            "transcription_model": _model("transcribe"), "speech_model": _model("speech"),
            "max_record_seconds": 30, "max_audio_bytes": MAX_AUDIO_BYTES,
            "live_transcription": os.environ.get("BLUEPRINTS_LIVE_TRANSCRIPTION", "1") != "0",
            "live_model": os.environ.get("BLUEPRINTS_LIVE_TRANSCRIBE_MODEL", "gemini-3.5-transcribe-live")}


class LiveToken(BaseModel):
    language: str = Field(default="auto", max_length=30, pattern=r"^(auto|[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2})$")


@router.post("/live-token")
async def live_token(body: LiveToken, response: Response):
    """Mint one constrained two-minute token, never expose the long-lived API key."""
    if os.environ.get("BLUEPRINTS_LIVE_TRANSCRIPTION", "1") == "0":
        raise HTTPException(503, "Live transcription is disabled on this server.")
    now = datetime.now(timezone.utc)
    model = os.environ.get("BLUEPRINTS_LIVE_TRANSCRIBE_MODEL", "gemini-3.5-transcribe-live")
    setup = {"model": "models/" + model.removeprefix("models/"),
             "generationConfig": {"responseModalities": ["TEXT"]},
             "inputAudioTranscription": {
                 "languageCodes": [] if body.language == "auto" else [body.language],
                 "customVocabulary": ["floor plan", "door", "window", "washbasin", "pohjapiirustus", "ikkuna"],
             }}
    payload = {"uses": 1, "expireTime": (now + timedelta(minutes=2)).isoformat(),
               "newSessionExpireTime": (now + timedelta(seconds=60)).isoformat(),
               "bidiGenerateContentSetup": setup}
    token = await asyncio.to_thread(_post_google, payload, auth_token=True)
    if not isinstance(token.get("name"), str) or not token["name"]:
        raise HTTPException(502, "Google did not return a live transcription token.")
    response.headers["Cache-Control"] = "no-store"
    return {"token": token["name"], "setup": setup, "max_record_seconds": 30}


@router.post("/transcribe")
async def transcribe(request: Request, language: str = "auto"):
    mime = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime not in AUDIO_TYPES:
        raise HTTPException(415, "Use WebM, OGG, WAV, MP3, M4A, AAC or FLAC audio.")
    # Bound streamed uploads, including ones without a Content-Length header.
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Recording is too large. Record a shorter question.")
    if not data:
        raise HTTPException(400, "No audio was recorded.")
    import re
    if language != "auto" and not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}", language):
        raise HTTPException(400, "Invalid language code.")
    payload = {"model": _model("transcribe"), "input": [{"type": "audio",
        "data": base64.b64encode(data).decode(), "mime_type": "audio/m4a" if mime == "audio/mp4" else mime}],
        "generation_config": {"transcription_config": {
            "language_codes": [] if language == "auto" else [language],
            "custom_vocabulary": ["floor plan", "door", "window", "washbasin", "pohjapiirustus", "ikkuna"],
        }}}
    reply = await asyncio.to_thread(_post_google, payload)
    text = " ".join(part.get("text", "") for part in _output_content(reply) if part.get("type") == "text").strip()
    if not text:
        raise HTTPException(422, "No speech was recognised. Try again or type your question.")
    if len(text) > 4000:
        raise HTTPException(422, "Question is too long. Record a shorter question.")
    return {"text": text, "model": _model("transcribe")}


class Speech(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/speak")
async def speak(body: Speech):
    if not body.text.strip():
        raise HTTPException(400, "There is no answer to read.")
    payload = {"model": _model("speech"), "input": [{"type": "user_input", "content": [
        {"type": "text", "text": body.text, "annotations": [{"type": "speech_metadata",
            "style": "Clear, calm, concise delivery. Read the provided transcript exactly."}]}]}],
        "response_format": {"type": "audio", "mime_type": "audio/wav"},
        "generation_config": {"speech_config": [{"voice": os.environ.get("BLUEPRINTS_TTS_VOICE", "Kore")}]}}
    reply = await asyncio.to_thread(_post_google, payload)
    audio = [part for part in _output_content(reply) if part.get("type") == "audio" and part.get("data")]
    if not audio:
        raise HTTPException(502, "Google did not return speech. The answer is still available on screen.")
    # A non-streaming TTS response contains one audio output. WAV must be returned as WAV, not raw PCM.
    part = audio[-1]
    if part.get("mime_type", "audio/wav").split(";", 1)[0] not in ("audio/wav", "audio/x-wav"):
        raise HTTPException(502, "Google returned an unsupported speech format.")
    try:
        data = base64.b64decode(part["data"], validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(502, "Google returned invalid speech data.") from None
    if not (data.startswith(b"RIFF") and data[8:12] == b"WAVE"):
        raise HTTPException(502, "Google did not return a valid WAV recording.")
    return Response(data, media_type="audio/wav", headers={"Cache-Control": "no-store"})
