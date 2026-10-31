"""Provider adapters: build requests and parse streaming events. Pure (no network here)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

KINDS = {"openai": "OpenAI-compatible", "anthropic": "Anthropic", "gemini": "Google Gemini"}
DEFAULT_URLS = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com",
                "gemini": "https://generativelanguage.googleapis.com"}


class ProviderError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class ProviderConfig:
    kind: str
    base_url: str
    model: str
    api_key: str = field(default="", repr=False)          # never print/log
    temperature: float = 0.2
    max_tokens: int = 1024
    headers: dict = field(default_factory=dict, repr=False)


@dataclass
class ProviderRequest:
    url: str
    headers: dict
    body: dict


def _base(cfg: ProviderConfig) -> str:
    return (cfg.base_url or DEFAULT_URLS[cfg.kind]).rstrip("/")


def _headers(cfg: ProviderConfig, extra: dict) -> dict:
    h = {"Content-Type": "application/json", **extra}
    h.update({k: v for k, v in (cfg.headers or {}).items() if k and v is not None})
    return h


def build_request(cfg: ProviderConfig, messages: list[dict], system: str) -> ProviderRequest:
    """`messages`: [{"role": "user"|"assistant", "content": str}], alternating, starting with user."""
    base = _base(cfg)
    if cfg.kind == "openai":
        auth = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        body = {"model": cfg.model, "stream": True, "temperature": cfg.temperature, "max_tokens": cfg.max_tokens,
                "messages": ([{"role": "system", "content": system}] if system else []) + messages}
        return ProviderRequest(f"{base}/chat/completions", _headers(cfg, auth), body)
    if cfg.kind == "anthropic":
        h = {"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01"}
        body = {"model": cfg.model, "stream": True, "max_tokens": cfg.max_tokens, "messages": messages,
                "temperature": min(1.0, max(0.0, cfg.temperature))}
        if system:
            body["system"] = system
        return ProviderRequest(f"{base}/v1/messages", _headers(cfg, h), body)
    if cfg.kind == "gemini":
        body = {"contents": [{"role": "model" if m["role"] == "assistant" else "user",
                              "parts": [{"text": m["content"]}]} for m in messages],
                "generationConfig": {"temperature": cfg.temperature, "maxOutputTokens": cfg.max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        return ProviderRequest(f"{base}/v1beta/models/{cfg.model}:streamGenerateContent?alt=sse",
                               _headers(cfg, {"x-goog-api-key": cfg.api_key}), body)
    raise ProviderError(f"Unknown provider type: {cfg.kind}")


def _err_text(obj) -> str:
    if isinstance(obj, dict):
        e = obj.get("error", obj)
        if isinstance(e, dict):
            return str(e.get("message") or e.get("status") or "")[:300]
        return str(e)[:300]
    return str(obj)[:300]


def parse_stream_event(kind: str, data: str) -> str:
    """Text delta contained in one SSE `data:` payload ('' if none). Raises ProviderError on error payloads."""
    data = data.strip()
    if not data or data == "[DONE]":
        return ""
    try:
        obj = json.loads(data)
    except ValueError:
        return ""
    if not isinstance(obj, dict):
        return ""
    if kind == "openai":
        if "error" in obj:
            raise ProviderError(_err_text(obj))
        ch = (obj.get("choices") or [{}])[0]
        return ((ch.get("delta") or {}).get("content")) or ""
    if kind == "anthropic":
        if obj.get("type") == "error":
            raise ProviderError(_err_text(obj))
        if obj.get("type") == "content_block_delta":
            d = obj.get("delta") or {}
            return d.get("text", "") if d.get("type") == "text_delta" else ""
        return ""
    if kind == "gemini":
        if "error" in obj:
            raise ProviderError(_err_text(obj))
        block = (obj.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise ProviderError(f"The request was blocked by the provider ({block}).")
        cand = (obj.get("candidates") or [{}])[0]
        return "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
    return ""


def models_request(cfg: ProviderConfig) -> tuple[str, dict]:
    base = _base(cfg)
    if cfg.kind == "openai":
        return f"{base}/models", _headers(cfg, {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {})
    if cfg.kind == "anthropic":
        return f"{base}/v1/models", _headers(cfg, {"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01"})
    return f"{base}/v1beta/models", _headers(cfg, {"x-goog-api-key": cfg.api_key})


def parse_models(kind: str, payload: dict) -> list[str]:
    if kind == "gemini":
        return sorted(m["name"].removeprefix("models/") for m in payload.get("models", []) if "name" in m)
    return sorted(m["id"] for m in payload.get("data", []) if "id" in m)


class SSEParser:
    """Feed raw lines; returns (event, data) when a blank line completes an event."""

    def __init__(self) -> None:
        self._event, self._data = "", []

    def feed(self, line: str) -> Optional[tuple[str, str]]:
        line = line.rstrip("\r\n")
        if not line:
            if not self._data:
                self._event = ""
                return None
            out = (self._event, "\n".join(self._data))
            self._event, self._data = "", []
            return out
        if line.startswith(":"):
            return None
        field_, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field_ == "event":
            self._event = value
        elif field_ == "data":
            self._data.append(value)
        return None


def friendly_http_error(status: int, body: str) -> str:
    detail = ""
    try:
        detail = _err_text(json.loads(body))
    except ValueError:
        pass
    if status in (401, 403):
        return "The API key was rejected. Check the key and that it matches this provider."
    if status == 404:
        return "Not found — check the base URL and the model name."
    if status == 429:
        return "Rate limited or out of quota. Try again shortly."
    if status == 400:
        return f"The provider rejected the request: {detail or 'bad request'}"
    if status >= 500:
        return f"The AI server had an error ({status})."
    return f"Request failed ({status}). {detail}".strip()
