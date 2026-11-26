import hashlib
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from google import genai
from groq import Groq

from services.nvidia_client import fetch_nvidia_model_ids, is_nvidia_configured

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

REFRESH_INTERVAL_SECONDS = 86400  # 24 hours

DEFAULT_TOOL_MODELS: List[str] = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",
]

DEFAULT_ROUTER_MODELS: List[str] = [
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
]

DEFAULT_STT_MODELS: List[str] = [
    "whisper-large-v3-turbo",
    "whisper-large-v3",
]

DEFAULT_GEMINI_MODELS: List[str] = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
]

DEFAULT_NVIDIA_VISION_MODELS: List[str] = [
    "meta/llama-3.2-11b-vision-instruct",
    "meta/llama-3.2-90b-vision-instruct",
    "microsoft/phi-4-multimodal-instruct",
]

DEFAULT_NVIDIA_FAST_MODELS: List[str] = [
    "meta/llama-3.3-70b-instruct",
    "meta/llama-3.1-70b-instruct",
    "qwen/qwen2.5-72b-instruct",
    "meta/llama-3.1-8b-instruct",
]

EXCLUDED_CHAT_TOKENS = (
    "whisper",
    "orpheus",
    "tts",
    "safety",
    "safeguard",
    "guard",
    "embed",
    "rerank",
    "moderation",
    "audio",
    "playai",
)

EXCLUDED_GEMINI_TOKENS = (
    "embedding",
    "embed",
    "imagen",
    "image",
    "veo",
    "tts",
    "audio",
    "robotics",
    "computer-use",
    "aqa",
    "argon",
    "carbon",
    "neon",
    "hydrogen",
    "nano-banana",
    "banana",
)

EXCLUDED_NVIDIA_TOKENS = (
    "embed",
    "rerank",
    "reward",
    "guard",
    "shield",
    "safety",
    "nv-embed",
    "retriever",
    "parakeet",
    "canary",
    "fastpitch",
    "tts",
    "asr",
    "whisper",
    "flux",
    "sdxl",
    "stable-diffusion",
    "edify",
    "trellis",
    "bionemo",
    "esmfold",
    "alphafold",
    "diffdock",
    "fourcastnet",
    "corrdiff",
    "usdcode",
    "parse",
    "ocr",
    "translate",
    "riva-",
    "-riva",
    "gliner",
    "-pii",
    "detector",
    "nvclip",
    "ising",
    "calibration",
    "starcoder",
)

NVIDIA_VISION_TOKENS = (
    "vision",
    "-vl",
    "vl-",
    "multimodal",
    "maverick",
    "scout",
    "paligemma",
    "pixtral",
    "internvl",
    "vila",
    "neva",
    "cosmos",
    "omni",
    "gemma-4",
)

NVIDIA_SLOW_TOKENS = (
    "ultra",
    "405b",
    "340b",
    "253b",
    "235b",
    "671b",
    "550b",
    "-1t-",
    "-1t",
    "-r1",
    "thinking",
    "reasoning",
)


def _is_key_configured(env_name: str, placeholder: str) -> bool:
    val = os.getenv(env_name, "").strip()
    return bool(val and val != placeholder)


def _is_excluded_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return any(tok in lowered for tok in EXCLUDED_CHAT_TOKENS)


def _append_unique(target: List[str], item: str) -> None:
    if item and item not in target:
        target.append(item)


def resolve_tool_models(live_groq_ids: List[str]) -> List[str]:
    """
    Orders Groq Function Calling / Tool Use models with Primary -> Backup chain:
    1. GPT OSS 120B
    2. Qwen 3.8 27B
    3. GPT OSS 20B (excluding Safety GPT OSS 20B)
    4. Other live general chat/tool models (e.g. llama-3.3-70b-versatile)
    """
    if not live_groq_ids:
        return list(DEFAULT_TOOL_MODELS)

    candidates = [m for m in live_groq_ids if not _is_excluded_chat_model(m)]
    if not candidates:
        return list(DEFAULT_TOOL_MODELS)

    ordered: List[str] = []

    # 1. GPT OSS 120B
    for m in candidates:
        if "gpt-oss-120b" in m.lower():
            _append_unique(ordered, m)

    # 2. Qwen 3.8 27B
    for m in candidates:
        low = m.lower()
        if "qwen" in low and ("27b" in low or "3.8" in low or "3_8" in low):
            _append_unique(ordered, m)

    # 3. GPT OSS 20B (non-safety)
    for m in candidates:
        if "gpt-oss-20b" in m.lower():
            _append_unique(ordered, m)

    # 4. Llama 3.3 70B & other live chat/tool backups
    for m in candidates:
        if "llama-3.3-70b" in m.lower():
            _append_unique(ordered, m)
    for m in candidates:
        if "llama" in m.lower() or "qwen" in m.lower():
            _append_unique(ordered, m)
    for m in candidates:
        _append_unique(ordered, m)

    return ordered or list(DEFAULT_TOOL_MODELS)


