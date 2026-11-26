import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse
import uuid

import httpx

from schemas import ProjectState, SfxAsset, ToolCallSchema
from services.sfx_library import save_uploaded_sfx

TTSAI_BASE_URL = "https://api.tts.ai/v1"
TTSAI_ORIGIN = "https://api.tts.ai/"
TTSAI_PLACEHOLDER = "your_ttsai_api_key_here"
DEFAULT_TTS_MODEL = "kokoro"
DEFAULT_TTS_VOICE = "af_bella"

ANONYMOUS_ALLOWED_MODELS = {"piper", "vits", "melotts", "kokoro"}
CURATED_TTS_VOICES: List[Dict[str, str]] = [
    {"id": "af_bella", "name": "Bella (Warm Female)", "model": "kokoro", "language": "en-US"},
    {"id": "af_heart", "name": "Heart (Expressive Female)", "model": "kokoro", "language": "en-US"},
    {"id": "af_nicole", "name": "Nicole (Clean Narration)", "model": "kokoro", "language": "en-US"},
    {"id": "am_adam", "name": "Adam (Bold Male)", "model": "kokoro", "language": "en-US"},
    {"id": "am_michael", "name": "Michael (Creator Male)", "model": "kokoro", "language": "en-US"},
    {"id": "bf_emma", "name": "Emma (UK Female)", "model": "kokoro", "language": "en-GB"},
    {"id": "bm_george", "name": "George (UK Male)", "model": "kokoro", "language": "en-GB"},
]


def get_ttsai_api_key() -> str:
    return os.getenv("TTSAI_API_KEY", "").strip()


def is_ttsai_key_configured() -> bool:
    key = get_ttsai_api_key()
    return bool(key and key != TTSAI_PLACEHOLDER)


def _sanitize_tts_text(text: str, max_chars: int = 480) -> str:
    cleaned = " ".join((text or "").strip().split())
    if len(cleaned) <= max_chars:
        return cleaned
    clipped = cleaned[:max_chars].rsplit(" ", 1)[0].strip()
    return clipped or cleaned[:max_chars]


def _resolve_result_url(raw_url: str) -> str:
    u = (raw_url or "").strip()
    if not u:
        return ""
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return urljoin(TTSAI_ORIGIN, u)


def list_tts_voices(timeout: float = 8.0) -> Dict[str, Any]:
    """Lists curated + live TTS.ai voices (works anonymously or with optional TTSAI_API_KEY)."""
    api_key = get_ttsai_api_key() if is_ttsai_key_configured() else ""
    headers: Dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    voices: List[Dict[str, Any]] = list(CURATED_TTS_VOICES)
    source = "curated"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(f"{TTSAI_BASE_URL}/voices/", headers=headers)
            if resp.status_code == 404:
                resp = client.get(f"{TTSAI_BASE_URL}/voices", headers=headers)
            if resp.status_code < 400:
                payload = resp.json()
                raw_items = (
                    payload
                    if isinstance(payload, list)
                    else payload.get("voices")
                    or payload.get("data")
                    or payload.get("results")
                    or []
                )
                seen = {v["id"] for v in voices}
                for item in raw_items[:80]:
                    if not isinstance(item, dict):
                        continue
                    vid = str(item.get("id") or item.get("voice_id") or item.get("voice") or "").strip()
                    if not vid or vid in seen:
                        continue
                    seen.add(vid)
                    voices.append(
                        {
                            "id": vid,
                            "name": str(item.get("name") or vid),
                            "model": str(item.get("model") or DEFAULT_TTS_MODEL),
                            "language": str(item.get("language") or item.get("lang") or "en"),
                        }
                    )
                source = "tts.ai"
    except Exception:
        pass

    return {
        "voices": voices,
        "default_model": DEFAULT_TTS_MODEL,
        "default_voice": DEFAULT_TTS_VOICE,
        "key_configured": bool(api_key),
        "source": source,
    }


