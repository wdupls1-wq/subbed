"""
app.py — SubStation menu bar application (rumps-based)

A macOS menu bar app with no Dock icon (LSUIElement=true in Info.plist).
Uses NSOpenPanel for file selection and NSWorkspace to reveal output in Finder.
Transcription runs in a background thread; progress is polled via a rumps.Timer.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
from pathlib import Path

import rumps

# ── Constants ────────────────────────────────────────────────────────────────

APP_NAME    = "SubStation"
APP_VERSION = "1.0.0"

DEFAULT_OUTPUT_DIR = str(Path.home() / "Desktop" / "SubStation Output")

MODELS = [
    ("tiny",   "tiny  — fastest, lowest accuracy (~75 MB)"),
    ("base",   "base  — fast, decent accuracy (~145 MB)"),
    ("small",  "small — balanced  (recommended) (~465 MB)"),
    ("medium", "medium — accurate, slower (~1.5 GB)"),
    ("large",  "large  — most accurate, slow (~2.9 GB)"),
]

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


# ── App ──────────────────────────────────────────────────────────────────────

class SubStationApp(rumps.App):
    def __init__(self):
        super().__init__(
            APP_NAME,
            title="SubS",        # compact menu bar label
            quit_button=None,    # we add our own quit item
        )

        # ── State ────────────────────────────────────────────────────────────
        self._current_model: str   = "small"
        self._output_dir:    str   = DEFAULT_OUTPUT_DIR
        self._timing_offset: float = 0.0
        self._busy:          bool  = False
        self._progress_q:  queue.Queue = queue.Queue()
        self._cancel_evt:  threading.Event = threading.Event()
        self._spinner_idx: int     = 0
        self._last_status: str     = ""

        # ── Menu items ───────────────────────────────────────────────────────
        self._open_item   = rumps.MenuItem("Open File…",     callback=self._open_file)
        self._cancel_item = rumps.MenuItem("Cancel",         callback=self._cancel)
        self._cancel_item.set_callback(self._cancel)
        self._cancel_item.hidden = True

        # Model submenu
        self._model_menu  = rumps.MenuItem("Model")
        self._model_items: dict[str, rumps.MenuItem] = {}
        for key, label in MODELS:
            item = rumps.MenuItem(label, callback=self._select_model)
            item._model_key = key
            if key == self._current_model:
                item.state = 1
            self._model_menu.add(item)
            self._model_items[key] = item

        # Offset item (opens dialog)
        self._offset_item = rumps.MenuItem(
            self._offset_label(), callback=self._set_offset
        )

        # Output folder item
        self._outdir_item = rumps.MenuItem(
            self._outdir_label(), callback=self._set_output_dir
        )

        # Separator + Quit
        self._quit_item = rumps.MenuItem("Quit SubStation", callback=rumps.quit_application)

        self.menu = [
            self._open_item,
            self._cancel_item,
            rumps.separator,
            self._model_menu,
            self._offset_item,
            self._outdir_item,
            rumps.separator,
            self._quit_item,
        ]

        # Progress polling timer (200 ms interval)
        self._timer = rumps.Timer(self._poll_progress, 0.2)
        self._timer.start()

        # Ensure output dir exists
        Path(self._output_dir).mkdir(parents=True, exist_ok=True)

    # ── Labels ───────────────────────────────────────────────────────────────

    def _offset_label(self) -> str:
        sign = "+" if self._timing_offset >= 0 else ""
        return f"Timing Offset: {sign}{self._timing_offset:.1f}s"

    def _outdir_label(self) -> str:
        short = Path(self._output_dir).name
        return f"Output: …/{short}"

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _select_model(self, sender):
        if self._busy:
            return
        for key, item in self._model_items.items():
            item.state = 0
        sender.state = 1
        self._current_model = sender._model_key

    def _set_offset(self, _):
        if self._busy:
            return
        response = rumps.Window(
            title="Timing Offset",
            message=(
                "Enter offset in seconds.\n"
                "Positive = shift subtitles later\n"
                "Negative = shift subtitles earlier"
            ),
            default_text=str(self._timing_offset),
            ok="Apply",
            cancel="Cancel",
            dimensions=(260, 24),
        ).run()
        if response.clicked:
            try:
                self._timing_offset = float(response.text.strip())
                self._offset_item.title = self._offset_label()
            except ValueError:
                rumps.alert(
                    title="Invalid offset",
                    message="Please enter a number (e.g. 1.5 or -0.5).",
                )

    def _set_output_dir(self, _):
        if self._busy:
            return
        # NSOpenPanel for folder selection
        try:
            from AppKit import NSOpenPanel, NSApplication
            panel = NSOpenPanel.openPanel()
            panel.setCanChooseFiles_(False)
            panel.setCanChooseDirectories_(True)
            panel.setAllowsMultipleSelection_(False)
            panel.setTitle_("Choose Output Folder")
            if panel.runModal() == 1:  # NSModalResponseOK
                url = panel.URLs()[0]
                self._output_dir = url.path()
                self._outdir_item.title = self._outdir_label()
                Path(self._output_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            rumps.alert(title="Folder selection failed", message=str(e))

    def _open_file(self, _):
        if self._busy:
            return
        try:
            from AppKit import NSOpenPanel
            panel = NSOpenPanel.openPanel()
            panel.setCanChooseFiles_(True)
            panel.setCanChooseDirectories_(False)
            panel.setAllowsMultipleSelection_(False)
            panel.setTitle_("Select Video or Audio File")
            panel.setAllowedFileTypes_([
                # Video
                "mp4", "m4v", "mov", "mkv", "avi", "wmv", "flv", "webm", "ts",
                # Audio
                "mp3", "m4a", "aac", "wav", "flac", "ogg", "wma", "opus",
            ])
            if panel.runModal() == 1:
                url       = panel.URLs()[0]
                file_path = url.path()
                self._start_transcription(file_path)
        except Exception as e:
            rumps.alert(title="Could not open file panel", message=str(e))

    def _cancel(self, _):
        self._cancel_evt.set()

    # ── Transcription thread ─────────────────────────────────────────────────

    def _start_transcription(self, file_path: str):
        self._busy = True
        self._cancel_evt.clear()
        self._open_item.set_callback(None)  # disable while busy
        self._cancel_item.hidden = False
        self.title = "⠋ 0%"

        thread = threading.Thread(
            target=self._run_transcription,
            args=(file_path,),
            daemon=True,
        )
        thread.start()

    def _run_transcription(self, file_path: str):
        """Runs in background thread. Uses progress_q for IPC."""
        from transcriber import transcribe_file
        import subtitle_processor

        try:
            segments = transcribe_file(
                input_path=file_path,
                model_size=self._current_model,
                progress_queue=self._progress_q,
                cancel_event=self._cancel_evt,
            )

            if self._cancel_evt.is_set():
                self._progress_q.put(("cancelled", "Cancelled"))
                return

            # Determine output path
            stem      = Path(file_path).stem
            out_dir   = Path(self._output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path  = out_dir / f"{stem}.srt"

            # Handle filename collision
            counter = 1
            while out_path.exists():
                out_path = out_dir / f"{stem}_{counter}.srt"
                counter += 1

            subtitle_processor.process(
                segments=segments,
                output_path=out_path,
                offset_sec=self._timing_offset,
            )

            self._progress_q.put(("done", str(out_path)))

        except Exception as exc:
            self._progress_q.put(("error", str(exc)))

    # ── Progress polling ──────────────────────────────────────────────────────

    def _poll_progress(self, _):
        try:
            while True:
                item = self._progress_q.get_nowait()
                if isinstance(item, tuple) and len(item) == 2:
                    status, payload = item

                    if status == "done":
                        self._on_done(payload)
                        return
                    elif status == "error":
                        self._on_error(payload)
                        return
                    elif status == "cancelled":
                        self._on_cancelled()
                        return
                    else:
                        # (pct, msg) progress update
                        pct = status
                        msg = payload
                        self._spinner_idx = (self._spinner_idx + 1) % len(SPINNER_FRAMES)
                        spin = SPINNER_FRAMES[self._spinner_idx]
                        self.title = f"{spin} {int(pct)}%"
        except queue.Empty:
            if self._busy:
                # Animate spinner even without new messages
                self._spinner_idx = (self._spinner_idx + 1) % len(SPINNER_FRAMES)
                self.title = self.title  # no-op refresh trick not needed; spinner in _run

    def _on_done(self, srt_path: str):
        self._reset_ui()
        # Reveal in Finder
        try:
            from AppKit import NSWorkspace
            from Foundation import NSURL
            url = NSURL.fileURLWithPath_(srt_path)
            NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([url])
        except Exception:
            pass  # Finder reveal is best-effort
        rumps.notification(
            title=APP_NAME,
            subtitle="Subtitles ready",
            message=Path(srt_path).name,
        )

    def _on_error(self, message: str):
        self._reset_ui()
        rumps.alert(
            title="Transcription failed",
            message=message,
            ok="OK",
        )

    def _on_cancelled(self):
        self._reset_ui()
        rumps.notification(
            title=APP_NAME,
            subtitle="Cancelled",
            message="Transcription was cancelled.",
        )

    def _reset_ui(self):
        self._busy = False
        self.title = "SubS"
        self._open_item.set_callback(self._open_file)
        self._cancel_item.hidden = True


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # Hide Dock icon — this is also set in Info.plist (LSUIElement),
    # but we set it programmatically too for direct `python app.py` launches.
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )
    except Exception:
        pass

    SubStationApp().run()


if __name__ == "__main__":
    main()