def resolve_router_models(live_groq_ids: List[str]) -> List[str]:
    """
    Orders Groq Intent Router models with Primary -> Backup chain:
    1. GPT OSS 20B (fast lightweight router, non-safety)
    2. Qwen 3.8 27B
    3. GPT OSS 120B
    4. Llama 3.3 70B & other live general chat models
    """
    if not live_groq_ids:
        return list(DEFAULT_ROUTER_MODELS)

    candidates = [m for m in live_groq_ids if not _is_excluded_chat_model(m)]
    if not candidates:
        return list(DEFAULT_ROUTER_MODELS)

    ordered: List[str] = []

    # 1. GPT OSS 20B (non-safety)
    for m in candidates:
        if "gpt-oss-20b" in m.lower():
            _append_unique(ordered, m)

    # 2. Qwen 3.8 27B
    for m in candidates:
        low = m.lower()
        if "qwen" in low and ("27b" in low or "3.8" in low or "3_8" in low):
            _append_unique(ordered, m)

    # 3. GPT OSS 120B
    for m in candidates:
        if "gpt-oss-120b" in m.lower():
            _append_unique(ordered, m)

    # 4. Llama 3.3 70B & other live chat backups
    for m in candidates:
        if "llama-3.3-70b" in m.lower():
            _append_unique(ordered, m)
    for m in candidates:
        if "llama" in m.lower() or "qwen" in m.lower():
            _append_unique(ordered, m)
    for m in candidates:
        _append_unique(ordered, m)

    return ordered or list(DEFAULT_ROUTER_MODELS)


def resolve_stt_models(live_groq_ids: List[str]) -> List[str]:
    """
    Orders Groq Speech-to-Text models:
    1. Whisper Large v3 Turbo (Primary)
    2. Whisper Large v3 (Backup)
    """
    if not live_groq_ids:
        return list(DEFAULT_STT_MODELS)

    whisper_models = [m for m in live_groq_ids if "whisper" in m.lower()]
    if not whisper_models:
        return list(DEFAULT_STT_MODELS)

    ordered: List[str] = []
    for m in whisper_models:
        if "whisper-large-v3-turbo" in m.lower() or "turbo" in m.lower():
            _append_unique(ordered, m)
    for m in whisper_models:
        low = m.lower()
        if "v3" in low and "turbo" not in low:
            _append_unique(ordered, m)
    for m in whisper_models:
        _append_unique(ordered, m)

    return ordered or list(DEFAULT_STT_MODELS)


def _extract_gemini_version(model_id: str) -> tuple[int, int]:
    low = model_id.lower()
    if low in {"gemini-flash-latest", "gemini-pro-latest"}:
        return (2, 4)
    m = re.search(r"gemini-(\d+)(?:\.(\d+))?", low)
    if m:
        return (int(m.group(1)), int(m.group(2) or 0))
    return (0, 0)


