"""
deepgram.py
-----------
Deepgram live (streaming) speech-to-text over a WebSocket.

Everything here follows Deepgram's public docs:
  - endpoint:  wss://api.deepgram.com/v1/listen
  - auth:      header  "Authorization: Token <API_KEY>"
  - settings:  query parameters (model, language, encoding, sample_rate, ...)
  - audio:     sent as binary WebSocket frames
  - results:   JSON text messages with "type": "Results", containing
               channel.alternatives[0].transcript, is_final, speech_final,
               start and duration (seconds of audio)
  - keepalive: {"type": "KeepAlive"} as a TEXT frame; without audio or a
               keepalive for 10 seconds Deepgram closes the stream
  - closing:   {"type": "CloseStream"}; Deepgram sends the last results,
               then closes the connection

Audio format: Exotel's audio (16-bit little-endian PCM, mono, 8 kHz) matches
Deepgram's "linear16", so we forward the bytes exactly as decoded, with no
conversion.
"""

from __future__ import annotations

import asyncio
import bisect
import json
import logging
import time
from typing import List, Optional
from urllib.parse import urlencode

import websockets

from app.stt.base import STTProvider, Transcript, TranscriptCallback

logger = logging.getLogger("voice.stt.deepgram")

DEEPGRAM_URL = "wss://api.deepgram.com/v1/listen"
BYTES_PER_SAMPLE = 2          # 16-bit audio
KEEPALIVE_EVERY_S = 4         # Deepgram recommends every 3-5 seconds
CLOSE_TIMEOUT_S = 5           # how long to wait for the last results on close


def build_url(model: str, language: str, sample_rate: int, base_url: str = DEEPGRAM_URL) -> str:
    """Build the Deepgram URL with our settings as query parameters."""
    params = {
        "model": model,               # nova-3
        "language": language,         # multi = Hindi + English (code-switching)
        "encoding": "linear16",       # 16-bit little-endian PCM (what Exotel sends)
        "sample_rate": sample_rate,   # 8000 by default (from Exotel's start event)
        "channels": 1,                # mono
        "interim_results": "true",    # also send partial text while the caller speaks
        "endpointing": 100,           # ms of pause = end of speech (Deepgram's tip for code-switching)
        "smart_format": "true",       # nicer numbers, dates, punctuation
    }
    return f"{base_url}?{urlencode(params)}"


