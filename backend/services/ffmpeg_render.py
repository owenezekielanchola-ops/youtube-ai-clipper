import asyncio
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from schemas import (
    AudioEditItem,
    ClipItem,
    ProjectState,
    SfxClipItem,
    VideoCropRect,
    ZoomEffectItem,
)
from services.ass_subtitles import generate_ass_subtitles
from services.sfx_library import resolve_sfx_file_path
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


def _has_audio_stream(media_path: Path, ffmpeg_bin: str) -> bool:
    res = subprocess.run(
        [ffmpeg_bin, "-nostdin", "-i", media_path.resolve().as_posix()],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return "Audio:" in (res.stderr or "")


def _build_atempo_filter(speed: float) -> str:
    spd = max(0.25, min(4.0, float(speed or 1.0)))
    stages: List[str] = []
    remaining = spd
    while remaining > 2.0 + 1e-6:
        stages.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5 - 1e-6:
        stages.append("atempo=0.5")
        remaining /= 0.5
    stages.append(f"atempo={remaining:.4f}")
    return ",".join(stages)


def _aspect_target_dims(aspect_ratio: str) -> tuple[int, int]:
    if aspect_ratio == "9:16":
        return 1080, 1920
    if aspect_ratio == "1:1":
        return 1080, 1080
    return 1280, 720


def _build_zoom_vf_filters(
    zooms: List[ZoomEffectItem], out_w: int, out_h: int
) -> List[str]:
    valid_zooms = [z for z in zooms if z.end > z.start + 0.05]
    if not valid_zooms:
        return []

    scale_expr = "1"
    fx_expr = "0.5"
    fy_expr = "0.5"
    for z in valid_zooms:
        s_t = max(0.0, float(z.start))
        e_t = max(s_t + 0.05, float(z.end))
        span = e_t - s_t
        st_sc = max(1.0, min(3.0, float(z.startScale)))
        en_sc = max(1.0, min(3.0, float(z.endScale)))
        fx = max(0.0, min(1.0, float(z.focusX)))
        fy = max(0.0, min(1.0, float(z.focusY)))

        if z.kind == "punch_in" or z.easing == "instant":
            z_sc = f"{en_sc:.3f}"
        elif z.easing == "linear":
            z_sc = f"({st_sc:.3f}+({en_sc - st_sc:.3f})*(t-{s_t:.3f})/{span:.3f})"
        else:
            z_sc = f"({st_sc:.3f}+({en_sc - st_sc:.3f})*(0.5-0.5*cos(PI*(t-{s_t:.3f})/{span:.3f})))"

        scale_expr = f"if(between(t,{s_t:.3f},{e_t:.3f}),{z_sc},{scale_expr})"
        fx_expr = f"if(between(t,{s_t:.3f},{e_t:.3f}),{fx:.3f},{fx_expr})"
        fy_expr = f"if(between(t,{s_t:.3f},{e_t:.3f}),{fy:.3f},{fy_expr})"

    scaled_w = f"trunc(max({out_w},{out_w}*({scale_expr}))/2)*2"
    scaled_h = f"trunc(max({out_h},{out_h}*({scale_expr}))/2)*2"
    return [
        f"scale=w='{scaled_w}':h='{scaled_h}':eval=frame:flags=bicubic",
        f"crop={out_w}:{out_h}:'max(0,({scaled_w}-{out_w})*({fx_expr}))':'max(0,({scaled_h}-{out_h})*({fy_expr}))'",
    ]


def _build_vf_filter(
    aspect_ratio: str,
    ass_path: Path | None,
    work_dir: Path,
    zooms: List[ZoomEffectItem] | None = None,
    clip_speed: float = 1.0,
    crop: Optional[VideoCropRect] = None,
) -> str:
    filters: List[str] = []
    spd = max(0.25, min(4.0, float(clip_speed or 1.0)))
    if abs(spd - 1.0) > 0.01:
        filters.append(f"setpts=PTS/{spd:.6f}")

    out_w, out_h = _aspect_target_dims(aspect_ratio)
    scale_pad = (
        f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=bicubic,"
        f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:black"
    )

    timed_crop_applied = False
    if crop is not None:
        cx = max(0.0, min(0.9, float(crop.x)))
        cy = max(0.0, min(0.9, float(crop.y)))
        cw = max(0.1, min(1.0 - cx, float(crop.w)))
        ch = max(0.1, min(1.0 - cy, float(crop.h)))
        if cx > 0.002 or cy > 0.002 or cw < 0.998 or ch < 0.998:
            crop_expr = (
                f"crop=w='max(16,trunc(iw*{cw:.4f}/2)*2)':"
                f"h='max(16,trunc(ih*{ch:.4f}/2)*2)':"
                f"x='trunc(iw*{cx:.4f}/2)*2':"
                f"y='trunc(ih*{cy:.4f}/2)*2'"
            )
            if crop.start is not None and crop.end is not None and float(crop.end) > float(crop.start) + 0.05:
                cs = max(0.0, float(crop.start))
                ce = max(cs + 0.05, float(crop.end))
                filters.append(
                    f"split[_cbase][_ccrop];"
                    f"[_cbase]{scale_pad}[_vbase];"
                    f"[_ccrop]{crop_expr},{scale_pad}[_vcrop];"
                    f"[_vbase][_vcrop]overlay=0:0:eof_action=pass:enable='between(t,{cs:.3f},{ce:.3f})'"
                )
                timed_crop_applied = True
            else:
                filters.append(crop_expr)

    if not timed_crop_applied:
        # Fit entire video (or full-duration cropped region) cleanly inside target ratio without distorting
        filters.append(scale_pad)
    filters.append("setsar=1")

    if zooms:
        filters.extend(_build_zoom_vf_filters(zooms, out_w, out_h))

    if ass_path is not None:
        escaped_ass = _escape_ffmpeg_subtitles_path(ass_path, work_dir)
        filters.append(f"ass={escaped_ass}")

    return ",".join(filters)


def _build_audio_edits_af_filter(
    audio_edits: List[AudioEditItem],
    window_start: float = 0.0,
    window_duration: Optional[float] = None,
) -> str:
    """
    Builds an FFmpeg `volume='...':eval=frame` filter for original video audio edits
    (muted/cut sections or volume ducking/boosting) over timeline window `[window_start, window_end]`.
    Does not affect separately mixed SFX/TTS tracks.
    """
    if not audio_edits:
        return ""
    win_s = max(0.0, float(window_start))
    win_e = (
        win_s + max(0.0, float(window_duration))
        if window_duration is not None
        else None
    )
    expr = "1.000"
    changed = False
    for ae in audio_edits:
        raw_s = max(0.0, float(ae.start))
        raw_e = max(0.0, float(ae.end))
        if raw_e <= raw_s + 0.02:
            continue
        ov_s = max(win_s, raw_s)
        ov_e = min(win_e, raw_e) if win_e is not None else raw_e
        if ov_e <= ov_s + 0.02:
            continue
        loc_s = max(0.0, ov_s - win_s)
        loc_e = max(loc_s + 0.02, ov_e - win_s)
        vol = 0.0 if ae.muted else max(0.0, min(2.0, float(ae.volume)))
        if abs(vol - 1.0) <= 0.005:
            continue
        expr = f"if(between(t,{loc_s:.3f},{loc_e:.3f}),{vol:.3f},{expr})"
        changed = True
    return f"volume='{expr}':eval=frame" if changed else ""


def _collect_active_sfx(
    sfx_clips: List[SfxClipItem],
    sfx_dir: Path,
    window_start: float,
    window_duration: float,
) -> List[Dict[str, Any]]:
    window_end = window_start + max(0.0, window_duration)
    collected: List[Dict[str, Any]] = []
    for sc in sfx_clips:
        sc_start = max(0.0, float(sc.start))
        sc_dur = max(0.05, float(sc.duration))
        sc_trim = max(0.0, float(getattr(sc, "trimStart", 0.0) or 0.0))
        sc_end = sc_start + sc_dur
        if min(sc_end, window_end) <= max(sc_start, window_start) + 0.02:
            continue
        sfx_path = resolve_sfx_file_path(sfx_dir, sc.assetId, sc.url)
        if sfx_path is None:
            continue
        window_trim = max(0.0, window_start - sc_start)
        trim_start = sc_trim + window_trim
        local_start = max(0.0, sc_start - window_start)
        delay_ms = int(round(local_start * 1000.0))
        play_dur = max(0.05, min(window_duration - local_start, sc_dur - window_trim))
        volume = max(0.0, min(2.0, float(sc.volume)))
        collected.append(
            {
                "path": sfx_path,
                "trim_start": round(trim_start, 3),
                "delay_ms": delay_ms,
                "play_dur": round(play_dur, 3),
                "volume": round(volume, 3),
            }
        )
    return collected


def _build_sfx_filter_complex(
    vf_filter: str,
    active_sfx: List[Dict[str, Any]],
    has_base_audio: bool,
    base_audio_filter: str,
    total_duration: float,
) -> tuple[List[str], str]:
    extra_inputs: List[str] = []
    parts: List[str] = []

    vf_chain = vf_filter.strip() or "null"
    parts.append(f"[0:v]{vf_chain}[vout]")

    if has_base_audio:
        base_af = f"{base_audio_filter}," if base_audio_filter.strip() else ""
        parts.append(
            f"[0:a]{base_af}aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[basea]"
        )
    else:
        parts.append(
            f"anullsrc=r=44100:cl=stereo:d={max(0.5, total_duration):.3f}[basea]"
        )

    for idx, item in enumerate(active_sfx, start=1):
        extra_inputs.extend(["-i", item["path"].as_posix()])
        parts.append(
            f"[{idx}:a]atrim=start={item['trim_start']:.3f}:duration={item['play_dur']:.3f},"
            f"asetpts=PTS-STARTPTS,"
            f"aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            f"volume={item['volume']:.3f},"
            f"adelay={item['delay_ms']}|{item['delay_ms']}[sfx{idx}]"
        )

    mix_labels = "[basea]" + "".join(f"[sfx{i}]" for i in range(1, len(active_sfx) + 1))
    parts.append(
        f"{mix_labels}amix=inputs={len(active_sfx) + 1}:duration=first:dropout_transition=0:normalize=0[aout]"
    )
    return extra_inputs, ";".join(parts)


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
        code = proc.wait()
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        raise
    finally:
        stderr_thread.join()

    stderr_out = "".join(stderr_chunks)
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
    raw_pid = str(project_state.projectId or "").strip()
    safe_pid = re.sub(r"[^a-zA-Z0-9_-]", "", raw_pid)
    if not raw_pid or safe_pid != raw_pid:
        raise ValueError("Invalid projectId.")
    downloads_dir = (workspace_dir / "downloads").resolve()
    source_mp4 = (downloads_dir / f"{safe_pid}.mp4").resolve()
    if not source_mp4.is_relative_to(downloads_dir):
        raise ValueError("Invalid source video path.")
    if not source_mp4.exists():
        raise FileNotFoundError(f"Source MP4 not found at {source_mp4}")

    exports_dir = (workspace_dir / "exports").resolve()
    exports_dir.mkdir(parents=True, exist_ok=True)

    job_id = uuid.uuid4().hex[:8]
    work_dir = (exports_dir / f"_tmp_{safe_pid}_{job_id}").resolve()
    if not work_dir.is_relative_to(exports_dir):
        raise ValueError("Invalid export work directory.")
    work_dir.mkdir(parents=True, exist_ok=True)

    clips = project_state.tracks.clips
    if not clips:
        raise ValueError("Cannot export an empty timeline with 0 clips.")

    ratio = project_state.workspaceState.targetAspectRatio
    has_captions = len(project_state.tracks.captions) > 0
    has_overlay = bool((project_state.workspaceState.overlayText or "").strip())
    has_drawings = len(project_state.tracks.drawings) > 0
    needs_ass = has_captions or has_overlay or has_drawings
    has_audio = _has_audio_stream(source_mp4, ffmpeg_bin)
    sfx_dir = (workspace_dir / "sfx").resolve()
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
                clip_speed = max(0.25, min(4.0, float(getattr(clip, "speed", 1.0) or 1.0)))
                raw_span = max(0.1, clip.sourceEnd - clip.sourceStart)
                standalone_dur = round(raw_span / clip_speed, 3)
                standalone_clip = ClipItem(
                    id=clip.id,
                    title=clip.title,
                    sourceStart=clip.sourceStart,
                    sourceEnd=clip.sourceEnd,
                    timelineStart=0.0,
                    duration=standalone_dur,
                    speed=clip_speed,
                )
                local_zooms = [
                    ZoomEffectItem(
                        id=z.id,
                        start=max(0.0, z.start - clip.timelineStart),
                        end=min(standalone_dur, z.end - clip.timelineStart),
                        kind=z.kind,
                        startScale=z.startScale,
                        endScale=z.endScale,
                        focusX=z.focusX,
                        focusY=z.focusY,
                        easing=z.easing,
                    )
                    for z in project_state.tracks.zooms
                    if min(clip.timelineStart + standalone_dur, z.end)
                    > max(clip.timelineStart, z.start) + 0.05
                ]
                ass_path: Path | None = None
                if needs_ass:
                    ass_path = work_dir / f"clip_{idx}.ass"
                    generate_ass_subtitles(
                        project_state,
                        ass_path,
                        clips_override=[standalone_clip],
                        timeline_offset=clip.timelineStart,
                    )

                raw_crop = project_state.workspaceState.crop
                local_crop = raw_crop
                if (
                    raw_crop is not None
                    and raw_crop.start is not None
                    and raw_crop.end is not None
                ):
                    ov_s = max(clip.timelineStart, float(raw_crop.start))
                    ov_e = min(clip.timelineStart + standalone_dur, float(raw_crop.end))
                    if ov_e > ov_s + 0.05:
                        local_crop = VideoCropRect(
                            x=raw_crop.x,
                            y=raw_crop.y,
                            w=raw_crop.w,
                            h=raw_crop.h,
                            start=round(ov_s - clip.timelineStart, 3),
                            end=round(ov_e - clip.timelineStart, 3),
                        )
                    else:
                        local_crop = None

                vf_filter = _build_vf_filter(
                    ratio,
                    ass_path,
                    work_dir,
                    zooms=local_zooms,
                    clip_speed=clip_speed,
                    crop=local_crop,
                )
                local_sfx = _collect_active_sfx(
                    project_state.tracks.sfx, sfx_dir, clip.timelineStart, standalone_dur
                )
                safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "_", clip.title).strip("_") or f"clip_{idx + 1}"
                out_name = f"{safe_pid}_{idx + 1}_{safe_title}_{job_id}.mp4"
                out_path = (exports_dir / out_name).resolve()
                if not out_path.is_relative_to(exports_dir):
                    raise ValueError("Invalid export output path.")

                clip_src_start = max(0.0, float(clip.sourceStart))
                cmd = [
                    ffmpeg_bin,
                    "-nostdin",
                    "-y",
                    "-ss",
                    f"{clip_src_start:.3f}",
                    "-t",
                    f"{raw_span:.3f}",
                    "-i",
                    source_mp4.as_posix(),
                ]
                clip_audio_af = _build_audio_edits_af_filter(
                    project_state.tracks.audioEdits,
                    window_start=clip.timelineStart,
                    window_duration=standalone_dur,
                )
                af_parts: List[str] = []
                if has_audio and abs(clip_speed - 1.0) > 0.01:
                    af_parts.append(_build_atempo_filter(clip_speed))
                if has_audio and clip_audio_af:
                    af_parts.append(clip_audio_af)
                base_af = ",".join(af_parts)
                if local_sfx:
                    extra_in, fc = _build_sfx_filter_complex(
                        vf_filter, local_sfx, has_audio, base_af, standalone_dur
                    )
                    cmd.extend(extra_in)
                    cmd.extend(["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"])
                else:
                    if vf_filter:
                        cmd.extend(["-vf", vf_filter])
                    if base_af:
                        cmd.extend(["-af", base_af])
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
            clip_speed = max(0.25, min(4.0, float(getattr(clip, "speed", 1.0) or 1.0)))
            clip_src_start = max(0.0, float(clip.sourceStart))
            clip_raw_span = max(0.1, float(clip.sourceEnd) - clip_src_start)
            cut_cmd = [
                ffmpeg_bin,
                "-nostdin",
                "-y",
                "-ss",
                f"{clip_src_start:.3f}",
                "-t",
                f"{clip_raw_span:.3f}",
                "-i",
                source_mp4.as_posix(),
            ]
            if abs(clip_speed - 1.0) > 0.01:
                cut_cmd.extend(["-vf", f"setpts=PTS/{clip_speed:.6f}"])
                if has_audio:
                    cut_cmd.extend(["-af", _build_atempo_filter(clip_speed)])
            cut_cmd.extend(
                [
                    "-r",
                    "30",
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
                    "-ar",
                    "44100",
                    "-ac",
                    "2",
                    "-b:a",
                    "128k",
                    "-avoid_negative_ts",
                    "make_zero",
                    seg_path.as_posix(),
                ]
            )
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
        if needs_ass:
            emit(68, "Generating styled .ass subtitles...")
            ass_path = work_dir / "captions.ass"
            generate_ass_subtitles(project_state, ass_path)

        vf_filter = _build_vf_filter(
            ratio,
            ass_path,
            work_dir,
            zooms=project_state.tracks.zooms,
            crop=project_state.workspaceState.crop,
        )
        out_name = f"{safe_pid}_final_{ratio.replace(':', 'x')}_{job_id}.mp4"
        final_out = (exports_dir / out_name).resolve()
        if not final_out.is_relative_to(exports_dir):
            raise ValueError("Invalid final export path.")
        total_tl_duration = sum(c.duration for c in clips)
        active_sfx = _collect_active_sfx(
            project_state.tracks.sfx, sfx_dir, 0.0, total_tl_duration
        )
        merged_audio_af = _build_audio_edits_af_filter(
            project_state.tracks.audioEdits,
            window_start=0.0,
            window_duration=total_tl_duration,
        )

        if not vf_filter and not active_sfx and not merged_audio_af:
            emit(90, "Finalizing MP4 container...")
            shutil.move(str(merged_raw), str(final_out))
        elif active_sfx:
            emit(72, f"Encoding {ratio}, burning overlays & mixing {len(active_sfx)} SFX...")
            merged_has_audio = _has_audio_stream(merged_raw, ffmpeg_bin)
            extra_in, fc = _build_sfx_filter_complex(
                vf_filter, active_sfx, merged_has_audio, merged_audio_af, total_tl_duration
            )
            burn_cmd = [
                ffmpeg_bin,
                "-nostdin",
                "-y",
                "-i",
                merged_raw.name,
                *extra_in,
                "-filter_complex",
                fc,
                "-map",
                "[vout]",
                "-map",
                "[aout]",
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
                f"Encoding {ratio} & mixing SFX...",
            )
        else:
            emit(72, f"Encoding {ratio} & burning captions (-threads 0 -preset ultrafast)...")
            merged_has_audio = (
                _has_audio_stream(merged_raw, ffmpeg_bin) if merged_audio_af else False
            )
            burn_cmd = [
                ffmpeg_bin,
                "-nostdin",
                "-y",
                "-i",
                merged_raw.name,
            ]
            if vf_filter:
                burn_cmd.extend(["-vf", vf_filter])
            if merged_audio_af and merged_has_audio:
                burn_cmd.extend(["-af", merged_audio_af, "-c:a", "aac", "-b:a", "128k"])
            else:
                burn_cmd.extend(["-c:a", "copy"])
            burn_cmd.extend(
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
                    "-movflags",
                    "+faststart",
                    "-progress",
                    "pipe:1",
                    "-nostats",
                    final_out.as_posix(),
                ]
            )
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