def resolve_gemini_models(live_gemini_ids: List[str]) -> List[str]:
    """
    Orders Gemini multimodal models dynamically so newer releases (e.g. Gemini 3/4+)
    are automatically adopted while prioritizing fast flash -> pro -> remaining backups:
    1. Highest stable non-lite Flash (e.g. gemini-3-flash, gemini-2.5-flash)
    2. Highest stable Pro (e.g. gemini-3-pro, gemini-2.5-pro)
    3. Older / preview / lite / latest aliases as fallback
    """
    if not live_gemini_ids:
        return list(DEFAULT_GEMINI_MODELS)

    candidates = [
        m
        for m in live_gemini_ids
        if "gemini" in m.lower()
        and not any(tok in m.lower() for tok in EXCLUDED_GEMINI_TOKENS)
    ]
    if not candidates:
        return list(DEFAULT_GEMINI_MODELS)

    def _rank_key(model_id: str) -> tuple[int, int, int, int, int, int, int, str]:
        low = model_id.lower()
        major, minor = _extract_gemini_version(low)
        # Never let unknown codenames lacking 'flash' or 'pro' outrank standard Flash/Pro models
        is_known_tier = 0 if ("flash" in low or "pro" in low) else 1
        is_preview_or_exp = 1 if ("preview" in low or "exp" in low) else 0
        is_lite = 1 if "lite" in low else 0
        is_flash = 0 if "flash" in low else (1 if "pro" in low else 2)
        is_exact = 0 if re.fullmatch(r"gemini-\d+(?:\.\d+)?-(?:flash|pro)", low) else 1
        return (is_known_tier, -major, -minor, is_preview_or_exp, is_lite, is_flash, is_exact, low)

    ordered: List[str] = []
    for m in sorted(candidates, key=_rank_key):
        _append_unique(ordered, m)

    return ordered or list(DEFAULT_GEMINI_MODELS)


def _extract_param_b(model_id: str) -> float:
    low = model_id.lower()
    # Prefer MoE active parameter count when advertised (e.g. 120b-a12b -> 12B, 30b-a3b -> 3B)
    m_active = re.search(r"-[ae](\d+(?:\.\d+)?)b\b", low)
    if m_active:
        return float(m_active.group(1))
    m_trillion = re.search(r"(\d+(?:\.\d+)?)t\b", low)
    if m_trillion:
        return float(m_trillion.group(1)) * 1000.0
    m = re.search(r"(\d+(?:\.\d+)?)b\b", low)
    return float(m.group(1)) if m else 32.0


def resolve_nvidia_models(live_nvidia_ids: List[str]) -> Tuple[List[str], List[str]]:
    """
    Auto-classifies live NVIDIA NIM models into:
    1. fast_text_chain: Fast instruct/JSON models first, deprioritizing slow/reasoning/1T models.
    2. vision_chain: General multimodal VLMs first (ranked by MoE active size), robotics/cosmos later.
    """
    if not live_nvidia_ids:
        return list(DEFAULT_NVIDIA_FAST_MODELS), list(DEFAULT_NVIDIA_VISION_MODELS)

    candidates = [
        m
        for m in live_nvidia_ids
        if not any(tok in m.lower() for tok in EXCLUDED_NVIDIA_TOKENS)
    ]
    if not candidates:
        return list(DEFAULT_NVIDIA_FAST_MODELS), list(DEFAULT_NVIDIA_VISION_MODELS)

    vision_candidates = [
        m for m in candidates if any(tok in m.lower() for tok in NVIDIA_VISION_TOKENS)
    ]

    def _vision_rank(model_id: str) -> tuple[int, int, int, int, str]:
        low = model_id.lower()
        # Cosmos models are robotics/physical-AI specialized, so keep general VLMs ahead of them
        is_slow = 1 if any(tok in low for tok in NVIDIA_SLOW_TOKENS) else 0
        param_b = _extract_param_b(low)
        if any(
            k in low
            for k in (
                "super-vl",
                "-vl",
                "vl-",
                "llama-3.2",
                "llama-4-maverick",
                "llama-4-scout",
                "phi-4-multimodal",
                "pixtral",
                "internvl",
            )
        ):
            vlm_tier = 0
        elif "cosmos" in low:
            vlm_tier = 2
        else:
            vlm_tier = 1
        size_tier = 0 if param_b <= 32.0 else 1
        return (is_slow, vlm_tier, size_tier, 0 if "instruct" in low else 1, low)

    def _text_rank(model_id: str) -> tuple[int, int, int, float, str]:
        low = model_id.lower()
        is_slow = 1 if any(tok in low for tok in NVIDIA_SLOW_TOKENS) else 0
        is_legacy_gemma3 = "gemma-3-" in low
        if not is_legacy_gemma3 and any(k in low for k in ("flash", "lightning", "bolt")):
            speed_tier = 0
        elif not is_legacy_gemma3 and any(
            k in low
            for k in (
                "llama-3.3-70b",
                "llama-3.1-70b",
                "qwen2.5-72b",
                "gpt-oss",
                "kimi",
                "llama",
                "qwen",
                "mistral",
                "minimax",
                "glm",
                "step",
            )
        ):
            speed_tier = 1
        elif is_legacy_gemma3:
            speed_tier = 3
        else:
            speed_tier = 2
        is_instruct = (
            0
            if any(k in low for k in ("instruct", "-it", "flash", "lightning", "bolt"))
            else 1
        )
        param_b = _extract_param_b(low)
        ideal_dist = abs(param_b - 16.0)
        return (is_slow, speed_tier, is_instruct, ideal_dist, low)

    vision_chain: List[str] = []
    for m in sorted(vision_candidates, key=_vision_rank):
        _append_unique(vision_chain, m)

    fast_chain: List[str] = []
    # Put pure/fast text instruct models first, keeping vision models out of text front if plenty exist
    text_primary = [m for m in candidates if m not in vision_candidates] or candidates
    for m in sorted(text_primary, key=_text_rank):
        _append_unique(fast_chain, m)

    return (fast_chain or list(DEFAULT_NVIDIA_FAST_MODELS)), vision_chain


