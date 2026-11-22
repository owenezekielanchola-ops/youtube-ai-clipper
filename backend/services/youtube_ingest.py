import asyncio
import bisect
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import uuid
from pathlib import Path

import yt_dlp

from schemas import ClipItem


class IngestValidationError(ValueError):
    """Raised when a YouTube URL cannot or should not be ingested as requested."""


MAX_FULL_DOWNLOAD_DURATION_SEC = 3600.0        # 1h max for full-video download
LONG_VIDEO_SLICE_THRESHOLD_SEC = 1800.0        # 30m threshold for targeted audio slices & clip caps
MAX_CLIP_ONLY_SINGLE_DURATION_SEC = 180.0      # Safety cap per clip on >30m VODs (unless explicit range)
MAX_CLIP_ONLY_TOTAL_DURATION_SEC = 360.0       # Safety cap total downloaded clip duration on >30m VODs


def _bisect_by_start(items: List[Dict[str, Any]], target: float) -> int:
    if sys.version_info >= (3, 10):
        return bisect.bisect_left(items, target, key=lambda item: float(item.get("start", 0.0)))
    lo, hi = 0, len(items)
    while lo < hi:
        mid = (lo + hi) // 2
        if float(items[mid].get("start", 0.0)) < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


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


def _format_duration_hms(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs}h {mins:02d}m"
    if mins > 0:
        return f"{mins}m {secs:02d}s" if secs > 0 else f"{mins}m"
    return f"{secs}s"


def _check_live_or_unready(info: Dict[str, Any]) -> None:
    live_status = str(info.get("live_status") or "").lower()
    if live_status == "is_upcoming":
        raise IngestValidationError(
            "This YouTube livestream is scheduled (upcoming) and has not aired yet."
        )
    if bool(info.get("is_live")) or live_status == "is_live":
        raise IngestValidationError(
            "This YouTube livestream is currently LIVE. Wait until the stream ends "
            "(and VOD processing finishes), then paste the link with a clip prompt like "
            "'find only the interesting part'."
        )


