import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from groq import Groq

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


def resolve_gemini_models(live_gemini_ids: List[str]) -> List[str]:
    """
    Orders Gemini multimodal models:
    1. gemini-2.5-flash (Primary)
    2. gemini-2.5-pro / gemini-2.0-flash (Backups)
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

    ordered: List[str] = []
    preferred_prefixes = (
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
    )
    for pref in preferred_prefixes:
        # Prefer exact match first, then non-lite variants
        exact = [m for m in candidates if m.lower() == pref]
        for m in exact:
            _append_unique(ordered, m)
        non_lite = [
            m
            for m in candidates
            if m.lower().startswith(pref) and "lite" not in m.lower()
        ]
        for m in non_lite:
            _append_unique(ordered, m)

    for m in candidates:
        if m.lower().startswith(("gemini-2.5", "gemini-2.0")):
            _append_unique(ordered, m)

    return ordered or list(DEFAULT_GEMINI_MODELS)


class ModelRegistry:
    """Thread-safe live model discovery cache with 24-hour auto-refresh & manual force-refresh."""

    def __init__(self, refresh_interval_seconds: int = REFRESH_INTERVAL_SECONDS) -> None:
        self._lock = threading.Lock()
        self.refresh_interval_seconds = refresh_interval_seconds
        self.last_refreshed_at: float = 0.0
        self.last_refreshed_iso: Optional[str] = None
        self.available_groq_models: List[str] = []
        self.available_gemini_models: List[str] = []
        self.tool_models_chain: List[str] = list(DEFAULT_TOOL_MODELS)
        self.router_models_chain: List[str] = list(DEFAULT_ROUTER_MODELS)
        self.stt_models_chain: List[str] = list(DEFAULT_STT_MODELS)
        self.gemini_models_chain: List[str] = list(DEFAULT_GEMINI_MODELS)
        self.vision_models_chain: List[str] = list(DEFAULT_GEMINI_MODELS) + [
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-120b",
        ]
        self.groq_configured: bool = False
        self.gemini_configured: bool = False
        self.groq_refresh_error: Optional[str] = None
        self.gemini_refresh_error: Optional[str] = None

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
            client = genai.Client(api_key=api_key)
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

    def refresh_now(self, reload_env: bool = True) -> Dict[str, Any]:
        with self._lock:
            if reload_env and ENV_PATH.exists():
                load_dotenv(ENV_PATH, override=True)

            self.groq_configured = _is_key_configured(
                "GROQ_API_KEY", "your_groq_api_key_here"
            )
            self.gemini_configured = _is_key_configured(
                "GEMINI_API_KEY", "your_gemini_api_key_here"
            )

            live_groq, groq_err = self._fetch_groq_models()
            live_gemini, gemini_err = self._fetch_gemini_models()

            self.available_groq_models = live_groq
            self.available_gemini_models = live_gemini
            self.groq_refresh_error = groq_err
            self.gemini_refresh_error = gemini_err

            self.tool_models_chain = resolve_tool_models(live_groq)
            self.router_models_chain = resolve_router_models(live_groq)
            self.stt_models_chain = resolve_stt_models(live_groq)
            self.gemini_models_chain = resolve_gemini_models(live_gemini)

            vision_chain: List[str] = []
            for m in self.gemini_models_chain:
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

    def _snapshot_locked(self) -> Dict[str, Any]:
        elapsed = max(0.0, time.time() - self.last_refreshed_at) if self.last_refreshed_at > 0 else 0.0
        next_in = max(0, int(round(self.refresh_interval_seconds - elapsed)))
        return {
            "groq_configured": self.groq_configured,
            "gemini_configured": self.gemini_configured,
            "last_refreshed_at": self.last_refreshed_at,
            "last_refreshed_iso": self.last_refreshed_iso,
            "refresh_interval_seconds": self.refresh_interval_seconds,
            "next_refresh_in_seconds": next_in,
            "primary_tool_model": self.tool_models_chain[0] if self.tool_models_chain else None,
            "backup_tool_models": self.tool_models_chain[1:],
            "tool_models_chain": list(self.tool_models_chain),
            "primary_router_model": self.router_models_chain[0] if self.router_models_chain else None,
            "backup_router_models": self.router_models_chain[1:],
            "router_models_chain": list(self.router_models_chain),
            "primary_stt_model": self.stt_models_chain[0] if self.stt_models_chain else None,
            "backup_stt_models": self.stt_models_chain[1:],
            "stt_models_chain": list(self.stt_models_chain),
            "primary_viral_model": self.gemini_models_chain[0] if self.gemini_models_chain else None,
            "backup_viral_models": self.gemini_models_chain[1:],
            "gemini_models_chain": list(self.gemini_models_chain),
            "vision_models_chain": list(self.vision_models_chain),
            "available_groq_models": list(self.available_groq_models),
            "available_gemini_models": list(self.available_gemini_models),
            "groq_refresh_error": self.groq_refresh_error,
            "gemini_refresh_error": self.gemini_refresh_error,
        }

    def get_state(self, force_refresh: bool = False) -> Dict[str, Any]:
        if force_refresh:
            return self.refresh_now(reload_env=True)
        with self._lock:
            needs = self._needs_refresh_locked()
            if not needs:
                return self._snapshot_locked()
        return self.refresh_now(reload_env=True)

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


model_registry = ModelRegistry()


def get_registry_state(force_refresh: bool = False) -> Dict[str, Any]:
    return model_registry.get_state(force_refresh=force_refresh)


def refresh_models(force: bool = True) -> Dict[str, Any]:
    if force:
        return model_registry.refresh_now(reload_env=True)
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