def synthesize_tts_to_mp3_bytes(
    text: str,
    voice: Optional[str] = None,
    model: Optional[str] = None,
    speed: float = 1.0,
    timeout: float = 45.0,
) -> Tuple[bytes, str, str]:
    """
    Generates spoken MP3 audio using TTS.ai's REST API (/v1/tts/ + /v1/speech/results/).
    Anonymous mode works without an API key (up to 500 chars/req on kokoro/piper/vits/melotts);
    setting TTSAI_API_KEY in backend/.env unlocks 15,000 bonus credits and 5,000 chars/req.
    Returns (mp3_bytes, model_used, voice_used).
    """
    has_key = is_ttsai_key_configured()
    max_chars = 4800 if has_key else 480
    clean_text = _sanitize_tts_text(text, max_chars=max_chars)
    if not clean_text:
        raise ValueError("Text for voiceover cannot be empty.")

    chosen_model = (model or DEFAULT_TTS_MODEL).strip() or DEFAULT_TTS_MODEL
    if not has_key and chosen_model.lower() not in ANONYMOUS_ALLOWED_MODELS:
        chosen_model = DEFAULT_TTS_MODEL
    chosen_voice = (voice or DEFAULT_TTS_VOICE).strip() or DEFAULT_TTS_VOICE
    safe_speed = round(max(0.5, min(2.0, float(speed or 1.0))), 2)

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if has_key:
        headers["Authorization"] = f"Bearer {get_ttsai_api_key()}"

    payload: Dict[str, Any] = {
        "model": chosen_model,
        "voice": chosen_voice,
        "text": clean_text,
        "format": "mp3",
        "speed": safe_speed,
    }

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.post(f"{TTSAI_BASE_URL}/tts/", headers=headers, json=payload)
        if resp.status_code >= 400:
            detail = resp.text.strip()[:240]
            raise RuntimeError(f"TTS.ai HTTP {resp.status_code}: {detail}")

        ctype = (resp.headers.get("content-type") or "").lower()
        if ctype.startswith("audio/") or resp.content[:3] == b"ID3":
            if len(resp.content) < 128:
                raise RuntimeError("TTS.ai returned an empty audio stream.")
            return resp.content, chosen_model, chosen_voice

        job_data = resp.json()
        if not isinstance(job_data, dict):
            raise RuntimeError("Unexpected JSON payload from TTS.ai.")

        result_url = _resolve_result_url(str(job_data.get("result_url") or ""))
        job_uuid = str(job_data.get("uuid") or job_data.get("job_id") or "").strip()

        if not result_url:
            if not job_uuid:
                raise RuntimeError(f"TTS.ai did not return uuid or result_url: {job_data}")
            deadline = time.monotonic() + max(12.0, timeout - 3.0)
            wait_sec = 0.6
            while time.monotonic() < deadline:
                poll_resp = client.get(
                    f"{TTSAI_BASE_URL}/speech/results/",
                    params={"uuid": job_uuid},
                    headers=headers,
                )
                if poll_resp.status_code >= 400:
                    raise RuntimeError(
                        f"TTS.ai poll HTTP {poll_resp.status_code}: {poll_resp.text[:180]}"
                    )
                poll_json = poll_resp.json()
                if not isinstance(poll_json, dict):
                    raise RuntimeError("Unexpected poll response payload from TTS.ai.")
                status = str(poll_json.get("status") or "").strip().lower()
                if status in {"completed", "done", "succeeded", "success"}:
                    result_url = _resolve_result_url(str(poll_json.get("result_url") or ""))
                    if result_url:
                        break
                if status in {"failed", "error", "cancelled", "canceled"}:
                    err_msg = poll_json.get("error") or poll_json.get("message") or status
                    raise RuntimeError(f"TTS.ai synthesis failed: {err_msg}")
                time.sleep(wait_sec)
                wait_sec = min(1.5, wait_sec + 0.15)

        if not result_url:
            raise RuntimeError("Timed out waiting for TTS.ai audio synthesis.")

        same_origin = urlparse(result_url).netloc == urlparse(TTSAI_ORIGIN).netloc
        dl_headers = {"Authorization": f"Bearer {get_ttsai_api_key()}"} if (has_key and same_origin) else None
        audio_resp = client.get(result_url, headers=dl_headers)
        dl_ctype = (audio_resp.headers.get("content-type") or "").lower()
        if (
            audio_resp.status_code >= 400
            or len(audio_resp.content) < 128
            or dl_ctype.startswith("text/")
            or dl_ctype.startswith("application/json")
        ):
            raise RuntimeError(
                f"Failed downloading synthesized audio ({audio_resp.status_code})."
            )
        return audio_resp.content, chosen_model, chosen_voice


