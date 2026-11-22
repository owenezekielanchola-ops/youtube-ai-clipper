import bisect
import json
import os
import re
import sys
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
    SfxAsset,
    ToolCallSchema,
)
from services.model_registry import (
    get_gemini_models_chain,
    get_router_models_chain,
    get_tool_models_chain,
    report_model_failure,
)
from services.reference_style import clone_reference_style_for_project
from services.sfx_library import find_best_matching_sfx, list_sfx_assets
from services.transcription import (
    build_captions_from_transcript,
    detect_pause_gaps,
    load_cached_transcript,
)
from services.visual_planner import (
    convert_visual_plan_to_tool_calls,
    plan_visual_edits_with_gemini,
    should_use_visual_planner,
)
from services.youtube_ingest import parse_explicit_clip_range

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

CAPCUT_EFFECT_KEYWORDS = (
    "zoom",
    "zoom in",
    "zoom out",
    "punch in",
    "punch-in",
    "draw",
    "arrow",
    "circle",
    "rectangle",
    "highlight box",
    "speed",
    "speed up",
    "slow down",
    "slow motion",
    "slow-mo",
    "fast forward",
    "sound effect",
    "sound effects",
    "audio effect",
    "audio effects",
    "sfx",
    "mp3",
    "crop",
    "crop to",
    "uncrop",
    "reset crop",
    "what are they doing",
    "what's happening",
    "whats happening",
    "change caption",
    "caption color",
    "captions color",
    "text color",
    "highlight color",
    "caption to",
    "captions to",
    "make caption",
    "make captions",
    "subtitle color",
    "font size",
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
    "caption",
    "captions",
    "subtitle",
    "subtitles",
    "color",
    "text color",
    "highlight color",
    "font",
    "karaoke",
    "yellow",
    "black",
    "white",
    "red",
    "blue",
    "green",
    "pink",
    "purple",
    "orange",
    "cyan",
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
) + CAPCUT_EFFECT_KEYWORDS

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
    explicit_clip_discovery = any(
        p in lowered
        for p in (
            "top 3",
            "top 2",
            "isolate",
            "clip the",
            "make a short",
            "create a short",
        )
    )
    if _has_phrase_or_word(lowered, CAPCUT_EFFECT_KEYWORDS) and not explicit_clip_discovery:
        return "timeline_edit", "keyword-fast-path"

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
        "specific moments, stories, reactions, topics, or highlights from the full video.\n"
        "- timeline_edit: trimming/cutting specific timestamps, removing pauses/silence, splitting, "
        "deleting clips, changing aspect ratio, generating/styling captions, zoom effects "
        "(zoom in/out, punch-in, removing zoom), cropping video frame (full clip or timed crop for a while), "
        "drawing lines/arrows/circles/highlights, changing playback speed, "
        "or adding/removing sound effects (SFX).\n"
        "Reply with ONLY one token: viral_discovery or timeline_edit."
    )

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_key and groq_key != "your_groq_api_key_here":
        groq_client = Groq(api_key=groq_key, timeout=15.0)
        for idx, model_id in enumerate(get_router_models_chain()[:3]):
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
            except Exception as exc:
                report_model_failure("groq", model_id, exc)
                continue

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key and gemini_key != "your_gemini_api_key_here":
        gemini_client = genai.Client(api_key=gemini_key)
        for idx, model_id in enumerate(get_gemini_models_chain()[:2]):
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
            except Exception as exc:
                report_model_failure("gemini", model_id, exc)
                continue

    heuristic_words = DISCOVERY_HINT_WORDS + ("show",)
    if _has_phrase_or_word(lowered, heuristic_words):
        return "viral_discovery", "heuristic-fallback"
    return "timeline_edit", "heuristic-fallback"


def _extract_top_heatmap_peaks(project_state: ProjectState, top_k: int = 8) -> List[Dict[str, float]]:
    heatmap = project_state.sourceVideo.heatmap or []
    duration = float(project_state.sourceVideo.originalDuration or 0.0)
    min_sep = max(8.0, min(180.0, duration * 0.015))
    sorted_pts = sorted(heatmap, key=lambda h: h.value, reverse=True)
    peaks: List[Dict[str, float]] = []
    for pt in sorted_pts:
        # Avoid clustering all peaks right next to each other (scales with VOD duration)
        if any(abs(pt.start_time - p["start_time"]) < min_sep for p in peaks):
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


_LONG_VOD_STOPWORDS = {
    "find",
    "only",
    "the",
    "part",
    "most",
    "clip",
    "video",
    "stream",
    "live",
    "interesting",
    "best",
    "funny",
    "moment",
    "moments",
    "make",
    "short",
    "shorts",
    "highlight",
    "highlights",
    "please",
    "show",
    "give",
    "from",
    "with",
    "this",
    "that",
    "about",
    "what",
    "where",
    "when",
    "just",
    "some",
    "good",
    "cool",
    "nice",
    "awesome",
    "amazing",
    "viral",
    "youtube",
    "hours",
    "hour",
    "something",
    "like",
    "want",
    "need",
    "extract",
    "pick",
    "grab",
    "create",
    "into",
    "really",
    "very",
}

_REACTION_ENERGY_MARKERS = (
    "haha",
    "lmao",
    "lmfao",
    "omg",
    "oh my god",
    "wtf",
    "no way",
    "wait",
    "hold on",
    "insane",
    "crazy",
    "hilarious",
    "shut up",
    "are you kidding",
    "holy",
    "let's go",
    "lets go",
    "unbelievable",
    "plot twist",
    "secret",
    "banned",
    "crying",
    "screaming",
    "laughing",
    "what the",
    "bro",
    "dude",
    "yo",
)


def _bisect_dict_start(items: List[Dict[str, Any]], target: float) -> int:
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


def _format_hms_short(sec: float) -> str:
    total = max(0, int(round(sec)))
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs}h{mins:02d}m"
    return f"{mins}m{secs:02d}s"


def _extract_prompt_keywords(prompt: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9]+", (prompt or "").lower())
    keywords: List[str] = []
    seen: set[str] = set()
    for tok in tokens:
        if len(tok) < 3 or tok in _LONG_VOD_STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        keywords.append(tok)
        if len(keywords) >= 12:
            break
    return keywords


def _score_segment_interest(text: str, prompt_keywords: List[str]) -> float:
    if not text:
        return 0.0
    low = text.lower()
    score = 0.0
    for kw in prompt_keywords:
        if (len(kw) >= 4 and kw in low) or re.search(rf"\b{re.escape(kw)}\b", low):
            score += 4.0
    reaction_bonus = 0.0
    for marker in _REACTION_ENERGY_MARKERS:
        if marker in low:
            reaction_bonus += 1.8
            if reaction_bonus >= 5.4:
                break
    score += min(5.4, reaction_bonus)
    if "!" in text or "?" in text:
        score += 1.0
    if re.search(r"\b[A-Z]{3,}\b", text):
        score += 0.8
    return score


def _extract_targeted_transcript_windows(
    transcript: Optional[Dict[str, Any]],
    heatmap_peaks: List[Dict[str, float]],
    prompt: str,
    chapters: Optional[List[Dict[str, Any]]] = None,
    max_chars: int = 7500,
    prefer_start: bool = False,
) -> str:
    if not transcript:
        return "(No transcript cached)"
    segments = transcript.get("segments") or []
    if not segments or len(segments) <= 100 or prefer_start:
        return _format_transcript_for_llm(
            transcript, max_segments=70, max_chars=max_chars, prefer_start=prefer_start
        )

    seg_starts = [float(s.get("start", 0.0)) for s in segments]
    prompt_keywords = _extract_prompt_keywords(prompt)
    candidates: List[Tuple[float, float, float, str]] = []
    chosen_centers: List[float] = []

    # 1. Chapter matches (if prompt keywords match a YouTube chapter title)
    if chapters and prompt_keywords:
        matched_chapters = 0
        for ch in chapters:
            if not isinstance(ch, dict):
                continue
            ch_title = str(ch.get("title") or "")
            ch_low = ch_title.lower()
            if not any(kw in ch_low for kw in prompt_keywords):
                continue
            ch_s = float(ch.get("start_time", 0.0))
            ch_e = float(ch.get("end_time", ch_s + 30.0))
            lo = bisect.bisect_left(seg_starts, max(0.0, ch_s - 2.0))
            hi = bisect.bisect_right(seg_starts, ch_e)
            best_score = 0.0
            best_t = min(ch_e, ch_s + 10.0)
            for s_item in segments[lo:hi]:
                sc = _score_segment_interest(str(s_item.get("text", "")), prompt_keywords)
                if sc > best_score:
                    best_score = sc
                    best_t = float(s_item.get("start", best_t))
            candidates.append(
                (12.0 + best_score, max(0.0, best_t - 20.0), best_t + 25.0, f"Chapter: {ch_title[:32]}")
            )
            chosen_centers.append(best_t)
            matched_chapters += 1
            if matched_chapters >= 3:
                break

    # 2. Heatmap peak context windows (scan wide heatmap buckets via bisect to find exact peak sentence)
    top_peaks = sorted(
        heatmap_peaks or [], key=lambda p: float(p.get("intensity", 0.0)), reverse=True
    )[:6]
    for pk in top_peaks:
        hs = float(pk.get("start_time", 0.0))
        he = float(pk.get("end_time", hs))
        center = (hs + max(hs, he)) * 0.5
        best_seg_score = 0.0
        target_t = center
        if he - hs > 90.0:
            lo = bisect.bisect_left(seg_starts, max(0.0, hs - 5.0))
            hi = bisect.bisect_right(seg_starts, he + 5.0)
            best_key = (-1.0, -float("inf"))
            for s_item in segments[lo:hi]:
                seg_t = float(s_item.get("start", center))
                sc = _score_segment_interest(str(s_item.get("text", "")), prompt_keywords)
                cand_key = (sc, -abs(seg_t - center))
                if cand_key > best_key:
                    best_key = cand_key
                    best_seg_score = sc
                    target_t = seg_t
        candidates.append(
            (
                6.0 + float(pk.get("intensity", 0.5)) * 4.0 + best_seg_score,
                max(0.0, target_t - 25.0),
                target_t + 25.0,
                f"Peak {float(pk.get('intensity', 0.0)):.2f}",
            )
        )
        chosen_centers.append(target_t)

    # 3. Single O(N) global pass for prompt keywords & excitement markers across 24h
    scored_hits: List[Tuple[float, float]] = []
    for s_item in segments:
        sc = _score_segment_interest(str(s_item.get("text", "")), prompt_keywords)
        if sc > 0.0:
            scored_hits.append((sc, float(s_item.get("start", 0.0))))
    scored_hits.sort(key=lambda item: item[0], reverse=True)
    added_keyword_windows = 0
    for sc, hit_t in scored_hits:
        if any(abs(hit_t - c) < 60.0 for c in chosen_centers):
            continue
        candidates.append(
            (4.0 + sc, max(0.0, hit_t - 20.0), hit_t + 25.0, "Keyword/Energy")
        )
        chosen_centers.append(hit_t)
        added_keyword_windows += 1
        if added_keyword_windows >= 4:
            break

    # 4. Distributed fallback if neither heatmap nor keyword hits existed
    if not candidates:
        min_t = seg_starts[0]
        max_t = float(segments[-1].get("end", seg_starts[-1]))
        span = max(60.0, max_t - min_t)
        for idx, frac in enumerate((0.10, 0.30, 0.50, 0.70, 0.90)):
            anchor = min_t + span * frac
            candidates.append(
                (1.0 - idx * 0.05, max(0.0, anchor - 20.0), anchor + 20.0, f"Sample {int(frac * 100)}%")
            )

    # Merge overlapping or near-adjacent windows (< 10s gap), preserving highest-priority label
    candidates.sort(key=lambda w: w[1])
    merged: List[Tuple[float, float, float, str]] = [candidates[0]]
    for prio, ws, we, label in candidates[1:]:
        p_prio, p_ws, p_we, p_label = merged[-1]
        if ws <= p_we + 10.0:
            best_prio = max(p_prio, prio)
            best_label = p_label if p_prio >= prio else label
            merged[-1] = (best_prio, p_ws, max(p_we, we), best_label)
        else:
            merged.append((prio, ws, we, label))

    # Emit highest-priority windows first so LLM token budget captures best matches cleanly
    merged.sort(key=lambda w: w[0], reverse=True)
    lines: List[str] = []
    total_chars = 0
    seen_seg_indices: set[int] = set()

    for _prio, ws, we, label in merged:
        lo = bisect.bisect_left(seg_starts, max(0.0, ws - 2.0))
        hi = bisect.bisect_right(seg_starts, we)
        window_lines: List[str] = []
        for idx in range(lo, hi):
            if idx in seen_seg_indices:
                continue
            s_item = segments[idx]
            s_t = round(float(s_item.get("start", 0.0)), 2)
            e_t = round(float(s_item.get("end", 0.0)), 2)
            if e_t < ws or s_t > we:
                continue
            txt = str(s_item.get("text", "")).strip()[:140]
            if txt:
                seen_seg_indices.add(idx)
                window_lines.append(f"[{s_t:.2f}s -> {e_t:.2f}s] {txt}")
        if not window_lines:
            continue
        header = f"--- Window [{ws:.1f}s -> {we:.1f}s] ({_format_hms_short(ws)} | {label}) ---"
        if lines and total_chars + len(header) + len(window_lines[0]) + 2 > max_chars:
            break
        lines.append(header)
        total_chars += len(header) + 1
        budget_full = False
        for wline in window_lines:
            if total_chars + len(wline) + 1 > max_chars:
                budget_full = True
                break
            lines.append(wline)
            total_chars += len(wline) + 1
        if budget_full:
            break

    return "\n".join(lines) if lines else _format_transcript_for_llm(
        transcript, max_segments=70, max_chars=max_chars, prefer_start=prefer_start
    )


