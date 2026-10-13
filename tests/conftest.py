"""
Shared test setup (pytest loads this file automatically).

- Every test writes its debug .wav files into a temporary folder that pytest
  deletes afterwards, so running the tests never leaves files in debug_audio/.
- The Deepgram key is removed during tests, so tests never call the real
  Deepgram (no cost, no internet needed), even if your .env has a key.
"""

import pytest


@pytest.fixture(autouse=True)
def test_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUG_AUDIO_DIR", str(tmp_path))
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)