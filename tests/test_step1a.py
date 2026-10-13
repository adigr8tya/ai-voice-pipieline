"""
Tests for Step 1A: the Exotel event parser and the /ws endpoint.
Run with:  pytest -v
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.exotel_events import mask_phone, parse_event
from app.main import app


# ---------- parser tests ----------

def test_parse_connected():
    ev = parse_event('{"event": "connected"}')
    assert ev.event == "connected"


def test_parse_start_reads_call_details():
    msg = {
        "event": "start",
        "stream_sid": "MZ1",
        "start": {
            "stream_sid": "MZ1",
            "call_sid": "CA1",
            "from": "+910000000001",
            "to": "+910000000002",
            "media_format": {"encoding": "audio/x-raw", "sample_rate": "8000", "bit_rate": "16"},
        },
    }
    ev = parse_event(json.dumps(msg))
    assert ev.call_sid == "CA1"
    assert ev.stream_sid == "MZ1"
    assert ev.media_format["sample_rate"] == "8000"


def test_parse_media_keeps_base64_payload():
    ev = parse_event('{"event": "media", "media": {"payload": "AAAA"}}')
    assert ev.media_payload_b64 == "AAAA"


def test_parse_stop_reason():
    ev = parse_event('{"event": "stop", "stop": {"reason": "callended"}}')
    assert ev.stop_reason == "callended"


def test_parse_rejects_bad_input():
    with pytest.raises(ValueError):
        parse_event("not json")
    with pytest.raises(ValueError):
        parse_event('{"no_event_field": 1}')


def test_mask_phone_hides_middle_digits():
    assert mask_phone("+919876543210") == "+91*******210"
    assert mask_phone(None) == "unknown"


# ---------- WebSocket endpoint tests ----------

def test_health_endpoint():
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}


def test_full_call_sequence_is_handled(caplog):
    """connected -> start -> 3 x media -> stop should be logged and finish cleanly."""
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="voice"):
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"event": "connected"}))
            ws.send_text(json.dumps({
                "event": "start",
                "stream_sid": "MZ1",
                "start": {"stream_sid": "MZ1", "call_sid": "CA1",
                          "from": "+919876543210", "to": "+918047491899"},
            }))
            for _ in range(3):
                ws.send_text(json.dumps({"event": "media", "media": {"payload": "AAAA"}}))
            ws.send_text(json.dumps({"event": "stop", "stop": {"reason": "callended"}}))

    log_text = caplog.text
    assert "Exotel event: start" in log_text
    assert "total_media_chunks=3" in log_text      # all 3 audio chunks were counted
    assert "reason=callended" in log_text
    assert "+919876543210" not in log_text          # full phone number must NOT be logged


def test_bad_message_does_not_crash_the_call(caplog):
    """A garbage message is skipped and the call continues normally."""
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="voice"):
        with client.websocket_connect("/ws") as ws:
            ws.send_text("this is not json")
            ws.send_text(json.dumps({"event": "stop", "stop": {"reason": "stopped"}}))
    assert "Ignoring unreadable message" in caplog.text
    assert "reason=stopped" in caplog.text
