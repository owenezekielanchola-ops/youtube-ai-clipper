from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types
from groq import Groq
import yt_dlp

from schemas import CaptionItem, ProjectState, SfxAsset, ToolCallSchema
from services.ai_racer import race_ai_candidates
from services.model_registry import (
    get_gemini_models_chain,
    get_nvidia_fast_models_chain,
    get_tool_models_chain,
    report_model_failure,
)
from services.nvidia_client import call_nvidia_chat, is_nvidia_configured
from services.sfx_library import find_best_matching_sfx
from services.transcription import build_captions_from_transcript, detect_pause_gaps
from services.visual_planner import analyze_user_clip_scenes_for_reference
from services.youtube_ingest import _apply_ytdlp_common_opts

DEFAULT_STYLE_PROFILE: Dict[str, Any] = {
    "pacing": "fast",
    "avg_cut_duration_sec": 3.5,
    "target_aspect_ratio": "9:16",
    "caption_preset": "karaoke",
    "caption_position": "bottom",
    "caption_font_size": 30,
    "caption_highlight_color": "#FACC15",
    "caption_is_bold": True,
    "zoom_style": "punch_in",
    "zoom_frequency_per_min": 6.0,
    "zoom_intensity": 1.32,
    "speed_style": "energetic_ramps",
    "sfx_density": "medium",
    "sfx_cue_types": ["whoosh", "pop", "boom"],
    "drawing_style": "none",
    "reference_beats": [],
    "summary": "Fast-paced short-form style with punch-in zooms, karaoke highlights, and reactive SFX.",
}

_VALID_PACING = {"fast", "medium", "slow"}
_VALID_RATIOS = {"9:16", "16:9", "1:1"}
_VALID_PRESETS = {"karaoke", "bold_social", "yellow_highlight", "minimal"}
_VALID_CAP_POS = {"top", "middle", "bottom"}
_VALID_ZOOM_STYLES = {"punch_in", "zoom_in", "zoom_out", "mixed", "none"}
_VALID_SPEED_STYLES = {"none", "energetic_ramps", "slightly_fast", "dramatic_slowmo"}
_VALID_SFX_DENSITY = {"none", "light", "medium", "heavy"}
_VALID_DRAWING_STYLES = {"none", "arrows_highlights", "circles"}


def _strip_json_payload(raw_text: str) -> Dict[str, Any]:
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
    if isinstance(parsed, dict):
        return parsed
    return {}


def _clamp_float(val: Any, low: float, high: float, default: float) -> float:
    try:
        num = float(val)
        return round(max(low, min(high, num)), 2)
    except (TypeError, ValueError):
        return default


def _sanitize_style_profile(raw: Dict[str, Any]) -> Dict[str, Any]:
    prof = dict(DEFAULT_STYLE_PROFILE)
    if not isinstance(raw, dict):
        return prof

    pacing = str(raw.get("pacing", prof["pacing"])).strip().lower()
    prof["pacing"] = pacing if pacing in _VALID_PACING else prof["pacing"]

    prof["avg_cut_duration_sec"] = _clamp_float(
        raw.get("avg_cut_duration_sec"), 1.5, 15.0, prof["avg_cut_duration_sec"]
    )

    ratio = str(raw.get("target_aspect_ratio", prof["target_aspect_ratio"])).strip()
    prof["target_aspect_ratio"] = ratio if ratio in _VALID_RATIOS else prof["target_aspect_ratio"]

    preset = str(raw.get("caption_preset", prof["caption_preset"])).strip().lower()
    prof["caption_preset"] = preset if preset in _VALID_PRESETS else prof["caption_preset"]

    pos = str(raw.get("caption_position", prof["caption_position"])).strip().lower()
    prof["caption_position"] = pos if pos in _VALID_CAP_POS else prof["caption_position"]

    try:
        prof["caption_font_size"] = max(20, min(44, int(raw.get("caption_font_size", 30))))
    except (TypeError, ValueError):
        prof["caption_font_size"] = 30

    hl_color = str(raw.get("caption_highlight_color", "#FACC15")).strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", hl_color):
        prof["caption_highlight_color"] = hl_color.upper()

    if isinstance(raw.get("caption_is_bold"), bool):
        prof["caption_is_bold"] = raw["caption_is_bold"]

    z_style = str(raw.get("zoom_style", prof["zoom_style"])).strip().lower()
    prof["zoom_style"] = z_style if z_style in _VALID_ZOOM_STYLES else prof["zoom_style"]

    prof["zoom_frequency_per_min"] = _clamp_float(
        raw.get("zoom_frequency_per_min"), 0.0, 18.0, prof["zoom_frequency_per_min"]
    )
    prof["zoom_intensity"] = _clamp_float(
        raw.get("zoom_intensity"), 1.12, 1.75, prof["zoom_intensity"]
    )

    spd_style = str(raw.get("speed_style", prof["speed_style"])).strip().lower()
    prof["speed_style"] = (
        spd_style if spd_style in _VALID_SPEED_STYLES else prof["speed_style"]
    )

    sfx_den = str(raw.get("sfx_density", prof["sfx_density"])).strip().lower()
    prof["sfx_density"] = sfx_den if sfx_den in _VALID_SFX_DENSITY else prof["sfx_density"]

    cues_raw = raw.get("sfx_cue_types")
    if isinstance(cues_raw, list):
        cues = [
            str(c).strip().lower()[:24]
            for c in cues_raw
            if str(c).strip()
        ][:6]
        if cues:
            prof["sfx_cue_types"] = cues

    d_style = str(raw.get("drawing_style", prof["drawing_style"])).strip().lower()
    prof["drawing_style"] = (
        d_style if d_style in _VALID_DRAWING_STYLES else prof["drawing_style"]
    )

    beats_out: List[Dict[str, Any]] = []
    for bt in raw.get("reference_beats") or []:
        if not isinstance(bt, dict):
            continue
        bs = _clamp_float(bt.get("start"), 0.0, 600.0, 0.0)
        be = _clamp_float(bt.get("end"), bs + 0.3, 600.0, bs + 2.0)
        mtype = str(bt.get("moment_type") or "highlight").strip().lower()[:30]
        what = str(bt.get("what_is_happening") or bt.get("description") or "").strip()[:200]
        edits = [
            str(e).strip().lower()[:30]
            for e in (bt.get("edits_used") or [])
            if str(e).strip()
        ][:6]
        if what:
            beats_out.append(
                {
                    "start": bs,
                    "end": be,
                    "moment_type": mtype,
                    "what_is_happening": what,
                    "edits_used": edits,
                }
            )
        if len(beats_out) >= 8:
            break
    prof["reference_beats"] = beats_out

    summary = str(raw.get("summary") or "").strip()
    if summary:
        prof["summary"] = summary[:400]

    return prof


