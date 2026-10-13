"""
trigger_call.py
---------------
Step 1D: asks Exotel to call a phone number and stream the call's audio to
our Voice server. This is TDD Figure 1, arrow 2 (Backend -> Exotel). For
now it is a script; on Day 3 the Backend API will make this same request.

Uses Exotel's flow-less "Connect Voice AI" request (Exotel developer docs,
AgentStream -> Developer guide):

  POST https://<api_key>:<api_token>@<subdomain>/v1/accounts/<account_sid>/calls/connect
    from        = number to dial (E.164, e.g. +9198xxxxxxxx)   required
    callerid    = your ExoPhone                                  required
    streamurl   = our wss:// address (max 600 characters)        required
    streamtype  = bidirectional                                  required

When the person answers, Exotel opens a WebSocket to `streamurl` and sends
connected -> start -> media ... -> stop, which our /ws endpoint handles.

Settings come from .env (never typed on the command line, never logged):
  EXOTEL_ACCOUNT_SID, EXOTEL_API_KEY, EXOTEL_API_TOKEN,
  EXOTEL_SUBDOMAIN (api.in.exotel.com = Mumbai, api.exotel.com = Singapore),
  EXOTEL_CALLER_ID, STREAM_URL (e.g. wss://xxxx.trycloudflare.com/ws)

Usage:
    python scripts/trigger_call.py --to +91XXXXXXXXXX
"""

import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

REQUIRED = ["EXOTEL_ACCOUNT_SID", "EXOTEL_API_KEY", "EXOTEL_API_TOKEN",
            "EXOTEL_CALLER_ID", "STREAM_URL"]


def mask_phone(number):
    """+919876543210 -> +91*******210 (so test numbers don't end up in logs/screenshots)."""
    if not number or len(number) <= 6:
        return "***"
    return number[:3] + "*" * (len(number) - 6) + number[-3:]


def main():
    parser = argparse.ArgumentParser(description="Start an outbound Exotel call streamed to our server")
    parser.add_argument("--to", required=True, help="number to call, E.164 format, e.g. +9198XXXXXXXX")
    args = parser.parse_args()

    load_dotenv()   # read settings from .env

    # Stop early, with a clear message, if any setting is missing
    missing = [name for name in REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        sys.exit(f"Missing in .env: {', '.join(missing)}")

    sid = os.environ["EXOTEL_ACCOUNT_SID"].strip()
    subdomain = os.environ.get("EXOTEL_SUBDOMAIN", "api.in.exotel.com").strip()
    stream_url = os.environ["STREAM_URL"].strip()

    if not stream_url.startswith(("wss://", "ws://")):
        sys.exit("STREAM_URL must start with wss:// (e.g. wss://xxxx.trycloudflare.com/ws)")

    url = f"https://{subdomain}/v1/accounts/{sid}/calls/connect"
    form = {
        "from": args.to,
        "callerid": os.environ["EXOTEL_CALLER_ID"].strip(),
        "streamurl": stream_url,
        "streamtype": "bidirectional",
    }

    print(f"Asking Exotel to call {mask_phone(args.to)}, streaming to {stream_url}")
    # API key + token are sent as HTTP Basic auth (username, password); never printed
    response = httpx.post(
        url,
        data=form,
        auth=(os.environ["EXOTEL_API_KEY"].strip(), os.environ["EXOTEL_API_TOKEN"].strip()),
        timeout=20,
    )

    print(f"Exotel replied: HTTP {response.status_code}")
    if response.is_success:
        print("Call requested. Answer your phone, speak, and watch the server terminal.")
    else:
        # Show Exotel's error so we can see what it did not accept
        print(response.text)
        sys.exit(1)


if __name__ == "__main__":
    main()
