import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types
from groq import Groq

from schemas import (
    SHARED_TOOLS_DEFINITIONS,
    AiEditResponse,
    CaptionItem,
    ProjectState,
    ToolCallSchema,
)
from services.model_registry import (
    get_gemini_models_chain,
    get_router_models_chain,
    get_tool_models_chain,
)
from services.transcription import (
    build_captions_from_transcript,
    detect_pause_gaps,
    load_cached_transcript,
)

VIRAL_FINDER_KEYWORDS = (
    "funny",
    "funniest",
    "viral",
    "most viewed",
    "highlight",
    "best part",
    "best moment",
    "humor",
    "laugh",
    "comedy",
    "punchline",
    "hook",
    "top 3",
    "top 2",
    "top clips",
    "find moments",
    "clip the best",
    "create a short",
    "make a short",
    "interesting",
    "exciting",
    "engaging",
    "crazy",
    "wild",
    "juicy",
    "insane",
    "epic",
    "emotional",
    "dramatic",
    "reaction",
    "clip where",
    "part where",
    "moment when",
    "talks about",
    "talking about",
    "speaks about",
    "find where",
    "find the part",
)

FAST_EDIT_KEYWORDS = (
    "aspect ratio",
    "format",
    "switch to",
    "change to",
    "remove pause",
    "remove pauses",
    "remove all pauses",
    "remove silence",
    "dead air",
    "cut first",
    "cut the first",
    "trim",
    "9:16",
    "16:9",
    "1:1",
    "karaoke",
    "yellow",
    "bold",
    "minimal",
    "split",
    "delete",
    "merge",
    "reorder",
    "auto caption",
    "generate caption",
    "tiktok",
    "reel",
    "youtube short",
)

DISCOVERY_HINT_WORDS = (
    "find",
    "funny",
    "funniest",
    "viral",
    "highlight",
    "best",
    "top",
    "where",
    "when",
    "part",
    "moment",
    "segment",
    "story",
    "about",
    "pick",
    "grab",
    "extract",
)


def _has_phrase_or_word(text: str, phrases: Tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(p)}\b", text) for p in phrases)


def _should_use_gemini_viral_pipeline(prompt: str) -> bool:
    lowered = prompt.lower()
    return _has_phrase_or_word(lowered, VIRAL_FINDER_KEYWORDS)


def _parse_router_intent(raw_text: str) -> Optional[str]:
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL | re.IGNORECASE).strip().lower()
    if "viral_discovery" in cleaned:
        return "viral_discovery"
    if "timeline_edit" in cleaned:
        return "timeline_edit"
    return None