def _source_time_to_timeline(src_time: float, clips: List[Any]) -> Optional[float]:
    if not clips:
        return round(max(0.0, float(src_time)), 3)
    for clip in clips:
        if clip.sourceStart - 0.15 <= src_time <= clip.sourceEnd + 0.15:
            speed = max(0.25, min(4.0, float(getattr(clip, "speed", 1.0) or 1.0)))
            clamped = max(clip.sourceStart, min(clip.sourceEnd, src_time))
            return round(clip.timelineStart + (clamped - clip.sourceStart) / speed, 3)
    return None


def _format_timeline_transcript_for_llm(
    transcript: Optional[Dict[str, Any]],
    clips: List[Any],
    max_segments: int = 60,
    max_chars: int = 6500,
    prefer_start: bool = False,
) -> str:
    if not transcript or not clips:
        return _format_transcript_for_llm(
            transcript, max_segments=max_segments, max_chars=max_chars, prefer_start=prefer_start
        )

    segments = transcript.get("segments") or []
    mapped_lines: List[str] = []
    for seg in segments:
        src_s = float(seg.get("start", 0.0))
        src_e = float(seg.get("end", src_s + 1.0))
        txt = str(seg.get("text", "")).strip()[:140]
        if not txt:
            continue
        for clip in clips:
            if min(src_e, clip.sourceEnd) <= max(src_s, clip.sourceStart) + 0.05:
                continue
            speed = max(0.25, min(4.0, float(getattr(clip, "speed", 1.0) or 1.0)))
            ov_s = max(src_s, clip.sourceStart)
            ov_e = min(src_e, clip.sourceEnd)
            tl_s = clip.timelineStart + (ov_s - clip.sourceStart) / speed
            tl_e = clip.timelineStart + (ov_e - clip.sourceStart) / speed
            mapped_lines.append(
                f"[TL {tl_s:.2f}s -> {tl_e:.2f}s | SRC {ov_s:.2f}s -> {ov_e:.2f}s] {txt}"
            )
            break

    if not mapped_lines:
        return _format_transcript_for_llm(
            transcript, max_segments=max_segments, max_chars=max_chars, prefer_start=prefer_start
        )

    if len(mapped_lines) > max_segments:
        if prefer_start:
            mapped_lines = mapped_lines[:max_segments]
        else:
            mapped_lines = [
                mapped_lines[int(i * len(mapped_lines) / max_segments)]
                for i in range(max_segments)
            ]

    out_lines: List[str] = []
    total_chars = 0
    for line in mapped_lines:
        if out_lines and total_chars + len(line) + 1 > max_chars:
            break
        out_lines.append(line)
        total_chars += len(line) + 1
    return "\n".join(out_lines)


def _find_phrase_timeline_window(
    phrase: str,
    transcript: Optional[Dict[str, Any]],
    clips: List[Any],
) -> Optional[Tuple[float, float]]:
    if not transcript or not phrase:
        return None

    clean_phrase = re.sub(r"[^\w'\s]+", " ", phrase.lower()).strip()
    phrase_tokens = [tok for tok in clean_phrase.split() if tok]
    if not phrase_tokens:
        return None

    matched_src: Optional[Tuple[float, float]] = None

    # 1. Exact/near consecutive word sequence match in word timestamps
    words = transcript.get("words") or []
    if words:
        norm_words = [
            re.sub(r"[^\w']+", "", str(w.get("word", "")).lower())
            for w in words
        ]
        n = len(phrase_tokens)
        if n <= len(norm_words):
            for idx in range(len(norm_words) - n + 1):
                if norm_words[idx : idx + n] == phrase_tokens:
                    ws = float(words[idx].get("start", 0.0))
                    we = float(words[idx + n - 1].get("end", ws + 0.8))
                    # Prefer match inside active clips if one exists
                    if not clips or _source_time_to_timeline((ws + we) / 2.0, clips) is not None:
                        matched_src = (ws, we)
                        break
                    if matched_src is None:
                        matched_src = (ws, we)

    # 2. Fallback to segment substring search
    if matched_src is None:
        segments = transcript.get("segments") or []
        for seg in segments:
            seg_norm = re.sub(r"[^\w'\s]+", " ", str(seg.get("text", "")).lower()).strip()
            if clean_phrase in seg_norm:
                ss = float(seg.get("start", 0.0))
                se = float(seg.get("end", ss + 1.5))
                if not clips or _source_time_to_timeline((ss + se) / 2.0, clips) is not None:
                    matched_src = (ss, se)
                    break
                if matched_src is None:
                    matched_src = (ss, se)

    if matched_src is None:
        return None

    src_s, src_e = matched_src
    if not clips:
        return round(src_s, 2), round(max(src_s + 0.5, src_e), 2)

    for clip in clips:
        if min(src_e, clip.sourceEnd) > max(src_s, clip.sourceStart) + 0.05:
            speed = max(0.25, min(4.0, float(getattr(clip, "speed", 1.0) or 1.0)))
            cs = max(clip.sourceStart, src_s)
            ce = min(clip.sourceEnd, src_e)
            tl_s = clip.timelineStart + (cs - clip.sourceStart) / speed
            tl_e = clip.timelineStart + max(0.3, ce - clip.sourceStart) / speed
            return round(tl_s, 2), round(tl_e, 2)

    return None


def _enrich_sfx_tool_calls(
    tool_calls: List[ToolCallSchema], sfx_assets: List[SfxAsset]
) -> List[ToolCallSchema]:
    if not sfx_assets:
        return tool_calls

    enriched: List[ToolCallSchema] = []
    for tc in tool_calls:
        if tc.name == "add_sfx_clip":
            args = dict(tc.arguments or {})
            asset_id = str(args.get("asset_id") or "").strip()
            query = str(args.get("sfx_name") or args.get("name") or "").strip()

            chosen: Optional[SfxAsset] = next(
                (a for a in sfx_assets if a.id == asset_id), None
            )
            if chosen is None:
                chosen = find_best_matching_sfx(sfx_assets, query)

            if chosen is not None:
                args["asset_id"] = chosen.id
                args["sfx_name"] = chosen.name
                args["name"] = chosen.name
                args["url"] = chosen.url
                raw_trim = args.get("trim_start") if args.get("trim_start") is not None else args.get("trimStart")
                trim_val = 0.0
                if raw_trim is not None:
                    try:
                        trim_val = max(0.0, min(max(0.0, chosen.duration - 0.15), float(raw_trim)))
                    except (TypeError, ValueError):
                        trim_val = 0.0
                if trim_val > 0 or "trim_start" in args or "trimStart" in args:
                    args["trim_start"] = round(trim_val, 3)
                max_avail = max(0.15, chosen.duration - trim_val)
                args["duration"] = round(
                    max(0.15, min(max_avail, float(args.get("duration") or max_avail))),
                    3,
                )
                try:
                    vol = float(args.get("volume", 0.85))
                except (TypeError, ValueError):
                    vol = 0.85
                args["volume"] = round(max(0.0, min(2.0, vol)), 2)

            enriched.append(
                ToolCallSchema(
                    id=tc.id,
                    name=tc.name,
                    arguments=args,
                    reason=tc.reason,
                )
            )
        else:
            enriched.append(tc)
    return enriched


def _box_from_focus(
    fx: float, fy: float, scale: float = 1.65
) -> Dict[str, float]:
    sc = max(1.15, min(3.0, float(scale or 1.65)))
    w = round(max(0.28, min(0.65, 1.0 / sc)), 3)
    h = round(max(0.32, min(0.70, 1.0 / sc)), 3)
    x = round(max(0.0, min(1.0 - w, float(fx) - w / 2.0)), 3)
    y = round(max(0.0, min(1.0 - h, float(fy) - h / 2.0)), 3)
    return {"x": x, "y": y, "w": w, "h": h}


