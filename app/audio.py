"""
audio.py
--------
Step 1B: everything about the caller's audio after Exotel sends it.

1. decode_payload(): turns Exotel's base64 text into raw audio bytes.
   Per the TDD (arrow 4) and Exotel's docs, those bytes are 16-bit PCM,
   mono, 8 kHz by default. These raw bytes are what Step 1C sends to
   Deepgram (encoding=linear16, sample_rate=8000, channels=1).

2. DebugWavRecorder: writes the decoded bytes of one call into a .wav file
   so you can PLAY it and hear whether the audio is correct. If it sounds
   clear, the bytes and format are right. If it sounds like static/noise,
   the byte order or sample rate is wrong, and Deepgram would fail too.

   This is a DEBUG tool only. The TDD says real call recordings come from
   Exotel's own recording (Section 6), not from us. Saved files contain
   customer voice (personal data), so turn this off (SAVE_DEBUG_AUDIO=0)
   once testing is done, and never commit the debug_audio folder.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import wave
from pathlib import Path
from typing import Optional

SAMPLE_WIDTH_BYTES = 2      # 16-bit audio = 2 bytes per sample
CHANNELS = 1                # mono
DEFAULT_SAMPLE_RATE = 8000  # Exotel's default (TDD arrow 4)


def decode_payload(payload_b64: Optional[str]) -> bytes:
    """
    Turn the base64 text from a "media" event into raw PCM bytes.
    Returns b"" for a missing payload. Raises ValueError for broken base64,
    so the caller can skip that one chunk without ending the call.
    """
    if not payload_b64:
        return b""
    try:
        # validate=True: reject text that is not proper base64
        return base64.b64decode(payload_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Audio payload is not valid base64: {exc}") from exc


def sample_rate_from_media_format(media_format: dict) -> int:
    """
    Read the sample rate from the "start" event's media_format.
    Exotel sends it as a string (e.g. "8000"); fall back to 8000 if missing
    or unreadable.
    """
    try:
        return int(media_format.get("sample_rate", DEFAULT_SAMPLE_RATE))
    except (TypeError, ValueError):
        return DEFAULT_SAMPLE_RATE


def debug_audio_enabled() -> bool:
    """SAVE_DEBUG_AUDIO=0 turns saving off. On by default while testing."""
    return os.environ.get("SAVE_DEBUG_AUDIO", "1") != "0"


def debug_audio_dir() -> Path:
    """Folder for the debug .wav files (DEBUG_AUDIO_DIR, default ./debug_audio)."""
    return Path(os.environ.get("DEBUG_AUDIO_DIR", "debug_audio"))


class DebugWavRecorder:
    """
    Writes one call's audio into a .wav file, chunk by chunk.
    Writing as we go (instead of keeping everything in memory) means a
    long call does not use more and more RAM.
    """

    def __init__(self, stream_sid: Optional[str], sample_rate: int) -> None:
        folder = debug_audio_dir()
        folder.mkdir(parents=True, exist_ok=True)

        # Keep only safe characters from stream_sid for the file name
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", stream_sid or "unknown")
        self.path = folder / f"call_{safe_id}.wav"
        self.sample_rate = sample_rate
        self.bytes_written = 0

        # The wave module writes the .wav header for us
        self._wav = wave.open(str(self.path), "wb")
        self._wav.setnchannels(CHANNELS)
        self._wav.setsampwidth(SAMPLE_WIDTH_BYTES)
        self._wav.setframerate(sample_rate)

    def write(self, pcm: bytes) -> None:
        """Append one chunk of raw PCM audio."""
        self._wav.writeframes(pcm)
        self.bytes_written += len(pcm)

    def close(self) -> float:
        """Finish the file and return how many seconds of audio it holds."""
        self._wav.close()
        return self.bytes_written / (self.sample_rate * SAMPLE_WIDTH_BYTES * CHANNELS)