def _classify_prompt_intent_with_ai(prompt: str) -> Tuple[str, str]:
    """
    Hybrid 0ms-keyword + AI Intent Router with multi-model & cross-provider failover:
    - Instant keyword fast-path for unmistakable viral or edit prompts.
    - Otherwise asks the fast Groq Router chain (GPT OSS 20B -> Qwen 3.8 27B -> GPT OSS 120B -> backups),
      failing over to Gemini if Groq fails, then to a deterministic keyword heuristic.
    """
    lowered = prompt.lower()
    if _should_use_gemini_viral_pipeline(prompt):
        return "viral_discovery", "keyword-fast-path"

    if _has_phrase_or_word(lowered, FAST_EDIT_KEYWORDS) and not _has_phrase_or_word(
        lowered, DISCOVERY_HINT_WORDS
    ):
        return "timeline_edit", "keyword-fast-path"

    router_system_prompt = (
        "You are a fast intent router for a YouTube AI video editor.\n"
        "Classify the user's request into EXACTLY ONE label:\n"
        "- viral_discovery: finding, clipping, extracting, or isolating interesting/emotional/"
        "specific moments, stories, reactions, topics, or highlights from the video.\n"
        "- timeline_edit: trimming/cutting specific timestamps, removing pauses/silence, splitting, "
        "deleting clips, changing aspect ratio, or generating/styling captions.\n"
        "Reply with ONLY one token: viral_discovery or timeline_edit."
    )

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_key and groq_key != "your_groq_api_key_here":
        groq_client = Groq(api_key=groq_key)
        for idx, model_id in enumerate(get_router_models_chain()):
            try:
                completion = groq_client.chat.completions.create(
                    model=model_id,
                    messages=[
                        {"role": "system", "content": router_system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,
                    max_tokens=20,
                )
                msg = completion.choices[0].message
                raw_text = str(getattr(msg, "content", "") or "")
                intent = _parse_router_intent(raw_text)
                if intent:
                    label = f"{model_id} [backup]" if idx > 0 else model_id
                    return intent, label
            except Exception:
                continue

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key and gemini_key != "your_gemini_api_key_here":
        gemini_client = genai.Client(api_key=gemini_key)
        for idx, model_id in enumerate(get_gemini_models_chain()):
            try:
                response = gemini_client.models.generate_content(
                    model=model_id,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=router_system_prompt,
                        temperature=0.0,
                        max_output_tokens=20,
                    ),
                )
                intent = _parse_router_intent(str(getattr(response, "text", "") or ""))
                if intent:
                    label = f"{model_id} [backup]" if idx > 0 else f"{model_id} [gemini-backup]"
                    return intent, label
            except Exception:
                continue

    heuristic_words = DISCOVERY_HINT_WORDS + ("show",)
    if _has_phrase_or_word(lowered, heuristic_words):
        return "viral_discovery", "heuristic-fallback"
    return "timeline_edit", "heuristic-fallback"


def _extract_top_heatmap_peaks(project_state: ProjectState, top_k: int = 8) -> List[Dict[str, float]]:
    heatmap = project_state.sourceVideo.heatmap or []
    sorted_pts = sorted(heatmap, key=lambda h: h.value, reverse=True)
    peaks: List[Dict[str, float]] = []
    for pt in sorted_pts:
        # Avoid clustering all peaks within 8 seconds of each other
        if any(abs(pt.start_time - p["start_time"]) < 8.0 for p in peaks):
            continue
        peaks.append(
            {
                "start_time": round(pt.start_time, 2),
                "end_time": round(pt.end_time, 2),
                "intensity": round(pt.value, 3),
            }
        )
        if len(peaks) >= top_k:
            break
    return sorted(peaks, key=lambda x: x["start_time"])


def _format_transcript_for_llm(
    transcript: Optional[Dict[str, Any]],
    max_segments: int = 70,
    max_chars: int = 7500,
    prefer_start: bool = False,
) -> str:
    if not transcript:
        return "(No transcript cached)"
    segments = transcript.get("segments") or []
    if segments:
        if len(segments) > max_segments:
            if prefer_start:
                selected = segments[:max_segments]
            else:
                selected = [
                    segments[int(i * len(segments) / max_segments)]
                    for i in range(max_segments)
                ]
        else:
            selected = segments

        lines: List[str] = []
        total_chars = 0
        for seg in selected:
            s = round(float(seg.get("start", 0.0)), 2)
            e = round(float(seg.get("end", 0.0)), 2)
            txt = str(seg.get("text", "")).strip()[:140]
            line = f"[{s:.2f}s -> {e:.2f}s] {txt}"
            if lines and total_chars + len(line) + 1 > max_chars:
                break
            lines.append(line)
            total_chars += len(line) + 1
        return "\n".join(lines)

    words = transcript.get("words") or []
    word_parts: List[str] = []
    total_chars = 0
    for w in words[:180]:
        piece = f"{w.get('word')}({w.get('start')})"
        if word_parts and total_chars + len(piece) + 1 > max_chars:
            break
        word_parts.append(piece)
        total_chars += len(piece) + 1
    return " ".join(word_parts)


def _snap_to_word_boundaries(
    start: float,
    end: float,
    transcript: Optional[Dict[str, Any]],
    max_duration: float,
) -> Tuple[float, float]:
    """Snaps proposed clip boundaries to Groq Whisper word/sentence boundaries so speakers aren't cut mid-word."""
    s = max(0.0, float(start))
    e = min(max_duration, float(end))
    if not transcript:
        return round(s, 2), round(max(s + 1.0, e), 2)

    words = transcript.get("words") or []
    if not words:
        return round(s, 2), round(max(s + 1.0, e), 2)

    # Snap start to nearest word start within 1.2s
    best_start = min(words, key=lambda w: abs(float(w["start"]) - s))
    if abs(float(best_start["start"]) - s) <= 1.5:
        s = max(0.0, float(best_start["start"]) - 0.08)

    # Snap end to nearest word end within 1.5s
    best_end = min(words, key=lambda w: abs(float(w["end"]) - e))
    if abs(float(best_end["end"]) - e) <= 1.5:
        e = min(max_duration, float(best_end["end"]) + 0.15)

    if e <= s + 0.3:
        e = min(max_duration, max(float(end), s + 3.0))
    return round(s, 2), round(e, 2)


def _strip_json_fences(raw_text: str) -> str:
    text = raw_text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()
    if not text.startswith("{"):
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx > start_idx:
            text = text[start_idx : end_idx + 1]
    return text.strip()


def _normalize_isolated_clips(
    raw_clips: List[Dict[str, Any]],
    transcript: Optional[Dict[str, Any]],
    duration: float,
) -> List[Dict[str, Any]]:
    max_dur = duration if duration > 0 else 99999.0
    snapped_clips: List[Dict[str, Any]] = []
    for clip in raw_clips:
        s, e = _snap_to_word_boundaries(
            float(clip.get("source_start", 0.0)),
            float(clip.get("source_end", min(30.0, max_dur))),
            transcript,
            max_dur,
        )
        snapped_clips.append(
            {
                "source_start": s,
                "source_end": e,
                "title": str(clip.get("title") or "Viral Highlight"),
                "reason": str(clip.get("reason") or "High-engagement hook and payoff"),
                "virality_score": float(clip.get("virality_score") or 9.0),
            }
        )
    return snapped_clips


def _build_captions_for_isolated_or_range(
    transcript: Optional[Dict[str, Any]],
    isolated_clips: List[Dict[str, Any]],
    cap_range: Tuple[Optional[float], Optional[float]],
) -> Optional[List[CaptionItem]]:
    if not transcript:
        return None
    if isolated_clips:
        seen: set[Tuple[float, float]] = set()
        combined: List[CaptionItem] = []
        for clip in isolated_clips:
            for cap in build_captions_from_transcript(
                transcript, float(clip["source_start"]), float(clip["source_end"])
            ):
                key = (cap.start, cap.end)
                if key not in seen:
                    seen.add(key)
                    combined.append(cap)
        combined.sort(key=lambda c: c.start)
        return combined
    return build_captions_from_transcript(transcript, cap_range[0], cap_range[1])


def _filter_protected_caption_calls(
    tool_calls: List[ToolCallSchema], project_state: ProjectState, prompt: str
) -> List[ToolCallSchema]:
    """Enforces Manual Caption Override Protection on AI tool outputs."""
    protected_ids = {c.id for c in project_state.tracks.captions if c.isManuallyEdited}
    filtered: List[ToolCallSchema] = []
    for tc in tool_calls:
        if tc.name in {"edit_caption", "adjust_caption_timing", "delete_caption"}:
            cid = str(tc.arguments.get("caption_id", ""))
            if cid in protected_ids and cid not in prompt:
                continue
        elif tc.name == "combine_captions":
            cid1 = str(tc.arguments.get("caption_id_1", ""))
            cid2 = str(tc.arguments.get("caption_id_2", ""))
            if (cid1 in protected_ids and cid1 not in prompt) or (
                cid2 in protected_ids and cid2 not in prompt
            ):
                continue
        filtered.append(tc)
    return filtered


def _parse_viral_json_response(
    raw_text: str,
    project_state: ProjectState,
    transcript: Optional[Dict[str, Any]],
    prompt: str,
    duration: float,
    route_used: str,
) -> AiEditResponse:
    parsed = json.loads(_strip_json_fences(raw_text))
    explanation = str(
        parsed.get("explanation") or "Found highlight moment(s) and updated your timeline."
    )

    tool_calls: List[ToolCallSchema] = []
    wants_captions = False
    isolated_clips: List[Dict[str, Any]] = []
    cap_range: Tuple[Optional[float], Optional[float]] = (None, None)

    for raw_tc in parsed.get("tool_calls") or []:
        name = str(raw_tc.get("name", ""))
        args = dict(raw_tc.get("arguments") or {})
        if not name:
            continue
        if name == "isolate_highlight_clips" and isinstance(args.get("clips"), list):
            snapped_clips = _normalize_isolated_clips(args["clips"], transcript, duration)
            args["clips"] = snapped_clips
            isolated_clips.extend(snapped_clips)
        if name == "generate_auto_captions":
            wants_captions = True
            st_raw = args.get("start_time")
            et_raw = args.get("end_time")
            if st_raw is None or st_raw == "":
                args.pop("start_time", None)
                start_t = None
            else:
                start_t = float(st_raw)
            if et_raw is None or et_raw == "":
                args.pop("end_time", None)
                end_t = None
            else:
                end_t = float(et_raw)
            cap_range = (start_t, end_t)

        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name=name,
                arguments=args,
            )
        )

    tool_calls = _filter_protected_caption_calls(tool_calls, project_state, prompt)

    auto_captions: Optional[List[CaptionItem]] = (
        _build_captions_for_isolated_or_range(transcript, isolated_clips, cap_range)
        if wants_captions
        else None
    )

    return AiEditResponse(
        message=explanation,
        tool_calls=tool_calls,
        auto_captions=auto_captions,
        route_used=route_used,
    )