def _enforce_crop_and_zoom_intent(
    prompt: str,
    tool_calls: List[ToolCallSchema],
    project_state: ProjectState,
    visual_plan: Optional[Dict[str, Any]] = None,
    transcript: Optional[Dict[str, Any]] = None,
) -> Tuple[List[ToolCallSchema], Optional[str]]:
    """
    Guarantees that:
    1. When the user asks to remove/replace a zoom, any erroneous `add_zoom_effect` is stripped
       and `delete_zoom_effect` is emitted for active zoom(s).
    2. When the user asks to CROP (including timed crops 'at the funny part / only for a while'),
       any mistaken `add_zoom_effect` is converted into `set_video_crop(x, y, w, h, start?, end?)`,
       and if `set_video_crop` was omitted after `delete_zoom_effect`, it is synthesized from
       the visual plan, replaced zoom, or prompt heuristics.
    """
    lowered = prompt.lower()
    wants_remove_zoom = bool(
        re.search(
            r"\b(remove|delete|clear|no|without|replace|stop|take\s+out|get\s+rid\s+of)\b.{0,30}\bzoom\b",
            lowered,
        )
        or (
            "replace it" in lowered
            and "crop" in lowered
            and len(project_state.tracks.zooms or []) > 0
        )
    )
    wants_reset_crop = any(
        k in lowered
        for k in ("reset crop", "uncrop", "full frame", "remove crop", "clear crop", "remove the crop")
    )
    wants_crop = ("crop" in lowered) and not wants_reset_crop
    wants_add_zoom = (
        any(k in lowered for k in ("zoom", "punch in", "punch-in"))
        and not wants_remove_zoom
    )
    explicit_range_match = re.search(
        r"\b(?:from|at|between)\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)?\s*(?:to|and|-)\s*(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)?\b",
        lowered,
    ) or re.search(
        r"\b(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)\s*(?:to|-)\s*(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)?\b",
        lowered,
    )
    explicit_for_dur_match = re.search(
        r"\bfor\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)\b",
        lowered,
    )
    wants_timed_crop = wants_crop and (
        bool(explicit_range_match or explicit_for_dur_match)
        or any(
            p in lowered
            for p in (
                "for a while",
                "only for",
                "a while",
                "temporarily",
                "briefly",
                "short while",
                "few seconds",
                "at the funny",
                "in the funny",
                "funny part",
                "funny moment",
                "when he",
                "when she",
                "when they",
                "during the",
                "at the reaction",
                "during reaction",
                "replace it",
                "replace the zoom",
            )
        )
    )

    if not wants_remove_zoom and not wants_crop and not wants_reset_crop:
        return tool_calls, None

    clips = project_state.tracks.clips or []
    total_tl_dur = max(
        0.5,
        sum(float(c.duration) for c in clips)
        if clips
        else float(project_state.sourceVideo.originalDuration or 10.0),
    )
    playhead = max(0.0, float(project_state.workspaceState.playheadPosition or 0.0))
    active_zooms = list(project_state.tracks.zooms or [])

    intercepted_zoom_args: Optional[Dict[str, Any]] = None
    filtered_calls: List[ToolCallSchema] = []
    for tc in tool_calls:
        if tc.name == "add_zoom_effect" and (wants_remove_zoom or (wants_crop and not wants_add_zoom)):
            intercepted_zoom_args = dict(tc.arguments or {})
            continue
        filtered_calls.append(tc)

    added_note_parts: List[str] = []

    # 1. Ensure delete_zoom_effect is called if user asked to remove/replace zoom
    deleted_zoom_obj = None
    if wants_remove_zoom and active_zooms:
        existing_del_ids = {
            str((tc.arguments or {}).get("zoom_id") or "")
            for tc in filtered_calls
            if tc.name == "delete_zoom_effect"
        }
        if not existing_del_ids:
            # Pick zoom closest to playhead or funny moment, or delete all active zooms
            for z in active_zooms:
                deleted_zoom_obj = deleted_zoom_obj or z
                filtered_calls.insert(
                    0,
                    ToolCallSchema(
                        id=f"tc_{uuid.uuid4().hex[:8]}",
                        name="delete_zoom_effect",
                        arguments={"zoom_id": z.id},
                        reason="Removed existing zoom effect as requested",
                    ),
                )
            added_note_parts.append(f"Removed {len(active_zooms)} zoom effect(s)")
        else:
            deleted_zoom_obj = next(
                (z for z in active_zooms if z.id in existing_del_ids),
                active_zooms[0],
            )
    elif active_zooms:
        deleted_zoom_obj = active_zooms[0]

    # 2. Handle reset crop
    if wants_reset_crop:
        has_crop_call = any(tc.name == "set_video_crop" for tc in filtered_calls)
        if not has_crop_call:
            filtered_calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="set_video_crop",
                    arguments={"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
                )
            )
            added_note_parts.append("Reset video crop to full frame")
        return filtered_calls, ("; ".join(added_note_parts) if added_note_parts else None)

    if not wants_crop:
        return filtered_calls, ("; ".join(added_note_parts) if added_note_parts else None)

    # 3. Resolve best target time window [c_start, c_end] if timed crop is requested
    def _resolve_crop_time_window() -> Tuple[float, float]:
        if explicit_range_match:
            rs = float(explicit_range_match.group(1))
            re_t = float(explicit_range_match.group(2))
            if re_t > rs:
                s = max(0.0, min(total_tl_dur - 0.3, rs))
                e = max(s + 0.5, min(total_tl_dur, re_t))
                return round(s, 2), round(e, 2)
        if explicit_for_dur_match:
            span = max(0.5, min(total_tl_dur, float(explicit_for_dur_match.group(1))))
            s = min(max(0.0, total_tl_dur - 0.5), playhead)
            e = min(total_tl_dur, max(s + 0.5, s + span))
            return round(s, 2), round(e, 2)
        if visual_plan:
            for st in visual_plan.get("planned_steps") or []:
                if isinstance(st, dict) and st.get("action") in {"set_video_crop", "add_zoom_effect"}:
                    p = st.get("params") or {}
                    if p.get("start") is not None and p.get("end") is not None:
                        s = max(0.0, min(total_tl_dur - 0.3, float(p["start"])))
                        e = max(s + 0.5, min(total_tl_dur, float(p["end"])))
                        return round(s, 2), round(e, 2)
        if intercepted_zoom_args and intercepted_zoom_args.get("start") is not None:
            s = max(0.0, min(total_tl_dur - 0.3, float(intercepted_zoom_args.get("start", 0.0))))
            e = max(s + 0.5, min(total_tl_dur, float(intercepted_zoom_args.get("end", s + 3.0))))
            return round(s, 2), round(e, 2)
        if deleted_zoom_obj is not None:
            s = max(0.0, min(total_tl_dur - 0.3, float(deleted_zoom_obj.start)))
            e = max(s + 0.5, min(total_tl_dur, float(deleted_zoom_obj.end)))
            return round(s, 2), round(e, 2)
        if visual_plan:
            moments = visual_plan.get("detected_moments") or []
            preferred = [
                m
                for m in moments
                if isinstance(m, dict)
                and m.get("moment_type") in {"funny", "reaction", "punchline", "highlight"}
            ] or [m for m in moments if isinstance(m, dict)]
            if preferred:
                m0 = preferred[0]
                s = max(0.0, min(total_tl_dur - 0.3, float(m0.get("start", 0.0))))
                e = max(s + 0.5, min(total_tl_dur, float(m0.get("end", s + 3.2))))
                return round(s, 2), round(e, 2)
        if project_state.workspaceState.selectedTimeRange is not None:
            rng = project_state.workspaceState.selectedTimeRange
            return round(max(0.0, rng.start), 2), round(min(total_tl_dur, max(rng.start + 0.5, rng.end)), 2)
        phrase_win = _extract_spoken_phrase_window(prompt, transcript, clips)
        if phrase_win is not None:
            s = max(0.0, phrase_win[0] - 0.1)
            e = min(total_tl_dur, max(s + 2.2, phrase_win[1] + 0.6))
            return round(s, 2), round(e, 2)
        s = min(max(0.0, total_tl_dur - 2.0), playhead)
        e = min(total_tl_dur, max(s + 1.0, s + 3.5))
        return round(s, 2), round(e, 2)

    # 4. Resolve best crop bounding box {x, y, w, h}
    def _resolve_crop_box() -> Dict[str, float]:
        if visual_plan:
            for st in visual_plan.get("planned_steps") or []:
                if isinstance(st, dict) and st.get("action") == "set_video_crop":
                    p = st.get("params") or {}
                    if p.get("w") is not None and p.get("h") is not None:
                        cx = max(0.0, min(0.9, float(p.get("x", 0.0))))
                        cy = max(0.0, min(0.9, float(p.get("y", 0.0))))
                        cw = max(0.15, min(1.0 - cx, float(p.get("w", 0.5))))
                        ch = max(0.15, min(1.0 - cy, float(p.get("h", 0.5))))
                        if cx > 0.005 or cy > 0.005 or cw < 0.98 or ch < 0.98:
                            return {"x": round(cx, 3), "y": round(cy, 3), "w": round(cw, 3), "h": round(ch, 3)}
            subjects = [s for s in (visual_plan.get("subjects") or []) if isinstance(s, dict)]
            if subjects:
                chosen_sub = next(
                    (
                        s
                        for s in subjects
                        if any(
                            k in str(s.get("label") or "").lower()
                            for k in ("creator", "face", "webcam", "streamer", "person", "cam", "reaction")
                        )
                    ),
                    subjects[0],
                )
                box = chosen_sub.get("box")
                if isinstance(box, dict) and box.get("w") is not None and box.get("h") is not None:
                    cx = max(0.0, min(0.85, float(box.get("x", 0.0))))
                    cy = max(0.0, min(0.85, float(box.get("y", 0.0))))
                    cw = max(0.22, min(1.0 - cx, float(box.get("w", 0.45))))
                    ch = max(0.25, min(1.0 - cy, float(box.get("h", 0.5))))
                    if cx > 0.005 or cy > 0.005 or cw < 0.98 or ch < 0.98:
                        return {"x": round(cx, 3), "y": round(cy, 3), "w": round(cw, 3), "h": round(ch, 3)}
                return _box_from_focus(
                    float(chosen_sub.get("focus_x", 0.5)),
                    float(chosen_sub.get("focus_y", 0.35)),
                    1.75,
                )
            for st in visual_plan.get("planned_steps") or []:
                if isinstance(st, dict) and st.get("action") == "add_zoom_effect":
                    p = st.get("params") or {}
                    return _box_from_focus(
                        float(p.get("focus_x", 0.5)),
                        float(p.get("focus_y", 0.35)),
                        float(p.get("end_scale", 1.7)),
                    )
        if intercepted_zoom_args:
            return _box_from_focus(
                float(intercepted_zoom_args.get("focus_x") or intercepted_zoom_args.get("focusX") or 0.5),
                float(intercepted_zoom_args.get("focus_y") or intercepted_zoom_args.get("focusY") or 0.35),
                float(intercepted_zoom_args.get("end_scale") or intercepted_zoom_args.get("endScale") or 1.7),
            )
        if deleted_zoom_obj is not None:
            return _box_from_focus(
                float(deleted_zoom_obj.focusX),
                float(deleted_zoom_obj.focusY),
                float(deleted_zoom_obj.endScale or 1.7),
            )
        if any(k in lowered for k in ("top-left", "top left")):
            return {"x": 0.0, "y": 0.0, "w": 0.42, "h": 0.48}
        if any(k in lowered for k in ("top-right", "top right")):
            return {"x": 0.58, "y": 0.0, "w": 0.42, "h": 0.48}
        if any(k in lowered for k in ("bottom-left", "bottom left")):
            return {"x": 0.0, "y": 0.52, "w": 0.42, "h": 0.48}
        if any(k in lowered for k in ("bottom-right", "bottom right")):
            return {"x": 0.58, "y": 0.52, "w": 0.42, "h": 0.48}
        if any(k in lowered for k in ("webcam", "facecam", "face cam")):
            return {"x": 0.0, "y": 0.0, "w": 0.42, "h": 0.48}
        return {"x": 0.22, "y": 0.06, "w": 0.56, "h": 0.68}

    existing_crop_tc = next((tc for tc in filtered_calls if tc.name == "set_video_crop"), None)
    if existing_crop_tc is None:
        crop_args: Dict[str, Any] = dict(_resolve_crop_box())
        if wants_timed_crop:
            cs, ce = _resolve_crop_time_window()
            crop_args["start"] = cs
            crop_args["end"] = ce
            added_note_parts.append(
                f"Cropped to creator/reaction region ({round(crop_args['w'] * 100)}%x{round(crop_args['h'] * 100)}%) from {cs:.1f}s to {ce:.1f}s"
            )
        else:
            added_note_parts.append(
                f"Cropped video frame ({round(crop_args['w'] * 100)}%x{round(crop_args['h'] * 100)}%)"
            )
        filtered_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="set_video_crop",
                arguments=crop_args,
                reason="Applied requested video crop",
            )
        )
    else:
        c_args = dict(existing_crop_tc.arguments or {})
        # If LLM emitted full-frame or invalid box by mistake when user asked to crop in
        if (
            float(c_args.get("x", 0.0)) <= 0.005
            and float(c_args.get("y", 0.0)) <= 0.005
            and float(c_args.get("w", 1.0)) >= 0.99
            and float(c_args.get("h", 1.0)) >= 0.99
        ):
            c_args.update(_resolve_crop_box())
        if wants_timed_crop and (c_args.get("start") is None or c_args.get("end") is None):
            cs, ce = _resolve_crop_time_window()
            c_args["start"] = cs
            c_args["end"] = ce
            added_note_parts.append(f"Timed crop active from {cs:.1f}s to {ce:.1f}s")
        existing_crop_tc.arguments = c_args

    return filtered_calls, ("; ".join(added_note_parts) if added_note_parts else None)


COLOR_NAME_TO_HEX: Dict[str, str] = {
    "black": "#000000",
    "white": "#FFFFFF",
    "yellow": "#FACC15",
    "gold": "#FACC15",
    "red": "#EF4444",
    "crimson": "#DC2626",
    "blue": "#38BDF8",
    "sky": "#38BDF8",
    "navy": "#1E3A8A",
    "green": "#22C55E",
    "lime": "#84CC16",
    "emerald": "#10B981",
    "orange": "#F97316",
    "amber": "#F59E0B",
    "pink": "#EC4899",
    "rose": "#F43F5E",
    "magenta": "#D946EF",
    "purple": "#A855F7",
    "violet": "#8B5CF6",
    "cyan": "#06B6D4",
    "teal": "#14B8A6",
    "aqua": "#06B6D4",
    "gray": "#94A3B8",
    "grey": "#94A3B8",
    "silver": "#CBD5E1",
    "brown": "#92400E",
}


def _normalize_color_to_hex(raw_val: Any) -> Optional[str]:
    if not isinstance(raw_val, str) or not raw_val.strip():
        return None
    t = raw_val.strip()
    mapped = COLOR_NAME_TO_HEX.get(t.lower())
    if mapped:
        return mapped
    cleaned = t.lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", cleaned):
        return "#" + "".join(c * 2 for c in cleaned).upper()
    if re.fullmatch(r"[0-9a-fA-F]{6}", cleaned):
        return "#" + cleaned.upper()
    for name, hex_code in COLOR_NAME_TO_HEX.items():
        if re.search(rf"\b{re.escape(name)}\b", t.lower()):
            return hex_code
    return None


def _extract_color_from_prompt(text: str) -> Optional[Tuple[str, str]]:
    lowered = text.lower()
    hex_m = re.search(r"#([0-9a-fA-F]{6})\b", lowered)
    if hex_m:
        h = "#" + hex_m.group(1).upper()
        return h, h
    for name, hex_code in COLOR_NAME_TO_HEX.items():
        if re.search(rf"\b{re.escape(name)}\b", lowered):
            return name, hex_code
    return None


