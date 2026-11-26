import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote, unquote

from schemas import SfxAsset
from services.youtube_ingest import _find_ffmpeg, _probe_media_duration

_SFX_META_LOCK = threading.RLock()

ALLOWED_SFX_EXTS = {".mp3", ".wav", ".m4a", ".ogg"}
MAX_SFX_BYTES = 25 * 1024 * 1024

_GENERIC_SFX_WORDS = {
    "a",
    "an",
    "the",
    "sound",
    "effect",
    "effects",
    "sfx",
    "audio",
    "mp3",
    "clip",
    "add",
    "put",
    "place",
    "here",
    "at",
    "this",
    "moment",
    "part",
    "right",
    "there",
    "applicable",
    "fitting",
    "fit",
    "matching",
    "match",
    "suitable",
    "appropriate",
    "video",
}

_SFX_SYNONYM_GROUPS: List[Set[str]] = [
    {
        "shock",
        "shocked",
        "surprise",
        "surprised",
        "eyebrow",
        "deadpan",
        "stare",
        "punchline",
        "impact",
        "boom",
        "vine",
        "meme",
        "wtf",
    },
    {
        "goofy",
        "clumsy",
        "silly",
        "fall",
        "trip",
        "slip",
        "cartoon",
        "bongo",
        "whistle",
        "chaotic",
        "panic",
        "run",
        "wacky",
    },
    {
        "roast",
        "roasted",
        "savage",
        "cooked",
        "doom",
        "brutal",
        "bell",
        "bong",
        "gong",
        "undertaker",
        "taco",
        "destroyed",
    },
    {
        "boring",
        "bored",
        "sleep",
        "sleepy",
        "tired",
        "yawn",
        "quiet",
        "silence",
        "silent",
        "dead",
        "snore",
        "auuuugh",
        "nap",
    },
    {
        "wholesome",
        "win",
        "victory",
        "clutch",
        "blessed",
        "magic",
        "sparkle",
        "glow",
        "angelic",
        "choir",
        "heaven",
        "heavenly",
        "glorious",
    },
    {
        "fail",
        "failed",
        "sad",
        "awkward",
        "cringe",
        "disappointed",
        "disappointment",
        "embarrassing",
        "loss",
        "mistake",
        "bruh",
        "womp",
        "sting",
        "brass",
        "realization",
        "oof",
    },
    {
        "transition",
        "cut",
        "zoom",
        "fast",
        "quick",
        "slap",
        "swipe",
        "whoosh",
        "whip",
        "snap",
        "swish",
    },
    {
        "smooth",
        "cool",
        "flex",
        "slick",
        "flirt",
        "charm",
        "comeback",
        "rizz",
        "synth",
        "ding",
        "reveal",
    },
]


def _infer_display_name(filename: str) -> str:
    stem = Path(filename).stem
    cleaned = re.sub(r"[_-]+", " ", stem).strip()
    if not cleaned:
        return "Custom SFX"
    if cleaned == cleaned.lower():
        return cleaned.title()
    return cleaned


def _infer_tags(name: str) -> List[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+", name.lower())
    seen: set[str] = set()
    tags: List[str] = []
    for tok in tokens:
        if len(tok) < 2 or tok in seen:
            continue
        seen.add(tok)
        tags.append(tok)
        if len(tags) >= 8:
            break
    return tags


def _normalize_tags(raw_tags: Optional[List[str]]) -> List[str]:
    if not raw_tags:
        return []
    seen: set[str] = set()
    cleaned: List[str] = []
    for item in raw_tags:
        val = str(item).strip().lower()
        if not val or val in seen:
            continue
        seen.add(val)
        cleaned.append(val)
    return cleaned


def _probe_sfx_duration(file_path: Path) -> float:
    try:
        ffmpeg_bin = _find_ffmpeg()
        probed = _probe_media_duration(file_path, ffmpeg_bin)
        if probed is not None and probed > 0:
            return round(max(0.15, min(60.0, float(probed))), 3)
    except Exception:
        pass
    return 1.5


def _load_meta(sfx_dir: Path) -> Dict[str, Dict[str, Any]]:
    meta_path = sfx_dir / "library.json"
    if not meta_path.is_file():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                str(k): dict(v)
                for k, v in data.items()
                if isinstance(v, dict)
            }
    except Exception:
        pass
    return {}


def _save_meta(sfx_dir: Path, meta: Dict[str, Dict[str, Any]]) -> None:
    sfx_dir.mkdir(parents=True, exist_ok=True)
    meta_path = sfx_dir / "library.json"
    tmp_path = sfx_dir / f".library_{uuid.uuid4().hex[:8]}.tmp"
    tmp_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(tmp_path, meta_path)


