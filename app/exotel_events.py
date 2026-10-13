"""
exotel_events.py
----------------
Turns the raw JSON text that Exotel sends over the WebSocket into a small,
easy-to-use Python object.

Message shapes below come from Exotel's public docs
(developer.exotel.com -> AgentStream -> WebSocket protocol):

  {"event": "connected"}
  {"event": "start", "sequence_number": "1", "stream_sid": "...",
   "start": {"stream_sid", "call_sid", "account_sid", "from", "to",
             "custom_parameters": {...},
             "media_format": {"encoding", "sample_rate", "bit_rate"}}}
  {"event": "media", "media": {"payload": "<base64 PCM>", ...}}
  {"event": "stop",  "stop":  {"reason": "stopped" | "callended"}}

Also exist (Voicebot applet only): "dtmf" and "mark". We only recognise
them for now and do nothing with them.

Every field is read with .get() so that a missing or extra field in a
message never crashes the call; we just get None for it.
"""

from __future__ import annotations  # lets us use modern type hints on Python 3.10

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ExotelEvent:
    """One message received from Exotel, in a friendlier shape."""

    event: str                                    # "connected", "start", "media", "stop", ...
    stream_sid: Optional[str] = None              # id of this audio stream (present on start/media)
    sequence_number: Optional[str] = None         # Exotel's message counter (as sent, a string)

    # Only filled for "start" events
    call_sid: Optional[str] = None                # id of the phone call
    from_number: Optional[str] = None             # caller number (personal data: mask before logging!)
    to_number: Optional[str] = None               # called number (personal data: mask before logging!)
    custom_parameters: Dict[str, Any] = field(default_factory=dict)
    media_format: Dict[str, Any] = field(default_factory=dict)  # encoding / sample_rate / bit_rate

    # Only filled for "media" events
    media_payload_b64: Optional[str] = None       # base64 audio; decoded in Step 1B, not here

    # Only filled for "stop" events
    stop_reason: Optional[str] = None             # "stopped" or "callended"

    # The untouched parsed JSON, handy for debugging
    raw: Dict[str, Any] = field(default_factory=dict)


def parse_event(text: str) -> ExotelEvent:
    """
    Parse one JSON text message from Exotel.

    Raises ValueError if the text is not valid JSON, is not a JSON object,
    or has no "event" field. The caller decides how to handle that.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Message is not valid JSON: {exc}") from exc

    if not isinstance(data, dict) or "event" not in data:
        raise ValueError("Message is not a JSON object with an 'event' field")

    event = ExotelEvent(
        event=str(data["event"]),
        stream_sid=data.get("stream_sid"),
        sequence_number=data.get("sequence_number"),
        raw=data,
    )

    # Each event type keeps its details in a nested object with the same
    # name as the event (e.g. "start": {...}). `or {}` guards against null.
    if event.event == "start":
        start = data.get("start") or {}
        event.stream_sid = event.stream_sid or start.get("stream_sid")
        event.call_sid = start.get("call_sid")
        event.from_number = start.get("from")
        event.to_number = start.get("to")
        event.custom_parameters = start.get("custom_parameters") or {}
        event.media_format = start.get("media_format") or {}

    elif event.event == "media":
        media = data.get("media") or {}
        event.media_payload_b64 = media.get("payload")

    elif event.event == "stop":
        stop = data.get("stop") or {}
        event.stop_reason = stop.get("reason")

    return event


def mask_phone(number: Optional[str]) -> str:
    """
    Hide most of a phone number before it goes into logs.
    "+919876543210" -> "+91*******210". Customer numbers are personal data.
    """
    if not number:
        return "unknown"
    if len(number) <= 6:
        return "*" * len(number)
    # Keep the first 3 and last 3 characters, mask everything in between
    return number[:3] + "*" * (len(number) - 6) + number[-3:]
