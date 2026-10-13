"""Fast one-shot structured LLM calls — one prompt in, one JSON object out.

Used for small jobs that don't need an agent loop: chat titles, play
suggestions, view specs, report digests, memory extraction. No CLI spawn, no
tools — a single HTTP request with a timeout, then the next provider.

Providers (tried in order, the job's configured provider first):

* ``together`` — Together AI chat completions (OpenAI-compatible) with
  ``response_format: json_schema``; on a schema rejection it retries in plain
  JSON mode. Key: ``TOGETHER_API_KEY`` env or Settings → Data providers.
* ``anthropic`` — the Claude Messages API (``anthropic`` SDK) with
  ``output_config.format`` structured output, when ``ANTHROPIC_API_KEY`` is set.
  Defaults to Claude Haiku 5.5.
* ``gemini`` — only when a Gemini key is configured and a job opts into it.
* ``claude_sdk`` — the Claude Agent SDK (spawns the CLI; slow cold start).
  Last resort, and only for jobs that allow it.

Every successful call records a usage row (source = the job) so costs show up
next to agent runs; Together's token usage is priced with configurable
per-million rates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

TOGETHER_URL = "https://api.together.xyz/v1/chat/completions"

PROVIDERS = ("together", "anthropic", "gemini", "claude_sdk")

# Default fast models per provider.
DEFAULT_MODELS = {
    "together": "deepseek-ai/DeepSeek-V4.1-Flash",
    "anthropic": "claude-haiku-5-5",
    "gemini": "gemini-3.1-flash-lite",
    "claude_sdk": "claude-haiku-5-5",
}

# USD per million tokens (input, output). Together prices are an estimate —
# override in Settings → Models with your account's rates.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "together": (0.30, 1.20),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "gemini": (0.10, 0.40),
}


class NoProvider(RuntimeError):
    """No provider is configured/reachable for this job."""


class ProviderUnavailable(RuntimeError):
    """This provider can't serve the call (no key, rejected key, not installed)."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None  # when the provider reports cost itself (Agent SDK)


@dataclass
class LLMResult:
    data: dict[str, Any]
    provider: str
    model: str
    duration_ms: int = 0
    usage: Usage = field(default_factory=Usage)
    cost_usd: float | None = None
    errors: list[str] = field(default_factory=list)


# ── keys / availability ─────────────────────────────────────────────────
def together_key() -> str:
    from app.services.intel_service import _key

    return _key("TOGETHER_API_KEY", "together_api_key")


def anthropic_key() -> str:
    v = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if v:
        return v
    from app.core.config import get_settings

    return (get_settings().anthropic_api_key or "").strip()


def anthropic_sdk_installed() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def gemini_key() -> str:
    from app.core.config import get_settings

    return (get_settings().gemini_api_key or "").strip()


def claude_sdk_available() -> bool:
    import shutil

    return bool(anthropic_key() or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or shutil.which("claude"))


def available(provider: str) -> bool:
    if provider == "together":
        return bool(together_key())
    if provider == "anthropic":
        return bool(anthropic_key()) and anthropic_sdk_installed()
    if provider == "gemini":
        return bool(gemini_key())
    if provider == "claude_sdk":
        return claude_sdk_available()
    return False


