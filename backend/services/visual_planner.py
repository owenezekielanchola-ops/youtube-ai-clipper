import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

from schemas import ProjectState, SfxAsset, ToolCallSchema, VideoCropRect
from services.model_registry import get_gemini_models_chain, report_model_failure
from services.sfx_library import find_best_matching_sfx
from services.youtube_ingest import _find_ffmpeg

VISUAL_PLANNER_PHRASES = (
    "creator",
    "streamer",
    "person",
    "facecam",
    "webcam",
    "face cam",
    "reaction",
    "react",
    "reacts",
    "reacting",
    "laugh",
    "laughs",
    "laughing",
    "smile",
    "smiling",
    "shocked",
    "surprised",
    "confused",
    "angry",
    "expression",
    "face",
    "pointing",
    "point to",
    "point at",
    "zoom to",
    "zoom into",
    "zoom onto",
    "zoom on",
    "focus on",
    "on screen",
    "on the screen",
    "in the video",
    "look at",
    "looking at",
    "what happens",
    "what is happening",
    "describe",
    "gameplay",
    "character",
    "chat",
    "scoreboard",
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
    "top left",
    "top right",
    "bottom left",
    "bottom right",
    "left side",
    "right side",
    "circle the",
    "arrow to",
    "arrow at",
    "box around",
    "highlight the",
    "crop",
    "crop to",
    "crop the",
    "crop on",
    "crop into",
    "crop out",
    "remove the zoom",
    "replace the zoom",
    "funny part",
    "funny moment",
    "funniest moment",
    "what are they doing",
    "what he is doing",
    "what she is doing",
    "what's happening",
    "whats happening",
    "list what happens",
    "story beats",
    "that guy",
    "this guy",
    "that girl",
    "sound effect",
    "sound effects",
    "sfx",
    "audio effect",
    "audio effects",
    "applicable",
    "fitting sound",
    "matching sound",
    "meme sound",
    "funny sound",
)


def should_use_visual_planner(prompt: str, project_state: Optional[ProjectState] = None) -> bool:
    """
    Checks whether the user's timeline edit prompt asks about visual subjects,
    on-screen positions, facial/body reactions, or screen objects that require
    Gemini multimodal video inspection before Groq calls timeline tools.
    """
    if project_state is not None and not project_state.tracks.clips:
        return False
    lowered = f" {prompt.lower().strip()} "
    if re.search(r"\b(remove|delete|clear|trim|shorten)\b.{0,35}\b(sfx|sound\s+effects?|audio\s+effects?)\b", lowered):
        also_visual_or_add = any(
            k in lowered
            for k in ("add", "replace", "crop", "zoom", "draw", "arrow", "circle", "describe", "what happens")
        )
        if not also_visual_or_add:
            return False
    if any(phrase in lowered for phrase in VISUAL_PLANNER_PHRASES):
        return True
    # "draw/circle/arrow/zoom/crop" combined with spatial prepositions targeting a visual noun
    if re.search(
        r"\b(zoom|crop|draw|arrow|circle|box|highlight|focus)\b.*?\b(at|on|to|onto|around|over|showing)\s+(the|this|that|his|her)\b",
        lowered,
    ):
        return True
    return False


def _proxy_target_dims(aspect_ratio: str) -> Tuple[int, int]:
    if aspect_ratio == "9:16":
        return 270, 480
    if aspect_ratio == "1:1":
        return 420, 420
    return 480, 270


