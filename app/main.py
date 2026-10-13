"""
main.py
-------
This is the "Voice server" box in the TDD (Figure 1). Exotel (or our fake
Exotel client) connects to its WebSocket endpoint /ws, one connection per call.

What it does right now:
  - Step 1A: accepts the connection, parses every Exotel message, logs it
  - Step 1B: decodes each audio chunk from base64 into raw PCM bytes,
             counts the bytes, and (debug only) saves the call as a .wav
  - Step 1C: streams those bytes to Deepgram (TDD arrow 5) and logs the
             partial and final text that comes back

What it does NOT do yet (later steps): call the LLM (Task 2), speak back
(Tasks 3-4).

Run it with:
    uvicorn app.main:app --port 8000
"""

from __future__ import annotations

import logging

from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from app import config
from app.audio import (
    DebugWavRecorder,
    debug_audio_enabled,
    decode_payload,
    sample_rate_from_media_format,
)
from app.exotel_events import mask_phone, parse_event
from app.stt.base import STTProvider, Transcript
from app.stt.deepgram import DeepgramSTT

# Basic logging setup: timestamps + level + message, printed to the terminal
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("voice")

app = FastAPI(title="Airtel Voice AI Agent (Task 1)")


@app.get("/health")
async def health() -> dict:
    """Simple check that the server is up (open it in a browser)."""
    return {"status": "ok"}


def log_transcript(t: Transcript) -> None:
    """
    Step 1C: print what the STT heard. Task 2 will send final text to the LLM
    from here instead of only logging it.
    Note: transcripts are what the customer said (personal data). Logging
    them is fine for local testing; review this before real campaigns.
    """
    if t.is_final:
        lag = f"{t.stt_lag_ms:.0f}ms" if t.stt_lag_ms is not None else "n/a"
        logger.info("STT FINAL: %s | speech_final=%s confidence=%s stt_lag=%s",
                    t.text, t.speech_final, t.confidence, lag)
    else:
        logger.info("STT partial: %s", t.text)


async def open_stt(sample_rate: int) -> Optional[STTProvider]:
    """
    Step 1C: open a Deepgram stream for this call.
    Returns None (and the call carries on without STT) if there is no API key
    or the connection fails, so a Deepgram problem never breaks the call.
    """
    api_key = config.deepgram_api_key()
    if not api_key:
        logger.warning("DEEPGRAM_API_KEY is not set in .env: STT is off for this call")
        return None
    stt = DeepgramSTT(
        api_key=api_key,
        model=config.deepgram_model(),
        language=config.deepgram_language(),
        sample_rate=sample_rate,
        on_transcript=log_transcript,
    )
    try:
        await stt.connect()
    except Exception as exc:
        # e.g. wrong key (HTTP 401), no internet. The key itself is never logged.
        logger.error("Could not connect to Deepgram: %s: %s", type(exc).__name__, exc)
        return None
    return stt


@app.websocket("/ws")
async def exotel_ws(websocket: WebSocket) -> None:
    """
    One phone call = one WebSocket connection to this endpoint.
    This function runs once per call and ends when the call ends.
    """
    # Accept the incoming WebSocket handshake from Exotel
    await websocket.accept()
    logger.info("WebSocket connected")

    # Per-call state. Each call has its own copy because this function
    # runs separately for every connection.
    stream_sid = None      # id of the audio stream (from the "start" event)
    media_count = 0        # how many audio chunks we have received
    audio_bytes = 0        # total decoded audio bytes (Step 1B)
    recorder = None        # debug .wav writer, created on "start" (Step 1B)
    stt = None             # Deepgram stream, opened on "start" (Step 1C)

    try:
        while True:
            # Wait for the next message. Exotel's docs show JSON messages but
            # don't say whether they come as text or binary frames, so accept both.
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            if message.get("text") is not None:
                text = message["text"]
            else:
                text = (message.get("bytes") or b"").decode("utf-8", errors="replace")

            # Turn the JSON text into an object; skip messages we can't read
            try:
                event = parse_event(text)
            except ValueError as exc:
                logger.warning("Ignoring unreadable message: %s", exc)
                continue

            if event.event == "connected":
                logger.info("Exotel event: connected")

            elif event.event == "start":
                stream_sid = event.stream_sid
                # Phone numbers are masked: never log customer numbers in full
                logger.info(
                    "Exotel event: start | stream_sid=%s call_sid=%s from=%s to=%s media_format=%s",
                    stream_sid,
                    event.call_sid,
                    mask_phone(event.from_number),
                    mask_phone(event.to_number),
                    event.media_format,
                )
                # Step 1B: start a debug .wav for this call, using the sample
                # rate Exotel reports (8000 by default)
                rate = sample_rate_from_media_format(event.media_format)
                if debug_audio_enabled() and recorder is None:
                    recorder = DebugWavRecorder(stream_sid, rate)
                # Step 1C: open the STT stream at the same sample rate
                if stt is None:
                    stt = await open_stt(rate)

            elif event.event == "media":
                media_count += 1

                # Step 1B: base64 text -> raw 16-bit PCM bytes.
                # A broken chunk is skipped; one bad chunk must not end the call.
                try:
                    pcm = decode_payload(event.media_payload_b64)
                except ValueError as exc:
                    logger.warning("Skipping audio chunk %d: %s", media_count, exc)
                    continue

                audio_bytes += len(pcm)
                # 16-bit audio = 2 bytes per sample, so a chunk should have an
                # even number of bytes. An odd count hints at a format problem.
                if len(pcm) % 2 != 0:
                    logger.warning("Audio chunk %d has an odd byte count (%d)", media_count, len(pcm))
                if recorder is not None:
                    recorder.write(pcm)
                # Step 1C: forward the same raw bytes to the STT (TDD arrow 5)
                if stt is not None:
                    await stt.send_audio(pcm)

                # Audio arrives about every 100 ms, so logging each chunk would
                # flood the terminal. Log only the first one and then every 50th.
                if media_count == 1 or media_count % 50 == 0:
                    logger.info(
                        "Exotel event: media | chunks received so far=%d chunk_bytes=%d total_bytes=%d",
                        media_count,
                        len(pcm),
                        audio_bytes,
                    )

            elif event.event == "stop":
                logger.info(
                    "Exotel event: stop | reason=%s total_media_chunks=%d total_audio_bytes=%d",
                    event.stop_reason,
                    media_count,
                    audio_bytes,
                )
                break  # the call is over, leave the loop

            else:
                # dtmf / mark / anything unknown: note it and carry on
                logger.info("Exotel event: %s (not handled yet)", event.event)

    except WebSocketDisconnect:
        # Exotel closed the connection without sending "stop" (e.g. network drop)
        logger.warning(
            "WebSocket disconnected without a stop event | stream_sid=%s chunks=%d",
            stream_sid,
            media_count,
        )
    finally:
        # Step 1C: let the STT send its last text, then disconnect
        if stt is not None:
            await stt.close()
        # Step 1B: finish the debug .wav (also runs if the call dropped)
        if recorder is not None:
            seconds = recorder.close()
            logger.info("Debug audio saved | file=%s duration=%.1fs", recorder.path, seconds)
        logger.info("Call session finished | stream_sid=%s", stream_sid)