def _run_gemini_viral_pipeline(
    prompt: str,
    project_state: ProjectState,
    transcript: Optional[Dict[str, Any]],
) -> AiEditResponse:
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    has_gemini = bool(gemini_key and gemini_key != "your_gemini_api_key_here")
    has_groq = bool(groq_key and groq_key != "your_groq_api_key_here")

    lowered_prompt = prompt.lower()
    prefer_start = any(
        w in lowered_prompt for w in ("first", "beginning", "intro", "start", "opening")
    )
    heatmap_peaks = _extract_top_heatmap_peaks(project_state)
    transcript_text = _format_transcript_for_llm(
        transcript, max_segments=70, max_chars=7500, prefer_start=prefer_start
    )
    duration = project_state.sourceVideo.originalDuration

    if not has_gemini and not has_groq:
        return _deterministic_viral_fallback(prompt, project_state, transcript, heatmap_peaks)

    first_note = (
        "\nIMPORTANT: The user specifically asked for the FIRST matching moment — pick the earliest matching clip in the video timeline."
        if "first" in lowered_prompt
        else ""
    )

    system_instruction = (
        "You are an expert viral YouTube & Shorts Video Editor running the 3-Signal Viral Finder Pipeline.\n"
        "You have access to:\n"
        "1. Signal 1: YouTube 'Most Replayed' Heatmap peaks (where real viewers rewatched most).\n"
        "2. Signal 2: Word/sentence-level Groq Whisper transcript with exact timestamps.\n"
        "3. Signal 3: Direct multimodal video stream from the YouTube URL (when available).\n\n"
        "Your job:\n"
        "- Identify the funniest, highest-retention viral moment(s) with a STRONG HOOK (first 3s), "
        "CLEAR BUILDUP, and SATISFYING PAYOFF/PUNCHLINE.\n"
        "- Snap every clip's source_start and source_end strictly to clean sentence boundaries from the transcript "
        "(ideally 15-60 seconds per clip, never cutting mid-word).\n"
        "- Output valid JSON ONLY matching this exact schema:\n"
        "{\n"
        '  "explanation": "Concise human explanation with timestamps and virality score",\n'
        '  "tool_calls": [\n'
        '    {\n'
        '      "name": "isolate_highlight_clips",\n'
        '      "arguments": {\n'
        '        "clips": [{"source_start": 0.0, "source_end": 30.0, "title": "...", "reason": "...", "virality_score": 9.5}]\n'
        '      }\n'
        '    }\n'
        '  ]\n'
        "}\n"
        "Available tool names from the 13 Shared Tools:\n"
        + ", ".join(t["function"]["name"] for t in SHARED_TOOLS_DEFINITIONS)
        + "\nAlso include `change_video_format` ('9:16' if user asks for Shorts/Reels/TikTok/vertical), "
        "`generate_auto_captions` if captions are requested or helpful, and `change_caption_style` if styling is requested.\n"
        "CRITICAL: Never modify captions that have `isManuallyEdited: true`."
        + first_note
    )

    def _build_viral_context(peaks: List[Dict[str, float]], t_text: str) -> str:
        return (
            f"User Request: {prompt}\n"
            f"Video Title: {project_state.sourceVideo.title}\n"
            f"Original Duration: {duration:.2f}s\n"
            f"Current Aspect Ratio: {project_state.workspaceState.targetAspectRatio}\n\n"
            f"Signal 1 — Top YouTube Most Replayed Heatmap Peaks:\n{json.dumps(peaks, indent=2)}\n\n"
            f"Signal 2 — Timestamped Transcript:\n{t_text}\n"
        )

    user_context = _build_viral_context(heatmap_peaks, transcript_text)

    last_error: Optional[Exception] = None

    if has_gemini:
        client = genai.Client(api_key=gemini_key)
        contents: List[Any] = []
        yt_url = project_state.youtubeUrl.strip()
        if yt_url.startswith("http://") or yt_url.startswith("https://"):
            contents.append(types.Part.from_uri(file_uri=yt_url, mime_type="video/mp4"))
        contents.append(user_context)

        for idx, model_id in enumerate(get_gemini_models_chain()):
            try:
                response = client.models.generate_content(
                    model=model_id,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        temperature=0.3,
                    ),
                )
                route_label = f"{model_id} (3-signal)" if idx == 0 else f"{model_id} (backup)"
                return _parse_viral_json_response(
                    response.text or "",
                    project_state,
                    transcript,
                    prompt,
                    duration,
                    route_label,
                )
            except Exception as exc:
                last_error = exc

    if has_groq:
        groq_client = Groq(api_key=groq_key)
        compact_context = _build_viral_context(
            heatmap_peaks[:5],
            _format_transcript_for_llm(
                transcript, max_segments=35, max_chars=3500, prefer_start=prefer_start
            ),
        )
        active_context = user_context
        for model_id in get_tool_models_chain():
            for attempt in range(2):
                try:
                    completion = groq_client.chat.completions.create(
                        model=model_id,
                        messages=[
                            {"role": "system", "content": system_instruction},
                            {"role": "user", "content": active_context},
                        ],
                        temperature=0.2,
                    )
                    raw_text = (completion.choices[0].message.content or "").strip()
                    return _parse_viral_json_response(
                        raw_text,
                        project_state,
                        transcript,
                        prompt,
                        duration,
                        f"groq ({model_id}) [viral backup]",
                    )
                except Exception as exc:
                    last_error = exc
                    msg_low = str(exc).lower()
                    if attempt == 0 and ("413" in msg_low or "request_too_large" in msg_low or "too large" in msg_low):
                        active_context = compact_context
                        continue
                    break

    fallback = _deterministic_viral_fallback(prompt, project_state, transcript, heatmap_peaks)
    if last_error is not None:
        fallback.message = f"[AI fallback: {last_error}] {fallback.message}"
    return fallback