def _fetch_reference_metadata_ytdlp(reference_url: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "title": "",
        "description": "",
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "tags": [],
        "inferred_aspect_ratio": "9:16" if "/shorts/" in reference_url.lower() else "16:9",
    }
    try:
        opts = _apply_ytdlp_common_opts(
            {
                "skip_download": True,
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
            }
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(reference_url, download=False) or {}
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        ratio = meta["inferred_aspect_ratio"]
        if width > 0 and height > 0:
            if height > width * 1.15:
                ratio = "9:16"
            elif abs(width - height) < max(width, height) * 0.15:
                ratio = "1:1"
            else:
                ratio = "16:9"
        meta.update(
            {
                "title": str(info.get("title") or "")[:180],
                "description": str(info.get("description") or "")[:600],
                "duration": float(info.get("duration") or 0.0),
                "width": width,
                "height": height,
                "tags": [str(t)[:30] for t in (info.get("tags") or [])[:12]],
                "inferred_aspect_ratio": ratio,
            }
        )
    except Exception:
        pass
    return meta


def analyze_reference_youtube_style(
    reference_url: str,
    custom_instruction: Optional[str] = None,
    deep_think: bool = False,
) -> Tuple[Dict[str, Any], str]:
    clean_url = reference_url.strip()
    note = (custom_instruction or "").strip()
    note_clause = f"\nUser extra instruction: {note}" if note else ""
    deep_clause = (
        "\nDEEP ANALYSIS MODE: Study EVERY visual cut, pacing shift, zoom intensity/timing, "
        "caption styling, sound effect cue, and extract 5-10 detailed chronological `reference_beats` "
        "explaining why each edit was used so we can replicate the creative intent accurately."
        if deep_think
        else ""
    )

    meta = _fetch_reference_metadata_ytdlp(clean_url)
    ref_dur = float(meta.get("duration") or 0.0)
    print(
        f"[ReferenceStyle] Probed reference '{(meta.get('title') or clean_url)[:48]}' ({ref_dur:.1f}s)",
        flush=True,
    )

    analysis_prompt = (
        "Watch this reference YouTube clip carefully and analyze BOTH its editing style AND a chronological beat list of what they are doing so we can replicate the style onto another video's matching moments.\n"
        "Inspect: cut pacing, aspect ratio, caption style/position/size/color, zoom style & frequency (punch-in cuts vs smooth zooms), "
        "speed changes (ramps/fast/slow-mo), sound effect density & cue words (whoosh, boom, pop, laugh, glitch, ding), screen drawings (arrows/circles), "
        "and beat-by-beat what is happening (hook, funny part, reaction, buildup, punchline, reveal).\n"
        "Return STRICT JSON ONLY matching these exact keys:\n"
        "{\n"
        '  "pacing": "fast" | "medium" | "slow",\n'
        '  "avg_cut_duration_sec": number (1.5 to 15.0),\n'
        '  "target_aspect_ratio": "9:16" | "16:9" | "1:1",\n'
        '  "caption_preset": "karaoke" | "bold_social" | "yellow_highlight" | "minimal",\n'
        '  "caption_position": "top" | "middle" | "bottom",\n'
        '  "caption_font_size": integer (20 to 44),\n'
        '  "caption_highlight_color": "#RRGGBB",\n'
        '  "caption_is_bold": boolean,\n'
        '  "zoom_style": "punch_in" | "zoom_in" | "zoom_out" | "mixed" | "none",\n'
        '  "zoom_frequency_per_min": number (0.0 to 18.0),\n'
        '  "zoom_intensity": number (1.12 to 1.75),\n'
        '  "speed_style": "none" | "energetic_ramps" | "slightly_fast" | "dramatic_slowmo",\n'
        '  "sfx_density": "none" | "light" | "medium" | "heavy",\n'
        '  "sfx_cue_types": ["whoosh", "pop", "boom"],\n'
        '  "drawing_style": "none" | "arrows_highlights" | "circles",\n'
        '  "reference_beats": [\n'
        '    {"start": 0.0, "end": 2.5, "moment_type": "hook" | "funny" | "reaction" | "action" | "punchline", "what_is_happening": "What the creator/people are doing here", "edits_used": ["punch_in_zoom", "sfx", "bold_captions"]}\n'
        "  ],\n"
        '  "summary": "1-3 sentence human summary of how this clip is edited"\n'
        "}"
        f"{deep_clause}{note_clause}"
    )

    use_direct_uri = (
        (clean_url.startswith("http://") or clean_url.startswith("https://"))
        and (ref_dur <= 300.0)
    )
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key and gemini_key != "your_gemini_api_key_here" and use_direct_uri:
        client = genai.Client(api_key=gemini_key)
        quota_failures = 0
        for model_id in get_gemini_models_chain()[:3]:
            try:
                print(f"[ReferenceStyle] Analyzing reference clip with {model_id}...", flush=True)
                contents: List[Any] = [
                    types.Part.from_uri(file_uri=clean_url, mime_type="video/mp4"),
                    analysis_prompt,
                ]
                resp = client.models.generate_content(
                    model=model_id,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )
                profile = _sanitize_style_profile(_strip_json_payload(resp.text or "{}"))
                return profile, f"gemini-vision ({model_id})"
            except Exception as exc:
                print(f"[ReferenceStyle] Gemini ({model_id}) failed: {str(exc)[:140]}", flush=True)
                report_model_failure("gemini", model_id, exc)
                low = str(exc).lower()
                if any(k in low for k in ("429", "resource_exhausted", "quota", "rate_limit", "rate limit")):
                    quota_failures += 1
                    if quota_failures >= 2:
                        break
                continue
    elif ref_dur > 300.0:
        print(
            f"[ReferenceStyle] Reference video is {ref_dur:.0f}s (> 300s) — skipping direct video stream and using metadata analysis.",
            flush=True,
        )

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    has_groq = bool(groq_key and groq_key != "your_groq_api_key_here")
    has_nvidia = is_nvidia_configured()
    if has_groq or has_nvidia:
        meta_context = (
            f"Reference YouTube URL: {clean_url}\n"
            f"Metadata: {json.dumps(meta, indent=2)}\n\n"
            f"{analysis_prompt}"
        )
        sys_msg = (
            "Respond with valid JSON ONLY predicting the editing style profile from the YouTube clip metadata and format."
        )
        race_cands: List[Dict[str, Any]] = []

        if has_groq:
            groq_client = Groq(api_key=groq_key, timeout=20.0)
            for model_id in get_tool_models_chain()[:2]:
                def _make_groq_ref(m_id: str = model_id):
                    def _run() -> Dict[str, Any]:
                        comp = groq_client.chat.completions.create(
                            model=m_id,
                            messages=[
                                {"role": "system", "content": sys_msg},
                                {"role": "user", "content": meta_context},
                            ],
                            temperature=0.2,
                        )
                        raw = str(comp.choices[0].message.content or "")
                        return _sanitize_style_profile(_strip_json_payload(raw))
                    return _run
                race_cands.append({
                    "provider": "groq",
                    "model": model_id,
                    "name": f"groq:{model_id}",
                    "fn": _make_groq_ref(),
                })

        if has_nvidia:
            for model_id in get_nvidia_fast_models_chain()[:2]:
                def _make_nv_ref(m_id: str = model_id):
                    def _run() -> Dict[str, Any]:
                        raw = call_nvidia_chat(
                            model_id=m_id,
                            messages=[
                                {"role": "system", "content": sys_msg},
                                {"role": "user", "content": meta_context},
                            ],
                            temperature=0.2,
                            max_tokens=2400 if deep_think else 1000,
                            timeout=35.0 if deep_think else 16.0,
                            enable_thinking=None if deep_think else False,
                        )
                        return _sanitize_style_profile(_strip_json_payload(raw))
                    return _run
                race_cands.append({
                    "provider": "nvidia",
                    "model": model_id,
                    "name": f"nvidia:{model_id}",
                    "fn": _make_nv_ref(),
                })

        def _validate_style_profile(profile_obj: Any) -> Dict[str, Any]:
            if not isinstance(profile_obj, dict) or "pacing" not in profile_obj:
                raise ValueError("Invalid style profile payload")
            return profile_obj

        try:
            won_prof, winner = race_ai_candidates(
                race_cands,
                validate_fn=_validate_style_profile,
                label="reference_style_deep" if deep_think else "reference_style",
                max_workers=3,
                per_race_timeout=40.0 if deep_think else 18.0,
                hedge_delay_sec=0.0,
            )
            return won_prof, f"{winner['provider']}:{winner['model']} ({winner['latency_ms']:.0f}ms)"
        except Exception as exc:
            print(f"[ReferenceStyle] Groq+NVIDIA race failed: {str(exc)[:140]}", flush=True)

    # Deterministic fallback from URL / metadata
    is_short = (
        meta["inferred_aspect_ratio"] == "9:16"
        or "/shorts/" in clean_url.lower()
        or (0 < meta.get("duration", 0) <= 65)
    )
    if is_short:
        fallback = _sanitize_style_profile(
            {
                "pacing": "fast",
                "avg_cut_duration_sec": 3.0,
                "target_aspect_ratio": "9:16",
                "caption_preset": "karaoke",
                "caption_position": "bottom",
                "caption_font_size": 32,
                "caption_highlight_color": "#FACC15",
                "caption_is_bold": True,
                "zoom_style": "punch_in",
                "zoom_frequency_per_min": 7.0,
                "zoom_intensity": 1.35,
                "speed_style": "energetic_ramps",
                "sfx_density": "medium",
                "sfx_cue_types": ["whoosh", "pop", "boom"],
                "drawing_style": "none",
                "summary": (
                    f"Fast vertical Short style inferred from {meta.get('title') or clean_url}: "
                    "punch-in zooms, karaoke highlights, pause cuts, and reactive SFX cues."
                ),
            }
        )
    else:
        fallback = _sanitize_style_profile(
            {
                "pacing": "medium",
                "avg_cut_duration_sec": 6.5,
                "target_aspect_ratio": meta["inferred_aspect_ratio"],
                "caption_preset": "bold_social",
                "caption_position": "bottom",
                "caption_font_size": 28,
                "caption_highlight_color": "#FACC15",
                "caption_is_bold": True,
                "zoom_style": "zoom_in",
                "zoom_frequency_per_min": 4.0,
                "zoom_intensity": 1.25,
                "speed_style": "none",
                "sfx_density": "light",
                "sfx_cue_types": ["whoosh", "ding"],
                "drawing_style": "arrows_highlights",
                "summary": (
                    f"Standard {meta['inferred_aspect_ratio']} YouTube pacing inferred from "
                    f"{meta.get('title') or clean_url}: smooth zooms, bold captions, and subtle callouts."
                ),
            }
        )
    return fallback, "offline-metadata-fallback"


def _pick_peak_timestamps(
    total_dur: float, count: int, audio_peaks: List[float]
) -> List[float]:
    if count <= 0 or total_dur < 2.0:
        return []
    safe_start = min(1.0, total_dur * 0.15)
    safe_end = max(safe_start + 0.5, total_dur - 1.4)
    span = safe_end - safe_start
    anchors: List[float] = []
    num_buckets = len(audio_peaks)

    for i in range(count):
        base_t = safe_start + (i + 0.5) * (span / count)
        if num_buckets > 4:
            # Look within +/- 15% window around evenly spaced anchor for loudest audio peak
            win_lo = max(safe_start, base_t - span / (count * 2.2))
            win_hi = min(safe_end, base_t + span / (count * 2.2))
            b_lo = max(0, min(num_buckets - 1, int((win_lo / total_dur) * num_buckets)))
            b_hi = max(b_lo, min(num_buckets - 1, int((win_hi / total_dur) * num_buckets)))
            best_b = max(range(b_lo, b_hi + 1), key=lambda b: audio_peaks[b])
            snapped = (best_b + 0.5) / num_buckets * total_dur
            base_t = max(safe_start, min(safe_end, snapped))
        anchors.append(round(base_t, 2))
    return sorted(anchors)


def _map_tl_after_cuts_and_speed(
    orig_tl: float,
    cut_tl_intervals: List[Tuple[float, float]],
    speed_ops: List[Tuple[float, float, float]],
    max_dur: float,
) -> float:
    t = max(0.0, float(orig_tl))
    removed_before = 0.0
    for cs, ce in cut_tl_intervals:
        if ce <= cs:
            continue
        if ce <= t:
            removed_before += ce - cs
        elif cs < t < ce:
            removed_before += t - cs
    post_cut_t = max(0.0, t - removed_before)
    delta_speed = 0.0
    for ss, se, spd in speed_ops:
        if se <= ss or spd <= 0:
            continue
        if post_cut_t >= se:
            delta_speed += (se - ss) * (1.0 / spd - 1.0)
        elif ss < post_cut_t < se:
            delta_speed += (post_cut_t - ss) * (1.0 / spd - 1.0)
    return round(max(0.0, min(max_dur, post_cut_t + delta_speed)), 2)


def apply_reference_style_to_project(
    profile: Dict[str, Any],
    project_state: ProjectState,
    transcript: Optional[Dict[str, Any]],
    sfx_assets: List[SfxAsset],
    custom_instruction: Optional[str] = None,
    user_scene_analysis: Optional[Dict[str, Any]] = None,
) -> Tuple[List[ToolCallSchema], Optional[List[CaptionItem]], str]:
    clips = project_state.tracks.clips
    raw_tl_dur = (
        sum(c.duration for c in clips)
        if clips
        else float(project_state.sourceVideo.originalDuration or 0.0)
    )
    tool_calls: List[ToolCallSchema] = []
    auto_captions: Optional[List[CaptionItem]] = None
    notes: List[str] = []

    user_moments: List[Dict[str, Any]] = list(
        (user_scene_analysis or {}).get("detected_moments") or []
    )
    user_subjects: List[Dict[str, Any]] = list(
        (user_scene_analysis or {}).get("subjects") or []
    )
    default_subject = next(
        (
            s
            for s in user_subjects
            if any(
                k in str(s.get("label", "")).lower()
                for k in ("creator", "cam", "webcam", "face", "person", "streamer")
            )
        ),
        user_subjects[0] if user_subjects else None,
    )
    default_fx = float(default_subject["focus_x"]) if default_subject else 0.5
    default_fy = float(default_subject["focus_y"]) if default_subject else 0.45

    def _subject_coords(target_label: str) -> Tuple[float, float]:
        low = (target_label or "").lower()
        if low:
            for s in user_subjects:
                if low in str(s.get("label", "")).lower():
                    return float(s.get("focus_x", default_fx)), float(s.get("focus_y", default_fy))
        return default_fx, default_fy

    ratio = str(profile.get("target_aspect_ratio") or "9:16")
    if custom_instruction and "keep ratio" in custom_instruction.lower():
        ratio = project_state.workspaceState.targetAspectRatio
    tool_calls.append(
        ToolCallSchema(
            id=f"tc_{uuid.uuid4().hex[:8]}",
            name="change_video_format",
            arguments={"aspect_ratio": ratio},
        )
    )
    notes.append(f"format {ratio}")

    # Captions + style
    if transcript and len(project_state.tracks.captions) == 0:
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="generate_auto_captions",
                arguments={},
            )
        )
        auto_captions = build_captions_from_transcript(transcript)

    tool_calls.append(
        ToolCallSchema(
            id=f"tc_{uuid.uuid4().hex[:8]}",
            name="change_caption_style",
            arguments={
                "preset": profile.get("caption_preset", "karaoke"),
                "position": profile.get("caption_position", "bottom"),
                "font_size": int(profile.get("caption_font_size", 30)),
                "highlight_color": profile.get("caption_highlight_color", "#FACC15"),
                "is_bold": bool(profile.get("caption_is_bold", True)),
            },
        )
    )
    notes.append(f"{profile.get('caption_preset', 'karaoke')} captions")

    # Pacing: remove silence gaps on fast pacing
    estimated_removed = 0.0
    cut_tl_intervals: List[Tuple[float, float]] = []
    if profile.get("pacing") == "fast" and transcript:
        pauses = detect_pause_gaps(transcript, min_gap=0.65)[:15]
        # Only cut pauses overlapping active source clips so timeline math stays valid
        active_pauses: List[Dict[str, float]] = []
        for p in pauses:
            ps = float(p["start"])
            pe = float(p["end"])
            if not clips:
                active_pauses.append({"start": ps, "end": pe})
                cut_tl_intervals.append((round(ps, 3), round(pe, 3)))
                estimated_removed += max(0.0, pe - ps)
            else:
                clip_overlap_tl = 0.0
                for c in clips:
                    os_val = max(ps, c.sourceStart)
                    oe_val = min(pe, c.sourceEnd)
                    ov = oe_val - os_val
                    if ov > 0.1:
                        spd = max(0.25, min(4.0, float(getattr(c, "speed", 1.0) or 1.0)))
                        tl_s = float(c.timelineStart) + (os_val - c.sourceStart) / spd
                        tl_e = float(c.timelineStart) + (oe_val - c.sourceStart) / spd
                        cut_tl_intervals.append((round(tl_s, 3), round(tl_e, 3)))
                        clip_overlap_tl += ov / spd
                if clip_overlap_tl > 0.1:
                    active_pauses.append({"start": ps, "end": pe})
                    estimated_removed += clip_overlap_tl
        if active_pauses:
            cut_tl_intervals.sort(key=lambda iv: iv[0])
            tool_calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="remove_time_ranges",
                    arguments={"ranges": active_pauses},
                )
            )
            notes.append(f"cut {len(active_pauses)} pause(s)")

    effective_dur = max(1.5, raw_tl_dur - estimated_removed) if raw_tl_dur > 0 else 0.0

    # Speed adjustments (use range-based speed so remove_time_ranges clip ID splits stay valid)
    speed_style = str(profile.get("speed_style") or "none")
    speed_ops: List[Tuple[float, float, float]] = []
    if speed_style == "slightly_fast" and effective_dur >= 1.5:
        end_full = round(effective_dur, 2)
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="set_clip_speed",
                arguments={"start_time": 0.0, "end_time": end_full, "speed": 1.15},
            )
        )
        notes.append("1.15x brisk speed")
    elif speed_style == "energetic_ramps" and effective_dur >= 8.0:
        s_t = round(effective_dur * 0.25, 2)
        e_t = round(min(s_t + 3.0, effective_dur - 1.0), 2)
        if e_t - s_t >= 1.0:
            tool_calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="set_clip_speed",
                    arguments={"start_time": s_t, "end_time": e_t, "speed": 1.5},
                )
            )
            notes.append(f"1.5x ramp ({s_t}s–{e_t}s)")
    elif speed_style == "dramatic_slowmo" and effective_dur >= 6.0:
        s_t = round(effective_dur * 0.58, 2)
        e_t = round(min(s_t + 2.5, effective_dur - 0.5), 2)
        if e_t - s_t >= 0.8:
            tool_calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="set_clip_speed",
                    arguments={"start_time": s_t, "end_time": e_t, "speed": 0.75},
                )
            )
            notes.append(f"0.75x slow-mo ({s_t}s–{e_t}s)")

    # Zoom effects (prioritize real funny/reaction/hook/action moments spotted in your clip!)
    zoom_style = str(profile.get("zoom_style") or "none")
    zoom_freq = float(profile.get("zoom_frequency_per_min") or 0.0)
    zoom_scale = float(profile.get("zoom_intensity") or 1.32)
    zoom_slots: List[Dict[str, Any]] = []

    if user_moments and effective_dur >= 2.5:
        priority_order = {"funny": 0, "reaction": 1, "punchline": 2, "hook": 3, "action": 4, "buildup": 5, "highlight": 6}
        ranked_moments = sorted(
            user_moments,
            key=lambda m: (priority_order.get(str(m.get("moment_type", "")).lower(), 9), float(m.get("start", 0.0))),
        )
        freq_cap = round((effective_dur / 60.0) * max(4.0, zoom_freq))
        dur_cap = max(1, int(effective_dur // 2.5))
        max_z = max(1, min(8, max(freq_cap, min(len(user_moments), dur_cap))))
        for rm in ranked_moments:
            if len(zoom_slots) >= max_z:
                break
            raw_s = float(rm.get("start", 0.0))
            raw_e = float(rm.get("end", raw_s + 1.8))
            zs = _map_tl_after_cuts_and_speed(
                raw_s, cut_tl_intervals, speed_ops, max(0.6, effective_dur - 0.6)
            )
            if any(abs(slot["start"] - zs) < 1.2 for slot in zoom_slots):
                continue
            ze_mapped = _map_tl_after_cuts_and_speed(
                raw_e, cut_tl_intervals, speed_ops, effective_dur
            )
            ze = round(min(effective_dur, max(zs + 1.1, ze_mapped)), 2)
            fx, fy = _subject_coords(str(rm.get("target_subject") or ""))
            zoom_slots.append({"start": zs, "end": ze, "fx": fx, "fy": fy})
        zoom_slots.sort(key=lambda item: item["start"])

    zoom_times: List[float] = [slot["start"] for slot in zoom_slots]
    if zoom_style != "none" and (zoom_freq > 0 or zoom_slots) and effective_dur >= 2.5:
        if not zoom_slots:
            zoom_count = max(1, min(8, round((effective_dur / 60.0) * zoom_freq)))
            peak_ts = _pick_peak_timestamps(
                effective_dur, zoom_count, project_state.sourceVideo.audioPeaks
            )
            for zt in peak_ts:
                dur_z = 1.4 if zoom_style == "punch_in" else 2.0
                zoom_slots.append(
                    {
                        "start": zt,
                        "end": round(min(effective_dur, zt + dur_z), 2),
                        "fx": default_fx,
                        "fy": default_fy,
                    }
                )
            zoom_times = [s["start"] for s in zoom_slots]

        deleted_zoom_ids: set[str] = set()
        for idx, slot in enumerate(zoom_slots):
            zt = float(slot["start"])
            z_end = float(slot["end"])
            if z_end - zt < 0.4:
                continue
            for ez in project_state.tracks.zooms or []:
                if ez.id not in deleted_zoom_ids and abs(float(ez.start) - zt) < 0.8:
                    tool_calls.append(
                        ToolCallSchema(
                            id=f"tc_{uuid.uuid4().hex[:8]}",
                            name="delete_zoom_effect",
                            arguments={"zoom_id": ez.id},
                        )
                    )
                    deleted_zoom_ids.add(ez.id)
            kind = zoom_style
            if kind == "mixed":
                kind = "punch_in" if idx % 2 == 0 else "zoom_in"
            tool_calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="add_zoom_effect",
                    arguments={
                        "start": zt,
                        "end": z_end,
                        "kind": kind,
                        "start_scale": zoom_scale if kind == "zoom_out" else 1.0,
                        "end_scale": 1.0 if kind == "zoom_out" else zoom_scale,
                        "focus_x": round(float(slot["fx"]), 3),
                        "focus_y": round(float(slot["fy"]), 3),
                        "easing": "instant" if kind == "punch_in" else "ease_in_out",
                    },
                )
            )
        notes.append(f"{len(zoom_slots)} zoom(s)")

    # Custom MP3 SFX placement (varied assets, punchy duration <= 2.6s, replace overlapping SFX)
    sfx_density = str(profile.get("sfx_density") or "none")
    cue_types: List[str] = list(profile.get("sfx_cue_types") or ["whoosh", "pop", "boom"])
    if sfx_density != "none" and effective_dur >= 2.0:
        if sfx_assets:
            density_map = {"light": 2, "medium": 4, "heavy": 6}
            target_sfx = min(
                density_map.get(sfx_density, 3),
                max(1, int(effective_dur // 2.5)),
            )
            raw_sfx_times = (
                zoom_times
                if len(zoom_times) >= target_sfx
                else _pick_peak_timestamps(
                    effective_dur, target_sfx, project_state.sourceVideo.audioPeaks
                )
            )
            sfx_times: List[float] = []
            for cand_t in raw_sfx_times:
                if any(abs(cand_t - prev_t) < 1.2 for prev_t in sfx_times):
                    continue
                sfx_times.append(cand_t)
                if len(sfx_times) >= target_sfx:
                    break

            used_sfx_ids: set[str] = set()
            deleted_sfx_ids: set[str] = set()
            placed_sfx_count = 0
            for idx, st in enumerate(sfx_times):
                cue = cue_types[idx % len(cue_types)] if cue_types else ""
                asset = find_best_matching_sfx(
                    sfx_assets, cue, exclude_ids=used_sfx_ids
                ) or find_best_matching_sfx(sfx_assets, cue)
                if asset is None:
                    continue
                used_sfx_ids.add(asset.id)
                for es in project_state.tracks.sfx or []:
                    if es.id not in deleted_sfx_ids and abs(float(es.start) - st) < 0.8:
                        tool_calls.append(
                            ToolCallSchema(
                                id=f"tc_{uuid.uuid4().hex[:8]}",
                                name="delete_sfx_clip",
                                arguments={"sfx_id": es.id},
                            )
                        )
                        deleted_sfx_ids.add(es.id)
                play_dur = round(min(float(asset.duration), 2.6, max(0.4, effective_dur - st)), 3)
                tool_calls.append(
                    ToolCallSchema(
                        id=f"tc_{uuid.uuid4().hex[:8]}",
                        name="add_sfx_clip",
                        arguments={
                            "start": st,
                            "asset_id": asset.id,
                            "sfx_name": asset.name,
                            "url": asset.url,
                            "duration": play_dur,
                            "volume": 0.8,
                        },
                    )
                )
                placed_sfx_count += 1
            notes.append(f"{placed_sfx_count} MP3 SFX")
        else:
            notes.append(
                "0 SFX placed (upload .mp3 files in the left SFX Library tab for auto SFX placement)"
            )

    # Drawings aimed at detected subject / moment
    drawing_style = str(profile.get("drawing_style") or "none")
    draw_anchor_s = zoom_times[0] if zoom_times else 1.0
    if drawing_style == "arrows_highlights" and effective_dur >= 3.5:
        tx = round(max(0.15, min(0.85, default_fx)), 3)
        ty = round(max(0.15, min(0.85, default_fy)), 3)
        ox = round(max(0.08, min(0.92, tx - 0.18 if tx > 0.3 else tx + 0.18)), 3)
        oy = round(max(0.08, min(0.92, ty - 0.18 if ty > 0.3 else ty + 0.18)), 3)
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="add_drawing",
                arguments={
                    "start": draw_anchor_s,
                    "end": round(min(effective_dur, draw_anchor_s + 2.2), 2),
                    "tool": "arrow",
                    "points": [{"x": ox, "y": oy}, {"x": tx, "y": ty}],
                    "color": "#FACC15",
                    "stroke_width": 6,
                    "opacity": 1.0,
                },
            )
        )
        notes.append("arrow callout")
    elif drawing_style == "circles" and effective_dur >= 4.0:
        cx1 = round(max(0.05, default_fx - 0.15), 3)
        cy1 = round(max(0.05, default_fy - 0.15), 3)
        cx2 = round(min(0.95, default_fx + 0.15), 3)
        cy2 = round(min(0.95, default_fy + 0.15), 3)
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="add_drawing",
                arguments={
                    "start": draw_anchor_s,
                    "end": round(min(effective_dur, draw_anchor_s + 2.3), 2),
                    "tool": "circle",
                    "points": [{"x": cx1, "y": cy1}, {"x": cx2, "y": cy2}],
                    "color": "#EF4444",
                    "stroke_width": 6,
                    "opacity": 1.0,
                },
            )
        )
        notes.append("circle callout")

    summary = str(profile.get("summary") or "Cloned reference editing style.")
    style_card = (
        f"Style profile: {ratio} · {profile.get('pacing', 'fast')} pacing · "
        f"{profile.get('caption_preset', 'karaoke')} captions · {zoom_style} zooms · {sfx_density} SFX"
    )
    msg_parts = [summary, style_card]

    ref_beats: List[Dict[str, Any]] = list(profile.get("reference_beats") or [])
    if ref_beats:
        beat_lines = [
            f"• [{b['start']:.1f}s–{b['end']:.1f}s] ({b['moment_type']}) {b['what_is_happening']}"
            + (f" → [{', '.join(b['edits_used'])}]" if b.get("edits_used") else "")
            for b in ref_beats[:5]
        ]
        msg_parts.append("Reference breakdown:\n" + "\n".join(beat_lines))

    if user_moments:
        um_lines = [
            f"• [{m['start']:.1f}s–{m['end']:.1f}s] ({m.get('moment_type', 'moment')}) {m.get('description', '')}"
            for m in user_moments[:5]
        ]
        msg_parts.append("Matched moments in your clip:\n" + "\n".join(um_lines))

    msg_parts.append(f"Applied edits: {', '.join(notes)}.")
    message = "\n\n".join(msg_parts)
    return tool_calls, auto_captions, message