def _entry_to_asset(filename: str, entry: Dict[str, Any], base_url: str) -> SfxAsset:
    base = base_url.rstrip("/")
    return SfxAsset(
        id=str(entry.get("id") or f"sfx_{uuid.uuid4().hex[:8]}"),
        name=str(entry.get("name") or _infer_display_name(filename)),
        filename=filename,
        url=f"{base}/media/sfx/{quote(filename)}",
        duration=round(max(0.15, min(60.0, float(entry.get("duration") or 1.5))), 3),
        tags=_normalize_tags(entry.get("tags")),
        description=str(entry.get("description") or ""),
    )


def list_sfx_assets(sfx_dir: Path, base_url: str = "http://localhost:8000") -> List[SfxAsset]:
    with _SFX_META_LOCK:
        sfx_dir.mkdir(parents=True, exist_ok=True)
        meta = _load_meta(sfx_dir)
        changed = False

        existing_files = {
            p.name: p
            for p in sfx_dir.iterdir()
            if p.is_file() and p.suffix.lower() in ALLOWED_SFX_EXTS
        }

        # Prune deleted audio files from metadata
        for known_name in list(meta.keys()):
            if known_name not in existing_files:
                del meta[known_name]
                changed = True

        # Auto-register newly added audio files dropped into workspace_data/sfx/
        for filename, path in existing_files.items():
            if filename not in meta:
                inferred_name = _infer_display_name(filename)
                meta[filename] = {
                    "id": f"sfx_{uuid.uuid4().hex[:8]}",
                    "name": inferred_name,
                    "duration": _probe_sfx_duration(path),
                    "tags": _infer_tags(inferred_name),
                    "description": "",
                }
                changed = True
            else:
                entry = meta[filename]
                if not entry.get("id"):
                    entry["id"] = f"sfx_{uuid.uuid4().hex[:8]}"
                    changed = True
                if not entry.get("duration"):
                    entry["duration"] = _probe_sfx_duration(path)
                    changed = True

        if changed:
            _save_meta(sfx_dir, meta)

        assets = [
            _entry_to_asset(filename, meta[filename], base_url)
            for filename in existing_files
        ]
        assets.sort(key=lambda a: a.name.lower())
        return assets


def save_uploaded_sfx(
    sfx_dir: Path,
    original_filename: str,
    content: bytes,
    name: Optional[str] = None,
    tags: Optional[List[str]] = None,
    description: str = "",
    base_url: str = "http://localhost:8000",
) -> SfxAsset:
    if not content:
        raise ValueError("Uploaded audio file is empty.")
    if len(content) > MAX_SFX_BYTES:
        raise ValueError("Audio file exceeds the 25 MB upload limit.")

    orig_path = Path(original_filename or "sfx.mp3")
    ext = orig_path.suffix.lower()
    if ext not in ALLOWED_SFX_EXTS:
        raise ValueError(
            f"Unsupported audio format '{ext or 'unknown'}'. Allowed: {', '.join(sorted(ALLOWED_SFX_EXTS))}."
        )

    safe_stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", orig_path.stem).strip("._") or "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)
    candidate_name = f"{safe_stem}{ext}"
    target_path = sfx_dir / candidate_name
    if target_path.exists():
        candidate_name = f"{safe_stem}_{uuid.uuid4().hex[:4]}{ext}"
        target_path = sfx_dir / candidate_name

    target_path.write_bytes(content)

    display_name = (name or "").strip() or _infer_display_name(candidate_name)
    resolved_tags = _normalize_tags(tags) if tags is not None else _infer_tags(display_name)
    duration = _probe_sfx_duration(target_path)

    with _SFX_META_LOCK:
        meta = _load_meta(sfx_dir)
        entry = {
            "id": f"sfx_{uuid.uuid4().hex[:8]}",
            "name": display_name,
            "duration": duration,
            "tags": resolved_tags,
            "description": str(description or "").strip(),
        }
        meta[candidate_name] = entry
        _save_meta(sfx_dir, meta)

    return _entry_to_asset(candidate_name, entry, base_url)


def update_sfx_metadata(
    sfx_dir: Path,
    sfx_id: str,
    name: Optional[str] = None,
    tags: Optional[List[str]] = None,
    description: Optional[str] = None,
    base_url: str = "http://localhost:8000",
) -> Optional[SfxAsset]:
    with _SFX_META_LOCK:
        list_sfx_assets(sfx_dir, base_url=base_url)
        meta = _load_meta(sfx_dir)
        for filename, entry in meta.items():
            if entry.get("id") == sfx_id:
                if name is not None and name.strip():
                    entry["name"] = name.strip()
                if tags is not None:
                    entry["tags"] = _normalize_tags(tags)
                if description is not None:
                    entry["description"] = description.strip()
                _save_meta(sfx_dir, meta)
                return _entry_to_asset(filename, entry, base_url)
        return None


