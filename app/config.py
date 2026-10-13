"""
config.py
---------
Reads settings from the ".env" file (and the real environment).

Secrets such as DEEPGRAM_API_KEY live ONLY in ".env", which git ignores.
Never hard-code keys in code and never log them.

Each setting is read through a small function (not a constant) so the
value is looked up when it is needed. That lets tests change settings
without restarting Python.
"""

import os

from dotenv import load_dotenv

# Copy the values from .env into the environment once, at startup.
# Values already set in the real environment win over .env.
load_dotenv()


def deepgram_api_key() -> str:
    """Deepgram API key, or "" when it is not set (STT then stays off)."""
    return os.environ.get("DEEPGRAM_API_KEY", "").strip()


def deepgram_model() -> str:
    """Deepgram model. nova-3 is the model named in the TDD's cost table."""
    return os.environ.get("DEEPGRAM_MODEL", "nova-3")


def deepgram_language() -> str:
    """
    "multi" = Nova-3 multilingual, which per Deepgram's docs includes Hindi
    and English and handles mixing the two in one sentence (code-switching).
    """
    return os.environ.get("DEEPGRAM_LANGUAGE", "multi")
