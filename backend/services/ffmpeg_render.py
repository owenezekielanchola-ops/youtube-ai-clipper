import asyncio
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List

from schemas import ClipItem, ProjectState
from services.ass_subtitles import generate_ass_subtitles
from services.youtube_ingest import _find_ffmpeg


def _escape_ffmpeg_subtitles_path(ass_path: Path, work_dir: Path) -> str:
    """
    Computes a POSIX path for the .ass subtitle file relative to work_dir (falling back
    to an absolute POSIX path across drives) and escapes libavfilter special characters
    so Windows drive colons and special chars never break `-vf ass=...`.
    """
    try:
        raw = ass_path.resolve().relative_to(work_dir.resolve()).as_posix()
    except ValueError:
        raw = ass_path.resolve().as_posix()

    for ch in ("\\", ":", "'", ",", "[", "]"):
        raw = raw.replace(ch, f"\\{ch}")
    return raw


def _build_vf_filter(aspect_ratio: str, ass_path: Path | None, work_dir: Path) -> str:
    filters: List[str] = []
    if aspect_ratio == "9:16":
        # Safe center crop to 9:16 with even dimensions + scale to 1080x1920
        filters.append(
            "crop='min(iw,trunc(ih*9/16/2)*2)':'min(ih,trunc(iw*16/9/2)*2)'"
        )
        filters.append("scale=1080:1920:flags=bicubic")
        filters.append("setsar=1")
    elif aspect_ratio == "1:1":
        filters.append("crop='min(iw,ih)':'min(iw,ih)'")
        filters.append("scale=1080:1080:flags=bicubic")
        filters.append("setsar=1")

    if ass_path is not None:
        escaped_ass = _escape_ffmpeg_subtitles_path(ass_path, work_dir)
        filters.append(f"ass={escaped_ass}")

    return ",".join(filters)


def _run_ffmpeg_with_progress(
    cmd: List[str],
    cwd: Path,
    total_duration: float,
    start_pct: int,
    end_pct: int,
    loop: asyncio.AbstractEventLoop,
    progress_cb: Callable[[int, str], Awaitable[None]],
    status_label: str,
) -> None:
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    dur = max(0.5, total_duration)

    stderr_chunks: List[str] = []

    def _drain_stderr() -> None:
        if proc.stderr is not None:
            stderr_chunks.append(proc.stderr.read())

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    try:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    ms = int(line.split("=", 1)[1])
                    sec = ms / 1_000_000.0
                    frac = max(0.0, min(1.0, sec / dur))
                    pct = int(start_pct + frac * (end_pct - start_pct))
                    asyncio.run_coroutine_threadsafe(progress_cb(pct, status_label), loop)
                except ValueError:
                    pass
    finally:
        if proc.poll() is None:
            proc.kill()
        stderr_thread.join()

    stderr_out = "".join(stderr_chunks)
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"FFmpeg failed (exit {code}): {stderr_out[-600:]}")


