import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from groq import Groq

from schemas import CaptionItem, WordTimestamp
from services.model_registry import get_stt_models_chain

MAX_GROQ_AUDIO_BYTES = 18 * 1024 * 1024  # Safe threshold below Groq's 25 MB limit
CHUNK_SECONDS = 600.0  # 10-minute 24kbps mono MP3 chunks (~1.8 MB each)


def _is_payload_too_large_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "413" in msg or "request_too_large" in msg or "too large" in msg


def _raw_dict_to_words_segments(
    raw_dict: Dict[str, Any], offset_sec: float = 0.0
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    words: List[Dict[str, Any]] = []
    for w in raw_dict.get("words") or []:
        word_text = str(w.get("word", "")).strip()
        if not word_text:
            continue
        words.append(
            {
                "word": word_text,
                "start": round(float(w.get("start", 0.0)) + offset_sec, 3),
                "end": round(float(w.get("end", 0.0)) + offset_sec, 3),
            }
        )

    segments: List[Dict[str, Any]] = []
    for seg in raw_dict.get("segments") or []:
        seg_text = str(seg.get("text", "")).strip()
        if not seg_text:
            continue
        segments.append(
            {
                "start": round(float(seg.get("start", 0.0)) + offset_sec, 3),
                "end": round(float(seg.get("end", 0.0)) + offset_sec, 3),
                "text": seg_text,
            }
        )

    return str(raw_dict.get("text", "")).strip(), words, segments


def _transcribe_single_bytes_with_fallback(
    client: Groq,
    filename: str,
    audio_bytes: bytes,
    stt_models: List[str],
) -> Tuple[Dict[str, Any], str]:
    transcription = None
    model_used = stt_models[0] if stt_models else "whisper-large-v3-turbo"
    last_exc: Optional[Exception] = None

    for model_id in stt_models:
        try:
            transcription = client.audio.transcriptions.create(
                file=(filename, audio_bytes),
                model=model_id,
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
            )
            model_used = model_id
            break
        except Exception as exc:
            last_exc = exc

    if transcription is None:
        assert last_exc is not None
        raise last_exc

    raw_dict = (
        transcription.model_dump()
        if hasattr(transcription, "model_dump")
        else dict(transcription)
    )
    return raw_dict, model_used


def _transcribe_audio_chunked(
    client: Groq, audio_path: Path, stt_models: List[str]
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """
    Splits/re-encodes large audio via FFmpeg into up to 36 compact 10-minute 24kbps MP3
    chunks (~1.8 MB each, up to 6 hours) and merges their offset word & segment timestamps.
    """
    from services.youtube_ingest import _find_ffmpeg

    ffmpeg_bin = _find_ffmpeg()
    all_text_parts: List[str] = []
    all_words: List[Dict[str, Any]] = []
    all_segments: List[Dict[str, Any]] = []
    model_used = stt_models[0] if stt_models else "whisper-large-v3-turbo"

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        chunk_idx = 0
        while chunk_idx < 36:
            offset_sec = chunk_idx * CHUNK_SECONDS
            chunk_path = tmp_root / f"_chunk_{chunk_idx}.mp3"
            proc = subprocess.run(
                [
                    ffmpeg_bin,
                    "-nostdin",
                    "-y",
                    "-ss",
                    str(offset_sec),
                    "-t",
                    str(CHUNK_SECONDS),
                    "-i",
                    audio_path.resolve().as_posix(),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-b:a",
                    "24k",
                    chunk_path.resolve().as_posix(),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            if proc.returncode != 0 or not chunk_path.exists() or chunk_path.stat().st_size < 1500:
                break

            chunk_bytes = chunk_path.read_bytes()
            raw_dict, model_used = _transcribe_single_bytes_with_fallback(
                client, chunk_path.name, chunk_bytes, stt_models
            )
            txt, words, segments = _raw_dict_to_words_segments(raw_dict, offset_sec=offset_sec)
            if txt:
                all_text_parts.append(txt)
            all_words.extend(words)
            all_segments.extend(segments)
            chunk_idx += 1

    if not all_words and not all_segments:
        raise RuntimeError("Chunked Groq Whisper transcription produced no transcript output.")

    return " ".join(all_text_parts).strip(), all_words, all_segments, model_used


def _demo_transcript(duration: float = 45.0) -> Dict[str, Any]:
    """Provides a realistic word-level transcript when running in demo://sample mode or without a Groq key."""
    sample_sentences = [
        (1.0, 4.8, "Wait till you see what happens right after this setup."),
        (5.6, 10.2, "Everyone thought the microphone was muted during the entire call."),
        (11.4, 15.8, "Then someone accidentally shared the wrong screen in front of everyone."),
        (16.2, 22.0, "Look at his face the exact second he realizes the camera is still live!"),
        (22.1, 27.6, "That single reaction turned into the funniest moment of the whole stream."),
        (28.5, 32.0, "I could not stop laughing for ten minutes straight."),
        (33.2, 41.5, "Subscribe and drop a comment if you have ever done this on a meeting."),
    ]
    words: List[Dict[str, Any]] = []
    segments: List[Dict[str, Any]] = []
    full_text_parts: List[str] = []

    for seg_start, seg_end, sentence in sample_sentences:
        if seg_start >= duration:
            break
        actual_end = min(seg_end, duration)
        tokens = sentence.split()
        span = max(0.2, actual_end - seg_start)
        per_word = span / max(1, len(tokens))
        for idx, tok in enumerate(tokens):
            w_start = round(seg_start + idx * per_word, 2)
            w_end = round(min(actual_end, w_start + per_word * 0.92), 2)
            words.append({"word": tok, "start": w_start, "end": w_end})
        segments.append({"start": seg_start, "end": actual_end, "text": sentence})
        full_text_parts.append(sentence)

    return {
        "text": " ".join(full_text_parts),
        "words": words,
        "segments": segments,
        "source": "demo_fallback",
    }


def transcribe_audio_groq(
    project_id: str,
    audio_path: Optional[Path],
    transcripts_dir: Path,
    duration: float = 45.0,
    is_demo: bool = False,
) -> Dict[str, Any]:
    """
    Transcribes the lightweight 16kHz mono MP3 via Groq Whisper (`whisper-large-v3-turbo`
    -> `whisper-large-v3` backup) with automatic 413 payload chunking recovery,
    caching the JSON on disk.
    """
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    cache_path = transcripts_dir / f"{project_id}.json"

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    has_audio = audio_path is not None and Path(audio_path).exists()
    has_groq_key = bool(api_key and api_key != "your_groq_api_key_here")

    if cache_path.exists():
        with cache_path.open("r", encoding="utf-8") as f:
            cached = json.load(f)
        if is_demo or cached.get("source") != "demo_fallback" or not has_groq_key or not has_audio:
            return cached

    if is_demo or not has_audio or not has_groq_key:
        transcript = _demo_transcript(duration)
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump(transcript, f, indent=2)
        return transcript

    assert audio_path is not None
    client = Groq(api_key=api_key)
    stt_models = get_stt_models_chain()

    try:
        file_size = audio_path.stat().st_size
        if file_size > MAX_GROQ_AUDIO_BYTES:
            full_text, words, segments, model_used = _transcribe_audio_chunked(
                client, audio_path, stt_models
            )
        else:
            audio_bytes = audio_path.read_bytes()
            try:
                raw_dict, model_used = _transcribe_single_bytes_with_fallback(
                    client, audio_path.name, audio_bytes, stt_models
                )
                full_text, words, segments = _raw_dict_to_words_segments(raw_dict, offset_sec=0.0)
            except Exception as exc:
                if _is_payload_too_large_error(exc):
                    full_text, words, segments, model_used = _transcribe_audio_chunked(
                        client, audio_path, stt_models
                    )
                else:
                    raise
    except Exception:
        transcript = _demo_transcript(duration)
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump(transcript, f, indent=2)
        return transcript

    transcript = {
        "text": full_text,
        "words": words,
        "segments": segments,
        "source": "groq_whisper",
        "model_used": model_used,
    }

    with cache_path.open("w", encoding="utf-8") as f:
        json.dump(transcript, f, indent=2)

    return transcript


def transcribe_audio_slices_groq(
    project_id: str,
    audio_slices: List[Dict[str, Any]],
    transcripts_dir: Path,
    duration: float = 45.0,
) -> Dict[str, Any]:
    """
    Transcribes targeted audio slices extracted from long (30m–24h) captionless VODs,
    preserving each slice's exact YouTube VOD timestamp offsets.
    """
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    cache_path = transcripts_dir / f"{project_id}.json"

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    has_groq_key = bool(api_key and api_key != "your_groq_api_key_here")
    if not audio_slices or not has_groq_key:
        transcript = _demo_transcript(min(max(10.0, float(duration or 45.0)), 60.0))
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump(transcript, f, separators=(",", ":"))
        return transcript

    client = Groq(api_key=api_key)
    stt_models = get_stt_models_chain()
    model_used = stt_models[0] if stt_models else "whisper-large-v3-turbo"
    all_text_parts: List[str] = []
    all_words: List[Dict[str, Any]] = []
    all_segments: List[Dict[str, Any]] = []

    for sl in audio_slices:
        raw_path = sl.get("path")
        if not raw_path:
            continue
        slice_path = Path(raw_path)
        if not slice_path.exists() or slice_path.stat().st_size < 500:
            continue
        base_offset = float(sl.get("start_offset", 0.0) or 0.0)
        try:
            if slice_path.stat().st_size > MAX_GROQ_AUDIO_BYTES:
                txt, words, segments, model_used = _transcribe_audio_chunked(
                    client, slice_path, stt_models
                )
                for w in words:
                    w["start"] = round(float(w["start"]) + base_offset, 3)
                    w["end"] = round(float(w["end"]) + base_offset, 3)
                for seg in segments:
                    seg["start"] = round(float(seg["start"]) + base_offset, 3)
                    seg["end"] = round(float(seg["end"]) + base_offset, 3)
            else:
                audio_bytes = slice_path.read_bytes()
                try:
                    raw_dict, model_used = _transcribe_single_bytes_with_fallback(
                        client, slice_path.name, audio_bytes, stt_models
                    )
                    txt, words, segments = _raw_dict_to_words_segments(
                        raw_dict, offset_sec=base_offset
                    )
                except Exception as exc:
                    if _is_payload_too_large_error(exc):
                        txt, words, segments, model_used = _transcribe_audio_chunked(
                            client, slice_path, stt_models
                        )
                        for w in words:
                            w["start"] = round(float(w["start"]) + base_offset, 3)
                            w["end"] = round(float(w["end"]) + base_offset, 3)
                        for seg in segments:
                            seg["start"] = round(float(seg["start"]) + base_offset, 3)
                            seg["end"] = round(float(seg["end"]) + base_offset, 3)
                    else:
                        raise
            if txt:
                all_text_parts.append(txt)
            all_words.extend(words)
            all_segments.extend(segments)
        except Exception:
            continue

    if not all_words and not all_segments:
        transcript = _demo_transcript(min(max(10.0, float(duration or 45.0)), 60.0))
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump(transcript, f, separators=(",", ":"))
        return transcript

    all_words.sort(key=lambda w: float(w.get("start", 0.0)))
    all_segments.sort(key=lambda s: float(s.get("start", 0.0)))
    transcript = {
        "text": " ".join(all_text_parts).strip(),
        "words": all_words,
        "segments": all_segments,
        "source": "groq_whisper_slices",
        "model_used": model_used,
    }
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump(transcript, f, separators=(",", ":"))
    return transcript


def load_cached_transcript(project_id: str, transcripts_dir: Path) -> Optional[Dict[str, Any]]:
    base_dir = transcripts_dir.resolve()
    cache_path = (base_dir / f"{project_id}.json").resolve()
    if not cache_path.is_relative_to(base_dir) or not cache_path.is_file():
        return None
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def detect_pause_gaps(transcript: Dict[str, Any], min_gap: float = 0.7) -> List[Dict[str, float]]:
    """
    Finds silent gaps >= min_gap seconds between consecutive Groq Whisper word timestamps.
    Leaves a small 0.06s natural breath buffer on both sides so cuts sound crisp, not clipped.
    """
    min_gap = max(0.2, float(min_gap or 0.7))
    words = transcript.get("words") or []
    if len(words) < 2:
        return []

    gaps: List[Dict[str, float]] = []
    breath_pad = 0.06

    # Check leading silence before first word
    first_start = float(words[0]["start"])
    if first_start >= min_gap + breath_pad and first_start - breath_pad >= 0.25:
        gaps.append({"start": 0.0, "end": round(first_start - breath_pad, 2)})

    for i in range(len(words) - 1):
        prev_end = float(words[i]["end"])
        next_start = float(words[i + 1]["start"])
        delta = next_start - prev_end
        if delta >= min_gap:
            cut_start = round(prev_end + breath_pad, 2)
            cut_end = round(next_start - breath_pad, 2)
            if cut_end - cut_start >= 0.25:
                gaps.append({"start": cut_start, "end": cut_end})

    return gaps


def build_captions_from_transcript(
    transcript: Dict[str, Any],
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    max_words_per_caption: int = 5,
) -> List[CaptionItem]:
    """
    Converts Groq Whisper word timestamps into readable 4-6 word caption blocks
    with exact per-word timing for real-time Karaoke highlighting.
    """
    words = transcript.get("words") or []
    filtered: List[Dict[str, Any]] = []
    for w in words:
        ws = float(w["start"])
        we = float(w["end"])
        if start_time is not None and we < start_time:
            continue
        if end_time is not None and ws > end_time:
            continue
        filtered.append(w)

    if not filtered:
        return []

    captions: List[CaptionItem] = []
    chunk: List[Dict[str, Any]] = []

    for idx, w in enumerate(filtered):
        chunk.append(w)
        text_token = str(w["word"]).strip()
        ends_sentence = text_token.endswith((".", "!", "?"))
        has_pause_after = False
        if idx + 1 < len(filtered):
            next_start = float(filtered[idx + 1]["start"])
            if next_start - float(w["end"]) > 0.45:
                has_pause_after = True

        if len(chunk) >= max_words_per_caption or ends_sentence or has_pause_after:
            captions.append(_chunk_to_caption(chunk))
            chunk = []

    if chunk:
        captions.append(_chunk_to_caption(chunk))

    return captions


def _chunk_to_caption(chunk: List[Dict[str, Any]]) -> CaptionItem:
    word_items = [
        WordTimestamp(
            word=str(item["word"]).strip(),
            start=round(float(item["start"]), 3),
            end=round(float(item["end"]), 3),
        )
        for item in chunk
    ]
    return CaptionItem(
        id=f"cap_{uuid.uuid4().hex[:8]}",
        start=word_items[0].start,
        end=round(max(word_items[0].start + 0.15, max(w.end for w in word_items)), 3),
        text=" ".join(w.word for w in word_items),
        isManuallyEdited=False,
        highlightedWords=[],
        words=word_items,
    )


def transcribe_voice_blob(file_bytes: bytes, filename: str) -> str:
    """Transcribes microphone voice commands recorded in the Right Panel using Groq Whisper."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key or api_key == "your_groq_api_key_here":
        raise RuntimeError("GROQ_API_KEY is required for voice command transcription.")

    client = Groq(api_key=api_key)
    stt_models = get_stt_models_chain()
    transcription: Any = None
    last_exc: Optional[Exception] = None

    for model_id in stt_models:
        try:
            transcription = client.audio.transcriptions.create(
                file=(filename or "voice_command.webm", file_bytes),
                model=model_id,
                response_format="text",
            )
            break
        except Exception as exc:
            last_exc = exc

    if transcription is None:
        assert last_exc is not None
        raise last_exc

    if isinstance(transcription, str):
        return transcription.strip()
    return str(getattr(transcription, "text", transcription)).strip()
