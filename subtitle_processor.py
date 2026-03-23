"""
subtitle_processor.py — Post-processing pipeline for raw Whisper segments.

Responsibilities:
  - Merge short adjacent segments to reduce subtitle fragmentation
  - Wrap long lines at 42 characters (word-boundary)
  - Split blocks that exceed 2 lines into separate SRT entries
  - Apply user-specified timing offset (seconds, positive or negative)
  - Write valid SRT output
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from transcriber import Segment

# ── Constants ────────────────────────────────────────────────────────────────

MAX_LINE_CHARS   = 42     # max characters per subtitle line
MAX_LINES        = 2      # max lines per subtitle block
MERGE_GAP_SEC    = 1.5    # merge adjacent segments closer than this (seconds)
MERGE_MAX_CHARS  = MAX_LINE_CHARS * MAX_LINES  # max chars of a merged block


# ── Merge ────────────────────────────────────────────────────────────────────

def merge_segments(segments: list[Segment]) -> list[Segment]:
    """
    Merge adjacent segments that are:
      • within MERGE_GAP_SEC of each other, AND
      • combined text fits within MERGE_MAX_CHARS
    """
    if not segments:
        return []

    merged: list[Segment] = [Segment(segments[0].start, segments[0].end, segments[0].text)]

    for seg in segments[1:]:
        prev = merged[-1]
        gap = seg.start - prev.end
        combined_text = prev.text.rstrip() + " " + seg.text.lstrip()
        if gap <= MERGE_GAP_SEC and len(combined_text) <= MERGE_MAX_CHARS:
            merged[-1] = Segment(prev.start, seg.end, combined_text)
        else:
            merged.append(Segment(seg.start, seg.end, seg.text))

    return merged


# ── Line wrapping ────────────────────────────────────────────────────────────

def wrap_text(text: str) -> list[str]:
    """
    Wrap text to MAX_LINE_CHARS, breaking only at word boundaries.
    Returns a list of line strings.
    """
    # textwrap handles word-boundary wrapping cleanly
    return textwrap.wrap(text, width=MAX_LINE_CHARS, break_long_words=False, break_on_hyphens=False)


# ── Block builder ────────────────────────────────────────────────────────────

@dataclass
class SRTBlock:
    index: int
    start: float
    end: float
    lines: list[str]

    def to_srt(self) -> str:
        ts_start = _fmt_ts(self.start)
        ts_end   = _fmt_ts(self.end)
        return f"{self.index}\n{ts_start} --> {ts_end}\n" + "\n".join(self.lines) + "\n"


def segments_to_blocks(segments: list[Segment], offset_sec: float = 0.0) -> list[SRTBlock]:
    """
    Convert Segment list → SRTBlock list.
    Applies offset, wraps lines, splits blocks that exceed MAX_LINES.
    """
    blocks: list[SRTBlock] = []
    counter = 1

    for seg in segments:
        start = max(0.0, seg.start + offset_sec)
        end   = max(start + 0.1, seg.end + offset_sec)  # end always > start

        lines = wrap_text(seg.text)

        if not lines:
            continue

        # If the segment fits in MAX_LINES: one block
        if len(lines) <= MAX_LINES:
            blocks.append(SRTBlock(counter, start, end, lines))
            counter += 1
        else:
            # Split into multiple blocks, distributing duration evenly
            chunks = [lines[i:i + MAX_LINES] for i in range(0, len(lines), MAX_LINES)]
            dur_each = (end - start) / len(chunks)
            for j, chunk in enumerate(chunks):
                b_start = start + j * dur_each
                b_end   = start + (j + 1) * dur_each
                blocks.append(SRTBlock(counter, b_start, b_end, chunk))
                counter += 1

    return blocks


# ── SRT writer ───────────────────────────────────────────────────────────────

def write_srt(blocks: list[SRTBlock], output_path: str | Path) -> None:
    """Write SRT blocks to file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for block in blocks:
            f.write(block.to_srt())
            f.write("\n")


# ── Timestamp formatting ──────────────────────────────────────────────────────

def _fmt_ts(seconds: float) -> str:
    """Format seconds → HH:MM:SS,mmm (SRT format)."""
    seconds = max(0.0, seconds)
    total_ms = int(round(seconds * 1000))
    ms  = total_ms % 1000
    s   = (total_ms // 1000) % 60
    m   = (total_ms // 60_000) % 60
    h   = total_ms // 3_600_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ── Full pipeline ─────────────────────────────────────────────────────────────

def process(
    segments: list[Segment],
    output_path: str | Path,
    offset_sec: float = 0.0,
) -> list[SRTBlock]:
    """
    Run full post-processing pipeline and write SRT file.
    Returns the list of SRTBlocks written.
    """
    merged = merge_segments(segments)
    blocks = segments_to_blocks(merged, offset_sec=offset_sec)
    write_srt(blocks, output_path)
    return blocks
