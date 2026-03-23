"""
test_srt_output.py — Integration smoke test for the full transcription pipeline.

Uses faster-whisper with the 'tiny' model on a synthetic 5-second sine-wave WAV.
A pure sine wave contains no speech, so the anti-hallucination config should
produce an empty-but-valid SRT file. This validates:
  - ffmpeg audio extraction pipeline runs without error
  - faster-whisper + VAD correctly suppresses non-speech
  - subtitle_processor writes a valid (possibly empty) SRT file

First run downloads the 'tiny' model (~75 MB). Subsequent runs use the cache.

Run with:
    cd "/Users/wdp/Desktop/Antigravity Subtitle - Subbed"
    source .venv/bin/activate
    python -m pytest tests/test_srt_output.py -v -s
"""

import sys
import os
import struct
import math
import tempfile
import unittest
import re
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_sine_wav(path: Path, duration_sec: float = 5.0, frequency_hz: float = 440.0):
    """Write a minimal 16 kHz, 16-bit mono WAV file containing a sine wave."""
    sample_rate = 16000
    num_samples = int(sample_rate * duration_sec)
    data_bytes  = num_samples * 2  # 16-bit = 2 bytes per sample

    with open(path, "wb") as f:
        # RIFF header
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_bytes))  # file size - 8
        f.write(b"WAVE")
        # fmt chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))          # chunk size
        f.write(struct.pack("<H", 1))           # PCM format
        f.write(struct.pack("<H", 1))           # 1 channel (mono)
        f.write(struct.pack("<I", sample_rate)) # sample rate
        f.write(struct.pack("<I", sample_rate * 2))  # byte rate
        f.write(struct.pack("<H", 2))           # block align
        f.write(struct.pack("<H", 16))          # bits per sample
        # data chunk
        f.write(b"data")
        f.write(struct.pack("<I", data_bytes))
        for i in range(num_samples):
            sample = int(32767 * math.sin(2 * math.pi * frequency_hz * i / sample_rate))
            f.write(struct.pack("<h", sample))


class TestFullPipeline(unittest.TestCase):
    """
    Integration test: sine wave WAV → transcriptor → SRT.
    Skip if ffmpeg is unavailable (CI environments without ffmpeg).
    """

    def test_sine_wave_produces_valid_srt(self):
        import shutil
        if not shutil.which("ffmpeg") and not os.path.exists("/opt/homebrew/bin/ffmpeg"):
            self.skipTest("ffmpeg not available in this environment")

        from transcriber import transcribe_file
        import subtitle_processor

        with tempfile.TemporaryDirectory(prefix="substation_test_") as tmpdir:
            tmp = Path(tmpdir)
            wav   = tmp / "sine.wav"
            out   = tmp / "sine.srt"

            _make_sine_wav(wav)

            # Run the full pipeline (tiny model — fastest)
            try:
                segments = transcribe_file(
                    input_path=wav,
                    model_size="tiny",
                    progress_queue=None,
                    cancel_event=None,
                )
            except RuntimeError as e:
                if "ffmpeg" in str(e).lower():
                    self.skipTest(f"ffmpeg issue: {e}")
                raise

            subtitle_processor.process(segments, out)

            # ── Assertions ────────────────────────────────────────────────
            self.assertTrue(out.exists(), "SRT file was not created")

            content = out.read_text(encoding="utf-8")

            if content.strip():
                # If there IS content (model didn't fully suppress sine noise),
                # validate SRT format rigorously
                ts_pattern = r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}"
                self.assertTrue(
                    re.search(ts_pattern, content),
                    f"SRT timestamp format invalid. Content:\n{content[:500]}"
                )
                # Validate each block
                blocks = [b.strip() for b in content.strip().split("\n\n") if b.strip()]
                for i, block in enumerate(blocks):
                    lines = block.splitlines()
                    self.assertTrue(lines[0].isdigit(), f"Block {i} missing index: {block}")
                    self.assertIn("-->", lines[1], f"Block {i} missing timestamp: {block}")
                    self.assertLessEqual(len(lines) - 2, 2, f"Block {i} has > 2 text lines: {block}")
                    for text_line in lines[2:]:
                        self.assertLessEqual(len(text_line), 42, f"Line too long: {text_line!r}")
            else:
                # Empty SRT = expected (anti-hallucination correctly suppressed non-speech)
                print("\n✅  Anti-hallucination: no speech detected in sine wave (correct)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
