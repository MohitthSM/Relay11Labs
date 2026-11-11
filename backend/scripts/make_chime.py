"""Write static/chime.wav: a two-note "ding-dong" with a soft decay. Stdlib only.

WAV rather than MP3 so stitching a chime in mock mode needs no ffmpeg. Run from backend/:
    uv run python scripts/make_chime.py
"""

import math
import struct
import wave
from pathlib import Path

RATE = 44100
NOTES = [(784.0, 0.45), (659.25, 0.75)]  # G5 then E5: (Hz, seconds)
AMPLITUDE = 0.35


def note(freq: float, seconds: float) -> list[int]:
    samples = []
    for i in range(int(RATE * seconds)):
        t = i / RATE
        attack = min(1.0, t / 0.01)  # 10 ms fade-in, no click
        decay = math.exp(-3.5 * t / seconds)
        value = AMPLITUDE * attack * decay * math.sin(2 * math.pi * freq * t)
        samples.append(int(value * 32767))
    return samples


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "static" / "chime.wav"
    samples = [s for freq, secs in NOTES for s in note(freq, secs)]
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    print(f"wrote {out} ({len(samples) / RATE:.2f} s)")


if __name__ == "__main__":
    main()