# ── JSON helpers ────────────────────────────────────────────────────────
def parse_json(text: str) -> dict[str, Any]:
    """Extract the JSON object from a reply (tolerates code fences / prose)."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the reply contained no JSON object")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("the reply was not a JSON object")
    return data


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Copy of ``schema`` with ``additionalProperties: false`` on every object
    (required by Claude structured outputs)."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items()}
            if out.get("type") == "object" or (isinstance(out.get("type"), list) and "object" in out["type"]):
                out.setdefault("additionalProperties", False)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


# ── pricing ─────────────────────────────────────────────────────────────
def price_for(provider: str, model: str) -> tuple[float, float] | None:
    from app.services import model_routing

    prices = model_routing.prices()
    if model in prices:
        return prices[model]
    if provider in prices:
        return prices[provider]
    return None


def cost_of(provider: str, model: str, usage: Usage) -> float | None:
    if usage.cost_usd is not None:
        return round(usage.cost_usd, 6)
    p = price_for(provider, model)
    if p is None or (usage.input_tokens == 0 and usage.output_tokens == 0):
        return None
    return round(usage.input_tokens / 1e6 * p[0] + usage.output_tokens / 1e6 * p[1], 6)


def _record(job: str, usage_id: str, res: LLMResult) -> None:
    try:
        from app.core.database import SessionLocal
        from app.services import costs_service

        with SessionLocal() as db:
            costs_service.record(db, source=job[:32], source_id=(usage_id or job)[:240], model=f"{res.provider}:{res.model}"[:120],
                                 total_cost_usd=res.cost_usd, duration_ms=res.duration_ms, num_turns=1)
    except Exception:  # noqa: BLE001 — usage is best-effort
        logger.debug("fast_llm usage record failed", exc_info=True)


# ── provider calls: messages in → (text, usage) out ─────────────────────
async def together_chat(messages: list[dict[str, str]], *, schema: dict[str, Any] | None, model: str, timeout: float,
                        name: str = "reply", max_tokens: int = 4000, temperature: float = 0.2,
                        key: str | None = None) -> tuple[str, Usage]:
    key = key if key is not None else together_key()
    if not key:
        raise ProviderUnavailable("no Together API key")
    # Hybrid reasoning models (DeepSeek V4.x) otherwise "think" for ~2k tokens
    # first: ~11s instead of ~3s for these one-shot structured jobs.
    body: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                            "reasoning": {"enabled": False}}
    if schema is not None:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": name, "schema": schema}}
    else:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(TOGETHER_URL, json=body, headers=headers)
        if resp.status_code == 400 and "schema" in resp.text.lower() and schema is not None:
            # Schema feature unsupported for this model → plain JSON mode.
            body["response_format"] = {"type": "json_object"}
            resp = await client.post(TOGETHER_URL, json=body, headers=headers)
    if resp.status_code in (401, 403):
        raise ProviderUnavailable(f"Together rejected the API key (HTTP {resp.status_code})")
    if resp.status_code >= 400:
        raise RuntimeError(f"Together HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    u = data.get("usage") or {}
    usage = Usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))
    text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    return text, usage


async def anthropic_chat(messages: list[dict[str, str]], *, schema: dict[str, Any] | None, model: str, timeout: float,
                         max_tokens: int = 4000) -> tuple[str, Usage]:
    """Claude Messages API — no CLI spawn. Structured output via output_config.format."""
    key = anthropic_key()
    if not key:
        raise ProviderUnavailable("no ANTHROPIC_API_KEY")
    try:
        import anthropic
    except ImportError as exc:
        raise ProviderUnavailable("the anthropic package isn't installed") from exc
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    convo = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
    kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": convo}
    if system:
        kwargs["system"] = system
    output_config: dict[str, Any] = {"effort": "low"}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": strict_schema(schema)}
    kwargs["output_config"] = output_config
    client = anthropic.AsyncAnthropic(api_key=key, timeout=timeout, max_retries=1)
    try:
        resp = await client.messages.create(**kwargs)
    except anthropic.AuthenticationError as exc:
        raise ProviderUnavailable("Anthropic rejected the API key") from exc
    if getattr(resp, "stop_reason", None) == "refusal":
        raise RuntimeError("Claude declined the request")
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    u = getattr(resp, "usage", None)
    return text, Usage(int(getattr(u, "input_tokens", 0) or 0), int(getattr(u, "output_tokens", 0) or 0))


async def gemini_chat(messages: list[dict[str, str]], *, schema: dict[str, Any] | None, model: str, timeout: float,
                      max_tokens: int = 4000) -> tuple[str, Usage]:
    key = gemini_key()
    if not key:
        raise ProviderUnavailable("no Gemini API key")
    from google import genai

    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    convo = "\n\n".join(f"{m['role'].upper()}:\n{m['content']}" for m in messages if m["role"] != "system")
    config: dict[str, Any] = {"response_mime_type": "application/json", "max_output_tokens": max_tokens}
    if system:
        config["system_instruction"] = system
    if schema is not None:
        config["response_json_schema"] = schema
    client = genai.Client(api_key=key)
    resp = await asyncio.wait_for(client.aio.models.generate_content(model=model, contents=convo, config=config), timeout)
    meta = getattr(resp, "usage_metadata", None)
    usage = Usage(int(getattr(meta, "prompt_token_count", 0) or 0), int(getattr(meta, "candidates_token_count", 0) or 0))
    return str(resp.text or ""), usage


async def claude_sdk_chat(messages: list[dict[str, str]], *, schema: dict[str, Any] | None, model: str, timeout: float,
                          max_tokens: int = 4000) -> tuple[str, Usage]:
    """Last resort: the Agent SDK with no tools, one turn. Slow (CLI cold start)."""
    try:
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

        from app.services.claude_sdk_config import configure_sdk_auth
    except ImportError as exc:  # pragma: no cover
        raise ProviderUnavailable("Claude Agent SDK not installed") from exc
    if not claude_sdk_available():
        raise ProviderUnavailable("Claude isn't signed in")
    configure_sdk_auth()
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    convo = "\n\n".join(f"{m['role'].upper()}:\n{m['content']}" for m in messages if m["role"] != "system")
    if schema is not None:
        system += "\n\nReturn ONLY a JSON object matching this JSON schema, no prose, no code fences:\n" + json.dumps(schema)
    else:
        system += "\n\nReturn only the JSON object, no prose, no code fences."
    options = ClaudeAgentOptions(system_prompt=system, model=model, tools=[], allowed_tools=[], setting_sources=[], max_turns=1)
    out: list[str] = []
    usage = Usage()

    async def run() -> None:
        async for msg in query(prompt=convo, options=options):
            if isinstance(msg, AssistantMessage):
                out.extend(b.text for b in msg.content if isinstance(b, TextBlock))
            elif isinstance(msg, ResultMessage):
                if getattr(msg, "is_error", False):
                    raise RuntimeError(str(getattr(msg, "result", "") or "Claude error"))
                u = getattr(msg, "usage", None) or {}
                usage.input_tokens = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
                usage.output_tokens = int(u.get("output_tokens") or 0)
                usage.cost_usd = getattr(msg, "total_cost_usd", None)

    await asyncio.wait_for(run(), timeout)
    return "".join(out), usage


_CALLS: dict[str, Callable[..., Any]] = {
    "together": together_chat,
    "anthropic": anthropic_chat,
    "gemini": gemini_chat,
    "claude_sdk": claude_sdk_chat,
}


async def chat(provider: str, messages: list[dict[str, str]], *, schema: dict[str, Any] | None, model: str | None = None,
               timeout: float = 20.0, max_tokens: int = 4000, name: str = "reply") -> tuple[str, Usage, str]:
    """One raw call to one provider → (text, usage, model used)."""
    model = model or DEFAULT_MODELS[provider]
    fn = _CALLS[provider]
    kw: dict[str, Any] = {"schema": schema, "model": model, "timeout": timeout, "max_tokens": max_tokens}
    if provider == "together":
        kw["name"] = name
    text, usage = await asyncio.wait_for(fn(messages, **kw), timeout + 2)
    return text, usage, model


# ── the main entry point ────────────────────────────────────────────────
async def complete_json(
    job: str,
    *,
    system: str,
    user: str,
    schema: dict[str, Any] | None = None,
    validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    timeout: float | None = None,
    max_tokens: int = 4000,
    usage_id: str = "",
    chain: list[tuple[str, str]] | None = None,
    record_usage: bool = True,
) -> LLMResult:
    """Run ``job`` as one structured call down its fallback chain.

    ``validate`` may transform the parsed object or raise ``ValueError`` to
    reject it (the next provider is then tried). Raises ``NoProvider`` when no
    provider is configured, ``RuntimeError`` when all configured ones failed.
    """
    from app.services import model_routing

    plan = chain if chain is not None else model_routing.fast_chain(job)
    plan = [(p, m) for p, m in plan if available(p)]
    if not plan:
        raise NoProvider(f"no model is configured for {job}")
    t = timeout if timeout is not None else model_routing.timeout_for(job)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    errors: list[str] = []
    for provider, model in plan:
        started = time.monotonic()
        try:
            text, usage, used = await chat(provider, messages, schema=schema, model=model, timeout=t,
                                           max_tokens=max_tokens, name=job.replace(".", "_")[:60])
            data = parse_json(text)
            if validate is not None:
                data = validate(data)
            res = LLMResult(data=data, provider=provider, model=used, duration_ms=int((time.monotonic() - started) * 1000),
                            usage=usage, cost_usd=cost_of(provider, used, usage), errors=errors)
            if record_usage:
                _record(job, usage_id, res)
            logger.info("fast_llm %s via %s/%s in %dms", job, provider, used, res.duration_ms)
            return res
        except (asyncio.TimeoutError, httpx.TimeoutException):
            errors.append(f"{provider}: timed out after {t:.0f}s")
        except ProviderUnavailable as exc:
            errors.append(f"{provider}: {exc}")
        except (ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{provider}: unusable reply ({str(exc)[:120]})")
        except Exception as exc:  # noqa: BLE001 — try the next provider
            logger.warning("fast_llm %s: %s failed", job, provider, exc_info=True)
            errors.append(f"{provider}: {str(exc)[:160]}")
    raise RuntimeError(f"{job}: " + "; ".join(errors))


def complete_json_sync(job: str, **kwargs: Any) -> LLMResult:
    """Blocking wrapper for sync callers (runs its own event loop)."""
    return asyncio.run(complete_json(job, **kwargs))