def synthesize_tts_to_sfx_asset(
    sfx_dir: Path,
    text: str,
    voice: Optional[str] = None,
    model: Optional[str] = None,
    speed: float = 1.0,
    name: Optional[str] = None,
    base_url: str = "http://localhost:8000",
) -> SfxAsset:
    """Synthesizes TTS.ai MP3 audio and stores it as a playable/exportable SFX asset."""
    clean_text = _sanitize_tts_text(text, max_chars=4800 if is_ttsai_key_configured() else 480)
    audio_bytes, used_model, used_voice = synthesize_tts_to_mp3_bytes(
        clean_text, voice=voice, model=model, speed=speed
    )
    snippet = clean_text[:34].rstrip()
    asset_name = (name or "").strip() or (
        f"TTS: {snippet}{'...' if len(clean_text) > 34 else ''}"
    )
    filename = f"tts_{uuid.uuid4().hex[:8]}.mp3"
    return save_uploaded_sfx(
        sfx_dir,
        filename,
        audio_bytes,
        name=asset_name,
        tags=["voiceover", "tts", used_voice.lower()],
        description=f"TTS.ai ({used_model}/{used_voice}): {clean_text[:140]}",
        base_url=base_url,
    )


def _timeline_to_source_start(project_state: ProjectState, tl_sec: float) -> float:
    clips = project_state.tracks.clips or []
    if not clips:
        return round(max(0.0, tl_sec), 2)
    for c in clips:
        c_end_tl = float(c.timelineStart) + float(c.duration)
        if float(c.timelineStart) <= tl_sec <= c_end_tl + 0.05:
            spd = max(0.25, min(4.0, float(getattr(c, "speed", 1.0) or 1.0)))
            return round(float(c.sourceStart) + max(0.0, tl_sec - float(c.timelineStart)) * spd, 2)
    return round(max(0.0, float(clips[0].sourceStart)), 2)


def _safe_float(val: Any, default: float) -> float:
    if val is None or isinstance(val, bool):
        return default
    try:
        num = float(val)
        return num if num == num else default
    except (TypeError, ValueError):
        return default


def _active_clip_speed_at_tl(project_state: ProjectState, tl_sec: float) -> float:
    for c in project_state.tracks.clips or []:
        c_end_tl = float(c.timelineStart) + float(c.duration)
        if float(c.timelineStart) <= tl_sec <= c_end_tl + 0.05:
            return max(0.25, min(4.0, float(getattr(c, "speed", 1.0) or 1.0)))
    if project_state.tracks.clips:
        return max(0.25, min(4.0, float(getattr(project_state.tracks.clips[0], "speed", 1.0) or 1.0)))
    return 1.0