class ModelRegistry:
    """Thread-safe live model discovery cache with 24-hour auto-refresh & manual force-refresh."""

    def __init__(self, refresh_interval_seconds: int = REFRESH_INTERVAL_SECONDS) -> None:
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self.refresh_interval_seconds = refresh_interval_seconds
        self.last_refreshed_at: float = 0.0
        self.last_refreshed_iso: Optional[str] = None
        self.available_groq_models: List[str] = []
        self.available_gemini_models: List[str] = []
        self.tool_models_chain: List[str] = list(DEFAULT_TOOL_MODELS)
        self.router_models_chain: List[str] = list(DEFAULT_ROUTER_MODELS)
        self.stt_models_chain: List[str] = list(DEFAULT_STT_MODELS)
        self.gemini_models_chain: List[str] = list(DEFAULT_GEMINI_MODELS)
        self.nvidia_fast_models_chain: List[str] = list(DEFAULT_NVIDIA_FAST_MODELS)
        self.nvidia_vision_models_chain: List[str] = list(DEFAULT_NVIDIA_VISION_MODELS)
        self.available_nvidia_models: List[str] = []
        self.vision_models_chain: List[str] = list(DEFAULT_GEMINI_MODELS) + list(
            DEFAULT_NVIDIA_VISION_MODELS
        ) + [
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-120b",
        ]
        self.groq_configured: bool = False
        self.gemini_configured: bool = False
        self.nvidia_configured: bool = False
        self.groq_refresh_error: Optional[str] = None
        self.gemini_refresh_error: Optional[str] = None
        self.nvidia_refresh_error: Optional[str] = None
        self.model_latencies_ms: Dict[str, float] = {}
        self.cooldown_until: Dict[str, float] = {}
        self.dead_models_until: Dict[str, float] = {}
        self._api_key_fingerprints: Dict[str, str] = {}
        self.last_race_winner: Optional[Dict[str, Any]] = None

    def _fetch_groq_models(self) -> tuple[List[str], Optional[str]]:
        if not _is_key_configured("GROQ_API_KEY", "your_groq_api_key_here"):
            return [], None
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        try:
            client = Groq(api_key=api_key, timeout=8.0)
            resp = client.models.list()
            data = getattr(resp, "data", resp) or []
            ids = sorted(
                {
                    str(getattr(item, "id", "") or "").strip()
                    for item in data
                    if getattr(item, "id", None)
                }
            )
            return ids, None
        except Exception as exc:
            return [], str(exc)

    def _fetch_gemini_models(self) -> tuple[List[str], Optional[str]]:
        if not _is_key_configured("GEMINI_API_KEY", "your_gemini_api_key_here"):
            return [], None
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        try:
            client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=8000))
            models = list(client.models.list())
            ids: List[str] = []
            for m in models:
                raw_name = str(getattr(m, "name", "") or "").strip()
                if not raw_name:
                    continue
                actions = getattr(m, "supported_actions", None)
                if actions and "generateContent" not in actions:
                    continue
                short_name = raw_name.removeprefix("models/")
                _append_unique(ids, short_name)
            return sorted(ids), None
        except Exception as exc:
            return [], str(exc)

    def _fetch_nvidia_models(self) -> tuple[List[str], Optional[str]]:
        if not is_nvidia_configured():
            return [], None
        return fetch_nvidia_model_ids(timeout=8.0)

    def refresh_now(
        self, reload_env: bool = True, clear_cooldowns: bool = False, force_refresh: bool = True
    ) -> Dict[str, Any]:
        with self._refresh_lock:
            with self._lock:
                if not force_refresh and not self._needs_refresh_locked():
                    return self._snapshot_locked()
                if reload_env and ENV_PATH.exists():
                    load_dotenv(ENV_PATH, override=True)

                for prov, env_name in (
                    ("groq", "GROQ_API_KEY"),
                    ("gemini", "GEMINI_API_KEY"),
                    ("nvidia", "NVIDIA_API_KEY"),
                ):
                    raw_k = os.getenv(env_name, "").strip()
                    fp = hashlib.sha256(raw_k.encode("utf-8")).hexdigest()[:12] if raw_k else ""
                    if fp != self._api_key_fingerprints.get(prov):
                        self._api_key_fingerprints[prov] = fp
                        for cache in (self.dead_models_until, self.cooldown_until):
                            for k in [k for k in cache if k.startswith(f"{prov}:")]:
                                cache.pop(k, None)

                if clear_cooldowns:
                    self.cooldown_until.clear()
                self.groq_configured = _is_key_configured(
                    "GROQ_API_KEY", "your_groq_api_key_here"
                )
                self.gemini_configured = _is_key_configured(
                    "GEMINI_API_KEY", "your_gemini_api_key_here"
                )
                self.nvidia_configured = is_nvidia_configured()

            # Network discovery runs OUTSIDE self._lock so inference threads never block
            live_groq, groq_err = self._fetch_groq_models()
            live_gemini, gemini_err = self._fetch_gemini_models()
            live_nvidia, nvidia_err = self._fetch_nvidia_models()

            with self._lock:
                self.available_groq_models = live_groq
                self.available_gemini_models = live_gemini
                self.available_nvidia_models = live_nvidia
                self.groq_refresh_error = groq_err
                self.gemini_refresh_error = gemini_err
                self.nvidia_refresh_error = nvidia_err

                self.tool_models_chain = resolve_tool_models(live_groq)
                self.router_models_chain = resolve_router_models(live_groq)
                self.stt_models_chain = resolve_stt_models(live_groq)
                self.gemini_models_chain = resolve_gemini_models(live_gemini)
                nv_fast, nv_vision = resolve_nvidia_models(live_nvidia)
                self.nvidia_fast_models_chain = nv_fast
                self.nvidia_vision_models_chain = nv_vision

                vision_chain: List[str] = []
                for m in self.gemini_models_chain:
                    _append_unique(vision_chain, m)
                for m in self.nvidia_vision_models_chain:
                    _append_unique(vision_chain, m)
                for m in self.tool_models_chain:
                    if "qwen" in m.lower() or "gpt-oss-120b" in m.lower():
                        _append_unique(vision_chain, m)
                self.vision_models_chain = vision_chain

                now = time.time()
                self.last_refreshed_at = now
                self.last_refreshed_iso = datetime.fromtimestamp(
                    now, tz=timezone.utc
                ).isoformat()

                return self._snapshot_locked()

    def _needs_refresh_locked(self) -> bool:
        if self.last_refreshed_at <= 0.0:
            return True
        return (time.time() - self.last_refreshed_at) >= self.refresh_interval_seconds

    def _active_chain_locked(self, provider: str, chain: List[str]) -> List[str]:
        now = time.time()
        active: List[tuple[int, int, int, str]] = []
        alive_fallback: List[str] = []

        for idx, m in enumerate(chain):
            key = f"{provider}:{m}"
            if self.dead_models_until.get(key, 0.0) > now:
                continue
            alive_fallback.append(m)
            if self.cooldown_until.get(key, 0.0) > now:
                continue
            lat = self.model_latencies_ms.get(key)
            lat_tier = 1 if (lat is not None and lat > 4000.0) else 0
            lat_bucket = int(round(lat / 400.0)) if lat is not None else 4
            active.append((lat_tier, lat_bucket, idx, m))

        if active:
            active.sort(key=lambda item: (item[0], item[1], item[2]))
            return [item[3] for item in active]
        return alive_fallback if alive_fallback else list(chain)

    def _snapshot_locked(self) -> Dict[str, Any]:
        elapsed = max(0.0, time.time() - self.last_refreshed_at) if self.last_refreshed_at > 0 else 0.0
        next_in = max(0, int(round(self.refresh_interval_seconds - elapsed)))
        active_tool = self._active_chain_locked("groq", self.tool_models_chain)
        active_router = self._active_chain_locked("groq", self.router_models_chain)
        active_stt = self._active_chain_locked("groq", self.stt_models_chain)
        active_gemini = self._active_chain_locked("gemini", self.gemini_models_chain)
        active_nv_fast = self._active_chain_locked("nvidia", self.nvidia_fast_models_chain)
        active_nv_vision = self._active_chain_locked("nvidia", self.nvidia_vision_models_chain)
        return {
            "groq_configured": self.groq_configured,
            "gemini_configured": self.gemini_configured,
            "nvidia_configured": self.nvidia_configured,
            "last_refreshed_at": self.last_refreshed_at,
            "last_refreshed_iso": self.last_refreshed_iso,
            "refresh_interval_seconds": self.refresh_interval_seconds,
            "next_refresh_in_seconds": next_in,
            "primary_tool_model": active_tool[0] if active_tool else None,
            "backup_tool_models": active_tool[1:],
            "tool_models_chain": active_tool,
            "primary_router_model": active_router[0] if active_router else None,
            "backup_router_models": active_router[1:],
            "router_models_chain": active_router,
            "primary_stt_model": active_stt[0] if active_stt else None,
            "backup_stt_models": active_stt[1:],
            "stt_models_chain": active_stt,
            "primary_viral_model": active_gemini[0] if active_gemini else None,
            "backup_viral_models": active_gemini[1:],
            "gemini_models_chain": active_gemini,
            "primary_nvidia_fast_model": active_nv_fast[0] if active_nv_fast else None,
            "backup_nvidia_fast_models": active_nv_fast[1:],
            "nvidia_fast_models_chain": active_nv_fast,
            "primary_nvidia_vision_model": active_nv_vision[0] if active_nv_vision else None,
            "backup_nvidia_vision_models": active_nv_vision[1:],
            "nvidia_vision_models_chain": active_nv_vision,
            "vision_models_chain": list(self.vision_models_chain),
            "available_groq_models": list(self.available_groq_models),
            "available_gemini_models": list(self.available_gemini_models),
            "available_nvidia_models": list(self.available_nvidia_models),
            "groq_refresh_error": self.groq_refresh_error,
            "gemini_refresh_error": self.gemini_refresh_error,
            "nvidia_refresh_error": self.nvidia_refresh_error,
            "last_race_winner": dict(self.last_race_winner) if self.last_race_winner else None,
        }

    def get_state(self, force_refresh: bool = False) -> Dict[str, Any]:
        if force_refresh:
            return self.refresh_now(reload_env=True, clear_cooldowns=True, force_refresh=True)
        with self._lock:
            needs = self._needs_refresh_locked()
            if not needs:
                return self._snapshot_locked()
        return self.refresh_now(reload_env=True, clear_cooldowns=False, force_refresh=False)

    def get_tool_models_chain(self) -> List[str]:
        return list(self.get_state()["tool_models_chain"])

    def get_router_models_chain(self) -> List[str]:
        return list(self.get_state()["router_models_chain"])

    def get_stt_models_chain(self) -> List[str]:
        return list(self.get_state()["stt_models_chain"])

    def get_gemini_models_chain(self) -> List[str]:
        return list(self.get_state()["gemini_models_chain"])

    def get_vision_models_chain(self) -> List[str]:
        return list(self.get_state()["vision_models_chain"])

    def get_nvidia_fast_models_chain(self) -> List[str]:
        return list(self.get_state()["nvidia_fast_models_chain"])

    def get_nvidia_vision_models_chain(self) -> List[str]:
        return list(self.get_state()["nvidia_vision_models_chain"])

    def is_on_cooldown(self, provider: str, model_id: str) -> bool:
        now = time.time()
        key = f"{provider}:{model_id}"
        with self._lock:
            return (
                self.cooldown_until.get(key, 0.0) > now
                or self.dead_models_until.get(key, 0.0) > now
            )

    def record_latency(
        self, provider: str, model_id: str, latency_ms: float, task: str = ""
    ) -> None:
        key = f"{provider}:{model_id}"
        clean_ms = max(1.0, float(latency_ms))
        with self._lock:
            prev = self.model_latencies_ms.get(key)
            self.model_latencies_ms[key] = (
                clean_ms if prev is None else (prev * 0.65 + clean_ms * 0.35)
            )
            self.cooldown_until.pop(key, None)
            self.dead_models_until.pop(key, None)
            self.last_race_winner = {
                "provider": provider,
                "model": model_id,
                "latency_ms": round(clean_ms, 1),
                "task": task,
            }

    def report_failure(self, provider: str, model_id: str, error_text: str = "") -> None:
        """
        Demotes a failing/deprecated model to the back of its active chain immediately,
        bans 404/retired models for 1 hour (surviving auto-rescans unless API keys change),
        applies a 60s cooldown on 429 rate-limit/quota errors (300s on 401/403), and triggers
        a live rescan on the next call when a model is retired/404/decommissioned.
        """
        err_low = (error_text or "").lower()
        looks_retired = any(
            tok in err_low
            for tok in (
                "decommission",
                "deprecated",
                "not_found",
                "404",
                "does not exist",
                "not found",
                "unsupported model",
            )
        )
        looks_rate_limited = any(
            tok in err_low
            for tok in ("429", "rate_limit", "rate limit", "resource_exhausted", "quota", "too many requests")
        )
        looks_auth_error = any(
            tok in err_low for tok in ("401", "403", "unauthorized", "invalid api key", "forbidden")
        )
        with self._lock:
            key = f"{provider}:{model_id}"
            self.model_latencies_ms.pop(key, None)
            if looks_retired:
                # Ban broken / unprovisioned 404 catalog entries for 1 hour so auto-rescan doesn't re-promote them
                self.dead_models_until[key] = time.time() + 3600.0
            elif looks_rate_limited:
                self.cooldown_until[key] = time.time() + 60.0
            elif looks_auth_error:
                self.cooldown_until[key] = time.time() + 300.0
            else:
                self.cooldown_until[key] = time.time() + 15.0

            if provider == "gemini":
                chains = [self.gemini_models_chain, self.vision_models_chain]
            elif provider == "nvidia":
                chains = [
                    self.nvidia_fast_models_chain,
                    self.nvidia_vision_models_chain,
                    self.vision_models_chain,
                ]
            else:
                chains = [self.tool_models_chain, self.router_models_chain, self.stt_models_chain]
            for chain in chains:
                if model_id in chain and len(chain) > 1:
                    chain.remove(model_id)
                    chain.append(model_id)
            if looks_retired:
                # Force fresh API discovery on next registry read
                self.last_refreshed_at = 0.0