def _run_gemini_editor_fallback(
    prompt: str,
    editor_context: str,
    project_state: ProjectState,
    transcript: Optional[Dict[str, Any]],
) -> Optional[AiEditResponse]:
    """Cross-provider fallback when Groq is unconfigured or all Groq tool models fail."""
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key or gemini_key == "your_gemini_api_key_here":
        return None

    tool_specs = "\n".join(
        f"- {t['function']['name']}: {t['function'].get('description', '')} "
        f"params={json.dumps(t['function'].get('parameters', {}))}"
        for t in SHARED_TOOLS_DEFINITIONS
    )
    gemini_instruction = (
        f"{editor_context}\n\n"
        "Output VALID JSON ONLY (no markdown fences) matching:\n"
        '{"explanation": "brief summary", "tool_calls": [{"name": "<tool_name>", "arguments": {...}}]}\n\n'
        f"Available tools & schemas:\n{tool_specs}"
    )

    client = genai.Client(api_key=gemini_key)
    duration = project_state.sourceVideo.originalDuration
    for model_id in get_gemini_models_chain():
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=f"User Request: {prompt}",
                config=types.GenerateContentConfig(
                    system_instruction=gemini_instruction,
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            return _parse_viral_json_response(
                response.text or "",
                project_state,
                transcript,
                prompt,
                duration,
                f"gemini-editor-backup ({model_id})",
            )
        except Exception:
            continue
    return None


def _run_groq_fast_editor(
    prompt: str,
    project_state: ProjectState,
    transcript: Optional[Dict[str, Any]],
    history: List[Dict[str, str]],
) -> AiEditResponse:
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    has_groq = bool(groq_key and groq_key != "your_groq_api_key_here")
    lowered_prompt = prompt.lower()
    prefer_start = any(
        w in lowered_prompt for w in ("first", "beginning", "intro", "start", "opening")
    )
    pauses = detect_pause_gaps(transcript, min_gap=0.7) if transcript else []
    heatmap_peaks = _extract_top_heatmap_peaks(project_state)
    transcript_text = _format_transcript_for_llm(
        transcript, max_segments=70, max_chars=7500, prefer_start=prefer_start
    )

    protected_captions = [
        {"id": c.id, "start": c.start, "end": c.end, "text": c.text}
        for c in project_state.tracks.captions
        if c.isManuallyEdited
    ]
    editable_captions = [
        {"id": c.id, "start": c.start, "end": c.end, "text": c.text}
        for c in project_state.tracks.captions
        if not c.isManuallyEdited
    ][:25]

    first_note = (
        "\n7. IMPORTANT: The user specifically asked for the FIRST matching moment — pick the earliest matching clip in the video timeline."
        if "first" in lowered_prompt
        else ""
    )

    def _build_editor_system_prompt(pauses_slice: List[Dict[str, float]], t_text: str) -> str:
        return (
            "You are a fast, precise video editing assistant controlling a shared non-destructive EDL timeline.\n"
            "You MUST execute edits by calling the provided tools. Every tool maps directly to the user's manual UI.\n\n"
            "CRITICAL RULES:\n"
            "1. NEVER edit, adjust, or overwrite any caption listed under `protectedManuallyEditedCaptions` "
            "(`isManuallyEdited: true`), unless the user explicitly references that exact caption ID.\n"
            "2. When the user says 'remove pauses' or 'remove silence', call `remove_time_ranges` with the "
            "pre-computed `detectedPauseGaps (>0.7s)` provided in the context.\n"
            "3. When the user says 'cut first N seconds', call `remove_time_ranges` with `[{start: 0, end: N}]`.\n"
            "4. When the user changes caption style (e.g. 'bold yellow', 'karaoke', 'minimal'), call `change_caption_style`.\n"
            "5. When the user asks to find, clip, or isolate a moment, reaction, or topic, call "
            "`isolate_highlight_clips` snapped strictly to clean transcript boundaries using the Heatmap Peaks & Transcript below.\n"
            "6. Keep your final text response brief and clear."
            f"{first_note}\n\n"
            f"Current Project Context:\n"
            f"- Original Duration: {project_state.sourceVideo.originalDuration:.2f}s\n"
            f"- Playhead Position: {project_state.workspaceState.playheadPosition:.2f}s\n"
            f"- Selected Clip IDs: {project_state.workspaceState.selectedClipIds}\n"
            f"- Aspect Ratio: {project_state.workspaceState.targetAspectRatio}\n"
            f"- Active Clips: {json.dumps([c.model_dump() for c in project_state.tracks.clips[:25]])}\n"
            f"- Current Caption Style: {project_state.captionStyle.model_dump_json()}\n"
            f"- Protected Manually-Edited Captions (DO NOT OVERWRITE): {json.dumps(protected_captions[:25])}\n"
            f"- Auto Captions Sample: {json.dumps(editable_captions)}\n"
            f"- Pre-detected Silence/Pause Gaps (>0.7s): {json.dumps(pauses_slice)}\n"
            f"- YouTube Most Replayed Heatmap Peaks: {json.dumps(heatmap_peaks[:5])}\n"
            f"- Timestamped Transcript Segments:\n{t_text}\n"
        )

    system_prompt = _build_editor_system_prompt(pauses[:35], transcript_text)

    if not has_groq:
        gemini_backup = _run_gemini_editor_fallback(prompt, system_prompt, project_state, transcript)
        if gemini_backup is not None:
            return gemini_backup
        return _deterministic_edit_fallback(prompt, project_state, transcript, pauses)

    client = Groq(api_key=groq_key)

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for msg in history[-6:]:
        role = msg.get("role", "user")
        if role in {"user", "assistant"}:
            messages.append({"role": role, "content": str(msg.get("content", ""))})
    messages.append({"role": "user", "content": prompt})

    compact_system_prompt = _build_editor_system_prompt(
        pauses[:15],
        _format_transcript_for_llm(
            transcript, max_segments=35, max_chars=3500, prefer_start=prefer_start
        ),
    )

    tool_models = get_tool_models_chain()
    completion = None
    used_model = tool_models[0] if tool_models else "openai/gpt-oss-120b"
    used_backup = False
    last_exc: Optional[Exception] = None

    for idx, model_id in enumerate(tool_models):
        for attempt in range(2):
            try:
                completion = client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    tools=SHARED_TOOLS_DEFINITIONS,
                    tool_choice="auto",
                    temperature=0.1,
                )
                used_model = model_id
                used_backup = idx > 0
                break
            except Exception as exc:
                last_exc = exc
                msg_low = str(exc).lower()
                if attempt == 0 and ("413" in msg_low or "request_too_large" in msg_low or "too large" in msg_low):
                    messages[0] = {"role": "system", "content": compact_system_prompt}
                    continue
                break
        if completion is not None:
            break

    if completion is None:
        gemini_backup = _run_gemini_editor_fallback(prompt, system_prompt, project_state, transcript)
        if gemini_backup is not None:
            return gemini_backup
        assert last_exc is not None
        raise last_exc

    choice = completion.choices[0].message
    raw_tool_calls = choice.tool_calls or []

    tool_calls: List[ToolCallSchema] = []
    wants_captions = False
    isolated_clips: List[Dict[str, Any]] = []
    cap_range: Tuple[Optional[float], Optional[float]] = (None, None)
    duration = project_state.sourceVideo.originalDuration

    for tc in raw_tool_calls:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        name = tc.function.name
        if name == "isolate_highlight_clips" and isinstance(args.get("clips"), list):
            snapped_clips = _normalize_isolated_clips(args["clips"], transcript, duration)
            args["clips"] = snapped_clips
            isolated_clips.extend(snapped_clips)
        if name == "generate_auto_captions":
            wants_captions = True
            st_raw = args.get("start_time")
            et_raw = args.get("end_time")
            if st_raw is None or st_raw == "":
                args.pop("start_time", None)
                start_t = None
            else:
                start_t = float(st_raw)
            if et_raw is None or et_raw == "":
                args.pop("end_time", None)
                end_t = None
            else:
                end_t = float(et_raw)
            cap_range = (start_t, end_t)
        tool_calls.append(
            ToolCallSchema(
                id=tc.id or f"tc_{uuid.uuid4().hex[:8]}",
                name=name,
                arguments=args,
            )
        )

    tool_calls = _filter_protected_caption_calls(tool_calls, project_state, prompt)

    auto_captions: Optional[List[CaptionItem]] = (
        _build_captions_for_isolated_or_range(transcript, isolated_clips, cap_range)
        if wants_captions
        else None
    )

    explanation = (choice.content or "").strip()
    if not explanation:
        names = ", ".join(tc.name for tc in tool_calls)
        explanation = f"Applied timeline edits: {names}." if names else "No timeline changes were required."

    return AiEditResponse(
        message=explanation,
        tool_calls=tool_calls,
        auto_captions=auto_captions,
        route_used=f"groq ({used_model}) [backup]" if used_backup else f"groq ({used_model})",
    )