def _build_smart_sfx_tool_calls(
    prompt: str,
    project_state: ProjectState,
    sfx_assets: List[SfxAsset],
    visual_plan: Optional[Dict[str, Any]] = None,
    transcript: Optional[Dict[str, Any]] = None,
) -> List[ToolCallSchema]:
    """
    Selects and places context-matched SFX clips onto the timeline:
    1. Planned `add_sfx_clip` steps from Gemini Visual Planner.
    2. Visual Planner `detected_moments` spaced >= 2.0s apart (with mood matching + variety).
    3. Transcript mood beats and/or evenly spaced timeline beats for video-wide SFX prompts.
    4. Single playhead/phrase-matched SFX for targeted prompts.
    """
    if not sfx_assets:
        return []

    clips = project_state.tracks.clips or []
    total_tl_dur = max(
        0.5,
        sum(float(c.duration) for c in clips)
        if clips
        else float(project_state.sourceVideo.originalDuration or 10.0),
    )
    playhead = max(0.0, float(project_state.workspaceState.playheadPosition or 0.0))

    def _make_sfx_tc(
        asset: SfxAsset,
        start_sec: float,
        volume: float = 0.85,
        reason: Optional[str] = None,
    ) -> ToolCallSchema:
        clamped_start = round(
            max(0.0, min(max(0.0, total_tl_dur - 0.15), float(start_sec))), 2
        )
        clamped_vol = round(max(0.1, min(2.0, float(volume))), 2)
        return ToolCallSchema(
            id=f"tc_{uuid.uuid4().hex[:8]}",
            name="add_sfx_clip",
            arguments={
                "start": clamped_start,
                "asset_id": asset.id,
                "sfx_name": asset.name,
                "name": asset.name,
                "url": asset.url,
                "duration": asset.duration,
                "volume": clamped_vol,
            },
            reason=reason,
        )

    sfx_calls: List[ToolCallSchema] = []
    used_ids: set[str] = set()
    placed_starts: List[float] = []

    # 1. Explicit add_sfx_clip steps from Gemini Visual Planner
    if visual_plan:
        for step in visual_plan.get("planned_steps") or []:
            if not isinstance(step, dict) or step.get("action") != "add_sfx_clip":
                continue
            p = dict(step.get("params") or {})
            asset_id_hint = str(p.get("asset_id") or "").strip()
            chosen = (
                next((a for a in sfx_assets if a.id == asset_id_hint), None)
                if asset_id_hint
                else None
            )
            if chosen is None:
                q = f"{p.get('sfx_name') or ''} {step.get('reason') or ''}".strip()
                chosen = find_best_matching_sfx(
                    sfx_assets,
                    q,
                    exclude_ids=used_ids if used_ids else None,
                )
            if chosen is None:
                continue
            st = float(p.get("start", playhead))
            if any(abs(st - ps) < 0.5 for ps in placed_starts):
                continue
            vol = float(p.get("volume", 0.85))
            reason_txt = str(step.get("reason") or f"Matched '{chosen.name}'").strip()[:160]
            sfx_calls.append(_make_sfx_tc(chosen, st, vol, reason_txt))
            used_ids.add(chosen.id)
            placed_starts.append(st)
        if sfx_calls:
            return sfx_calls

    # 2. Gemini detected visual moments -> mood-matched distinct SFX (>= 2.0s apart, max 5)
    if visual_plan and visual_plan.get("detected_moments"):
        moments = sorted(
            [m for m in (visual_plan.get("detected_moments") or []) if isinstance(m, dict)],
            key=lambda m: float(m.get("start", 0.0)),
        )
        for m in moments:
            ms = float(m.get("start", 0.0))
            if any(abs(ms - ps) < 2.0 for ps in placed_starts):
                continue
            m_type = str(m.get("moment_type") or "").strip()
            m_desc = str(m.get("description") or "").strip()
            query = f"{m_type} {m_desc}".strip() or prompt
            chosen = find_best_matching_sfx(
                sfx_assets,
                query,
                exclude_ids=used_ids if used_ids else None,
            )
            if chosen is None:
                continue
            reason_txt = (m_desc or m_type or f"Matched '{chosen.name}'")[:160]
            sfx_calls.append(_make_sfx_tc(chosen, ms, 0.85, reason_txt))
            used_ids.add(chosen.id)
            placed_starts.append(ms)
            if len(sfx_calls) >= 5:
                return sfx_calls
        if sfx_calls:
            return sfx_calls

    # 3. Video-wide / applicable multi-SFX placement from transcript cues & timeline beats
    lowered = prompt.lower()
    wants_multi_sfx = any(
        k in lowered
        for k in (
            "applicable",
            "fit",
            "fitting",
            "video",
            "throughout",
            "auto",
            "suitable",
            "appropriate",
            "matching",
            "sound effects",
            "audio effects",
        )
    )
    if wants_multi_sfx:
        mood_cue_tokens = (
            "!",
            "?",
            "bro",
            "what",
            "wait",
            "no way",
            "crazy",
            "wild",
            "omg",
            "lmao",
            "lol",
            "haha",
            "funny",
            "fail",
            "oops",
            "slip",
            "goofy",
            "roast",
            "cooked",
            "dead",
            "boring",
            "sleep",
            "win",
            "clutch",
            "smooth",
            "rizz",
            "wtf",
            "bruh",
            "ouch",
            "boom",
            "whoosh",
            "awkward",
            "cringe",
            "sad",
            "shock",
        )
        candidates: List[Tuple[float, str]] = []
        segments = (transcript or {}).get("segments") or []
        for seg in segments:
            txt = str(seg.get("text") or "").strip()
            if not txt:
                continue
            src_s = float(seg.get("start", 0.0))
            tl_s = _source_time_to_timeline(src_s, clips) if clips else src_s
            if tl_s is None:
                continue
            txt_low = txt.lower()
            if any(cue in txt_low for cue in mood_cue_tokens):
                candidates.append((tl_s, txt))

        for ts, txt in candidates:
            if any(abs(ts - ps) < 2.5 for ps in placed_starts):
                continue
            chosen = find_best_matching_sfx(
                sfx_assets,
                txt,
                exclude_ids=used_ids if used_ids else None,
            )
            if chosen is None:
                continue
            sfx_calls.append(
                _make_sfx_tc(chosen, ts, 0.85, f"Matched transcript beat: {txt[:90]}")
            )
            used_ids.add(chosen.id)
            placed_starts.append(ts)
            if len(sfx_calls) >= 4:
                break

        if len(sfx_calls) < 2:
            if total_tl_dur >= 6.0:
                beat_times = [
                    round(total_tl_dur * 0.16, 2),
                    round(total_tl_dur * 0.50, 2),
                    round(total_tl_dur * 0.82, 2),
                ]
            elif total_tl_dur >= 2.8:
                beat_times = [
                    round(total_tl_dur * 0.18, 2),
                    round(total_tl_dur * 0.65, 2),
                ]
            else:
                beat_times = [round(min(playhead, total_tl_dur * 0.35), 2)]

            for bt in beat_times:
                if any(abs(bt - ps) < 2.2 for ps in placed_starts):
                    continue
                chosen = find_best_matching_sfx(
                    sfx_assets,
                    prompt,
                    exclude_ids=used_ids if used_ids else None,
                )
                if chosen is None:
                    continue
                sfx_calls.append(
                    _make_sfx_tc(
                        chosen, bt, 0.85, f"Applicable SFX for timeline beat at {bt:.1f}s"
                    )
                )
                used_ids.add(chosen.id)
                placed_starts.append(bt)
                if len(sfx_calls) >= 3:
                    break

        if sfx_calls:
            return sfx_calls

    # 4. Single SFX placement at matched spoken phrase or playhead
    phrase_win = _extract_spoken_phrase_window(prompt, transcript, clips)
    s_start = round(phrase_win[0], 2) if phrase_win is not None else round(playhead, 2)
    chosen = find_best_matching_sfx(sfx_assets, prompt)
    if chosen is not None:
        sfx_calls.append(_make_sfx_tc(chosen, s_start, 0.85, f"Placed '{chosen.name}' SFX"))
    return sfx_calls


