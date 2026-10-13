"""
Tests for Step 1B: decoding audio and saving the debug .wav.
Run with:  pytest -v
"""

import base64
import json
import math
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from app.audio import DebugWavRecorder, decode_payload, sample_rate_from_media_format
from app.main import app


def make_tone(seconds=0.5, rate=8000, freq=440):
    """A short 16-bit little-endian sine tone, as raw PCM bytes."""
    n = int(seconds * rate)
    return b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(n)
    )


@pytest.fixture
def audio_dir(tmp_path, monkeypatch):
    """Send debug .wav files to a temporary folder during tests."""
    monkeypatch.setenv("DEBUG_AUDIO_DIR", str(tmp_path))
    monkeypatch.setenv("SAVE_DEBUG_AUDIO", "1")
    return tmp_path


# ---------- decoding ----------

def test_decode_round_trip():
    pcm = make_tone(0.1)
    assert decode_payload(base64.b64encode(pcm).decode()) == pcm


def test_decode_empty_and_bad_payload():
    assert decode_payload(None) == b""
    with pytest.raises(ValueError):
        decode_payload("not base64 !!!")


def test_sample_rate_reading():
    assert sample_rate_from_media_format({"sample_rate": "8000"}) == 8000
    assert sample_rate_from_media_format({"sample_rate": "16000"}) == 16000
    assert sample_rate_from_media_format({}) == 8000          # default
    assert sample_rate_from_media_format({"sample_rate": "x"}) == 8000


# ---------- debug wav ----------

def test_recorder_writes_playable_wav(audio_dir):
    pcm = make_tone(0.5)
    rec = DebugWavRecorder("MZ/../weird id", 8000)   # unsafe chars get cleaned
    rec.write(pcm)
    seconds = rec.close()

    assert rec.path.parent == audio_dir
    assert abs(seconds - 0.5) < 0.01
    with wave.open(str(rec.path), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 8000)
        assert wav.readframes(wav.getnframes()) == pcm   # bytes match exactly


# ---------- full call through /ws ----------

def test_call_audio_is_decoded_and_saved(audio_dir):
    """Every byte sent by 'Exotel' ends up in the saved .wav, unchanged."""
    pcm = make_tone(1.0)                                   # 16000 bytes
    chunks = [pcm[i:i + 1600] for i in range(0, len(pcm), 1600)]

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(json.dumps({
            "event": "start", "stream_sid": "MZ_T",
            "start": {"stream_sid": "MZ_T", "media_format": {"sample_rate": "8000"}},
        }))
        for c in chunks:
            ws.send_text(json.dumps({"event": "media",
                                     "media": {"payload": base64.b64encode(c).decode()}}))
        ws.send_text(json.dumps({"event": "stop", "stop": {"reason": "callended"}}))

    with wave.open(str(audio_dir / "call_MZ_T.wav"), "rb") as wav:
        assert wav.readframes(wav.getnframes()) == pcm


def test_bad_audio_chunk_is_skipped(audio_dir):
    """A broken chunk is skipped; the good ones are still saved."""
    good = make_tone(0.1)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"event": "start", "stream_sid": "MZ_B", "start": {}}))
        ws.send_text(json.dumps({"event": "media", "media": {"payload": "%%%bad%%%"}}))
        ws.send_text(json.dumps({"event": "media",
                                 "media": {"payload": base64.b64encode(good).decode()}}))
        ws.send_text(json.dumps({"event": "stop", "stop": {"reason": "stopped"}}))

    with wave.open(str(audio_dir / "call_MZ_B.wav"), "rb") as wav:
        assert wav.readframes(wav.getnframes()) == good


def test_saving_can_be_turned_off(audio_dir, monkeypatch):
    monkeypatch.setenv("SAVE_DEBUG_AUDIO", "0")
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"event": "start", "stream_sid": "MZ_OFF", "start": {}}))
        ws.send_text(json.dumps({"event": "stop", "stop": {"reason": "stopped"}}))
    assert not (audio_dir / "call_MZ_OFF.wav").exists()
