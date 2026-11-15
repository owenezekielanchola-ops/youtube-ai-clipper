import asyncio
import json
import math
import os
import shutil
import struct
import subprocess
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import uuid
from pathlib import Path

import yt_dlp

from schemas import ClipItem


def ensure_ffmpeg_available() -> str:
    """
    Resolves a working FFmpeg binary: checks system PATH first, then falls back
    to the bundled imageio-ffmpeg binary and creates a standard workspace_data/.bin/ffmpeg(.exe)
    shim so both subprocess callers and yt-dlp discover it reliably.
    """
    exe = shutil.which("ffmpeg")
    if exe:
        return str(Path(exe))

    target_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    bin_dir_path = Path(__file__).resolve().parents[2] / "workspace_data" / ".bin"
    shim_path = bin_dir_path / target_name
    if shim_path.is_file():
        bin_dir_str = str(bin_dir_path)
        cur_path = os.environ.get("PATH", "")
        if bin_dir_str not in cur_path.split(os.pathsep):
            os.environ["PATH"] = (
                f"{bin_dir_str}{os.pathsep}{cur_path}" if cur_path else bin_dir_str
            )
        return str(shim_path)

    bundled_exe: Optional[Path] = None
    try:
        import imageio_ffmpeg  # type: ignore

        candidate = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if candidate.is_file():
            bundled_exe = candidate
    except Exception:
        bundled_exe = None

    if bundled_exe is None:
        raise RuntimeError(
            "FFmpeg was not found on PATH. Please install FFmpeg and ensure 'ffmpeg' is accessible."
        )

    if bundled_exe.name.lower() == target_name:
        bin_dir = str(bundled_exe.parent)
        cur_path = os.environ.get("PATH", "")
        if bin_dir not in cur_path.split(os.pathsep):
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{cur_path}" if cur_path else bin_dir
        return str(bundled_exe)

    try:
        bin_dir_path.mkdir(parents=True, exist_ok=True)
        if not shim_path.exists() or shim_path.stat().st_size != bundled_exe.stat().st_size:
            shutil.copy2(bundled_exe, shim_path)
            if os.name != "nt":
                os.chmod(shim_path, 0o755)
    except OSError:
        if not shim_path.exists():
            return str(bundled_exe)

    bin_dir_str = str(bin_dir_path)
    cur_path = os.environ.get("PATH", "")
    if bin_dir_str not in cur_path.split(os.pathsep):
        os.environ["PATH"] = (
            f"{bin_dir_str}{os.pathsep}{cur_path}" if cur_path else bin_dir_str
        )
    return str(shim_path)


def _find_ffmpeg() -> str:
    return ensure_ffmpeg_available()


def _find_runtime_executable(binary_name: str, extra_dirs: List[Path]) -> Optional[str]:
    found = shutil.which(binary_name)
    if found:
        return str(Path(found).resolve())

    suffixes = (".exe", ".cmd", "") if os.name == "nt" else ("",)
    for directory in extra_dirs:
        if not directory.is_dir():
            continue
        for suffix in suffixes:
            candidate = directory / f"{binary_name}{suffix}"
            if candidate.is_file():
                resolved = candidate.resolve()
                parent = str(resolved.parent)
                cur_path = os.environ.get("PATH", "")
                if parent not in cur_path.split(os.pathsep):
                    os.environ["PATH"] = (
                        f"{cur_path}{os.pathsep}{parent}" if cur_path else parent
                    )
                return str(resolved)
    return None