def _deterministic_viral_fallback(
    prompt: str,
    project_state: ProjectState,
    transcript: Optional[Dict[str, Any]],
    heatmap_peaks: List[Dict[str, float]],
) -> AiEditResponse:
    """Runs the 3-Signal heuristic locally when GEMINI_API_KEY is not set."""
    lowered = prompt.lower()
    duration = max(1.0, project_state.sourceVideo.originalDuration)
    count = 3 if ("top 3" in lowered or "3 " in lowered) else 1

    clips_payload: List[Dict[str, Any]] = []
    peaks = heatmap_peaks or [{"start_time": min(10.0, duration * 0.25), "end_time": min(40.0, duration), "intensity": 0.9}]

    for idx, pk in enumerate(sorted(peaks, key=lambda x: x["intensity"], reverse=True)[:count]):
        center = (pk["start_time"] + pk["end_time"]) / 2.0
        raw_start = max(0.0, center - 12.0)
        raw_end = min(duration, raw_start + 28.0)
        s, e = _snap_to_word_boundaries(raw_start, raw_end, transcript, duration)
        clips_payload.append(
            {
                "source_start": s,
                "source_end": e,
                "title": f"Viral Peak #{idx + 1}",
                "reason": f"YouTube Most Replayed peak (intensity {pk['intensity']:.2f}) snapped to sentence boundaries.",
                "virality_score": round(min(9.9, 7.8 + pk["intensity"] * 2.0), 1),
            }
        )

    clips_payload.sort(key=lambda c: c["source_start"])
    tool_calls: List[ToolCallSchema] = [
        ToolCallSchema(
            id=f"tc_{uuid.uuid4().hex[:8]}",
            name="isolate_highlight_clips",
            arguments={"clips": clips_payload},
        )
    ]

    if any(k in lowered for k in ("short", "vertical", "9:16", "tiktok", "reel")):
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="change_video_format",
                arguments={"aspect_ratio": "9:16"},
            )
        )

    auto_captions: Optional[List[CaptionItem]] = None
    if "caption" in lowered or "karaoke" in lowered or "short" in lowered:
        s_min = min(c["source_start"] for c in clips_payload)
        e_max = max(c["source_end"] for c in clips_payload)
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="generate_auto_captions",
                arguments={"start_time": s_min, "end_time": e_max},
            )
        )
        if transcript:
            auto_captions = _build_captions_for_isolated_or_range(
                transcript, clips_payload, (s_min, e_max)
            )

        if "karaoke" in lowered:
            tool_calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="change_caption_style",
                    arguments={"preset": "karaoke", "highlight_color": "#FACC15", "is_bold": True},
                )
            )

    summary = ", ".join(
        f"{c['title']} ({c['source_start']:.1f}s - {c['source_end']:.1f}s, Score {c['virality_score']}/10)"
        for c in clips_payload
    )
    return AiEditResponse(
        message=(
            f"Found {len(clips_payload)} highlight moment(s) using Heatmap + Transcript analysis: {summary}. "
            "(Set GEMINI_API_KEY in backend/.env to enable cloud multimodal video understanding.)"
        ),
        tool_calls=tool_calls,
        auto_captions=auto_captions,
        route_used="local-signal-fallback",
    )