def _build_timeline_proxy_video(
    project_state: ProjectState,
    source_mp4: Path,
    work_dir: Path,
    include_existing_crop: bool = True,
) -> Tuple[Optional[Path], float]:
    """
    Renders a lightweight silent proxy MP4 representing the user's exact active timeline
    (clips trimmed, reordered, speed-scaled, optional existing crop, and fit into targetAspectRatio)
    so Gemini timestamps & normalized screen coordinates match the user's editor 1:1.
    """
    clips = project_state.tracks.clips
    if not clips or not source_mp4.is_file():
        return None, 0.0

    ffmpeg_bin = _find_ffmpeg()
    pw, ph = _proxy_target_dims(project_state.workspaceState.targetAspectRatio)
    total_tl_dur = sum(max(0.1, float(c.duration)) for c in clips)

    crop_filter = ""
    crop: Optional[VideoCropRect] = project_state.workspaceState.crop if include_existing_crop else None
    if crop is not None:
        cx = max(0.0, min(0.9, float(crop.x)))
        cy = max(0.0, min(0.9, float(crop.y)))
        cw = max(0.1, min(1.0 - cx, float(crop.w)))
        ch = max(0.1, min(1.0 - cy, float(crop.h)))
        if cx > 0.002 or cy > 0.002 or cw < 0.998 or ch < 0.998:
            crop_filter = (
                f"crop=w='max(16,trunc(iw*{cw:.4f}/2)*2)':"
                f"h='max(16,trunc(ih*{ch:.4f}/2)*2)':"
                f"x='trunc(iw*{cx:.4f}/2)*2':"
                f"y='trunc(ih*{cy:.4f}/2)*2',"
            )

    seg_paths: List[Path] = []
    for idx, clip in enumerate(clips):
        seg_out = work_dir / f"proxy_seg_{idx:03d}.mp4"
        clip_speed = max(0.25, min(4.0, float(getattr(clip, "speed", 1.0) or 1.0)))
        raw_span = max(0.1, float(clip.sourceEnd) - float(clip.sourceStart))
        vf = (
            f"setpts=PTS/{clip_speed:.6f},"
            f"{crop_filter}"
            f"fps=6,"
            f"scale={pw}:{ph}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=fast_bilinear,"
            f"pad={pw}:{ph}:(ow-iw)/2:(oh-ih)/2:black,"
            f"setsar=1"
        )
        cmd = [
            ffmpeg_bin,
            "-nostdin",
            "-y",
            "-ss",
            f"{clip.sourceStart:.3f}",
            "-t",
            f"{raw_span:.3f}",
            "-i",
            source_mp4.resolve().as_posix(),
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            seg_out.as_posix(),
        ]
        res = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if res.returncode != 0 or not seg_out.is_file():
            return None, total_tl_dur
        seg_paths.append(seg_out)

    final_proxy = work_dir / "timeline_proxy.mp4"
    if len(seg_paths) == 1:
        shutil.copy2(seg_paths[0], final_proxy)
        return final_proxy, round(total_tl_dur, 3)

    concat_list = work_dir / "proxy_list.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.name}'" for p in seg_paths) + "\n", encoding="utf-8"
    )
    concat_res = subprocess.run(
        [
            ffmpeg_bin,
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_list.name,
            "-c",
            "copy",
            final_proxy.as_posix(),
        ],
        cwd=str(work_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if concat_res.returncode != 0 or not final_proxy.is_file():
        return None, total_tl_dur
    return final_proxy, round(total_tl_dur, 3)


def _extract_timeline_keyframes(
    proxy_mp4: Path, work_dir: Path, total_tl_dur: float, max_frames: int = 10
) -> List[Tuple[float, bytes]]:
    if not proxy_mp4.is_file() or total_tl_dur <= 0:
        return []
    ffmpeg_bin = _find_ffmpeg()
    frames_dir = work_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    fps = max(0.15, min(2.0, max_frames / max(1.0, total_tl_dur)))
    cmd = [
        ffmpeg_bin,
        "-nostdin",
        "-y",
        "-i",
        proxy_mp4.as_posix(),
        "-vf",
        f"fps={fps:.4f}",
        "-frames:v",
        str(max_frames),
        "-q:v",
        "6",
        (frames_dir / "f_%03d.jpg").as_posix(),
    ]
    res = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if res.returncode != 0:
        return []
    files = sorted(frames_dir.glob("f_*.jpg"))
    out: List[Tuple[float, bytes]] = []
    step = total_tl_dur / max(1, len(files))
    for i, fp in enumerate(files):
        ts = round(min(total_tl_dur, (i + 0.5) * step), 2)
        out.append((ts, fp.read_bytes()))
    return out


def _strip_json_dict(raw_text: str) -> Dict[str, Any]:
    text = (raw_text or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n?```\s*$", "", text)
    if not text.startswith("{"):
        s_idx = text.find("{")
        e_idx = text.rfind("}")
        if s_idx != -1 and e_idx > s_idx:
            text = text[s_idx : e_idx + 1]
    parsed = json.loads(text)
    return parsed if isinstance(parsed, dict) else {}


def _clamp(val: Any, lo: float, hi: float, default: float) -> float:
    try:
        return round(max(lo, min(hi, float(val))), 3)
    except (TypeError, ValueError):
        return default


def _sanitize_visual_plan(raw: Dict[str, Any], total_tl_dur: float) -> Dict[str, Any]:
    max_t = max(0.5, float(total_tl_dur))
    plan: Dict[str, Any] = {
        "scene_summary": str(raw.get("scene_summary") or "Analyzed active timeline video.")[:500],
        "subjects": [],
        "detected_moments": [],
        "planned_steps": [],
        "recommended_sfx_moods": [],
    }

    for sub in raw.get("subjects") or []:
        if not isinstance(sub, dict):
            continue
        sub_entry: Dict[str, Any] = {
            "label": str(sub.get("label") or "subject")[:80],
            "focus_x": _clamp(sub.get("focus_x"), 0.0, 1.0, 0.5),
            "focus_y": _clamp(sub.get("focus_y"), 0.0, 1.0, 0.5),
        }
        if isinstance(sub.get("box"), dict):
            bx = _clamp(sub["box"].get("x"), 0.0, 0.9, 0.0)
            by = _clamp(sub["box"].get("y"), 0.0, 0.9, 0.0)
            bw = _clamp(sub["box"].get("w"), 0.1, 1.0 - bx, 1.0 - bx)
            bh = _clamp(sub["box"].get("h"), 0.1, 1.0 - by, 1.0 - by)
            sub_entry["box"] = {"x": bx, "y": by, "w": bw, "h": bh}
        plan["subjects"].append(sub_entry)

    valid_mtypes = {"hook", "funny", "reaction", "action", "buildup", "punchline", "highlight"}
    for mom in raw.get("detected_moments") or []:
        if not isinstance(mom, dict):
            continue
        ms = _clamp(mom.get("start"), 0.0, max_t - 0.1, 0.0)
        me = _clamp(mom.get("end"), ms + 0.3, max_t, min(max_t, ms + 2.5))
        mtype = str(mom.get("moment_type") or "highlight").strip().lower()
        if mtype not in valid_mtypes:
            mtype = "highlight"
        plan["detected_moments"].append(
            {
                "start": ms,
                "end": me,
                "moment_type": mtype,
                "description": str(mom.get("description") or mom.get("what_they_are_doing") or "")[:240],
                "target_subject": str(mom.get("target_subject") or "")[:80],
            }
        )

    allowed_actions = {
        "add_zoom_effect",
        "delete_zoom_effect",
        "add_drawing",
        "delete_drawing",
        "add_sfx_clip",
        "delete_sfx_clip",
        "set_clip_speed",
        "set_video_crop",
        "remove_time_ranges",
        "change_caption_style",
        "change_video_format",
    }
    for step in raw.get("planned_steps") or []:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "").strip()
        if action not in allowed_actions:
            continue
        params = dict(step.get("params") or {})
        if action == "add_zoom_effect":
            zs = _clamp(params.get("start"), 0.0, max_t - 0.25, 0.0)
            ze = _clamp(params.get("end"), zs + 0.3, max_t, min(max_t, zs + 2.2))
            kind = str(params.get("kind") or "punch_in")
            if kind not in {"punch_in", "zoom_in", "zoom_out"}:
                kind = "punch_in"
            params = {
                "start": zs,
                "end": ze,
                "kind": kind,
                "start_scale": _clamp(
                    params.get("start_scale"), 1.0, 3.0, 1.45 if kind == "zoom_out" else 1.0
                ),
                "end_scale": _clamp(
                    params.get("end_scale"), 1.0, 3.0, 1.0 if kind == "zoom_out" else 1.65
                ),
                "focus_x": _clamp(params.get("focus_x"), 0.0, 1.0, 0.5),
                "focus_y": _clamp(params.get("focus_y"), 0.0, 1.0, 0.5),
                "easing": (
                    str(params.get("easing"))
                    if params.get("easing") in {"instant", "linear", "ease_in_out"}
                    else ("instant" if kind == "punch_in" else "ease_in_out")
                ),
            }
        elif action == "add_drawing":
            ds = _clamp(params.get("start"), 0.0, max_t - 0.25, 0.0)
            de = _clamp(params.get("end"), ds + 0.3, max_t, min(max_t, ds + 2.5))
            d_tool = str(params.get("tool") or "arrow")
            if d_tool not in {"freehand", "line", "arrow", "circle", "rect", "highlight"}:
                d_tool = "arrow"
            raw_pts = params.get("points") if isinstance(params.get("points"), list) else []
            norm_pts = [
                {
                    "x": _clamp(pt.get("x"), 0.0, 1.0, 0.5),
                    "y": _clamp(pt.get("y"), 0.0, 1.0, 0.5),
                }
                for pt in raw_pts
                if isinstance(pt, dict)
            ]
            if len(norm_pts) < 2:
                norm_pts = [{"x": 0.24, "y": 0.26}, {"x": 0.5, "y": 0.48}]
            params = {
                "start": ds,
                "end": de,
                "tool": d_tool,
                "points": norm_pts,
                "color": str(params.get("color") or "#EF4444"),
                "stroke_width": int(_clamp(params.get("stroke_width"), 2, 24, 6)),
                "opacity": _clamp(params.get("opacity"), 0.1, 1.0, 1.0),
            }
        elif action == "add_sfx_clip":
            asset_id_val = str(params.get("asset_id") or "").strip()
            params = {
                "start": _clamp(params.get("start"), 0.0, max_t, 0.0),
                "sfx_name": str(params.get("sfx_name") or params.get("mood") or "sfx")[:60],
                "volume": _clamp(params.get("volume"), 0.1, 2.0, 0.85),
                **({"asset_id": asset_id_val} if asset_id_val else {}),
            }
        elif action == "set_clip_speed":
            if params.get("start_time") is not None and params.get("end_time") is not None:
                ss = _clamp(params.get("start_time"), 0.0, max_t - 0.2, 0.0)
                se = _clamp(params.get("end_time"), ss + 0.2, max_t, min(max_t, ss + 3.0))
                params = {
                    "start_time": ss,
                    "end_time": se,
                    "speed": _clamp(params.get("speed"), 0.25, 4.0, 1.5),
                }
            else:
                params = {
                    "speed": _clamp(params.get("speed"), 0.25, 4.0, 1.5),
                    **(
                        {"clip_id": str(params["clip_id"])}
                        if params.get("clip_id")
                        else {}
                    ),
                }
        elif action == "delete_zoom_effect":
            params = {"zoom_id": str(params.get("zoom_id") or "").strip()}
        elif action == "set_video_crop":
            cx = _clamp(params.get("x"), 0.0, 0.9, 0.0)
            cy = _clamp(params.get("y"), 0.0, 0.9, 0.0)
            cw = _clamp(params.get("w"), 0.1, 1.0 - cx, 1.0 - cx)
            ch = _clamp(params.get("h"), 0.1, 1.0 - cy, 1.0 - cy)
            raw_start = params.get("start")
            raw_end = params.get("end")
            params = {"x": cx, "y": cy, "w": cw, "h": ch}
            if raw_start is not None or raw_end is not None:
                cs = _clamp(raw_start, 0.0, max_t - 0.25, 0.0)
                ce = _clamp(raw_end, cs + 0.3, max_t, min(max_t, cs + 3.5))
                params["start"] = cs
                params["end"] = ce

        plan["planned_steps"].append(
            {
                "action": action,
                "reason": str(step.get("reason") or "")[:180],
                "params": params,
            }
        )

    moods = raw.get("recommended_sfx_moods") or []
    if isinstance(moods, list):
        plan["recommended_sfx_moods"] = [str(m).strip().lower()[:30] for m in moods if str(m).strip()][:5]

    return plan


def convert_visual_plan_to_tool_calls(
    plan: Dict[str, Any], sfx_assets: Optional[List[SfxAsset]] = None
) -> List[ToolCallSchema]:
    """Converts Gemini's structured planned_steps directly into ToolCallSchema (used as backup when Groq is unavailable)."""
    assets = sfx_assets or []
    calls: List[ToolCallSchema] = []
    for step in plan.get("planned_steps") or []:
        action = str(step.get("action") or "")
        params = dict(step.get("params") or {})
        if not action:
            continue
        if action == "add_sfx_clip":
            asset_id_hint = str(params.get("asset_id") or "").strip()
            chosen = next((a for a in assets if a.id == asset_id_hint), None) if assets and asset_id_hint else None
            if chosen is None and assets:
                sfx_query = f"{params.get('sfx_name') or ''} {step.get('reason') or ''}".strip()
                chosen = find_best_matching_sfx(assets, sfx_query)
            if chosen is None:
                continue
            params["asset_id"] = chosen.id
            params["sfx_name"] = chosen.name
            params["name"] = chosen.name
            params["url"] = chosen.url
            params["duration"] = chosen.duration
        calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name=action,
                arguments=params,
                reason=str(step.get("reason") or "") or None,
            )
        )
    return calls


def plan_visual_edits_with_gemini(
    prompt: str,
    project_state: ProjectState,
    transcript_tl_text: str,
    downloads_dir: Path,
    sfx_assets: Optional[List[SfxAsset]] = None,
) -> Optional[Tuple[Dict[str, Any], str]]:
    """
    Stage 1 (Gemini Visual Planner):
    Inspects the user's actual edited timeline video (local proxy MP4 matching
    current clips/speeds/aspect ratio, with keyframe fallback) and produces a
    structured visual plan with exact normalized coordinates & timeline timestamps
    for Stage 2 (Groq Tool Executor).
    """
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key or gemini_key == "your_gemini_api_key_here":
        return None

    source_mp4 = (downloads_dir / f"{project_state.projectId}.mp4").resolve()
    if not source_mp4.is_file():
        return None

    work_dir = Path(tempfile.mkdtemp(prefix="visplan_"))
    client = genai.Client(api_key=gemini_key)
    uploaded_file = None

    try:
        wants_crop = "crop" in prompt.lower()
        print("[VisualPlanner] Building timeline proxy...", flush=True)
        proxy_mp4, total_tl_dur = _build_timeline_proxy_video(
            project_state,
            source_mp4,
            work_dir,
            include_existing_crop=not wants_crop,
        )
        if proxy_mp4 is None or total_tl_dur <= 0:
            return None

        sfx_summary = [
            {
                "id": a.id,
                "name": a.name,
                "tags": a.tags,
                "description": a.description,
                "duration": a.duration,
            }
            for a in (sfx_assets or [])
        ]

        planner_instruction = (
            "You are Stage 1 (Gemini Visual Planner) for an AI video editor.\n"
            "Stage 2 (Groq Tool Executor) CANNOT see images or video — it relies completely on YOUR visual observations.\n"
            "Watch the attached video clip (whose timeline starts at 0.00s and ends at "
            f"{total_tl_dur:.2f}s in exact 1:1 Timeline Seconds matching the editor) and inspect the user's request.\n\n"
            "COORDINATE & PLANNING RULES:\n"
            "- Screen coordinates are normalized 0.0 to 1.0: x=0.0 left edge, x=0.5 center, x=1.0 right edge; "
            "y=0.0 top edge, y=0.5 center, y=1.0 bottom edge.\n"
            "- Always identify visible subjects (e.g. creator webcam, gameplay character, chat overlay) with `focus_x`, `focus_y`, and bounding `box: {x, y, w, h}`.\n"
            "- Always produce `detected_moments` listing chronologically WHAT THEY ARE DOING and any FUNNY / REACTION / ACTION / PUNCHLINE / AWKWARD / ROAST / WIN moments (including silent facial reactions).\n"
            "- CRITICAL CROP vs ZOOM RULE: If the user asks to CROP (e.g. 'crop to creator face', 'replace the zoom with crop', 'crop showing reaction'), you MUST use `set_video_crop` — NEVER use `add_zoom_effect` when the user asked for a crop!\n"
            "- `set_video_crop` takes `{x, y, w, h}` (normalized 0.0..1.0 bounding box around the requested subject/face) PLUS optional `start` and `end` (in Timeline Seconds `TL`) when the user asks to crop at a specific moment/funny part or 'only for a while'.\n"
            "- If the user asks to REMOVE or REPLACE an existing zoom, include a `delete_zoom_effect` step with its `zoom_id` from `Active Zoom Effects`.\n"
            "- SMART SFX PLACEMENT RULE: If the user asks to add sound effects applicable/fitting to the video (e.g. 'add sound effects that is applicable for the video', 'add fitting sfx'), watch the ENTIRE clip and pick 2 to 6 key moments (funny/goofy slip, shocked face/punchline, savage roast/doom, awkward fail, boring/quiet beat, wholesome/win, smooth/rizz moment, or quick zoom/transition). Match EACH moment to the best sound in `Available Uploaded SFX Library` using its exact `id`, `name`, `tags`, and `description`. Space SFX at least ~2.0s apart, avoid repeating the exact same sound unnecessarily across different moods, and emit one `add_sfx_clip` step per beat with `{start, asset_id, sfx_name, volume}` (volume typically 0.75 to 0.9).\n"
            "- Output VALID JSON ONLY matching this schema:\n"
            "{\n"
            '  "scene_summary": "Brief description of visual layout and overall funny/action story",\n'
            '  "subjects": [{"label": "creator facecam", "focus_x": 0.18, "focus_y": 0.22, "box": {"x": 0.04, "y": 0.05, "w": 0.28, "h": 0.34}}],\n'
            '  "detected_moments": [\n'
            '    {"start": 4.2, "end": 6.8, "moment_type": "funny" | "reaction" | "hook" | "action" | "buildup" | "punchline" | "highlight", "description": "What they are doing / why this part is funny or key", "target_subject": "creator facecam"}\n'
            "  ],\n"
            '  "planned_steps": [\n'
            '    {\n'
            '      "action": "add_zoom_effect" | "delete_zoom_effect" | "add_drawing" | "add_sfx_clip" | "set_clip_speed" | "set_video_crop",\n'
            '      "reason": "Why & where on screen",\n'
            '      "params": {\n'
            '        // for add_zoom_effect: start, end, kind ("punch_in"|"zoom_in"|"zoom_out"), start_scale, end_scale, focus_x, focus_y, easing\n'
            '        // for delete_zoom_effect: zoom_id\n'
            '        // for add_drawing: start, end, tool ("arrow"|"circle"|"rect"|"line"|"highlight"), points: [{"x":..,"y":..},{"x":..,"y":..}], color, stroke_width\n'
            '        // for add_sfx_clip: start, asset_id, sfx_name, volume\n'
            '        // for set_clip_speed: start_time, end_time, speed\n'
            '        // for set_video_crop: x, y, w, h (0.0 to 1.0), plus optional start, end (timeline seconds if cropping only for a while / at a specific moment)\n'
            "      }\n"
            "    }\n"
            "  ],\n"
            '  "recommended_sfx_moods": ["whoosh", "laugh", "boom"]\n'
            "}\n"
        )

        active_zooms_summary = [
            z.model_dump() for z in (project_state.tracks.zooms or [])[:15]
        ]
        prompt_context = (
            f"User Prompt: {prompt}\n"
            f"Target Aspect Ratio: {project_state.workspaceState.targetAspectRatio}\n"
            f"Total Timeline Duration: {total_tl_dur:.2f}s\n"
            f"Current Playhead (TL): {project_state.workspaceState.playheadPosition:.2f}s\n"
            f"Active Zoom Effects on Timeline: {json.dumps(active_zooms_summary)}\n"
            f"Available Uploaded SFX Library: {json.dumps(sfx_summary)}\n"
            f"Timeline Transcript (TL sec):\n{transcript_tl_text[:3500]}\n"
        )

        # Try full proxy video upload via Gemini Files API first (gives full motion & reaction timing)
        media_parts: List[Any] = []
        try:
            print("[VisualPlanner] Uploading proxy to Gemini...", flush=True)
            uploaded_file = client.files.upload(file=str(proxy_mp4))
            waited = 0.0
            while waited < 10.0:
                state_str = str(getattr(uploaded_file, "state", "") or "").upper()
                if "PROCESSING" not in state_str:
                    break
                time.sleep(1.0)
                waited += 1.0
                if getattr(uploaded_file, "name", None):
                    uploaded_file = client.files.get(name=uploaded_file.name)
            state_final = str(getattr(uploaded_file, "state", "") or "").upper()
            if "FAILED" not in state_final and "PROCESSING" not in state_final:
                media_parts.append(uploaded_file)
        except Exception:
            uploaded_file = None

        # Fallback: sampled JPEG frames labeled with exact TL timestamps
        if not media_parts:
            print("[VisualPlanner] Falling back to keyframe images...", flush=True)
            keyframes = _extract_timeline_keyframes(proxy_mp4, work_dir, total_tl_dur, max_frames=10)
            if not keyframes:
                return None
            for ts, jpg_bytes in keyframes:
                media_parts.append(f"[Frame at Timeline TL={ts:.2f}s]")
                media_parts.append(types.Part.from_bytes(data=jpg_bytes, mime_type="image/jpeg"))

        contents: List[Any] = [*media_parts, prompt_context]

        quota_failures = 0
        for model_id in get_gemini_models_chain()[:3]:
            try:
                print(f"[VisualPlanner] Inspecting clip with {model_id}...", flush=True)
                resp = client.models.generate_content(
                    model=model_id,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=planner_instruction,
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )
                raw_dict = _strip_json_dict(resp.text or "{}")
                sanitized = _sanitize_visual_plan(raw_dict, total_tl_dur)
                return sanitized, model_id
            except Exception as exc:
                print(f"[VisualPlanner] {model_id} failed: {str(exc)[:140]}", flush=True)
                report_model_failure("gemini", model_id, exc)
                low = str(exc).lower()
                if any(k in low for k in ("429", "resource_exhausted", "quota", "rate_limit", "rate limit")):
                    quota_failures += 1
                    if quota_failures >= 2:
                        break
                continue

        return None
    finally:
        if uploaded_file is not None and getattr(uploaded_file, "name", None):
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass
        shutil.rmtree(work_dir, ignore_errors=True)


def analyze_user_clip_scenes_for_reference(
    project_state: ProjectState,
    transcript_tl_text: str,
    downloads_dir: Path,
    sfx_assets: Optional[List[SfxAsset]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Runs Gemini Visual Planner on the user's own active timeline clip to list
    chronological story/funny/reaction moments ('what they are doing') and exact
    subject coordinates so Reference Style Cloning maps reference edits onto
    the actual matching events in the user's clip.
    """
    scene_prompt = (
        "Break down what they are doing chronologically in THIS clip: list every hook, "
        "buildup, funny moment, creator facial reaction, key action, and punchline with exact "
        "TL timestamps, and identify exact (focus_x, focus_y) and bounding box for the main "
        "creator/webcam/subject on screen."
    )
    res = plan_visual_edits_with_gemini(
        prompt=scene_prompt,
        project_state=project_state,
        transcript_tl_text=transcript_tl_text,
        downloads_dir=downloads_dir,
        sfx_assets=sfx_assets,
    )
    if res is None:
        return None
    plan, model_id = res
    plan["planner_model"] = model_id
    return plan