def _render_sync(
    project_state: ProjectState,
    export_mode: str,
    workspace_dir: Path,
    loop: asyncio.AbstractEventLoop,
    progress_cb: Callable[[int, str], Awaitable[None]],
) -> List[Dict[str, str]]:
    ffmpeg_bin = _find_ffmpeg()
    source_mp4 = (workspace_dir / "downloads" / f"{project_state.projectId}.mp4").resolve()
    if not source_mp4.exists():
        raise FileNotFoundError(f"Source MP4 not found at {source_mp4}")

    exports_dir = (workspace_dir / "exports").resolve()
    exports_dir.mkdir(parents=True, exist_ok=True)

    job_id = uuid.uuid4().hex[:8]
    work_dir = (exports_dir / f"_tmp_{project_state.projectId}_{job_id}").resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    clips = project_state.tracks.clips
    if not clips:
        raise ValueError("Cannot export an empty timeline with 0 clips.")

    ratio = project_state.workspaceState.targetAspectRatio
    has_captions = len(project_state.tracks.captions) > 0
    exported_files: List[Dict[str, str]] = []

    def emit(pct: int, msg: str) -> None:
        asyncio.run_coroutine_threadsafe(progress_cb(pct, msg), loop)

    try:
        emit(5, "Preparing sequential low-RAM export...")

        if export_mode == "individual":
            total_clips = len(clips)
            for idx, clip in enumerate(clips):
                base_pct = int(5 + (idx / total_clips) * 90)
                next_pct = int(5 + ((idx + 1) / total_clips) * 90)
                emit(base_pct, f"Rendering clip {idx + 1}/{total_clips}: {clip.title}...")

                # Remap clip so timelineStart = 0 for standalone export
                standalone_clip = ClipItem(
                    id=clip.id,
                    title=clip.title,
                    sourceStart=clip.sourceStart,
                    sourceEnd=clip.sourceEnd,
                    timelineStart=0.0,
                    duration=clip.duration,
                )
                ass_path: Path | None = None
                if has_captions:
                    ass_path = work_dir / f"clip_{idx}.ass"
                    generate_ass_subtitles(
                        project_state, ass_path, clips_override=[standalone_clip]
                    )

                vf_filter = _build_vf_filter(ratio, ass_path, work_dir)
                safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "_", clip.title).strip("_") or f"clip_{idx + 1}"
                out_name = f"{project_state.projectId}_{idx + 1}_{safe_title}_{job_id}.mp4"
                out_path = exports_dir / out_name

                cmd = [
                    ffmpeg_bin,
                    "-nostdin",
                    "-y",
                    "-ss",
                    f"{clip.sourceStart:.3f}",
                    "-t",
                    f"{max(0.1, clip.sourceEnd - clip.sourceStart):.3f}",
                    "-i",
                    source_mp4.as_posix(),
                ]
                if vf_filter:
                    cmd.extend(["-vf", vf_filter])
                cmd.extend(
                    [
                        "-c:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-threads",
                        "0",
                        "-crf",
                        "21",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        "-movflags",
                        "+faststart",
                        "-progress",
                        "pipe:1",
                        "-nostats",
                        out_path.as_posix(),
                    ]
                )
                _run_ffmpeg_with_progress(
                    cmd,
                    work_dir,
                    clip.duration,
                    base_pct,
                    next_pct,
                    loop,
                    progress_cb,
                    f"Rendering clip {idx + 1}/{total_clips}: {clip.title}...",
                )
                exported_files.append(
                    {
                        "filename": out_name,
                        "title": clip.title,
                        "url": f"/media/exports/{out_name}",
                    }
                )
            emit(100, "All individual clips exported!")
            return exported_files

        # Combined mode:
        # 1. Cut each clip sequentially (1 stream at a time -> minimal RAM)
        segment_paths: List[Path] = []
        total_clips = len(clips)
        for idx, clip in enumerate(clips):
            pct = int(8 + (idx / max(1, total_clips)) * 48)
            emit(pct, f"Cutting clip {idx + 1}/{total_clips} sequentially...")
            seg_path = work_dir / f"seg_{idx:03d}.mp4"
            cut_cmd = [
                ffmpeg_bin,
                "-nostdin",
                "-y",
                "-ss",
                f"{clip.sourceStart:.3f}",
                "-t",
                f"{max(0.1, clip.sourceEnd - clip.sourceStart):.3f}",
                "-i",
                source_mp4.as_posix(),
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-threads",
                "0",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-avoid_negative_ts",
                "make_zero",
                seg_path.as_posix(),
            ]
            res = subprocess.run(
                cut_cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if res.returncode != 0:
                raise RuntimeError(f"FFmpeg clip cut failed: {res.stderr[-500:]}")
            segment_paths.append(seg_path)

        # 2. Concatenate via FFmpeg lightweight concat demuxer
        emit(60, "Concatenating timeline via FFmpeg concat demuxer...")
        concat_file = work_dir / "list.txt"
        concat_lines = [f"file '{p.name}'" for p in segment_paths]
        concat_file.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

        merged_raw = work_dir / "merged_raw.mp4"
        if len(segment_paths) == 1:
            shutil.copy2(segment_paths[0], merged_raw)
        else:
            concat_cmd = [
                ffmpeg_bin,
                "-nostdin",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_file.name,
                "-c",
                "copy",
                str(merged_raw),
            ]
            res = subprocess.run(
                concat_cmd,
                cwd=str(work_dir),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if res.returncode != 0:
                raise RuntimeError(f"FFmpeg concat failed: {res.stderr[-500:]}")

        # 3. Generate .ass subtitles and run final crop + subtitle burn-in pass
        ass_path: Path | None = None
        if has_captions:
            emit(68, "Generating styled .ass subtitles...")
            ass_path = work_dir / "captions.ass"
            generate_ass_subtitles(project_state, ass_path)

        vf_filter = _build_vf_filter(ratio, ass_path, work_dir)
        out_name = f"{project_state.projectId}_final_{ratio.replace(':', 'x')}_{job_id}.mp4"
        final_out = exports_dir / out_name
        total_tl_duration = sum(c.duration for c in clips)

        if not vf_filter:
            emit(90, "Finalizing MP4 container...")
            shutil.move(str(merged_raw), str(final_out))
        else:
            emit(72, f"Encoding {ratio} & burning captions (-threads 0 -preset ultrafast)...")
            burn_cmd = [
                ffmpeg_bin,
                "-nostdin",
                "-y",
                "-i",
                merged_raw.name,
                "-vf",
                vf_filter,
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-threads",
                "0",
                "-crf",
                "21",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                "-progress",
                "pipe:1",
                "-nostats",
                final_out.as_posix(),
            ]
            _run_ffmpeg_with_progress(
                burn_cmd,
                work_dir,
                total_tl_duration,
                72,
                98,
                loop,
                progress_cb,
                f"Encoding {ratio} & burning captions...",
            )

        exported_files.append(
            {
                "filename": out_name,
                "title": project_state.projectName,
                "url": f"/media/exports/{out_name}",
            }
        )
        emit(100, "Export complete!")
        return exported_files

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def render_project_video(
    project_state: ProjectState,
    export_mode: str,
    workspace_dir: Path,
    progress_cb: Callable[[int, str], Awaitable[None]],
) -> List[Dict[str, str]]:
    loop = asyncio.get_running_loop()
    return await asyncio.to_thread(
        _render_sync, project_state, export_mode, workspace_dir, loop, progress_cb
    )