def _deterministic_edit_fallback(
    prompt: str,
    project_state: ProjectState,
    transcript: Optional[Dict[str, Any]],
    pauses: List[Dict[str, float]],
) -> AiEditResponse:
    """Deterministic command parser fallback when GROQ_API_KEY is not configured."""
    lowered = prompt.lower()
    tool_calls: List[ToolCallSchema] = []
    auto_captions: Optional[List[CaptionItem]] = None
    notes: List[str] = []

    first_n = re.search(r"(?:cut|remove|trim)\s+(?:the\s+)?first\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)", lowered)
    if first_n:
        secs = float(first_n.group(1))
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="remove_time_ranges",
                arguments={"ranges": [{"start": 0.0, "end": secs}]},
            )
        )
        notes.append(f"Removed first {secs}s")

    if "pause" in lowered or "silence" in lowered or "dead air" in lowered:
        if pauses:
            tool_calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="remove_time_ranges",
                    arguments={"ranges": pauses},
                )
            )
            notes.append(f"Removed {len(pauses)} pause gap(s) > 0.7s")
        else:
            notes.append("No pause gaps > 0.7s were detected")

    if "9:16" in lowered or "vertical" in lowered or "short" in lowered or "tiktok" in lowered:
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="change_video_format",
                arguments={"aspect_ratio": "9:16"},
            )
        )
        notes.append("Switched format to 9:16")
    elif "1:1" in lowered or "square" in lowered:
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="change_video_format",
                arguments={"aspect_ratio": "1:1"},
            )
        )
        notes.append("Switched format to 1:1")
    elif "16:9" in lowered or "landscape" in lowered:
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="change_video_format",
                arguments={"aspect_ratio": "16:9"},
            )
        )
        notes.append("Switched format to 16:9")

    if "split" in lowered and project_state.tracks.clips:
        playhead = project_state.workspaceState.playheadPosition
        target = next(
            (
                c
                for c in project_state.tracks.clips
                if c.timelineStart < playhead < c.timelineStart + c.duration
            ),
            project_state.tracks.clips[0],
        )
        split_ts = playhead if target.timelineStart < playhead < target.timelineStart + target.duration else target.timelineStart + target.duration / 2.0
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="split_clip",
                arguments={"clip_id": target.id, "split_timestamp": round(split_ts, 2)},
            )
        )
        notes.append(f"Split clip at {split_ts:.2f}s")

    style_args: Dict[str, Any] = {}
    if "karaoke" in lowered:
        style_args["preset"] = "karaoke"
    elif "bold social" in lowered or "uppercase" in lowered:
        style_args["preset"] = "bold_social"
    elif "minimal" in lowered:
        style_args["preset"] = "minimal"
    elif "yellow" in lowered and "highlight" in lowered:
        style_args["preset"] = "yellow_highlight"

    if "yellow" in lowered or "gold" in lowered:
        style_args["highlight_color"] = "#FACC15"
        if "text" in lowered or "font" in lowered or "caption" in lowered:
            style_args["text_color"] = "#FACC15"
    if "bold" in lowered:
        style_args["is_bold"] = True
    if "top" in lowered:
        style_args["position"] = "top"
    elif "middle" in lowered or "center" in lowered:
        style_args["position"] = "middle"
    elif "bottom" in lowered:
        style_args["position"] = "bottom"

    if style_args:
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="change_caption_style",
                arguments=style_args,
            )
        )
        notes.append("Updated caption styling")

    if "auto caption" in lowered or "generate caption" in lowered or "add caption" in lowered:
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="generate_auto_captions",
                arguments={},
            )
        )
        if transcript:
            auto_captions = build_captions_from_transcript(transcript)
        notes.append("Generated auto captions (preserving manually edited captions)")

    message = (
        "; ".join(notes) + "."
        if notes
        else "Could not match a local edit rule without GROQ_API_KEY. Please configure GROQ_API_KEY in backend/.env for natural-language tool calling."
    )
    return AiEditResponse(
        message=message,
        tool_calls=tool_calls,
        auto_captions=auto_captions,
        route_used="local-rule-fallback",
    )


