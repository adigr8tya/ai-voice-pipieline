"""
convert_to_8k.py
----------------
Converts any PCM .wav file into the format Exotel sends us:
16-bit, mono, 8000 Hz. Use it to prepare test audio for the fake client.

Uses only Python's standard library (no ffmpeg needed), so it works on
Windows as is. Input must be a .wav file; if your recording is .mp3 or
.m4a, export it as .wav first (e.g. with Audacity or any online converter).

Usage:
    python scripts/convert_to_8k.py my_recording.wav samples/hindi_8k.wav
"""

import argparse
import array
import sys
import wave

TARGET_RATE = 8000


def read_wav_as_mono_ints(path):
    """
    Read a .wav file and return (samples, sample_rate), where samples is a
    list of 16-bit integers for a single (mono) channel.
    """
    with wave.open(path, "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()        # bytes per sample: 1, 2, 3 or 4
        rate = wav.getframerate()
        raw = wav.readframes(wav.getnframes())

    # Step 1: bytes -> list of integers, scaled to the 16-bit range
    if width == 1:
        # 8-bit .wav is unsigned (0..255, silence = 128)
        values = [(b - 128) << 8 for b in raw]
    elif width == 2:
        arr = array.array("h")            # "h" = signed 16-bit
        arr.frombytes(raw)
        if sys.byteorder == "big":        # .wav is always little-endian
            arr.byteswap()
        values = arr.tolist()
    elif width in (3, 4):
        # 24/32-bit: keep the top 2 bytes of each little-endian sample
        values = [
            int.from_bytes(raw[i + width - 2:i + width], "little", signed=True)
            for i in range(0, len(raw), width)
        ]
    else:
        raise ValueError(f"Unsupported sample width: {width} bytes")

    # Step 2: stereo (or more) -> mono, by averaging the channels
    if channels > 1:
        values = [
            sum(values[i:i + channels]) // channels
            for i in range(0, len(values), channels)
        ]
    return values, rate


def resample(values, from_rate, to_rate):
    """
    Change the sample rate with simple linear interpolation.
    Good enough for test audio (phone-call quality), not for music.
    """
    if from_rate == to_rate:
        return values
    out_len = int(len(values) * to_rate / from_rate)
    step = from_rate / to_rate
    out = []
    for n in range(out_len):
        pos = n * step
        i = int(pos)
        frac = pos - i
        a = values[i]
        b = values[i + 1] if i + 1 < len(values) else a
        out.append(int(a + (b - a) * frac))
    return out


def main():
    parser = argparse.ArgumentParser(description="Convert a .wav to 16-bit mono 8 kHz")
    parser.add_argument("input", help="source .wav file")
    parser.add_argument("output", help="where to write the converted .wav")
    args = parser.parse_args()

    values, rate = read_wav_as_mono_ints(args.input)
    values = resample(values, rate, TARGET_RATE)

    # Clamp to the valid 16-bit range, then write the new .wav
    pcm = array.array("h", (max(-32768, min(32767, v)) for v in values))
    if sys.byteorder == "big":
        pcm.byteswap()
    with wave.open(args.output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(TARGET_RATE)
        wav.writeframes(pcm.tobytes())

    print(f"Wrote {args.output}: {len(values) / TARGET_RATE:.1f}s, 16-bit mono {TARGET_RATE} Hz (from {rate} Hz)")


if __name__ == "__main__":
    main()
