import base64
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_PLACEHOLDER = "your_nvidia_api_key_here"


def get_nvidia_api_key() -> str:
    return os.getenv("NVIDIA_API_KEY", "").strip()


def is_nvidia_configured() -> bool:
    key = get_nvidia_api_key()
    return bool(key and key != NVIDIA_PLACEHOLDER)


def fetch_nvidia_model_ids(
    api_key: Optional[str] = None, timeout: float = 8.0
) -> Tuple[List[str], Optional[str]]:
    """Lists active model IDs from NVIDIA NIM's OpenAI-compatible /v1/models endpoint."""
    key = (api_key or get_nvidia_api_key()).strip()
    if not key or key == NVIDIA_PLACEHOLDER:
        return [], None
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(
                f"{NVIDIA_BASE_URL}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            if resp.status_code >= 400:
                return [], f"NVIDIA {resp.status_code}: {resp.text[:160]}"
            payload = resp.json()
        data = payload.get("data") or []
        ids = sorted(
            {
                str(item.get("id") or "").strip()
                for item in data
                if isinstance(item, dict) and item.get("id")
            }
        )
        return ids, None
    except Exception as exc:
        return [], str(exc)


def _extract_message_text(message_obj: Any) -> str:
    if not isinstance(message_obj, dict):
        return ""
    content = message_obj.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                txt = str(item.get("text") or "").strip()
                if txt:
                    parts.append(txt)
            elif isinstance(item, str) and item.strip():
                parts.append(item.strip())
        joined = "\n".join(parts).strip()
        if joined:
            return joined

    # Recover structured JSON if a reasoning VLM emitted the object inside reasoning_content right at max_tokens
    reasoning = str(message_obj.get("reasoning_content") or "").strip()
    if "{" in reasoning and "}" in reasoning:
        s_i = reasoning.find("{")
        e_i = reasoning.rfind("}")
        if e_i > s_i:
            return reasoning[s_i : e_i + 1].strip()
    return ""


def call_nvidia_chat(
    model_id: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.2,
    max_tokens: int = 1200,
    timeout: float = 15.0,
    api_key: Optional[str] = None,
    enable_thinking: Optional[bool] = None,
) -> str:
    """Calls NVIDIA NIM /v1/chat/completions for fast text, JSON, or base64-keyframe VLM tasks."""
    key = (api_key or get_nvidia_api_key()).strip()
    if not key or key == NVIDIA_PLACEHOLDER:
        raise RuntimeError("NVIDIA_API_KEY is not configured.")

    base_payload: Dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    payload = dict(base_payload)
    if enable_thinking is False:
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            f"{NVIDIA_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
        # Some NIM models reject chat_template_kwargs with 400/422; retry cleanly without it once
        if (
            enable_thinking is False
            and resp.status_code in (400, 422)
        ):
            resp = client.post(
                f"{NVIDIA_BASE_URL}/chat/completions",
                headers=headers,
                json=base_payload,
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"NVIDIA {resp.status_code}: {resp.text[:240]}")
        data = resp.json()

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"NVIDIA returned empty choices for {model_id}")
    text = _extract_message_text(choices[0].get("message"))
    if not text:
        raise RuntimeError(f"NVIDIA returned empty content for {model_id}")
    return text


def build_nvidia_vision_user_content(
    prompt_text: str, keyframes: List[Tuple[float, bytes]]
) -> List[Dict[str, Any]]:
    """Builds timestamped image_url (base64 JPEG) + prompt blocks for NVIDIA VLMs."""
    blocks: List[Dict[str, Any]] = []
    for ts, jpg_bytes in keyframes:
        b64 = base64.b64encode(jpg_bytes).decode("ascii")
        blocks.append({"type": "text", "text": f"[Frame at Timeline TL={ts:.2f}s]"})
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )
    blocks.append({"type": "text", "text": prompt_text})
    return blocks