def clone_reference_style_for_project(
    reference_url: str,
    project_state: ProjectState,
    transcript: Optional[Dict[str, Any]],
    sfx_assets: List[SfxAsset],
    downloads_dir: Any,
    custom_instruction: Optional[str] = None,
    transcript_tl_text: str = "",
    deep_think: bool = False,
) -> Tuple[Dict[str, Any], List[ToolCallSchema], Optional[List[CaptionItem]], str, str]:
    """
    Two-side Gemini/NVIDIA Reference Cloner:
    1. Watches reference YouTube URL -> extracts style profile & beat breakdown ('what happens').
    2. Watches YOUR active timeline clip via Visual Planner (if downloaded & vision configured)
       -> spots funny/reaction/action moments & subject coordinates in your clip.
    3. Runs both analyses concurrently and applies reference beats onto your clip's real moments.
    """
    user_scene: Optional[Dict[str, Any]] = None
    if project_state.tracks.clips and downloads_dir is not None:
        with ThreadPoolExecutor(max_workers=2) as pool:
            ref_future = pool.submit(
                analyze_reference_youtube_style,
                reference_url,
                custom_instruction=custom_instruction,
                deep_think=deep_think,
            )
            scene_future = pool.submit(
                analyze_user_clip_scenes_for_reference,
                project_state=project_state,
                transcript_tl_text=transcript_tl_text,
                downloads_dir=downloads_dir,
                sfx_assets=sfx_assets,
                deep_think=deep_think,
            )
            profile, route_label = ref_future.result()
            try:
                user_scene = scene_future.result()
            except Exception:
                user_scene = None
    else:
        profile, route_label = analyze_reference_youtube_style(
            reference_url,
            custom_instruction=custom_instruction,
            deep_think=deep_think,
        )

    tool_calls, auto_captions, summary_msg = apply_reference_style_to_project(
        profile=profile,
        project_state=project_state,
        transcript=transcript,
        sfx_assets=sfx_assets,
        custom_instruction=custom_instruction,
        user_scene_analysis=user_scene,
    )
    if user_scene:
        profile = dict(profile)
        profile["matched_user_moments"] = user_scene.get("detected_moments", [])
        if user_scene.get("planner_model"):
            route_label = f"{route_label} + clip-vision ({user_scene['planner_model']})"
    if deep_think:
        route_label = f"{route_label} [Deep Think]"
    return profile, tool_calls, auto_captions, summary_msg, route_label