model_registry = ModelRegistry()


def get_registry_state(force_refresh: bool = False) -> Dict[str, Any]:
    return model_registry.get_state(force_refresh=force_refresh)


def refresh_models(force: bool = True) -> Dict[str, Any]:
    if force:
        return model_registry.refresh_now(reload_env=True, clear_cooldowns=True)
    return model_registry.get_state(force_refresh=False)


def get_tool_models_chain() -> List[str]:
    return model_registry.get_tool_models_chain()


def get_router_models_chain() -> List[str]:
    return model_registry.get_router_models_chain()


def get_stt_models_chain() -> List[str]:
    return model_registry.get_stt_models_chain()


def get_gemini_models_chain() -> List[str]:
    return model_registry.get_gemini_models_chain()


def get_vision_models_chain() -> List[str]:
    return model_registry.get_vision_models_chain()


def get_nvidia_fast_models_chain() -> List[str]:
    return model_registry.get_nvidia_fast_models_chain()


def get_nvidia_vision_models_chain() -> List[str]:
    return model_registry.get_nvidia_vision_models_chain()


def is_model_on_cooldown(provider: str, model_id: str) -> bool:
    return model_registry.is_on_cooldown(provider, model_id)


def record_model_latency(
    provider: str, model_id: str, latency_ms: float, task: str = ""
) -> None:
    model_registry.record_latency(provider, model_id, latency_ms, task=task)


def report_model_failure(provider: str, model_id: str, exc: Optional[BaseException] = None) -> None:
    model_registry.report_failure(provider, model_id, str(exc) if exc else "")
