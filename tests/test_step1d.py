"""
Tests for Step 1D (the parts we can test without a real phone call).
Run with:  pytest -v
"""

import json
import logging

from fastapi.testclient import TestClient

from app.main import app


def test_json_sent_as_binary_frames_is_understood(caplog):
    """If Exotel sends its JSON as binary frames instead of text, the call still works."""
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="voice"):
        with client.websocket_connect("/ws") as ws:
            ws.send_bytes(json.dumps({"event": "connected"}).encode())
            ws.send_bytes(json.dumps({"event": "start", "stream_sid": "MZ_BIN", "start": {}}).encode())
            ws.send_bytes(json.dumps({"event": "media", "media": {"payload": "AAAA"}}).encode())
            ws.send_bytes(json.dumps({"event": "stop", "stop": {"reason": "callended"}}).encode())
    assert "Exotel event: connected" in caplog.text
    assert "total_media_chunks=1" in caplog.text
    assert "reason=callended" in caplog.text


def test_disconnect_without_stop_is_handled(caplog):
    """Caller drops (connection closes with no 'stop'): logged, no crash."""
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="voice"):
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"event": "start", "stream_sid": "MZ_DROP", "start": {}}))
        # leaving the "with" block closes the connection without a stop event
    assert "disconnected without a stop event" in caplog.text
    assert "Call session finished | stream_sid=MZ_DROP" in caplog.text
