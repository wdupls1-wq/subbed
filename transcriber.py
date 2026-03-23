"""
transcriber.py — Core transcription engine for SubStation
Uses faster-whisper (CTranslate2 backend) for efficient local inference.

Design goals for adult film post-production audio:
  - Handles degraded audio: overlapping speech, music, breathy/low-volume dialogue
  - Aggressive anti-hallucination settings for long silent/non-verbal passages
  - Chunks files > 20 min to manage memory and enable partial progress reporting
  - Lightweight ffmpeg pre-processing (loudnorm + highpass) before transcription
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

# ── ffmpeg detection ─────────────────────────────────────────────────────────

_FFMPEG_SEARCH_PATHS = [
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
]


def find_ffmpeg() -> str:
    """Return path to ffmpeg binary or raise RuntimeError."""
    for p in _FFMPEG_SEARCH_PATHS:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    # Fall back to PATH
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise RuntimeError(
        "ffmpeg not found.\n\n"
        "Install it with:\n"
        "    brew install ffmpeg\n\n"
        "Then relaunch SubStation."
    )


# ── Segment dataclass ────────────────────────────────────────────────────────

@dataclass
class Segment:
    start: float   # seconds
    end: float     # seconds
    text: str


# ── Audio extraction ─────────────────────────────────────────────────────────

def extract_audio(
    input_path: str | Path,
    output_wav: str | Path,
    ffmpeg_bin: str,
) -> None:
    """
    Extract audio from video/audio file → 16 kHz mono WAV.
    Applies:
      - loudnorm     : EBU R128 normalisation (handles wide dynamic range)
      - highpass=f=80: removes sub-80 Hz rumble (mic handling noise, bass thump)
      - anlmdn       : non-local means denoising (reduces hiss / background noise)

    # Optional demucs vocal isolation (uncomment if you want stem separation):
    # from demucs.api import Separator
    # separator = Separator(model="htdemucs")
    # ... (adds ~5 min per 30 min on CPU; comment out demucs and use ffmpeg-only by default)
    """
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(input_path),
        "-vn",                         # drop video stream
        "-af", (
            "highpass=f=80,"           # remove sub-80Hz rumble
            "loudnorm=I=-16:TP=-1.5:LRA=11,"  # normalise loudness
            "anlmdn=s=7:p=0.002:b=0.002"      # non-local means denoising
        ),
        "-ar", "16000",                # 16 kHz sample rate (Whisper requirement)
        "-ac", "1",                    # mono
        "-c:a", "pcm_s16le",           # 16-bit PCM WAV
        str(output_wav),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed to extract audio.\n\n"
            f"Command: {' '.join(cmd)}\n\n"
            f"Error:\n{result.stderr[-1000:]}"
        )


def get_duration_seconds(wav_path: str | Path, ffmpeg_bin: str) -> float:
    """Return duration of a WAV file in seconds."""
    cmd = [
        ffmpeg_bin.replace("ffmpeg", "ffprobe"),
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(wav_path),
    ]
    # also try sibling ffprobe
    ffprobe = shutil.which("ffprobe") or ffmpeg_bin.replace("ffmpeg", "ffprobe")
    cmd[0] = ffprobe
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def extract_chunk(
    wav_path: str | Path,
    output_path: str | Path,
    start_sec: float,
    duration_sec: float,
    ffmpeg_bin: str,
) -> None:
    """Extract a time-bounded chunk from a WAV file."""
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(wav_path),
        "-ss", str(start_sec),
        "-t", str(duration_sec),
        "-c", "copy",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg chunk extraction failed:\n{result.stderr[-500:]}")


# ── Transcription ────────────────────────────────────────────────────────────

# Anti-hallucination / degraded-audio configuration for faster-whisper
# These settings are tuned for adult film post-production audio characteristics:
#   - Long non-verbal passages → vad_filter + no_speech_threshold silence them
#   - Repeated-text artifacts  → compression_ratio_threshold discards them
#   - Low-confidence tokens    → log_prob_threshold rejects them
#   - Snowballing hallucination on context → condition_on_previous_text=False
_TRANSCRIBE_KWARGS = dict(
    beam_size=5,
    best_of=5,
    no_speech_threshold=0.6,          # high: aggressively skip non-speech segments
    log_prob_threshold=-1.0,          # discard low-probability token sequences
    compression_ratio_threshold=2.4,  # discard repetitive/artifact sequences
    condition_on_previous_text=False, # prevents hallucination drift on long files
    word_timestamps=True,             # word-level timing for accurate line breaks
    vad_filter=True,                  # silero VAD: only transcribe speech regions
    vad_parameters=dict(
        min_silence_duration_ms=500,  # ignore silences < 500 ms (natural pauses OK)
        speech_pad_ms=200,            # pad each speech segment by 200 ms
    ),
)

CHUNK_DURATION_SEC = 90        # chunk size
CHUNK_OVERLAP_SEC  = 15        # overlap to avoid cutting words at boundaries
LONG_FILE_THRESHOLD_MIN = 20   # files longer than this are chunked


def transcribe_file(
    input_path: str | Path,
    model_size: str = "small",
    progress_queue: "queue.Queue | None" = None,
    cancel_event: "threading.Event | None" = None,
) -> list[Segment]:
    """
    Full transcription pipeline:
      1. Detect ffmpeg
      2. Extract + pre-process audio to 16 kHz mono WAV
      3. If file > 20 min: chunk into overlapping 90-s pieces
      4. Transcribe each chunk with faster-whisper
      5. Merge chunk results, de-duplicate seam overlaps
      6. Return list of Segment objects

    progress_queue: if provided, receives (percent: float, status: str) tuples
    cancel_event: if set, transcription is aborted cleanly
    """
    from faster_whisper import WhisperModel  # deferred import (slow first load)

    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    ffmpeg_bin = find_ffmpeg()

    def _post(pct: float, msg: str) -> None:
        if progress_queue:
            progress_queue.put((pct, msg))

    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    with tempfile.TemporaryDirectory(prefix="substation_") as tmpdir:
        tmp = Path(tmpdir)
        full_wav = tmp / "full.wav"

        # ── Step 1: extract audio ────────────────────────────────────────────
        _post(0.0, "Extracting audio…")
        extract_audio(input_path, full_wav, ffmpeg_bin)
        if _cancelled():
            return []

        duration = get_duration_seconds(full_wav, ffmpeg_bin)

        # ── Step 2: load model ───────────────────────────────────────────────
        _post(2.0, f"Loading model '{model_size}'…")
        model = WhisperModel(
            model_size,
            device="auto",              # GPU if available, otherwise CPU
            compute_type="auto",        # int8 on CPU, float16 on GPU
        )
        if _cancelled():
            return []

        # ── Step 3: chunk or direct ──────────────────────────────────────────
        long_file = duration > LONG_FILE_THRESHOLD_MIN * 60

        if not long_file:
            _post(5.0, "Transcribing…")
            segments = _transcribe_wav(model, full_wav, offset_sec=0.0)
            _post(100.0, "Done")
            return segments

        # Long-file chunking path
        chunk_starts = []
        t = 0.0
        while t < duration:
            chunk_starts.append(t)
            t += CHUNK_DURATION_SEC - CHUNK_OVERLAP_SEC

        all_segments: list[Segment] = []
        seen_end_times: set[float] = set()

        for i, start in enumerate(chunk_starts):
            if _cancelled():
                break
            chunk_wav = tmp / f"chunk_{i:04d}.wav"
            extract_chunk(full_wav, chunk_wav, start, CHUNK_DURATION_SEC, ffmpeg_bin)

            pct = 5.0 + 93.0 * (i / len(chunk_starts))
            _post(pct, f"Transcribing chunk {i+1}/{len(chunk_starts)}…")

            chunk_segments = _transcribe_wav(model, chunk_wav, offset_sec=start)

            for seg in chunk_segments:
                # De-duplicate: skip segments whose end time was already emitted
                # (they fell in the overlap zone of the previous chunk)
                key = round(seg.end, 1)
                if key not in seen_end_times:
                    all_segments.append(seg)
                    seen_end_times.add(key)

        _post(100.0, "Done")
        return all_segments


def _transcribe_wav(
    model,
    wav_path: Path,
    offset_sec: float = 0.0,
) -> list[Segment]:
    """Run faster-whisper on a single WAV file and return Segment list."""
    segments_gen, _info = model.transcribe(str(wav_path), **_TRANSCRIBE_KWARGS)
    results = []
    for seg in segments_gen:
        text = seg.text.strip()
        if not text:
            continue
        results.append(Segment(
            start=seg.start + offset_sec,
            end=seg.end + offset_sec,
            text=text,
        ))
    return results