def process_ai_edit(
    prompt: str,
    project_state: ProjectState,
    transcripts_dir: Path,
    history: Optional[List[Dict[str, str]]] = None,
) -> AiEditResponse:
    transcript = load_cached_transcript(project_state.projectId, transcripts_dir)
    intent, router_used = _classify_prompt_intent_with_ai(prompt)

    def _with_router_tag(res: AiEditResponse) -> AiEditResponse:
        if router_used != "keyword-fast-path":
            res.route_used = f"{res.route_used} [router: {router_used}]"
        return res

    if intent == "viral_discovery":
        try:
            return _with_router_tag(_run_gemini_viral_pipeline(prompt, project_state, transcript))
        except Exception as exc:
            fallback = _deterministic_viral_fallback(
                prompt, project_state, transcript, _extract_top_heatmap_peaks(project_state)
            )
            fallback.message = f"[Gemini fallback: {exc}] {fallback.message}"
            return _with_router_tag(fallback)

    try:
        return _with_router_tag(_run_groq_fast_editor(prompt, project_state, transcript, history or []))
    except Exception as exc:
        pauses = detect_pause_gaps(transcript, min_gap=0.7) if transcript else []
        fallback = _deterministic_edit_fallback(prompt, project_state, transcript, pauses)
        fallback.message = f"[Groq fallback: {exc}] {fallback.message}"
        return _with_router_tag(fallback)