def delete_sfx_asset(sfx_dir: Path, sfx_id: str) -> bool:
    with _SFX_META_LOCK:
        list_sfx_assets(sfx_dir)
        meta = _load_meta(sfx_dir)
        target_filename: Optional[str] = None
        for filename, entry in meta.items():
            if entry.get("id") == sfx_id:
                target_filename = filename
                break
        if target_filename is None:
            return False

        audio_path = (sfx_dir / target_filename).resolve()
        root_resolved = sfx_dir.resolve()
        if audio_path.is_relative_to(root_resolved):
            audio_path.unlink(missing_ok=True)
        del meta[target_filename]
        _save_meta(sfx_dir, meta)
        return True


def resolve_sfx_file_path(
    sfx_dir: Path, asset_id: Optional[str], url: Optional[str]
) -> Optional[Path]:
    if not sfx_dir.exists():
        return None
    root_resolved = sfx_dir.resolve()

    if asset_id:
        meta = _load_meta(sfx_dir)
        for filename, entry in meta.items():
            if entry.get("id") == asset_id:
                candidate = (sfx_dir / filename).resolve()
                if (
                    candidate.is_relative_to(root_resolved)
                    and candidate.is_file()
                    and candidate.suffix.lower() in ALLOWED_SFX_EXTS
                ):
                    return candidate

    if url and "/media/sfx/" in url:
        raw_rel = unquote(url.split("/media/sfx/", 1)[-1]).split("?", 1)[0].strip("/")
        if raw_rel:
            candidate = (sfx_dir / Path(raw_rel).name).resolve()
            if (
                candidate.is_relative_to(root_resolved)
                and candidate.is_file()
                and candidate.suffix.lower() in ALLOWED_SFX_EXTS
            ):
                return candidate

    return None


def find_best_matching_sfx(
    assets: List[SfxAsset],
    query: str,
    exclude_ids: Optional[Set[str]] = None,
) -> Optional[SfxAsset]:
    if not assets:
        return None

    exclude_set = set(exclude_ids or set())
    non_excluded = [a for a in assets if a.id not in exclude_set]
    fallback_asset = non_excluded[0] if non_excluded else assets[0]

    clean_q = (query or "").strip().lower()
    if not clean_q:
        return fallback_asset

    raw_tokens = re.findall(r"[a-z0-9]+", clean_q)
    meaningful_tokens = [t for t in raw_tokens if len(t) >= 2 and t not in _GENERIC_SFX_WORDS]
    if not meaningful_tokens:
        return fallback_asset

    expanded_tokens: Set[str] = set()
    for tok in meaningful_tokens:
        for grp in _SFX_SYNONYM_GROUPS:
            if tok in grp:
                expanded_tokens.update(grp)
    expanded_tokens.difference_update(meaningful_tokens)

    scored: List[tuple[SfxAsset, float]] = []
    for asset in assets:
        name_low = asset.name.lower()
        desc_low = asset.description.lower()
        tags_low = [t.lower() for t in asset.tags]
        haystack_tokens = set(re.findall(r"[a-z0-9]+", f"{name_low} {desc_low} {' '.join(tags_low)}"))

        score = 0.0
        for tok in meaningful_tokens:
            if tok in name_low:
                score += 3.0
            if tok in tags_low:
                score += 4.0
            elif tok in haystack_tokens:
                score += 2.0
            elif any(tok in t or t in tok for t in tags_low):
                score += 1.5
            elif tok in desc_low:
                score += 1.0

        for etok in expanded_tokens:
            if etok in tags_low or etok in name_low:
                score += 2.2
            elif etok in haystack_tokens:
                score += 1.2

        scored.append((asset, score))

    if exclude_set and non_excluded:
        pos_non_excluded = [(a, s) for a, s in scored if a.id not in exclude_set and s > 0]
        if pos_non_excluded:
            return max(pos_non_excluded, key=lambda item: item[1])[0]

    best_asset, best_score = max(scored, key=lambda item: item[1])
    if best_score > 0:
        if best_asset.id in exclude_set and non_excluded:
            adjusted = [
                (a, s - (1.5 if a.id in exclude_set else 0.0)) for a, s in scored
            ]
            adj_best, adj_score = max(adjusted, key=lambda item: item[1])
            if adj_score > 0:
                return adj_best
        return best_asset
    return fallback_asset