def _enforce_caption_and_timeline_tools(
    prompt: str,
    tool_calls: List[ToolCallSchema],
    project_state: ProjectState,
    pauses: Optional[List[Dict[str, float]]] = None,
    visual_plan: Optional[Dict[str, Any]] = None,
    transcript: Optional[Dict[str, Any]] = None,
    sfx_assets: Optional[List[SfxAsset]] = None,
) -> Tuple[List[ToolCallSchema], Optional[str]]:
    """
    Guarantees that plain-English requests for caption color/style, speed, pauses,
    aspect ratio, and drawing/SFX deletion always invoke the right tool with
    normalized hex colors and valid parameters.
    """
    lowered = prompt.lower()
    calls = list(tool_calls)
    notes: List[str] = []

    # 1. Normalize any color names inside existing change_caption_style / add_drawing tool calls
    for tc in calls:
        if tc.name == "change_caption_style":
            args = dict(tc.arguments or {})
            if "color" in args and "text_color" not in args:
                args["text_color"] = args.pop("color")
            if "textColor" in args and "text_color" not in args:
                args["text_color"] = args.pop("textColor")
            if "highlightColor" in args and "highlight_color" not in args:
                args["highlight_color"] = args.pop("highlightColor")
            if "text_color" in args:
                norm_tc = _normalize_color_to_hex(args["text_color"])
                if norm_tc:
                    args["text_color"] = norm_tc
            if "highlight_color" in args:
                norm_hc = _normalize_color_to_hex(args["highlight_color"])
                if norm_hc:
                    args["highlight_color"] = norm_hc
            tc.arguments = args
        elif tc.name == "add_drawing":
            args = dict(tc.arguments or {})
            if "color" in args:
                norm_dc = _normalize_color_to_hex(args["color"])
                if norm_dc:
                    args["color"] = norm_dc
            tc.arguments = args

    # 2. Enforce caption color & style requests ("change the caption to black", "make the text black and Impact font", etc.)
    is_drawing_request = any(
        re.search(rf"\b{w}\b", lowered)
        for w in ("draw", "arrow", "circle", "box", "rect", "rectangle", "line")
    )
    has_caption_word = any(
        k in lowered for k in ("caption", "captions", "subtitle", "subtitles")
    )
    is_overlay_request = (
        any(k in lowered for k in ("overlay", "watermark", "lacy box", "badge"))
        and not has_caption_word
    )
    mentions_caption_or_color = (
        has_caption_word
        or any(
            k in lowered
            for k in (
                "text color",
                "font color",
                "highlight color",
                "change color",
                "color to",
                "colour",
                "make the text",
                "text to",
                "font",
                "karaoke",
                "bold social",
                "minimal",
                "unbold",
                "bold text",
            )
        )
    )

    color_hit = _extract_color_from_prompt(prompt)
    if mentions_caption_or_color and not is_drawing_request and not is_overlay_request:
        desired_style: Dict[str, Any] = {}
        if "karaoke" in lowered:
            desired_style["preset"] = "karaoke"
        elif "bold social" in lowered or "uppercase" in lowered:
            desired_style["preset"] = "bold_social"
        elif "minimal" in lowered:
            desired_style["preset"] = "minimal"
        elif "yellow highlight" in lowered:
            desired_style["preset"] = "yellow_highlight"

        for font_name in ("Inter", "Montserrat", "Impact", "Poppins", "Arial"):
            if re.search(rf"\b{font_name.lower()}\b", lowered):
                desired_style["font_family"] = font_name
                break

        if any(k in lowered for k in ("unbold", "not bold", "no bold", "regular font", "regular text")):
            desired_style["is_bold"] = False
        elif re.search(r"\bbold\b", lowered):
            desired_style["is_bold"] = True

        if color_hit is not None:
            c_name, c_hex = color_hit
            only_highlight = "highlight" in lowered and not any(
                k in lowered for k in ("text color", "caption to", "captions to", "change the caption", "make the text", "text to")
            )
            if only_highlight:
                desired_style["highlight_color"] = c_hex
            else:
                desired_style["text_color"] = c_hex
                if any(k in lowered for k in ("all " + c_name, "whole caption", "both", "solid " + c_name)):
                    desired_style["highlight_color"] = c_hex

        if "top" in lowered and any(k in lowered for k in ("caption", "subtitle", "text", "put", "move", "position")):
            desired_style["position"] = "top"
        elif ("middle" in lowered or "center" in lowered) and any(
            k in lowered for k in ("caption", "subtitle", "text", "put", "move", "position")
        ):
            desired_style["position"] = "middle"
        elif "bottom" in lowered and any(k in lowered for k in ("caption", "subtitle", "text", "put", "move", "position")):
            desired_style["position"] = "bottom"

        if "bigger" in lowered or "larger" in lowered or "increase font" in lowered:
            desired_style["font_size"] = min(48, int(project_state.captionStyle.fontSize) + 6)
        elif "smaller" in lowered or "decrease font" in lowered:
            desired_style["font_size"] = max(16, int(project_state.captionStyle.fontSize) - 6)

        if desired_style:
            existing_style_tc = next((tc for tc in calls if tc.name == "change_caption_style"), None)
            if existing_style_tc is None:
                calls.append(
                    ToolCallSchema(
                        id=f"tc_{uuid.uuid4().hex[:8]}",
                        name="change_caption_style",
                        arguments=desired_style,
                        reason="Updated caption style as requested",
                    )
                )
                if color_hit is not None:
                    notes.append(f"Changed caption color to {color_hit[0]} ({color_hit[1]})")
                else:
                    notes.append("Updated caption style")
            else:
                merged_args = dict(existing_style_tc.arguments or {})
                for k, v in desired_style.items():
                    if merged_args.get(k) is None or k in {"text_color", "highlight_color"}:
                        merged_args[k] = v
                existing_style_tc.arguments = merged_args
                if color_hit is not None:
                    notes.append(f"Changed caption color to {color_hit[0]} ({color_hit[1]})")

            # If no captions exist on timeline yet, auto-generate them so the user sees their styled captions
            if (
                len(project_state.tracks.captions or []) == 0
                and transcript
                and not any(tc.name == "generate_auto_captions" for tc in calls)
            ):
                calls.insert(
                    0,
                    ToolCallSchema(
                        id=f"tc_{uuid.uuid4().hex[:8]}",
                        name="generate_auto_captions",
                        arguments={},
                        reason="Auto-generated captions so style change is visible",
                    ),
                )

    # 3. Enforce delete_drawing / delete_sfx_clip when user asks to remove drawing or sound effect
    if re.search(r"\b(remove|delete|clear)\b.{0,25}\b(drawing|arrow|circle|box|line|highlight)\b", lowered):
        calls = [tc for tc in calls if tc.name != "add_drawing"]
        active_drawings = list(project_state.tracks.drawings or [])
        if active_drawings and not any(tc.name == "delete_drawing" for tc in calls):
            for d in active_drawings:
                calls.append(
                    ToolCallSchema(
                        id=f"tc_{uuid.uuid4().hex[:8]}",
                        name="delete_drawing",
                        arguments={"drawing_id": d.id},
                    )
                )
            notes.append(f"Removed {len(active_drawings)} drawing(s)")

    wants_remove_sfx = bool(
        re.search(
            r"\b(remove|delete|clear)\b.{0,25}\b(sfx|sound\s+effects?|audio\s+effects?)\b",
            lowered,
        )
    )
    if wants_remove_sfx:
        calls = [tc for tc in calls if tc.name != "add_sfx_clip"]
        active_sfx = list(project_state.tracks.sfx or [])
        if active_sfx and not any(tc.name == "delete_sfx_clip" for tc in calls):
            for s in active_sfx:
                calls.append(
                    ToolCallSchema(
                        id=f"tc_{uuid.uuid4().hex[:8]}",
                        name="delete_sfx_clip",
                        arguments={"sfx_id": s.id},
                    )
                )
            notes.append(f"Removed {len(active_sfx)} SFX clip(s)")

    # 4. Enforce remove pauses when user asks to remove pauses/silence and LLM omitted it
    if (
        any(k in lowered for k in ("remove pause", "remove pauses", "remove all pauses", "remove silence", "cut silence", "dead air"))
        and pauses
        and not any(tc.name == "remove_time_ranges" for tc in calls)
    ):
        calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="remove_time_ranges",
                arguments={"ranges": pauses},
            )
        )
        notes.append(f"Removed {len(pauses)} pause gap(s)")

    # 5. Enforce speed change when user asks to speed up / slow down and LLM omitted set_clip_speed
    has_speed_word = any(
        k in lowered
        for k in ("speed", "fast", "slow", "playback", "tempo", "faster", "slower", "timelapse", "time-lapse")
    )
    has_zoom_or_crop_word = any(k in lowered for k in ("zoom", "punch", "scale", "crop"))
    speed_match = (
        re.search(r"(\d+(?:\.\d+)?)\s*x\b", lowered)
        if (has_speed_word or not has_zoom_or_crop_word)
        else None
    )
    wants_speed = bool(
        speed_match
        or any(
            k in lowered
            for k in ("speed up", "fast forward", "slow down", "slow motion", "slow-mo", "slowmo")
        )
    )
    if wants_speed and not any(tc.name == "set_clip_speed" for tc in calls):
        if speed_match:
            target_spd = round(max(0.25, min(4.0, float(speed_match.group(1)))), 2)
        elif any(k in lowered for k in ("slow down", "slow motion", "slow-mo", "slowmo")):
            target_spd = 0.5
        else:
            target_spd = 1.5
        clips = project_state.tracks.clips or []
        total_tl_dur = max(0.5, sum(float(c.duration) for c in clips) if clips else 10.0)
        playhead = max(0.0, float(project_state.workspaceState.playheadPosition or 0.0))
        if any(k in lowered for k in ("whole", "entire", "full", "all clip")) and clips:
            for c in clips:
                calls.append(
                    ToolCallSchema(
                        id=f"tc_{uuid.uuid4().hex[:8]}",
                        name="set_clip_speed",
                        arguments={"clip_id": c.id, "speed": target_spd},
                    )
                )
            notes.append(f"Set clip speed to {target_spd}x")
        else:
            s_t = min(max(0.0, total_tl_dur - 1.5), playhead)
            e_t = min(total_tl_dur, max(s_t + 0.5, s_t + 4.0))
            if visual_plan and visual_plan.get("detected_moments"):
                m0 = visual_plan["detected_moments"][0]
                s_t = float(m0.get("start", s_t))
                e_t = float(m0.get("end", e_t))
            calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="set_clip_speed",
                    arguments={"start_time": round(s_t, 2), "end_time": round(e_t, 2), "speed": target_spd},
                )
            )
            notes.append(f"Set {s_t:.1f}s–{e_t:.1f}s speed to {target_spd}x")

    # 6. SFX Trim & Smart Multi-SFX Enforcement
    has_sfx_word = any(
        k in lowered
        for k in ("sound effect", "sound effects", "sfx", "audio effect", "audio effects", "mp3")
    )
    active_sfx_clips = list(project_state.tracks.sfx or [])
    wants_trim_sfx = bool(
        has_sfx_word
        and active_sfx_clips
        and not wants_remove_sfx
        and any(k in lowered for k in ("trim", "shorten", "cut", "skip", "shorter", "duration"))
        and not re.search(r"\b(add|put|place|insert)\b.{0,20}\b(sound|sfx|mp3|audio)\b", lowered)
    )
    if wants_trim_sfx and not any(tc.name == "trim_sfx_clip" for tc in calls):
        def _sfx_token_match(sfx_name_val: str) -> bool:
            tokens = [
                w
                for w in re.findall(r"[a-z0-9]+", sfx_name_val.lower())
                if len(w) >= 3 and w not in {"sfx", "the", "sound", "effect", "audio"}
            ]
            return any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in tokens)

        target_sfx = next(
            (s for s in active_sfx_clips if s.name.lower() in lowered),
            None,
        ) or next(
            (s for s in active_sfx_clips if _sfx_token_match(s.name)),
            active_sfx_clips[-1],
        )
        trim_args: Dict[str, Any] = {"sfx_id": target_sfx.id, "sfx_name": target_sfx.name}
        skip_m = re.search(
            r"\b(?:skip|trim(?:\s+(?:start|off))?|cut\s+off|from|start\s+at)\s+(?:the\s+)?(?:first\s+)?(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)?\b",
            lowered,
        )
        range_m = re.search(
            r"\bfrom\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)?\s*(?:to|-)\s*(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)?\b",
            lowered,
        )
        dur_m = re.search(
            r"\b(?:to|for|duration)\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)\b",
            lowered,
        )
        if range_m:
            ts = max(0.0, float(range_m.group(1)))
            te = max(ts + 0.15, float(range_m.group(2)))
            trim_args["trim_start"] = round(ts, 2)
            trim_args["duration"] = round(te - ts, 2)
        else:
            if skip_m:
                trim_args["trim_start"] = round(max(0.0, float(skip_m.group(1))), 2)
            if dur_m:
                trim_args["duration"] = round(max(0.15, float(dur_m.group(1))), 2)
            elif skip_m:
                old_trim = max(0.0, float(getattr(target_sfx, "trimStart", 0.0) or 0.0))
                trim_args["duration"] = round(
                    max(0.15, (old_trim + target_sfx.duration) - float(trim_args["trim_start"])),
                    2,
                )
            else:
                trim_args["duration"] = round(max(0.15, min(target_sfx.duration * 0.5, 1.5)), 2)
        calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="trim_sfx_clip",
                arguments=trim_args,
                reason="Trimmed SFX clip as requested",
            )
        )
        notes.append(f"Trimmed '{target_sfx.name}' SFX")

    wants_add_sfx = (
        any(
            k in lowered
            for k in ("sound effect", "sound effects", "sfx", "audio effect", "audio effects")
        )
        and not wants_remove_sfx
        and not wants_trim_sfx
    )
    if wants_add_sfx and sfx_assets and not any(tc.name == "add_sfx_clip" for tc in calls):
        sfx_calls = _build_smart_sfx_tool_calls(
            prompt=prompt,
            project_state=project_state,
            sfx_assets=sfx_assets,
            visual_plan=visual_plan,
            transcript=transcript,
        )
        if sfx_calls:
            calls.extend(sfx_calls)
            placed_summary = ", ".join(
                f"{tc.arguments.get('sfx_name', 'SFX')} @ {float(tc.arguments.get('start', 0.0)):.1f}s"
                for tc in sfx_calls[:4]
            )
            extra = f" (+{len(sfx_calls) - 4} more)" if len(sfx_calls) > 4 else ""
            notes.append(f"Added {len(sfx_calls)} SFX ({placed_summary}{extra})")

    return calls, ("; ".join(notes) if notes else None)


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

    if len(words) > 256:
        idx_s = _bisect_dict_start(words, s)
        start_candidates = words[max(0, idx_s - 12) : min(len(words), idx_s + 13)] or words[:1]
        best_start = min(start_candidates, key=lambda w: abs(float(w["start"]) - s))
        idx_e = _bisect_dict_start(words, e)
        end_candidates = words[max(0, idx_e - 14) : min(len(words), idx_e + 15)] or words[-1:]
        best_end = min(end_candidates, key=lambda w: abs(float(w["end"]) - e))
    else:
        # Snap start to nearest word start within 1.5s
        best_start = min(words, key=lambda w: abs(float(w["start"]) - s))
        # Snap end to nearest word end within 1.5s
        best_end = min(words, key=lambda w: abs(float(w["end"]) - e))

    if abs(float(best_start["start"]) - s) <= 1.5:
        s = max(0.0, float(best_start["start"]) - 0.08)

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


def _format_campaign_rules_block(
    campaign_id: Optional[str], campaign_rules: Optional[List[str]]
) -> str:
    clean_rules = [str(r).strip() for r in (campaign_rules or []) if str(r).strip()]
    if not clean_rules:
        return ""
    label = (campaign_id or "active_campaign").strip()
    bullets = "\n".join(f"- {rule}" for rule in clean_rules)
    return (
        f"\n\nACTIVE CAMPAIGN RULES ({label}) — MANDATORY:\n"
        f"{bullets}\n"
        "- Always call `generate_auto_captions` if no captions exist yet so subtitles are engaging and reflect what is being said and done."
    )


