"""Async HTTP streaming client (httpx, imported lazily so tests don't need it)."""
from __future__ import annotations

import json
from typing import AsyncIterator
from urllib.parse import urlparse

from .providers import (ProviderConfig, ProviderError, SSEParser, build_request, friendly_http_error,
                        models_request, parse_models, parse_stream_event)
from .tools import (SHELL_TOOL, ToolCall, ToolCallAccumulator, build_request_with_tools,
                    supports_tools)


def _httpx():
    try:
        import httpx
        return httpx
    except ImportError as e:
        raise ProviderError("The 'httpx' package isn't installed.") from e


async def stream_chat(cfg: ProviderConfig, messages: list[dict], system: str) -> AsyncIterator[str]:
    httpx = _httpx()
    req = build_request(cfg, messages, system)
    host = urlparse(req.url).netloc
    timeout = httpx.Timeout(connect=15, read=120, write=30, pool=15)
    sse = SSEParser()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", req.url, headers=req.headers, json=req.body) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode(errors="replace")
                    raise ProviderError(friendly_http_error(r.status_code, body))
                async for line in r.aiter_lines():
                    ev = sse.feed(line)
                    if ev:
                        text = parse_stream_event(cfg.kind, ev[1])
                        if text:
                            yield text
    except httpx.ConnectError as e:
        raise ProviderError(f"Can't reach {host}. Is the server running and the URL correct?") from e
    except httpx.TimeoutException as e:
        raise ProviderError("The AI server timed out.") from e
    except httpx.HTTPError as e:
        raise ProviderError(f"Network error talking to {host}.") from e


# ---- native tool-calling streaming -----------------------------
#
# stream_chat_with_tools yields ChatEvent dicts:
#   {"type": "text", "delta": "..."}                     → assistant text delta
#   {"type": "tool_calls", "calls": [ToolCall, ...]}     → one batch at stream end
#
# The caller is responsible for: (a) appending the text deltas to the assistant
# message, (b) running the tool calls (subject to approval), (c) feeding the
# tool results back into the next request as a tool_result message, (d) looping
# until the assistant stops calling tools.


class ChatEvent:
    """Sentinel class so callers can `isinstance` check; values are plain dicts."""
    pass


async def stream_chat_with_tools(cfg: ProviderConfig, messages: list[dict], system: str,
                                 tools_enabled: bool = True) -> AsyncIterator[dict]:
    """Stream a chat completion with native tool-calling.

    Yields dicts with `type` in {"text", "tool_calls", "done"}. Falls back to
    plain-text streaming (no tool calls) when the provider doesn't support tools
    or when tools_enabled is False — so callers can use this for both modes.
    """
    if not tools_enabled or not supports_tools(cfg.kind):
        async for delta in stream_chat(cfg, messages, system):
            yield {"type": "text", "delta": delta}
        yield {"type": "done"}
        return

    httpx = _httpx()
    # Build the base request then merge in the tools kwargs.
    req = build_request(cfg, messages, system)
    extra = build_request_with_tools(cfg, messages, system, tools_enabled=True)
    body = {**req.body, **extra}
    host = urlparse(req.url).netloc
    timeout = httpx.Timeout(connect=15, read=120, write=30, pool=15)
    sse = SSEParser()
    acc = ToolCallAccumulator(cfg.kind)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", req.url, headers=req.headers, json=body) as r:
                if r.status_code >= 400:
                    raw = (await r.aread()).decode(errors="replace")
                    raise ProviderError(friendly_http_error(r.status_code, raw))
                async for line in r.aiter_lines():
                    ev = sse.feed(line)
                    if not ev:
                        continue
                    data = ev[1]
                    # First, try to extract text deltas (same path as plain streaming).
                    text = parse_stream_event(cfg.kind, data)
                    if text:
                        yield {"type": "text", "delta": text}
                    # Then, accumulate tool-call deltas. We feed the raw JSON obj.
                    try:
                        obj = json.loads(data) if data.strip() and data.strip() != "[DONE]" else None
                    except ValueError:
                        obj = None
                    if isinstance(obj, dict):
                        acc.feed(obj)
    except httpx.ConnectError as e:
        raise ProviderError(f"Can't reach {host}. Is the server running and the URL correct?") from e
    except httpx.TimeoutException as e:
        raise ProviderError("The AI server timed out.") from e
    except httpx.HTTPError as e:
        raise ProviderError(f"Network error talking to {host}.") from e
    calls = acc.finish()
    if calls:
        yield {"type": "tool_calls", "calls": calls}
    yield {"type": "done"}


async def list_models(cfg: ProviderConfig) -> list[str]:
    httpx = _httpx()
    url, headers = models_request(cfg)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20)) as client:
            r = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        raise ProviderError(f"Can't reach {urlparse(url).netloc}.") from e
    if r.status_code >= 400:
        raise ProviderError(friendly_http_error(r.status_code, r.text))
    try:
        return parse_models(cfg.kind, r.json())
    except (ValueError, KeyError, TypeError) as e:
        raise ProviderError("The server's model list wasn't in the expected format.") from e


async def test_connection(cfg: ProviderConfig) -> str:
    """Raises ProviderError on failure; returns a short success message."""
    got = ""
    small = ProviderConfig(**{**cfg.__dict__, "max_tokens": 16})
    async for chunk in stream_chat(small, [{"role": "user", "content": "Reply with the single word: OK"}], ""):
        got += chunk
        if len(got) > 2:
            break
    return "Connected — the model replied." if got else "Connected, but the model returned no text."
