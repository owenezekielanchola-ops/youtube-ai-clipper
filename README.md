# ClipForge AI — YouTube Clipper & Manual Video Editor (Local 8GB RAM Edition)

A lightweight, full-stack local web application for turning YouTube videos into viral Clips, Shorts, and edited videos using a **Shared Editing Engine**: both the AI assistant and the manual editor UI operate on the exact same non-destructive Project State JSON (`Edit Decision List`) with full `Ctrl+Z` / `Ctrl+Y` Undo/Redo support.

## Architecture Highlights (8GB RAM Optimized)

- **Shared 13-Tool Editing Engine:** Every AI edit (`isolate_highlight_clips`, `trim_clip`, `split_clip`, `delete_clips`, `merge_clips`, `reorder_clips`, `remove_time_ranges`, `generate_auto_captions`, `add_caption`, `edit_caption`, `adjust_caption_timing`, `change_caption_style`, `change_video_format`) runs through the exact same Zustand timeline engine as manual UI interactions, with atomic `Ctrl+Z` / `Ctrl+Y` Undo/Redo.
- **Zero Heavy Infrastructure:** No Docker, Redis, Celery, PostgreSQL, or cloud storage. Projects save locally as `.json` files in `workspace_data/projects/`.
- **Capped `<= 1080p` YouTube Ingest:** Uses `yt-dlp[default]` with automatic Node.js / Deno JS-runtime discovery (`js_runtimes`) to solve YouTube challenges cleanly across Windows, macOS, and Linux.
- **Compressed Audio Transcription & Streaming Peaks:** FFmpeg extracts mono `16kHz 48kbps` MP3 (`~15MB` for 45 min) for Groq `whisper-large-v3-turbo` word-level transcription, and streams `200Hz` PCM samples in Python (~1 MB RAM) instead of decoding full audio buffers in the browser.
- **3-Signal Viral Finder Pipeline:**
  1. **YouTube "Most Replayed" Heatmap** peaks via `yt-dlp`
  2. **Groq Whisper** word/sentence boundary timestamps
  3. **Gemini 2.5 Flash** multimodal video understanding via direct YouTube URL (`Part.from_uri`)
- **Manual Caption Override Protection:** Editing or timing any caption locks `isManuallyEdited: true` so neither AI edits nor auto-caption regeneration overwrite your manual work unless explicitly asked.
- **Sequential Low-RAM FFmpeg Export:** Cuts clips sequentially, joins via the FFmpeg `concat` demuxer, center-crops (`16:9`, `9:16`, or `1:1`), and burns styled `.ass` subtitles (Karaoke, Bold Social, Yellow Highlight, Minimal) using `-threads 2 -preset veryfast` and cross-platform escaped paths. Also supports `.srt` / `.vtt` subtitle downloads.

---

## Prerequisites

### 1. Python & Node.js
- **Python 3.10+** (with `venv` and `pip`)
- **Node.js 22+ LTS** (runs the Vite/React frontend **and** serves as the JavaScript runtime for `yt-dlp[default]`; Deno 2.3+ is also auto-detected if installed)

### 2. FFmpeg (Required for Demo, Ingest & Export)
Install FFmpeg and ensure `ffmpeg` and `ffprobe` are available on your system `PATH`:

- **Windows (PowerShell):**
  ```powershell
  winget install Gyan.FFmpeg
  ```
  *(Or using Chocolatey: `choco install ffmpeg`, or Scoop: `scoop install ffmpeg`. Restart your terminal afterwards so `PATH` refreshes.)*

- **macOS (Homebrew):**
  ```bash
  brew install ffmpeg
  ```

- **Linux:**
  - Ubuntu / Debian: `sudo apt update && sudo apt install -y ffmpeg`
  - Fedora: `sudo dnf install -y ffmpeg`
  - Arch: `sudo pacman -S ffmpeg`

Verify installation on any OS:
```bash
ffmpeg -version
```

### 3. API Keys (`backend/.env`)
Create `backend/.env` from `backend/.env.example` (the startup scripts create this file automatically on first run):

```env
GROQ_API_KEY=your_groq_api_key_here
GEMINI_API_KEY=your_gemini_api_key_here
```

- **`GROQ_API_KEY`** ([Groq Console](https://console.groq.com/)): Used for `whisper-large-v3-turbo` audio/voice transcription and `llama-3.3-70b-versatile` fast timeline tool-calling.
- **`GEMINI_API_KEY`** ([Google AI Studio](https://aistudio.google.com/)): Used for `gemini-2.5-flash` multimodal viral highlight detection.

> **Note:** Clicking **Demo** (or pasting `demo://sample`) works completely offline without API keys using a locally synthesized 45s test video and deterministic fallback rules. Once keys are saved in `backend/.env`, restart the backend and open `http://localhost:8000/api/health` to verify `"groq_configured": true` and `"gemini_configured": true`.

---

## Quick Start (One Command)

### Windows
Double-click `start.bat` or run from a terminal:
```bat
start.bat
```

### macOS / Linux
```bash
chmod +x start.sh
./start.sh
```

Both scripts automatically check prerequisites, create `backend/.env` (if missing), set up `backend/.venv`, install backend & frontend dependencies on first launch, and start:
- **Frontend Studio:** `http://localhost:5173`
- **Backend API & Health Check:** `http://localhost:8000/api/health`

---

## Manual Setup (Optional)

### Backend (`localhost:8000`)
```bash
cd backend
cp .env.example .env
# Add GROQ_API_KEY and GEMINI_API_KEY inside backend/.env
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend (`localhost:5173`)
```bash
cd frontend
npm install
npm run dev
```

---

## Where Your Edited Videos & Files Go

- **Instant Live Preview:** Every AI or manual timeline edit updates the Center Video Player immediately (`16:9`, `9:16`, or `1:1`) with real-time caption overlays.
- **Final Exported MP4s:** Click **Export → Render MP4** in the top navbar. Download links appear directly in the modal, and rendered files are saved locally in:
  - `workspace_data/exports/` (rendered `.mp4` exports)
  - `workspace_data/downloads/` (source MP4 + compressed MP3)
  - `workspace_data/transcripts/` (cached Whisper JSON)
  - `workspace_data/projects/` (saved project `.json` EDLs)

*(All `.env` files and `workspace_data/` media outputs are excluded from Git via `.gitignore`.)*