def _discover_js_runtimes() -> Dict[str, Dict[str, Any]]:
    """
    Discovers Node.js / Deno executables on PATH or in common user directories
    (fnm, nvm, Homebrew, ~/.deno/bin) so yt-dlp[default] can solve YouTube JS challenges.
    """
    home = Path.home()
    deno_dirs = [
        home / ".deno" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]
    node_dirs = [
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path(r"C:\Program Files\nodejs"),
    ]

    if os.name == "nt":
        local_app_data = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        fnm_base = local_app_data / "fnm_multishells"
        if fnm_base.is_dir():
            node_dirs.extend(sorted(fnm_base.iterdir(), reverse=True))
        nvm_win = Path(os.environ.get("NVM_HOME", home / "AppData" / "Roaming" / "nvm"))
        if nvm_win.is_dir():
            node_dirs.extend(sorted(nvm_win.iterdir(), reverse=True))
    else:
        fnm_base = home / ".local" / "share" / "fnm" / "node-versions"
        if fnm_base.is_dir():
            for child in sorted(fnm_base.iterdir(), reverse=True):
                node_dirs.append(child / "installation" / "bin")
        nvm_dir = Path(os.environ.get("NVM_DIR", home / ".nvm")) / "versions" / "node"
        if nvm_dir.is_dir():
            for child in sorted(nvm_dir.iterdir(), reverse=True):
                node_dirs.append(child / "bin")

    runtimes: Dict[str, Dict[str, Any]] = {}
    deno_path = _find_runtime_executable("deno", deno_dirs)
    if deno_path:
        runtimes["deno"] = {"path": deno_path}
    node_path = _find_runtime_executable("node", node_dirs)
    if node_path:
        runtimes["node"] = {"path": node_path}
    return runtimes


def _resolve_ytdlp_cookies_file() -> Optional[Path]:
    env_path = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        if p.is_file() and p.stat().st_size > 0:
            return p.resolve()

    backend_dir = Path(__file__).resolve().parents[1]
    repo_dir = Path(__file__).resolve().parents[2]
    for candidate in (backend_dir / "cookies.txt", repo_dir / "cookies.txt"):
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate.resolve()
    return None


def _apply_ytdlp_common_opts(opts: Dict[str, Any]) -> Dict[str, Any]:
    """Attaches discovered JS runtimes and optional YouTube cookies to yt-dlp options."""
    js_runtimes = _discover_js_runtimes()
    if js_runtimes:
        opts["js_runtimes"] = js_runtimes

    cookies_file = _resolve_ytdlp_cookies_file()
    if cookies_file is not None:
        opts["cookiefile"] = str(cookies_file)
    else:
        browser = os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "").strip()
        if browser:
            opts["cookiesfrombrowser"] = (browser.lower(),)
    return opts


