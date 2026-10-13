# Airtel Voice AI Agent: Voice server



**Current status:** Task 1 (Exotel → Voice server → Deepgram STT) is built.
Steps 1A to 1C are tested; **Step 1D (real phone call) is not yet confirmed.**

---

## Build order

The pipeline is built one task at a time, and each task is confirmed working before the next starts.

| Task | What | Status |
|---|---|---|
| **1** | WebSocket + telephony + STT: caller's speech becomes text in the logs | 1A–1C ✅ · 1D ⏳ needs one real call |
| 2 | Add the LLM (Groq, Llama 3.1 8B): text in, reply text out | Not started |
| 3 | Add TTS (Sarvam): reply text becomes audio | Not started |
| 4 | Send that audio back to the caller through Exotel | Not started |

Task 1 steps:

| Step | What | Status |
|---|---|---|
| 1A | `/ws` endpoint receives and logs Exotel events | ✅ tested |
| 1B | Decode base64 audio to raw PCM; save a debug `.wav` | ✅ tested, audio plays clearly |
| 1C | Stream the audio to Deepgram; log partial and final text | ✅ tested with recordings |
| 1D | Real outbound call through Exotel | ⏳ code written, not yet run |

---

## How the data flows (TDD Figure 1)

```
Customer ──phone──► Exotel ──WebSocket (/ws)──► Voice server ──stream──► Deepgram
                    (arrow 3)    (arrow 4)       app/main.py   (arrow 5)    │
                                                      ▲                      │
                                                      └──── text ────────────┘
```

- **Arrow 2** (start the call): `scripts/trigger_call.py`. The Backend API will make this request on Day 3.
- **Arrow 4, Exotel → us:** JSON events `connected`, `start`, `media`, `stop`. Each audio payload is base64, raw 16-bit little-endian PCM, mono, 8 kHz by default.
- **Arrow 5, us ↔ Deepgram:** the decoded bytes go to Deepgram unchanged (`linear16`), and partial and final text comes back.
- **Not built yet:** arrow 4 in the other direction (reply audio to Exotel), arrow 6 (Groq), arrow 7 (TTS), and arrows 1, 8, 9 and 10 (Campaign UI, Backend, PostgreSQL, recordings).

---

## Project layout

```
app/
  main.py               Voice server: FastAPI app, /ws endpoint (one WebSocket per call)
  config.py             reads settings from .env
  exotel_events.py      parses Exotel WebSocket messages; masks phone numbers for logs
  audio.py              base64 -> raw PCM; debug .wav recorder
  stt/
    base.py             STTProvider interface (lets Sarvam replace Deepgram later)
    deepgram.py         Deepgram live-streaming client
scripts/
  trigger_call.py       starts a real outbound call through Exotel (Step 1D)
  fake_exotel_client.py pretends to be Exotel, for testing without a phone
  convert_to_8k.py      converts any .wav to 16-bit mono 8 kHz
tests/                  pytest tests (24); never call the real Deepgram or Exotel
requirements.txt        Python packages
pytest.ini              tells pytest where the code and tests are
.env.example            list of settings; copy to .env and fill in
.gitignore              keeps .env and debug_audio/ out of git
```

---

## Setup (Windows PowerShell, Python 3.10)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # then fill in your keys in .env
pytest -v                     # expect 24 passed
```

If activation says "running scripts is disabled", run this once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

---

## Settings (`.env`)



| Setting | Used for | Default |
|---|---|---|
| `SAVE_DEBUG_AUDIO` | `1` saves each call as a `.wav`, `0` turns it off | `1` |
| `DEBUG_AUDIO_DIR` | folder for the debug `.wav` files | `debug_audio` |
| `DEEPGRAM_API_KEY` | Deepgram key; without it, calls run with STT off | none |
| `DEEPGRAM_MODEL` | Deepgram model | `nova-3` |
| `DEEPGRAM_LANGUAGE` | `multi` covers Hindi and English, including both mixed in one sentence | `multi` |
| `EXOTEL_ACCOUNT_SID`, `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN` | Exotel API credentials | none |
| `EXOTEL_SUBDOMAIN` | `api.in.exotel.com` (Mumbai) or `api.exotel.com` (Singapore) | `api.in.exotel.com` |
| `EXOTEL_CALLER_ID` | your ExoPhone | none |
| `STREAM_URL` | public address of `/ws`, e.g. `wss://xxxx.trycloudflare.com/ws` | none |

---

## Test without a phone (Steps 1A–1C)

```powershell
# 1. Prepare a recording (any .wav) in the format Exotel sends
mkdir samples
python scripts\convert_to_8k.py my_recording.wav samples\test_8k.wav

# 2. Terminal 1: start the server
uvicorn app.main:app --port 8000

# 3. Terminal 2: stream the recording like a live call
python scripts\fake_exotel_client.py --wav samples\test_8k.wav
```

Expected in terminal 1:
- `Deepgram connected`, then `STT partial: ...` and `STT FINAL: ... stt_lag=…ms`.
- `Debug audio saved | file=debug_audio\call_MZ_TEST_STREAM.wav`. Play this file to hear exactly what Deepgram received.

---

## Real phone call (Step 1D)

```powershell
# One-time install of the tunnel tool (makes localhost reachable by Exotel)
winget install --id Cloudflare.cloudflared

# Terminal 1
uvicorn app.main:app --port 8000

# Terminal 2: prints https://xxxx.trycloudflare.com
cloudflared tunnel --url http://localhost:8000
```

Then:
1. Set `STREAM_URL=wss://xxxx.trycloudflare.com/ws` in `.env`: use `wss` instead of `https` and add `/ws` at the end. The address changes every time the tunnel restarts.
2. Terminal 3: `python scripts\trigger_call.py --to +91XXXXXXXXXX`
3. Answer the phone and speak. Terminal 1 shows `start`, `Deepgram connected`, `STT FINAL: ...` and, when you hang up, `stop`.

This uses Exotel's flow-less "Connect Voice AI" request: `calls/connect` with `from`, `callerid`, `streamurl` and `streamtype=bidirectional`.

---

## Known open points

- **Exotel outbound calls:** confirm "Connect Voice AI" is enabled on our plan (TDD "Check on Day 1"). The fallback is a call flow with a Voicebot applet pointing at the same `wss://.../ws`, which needs no server changes.
- **Real-call format:** one real call will confirm the event shapes and the sample rate. The server reads the rate from the `start` event, so no code change is expected.
- **8 kHz on Deepgram:** used in Deepgram's own example but not in a documented list of supported rates. It is confirmed only once real recordings transcribe correctly.
- **Personal data:**
  - Transcripts in the logs and the debug `.wav` files hold customer speech.
  - This is acceptable for testing only. Set `SAVE_DEBUG_AUDIO=0` and review transcript logging before live campaigns.
- **For Task 4, the audio we send to Exotel** must be raw 16-bit little-endian PCM, mono, at the call's sample rate, in chunks of 3,200 to 100,000 bytes that are multiples of 320. TTS output will need converting to this format.
- 