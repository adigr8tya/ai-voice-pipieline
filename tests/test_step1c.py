"""
Tests for Step 1C: Deepgram STT.
None of these call the real Deepgram. Run with:  pytest -v
"""

import asyncio
import base64
import json
import logging
from urllib.parse import parse_qs, urlparse

import websockets
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.stt.deepgram import DeepgramSTT, build_url, parse_message


# ---------- URL and message parsing ----------

def test_build_url_has_our_settings():
    query = parse_qs(urlparse(build_url("nova-3", "multi", 8000)).query)
    assert query["model"] == ["nova-3"]
    assert query["language"] == ["multi"]
    assert query["encoding"] == ["linear16"]
    assert query["sample_rate"] == ["8000"]
    assert query["channels"] == ["1"]
    assert query["interim_results"] == ["true"]


def test_parse_results_message():
    msg = {"type": "Results", "start": 0.0, "duration": 1.04, "is_final": True,
           "speech_final": True,
           "channel": {"alternatives": [{"transcript": " namaste ", "confidence": 0.96}]}}
    r = parse_message(json.dumps(msg))
    assert r["text"] == "namaste" and r["is_final"] and r["speech_final"]
    assert r["confidence"] == 0.96 and r["duration"] == 1.04


def test_parse_ignores_other_messages():
    assert parse_message('{"type": "Metadata"}') is None
    assert parse_message("not json") is None


# ---------- DeepgramSTT against a fake local Deepgram server ----------

def test_deepgram_client_with_fake_server():
    """
    A tiny local server pretends to be Deepgram. Checks that our client sends
    the auth header and the raw audio bytes, sends CloseStream at the end,
    and passes the returned text to our callback.
    """
    received_audio = bytearray()
    seen = {}
    transcripts = []

    async def fake_deepgram(ws):
        seen["auth"] = ws.request.headers.get("Authorization")
        async for msg in ws:
            if isinstance(msg, bytes):
                received_audio.extend(msg)
                if len(received_audio) == len(msg):    # first chunk: send a partial
                    await ws.send(json.dumps({"type": "Results", "is_final": False,
                        "channel": {"alternatives": [{"transcript": "hel"}]}}))
            elif json.loads(msg).get("type") == "CloseStream":
                seen["close_stream"] = True
                await ws.send(json.dumps({"type": "Results", "start": 0.0, "duration": 0.2,
                    "is_final": True, "speech_final": True,
                    "channel": {"alternatives": [{"transcript": "hello", "confidence": 0.9}]}}))
                await ws.close()

    async def scenario():
        async with websockets.serve(fake_deepgram, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            stt = DeepgramSTT(api_key="test-key", model="nova-3", language="multi",
                              sample_rate=8000, on_transcript=transcripts.append,
                              base_url=f"ws://127.0.0.1:{port}")
            await stt.connect()
            for _ in range(3):
                await stt.send_audio(b"\x01\x00" * 800)     # 3 x 100 ms
            await asyncio.sleep(0.1)
            await stt.close()

    asyncio.run(scenario())

    assert seen["auth"] == "Token test-key"
    assert len(received_audio) == 3 * 1600          # every byte arrived
    assert seen.get("close_stream") is True
    assert [t.text for t in transcripts] == ["hel", "hello"]
    assert transcripts[0].is_final is False
    assert transcripts[1].is_final and transcripts[1].stt_lag_ms is not None


# ---------- /ws endpoint forwards audio to the STT ----------

class FakeSTT:
    """Stands in for Deepgram inside the /ws endpoint."""
    def __init__(self):
        self.audio = bytearray()
        self.closed = False

    async def send_audio(self, pcm):
        self.audio.extend(pcm)

    async def close(self):
        self.closed = True


def test_ws_forwards_audio_to_stt(monkeypatch):
    fake = FakeSTT()
    opened_with = {}

    async def fake_open_stt(sample_rate):
        opened_with["rate"] = sample_rate
        return fake

    monkeypatch.setattr(main_module, "open_stt", fake_open_stt)
    pcm = b"\x10\x00" * 800

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"event": "start", "stream_sid": "MZ_S",
                                 "start": {"media_format": {"sample_rate": "8000"}}}))
        for _ in range(2):
            ws.send_text(json.dumps({"event": "media",
                                     "media": {"payload": base64.b64encode(pcm).decode()}}))
        ws.send_text(json.dumps({"event": "stop", "stop": {"reason": "callended"}}))

    assert opened_with["rate"] == 8000
    assert bytes(fake.audio) == pcm * 2      # exact bytes, nothing lost
    assert fake.closed                       # STT closed when the call ended


def test_call_works_without_deepgram_key(caplog):
    """No key in .env: STT is skipped with a warning; the call still works."""
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="voice"):
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"event": "start", "stream_sid": "MZ_N", "start": {}}))
            ws.send_text(json.dumps({"event": "stop", "stop": {"reason": "stopped"}}))
    assert "DEEPGRAM_API_KEY is not set" in caplog.text
    assert "reason=stopped" in caplog.text
