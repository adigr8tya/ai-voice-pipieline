"""
fake_exotel_client.py
---------------------
Pretends to be Exotel so we can test our server WITHOUT a real phone call.

It connects to our /ws endpoint and sends, in order:
    connected -> start -> media chunks -> stop
using the message shapes from Exotel's docs.

Two modes:
  - default: 30 chunks of silence (Step 1A plumbing test)
  - --wav FILE: streams a real recording in 100 ms chunks at real-time
    speed, like a live caller (Step 1B/1C). The file must be 16-bit mono
    8 kHz; use scripts/convert_to_8k.py to prepare it.

Usage (server must already be running):
    python scripts/fake_exotel_client.py
    python scripts/fake_exotel_client.py --wav samples/hindi_8k.wav
"""

import argparse
import asyncio
import base64
import json
import sys
import wave

import websockets

SAMPLE_RATE = 8000
# 100 ms of audio at 8 kHz, 16-bit (2 bytes) mono = 8000 * 0.1 * 2 = 1600 bytes.
# The TDD says Exotel sends chunks of at least 100 ms.
CHUNK_BYTES = 1600


def load_wav_chunks(path):
    """Read a 16-bit mono 8 kHz .wav and split it into 100 ms chunks."""
    with wave.open(path, "rb") as wav:
        fmt = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
        if fmt != (1, 2, SAMPLE_RATE):
            sys.exit(
                f"{path} is channels={fmt[0]}, bytes/sample={fmt[1]}, rate={fmt[2]}. "
                f"Expected mono, 16-bit, 8000 Hz. Convert it first:\n"
                f"  python scripts/convert_to_8k.py {path} converted_8k.wav"
            )
        pcm = wav.readframes(wav.getnframes())
    return [pcm[i:i + CHUNK_BYTES] for i in range(0, len(pcm), CHUNK_BYTES)]


async def run(url, chunks):
    async with websockets.connect(url) as ws:
        # 1) "connected": Exotel says hello right after the handshake
        await ws.send(json.dumps({"event": "connected"}))

        # 2) "start": call details. Phone numbers here are fake test numbers.
        await ws.send(json.dumps({
            "event": "start",
            "sequence_number": "1",
            "stream_sid": "MZ_TEST_STREAM",
            "start": {
                "stream_sid": "MZ_TEST_STREAM",
                "call_sid": "CA_TEST_CALL",
                "account_sid": "AC_TEST_ACCOUNT",
                "from": "+910000000001",
                "to": "+910000000002",
                "custom_parameters": {},
                "media_format": {
                    "encoding": "audio/x-raw",
                    "sample_rate": str(SAMPLE_RATE),
                    "bit_rate": "16",
                },
            },
        }))

        # 3) "media": one base64 chunk every 100 ms, like a real call
        for i, chunk in enumerate(chunks):
            await ws.send(json.dumps({
                "event": "media",
                "sequence_number": str(i + 2),
                "stream_sid": "MZ_TEST_STREAM",
                "media": {"payload": base64.b64encode(chunk).decode("ascii")},
            }))
            await asyncio.sleep(0.1)

        # 4) "stop": the call ended (caller hung up)
        await ws.send(json.dumps({
            "event": "stop",
            "stream_sid": "MZ_TEST_STREAM",
            "stop": {"reason": "callended"},
        }))

    total = sum(len(c) for c in chunks)
    print(f"Done: sent connected, start, {len(chunks)} media chunks ({total} bytes), stop")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fake Exotel client for local testing")
    parser.add_argument("--url", default="ws://localhost:8000/ws", help="our server's WebSocket URL")
    parser.add_argument("--wav", help="16-bit mono 8 kHz .wav to stream (default: silence)")
    parser.add_argument("--chunks", type=int, default=30, help="silent chunks to send when no --wav")
    args = parser.parse_args()

    if args.wav:
        chunk_list = load_wav_chunks(args.wav)
    else:
        chunk_list = [bytes(CHUNK_BYTES)] * args.chunks   # all-zero bytes = silence

    asyncio.run(run(args.url, chunk_list))