def _extract_youtube_chapters(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_chapters = info.get("chapters") or []
    chapters: List[Dict[str, Any]] = []
    if not isinstance(raw_chapters, list):
        return chapters
    for ch in raw_chapters:
        if not isinstance(ch, dict):
            continue
        title = str(ch.get("title") or "").strip()
        if not title:
            continue
        try:
            st = max(0.0, float(ch.get("start_time", 0.0)))
            et = float(ch.get("end_time", st))
        except (TypeError, ValueError):
            continue
        if et > st:
            chapters.append(
                {
                    "start_time": round(st, 2),
                    "end_time": round(et, 2),
                    "title": title[:120],
                }
            )
    return chapters


def _parse_hms_token(token: str) -> Optional[float]:
    parts = token.strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        h, m, s = nums
        if h < 0 or m < 0 or s < 0 or m >= 60 or s >= 60:
            return None
        return float(h * 3600 + m * 60 + s)
    if len(nums) == 2:
        m, s = nums
        if m < 0 or s < 0 or s >= 60:
            return None
        return float(m * 60 + s)
    return None


def parse_explicit_clip_range(
    prompt: str, duration: float = 0.0
) -> Optional[Tuple[float, float]]:
    if not prompt:
        return None
    match = re.search(
        r"(?:from\s+)?(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:to|-|–|—|until)\s*(\d{1,2}:\d{2}(?::\d{2})?)",
        prompt,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    start = _parse_hms_token(match.group(1))
    end = _parse_hms_token(match.group(2))
    if start is None or end is None or end <= start:
        return None
    if duration > 0:
        if start >= duration:
            return None
        end = min(float(duration), end)
    span = end - start
    if span < 3.0 or span > 600.0:
        return None
    return round(start, 2), round(end, 2)


def _merge_time_windows(windows: List[Tuple[float, float]], gap_sec: float = 10.0) -> List[Tuple[float, float]]:
    if not windows:
        return []
    ordered = sorted((round(s, 2), round(e, 2)) for s, e in windows if e > s)
    merged: List[Tuple[float, float]] = [ordered[0]]
    for s, e in ordered[1:]:
        prev_s, prev_e = merged[-1]
        if s <= prev_e + gap_sec:
            merged[-1] = (prev_s, max(prev_e, e))
        else:
            merged.append((s, e))
    return merged


def _select_long_video_audio_windows(
    duration: float, raw_heatmap: Any, initial_prompt: str
) -> List[Tuple[float, float]]:
    explicit = parse_explicit_clip_range(initial_prompt, duration)
    if explicit is not None:
        rs, re_val = explicit
        return [(round(max(0.0, rs - 15.0), 2), round(min(duration, re_val + 15.0), 2))]

    lower_prompt = (initial_prompt or "").lower()
    is_start_focused = any(
        w in lower_prompt for w in ("first", "intro", "beginning", "start", "opening")
    )
    if is_start_focused:
        return [(0.0, round(min(duration, 240.0), 2))]

    valid_pts = [
        pt
        for pt in (raw_heatmap if isinstance(raw_heatmap, list) else [])
        if isinstance(pt, dict) and "start_time" in pt
    ]
    if valid_pts:
        sorted_pts = sorted(valid_pts, key=lambda p: float(p.get("value", 0.0)), reverse=True)
        min_sep = max(120.0, duration * 0.04)
        chosen_centers: List[float] = []
        for sep in (min_sep, 60.0):
            for pt in sorted_pts:
                st = float(pt.get("start_time", 0.0))
                et = float(pt.get("end_time", st))
                center = (st + max(st, et)) * 0.5
                if any(abs(center - c) < sep for c in chosen_centers):
                    continue
                chosen_centers.append(center)
                if len(chosen_centers) >= 5:
                    break
            if len(chosen_centers) >= 3:
                break
        if chosen_centers:
            peak_windows = [
                (max(0.0, c - 25.0), min(duration, c + 50.0))
                for c in chosen_centers
            ]
            return _merge_time_windows(peak_windows)

    even_windows = [
        (max(0.0, duration * frac - 60.0), min(duration, duration * frac + 60.0))
        for frac in (0.10, 0.35, 0.65, 0.88)
    ]
    return _merge_time_windows(even_windows)


def _ffmpeg_http_input_args(
    url: str, http_headers: Optional[Dict[str, str]]
) -> List[str]:
    low = url.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return []
    args = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "3"]
    if isinstance(http_headers, dict) and http_headers:
        header_lines = "".join(
            f"{k}: {v}\r\n"
            for k, v in http_headers.items()
            if k and v is not None and str(v).strip()
        )
        if header_lines:
            args.extend(["-headers", header_lines])
    return args


def _pick_direct_audio_url(
    info: Dict[str, Any],
) -> Tuple[Optional[str], Optional[Dict[str, str]]]:
    formats = info.get("formats") or []
    if not isinstance(formats, list):
        return None, None
    audio_only = [
        f
        for f in formats
        if isinstance(f, dict)
        and f.get("url")
        and f.get("vcodec") in (None, "none")
        and f.get("acodec") not in (None, "none")
    ]
    candidates = audio_only or [
        f
        for f in formats
        if isinstance(f, dict)
        and f.get("url")
        and f.get("acodec") not in (None, "none")
    ]
    if not candidates:
        return None, None

    def _rank(f: Dict[str, Any]) -> Tuple[int, int, float]:
        proto = str(f.get("protocol") or "").lower()
        is_direct = 0 if proto in ("https", "http") else 1
        ext = str(f.get("ext") or "").lower()
        acodec = str(f.get("acodec") or "").lower()
        is_m4a = 0 if (ext == "m4a" or "mp4a" in acodec) else 1
        abr = float(f.get("abr") or f.get("tbr") or 999.0)
        return (is_direct, is_m4a, abs(abr - 64.0) if audio_only else abr)

    candidates.sort(key=_rank)
    chosen = candidates[0]
    headers = chosen.get("http_headers") or info.get("http_headers")
    return str(chosen["url"]), headers if isinstance(headers, dict) else None


def _download_audio_slices(
    youtube_url: str,
    project_id: str,
    downloads_dir: Path,
    windows: List[Tuple[float, float]],
    ffmpeg_bin: str,
    direct_audio_url: Optional[str] = None,
    direct_audio_headers: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    slices: List[Dict[str, Any]] = []
    last_err: Optional[str] = None
    for idx, (win_s, win_e) in enumerate(windows):
        span_dur = max(5.0, win_e - win_s)
        print(
            f"[Ingest] Downloading audio slice {idx + 1}/{len(windows)} "
            f"({win_s:.0f}s -> {win_e:.0f}s)...",
            flush=True,
        )
        raw_tmpl = (downloads_dir / f"_raw_slice_{project_id}_{idx}.%(ext)s").as_posix()
        slice_mp3 = downloads_dir / f"_slice_{project_id}_{idx}.mp3"
        try:
            got_slice = False
            if direct_audio_url:
                try:
                    fast_cmd = [
                        ffmpeg_bin,
                        "-nostdin",
                        "-y",
                        *_ffmpeg_http_input_args(direct_audio_url, direct_audio_headers),
                        "-ss",
                        str(win_s),
                        "-t",
                        str(span_dur),
                        "-i",
                        direct_audio_url,
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-b:a",
                        "24k",
                        slice_mp3.resolve().as_posix(),
                    ]
                    fast_proc = subprocess.run(
                        fast_cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=max(75, int(span_dur * 0.6)),
                    )
                    if (
                        fast_proc.returncode == 0
                        and slice_mp3.exists()
                        and slice_mp3.stat().st_size >= 1500
                    ):
                        got_slice = True
                    else:
                        err_tail = (
                            fast_proc.stderr.decode("utf-8", errors="replace").strip().splitlines()[-1]
                            if fast_proc.stderr
                            else f"exit {fast_proc.returncode}"
                        )
                        print(
                            f"[Ingest] Direct slice {idx + 1} FFmpeg fallback ({err_tail})",
                            flush=True,
                        )
                        slice_mp3.unlink(missing_ok=True)
                except Exception as fast_exc:
                    print(
                        f"[Ingest] Direct slice {idx + 1} FFmpeg fallback ({fast_exc})",
                        flush=True,
                    )
                    slice_mp3.unlink(missing_ok=True)

            if not got_slice:
                ydl_opts: Dict[str, Any] = _apply_ytdlp_common_opts({
                    "format": "bestaudio[abr<=64]/bestaudio/worst/best",
                    "ffmpeg_location": str(Path(ffmpeg_bin).parent),
                    "outtmpl": raw_tmpl,
                    "download_ranges": yt_dlp.utils.download_range_func(None, [(win_s, win_e)]),
                    "noplaylist": True,
                    "quiet": True,
                    "no_warnings": True,
                })
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([youtube_url])

                raw_candidates = [
                    p
                    for p in sorted(downloads_dir.glob(f"_raw_slice_{project_id}_{idx}.*"))
                    if p.is_file() and p.suffix.lower() not in {".part", ".ytdl"}
                ]
                if not raw_candidates:
                    last_err = f"No downloaded slice file found for slice {idx + 1}"
                    print(f"[Ingest] Slice {idx + 1} failed: {last_err}", flush=True)
                    continue
                raw_file = raw_candidates[0]
                subprocess.run(
                    [
                        ffmpeg_bin,
                        "-nostdin",
                        "-y",
                        "-i",
                        raw_file.resolve().as_posix(),
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-b:a",
                        "24k",
                        slice_mp3.resolve().as_posix(),
                    ],
                    check=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=60,
                )

            probed_dur = _probe_media_duration(slice_mp3, ffmpeg_bin)
            slices.append(
                {
                    "path": slice_mp3,
                    "start_offset": round(win_s, 2),
                    "end_offset": round(win_e, 2),
                    "duration": round(float(probed_dur or span_dur), 2),
                }
            )
        except Exception as exc:
            last_err = str(exc)
            print(f"[Ingest] Slice {idx + 1} failed: {exc}", flush=True)
            slice_mp3.unlink(missing_ok=True)
        finally:
            for tmp in downloads_dir.glob(f"_raw_slice_{project_id}_{idx}.*"):
                tmp.unlink(missing_ok=True)

    if not slices:
        raise RuntimeError(
            f"Failed to download any audio slices for {project_id}: {last_err or 'unknown error'}"
        )
    return slices


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

    # Probe metadata first to block live/upcoming streams and >1h full downloads
    probe_opts: Dict[str, Any] = _apply_ytdlp_common_opts({
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    })
    with yt_dlp.YoutubeDL(probe_opts) as ydl:
        probe_info = ydl.extract_info(youtube_url, download=False)
    _check_live_or_unready(probe_info)
    probe_dur = float(probe_info.get("duration") or 0.0)
    if probe_dur > MAX_FULL_DOWNLOAD_DURATION_SEC:
        raise IngestValidationError(
            f"This video is {_format_duration_hms(probe_dur)} long (> 1 hour). "
            "Full-video download is disabled for long videos/VODs — enter a clip prompt "
            "(e.g. 'find only the interesting part') and click 'Clip & Load' so only "
            "the matching clip(s) are downloaded."
        )

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
        with urllib.request.urlopen(req, timeout=30) as resp:
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

    print("[Ingest] Probing YouTube metadata...", flush=True)
    with yt_dlp.YoutubeDL(meta_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)

    _check_live_or_unready(info)
    title = info.get("title") or "YouTube Video"
    thumbnail = info.get("thumbnail") or ""
    duration = float(info.get("duration") or 0.0)
    raw_heatmap = info.get("heatmap") or []
    chapters = _extract_youtube_chapters(info)

    yt_transcript = _extract_youtube_json3_transcript(info)
    print(
        f"[Ingest] Probed '{title[:48]}' ({_format_duration_hms(duration)}) | "
        f"captions={'yes (' + str(len(yt_transcript.get('segments', []))) + ' segs)' if yt_transcript else 'no'} | "
        f"heatmap={len(raw_heatmap) if isinstance(raw_heatmap, list) else 0} pts",
        flush=True,
    )
    if yt_transcript is not None:
        effective_duration = duration
        if effective_duration <= 0 and yt_transcript.get("segments"):
            effective_duration = float(yt_transcript["segments"][-1]["end"])
        fallback_peaks = [0.4, 0.55, 0.85, 0.65, 0.9, 0.5, 0.7, 0.45]
        heatmap = _normalize_heatmap(raw_heatmap, fallback_peaks, effective_duration or 60.0)
        return {
            "project_id": project_id,
            "audio_path": None,
            "audio_slices": [],
            "prefetched_transcript": yt_transcript,
            "title": title,
            "thumbnail": thumbnail,
            "duration": effective_duration or 60.0,
            "full_duration": effective_duration or 60.0,
            "transcribed_window_duration": effective_duration or 60.0,
            "heatmap": heatmap,
            "chapters": chapters,
        }

    # No YouTube captions available:
    # For >30m VODs, download targeted peak/evenly-spaced audio slices instead of blind [0..20m] only.
    if duration > LONG_VIDEO_SLICE_THRESHOLD_SEC:
        windows = _select_long_video_audio_windows(duration, raw_heatmap, initial_prompt)
        direct_audio_url, direct_audio_headers = _pick_direct_audio_url(info)
        slices = _download_audio_slices(
            youtube_url,
            project_id,
            downloads_dir,
            windows,
            ffmpeg_bin,
            direct_audio_url=direct_audio_url,
            direct_audio_headers=direct_audio_headers,
        )
        transcribed_window_duration = round(sum(float(s["duration"]) for s in slices), 2)
        heatmap = _normalize_heatmap(raw_heatmap, [0.4, 0.7, 0.9, 0.6, 0.5], duration)
        return {
            "project_id": project_id,
            "audio_path": None,
            "audio_slices": slices,
            "prefetched_transcript": None,
            "title": title,
            "thumbnail": thumbnail,
            "duration": duration,
            "full_duration": duration,
            "transcribed_window_duration": transcribed_window_duration,
            "heatmap": heatmap,
            "chapters": chapters,
        }

    # Short/medium captionless video (<= 30m): download capped compressed audio slice for Groq Whisper
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
        "audio_slices": [],
        "prefetched_transcript": None,
        "title": title,
        "thumbnail": thumbnail,
        "duration": effective_duration,
        "full_duration": full_duration,
        "transcribed_window_duration": effective_duration,
        "heatmap": heatmap,
        "chapters": chapters,
    }


def normalize_clip_windows(
    clip_ranges: List[Dict[str, Any]],
    full_duration: float,
    max_clip_duration: Optional[float] = None,
    max_total_duration: Optional[float] = None,
) -> List[Dict[str, Any]]:
    max_dur = max(4.0, float(full_duration or 60.0))
    normalized: List[Dict[str, Any]] = []
    running_total = 0.0
    for idx, raw in enumerate(clip_ranges):
        s = max(0.0, float(raw.get("source_start", 0.0)))
        e = float(raw.get("source_end", s + 25.0))
        if s >= max_dur:
            s = max(0.0, max_dur - 15.0)
        e = min(max_dur, max(s + 4.0, e))
        if max_clip_duration is not None and (e - s) > max_clip_duration:
            e = min(max_dur, s + max_clip_duration)
        if max_total_duration is not None:
            remaining = max_total_duration - running_total
            if remaining < 4.0:
                break
            if (e - s) > remaining:
                e = min(max_dur, s + remaining)
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
        running_total += e - s
    if not normalized:
        cap_default = min(max_dur, 30.0)
        if max_clip_duration is not None:
            cap_default = min(cap_default, max_clip_duration)
        normalized.append(
            {
                "source_start": 0.0,
                "source_end": round(max(4.0, cap_default), 2),
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
    allow_long_clips: bool = False,
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

    cap_single = (
        MAX_CLIP_ONLY_SINGLE_DURATION_SEC
        if (full_duration > LONG_VIDEO_SLICE_THRESHOLD_SEC and not allow_long_clips)
        else None
    )
    cap_total = (
        MAX_CLIP_ONLY_TOTAL_DURATION_SEC
        if (full_duration > LONG_VIDEO_SLICE_THRESHOLD_SEC and not allow_long_clips)
        else None
    )
    normalized_ranges = normalize_clip_windows(
        clip_ranges,
        full_duration,
        max_clip_duration=cap_single,
        max_total_duration=cap_total,
    )
    seg_files: List[Path] = []
    seg_already_encoded: List[bool] = []
    concat_list_path = downloads_dir / f"_concat_{project_id}.txt"
    video_path = downloads_dir / f"{project_id}.mp4"
    audio_path = downloads_dir / f"{project_id}.mp3"

    try:
        # Resolve direct stream URLs once so FFmpeg can use fast -ss input seeking (no 24h full-stream decode)
        stream_specs: List[Tuple[str, Optional[Dict[str, str]]]] = []
        try:
            probe_opts = _apply_ytdlp_common_opts({
                "format": (
                    "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
                    "best[height<=720][ext=mp4]/best[height<=720]"
                ),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            })
            with yt_dlp.YoutubeDL(probe_opts) as ydl:
                s_info = ydl.extract_info(youtube_url, download=False)
            top_headers = s_info.get("http_headers") if isinstance(s_info.get("http_headers"), dict) else None
            req_fmts = s_info.get("requested_formats") or []
            if len(req_fmts) >= 2 and req_fmts[0].get("url") and req_fmts[1].get("url"):
                stream_specs = [
                    (
                        str(req_fmts[0]["url"]),
                        req_fmts[0].get("http_headers") if isinstance(req_fmts[0].get("http_headers"), dict) else top_headers,
                    ),
                    (
                        str(req_fmts[1]["url"]),
                        req_fmts[1].get("http_headers") if isinstance(req_fmts[1].get("http_headers"), dict) else top_headers,
                    ),
                ]
            elif s_info.get("url"):
                stream_specs = [(str(s_info["url"]), top_headers)]
        except Exception:
            stream_specs = []

        for idx, seg in enumerate(normalized_ranges):
            s = float(seg["source_start"])
            e = float(seg["source_end"])
            span_dur = max(1.0, e - s)
            print(
                f"[Ingest] Downloading clip {idx + 1}/{len(normalized_ranges)} "
                f"({s:.1f}s -> {e:.1f}s)...",
                flush=True,
            )
            seg_mp4 = downloads_dir / f"_seg_{project_id}_{idx}.mp4"
            got_seg = False

            if stream_specs:
                try:
                    if len(stream_specs) == 2:
                        u0, h0 = stream_specs[0]
                        u1, h1 = stream_specs[1]
                        ff_cmd = [
                            ffmpeg_bin,
                            "-nostdin",
                            "-y",
                            *_ffmpeg_http_input_args(u0, h0),
                            "-ss",
                            str(s),
                            "-t",
                            str(span_dur),
                            "-i",
                            u0,
                            *_ffmpeg_http_input_args(u1, h1),
                            "-ss",
                            str(s),
                            "-t",
                            str(span_dur),
                            "-i",
                            u1,
                            "-map",
                            "0:v:0",
                            "-map",
                            "1:a:0",
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
                            seg_mp4.resolve().as_posix(),
                        ]
                    else:
                        u0, h0 = stream_specs[0]
                        ff_cmd = [
                            ffmpeg_bin,
                            "-nostdin",
                            "-y",
                            *_ffmpeg_http_input_args(u0, h0),
                            "-ss",
                            str(s),
                            "-t",
                            str(span_dur),
                            "-i",
                            u0,
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
                            seg_mp4.resolve().as_posix(),
                        ]
                    ff_res = subprocess.run(
                        ff_cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=120,
                    )
                    if ff_res.returncode == 0 and seg_mp4.exists() and seg_mp4.stat().st_size > 10000:
                        got_seg = True
                    else:
                        err_tail = (
                            ff_res.stderr.decode("utf-8", errors="replace").strip().splitlines()[-1]
                            if ff_res.stderr
                            else f"exit {ff_res.returncode}"
                        )
                        print(
                            f"[Ingest] Direct clip {idx + 1} FFmpeg fallback ({err_tail})",
                            flush=True,
                        )
                        seg_mp4.unlink(missing_ok=True)
                except Exception as seg_exc:
                    print(
                        f"[Ingest] Direct clip {idx + 1} FFmpeg fallback ({seg_exc})",
                        flush=True,
                    )
                    seg_mp4.unlink(missing_ok=True)

            if not got_seg:
                seg_tmpl = (downloads_dir / f"_seg_{project_id}_{idx}.%(ext)s").as_posix()
                ydl_opts: Dict[str, Any] = _apply_ytdlp_common_opts({
                    "format": (
                        "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
                        "best[height<=720][ext=mp4]/best[height<=720]"
                    ),
                    "merge_output_format": "mp4",
                    "download_ranges": yt_dlp.utils.download_range_func(None, [(s, e)]),
                    "force_keyframes_at_cuts": full_duration <= LONG_VIDEO_SLICE_THRESHOLD_SEC,
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
                _ensure_mp4_file(media_candidates[0], seg_mp4, ffmpeg_bin)

            probed_seg_dur = _probe_media_duration(seg_mp4, ffmpeg_bin)
            if probed_seg_dur and probed_seg_dur > 0:
                seg["actual_duration"] = round(probed_seg_dur, 2)
            seg_files.append(seg_mp4)
            seg_already_encoded.append(got_seg)

        if len(seg_files) == 1 and seg_already_encoded and seg_already_encoded[0]:
            seg_files[0].replace(video_path)
        elif len(seg_files) == 1:
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
        word_iter = (
            orig_words[_bisect_by_start(orig_words, max(0.0, orig_start - 5.0)) :]
            if len(orig_words) > 256
            else orig_words
        )
        for w in word_iter:
            ws = float(w.get("start", 0.0))
            if len(orig_words) > 256 and ws > orig_end + 5.0:
                break
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
        seg_iter = (
            orig_segments[_bisect_by_start(orig_segments, max(0.0, orig_start - 30.0)) :]
            if len(orig_segments) > 256
            else orig_segments
        )
        for s_item in seg_iter:
            ss = float(s_item.get("start", 0.0))
            if len(orig_segments) > 256 and ss >= orig_end + 5.0:
                break
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
