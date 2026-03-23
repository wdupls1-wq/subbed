"""
test_subtitle_processor.py — Unit tests for subtitle post-processing logic.
No model download required; runs in < 2 seconds.

Run with:
    cd "/Users/wdp/Desktop/Antigravity Subtitle - Subbed"
    source .venv/bin/activate
    python -m pytest tests/ -v
"""

import sys
import os
import unittest

# Allow importing from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from transcriber import Segment
from subtitle_processor import (
    merge_segments,
    wrap_text,
    segments_to_blocks,
    process,
    _fmt_ts,
    MAX_LINE_CHARS,
    MAX_LINES,
    MERGE_GAP_SEC,
)
import tempfile
from pathlib import Path


class TestTimestampFormatting(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(_fmt_ts(0.0), "00:00:00,000")

    def test_simple_seconds(self):
        self.assertEqual(_fmt_ts(5.5), "00:00:05,500")

    def test_over_one_minute(self):
        self.assertEqual(_fmt_ts(90.0), "00:01:30,000")

    def test_over_one_hour(self):
        self.assertEqual(_fmt_ts(3661.5), "01:01:01,500")

    def test_negative_clamped_to_zero(self):
        self.assertEqual(_fmt_ts(-3.0), "00:00:00,000")

    def test_millisecond_precision(self):
        self.assertEqual(_fmt_ts(1.123), "00:00:01,123")


class TestMergeSegments(unittest.TestCase):
    def _seg(self, start, end, text):
        return Segment(start=start, end=end, text=text)

    def test_close_segments_merged(self):
        segs = [
            self._seg(0.0, 1.0, "Hello"),
            self._seg(1.5, 2.5, "world"),   # gap = 0.5s < MERGE_GAP_SEC
        ]
        result = merge_segments(segs)
        self.assertEqual(len(result), 1)
        self.assertIn("Hello", result[0].text)
        self.assertIn("world", result[0].text)

    def test_far_segments_not_merged(self):
        segs = [
            self._seg(0.0, 1.0, "Hello"),
            self._seg(5.0, 6.0, "world"),   # gap = 4s > MERGE_GAP_SEC
        ]
        result = merge_segments(segs)
        self.assertEqual(len(result), 2)

    def test_merge_preserves_timing(self):
        segs = [
            self._seg(1.0, 2.0, "A"),
            self._seg(2.5, 3.5, "B"),
        ]
        result = merge_segments(segs)
        self.assertEqual(result[0].start, 1.0)
        self.assertEqual(result[0].end, 3.5)

    def test_long_combined_text_not_merged(self):
        # Two segments that are close but would exceed MERGE_MAX_CHARS
        long_text = "x" * 50
        segs = [
            self._seg(0.0, 1.0, long_text),
            self._seg(1.5, 2.5, long_text),
        ]
        result = merge_segments(segs)
        self.assertEqual(len(result), 2)

    def test_empty_list(self):
        self.assertEqual(merge_segments([]), [])

    def test_single_segment(self):
        segs = [self._seg(0.0, 1.0, "Hello")]
        result = merge_segments(segs)
        self.assertEqual(len(result), 1)


class TestLineWrapping(unittest.TestCase):
    def test_short_text_unchanged(self):
        lines = wrap_text("Hi there")
        self.assertEqual(lines, ["Hi there"])

    def test_long_text_wrapped(self):
        text = "This is a longer sentence that should wrap at the boundary"
        lines = wrap_text(text)
        for line in lines:
            self.assertLessEqual(len(line), MAX_LINE_CHARS)

    def test_word_boundary_respected(self):
        # Should not break in the middle of a word
        text = "The quick brown fox jumps over the lazy dog"
        lines = wrap_text(text)
        for line in lines:
            self.assertFalse(line.startswith(" "))
            self.assertFalse(line.endswith(" "))

    def test_exactly_42_chars(self):
        text = "a" * MAX_LINE_CHARS
        lines = wrap_text(text)
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(lines[0]), MAX_LINE_CHARS)


class TestSegmentsToBlocks(unittest.TestCase):
    def _seg(self, start, end, text):
        return Segment(start=start, end=end, text=text)

    def test_simple_block(self):
        segs = [self._seg(0.0, 2.0, "Hello world")]
        blocks = segments_to_blocks(segs)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].index, 1)

    def test_two_line_max_enforced(self):
        # Text that wraps to 3+ lines should be split into multiple blocks
        long_text = " ".join(["word"] * 25)  # ~100 chars, forces 3+ lines at 42
        segs = [self._seg(0.0, 6.0, long_text)]
        blocks = segments_to_blocks(segs)
        for block in blocks:
            self.assertLessEqual(len(block.lines), MAX_LINES)

    def test_positive_offset_applied(self):
        segs = [self._seg(1.0, 2.0, "Hello")]
        blocks = segments_to_blocks(segs, offset_sec=5.0)
        self.assertAlmostEqual(blocks[0].start, 6.0)
        self.assertAlmostEqual(blocks[0].end, 7.0)

    def test_negative_offset_applied(self):
        segs = [self._seg(10.0, 12.0, "Hello")]
        blocks = segments_to_blocks(segs, offset_sec=-3.0)
        self.assertAlmostEqual(blocks[0].start, 7.0)
        self.assertAlmostEqual(blocks[0].end, 9.0)

    def test_negative_offset_clamped_at_zero(self):
        # Offset that would make start negative → clamped to 0
        segs = [self._seg(1.0, 2.0, "Hello")]
        blocks = segments_to_blocks(segs, offset_sec=-5.0)
        self.assertGreaterEqual(blocks[0].start, 0.0)

    def test_sequential_indices(self):
        segs = [
            self._seg(0.0, 1.0, "First"),
            self._seg(3.0, 4.0, "Second"),
            self._seg(6.0, 7.0, "Third"),
        ]
        blocks = segments_to_blocks(segs)
        for i, block in enumerate(blocks, start=1):
            self.assertEqual(block.index, i)

    def test_empty_segments(self):
        self.assertEqual(segments_to_blocks([]), [])


class TestSRTOutput(unittest.TestCase):
    def test_srt_file_written(self):
        from subtitle_processor import process
        segs = [Segment(0.0, 1.5, "Hello world"), Segment(3.0, 5.0, "Goodbye")]
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test.srt"
            process(segs, out)
            self.assertTrue(out.exists())
            content = out.read_text()
            # SRT must start with block index 1
            self.assertTrue(content.startswith("1\n"))
            # Must contain --> arrow
            self.assertIn("-->", content)

    def test_srt_format_valid(self):
        import re
        from subtitle_processor import process
        segs = [Segment(61.5, 63.0, "Test line")]
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.srt"
            process(segs, out)
            content = out.read_text()
            # Validate timestamp format HH:MM:SS,mmm --> HH:MM:SS,mmm
            ts_pattern = r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}"
            self.assertTrue(re.search(ts_pattern, content))

    def test_empty_segments_writes_empty_srt(self):
        from subtitle_processor import process
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "empty.srt"
            process([], out)
            self.assertTrue(out.exists())
            self.assertEqual(out.read_text(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