def _compute_peaks_from_media(media_path: Path, num_buckets: int = 160) -> List[float]:
    """
    Streams ultra-low-sample-rate (200Hz) PCM via FFmpeg stdout to compute normalized volume peaks
    in Python so the browser never decodes audio buffers into RAM.
    """
    ffmpeg_bin = _find_ffmpeg()
    media_arg = media_path.resolve().as_posix()
    pcm_cmd = [
        ffmpeg_bin,
        "-nostdin",
        "-i",
        media_arg,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "200",
        "-f",
        "s16le",
        "pipe:1",
    ]
    proc = subprocess.Popen(
        pcm_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    samples: List[int] = []
    chunk_size = 4096
    assert proc.stdout is not None
    while True:
        data = proc.stdout.read(chunk_size)
        if not data:
            break
        even_len = len(data) - (len(data) % 2)
        if even_len > 0:
            unpacked = struct.unpack(f"<{even_len // 2}h", data[:even_len])
            samples.extend(abs(s) for s in unpacked)
    proc.wait()

    if not samples:
        return [0.2] * num_buckets

    bucket_size = max(1, len(samples) / num_buckets)
    raw_peaks: List[float] = []
    for i in range(num_buckets):
        start_idx = int(i * bucket_size)
        end_idx = min(len(samples), int((i + 1) * bucket_size))
        if start_idx >= end_idx:
            raw_peaks.append(0.0)
        else:
            segment = samples[start_idx:end_idx]
            rms = math.sqrt(sum(v * v for v in segment) / len(segment))
            raw_peaks.append(rms)

    max_val = max(raw_peaks) or 1.0
    return [round(max(0.05, min(1.0, p / max_val)), 3) for p in raw_peaks]


def _extract_audio_and_peaks(video_path: Path, audio_path: Path, num_buckets: int = 160) -> List[float]:
    """
    1. Extracts lightweight mono 16kHz 48kbps MP3 for Groq Whisper (keeps 45min < 15MB, ~0 RAM).
    2. Streams ultra-low-sample-rate (200Hz) PCM via FFmpeg stdout to compute normalized volume peaks
       in Python so the browser never decodes audio buffers into RAM.
    """
    ffmpeg_bin = _find_ffmpeg()
    video_arg = video_path.resolve().as_posix()
    audio_arg = audio_path.resolve().as_posix()

    # Step 1: Extract compressed 48kbps mono 16kHz MP3 (fallback to silent track if video has no audio)
    mp3_cmd = [
        ffmpeg_bin,
        "-nostdin",
        "-y",
        "-i",
        video_arg,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "48k",
        audio_arg,
    ]
    mp3_res = subprocess.run(
        mp3_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    if mp3_res.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
        silent_cmd = [
            ffmpeg_bin,
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "1.0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "48k",
            audio_arg,
        ]
        subprocess.run(
            silent_cmd,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return [0.1] * num_buckets

    # Step 2: Stream 200Hz signed 16-bit PCM to calculate lightweight peak array
    return _compute_peaks_from_media(video_path, num_buckets=num_buckets)


def _create_demo_sample_video(video_path: Path, duration: float = 45.0) -> Dict[str, Any]:
    """
    Generates a clean local 720p MP4 with synthesized speech-like tone cadence and visual timecode
    when the user tests with 'demo://sample' (zero external network required).
    """
    ffmpeg_bin = _find_ffmpeg()
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=1280x720:rate=30:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=44100:duration={duration}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-threads",
        "2",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        video_path.resolve().as_posix(),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return {
        "title": "Sample Studio Clip — AI Clipper Demo",
        "thumbnail": "",
        "duration": duration,
        "heatmap": [
            {"start_time": 0.0, "end_time": 9.0, "value": 0.35},
            {"start_time": 9.0, "end_time": 18.0, "value": 0.62},
            {"start_time": 18.0, "end_time": 32.0, "value": 0.96},
            {"start_time": 32.0, "end_time": 45.0, "value": 0.48},
        ],
    }


def _probe_media_duration(media_path: Path, ffmpeg_bin: Optional[str] = None) -> Optional[float]:
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin:
        res = subprocess.run(
            [
                str(Path(ffprobe_bin)),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                media_path.resolve().as_posix(),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        try:
            val = float(res.stdout.strip())
            if val > 0:
                return val
        except ValueError:
            pass

    if ffmpeg_bin:
        res = subprocess.run(
            [ffmpeg_bin, "-nostdin", "-i", media_path.resolve().as_posix()],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        import re

        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr or "")
        if m:
            hrs, mins, secs = int(m.group(1)), int(m.group(2)), float(m.group(3))
            val = hrs * 3600.0 + mins * 60.0 + secs
            if val > 0:
                return val
    return None


def _normalize_heatmap(
    raw_heatmap: Any, audio_peaks: List[float], duration: float
) -> List[Dict[str, float]]:
    heatmap: List[Dict[str, float]] = []
    if isinstance(raw_heatmap, list) and len(raw_heatmap) > 0:
        for pt in raw_heatmap:
            if isinstance(pt, dict) and "start_time" in pt and "end_time" in pt:
                heatmap.append(
                    {
                        "start_time": float(pt["start_time"]),
                        "end_time": float(pt["end_time"]),
                        "value": float(pt.get("value", 0.0)),
                    }
                )
    elif audio_peaks and duration > 0:
        step = duration / len(audio_peaks)
        for idx, val in enumerate(audio_peaks):
            heatmap.append(
                {
                    "start_time": round(idx * step, 2),
                    "end_time": round((idx + 1) * step, 2),
                    "value": float(val),
                }
            )
    return heatmap


def _ensure_mp4_file(source_file: Path, target_mp4: Path, ffmpeg_bin: str) -> None:
    if source_file.suffix.lower() == ".mp4":
        if source_file.resolve() != target_mp4.resolve():
            source_file.replace(target_mp4)
        return
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            source_file.resolve().as_posix(),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            target_mp4.resolve().as_posix(),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    source_file.unlink(missing_ok=True)


def _download_youtube_sync(youtube_url: str, project_id: str, downloads_dir: Path) -> Dict[str, Any]:
    downloads_dir = downloads_dir.resolve()
    downloads_dir.mkdir(parents=True, exist_ok=True)
    video_path = downloads_dir / f"{project_id}.mp4"
    audio_path = downloads_dir / f"{project_id}.mp3"

    if youtube_url.strip().lower() in {"demo://sample", "sample", "demo"}:
        meta = _create_demo_sample_video(video_path)
        audio_peaks = _extract_audio_and_peaks(video_path, audio_path)
        return {
            "project_id": project_id,
            "video_path": video_path,
            "audio_path": audio_path,
            "title": meta["title"],
            "thumbnail": meta["thumbnail"],
            "duration": float(meta["duration"]),
            "heatmap": meta["heatmap"],
            "audio_peaks": audio_peaks,
            "is_demo": True,
        }

    # Strictly cap YouTube resolution at <= 1080p MP4 for 8GB RAM constraint
    ffmpeg_bin = _find_ffmpeg()
    output_template = (downloads_dir / f"{project_id}.%(ext)s").as_posix()
    ydl_opts: Dict[str, Any] = _apply_ytdlp_common_opts({
        "format": (
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
            "best[height<=1080][ext=mp4]/best[height<=1080]"
        ),
        "merge_output_format": "mp4",
        "ffmpeg_location": str(Path(ffmpeg_bin).parent),
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    })

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        title = info.get("title") or "YouTube Video"
        thumbnail = info.get("thumbnail") or ""
        duration = float(info.get("duration") or 0.0)
        raw_heatmap = info.get("heatmap") or []

    # Locate downloaded mp4 if yt-dlp produced a slightly different extension before merge
    if not video_path.exists():
        candidates = sorted(downloads_dir.glob(f"{project_id}.*"))
        mp4_candidates = [c for c in candidates if c.suffix.lower() in {".mp4", ".mkv", ".webm"}]
        if not mp4_candidates:
            raise FileNotFoundError(f"Downloaded video file not found for {project_id}")
        _ensure_mp4_file(mp4_candidates[0], video_path, ffmpeg_bin)

    if duration <= 0:
        duration = _probe_media_duration(video_path, ffmpeg_bin) or 60.0

    audio_peaks = _extract_audio_and_peaks(video_path, audio_path)
    heatmap = _normalize_heatmap(raw_heatmap, audio_peaks, duration)

    return {
        "project_id": project_id,
        "video_path": video_path,
        "audio_path": audio_path,
        "title": title,
        "thumbnail": thumbnail,
        "duration": duration,
        "heatmap": heatmap,
        "audio_peaks": audio_peaks,
        "is_demo": False,
    }


def _synthesize_words_for_segment(seg_start: float, seg_end: float, text: str) -> List[Dict[str, Any]]:
    tokens = text.split()
    if not tokens:
        return []
    span = max(0.2, seg_end - seg_start)
    per_word = span / len(tokens)
    out: List[Dict[str, Any]] = []
    for idx, tok in enumerate(tokens):
        ws = round(seg_start + idx * per_word, 3)
        we = round(min(seg_end, ws + per_word * 0.92), 3)
        out.append({"word": tok, "start": ws, "end": max(ws + 0.02, we)})
    return out


def _extract_youtube_json3_transcript(info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extracts timestamped captions directly from YouTube's subtitles / automatic_captions
    (json3 format) when available, avoiding large audio downloads/uploads on long videos.
    """
    preferred_langs = ("en", "en-US", "en-GB", "en-orig")
    chosen_url: Optional[str] = None

    for source_key in ("subtitles", "automatic_captions"):
        tracks = info.get(source_key)
        if not isinstance(tracks, dict) or not tracks:
            continue
        candidate_langs = [lang for lang in preferred_langs if lang in tracks]
        for lang in tracks:
            if lang not in candidate_langs:
                candidate_langs.append(lang)
        for lang in candidate_langs:
            formats = tracks.get(lang) or []
            if not isinstance(formats, list):
                continue
            for fmt in formats:
                if isinstance(fmt, dict) and fmt.get("ext") == "json3" and fmt.get("url"):
                    chosen_url = str(fmt["url"])
                    break
            if chosen_url:
                break
        if chosen_url:
            break

    if not chosen_url:
        return None

    try:
        req = urllib.request.Request(
            chosen_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None

    events = payload.get("events") or []
    words: List[Dict[str, Any]] = []
    segments: List[Dict[str, Any]] = []

    for ev in events:
        if not isinstance(ev, dict):
            continue
        segs = ev.get("segs") or []
        if not segs:
            continue
        ev_start_ms = float(ev.get("tStartMs", 0.0))
        ev_dur_ms = max(200.0, float(ev.get("dDurationMs", 2000.0)))
        ev_end_ms = ev_start_ms + ev_dur_ms
        ev_start = round(ev_start_ms / 1000.0, 3)
        ev_end = round(ev_end_ms / 1000.0, 3)

        seg_text_parts: List[str] = []
        ev_words: List[Dict[str, Any]] = []
        has_word_offsets = False

        for idx, sg in enumerate(segs):
            if not isinstance(sg, dict):
                continue
            raw_utf8 = str(sg.get("utf8", ""))
            clean_txt = " ".join(raw_utf8.replace("\n", " ").split())
            if not clean_txt:
                continue
            seg_text_parts.append(clean_txt)
            if "tOffsetMs" in sg:
                has_word_offsets = True
                w_start_ms = ev_start_ms + float(sg.get("tOffsetMs", 0.0))
                next_offset_ms: Optional[float] = None
                for nxt in segs[idx + 1 :]:
                    if isinstance(nxt, dict) and "tOffsetMs" in nxt:
                        next_offset_ms = ev_start_ms + float(nxt["tOffsetMs"])
                        break
                w_end_ms = next_offset_ms if next_offset_ms and next_offset_ms > w_start_ms else min(ev_end_ms, w_start_ms + 450.0)
                tokens = clean_txt.split()
                span_s = max(0.05, (w_end_ms - w_start_ms) / 1000.0)
                per_tok = span_s / max(1, len(tokens))
                for t_idx, tok in enumerate(tokens):
                    ts = round(w_start_ms / 1000.0 + t_idx * per_tok, 3)
                    te = round(max(ts + 0.02, ts + per_tok * 0.92), 3)
                    ev_words.append({"word": tok, "start": ts, "end": te})

        full_seg_text = " ".join(seg_text_parts).strip()
        if not full_seg_text:
            continue

        if not ev_words or not has_word_offsets:
            ev_words = _synthesize_words_for_segment(ev_start, ev_end, full_seg_text)

        segments.append({"start": ev_start, "end": max(ev_start + 0.2, ev_end), "text": full_seg_text})
        words.extend(ev_words)

    if not segments and not words:
        return None

    return {
        "text": " ".join(s["text"] for s in segments),
        "words": words,
        "segments": segments,
        "source": "youtube_captions",
        "model_used": "youtube-json3",
    }


def extract_youtube_audio_and_meta(
    youtube_url: str,
    project_id: str,
    downloads_dir: Path,
    initial_prompt: str = "",
) -> Dict[str, Any]:
    """
    Low-storage Stage 1: fetches metadata + heatmap + YouTube captions (if available,
    using 0 MB audio transfer), or falls back to a capped compressed 24kbps mono MP3 window.
    """
    downloads_dir = downloads_dir.resolve()
    downloads_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = _find_ffmpeg()

    meta_opts: Dict[str, Any] = _apply_ytdlp_common_opts({
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    })

    with yt_dlp.YoutubeDL(meta_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)

    title = info.get("title") or "YouTube Video"
    thumbnail = info.get("thumbnail") or ""
    duration = float(info.get("duration") or 0.0)
    raw_heatmap = info.get("heatmap") or []

    yt_transcript = _extract_youtube_json3_transcript(info)
    if yt_transcript is not None:
        effective_duration = duration
        if effective_duration <= 0 and yt_transcript.get("segments"):
            effective_duration = float(yt_transcript["segments"][-1]["end"])
        fallback_peaks = [0.4, 0.55, 0.85, 0.65, 0.9, 0.5, 0.7, 0.45]
        heatmap = _normalize_heatmap(raw_heatmap, fallback_peaks, effective_duration or 60.0)
        return {
            "project_id": project_id,
            "audio_path": None,
            "prefetched_transcript": yt_transcript,
            "title": title,
            "thumbnail": thumbnail,
            "duration": effective_duration or 60.0,
            "full_duration": effective_duration or 60.0,
            "heatmap": heatmap,
        }

    # No YouTube captions available: download a capped compressed audio slice for Groq Whisper
    lower_prompt = (initial_prompt or "").lower()
    is_start_focused = any(
        w in lower_prompt for w in ("first", "intro", "beginning", "start", "opening")
    )
    max_audio_sec: Optional[float] = None
    if duration > 0:
        if is_start_focused and duration > 600.0:
            max_audio_sec = 600.0
        elif duration > 1200.0:
            max_audio_sec = 1200.0
    elif is_start_focused:
        max_audio_sec = 600.0

    raw_audio_tmpl = (downloads_dir / f"_raw_audio_{project_id}.%(ext)s").as_posix()
    ydl_opts: Dict[str, Any] = _apply_ytdlp_common_opts({
        "format": "bestaudio[abr<=64]/bestaudio/worst",
        "ffmpeg_location": str(Path(ffmpeg_bin).parent),
        "outtmpl": raw_audio_tmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    })
    if max_audio_sec is not None:
        ydl_opts["download_ranges"] = yt_dlp.utils.download_range_func(None, [(0.0, max_audio_sec)])

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])

    raw_candidates = sorted(downloads_dir.glob(f"_raw_audio_{project_id}.*"))
    if not raw_candidates:
        raise FileNotFoundError(f"Failed to download audio stream for {project_id}")
    raw_audio_file = raw_candidates[0]

    full_mp3_path = downloads_dir / f"{project_id}_full.mp3"
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            raw_audio_file.resolve().as_posix(),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "24k",
            full_mp3_path.resolve().as_posix(),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    raw_audio_file.unlink(missing_ok=True)

    probed_audio_dur = _probe_media_duration(full_mp3_path, ffmpeg_bin)
    full_duration = duration or probed_audio_dur or 60.0
    if max_audio_sec is not None:
        effective_duration = probed_audio_dur or (
            min(duration, max_audio_sec) if duration > 0 else max_audio_sec
        )
    else:
        effective_duration = full_duration

    # Derive heatmap from YouTube Most Replayed data or cheap audio peaks within the transcribed window
    filtered_heatmap = (
        [
            pt
            for pt in raw_heatmap
            if isinstance(pt, dict) and float(pt.get("start_time", 0.0)) < effective_duration
        ]
        if (max_audio_sec is not None and isinstance(raw_heatmap, list))
        else raw_heatmap
    )
    dummy_peaks: List[float] = []
    if not (isinstance(filtered_heatmap, list) and len(filtered_heatmap) > 0):
        dummy_peaks = _compute_peaks_from_media(full_mp3_path)
    heatmap = _normalize_heatmap(filtered_heatmap, dummy_peaks, effective_duration)

    return {
        "project_id": project_id,
        "audio_path": full_mp3_path,
        "prefetched_transcript": None,
        "title": title,
        "thumbnail": thumbnail,
        "duration": effective_duration,
        "full_duration": full_duration,
        "heatmap": heatmap,
    }


def normalize_clip_windows(
    clip_ranges: List[Dict[str, Any]], full_duration: float
) -> List[Dict[str, Any]]:
    max_dur = max(4.0, float(full_duration or 60.0))
    normalized: List[Dict[str, Any]] = []
    for idx, raw in enumerate(clip_ranges):
        s = max(0.0, float(raw.get("source_start", 0.0)))
        e = float(raw.get("source_end", s + 25.0))
        if s >= max_dur:
            s = max(0.0, max_dur - 15.0)
        e = min(max_dur, max(s + 4.0, e))
        if e - s < 2.0:
            continue
        item: Dict[str, Any] = {
            "source_start": round(s, 2),
            "source_end": round(e, 2),
            "title": str(raw.get("title") or f"Highlight #{idx + 1}"),
            "reason": str(raw.get("reason") or "Matched prompt highlight"),
            "virality_score": raw.get("virality_score"),
        }
        if raw.get("actual_duration") is not None:
            item["actual_duration"] = raw["actual_duration"]
        normalized.append(item)
    if not normalized:
        end_default = min(max_dur, 30.0)
        normalized.append(
            {
                "source_start": 0.0,
                "source_end": round(max(4.0, end_default), 2),
                "title": "Opening Highlight",
                "reason": "Default highlight segment",
                "virality_score": 8.0,
            }
        )
    return normalized


def download_youtube_clip_segments(
    youtube_url: str,
    project_id: str,
    downloads_dir: Path,
    clip_ranges: List[Dict[str, Any]],
    full_duration: float,
) -> Dict[str, Any]:
    """
    Low-storage Stage 2: downloads ONLY the AI-selected [source_start, source_end]
    video ranges via yt-dlp download_ranges, stitches them cleanly into {project_id}.mp4,
    and cleans up all temporary segment/audio files.
    """
    downloads_dir = downloads_dir.resolve()
    downloads_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = _find_ffmpeg()
    js_runtimes = _discover_js_runtimes()

    normalized_ranges = normalize_clip_windows(clip_ranges, full_duration)
    seg_files: List[Path] = []
    concat_list_path = downloads_dir / f"_concat_{project_id}.txt"
    video_path = downloads_dir / f"{project_id}.mp4"
    audio_path = downloads_dir / f"{project_id}.mp3"

    try:
        for idx, seg in enumerate(normalized_ranges):
            s = float(seg["source_start"])
            e = float(seg["source_end"])
            seg_tmpl = (downloads_dir / f"_seg_{project_id}_{idx}.%(ext)s").as_posix()
            ydl_opts: Dict[str, Any] = _apply_ytdlp_common_opts({
                "format": (
                    "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
                    "best[height<=720][ext=mp4]/best[height<=720]"
                ),
                "merge_output_format": "mp4",
                "download_ranges": yt_dlp.utils.download_range_func(None, [(s, e)]),
                "force_keyframes_at_cuts": True,
                "ffmpeg_location": str(Path(ffmpeg_bin).parent),
                "outtmpl": seg_tmpl,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            })

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([youtube_url])

            candidates = sorted(downloads_dir.glob(f"_seg_{project_id}_{idx}.*"))
            media_candidates = [
                c for c in candidates if c.suffix.lower() in {".mp4", ".mkv", ".webm"}
            ]
            if not media_candidates:
                raise FileNotFoundError(
                    f"Downloaded clip segment {idx} ({s}s-{e}s) not found for {project_id}"
                )
            seg_mp4 = downloads_dir / f"_seg_{project_id}_{idx}.mp4"
            _ensure_mp4_file(media_candidates[0], seg_mp4, ffmpeg_bin)
            probed_seg_dur = _probe_media_duration(seg_mp4, ffmpeg_bin)
            if probed_seg_dur and probed_seg_dur > 0:
                seg["actual_duration"] = round(probed_seg_dur, 2)
            seg_files.append(seg_mp4)

        if len(seg_files) == 1:
            subprocess.run(
                [
                    ffmpeg_bin,
                    "-y",
                    "-i",
                    seg_files[0].resolve().as_posix(),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-threads",
                    "2",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    video_path.resolve().as_posix(),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        else:
            lines = []
            for sf in seg_files:
                safe_path = sf.resolve().as_posix().replace("'", "'\\''")
                lines.append(f"file '{safe_path}'")
            concat_list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            subprocess.run(
                [
                    ffmpeg_bin,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    concat_list_path.resolve().as_posix(),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-threads",
                    "2",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    video_path.resolve().as_posix(),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
    finally:
        concat_list_path.unlink(missing_ok=True)
        for sf in downloads_dir.glob(f"_seg_{project_id}_*"):
            sf.unlink(missing_ok=True)
        (downloads_dir / f"{project_id}_full.mp3").unlink(missing_ok=True)

    actual_duration = _probe_media_duration(video_path, ffmpeg_bin)
    if not actual_duration or actual_duration <= 0:
        actual_duration = sum(float(s["source_end"]) - float(s["source_start"]) for s in normalized_ranges)

    audio_peaks = _extract_audio_and_peaks(video_path, audio_path)
    return {
        "video_path": video_path,
        "audio_path": audio_path,
        "duration": round(float(actual_duration), 2),
        "audio_peaks": audio_peaks,
        "normalized_ranges": normalized_ranges,
    }


def remap_clips_and_transcript_for_segments(
    clip_ranges: List[Dict[str, Any]],
    full_transcript: Dict[str, Any],
    full_heatmap: List[Dict[str, float]],
    full_duration: float,
) -> Tuple[List[ClipItem], Dict[str, Any], List[Dict[str, float]], float, int]:
    """
    Remaps original YouTube [source_start, source_end] ranges, transcript words/segments,
    and heatmap points sequentially onto the trimmed local MP4 timeline [0, total_clip_duration].
    """
    normalized = normalize_clip_windows(clip_ranges, full_duration)
    orig_words = full_transcript.get("words") or []
    orig_segments = full_transcript.get("segments") or []

    clips: List[ClipItem] = []
    remapped_words: List[Dict[str, Any]] = []
    remapped_segments: List[Dict[str, Any]] = []
    remapped_heatmap: List[Dict[str, float]] = []

    cursor = 0.0
    for idx, seg in enumerate(normalized):
        orig_start = float(seg["source_start"])
        orig_end = float(seg["source_end"])
        expected_dur = max(0.5, orig_end - orig_start)
        clip_dur = round(max(0.5, float(seg.get("actual_duration") or expected_dur)), 2)
        scale = clip_dur / expected_dur if expected_dur > 0 else 1.0

        clips.append(
            ClipItem(
                id=f"clip_{idx + 1}",
                title=str(seg.get("title") or f"Highlight #{idx + 1}")[:48],
                sourceStart=round(cursor, 2),
                sourceEnd=round(cursor + clip_dur, 2),
                timelineStart=round(cursor, 2),
                duration=clip_dur,
                viralityScore=(
                    float(seg["virality_score"])
                    if seg.get("virality_score") is not None
                    else None
                ),
                reason=seg.get("reason"),
            )
        )

        clip_word_texts: List[str] = []
        for w in orig_words:
            ws = float(w.get("start", 0.0))
            we = float(w.get("end", ws + 0.1))
            mid = (ws + we) * 0.5
            if mid < orig_start or mid > orig_end:
                continue
            new_s = round(
                max(cursor, min(cursor + clip_dur - 0.02, cursor + (ws - orig_start) * scale)),
                3,
            )
            new_e = round(
                max(new_s + 0.02, min(cursor + clip_dur, cursor + (we - orig_start) * scale)),
                3,
            )
            word_str = str(w.get("word", "")).strip()
            if word_str:
                remapped_words.append({"word": word_str, "start": new_s, "end": new_e})
                clip_word_texts.append(word_str)

        matched_seg_count = 0
        for s_item in orig_segments:
            ss = float(s_item.get("start", 0.0))
            se = float(s_item.get("end", ss + 0.5))
            if se <= orig_start or ss >= orig_end:
                continue
            new_s = round(
                max(cursor, min(cursor + clip_dur - 0.05, cursor + (ss - orig_start) * scale)),
                3,
            )
            new_e = round(
                max(new_s + 0.05, min(cursor + clip_dur, cursor + (se - orig_start) * scale)),
                3,
            )
            txt = str(s_item.get("text", "")).strip()
            if txt:
                remapped_segments.append({"start": new_s, "end": new_e, "text": txt})
                matched_seg_count += 1

        if matched_seg_count == 0 and clip_word_texts:
            remapped_segments.append(
                {
                    "start": round(cursor, 3),
                    "end": round(cursor + clip_dur, 3),
                    "text": " ".join(clip_word_texts),
                }
            )

        for hp in full_heatmap:
            hs = float(hp.get("start_time", 0.0))
            he = float(hp.get("end_time", hs))
            if he <= orig_start or hs >= orig_end:
                continue
            new_hs = round(
                max(cursor, min(cursor + clip_dur, cursor + (hs - orig_start) * scale)),
                2,
            )
            new_he = round(
                max(new_hs + 0.05, min(cursor + clip_dur, cursor + (he - orig_start) * scale)),
                2,
            )
            remapped_heatmap.append(
                {
                    "start_time": new_hs,
                    "end_time": new_he,
                    "value": float(hp.get("value", 0.0)),
                }
            )

        cursor = round(cursor + clip_dur, 2)

    remapped_transcript = {
        "text": " ".join(w["word"] for w in remapped_words)
        if remapped_words
        else str(full_transcript.get("text", "")),
        "words": remapped_words,
        "segments": remapped_segments,
        "source": full_transcript.get("source", "groq_whisper"),
        "model_used": full_transcript.get("model_used"),
    }

    total_clip_duration = round(max(0.5, cursor), 2)
    storage_saved_pct = max(
        0, min(99, round((1.0 - total_clip_duration / max(1.0, full_duration)) * 100))
    )
    return clips, remapped_transcript, remapped_heatmap, total_clip_duration, storage_saved_pct


async def ingest_youtube_video(
    youtube_url: str,
    workspace_dir: Path,
    base_url: str = "http://localhost:8000",
) -> Dict[str, Any]:
    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    downloads_dir = workspace_dir / "downloads"
    result = await asyncio.to_thread(_download_youtube_sync, youtube_url, project_id, downloads_dir)
    result["video_url"] = f"{base_url.rstrip('/')}/media/downloads/{project_id}.mp4"
    return result
