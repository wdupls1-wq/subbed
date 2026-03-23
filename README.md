# SubStation

**macOS menu bar app** for local, offline SRT subtitle generation from video or audio files.  
Uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2 backend) — no cloud, no internet required after first model download.

---

## Prerequisites

- macOS 12 (Monterey) or later
- Python 3.10+ (install via `brew install python@3.11`)
- ffmpeg (install via `brew install ffmpeg`)
- Homebrew: https://brew.sh

---

## Setup (one time)

```bash
cd "/Users/wdp/Desktop/Antigravity Subtitle - Subbed"
bash setup.sh
```

This will:
1. Create a `.venv/` virtual environment
2. Install all Python dependencies (`rumps`, `faster-whisper`, `pyobjc`)
3. Verify ffmpeg is available
4. Create the output folder at `~/Desktop/SubStation Output/`

---

## Launch

**Option A — Double-click** `SubStation.app` in Finder.

**Option B — Terminal:**
```bash
cd "/Users/wdp/Desktop/Antigravity Subtitle - Subbed"
source .venv/bin/activate
python app.py
```

The app appears in the **menu bar** (top right) as **"SubS"**. There is no Dock icon by design.

---

## Usage

| Step | Action |
|---|---|
| 1 | Click **SubS** in the menu bar |
| 2 | Select **Model** → choose accuracy/speed trade-off |
| 3 | Click **Open File…** → select your video or audio |
| 4 | Watch the menu bar spinner + % progress |
| 5 | On completion, Finder opens showing the `.srt` file |

---

## Settings

### Model selection

| Model | VRAM/RAM | Speed (M1) | Use when… |
|---|---|---|---|
| `tiny` | ~75 MB | ~32× realtime | Quick check / draft |
| `base` | ~145 MB | ~16× realtime | Fast turnaround |
| `small` | ~465 MB | ~6× realtime | **Recommended default** |
| `medium` | ~1.5 GB | ~2× realtime | Difficult/accented audio |
| `large` | ~2.9 GB | ~1× realtime | Maximum accuracy |

Models are downloaded on first use and cached at `~/.cache/huggingface/hub/`.

### Timing Offset

Click **Timing Offset** in the menu to shift all subtitle timestamps.  
- `+1.5` = subtitles appear 1.5 seconds later  
- `-0.5` = subtitles appear 0.5 seconds earlier  

### Output Folder

Click **Output: …/folder** to choose a different destination directory.  
Default: `~/Desktop/SubStation Output/`

---

## Audio Pre-processing

Before transcription, the audio is automatically processed through:

| Filter | Purpose |
|---|---|
| `highpass=f=80` | Removes sub-80 Hz rumble (handling noise, bass) |
| `loudnorm` | EBU R128 loudness normalisation (wide dynamic range) |
| `anlmdn` | Non-local means noise reduction (hiss, ambient noise) |

> **Optional:** Vocal isolation via `demucs` is available but not enabled by default (adds ~5 min/30 min CPU time). See the commented-out section in `transcriber.py` to enable it.

---

## Anti-Hallucination Configuration

Whisper can produce hallucinated text during long silent/non-verbal passages. SubStation mitigates this with:

| Setting | Value | Effect |
|---|---|---|
| `no_speech_threshold` | 0.6 | Skip low-confidence silence segments |
| `log_prob_threshold` | −1.0 | Reject uncertain token sequences |
| `compression_ratio_threshold` | 2.4 | Discard repetitive artifact sequences |
| `condition_on_previous_text` | False | Prevents hallucination drift |
| `vad_filter` | True | Silero VAD: only transcribe speech regions |
| `vad min_silence_duration_ms` | 500 | Ignore gaps < 500 ms |

---

## Long File Handling

Files longer than **20 minutes** are automatically:
- Split into 90-second overlapping chunks (15 s overlap)
- Transcribed sequentially with per-chunk progress reporting
- De-duplicated at seam boundaries (overlap artifacts removed)

---

## Running Tests

```bash
cd "/Users/wdp/Desktop/Antigravity Subtitle - Subbed"
source .venv/bin/activate
python -m pytest tests/ -v
```

- `test_subtitle_processor.py` — pure unit tests, no model download, runs in < 2 s
- `test_srt_output.py` — integration test, downloads `tiny` model on first run (~75 MB)

---

## Project Structure

```
Antigravity Subtitle - Subbed/
├── app.py                    # rumps menu bar application
├── transcriber.py            # ffmpeg extraction + faster-whisper engine
├── subtitle_processor.py     # merge, wrap, offset, SRT writer
├── requirements.txt          # Python dependencies
├── setup.sh                  # one-time setup script
├── SubStation.app/           # double-clickable app bundle
│   └── Contents/
│       ├── Info.plist        # LSUIElement=true → no Dock icon
│       └── MacOS/
│           └── launcher      # shell script: activates venv, exec python
└── tests/
    ├── test_subtitle_processor.py
    └── test_srt_output.py
```

---

## Troubleshooting

**"ffmpeg not found"**  
→ `brew install ffmpeg` then relaunch.

**"Setup Required" alert on launch**  
→ Run `bash setup.sh` in Terminal first.

**Spinning endlessly on a very long file**  
→ Long files chunk automatically; allow extra time. Progress % is shown in menu bar.

**Empty SRT file**  
→ Check if the audio has actual speech. A very low-volume or music-only file may produce no output (this is intentional — anti-hallucination is aggressive).

**"Transcription failed" alert**  
→ The full error message is shown. Most common causes: unsupported file format, missing ffmpeg, or insufficient disk space in `/tmp`.

---

## License

MIT — use freely, commercially or otherwise.