def _run_gemini_viral_pipeline(
    prompt: str,
    project_state: ProjectState,
    transcript: Optional[Dict[str, Any]],
    campaign_id: Optional[str] = None,
    campaign_rules: Optional[List[str]] = None,
    chapters: Optional[List[Dict[str, Any]]] = None,
) -> AiEditResponse:
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    has_gemini = bool(gemini_key and gemini_key != "your_gemini_api_key_here")
    has_groq = bool(groq_key and groq_key != "your_groq_api_key_here")

    lowered_prompt = prompt.lower()
    duration = float(project_state.sourceVideo.originalDuration or 0.0)

    # Deterministic explicit timestamp shortcut (e.g. "clip from 14:20:00 to 14:21:00")
    explicit_range = parse_explicit_clip_range(prompt, duration)
    if explicit_range is not None:
        rs, re_val = explicit_range
        max_dur = duration if duration > 0 else re_val
        snapped_s, snapped_e = _snap_to_word_boundaries(rs, re_val, transcript, max_dur)
        clips_payload = [
            {
                "source_start": snapped_s,
                "source_end": snapped_e,
                "title": f"Clip {_format_hms_short(snapped_s)} - {_format_hms_short(snapped_e)}",
                "reason": "Requested explicit timestamp range snapped to word boundaries.",
                "virality_score": 9.2,
            }
        ]
        tool_calls: List[ToolCallSchema] = [
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="isolate_highlight_clips",
                arguments={"clips": clips_payload},
            )
        ]
        if any(k in lowered_prompt for k in ("short", "vertical", "9:16", "tiktok", "reel")):
            tool_calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="change_video_format",
                    arguments={"aspect_ratio": "9:16"},
                )
            )
        auto_captions: Optional[List[CaptionItem]] = None
        if any(k in lowered_prompt for k in ("caption", "captions", "karaoke", "short")):
            tool_calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="generate_auto_captions",
                    arguments={"start_time": snapped_s, "end_time": snapped_e},
                )
            )
            if transcript:
                auto_captions = _build_captions_for_isolated_or_range(
                    transcript, clips_payload, (snapped_s, snapped_e)
                )
        if "karaoke" in lowered_prompt:
            tool_calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="change_caption_style",
                    arguments={"preset": "karaoke", "highlight_color": "#FACC15", "is_bold": True},
                )
            )
        return AiEditResponse(
            message=(
                f"Isolated requested timestamp window {snapped_s:.2f}s - {snapped_e:.2f}s "
                f"({_format_hms_short(snapped_s)} -> {_format_hms_short(snapped_e)})."
            ),
            tool_calls=tool_calls,
            auto_captions=auto_captions,
            route_used="explicit-timestamp-range",
        )

    prefer_start = any(
        w in lowered_prompt for w in ("first", "beginning", "intro", "start", "opening")
    )
    heatmap_peaks = _extract_top_heatmap_peaks(project_state)
    transcript_text = _extract_targeted_transcript_windows(
        transcript,
        heatmap_peaks,
        prompt,
        chapters=chapters,
        max_chars=7500,
        prefer_start=prefer_start,
    )

    if not has_gemini and not has_groq:
        return _deterministic_viral_fallback(prompt, project_state, transcript, heatmap_peaks)

    first_note = (
        "\nIMPORTANT: The user specifically asked for the FIRST matching moment — pick the earliest matching clip in the video timeline."
        if "first" in lowered_prompt
        else ""
    )
    campaign_block = _format_campaign_rules_block(campaign_id, campaign_rules)

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
        "Available tool names from the 20 Shared Tools:\n"
        + ", ".join(t["function"]["name"] for t in SHARED_TOOLS_DEFINITIONS)
        + "\nAlso include `change_video_format` ('9:16' if user asks for Shorts/Reels/TikTok/vertical), "
        "`generate_auto_captions` if captions are requested or helpful, and `change_caption_style` if styling is requested.\n"
        "CRITICAL: Never modify captions that have `isManuallyEdited: true`."
        + first_note
        + campaign_block
    )

    clean_chapters = [
        {
            "start": c.get("start_time"),
            "end": c.get("end_time"),
            "title": c.get("title"),
        }
        for c in (chapters or [])[:40]
        if isinstance(c, dict) and c.get("title")
    ]
    long_vod_note = (
        "\nNOTE: This is a >2 hour long VOD. Direct full-video streaming is skipped for latency; "
        "select timestamps strictly from the Heatmap Peaks, Chapters, and Targeted Transcript Windows below.\n"
        if duration > 7200.0
        else ""
    )

    def _build_viral_context(peaks: List[Dict[str, float]], t_text: str) -> str:
        chap_block = (
            f"YouTube Chapters (first {len(clean_chapters)}):\n{json.dumps(clean_chapters, indent=2)}\n\n"
            if clean_chapters
            else ""
        )
        return (
            f"User Request: {prompt}\n"
            f"Video Title: {project_state.sourceVideo.title}\n"
            f"Original Duration: {duration:.2f}s ({_format_hms_short(duration)})\n"
            f"Current Aspect Ratio: {project_state.workspaceState.targetAspectRatio}"
            f"{long_vod_note}\n\n"
            f"Signal 1 — Top YouTube Most Replayed Heatmap Peaks:\n{json.dumps(peaks, indent=2)}\n\n"
            f"{chap_block}"
            f"Signal 2 — Targeted Transcript Windows:\n{t_text}\n"
        )

    user_context = _build_viral_context(heatmap_peaks, transcript_text)

    last_error: Optional[Exception] = None

    def _is_quota_err(exc: Exception) -> bool:
        low = str(exc).lower()
        return any(k in low for k in ("429", "resource_exhausted", "quota", "rate_limit", "rate limit"))

    def _try_gemini_viral() -> Optional[AiEditResponse]:
        nonlocal last_error
        if not has_gemini:
            return None
        client = genai.Client(api_key=gemini_key)
        contents: List[Any] = []
        yt_url = project_state.youtubeUrl.strip()
        use_direct_yt_uri = (
            (yt_url.startswith("http://") or yt_url.startswith("https://"))
            and (duration <= 7200.0)
        )
        if use_direct_yt_uri:
            contents.append(types.Part.from_uri(file_uri=yt_url, mime_type="video/mp4"))
        contents.append(user_context)

        quota_failures = 0
        for idx, model_id in enumerate(get_gemini_models_chain()[:3]):
            try:
                print(f"[AI] Viral discovery via Gemini ({model_id})...", flush=True)
                response = client.models.generate_content(
                    model=model_id,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        temperature=0.3,
                    ),
                )
                if not use_direct_yt_uri and duration > 7200.0:
                    route_label = (
                        f"{model_id} (long-vod-signals)"
                        if idx == 0
                        else f"{model_id} (long-vod backup)"
                    )
                else:
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
                print(f"[AI] Gemini ({model_id}) failed: {str(exc)[:140]}", flush=True)
                report_model_failure("gemini", model_id, exc)
                if _is_quota_err(exc):
                    quota_failures += 1
                    if quota_failures >= 2:
                        break
        return None

    def _try_groq_viral() -> Optional[AiEditResponse]:
        nonlocal last_error
        if not has_groq:
            return None
        groq_client = Groq(api_key=groq_key, timeout=25.0)
        compact_context = _build_viral_context(
            heatmap_peaks[:5],
            _extract_targeted_transcript_windows(
                transcript,
                heatmap_peaks[:5],
                prompt,
                chapters=chapters,
                max_chars=3500,
                prefer_start=prefer_start,
            ),
        )
        active_context = user_context
        for model_id in get_tool_models_chain()[:3]:
            for attempt in range(2):
                try:
                    print(f"[AI] Viral discovery via Groq ({model_id})...", flush=True)
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
                        f"groq ({model_id}) [viral]",
                    )
                except Exception as exc:
                    last_error = exc
                    msg_low = str(exc).lower()
                    if attempt == 0 and ("413" in msg_low or "request_too_large" in msg_low or "too large" in msg_low):
                        active_context = compact_context
                        continue
                    print(f"[AI] Groq ({model_id}) failed: {str(exc)[:140]}", flush=True)
                    report_model_failure("groq", model_id, exc)
                    break
        return None

    # On >2h VODs (where direct video URI is skipped), Groq answers text JSON in ~1-3s;
    # keep Gemini first on <=2h videos where multimodal YouTube URL inspection is active.
    if duration > 7200.0 and has_groq:
        res = _try_groq_viral() or _try_gemini_viral()
    else:
        res = _try_gemini_viral() or _try_groq_viral()
    if res is not None:
        return res

    fallback = _deterministic_viral_fallback(prompt, project_state, transcript, heatmap_peaks)
    if last_error is not None:
        fallback.message = f"[AI fallback: {last_error}] {fallback.message}"
    return fallback