def materialize_voiceover_tool_call(
    raw_args: Dict[str, Any],
    project_state: ProjectState,
    sfx_dir: Optional[Path],
    existing_tool_calls: Sequence[ToolCallSchema],
    effective_dur: Optional[float] = None,
) -> Tuple[List[ToolCallSchema], Optional[SfxAsset], Optional[str]]:
    """
    Shared converter used by AI Chat & Reference Workflow: turns a `generate_voiceover`
    call into real `add_sfx_clip` + synced `add_caption` calls and returns the created SfxAsset.
    """
    spoken_text = " ".join(
        str(raw_args.get("text") or raw_args.get("script") or "").strip().split()
    )
    if not spoken_text:
        return [], None, None

    clips = project_state.tracks.clips or []
    clip0 = clips[0] if clips else None
    max_src = float(project_state.sourceVideo.originalDuration or 9999.0)
    raw_tl = _safe_float(raw_args.get("start"), 0.0)
    if effective_dur and effective_dur > 0.5:
        tl_start = round(max(0.0, min(effective_dur - 0.4, raw_tl)), 2)
    else:
        tl_start = round(max(0.0, raw_tl), 2)

    is_opening = tl_start <= 1.25
    also_add_caption = bool(raw_args.get("also_add_caption", True))
    voice = str(raw_args.get("voice") or "").strip() or None
    speed = max(0.7, min(1.5, _safe_float(raw_args.get("speed"), 1.05)))
    volume = max(0.2, min(1.0, _safe_float(raw_args.get("volume"), 0.95)))

    out_calls: List[ToolCallSchema] = []
    asset: Optional[SfxAsset] = None
    note: Optional[str] = None

    if sfx_dir is not None:
        try:
            asset = synthesize_tts_to_sfx_asset(
                sfx_dir,
                spoken_text,
                voice=voice,
                speed=speed,
                name="Intro Hook TTS" if is_opening else None,
            )
            play_dur = round(max(0.5, min(25.0, float(asset.duration))), 3)
            out_calls.append(
                ToolCallSchema(
                    id=f"tc_{uuid.uuid4().hex[:8]}",
                    name="add_sfx_clip",
                    arguments={
                        "start": tl_start,
                        "asset_id": asset.id,
                        "sfx_name": asset.name,
                        "name": asset.name,
                        "url": asset.url,
                        "duration": play_dur,
                        "volume": round(volume, 2),
                    },
                )
            )
            note = f"generated TTS voiceover '{asset.name}' ({play_dur:.1f}s)"
        except Exception as exc:
            note = f"TTS audio skipped ({exc})"
    else:
        note = "TTS audio skipped (SFX storage path unavailable)"

    has_matching_caption = any(
        tc.name == "add_caption"
        and (
            (is_opening and str((tc.arguments or {}).get("kind") or "") == "intro_hook")
            or str((tc.arguments or {}).get("text") or "").strip().lower() == spoken_text.lower()
        )
        for tc in existing_tool_calls
    )
    if also_add_caption and not has_matching_caption:
        if tl_start <= 0.05 and clip0 is not None:
            src_s = round(max(0.0, float(clip0.sourceStart)), 2)
            c0_end = max(src_s + 0.5, float(clip0.sourceEnd))
        elif tl_start > 0.05:
            src_s = _timeline_to_source_start(project_state, tl_start)
            c0_end = max(src_s + 0.5, max_src)
        else:
            src_s = 0.0
            c0_end = max(0.5, max_src)
        clip_speed = _active_clip_speed_at_tl(project_state, tl_start)
        cap_dur_tl = float(asset.duration) + 0.25 if asset is not None else max(2.2, min(4.5, len(spoken_text.split()) * 0.36))
        cap_span_src = max(1.8, min(6.5, cap_dur_tl)) * clip_speed
        src_e = round(max(src_s + 0.2, min(c0_end, max(src_s + 0.2, max_src), src_s + cap_span_src)), 2)
        out_calls.append(
            ToolCallSchema(
                id=f"tc_{uuid.uuid4().hex[:8]}",
                name="add_caption",
                arguments={
                    "start": src_s,
                    "end": src_e,
                    "text": spoken_text,
                    "kind": "intro_hook" if is_opening else "subtitle",
                },
            )
        )
    return out_calls, asset, note