def parse_message(text: str) -> Optional[dict]:
    """
    Read one JSON message from Deepgram.
    Returns a dict with transcript fields for a "Results" message, otherwise
    None (e.g. "Metadata" or "SpeechStarted" messages, which we don't need yet).
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("type") != "Results":
        return None

    alternatives = (data.get("channel") or {}).get("alternatives") or [{}]
    best = alternatives[0]               # Deepgram puts the best guess first
    return {
        "text": (best.get("transcript") or "").strip(),
        "confidence": best.get("confidence"),
        "is_final": bool(data.get("is_final")),
        "speech_final": bool(data.get("speech_final")),
        "start": data.get("start"),       # seconds into the audio
        "duration": data.get("duration"),  # seconds of audio this result covers
    }


class DeepgramSTT(STTProvider):
    """One Deepgram stream for one phone call."""

    def __init__(self, api_key: str, model: str, language: str,
                 sample_rate: int, on_transcript: TranscriptCallback,
                 base_url: str = DEEPGRAM_URL) -> None:
        # base_url is only changed by tests (to point at a fake local server)
        self._api_key = api_key                 # never logged
        self._url = build_url(model, language, sample_rate, base_url)
        self._sample_rate = sample_rate
        self._on_transcript = on_transcript

        self._ws = None                         # the open WebSocket
        self._receiver: Optional[asyncio.Task] = None   # reads results in the background
        self._keepalive: Optional[asyncio.Task] = None  # sends KeepAlive during silence
        self._alive = False                     # False once closed or broken
        self._last_send = 0.0                   # when we last sent anything

        # For measuring STT lag: after each chunk we remember
        # (seconds of audio sent so far, wall-clock time it was sent).
        self._audio_bytes_sent = 0
        self._sent_audio_end: List[float] = []
        self._sent_wall_time: List[float] = []

    async def connect(self) -> None:
        # The key goes in the header only; the URL (which we log) has no secret.
        self._ws = await websockets.connect(
            self._url,
            additional_headers={"Authorization": f"Token {self._api_key}"},
        )
        self._alive = True
        self._last_send = time.monotonic()
        self._receiver = asyncio.create_task(self._receive_loop())
        self._keepalive = asyncio.create_task(self._keepalive_loop())
        logger.info("Deepgram connected | %s", self._url)

    async def send_audio(self, pcm: bytes) -> None:
        if not self._alive or not pcm:
            return
        try:
            await self._ws.send(pcm)            # bytes -> binary frame
        except websockets.exceptions.ConnectionClosed as exc:
            # Don't crash the call; just stop sending. (The TDD's
            # "reconnect STT once" rule is Day 2 failure handling.)
            logger.error("Deepgram connection lost while sending audio: %s", exc)
            self._alive = False
            return

        now = time.monotonic()
        self._last_send = now
        self._audio_bytes_sent += len(pcm)
        self._sent_audio_end.append(
            self._audio_bytes_sent / (self._sample_rate * BYTES_PER_SAMPLE)
        )
        self._sent_wall_time.append(now)

    async def close(self) -> None:
        if self._keepalive:
            self._keepalive.cancel()
        if self._alive:
            self._alive = False
            try:
                # Ask Deepgram to finish: it sends the last results, then closes
                await self._ws.send(json.dumps({"type": "CloseStream"}))
            except websockets.exceptions.ConnectionClosed:
                pass
        if self._receiver:
            try:
                await asyncio.wait_for(self._receiver, timeout=CLOSE_TIMEOUT_S)
            except asyncio.TimeoutError:
                logger.warning("Deepgram did not finish within %ss; closing anyway", CLOSE_TIMEOUT_S)
                self._receiver.cancel()
            except Exception:  # receiver errors are already logged inside it
                pass
        if self._ws is not None:
            await self._ws.close()
        logger.info("Deepgram closed")

    # ---------- background tasks ----------

    async def _receive_loop(self) -> None:
        """Read every message Deepgram sends until the connection ends."""
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    continue                    # Deepgram sends text (JSON) only
                result = parse_message(message)
                if result is None or not result["text"]:
                    continue                    # not a transcript, or empty (silence)
                self._on_transcript(Transcript(
                    text=result["text"],
                    is_final=result["is_final"],
                    speech_final=result["speech_final"],
                    confidence=result["confidence"],
                    stt_lag_ms=self._lag_ms(result) if result["is_final"] else None,
                ))
        except websockets.exceptions.ConnectionClosedError as exc:
            # Closed with an error (e.g. bad key, wrong format, 10 s timeout)
            logger.error("Deepgram closed with an error: code=%s reason=%s",
                         exc.rcvd.code if exc.rcvd else None,
                         exc.rcvd.reason if exc.rcvd else None)
        except Exception:
            logger.exception("Deepgram receiver crashed")
        finally:
            self._alive = False

    async def _keepalive_loop(self) -> None:
        """During silence (no audio for a while) send KeepAlive so Deepgram stays open."""
        try:
            while self._alive:
                await asyncio.sleep(1)
                if time.monotonic() - self._last_send >= KEEPALIVE_EVERY_S:
                    await self._ws.send(json.dumps({"type": "KeepAlive"}))  # text frame
                    self._last_send = time.monotonic()
        except (asyncio.CancelledError, websockets.exceptions.ConnectionClosed):
            pass

    def _lag_ms(self, result: dict) -> Optional[float]:
        """
        STT lag for a final result = now minus the moment we SENT the last
        piece of audio this text covers. Shows how long Deepgram took.
        (Part of the per-stage timing the TDD asks for in Section 3.)
        """
        start, duration = result.get("start"), result.get("duration")
        if start is None or duration is None or not self._sent_audio_end:
            return None
        audio_end = start + duration
        # Find the first chunk whose audio reaches audio_end
        i = bisect.bisect_left(self._sent_audio_end, audio_end)
        i = min(i, len(self._sent_wall_time) - 1)
        return (time.monotonic() - self._sent_wall_time[i]) * 1000