def _run_gemini_editor_fallback(
    prompt: str,
    editor_context: str,
    project_state: ProjectState,
    transcript: Optional[Dict[str, Any]],
    sfx_assets: Optional[List[SfxAsset]] = None,
    visual_plan: Optional[Dict[str, Any]] = None,
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
    quota_failures = 0
    for model_id in get_gemini_models_chain()[:3]:
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
            res = _parse_viral_json_response(
                response.text or "",
                project_state,
                transcript,
                prompt,
                duration,
                f"gemini-editor-backup ({model_id})",
            )
            res.tool_calls = _enrich_sfx_tool_calls(res.tool_calls, sfx_assets or [])
            res.tool_calls, crop_zoom_note = _enforce_crop_and_zoom_intent(
                prompt, res.tool_calls, project_state, visual_plan=visual_plan, transcript=transcript
            )
            pauses_list = detect_pause_gaps(transcript, min_gap=0.7) if transcript else []
            res.tool_calls, style_tool_note = _enforce_caption_and_timeline_tools(
                prompt,
                res.tool_calls,
                project_state,
                pauses=pauses_list,
                visual_plan=visual_plan,
                transcript=transcript,
                sfx_assets=sfx_assets,
            )
            if res.auto_captions is None and transcript and any(
                tc.name == "generate_auto_captions" for tc in res.tool_calls
            ):
                res.auto_captions = build_captions_from_transcript(transcript)
            extra_notes = "; ".join(n for n in (crop_zoom_note, style_tool_note) if n)
            if extra_notes and extra_notes.lower() not in res.message.lower():
                res.message = f"{res.message} ({extra_notes}.)".strip()
            return res
        except Exception as exc:
            report_model_failure("gemini", model_id, exc)
            low = str(exc).lower()
            if any(k in low for k in ("429", "resource_exhausted", "quota", "rate_limit")):
                quota_failures += 1
                if quota_failures >= 2:
                    break
            continue
    return None


def _run_groq_fast_editor(
    prompt: str,
    project_state: ProjectState,
    transcript: Optional[Dict[str, Any]],
    history: List[Dict[str, str]],
    campaign_id: Optional[str] = None,
    campaign_rules: Optional[List[str]] = None,
    sfx_assets: Optional[List[SfxAsset]] = None,
    visual_plan: Optional[Dict[str, Any]] = None,
    planner_model: Optional[str] = None,
) -> AiEditResponse:
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    has_groq = bool(groq_key and groq_key != "your_groq_api_key_here")
    lowered_prompt = prompt.lower()
    prefer_start = any(
        w in lowered_prompt for w in ("first", "beginning", "intro", "start", "opening")
    )
    pauses = detect_pause_gaps(transcript, min_gap=0.7) if transcript else []
    heatmap_peaks = _extract_top_heatmap_peaks(project_state)
    transcript_text = _format_timeline_transcript_for_llm(
        transcript,
        project_state.tracks.clips,
        max_segments=60,
        max_chars=6500,
        prefer_start=prefer_start,
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
    sfx_catalog = [
        {
            "id": a.id,
            "name": a.name,
            "duration": a.duration,
            "tags": a.tags,
            "description": a.description,
        }
        for a in (sfx_assets or [])
    ]
    selected_range = (
        project_state.workspaceState.selectedTimeRange.model_dump()
        if project_state.workspaceState.selectedTimeRange is not None
        else None
    )

    first_note = (
        "\n11. IMPORTANT: The user specifically asked for the FIRST matching moment — pick the earliest matching clip in the video timeline."
        if "first" in lowered_prompt
        else ""
    )
    campaign_block = _format_campaign_rules_block(campaign_id, campaign_rules)
    visual_plan_block = (
        f"\n\nGEMINI VISUAL PLANNER REPORT (visual analysis already performed on the user's clip):\n"
        f"{json.dumps(visual_plan, indent=2)}\n"
        "12. MANDATORY VISUAL PLAN EXECUTION: Gemini watched the actual clip and measured exact on-screen positions (`focus_x`, `focus_y`, `points`, `box`) and `TL` reaction/funny moments above. "
        "Translate Gemini's `planned_steps` directly into tool calls (`add_zoom_effect`, `add_drawing`, `add_sfx_clip`, `set_clip_speed`, `set_video_crop`) preserving those exact coordinates and timestamps. "
        "Always list what Gemini spotted (what they are doing / funny parts) concisely in your text explanation; if the user ONLY asked to describe/list what happens without asking to edit, return that chronological list with no unnecessary edits."
        if visual_plan is not None
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
            "`isolate_highlight_clips` snapped strictly to clean transcript boundaries using the Heatmap Peaks & SRC timestamps below.\n"
            "6. Timeline Effects Coordinate Rule: `set_clip_speed` (start_time/end_time), `add_zoom_effect`, `add_drawing`, and `add_sfx_clip` "
            "ALWAYS use TIMELINE seconds (`TL` timestamps in transcript below & `Playhead Position`), NOT source seconds!\n"
            "7. 'Speed up this part' / 'slow down this part': if `Selected Time Range` is set, use `[selectedTimeRange.start, selectedTimeRange.end]`; "
            "otherwise use `[playheadPosition, playheadPosition + 4.0]` (or spoken phrase window) with `set_clip_speed` (default `1.5` speed up, `0.5` slow down).\n"
            "8. 'Zoom in when he says <phrase>': find the exact `TL` timestamp window where `<phrase>` is spoken in the transcript below and call `add_zoom_effect`.\n"
            "9. 'Add a sound effect here' or 'Add sound effects that are applicable for the video': inspect EVERY sound's `id`, `name`, `tags`, and `description` in `Available Custom MP3 SFX Library`. For a targeted SFX request, call `add_sfx_clip` at `playheadPosition` (or the matched `TL` timestamp). For a whole-video / 'applicable sound effects' request, follow Gemini Visual Planner's `planned_steps` / `detected_moments` and emit multiple well-spaced `add_sfx_clip` calls (>= 2s apart) matched to each moment's mood using exact `asset_id` and `sfx_name`, varying sounds across different moments. To trim an existing timeline sound effect (skip leading audio or shorten duration), call `trim_sfx_clip(sfx_id?, sfx_name?, trim_start?, duration?)`. If the library is empty, politely tell the user to upload `.mp3` files in the SFX Library tab.\n"
            "10. 'Draw a line/arrow/circle pointing here': call `add_drawing` over `[playheadPosition, playheadPosition + 2.5]` using normalized `0.0..1.0` coordinates (e.g. arrow from `(0.24, 0.26)` to `(0.50, 0.48)`).\n"
            "11. CROP vs ZOOM RULE: When the user asks to CROP (e.g. 'crop to creator face', 'replace the zoom with crop', 'crop showing reaction but only for a while'), you MUST call `set_video_crop(x, y, w, h, start?, end?)` — NEVER call `add_zoom_effect` when the user asks for a crop! If the user says 'only for a while', 'at the funny part', or 'during the reaction', pass `start` and `end` (in Timeline Seconds `TL`) to `set_video_crop`. If the user also asks to remove or replace an existing zoom, call BOTH `delete_zoom_effect` (using the ID from `Active Zoom Effects`) AND `set_video_crop`."
            f"{first_note}{campaign_block}{visual_plan_block}\n\n"
            f"Current Project Context:\n"
            f"- Original Duration: {project_state.sourceVideo.originalDuration:.2f}s\n"
            f"- Playhead Position (TL): {project_state.workspaceState.playheadPosition:.2f}s\n"
            f"- Selected Time Range (TL): {json.dumps(selected_range)}\n"
            f"- Selected Clip IDs: {project_state.workspaceState.selectedClipIds}\n"
            f"- Aspect Ratio: {project_state.workspaceState.targetAspectRatio}\n"
            f"- Current Video Crop: {project_state.workspaceState.crop.model_dump_json() if project_state.workspaceState.crop else 'None (full frame)'}\n"
            f"- Active Clips: {json.dumps([c.model_dump() for c in project_state.tracks.clips[:25]])}\n"
            f"- Active Zoom Effects: {json.dumps([z.model_dump() for z in project_state.tracks.zooms[:20]])}\n"
            f"- Active Drawings: {json.dumps([d.model_dump() for d in project_state.tracks.drawings[:20]])}\n"
            f"- Active SFX Clips: {json.dumps([s.model_dump() for s in project_state.tracks.sfx[:20]])}\n"
            f"- Available Custom MP3 SFX Library: {json.dumps(sfx_catalog)}\n"
            f"- Current Caption Style: {project_state.captionStyle.model_dump_json()}\n"
            f"- Protected Manually-Edited Captions (DO NOT OVERWRITE): {json.dumps(protected_captions[:25])}\n"
            f"- Auto Captions Sample: {json.dumps(editable_captions)}\n"
            f"- Pre-detected Silence/Pause Gaps (>0.7s): {json.dumps(pauses_slice)}\n"
            f"- YouTube Most Replayed Heatmap Peaks: {json.dumps(heatmap_peaks[:5])}\n"
            f"- Timestamped Transcript Segments (TL = timeline sec, SRC = source sec):\n{t_text}\n"
        )

    system_prompt = _build_editor_system_prompt(pauses[:35], transcript_text)

    if not has_groq:
        if visual_plan is not None and (
            visual_plan.get("planned_steps") or visual_plan.get("detected_moments")
        ):
            direct_calls = convert_visual_plan_to_tool_calls(visual_plan, sfx_assets)
            direct_calls = _enrich_sfx_tool_calls(direct_calls, sfx_assets or [])
            direct_calls, _ = _enforce_crop_and_zoom_intent(
                prompt, direct_calls, project_state, visual_plan=visual_plan, transcript=transcript
            )
            direct_calls, _ = _enforce_caption_and_timeline_tools(
                prompt,
                direct_calls,
                project_state,
                pauses=pauses,
                visual_plan=visual_plan,
                transcript=transcript,
                sfx_assets=sfx_assets,
            )
            if direct_calls:
                return AiEditResponse(
                    message=f"{visual_plan.get('scene_summary', 'Planned visual edits.')} Executed {len(direct_calls)} planned edit(s).",
                    tool_calls=direct_calls,
                    route_used=f"gemini ({planner_model}) [direct-plan]",
                )
        gemini_backup = _run_gemini_editor_fallback(
            prompt,
            system_prompt,
            project_state,
            transcript,
            sfx_assets=sfx_assets,
            visual_plan=visual_plan,
        )
        if gemini_backup is not None:
            return gemini_backup
        return _deterministic_edit_fallback(
            prompt, project_state, transcript, pauses, sfx_assets=sfx_assets
        )

    client = Groq(api_key=groq_key, timeout=25.0)

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for msg in history[-6:]:
        role = msg.get("role", "user")
        if role in {"user", "assistant"}:
            messages.append({"role": role, "content": str(msg.get("content", ""))})
    messages.append({"role": "user", "content": prompt})

    compact_system_prompt = _build_editor_system_prompt(
        pauses[:15],
        _format_timeline_transcript_for_llm(
            transcript,
            project_state.tracks.clips,
            max_segments=30,
            max_chars=3200,
            prefer_start=prefer_start,
        ),
    )

    tool_models = get_tool_models_chain()[:4]
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
                report_model_failure("groq", model_id, exc)
                break
        if completion is not None:
            break

    if completion is None:
        if visual_plan is not None and (
            visual_plan.get("planned_steps") or visual_plan.get("detected_moments")
        ):
            direct_calls = convert_visual_plan_to_tool_calls(visual_plan, sfx_assets)
            direct_calls = _enrich_sfx_tool_calls(direct_calls, sfx_assets or [])
            direct_calls, _ = _enforce_crop_and_zoom_intent(
                prompt, direct_calls, project_state, visual_plan=visual_plan, transcript=transcript
            )
            direct_calls, _ = _enforce_caption_and_timeline_tools(
                prompt,
                direct_calls,
                project_state,
                pauses=pauses,
                visual_plan=visual_plan,
                transcript=transcript,
                sfx_assets=sfx_assets,
            )
            if direct_calls:
                return AiEditResponse(
                    message=f"{visual_plan.get('scene_summary', 'Planned visual edits.')} Executed {len(direct_calls)} planned edit(s).",
                    tool_calls=direct_calls,
                    route_used=f"gemini ({planner_model}) [direct-plan]",
                )
        gemini_backup = _run_gemini_editor_fallback(
            prompt,
            system_prompt,
            project_state,
            transcript,
            sfx_assets=sfx_assets,
            visual_plan=visual_plan,
        )
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
    tool_calls = _enrich_sfx_tool_calls(tool_calls, sfx_assets or [])
    tool_calls, crop_zoom_note = _enforce_crop_and_zoom_intent(
        prompt, tool_calls, project_state, visual_plan=visual_plan, transcript=transcript
    )
    tool_calls, style_tool_note = _enforce_caption_and_timeline_tools(
        prompt,
        tool_calls,
        project_state,
        pauses=pauses,
        visual_plan=visual_plan,
        transcript=transcript,
        sfx_assets=sfx_assets,
    )
    if any(tc.name == "generate_auto_captions" for tc in tool_calls):
        wants_captions = True

    auto_captions: Optional[List[CaptionItem]] = (
        _build_captions_for_isolated_or_range(transcript, isolated_clips, cap_range)
        if wants_captions
        else None
    )

    explanation = (choice.content or "").strip()
    extra_notes = "; ".join(n for n in (crop_zoom_note, style_tool_note) if n)
    if extra_notes and extra_notes.lower() not in explanation.lower():
        explanation = f"{explanation} ({extra_notes}.)".strip() if explanation else f"{extra_notes}."
    if not explanation:
        names = ", ".join(tc.name for tc in tool_calls)
        explanation = f"Applied timeline edits: {names}." if names else "No timeline changes were required."

    base_route = f"groq ({used_model}) [backup]" if used_backup else f"groq ({used_model})"
    return AiEditResponse(
        message=explanation,
        tool_calls=tool_calls,
        auto_captions=auto_captions,
        route_used=f"gemini ({planner_model}) -> {base_route}" if planner_model else base_route,
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
    prompt_keywords = _extract_prompt_keywords(prompt)
    seg_list = (transcript.get("segments") or []) if transcript else []

    for idx, pk in enumerate(sorted(peaks, key=lambda x: x["intensity"], reverse=True)[:count]):
        center = (pk["start_time"] + pk["end_time"]) / 2.0
        if (pk["end_time"] - pk["start_time"]) > 90.0 and seg_list:
            lo = _bisect_dict_start(seg_list, max(0.0, pk["start_time"] - 5.0))
            best_key = (-1.0, -float("inf"))
            for s_item in seg_list[lo:]:
                st = float(s_item.get("start", 0.0))
                if st > pk["end_time"] + 5.0:
                    break
                sc = _score_segment_interest(str(s_item.get("text", "")), prompt_keywords)
                cand_key = (sc, -abs(st - center))
                if cand_key > best_key:
                    best_key = cand_key
                    center = st
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


def _extract_spoken_phrase_window(
    prompt: str,
    transcript: Optional[Dict[str, Any]],
    clips: List[Any],
) -> Optional[Tuple[float, float]]:
    match = re.search(
        r"(?:when\s+(?:he|she|they|i|we|you)\s+say(?:s)?|when\s+.*?\bsays?|where\s+.*?\bsays?|after\s+.*?\bsays?)\s+[\"']?([^\"',.!?\n]+)[\"']?",
        prompt,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    raw_phrase = re.sub(r"\b(please|right\s+here|right\s+now)\b", "", match.group(1), flags=re.IGNORECASE).strip()
    if not raw_phrase:
        return None
    return _find_phrase_timeline_window(raw_phrase, transcript, clips)


def _deterministic_edit_fallback(
    prompt: str,
    project_state: ProjectState,
    transcript: Optional[Dict[str, Any]],
    pauses: List[Dict[str, float]],
    sfx_assets: Optional[List[SfxAsset]] = None,
) -> AiEditResponse:
    """Deterministic command parser fallback when GROQ_API_KEY is not configured."""
    lowered = prompt.lower()
    tool_calls: List[ToolCallSchema] = []
    auto_captions: Optional[List[CaptionItem]] = None
    notes: List[str] = []
    clips = project_state.tracks.clips
    total_tl_dur = max(
        0.5,
        sum(c.duration for c in clips)
        if clips
        else float(project_state.sourceVideo.originalDuration or 10.0),
    )
    playhead = max(0.0, float(project_state.workspaceState.playheadPosition or 0.0))
    phrase_win = _extract_spoken_phrase_window(prompt, transcript, clips)

    has_sfx_target = any(
        k in lowered
        for k in ("sound effect", "sound effects", "sfx", "audio effect", "audio effects", "mp3")
    )
    first_n = (
        re.search(
            r"(?:cut|remove|trim)\s+(?:the\s+)?first\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)",
            lowered,
        )
        if not has_sfx_target
        else None
    )
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

    # Speed control rules ("speed up this part", "slow down", "1.5x")
    has_speed_word = any(
        k in lowered
        for k in ("speed", "fast", "slow", "playback", "tempo", "faster", "slower", "timelapse", "time-lapse")
    )
    has_zoom_or_crop_word = any(k in lowered for k in ("zoom", "punch", "scale", "crop"))
    speed_match = (
        re.search(r"(\d+(?:\.\d+)?)\s*x\b", lowered)
        if (has_speed_word or not has_zoom_or_crop_word)
        else None
    )
    wants_speed = bool(
        speed_match
        or "speed up" in lowered
        or "fast forward" in lowered
        or "faster" in lowered
        or "slow down" in lowered
        or "slow motion" in lowered
        or "slow-mo" in lowered
        or "slowmo" in lowered
        or "speed" in lowered
    )
    if wants_speed:
        if speed_match:
            target_spd = round(max(0.25, min(4.0, float(speed_match.group(1)))), 2)
        elif any(k in lowered for k in ("slow down", "slow motion", "slow-mo", "slowmo")):
            target_spd = 0.5
        else:
            target_spd = 1.5

        if any(k in lowered for k in ("whole", "entire", "full", "all clip")) and clips:
            for c in clips:
                tool_calls.append(
                    ToolCallSchema(
                        id=f"tc_{uuid.uuid4().hex[:8]}",
                        name="set_clip_speed",
                        arguments={"clip_id": c.id, "speed": target_spd},
                    )
                )
            notes.append(f"Set full timeline speed to {target_spd}x")
        elif project_state.workspaceState.selectedTimeRange is not None:
            rng = project_state.workspaceState.selectedTimeRange
            tool_calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="set_clip_speed",
                    arguments={
                        "start_time": round(rng.start, 2),
                        "end_time": round(rng.end, 2),
                        "speed": target_spd,
                    },
                )
            )
            notes.append(f"Set {rng.start:.1f}s–{rng.end:.1f}s speed to {target_spd}x")
        elif phrase_win is not None:
            s_t = max(0.0, phrase_win[0] - 0.1)
            e_t = min(total_tl_dur, max(s_t + 2.0, phrase_win[1] + 0.4))
            tool_calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="set_clip_speed",
                    arguments={
                        "start_time": round(s_t, 2),
                        "end_time": round(e_t, 2),
                        "speed": target_spd,
                    },
                )
            )
            notes.append(f"Set {s_t:.1f}s–{e_t:.1f}s speed to {target_spd}x")
        else:
            s_t = min(max(0.0, total_tl_dur - 1.5), playhead)
            e_t = min(total_tl_dur, max(s_t + 0.5, s_t + 4.0))
            tool_calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="set_clip_speed",
                    arguments={
                        "start_time": round(s_t, 2),
                        "end_time": round(e_t, 2),
                        "speed": target_spd,
                    },
                )
            )
            notes.append(f"Set {s_t:.1f}s–{e_t:.1f}s speed to {target_spd}x")

    # Zoom rules ("zoom in when he says ...", "punch-in zoom", "zoom out")
    wants_remove_zoom = bool(
        re.search(
            r"\b(remove|delete|clear|no|without|replace|stop|take\s+out|get\s+rid\s+of)\b.{0,30}\bzoom\b",
            lowered,
        )
    )
    if ("zoom" in lowered or "punch in" in lowered or "punch-in" in lowered) and not wants_remove_zoom:
        if "punch" in lowered:
            kind = "punch_in"
            s_scale, e_scale, easing = 1.0, 1.4, "instant"
        elif "zoom out" in lowered or "out" in lowered:
            kind = "zoom_out"
            s_scale, e_scale, easing = 1.4, 1.0, "ease_in_out"
        else:
            kind = "zoom_in"
            s_scale, e_scale, easing = 1.0, 1.4, "ease_in_out"

        if phrase_win is not None:
            z_start = max(0.0, round(phrase_win[0] - 0.1, 2))
            z_end = round(min(total_tl_dur, max(z_start + 1.6, phrase_win[1] + 0.5)), 2)
        else:
            z_start = round(min(max(0.0, total_tl_dur - 1.0), playhead), 2)
            z_end = round(min(total_tl_dur, z_start + 2.0), 2)
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="add_zoom_effect",
                arguments={
                    "start": z_start,
                    "end": z_end,
                    "kind": kind,
                    "start_scale": s_scale,
                    "end_scale": e_scale,
                    "focus_x": 0.5,
                    "focus_y": 0.5,
                    "easing": easing,
                },
            )
        )
        notes.append(f"Added {kind.replace('_', ' ')} ({z_start:.1f}s–{z_end:.1f}s)")

    # Drawing overlay rules ("draw an arrow pointing here", "circle here", "draw a line")
    if any(k in lowered for k in ("draw", "arrow", "circle", "box", "rect", "line", "highlight")):
        if "circle" in lowered:
            d_tool = "circle"
            pts = [{"x": 0.34, "y": 0.28}, {"x": 0.66, "y": 0.66}]
        elif "box" in lowered or "rect" in lowered:
            d_tool = "rect"
            pts = [{"x": 0.28, "y": 0.25}, {"x": 0.72, "y": 0.72}]
        elif "highlight" in lowered:
            d_tool = "highlight"
            pts = [{"x": 0.25, "y": 0.55}, {"x": 0.75, "y": 0.55}]
        elif "line" in lowered and "arrow" not in lowered:
            d_tool = "line"
            pts = [{"x": 0.25, "y": 0.55}, {"x": 0.75, "y": 0.55}]
        else:
            d_tool = "arrow"
            pts = [{"x": 0.24, "y": 0.26}, {"x": 0.50, "y": 0.48}]

        color = "#EF4444"
        if "yellow" in lowered or "gold" in lowered:
            color = "#FACC15"
        elif "green" in lowered:
            color = "#22C55E"
        elif "blue" in lowered:
            color = "#38BDF8"
        elif "white" in lowered:
            color = "#FFFFFF"

        if phrase_win is not None:
            d_start = max(0.0, round(phrase_win[0] - 0.1, 2))
            d_end = round(min(total_tl_dur, max(d_start + 2.0, phrase_win[1] + 0.8)), 2)
        else:
            d_start = round(min(max(0.0, total_tl_dur - 1.0), playhead), 2)
            d_end = round(min(total_tl_dur, d_start + 2.5), 2)

        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="add_drawing",
                arguments={
                    "start": d_start,
                    "end": d_end,
                    "tool": d_tool,
                    "points": pts,
                    "color": color,
                    "stroke_width": 6,
                    "opacity": 1.0,
                },
            )
        )
        notes.append(f"Drew {d_tool} ({d_start:.1f}s–{d_end:.1f}s)")

    # Sound effect rules ("add a sound effect here", "add sound effects that are applicable")
    wants_remove_sfx = bool(
        re.search(
            r"\b(remove|delete|clear)\b.{0,25}\b(sfx|sound\s+effects?|audio\s+effects?)\b",
            lowered,
        )
    )
    has_sfx_word = any(
        k in lowered
        for k in ("sound effect", "sound effects", "sfx", "audio effect", "audio effects", "mp3")
    )
    wants_trim_sfx = bool(
        has_sfx_word
        and (project_state.tracks.sfx or [])
        and not wants_remove_sfx
        and any(k in lowered for k in ("trim", "shorten", "cut", "skip", "shorter", "duration"))
        and not re.search(r"\b(add|put|place|insert)\b.{0,20}\b(sound|sfx|mp3|audio)\b", lowered)
    )
    if has_sfx_word and not wants_remove_sfx and not wants_trim_sfx:
        assets = sfx_assets or []
        if assets:
            sfx_calls = _build_smart_sfx_tool_calls(
                prompt=prompt,
                project_state=project_state,
                sfx_assets=assets,
                visual_plan=None,
                transcript=transcript,
            )
            if sfx_calls:
                tool_calls.extend(sfx_calls)
                placed_summary = ", ".join(
                    f"{tc.arguments.get('sfx_name', 'SFX')} @ {float(tc.arguments.get('start', 0.0)):.1f}s"
                    for tc in sfx_calls[:4]
                )
                extra = f" (+{len(sfx_calls) - 4} more)" if len(sfx_calls) > 4 else ""
                notes.append(f"Added {len(sfx_calls)} SFX ({placed_summary}{extra})")
        else:
            notes.append(
                "No custom MP3 SFX uploaded yet — open the SFX Library tab on the left to upload your MP3 files"
            )

    # Crop rules ("crop to top-left webcam", "crop center", "reset crop")
    if "crop" in lowered or "uncrop" in lowered:
        if any(k in lowered for k in ("reset crop", "uncrop", "full frame", "remove crop", "clear crop")):
            c_rect = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
            c_label = "Reset crop to full frame"
        elif any(k in lowered for k in ("top-left", "top left", "webcam", "facecam")):
            c_rect = {"x": 0.0, "y": 0.0, "w": 0.4, "h": 0.45}
            c_label = "Cropped to top-left webcam region"
        elif any(k in lowered for k in ("top-right", "top right")):
            c_rect = {"x": 0.6, "y": 0.0, "w": 0.4, "h": 0.45}
            c_label = "Cropped to top-right region"
        elif any(k in lowered for k in ("bottom-left", "bottom left")):
            c_rect = {"x": 0.0, "y": 0.55, "w": 0.4, "h": 0.45}
            c_label = "Cropped to bottom-left region"
        elif any(k in lowered for k in ("bottom-right", "bottom right")):
            c_rect = {"x": 0.6, "y": 0.55, "w": 0.4, "h": 0.45}
            c_label = "Cropped to bottom-right region"
        else:
            c_rect = {"x": 0.2, "y": 0.05, "w": 0.6, "h": 0.9}
            c_label = "Cropped to center focus region"
        tool_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="set_video_crop",
                arguments=c_rect,
            )
        )
        notes.append(c_label)

    tool_calls, fallback_crop_note = _enforce_crop_and_zoom_intent(
        prompt, tool_calls, project_state, visual_plan=None, transcript=transcript
    )
    if fallback_crop_note and fallback_crop_note not in notes:
        notes.append(fallback_crop_note)
    tool_calls, fallback_style_note = _enforce_caption_and_timeline_tools(
        prompt,
        tool_calls,
        project_state,
        pauses=pauses,
        visual_plan=None,
        transcript=transcript,
        sfx_assets=sfx_assets,
    )
    if fallback_style_note and fallback_style_note not in notes:
        notes.append(fallback_style_note)
    if any(tc.name == "generate_auto_captions" for tc in tool_calls) and auto_captions is None and transcript:
        auto_captions = build_captions_from_transcript(transcript)

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
    campaign_id: Optional[str] = None,
    campaign_rules: Optional[List[str]] = None,
    sfx_dir: Optional[Path] = None,
) -> AiEditResponse:
    transcript = load_cached_transcript(project_state.projectId, transcripts_dir)
    sfx_assets = list_sfx_assets(sfx_dir) if sfx_dir is not None else []

    yt_match = re.search(
        r"https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/\S+", prompt, re.IGNORECASE
    )
    style_clone_phrases = (
        "edit like",
        "like this",
        "copy style",
        "copy the style",
        "style of",
        "match this",
        "same style",
    )
    if yt_match and any(p in prompt.lower() for p in style_clone_phrases):
        ref_url = yt_match.group(0).rstrip(".,);\"'")
        tl_text = _format_timeline_transcript_for_llm(
            transcript, project_state.tracks.clips, max_segments=40, max_chars=3500
        )
        _prof, tool_calls, auto_captions, summary_msg, route_label = (
            clone_reference_style_for_project(
                reference_url=ref_url,
                project_state=project_state,
                transcript=transcript,
                sfx_assets=sfx_assets,
                downloads_dir=transcripts_dir.parent / "downloads",
                custom_instruction=prompt,
                transcript_tl_text=tl_text,
            )
        )
        return AiEditResponse(
            message=summary_msg,
            tool_calls=tool_calls,
            auto_captions=auto_captions,
            route_used=f"reference-style ({route_label})",
        )

    intent, router_used = _classify_prompt_intent_with_ai(prompt)
    print(f"[AI Edit] intent={intent} ({router_used})", flush=True)

    def _with_router_tag(res: AiEditResponse) -> AiEditResponse:
        if router_used != "keyword-fast-path":
            res.route_used = f"{res.route_used} [router: {router_used}]"
        return res

    if intent == "viral_discovery":
        try:
            return _with_router_tag(
                _run_gemini_viral_pipeline(
                    prompt,
                    project_state,
                    transcript,
                    campaign_id=campaign_id,
                    campaign_rules=campaign_rules,
                )
            )
        except Exception as exc:
            fallback = _deterministic_viral_fallback(
                prompt, project_state, transcript, _extract_top_heatmap_peaks(project_state)
            )
            fallback.message = f"[Gemini fallback: {exc}] {fallback.message}"
            return _with_router_tag(fallback)

    visual_plan: Optional[Dict[str, Any]] = None
    planner_model: Optional[str] = None
    if should_use_visual_planner(prompt, project_state):
        try:
            tl_text = _format_timeline_transcript_for_llm(
                transcript,
                project_state.tracks.clips,
                max_segments=40,
                max_chars=3500,
            )
            planned = plan_visual_edits_with_gemini(
                prompt=prompt,
                project_state=project_state,
                transcript_tl_text=tl_text,
                downloads_dir=transcripts_dir.parent / "downloads",
                sfx_assets=sfx_assets,
            )
            if planned is not None:
                visual_plan, planner_model = planned
        except Exception:
            visual_plan = None
            planner_model = None

    try:
        return _with_router_tag(
            _run_groq_fast_editor(
                prompt,
                project_state,
                transcript,
                history or [],
                campaign_id=campaign_id,
                campaign_rules=campaign_rules,
                sfx_assets=sfx_assets,
                visual_plan=visual_plan,
                planner_model=planner_model,
            )
        )
    except Exception as exc:
        pauses = detect_pause_gaps(transcript, min_gap=0.7) if transcript else []
        if visual_plan is not None and (
            visual_plan.get("planned_steps") or visual_plan.get("detected_moments")
        ):
            direct_calls = convert_visual_plan_to_tool_calls(visual_plan, sfx_assets)
            direct_calls = _enrich_sfx_tool_calls(direct_calls, sfx_assets or [])
            direct_calls, _ = _enforce_crop_and_zoom_intent(
                prompt, direct_calls, project_state, visual_plan=visual_plan, transcript=transcript
            )
            direct_calls, _ = _enforce_caption_and_timeline_tools(
                prompt,
                direct_calls,
                project_state,
                pauses=pauses,
                visual_plan=visual_plan,
                transcript=transcript,
                sfx_assets=sfx_assets,
            )
            if direct_calls:
                return _with_router_tag(
                    AiEditResponse(
                        message=f"{visual_plan.get('scene_summary', 'Planned visual edits.')} Executed {len(direct_calls)} planned edit(s).",
                        tool_calls=direct_calls,
                        route_used=f"gemini ({planner_model}) [direct-plan]",
                    )
                )
        fallback = _deterministic_edit_fallback(
            prompt, project_state, transcript, pauses, sfx_assets=sfx_assets
        )
        fallback.message = f"[Groq fallback: {exc}] {fallback.message}"
        return _with_router_tag(fallback)
