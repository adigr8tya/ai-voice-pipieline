"""
base.py
-------
The common shape every STT provider follows.

The rest of the code (main.py) talks ONLY to this interface, never to
Deepgram directly. The TDD lists "Deepgram or Sarvam" for STT, so if we
switch to Sarvam later we add app/stt/sarvam.py with the same methods and
main.py does not change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Transcript:
    """One piece of text coming back from the STT."""

    text: str
    is_final: bool                  # False = may still change, True = confirmed text
    speech_final: bool = False      # True = the caller paused (end of what they said)
    confidence: Optional[float] = None
    stt_lag_ms: Optional[float] = None  # time from sending that audio to getting this text


# The function main.py gives the provider; it is called for every transcript.
TranscriptCallback = Callable[[Transcript], None]


class STTProvider(ABC):
    """Streaming speech-to-text: raw audio bytes in, Transcript objects out."""

    @abstractmethod
    async def connect(self) -> None:
        """Open the streaming connection. Raises an exception if it fails."""

    @abstractmethod
    async def send_audio(self, pcm: bytes) -> None:
        """Send one chunk of raw 16-bit PCM audio. Must never raise."""

    @abstractmethod
    async def close(self) -> None:
        """Finish the stream: flush the last text, then disconnect. Must never raise."""
