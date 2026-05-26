"""Unified LLM client with Anthropic → OpenAI → Ollama routing.

Tries Anthropic (Claude) first. If Anthropic fails due to rate-limit, quota
exhaustion, overload, or authentication errors, automatically falls back to
OpenAI (GPT-5.5 by default). Also supports local Ollama models via the OpenAI-compatible
API (http://localhost:11434/v1).

All callers get back a response object matching Anthropic's interface:
    response.content[0].text
    response.usage.input_tokens
    response.usage.output_tokens

Usage:
    from llm_client import create_message, get_llm_client
    # Simple function call (drop-in for client.messages.create):
    response = create_message(model=MODEL, max_tokens=4096, system="...", messages=[...])

    # Or get a client object with .messages.create():
    client = get_llm_client()
    response = client.messages.create(model=MODEL, max_tokens=4096, ...)
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
try:
    from langfuse.decorators import observe
except ImportError:
    # Langfuse v4+ moved/removed decorators; fall back to no-op
    def observe(*args, **kwargs):
        def decorator(fn):
            return fn
        if args and callable(args[0]):
            return args[0]
        return decorator

from config import (
    ANTHROPIC_API_KEY,
    OLLAMA_URL,
    OLLAMA_MODEL,
    OLLAMA_REASONING_MODEL,
    OLLAMA_NUM_CTX,
    APPROVED_REASONING_MODELS,
    OPENAI_API_KEY,
    OPENAI_FALLBACK_MODEL,
    normalize_reasoning_model,
)

# ---------------------------------------------------------------------------
# OpenAI config
# ---------------------------------------------------------------------------
OPENAI_FALLBACK_CHAIN: List[str] = []
for _model in (OPENAI_FALLBACK_MODEL,):
    if _model and _model not in OPENAI_FALLBACK_CHAIN:
        OPENAI_FALLBACK_CHAIN.append(_model)

# ---------------------------------------------------------------------------
# Ollama config  (set at module level; overridden by set_ollama_override())
# ---------------------------------------------------------------------------
_ollama_override: Optional[str] = None  # "model_name" when --ollama-model is used


def set_ollama_override(model: str, url: Optional[str] = None) -> None:
    """Activate Ollama as the sole LLM provider for all subsequent calls.

    Args:
        model: approved Ollama reasoning model, "deepseek-r1" or "qwq".
        url:   Base URL override (default from OLLAMA_URL env / config).
    """
    global _ollama_override, _ollama_url, _active_provider
    model = normalize_reasoning_model(model)
    if APPROVED_REASONING_MODELS[model]["provider"] != "ollama":
        raise ValueError("--ollama-model must be one of the approved local reasoning models: deepseek-r1, qwq")
    _ollama_override = model
    if url:
        _ollama_url = url
    _active_provider = "ollama"


# Effective Ollama URL — can be patched by set_ollama_override()
_ollama_url: str = OLLAMA_URL or "http://localhost:11434"


def _is_ollama_model(model: str) -> bool:
    """Return True if Ollama should handle this model."""
    if _ollama_override:
        return True
    # Explicit env-var models are also Ollama-bound
    if OLLAMA_MODEL and model == OLLAMA_MODEL:
        return True
    if OLLAMA_REASONING_MODEL and model == OLLAMA_REASONING_MODEL:
        return True
    return False


def _is_gemini_model(model: str) -> bool:
    try:
        canonical = normalize_reasoning_model(model)
    except ValueError:
        return False
    return APPROVED_REASONING_MODELS[canonical]["provider"] == "google"


def _effective_ollama_model(model: str) -> str:
    """Resolve which Ollama model tag to use."""
    if _ollama_override:
        return _ollama_override
    return model


def _is_openai_model(model: str) -> bool:
    """Return True if the model name is an approved OpenAI reasoning model."""
    try:
        canonical = normalize_reasoning_model(model)
    except ValueError:
        return False
    return APPROVED_REASONING_MODELS[canonical]["provider"] == "openai"

# Track which provider is active so callers can log it
_active_provider = "anthropic"
_anthropic_disabled = False  # Sticky flag: once Anthropic fails with quota, skip it


def get_active_provider() -> str:
    """Return 'anthropic' or 'openai' depending on which is currently active."""
    return _active_provider


# ---------------------------------------------------------------------------
# Normalised response objects (match Anthropic SDK shapes)
# ---------------------------------------------------------------------------
@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class _ContentBlock:
    text: str = ""
    type: str = "text"


@dataclass
class _ToolCall:
    id: str
    name: str
    input: Dict[str, Any]


@dataclass
class _NormalisedResponse:
    """Mimics anthropic.types.Message so callers don't need to change."""
    content: List[_ContentBlock] = field(default_factory=list)
    usage: _Usage = field(default_factory=_Usage)
    model: str = ""
    provider: str = ""
    tool_calls: List[_ToolCall] = field(default_factory=list)
    stop_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Anthropic error detection
# ---------------------------------------------------------------------------
_FALLBACK_TRIGGERS = (
    "rate_limit",
    "rate limit",
    "overloaded",
    "quota",
    "credit",
    "insufficient_quota",
    "exceeded your current",
    "billing",
    "capacity",
    "529",
)


def _is_fallback_trigger(err: Exception) -> bool:
    """Return True if this Anthropic error should trigger OpenAI fallback."""
    err_str = str(err).lower()
    err_type = type(err).__name__.lower()

    # Direct type checks
    if "ratelimit" in err_type or "overloaded" in err_type:
        return True

    # Check HTTP status codes if available
    status = getattr(err, "status_code", None) or getattr(err, "status", None)
    if status in (429, 529):
        return True

    # String matching
    return any(trigger in err_str for trigger in _FALLBACK_TRIGGERS)


# ---------------------------------------------------------------------------
# Anthropic → OpenAI message translation
# ---------------------------------------------------------------------------

def _translate_messages_for_openai(
    system: Any,
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert Anthropic-style messages to OpenAI chat format."""
    oai_messages = []

    # System prompt → system role message
    if system:
        if isinstance(system, str):
            oai_messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            # Combine text parts for OpenAI system, stripping cache_control
            text_parts = []
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            oai_messages.append({"role": "system", "content": "\n\n".join(text_parts)})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            oai_messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            # Anthropic content blocks → OpenAI content parts
            parts = []
            tool_calls = []
            tool_results = []
            for block in content:
                if isinstance(block, str):
                    parts.append({"type": "text", "text": block})
                elif isinstance(block, dict):
                    block_type = block.get("type", "")
                    if block_type == "text":
                        parts.append({"type": "text", "text": block.get("text", "")})
                    elif block_type == "image":
                        # Anthropic image format → OpenAI image_url format
                        source = block.get("source", {})
                        media_type = source.get("media_type", "image/png")
                        data = source.get("data", "")
                        parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{data}"}
                        })
                    elif block_type == "tool_use":
                        import json as _json
                        tool_calls.append({
                            "id": block.get("id"),
                            "type": "function",
                            "function": {
                                "name": block.get("name"),
                                "arguments": _json.dumps(block.get("input", {}))
                            }
                        })
                    elif block_type == "tool_result":
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id"),
                            "content": str(block.get("content", ""))
                        })
                    else:
                        parts.append({"type": "text", "text": str(block)})

            if role == "assistant" and tool_calls:
                msg_dict = {"role": "assistant"}
                if parts:
                    msg_dict["content"] = parts
                else:
                    msg_dict["content"] = None
                msg_dict["tool_calls"] = tool_calls
                oai_messages.append(msg_dict)
            else:
                if parts:
                    oai_messages.append({"role": role, "content": parts})
                # Add individual tool result messages (OpenAI requires role="tool")
                oai_messages.extend(tool_results)
        else:
            oai_messages.append({"role": role, "content": str(content)})

    return oai_messages


# ---------------------------------------------------------------------------
# Core: call with fallback
# ---------------------------------------------------------------------------

@observe(as_type="generation")
def create_message(
    *,
    model: str,
    max_tokens: int,
    messages: List[Dict[str, Any]],
    system: Any = None,
    temperature: float = 0.1,
    tools: Optional[List[Dict[str, Any]]] = None,
) -> _NormalisedResponse:
    """Call Anthropic, falling back to OpenAI on quota/rate-limit errors.

    When Ollama override is active, ALL calls route to the local Ollama
    instance regardless of the requested model name.

    Parameters match anthropic.Anthropic().messages.create() so this is a
    near-drop-in replacement.

    Returns a _NormalisedResponse with .content[0].text and .usage fields.
    """
    global _active_provider, _anthropic_disabled
    model = normalize_reasoning_model(model)

    # ------------------------------------------------------------------
    # Ollama routing: if override is active or model is Ollama-bound
    # ------------------------------------------------------------------
    if _is_ollama_model(model):
        return _call_ollama(model=_effective_ollama_model(model),
                            max_tokens=max_tokens, temperature=temperature,
                            system=system, messages=messages, tools=tools)

    # ------------------------------------------------------------------
    # Gemini routing
    # ------------------------------------------------------------------
    if _is_gemini_model(model):
        return _call_gemini(model=model, max_tokens=max_tokens, temperature=temperature,
                            system=system, messages=messages)

    # ------------------------------------------------------------------
    # Direct OpenAI routing: if model is gpt-* or o*, skip Anthropic
    # ------------------------------------------------------------------
    if _is_openai_model(model):
        return _call_openai(model=model, max_tokens=max_tokens, temperature=temperature,
                            system=system, messages=messages, tools=tools)

    # ------------------------------------------------------------------
    # Attempt 1: Anthropic (unless permanently disabled for this session)
    # ------------------------------------------------------------------
    if not _anthropic_disabled and ANTHROPIC_API_KEY:
        try:
            from anthropic import Anthropic

            client = Anthropic(api_key=ANTHROPIC_API_KEY)
            kwargs: Dict[str, Any] = dict(
                model=model,
                max_tokens=max_tokens,
                messages=messages,
            )
            if system:
                kwargs["system"] = system
            if tools:
                kwargs["tools"] = tools

            # Extra headers for prompt caching
            if isinstance(system, list) and any(isinstance(b, dict) and "cache_control" in b for b in system):
                kwargs["extra_headers"] = {"anthropic-beta": "prompt-caching-2024-07-31"}

            resp = client.messages.create(**kwargs)
            _active_provider = "anthropic"

            # Wrap in normalised response
            safe_content = []
            extracted_tool_calls = []
            for b in resp.content:
                if b.type == "text":
                    safe_content.append(_ContentBlock(text=b.text))
                elif b.type == "tool_use":
                    extracted_tool_calls.append(_ToolCall(
                        id=b.id,
                        name=b.name,
                        input=b.input
                    ))

            return _NormalisedResponse(
                content=safe_content,
                usage=_Usage(
                    input_tokens=getattr(resp.usage, "input_tokens", 0),
                    output_tokens=getattr(resp.usage, "output_tokens", 0),
                ),
                model=getattr(resp, "model", model),
                provider="anthropic",
                tool_calls=extracted_tool_calls,
                stop_reason=getattr(resp, "stop_reason", None)
            )

        except Exception as e:
            if _is_fallback_trigger(e):
                print(
                    f"\n  [LLM Fallback] Anthropic error: {e}\n"
                    f"  → Switching to OpenAI ({OPENAI_FALLBACK_MODEL}) for remainder of session.\n",
                    file=sys.stderr,
                )
                _anthropic_disabled = True  # Don't retry Anthropic again this session
            else:
                raise  # Non-quota errors should propagate normally

    # ------------------------------------------------------------------
    # Attempt 2: OpenAI fallback chain
    # ------------------------------------------------------------------
    return _call_openai_fallback_chain(max_tokens=max_tokens, temperature=temperature,
                                       system=system, messages=messages, tools=tools)


def _call_openai_fallback_chain(
    *,
    max_tokens: int,
    temperature: float,
    system: Any,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
) -> _NormalisedResponse:
    """Try configured OpenAI fallback models in order."""
    errors: List[str] = []
    for model in OPENAI_FALLBACK_CHAIN:
        try:
            return _call_openai(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=messages,
                tools=tools,
            )
        except Exception as exc:
            errors.append(f"{model}: {exc}")
            if model != OPENAI_FALLBACK_CHAIN[-1]:
                print(
                    f"\n  [LLM Fallback] OpenAI fallback model failed: {model}\n"
                    f"  â†’ Trying {OPENAI_FALLBACK_CHAIN[OPENAI_FALLBACK_CHAIN.index(model) + 1]}.\n",
                    file=sys.stderr,
                )
    raise RuntimeError(
        "All OpenAI fallback models failed.\n"
        + "\n".join(f"  - {err}" for err in errors)
    )


def _is_reasoning_model(model: str) -> bool:
    """Return True if the model is a reasoning/o-series model that uses max_completion_tokens."""
    m = model.lower()
    # o1, o3, o4-mini, gpt-5.x series all require max_completion_tokens and don't support temperature
    return (
        m.startswith("o1") or m.startswith("o3") or m.startswith("o4")
        or m.startswith("gpt-5")
    )


def _call_openai(
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    system: Any,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
) -> _NormalisedResponse:
    """Call OpenAI directly. Raises RuntimeError if no API key."""
    global _active_provider

    if not OPENAI_API_KEY:
        raise RuntimeError(
            "No OPENAI_API_KEY set in .env. "
            "Add OPENAI_API_KEY to psur-generator/.env to enable OpenAI models."
        )

    try:
        from openai import OpenAI

        oai_client = OpenAI(api_key=OPENAI_API_KEY)
        oai_messages = _translate_messages_for_openai(system, messages)

        # Reasoning models (o-series, gpt-5.x) use max_completion_tokens
        # and do not support the temperature parameter
        kwargs: Dict[str, Any] = dict(
            model=model,
            messages=oai_messages,
        )
        if tools:
            # We would need to translate Anthropic tool format to OpenAI format here
            # For simplicity, we assume callers provide Anthropic tools and we translate
            oai_tools = []
            for t in tools:
                oai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {})
                    }
                })
            kwargs["tools"] = oai_tools
        if _is_reasoning_model(model):
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = temperature

        oai_resp = oai_client.chat.completions.create(**kwargs)

        _active_provider = "openai"
        choice = oai_resp.choices[0]
        usage = oai_resp.usage

        extracted_tool_calls = []
        if choice.message.tool_calls:
            import json as _json
            for tc in choice.message.tool_calls:
                try:
                    args = _json.loads(tc.function.arguments)
                except Exception:
                    args = {}
                extracted_tool_calls.append(_ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=args
                ))

        return _NormalisedResponse(
            content=[_ContentBlock(text=choice.message.content or "")],
            usage=_Usage(
                input_tokens=getattr(usage, "prompt_tokens", 0),
                output_tokens=getattr(usage, "completion_tokens", 0),
            ),
            model=oai_resp.model or model,
            provider="openai",
            tool_calls=extracted_tool_calls,
            stop_reason=choice.finish_reason
        )

    except Exception as oai_err:
        raise RuntimeError(
            f"OpenAI call failed (model={model}).\n"
            f"  Error: {oai_err}"
        ) from oai_err


def _call_gemini(
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    system: Any,
    messages: List[Dict[str, Any]],
) -> _NormalisedResponse:
    """Call Google Gemini for approved Gemini reasoning models."""
    global _active_provider

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_AI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No GEMINI_API_KEY, GOOGLE_AI_API_KEY, or GOOGLE_API_KEY set in .env. "
            "Add one to enable Gemini reasoning models."
        )

    prompt_parts: List[str] = []
    if system:
        if isinstance(system, str):
            prompt_parts.append(f"System:\n{system}")
        else:
            prompt_parts.append(f"System:\n{_flatten_content(system)}")
    for msg in messages:
        role = msg.get("role", "user")
        prompt_parts.append(f"{role.title()}:\n{_flatten_content(msg.get('content', ''))}")
    prompt = "\n\n".join(part for part in prompt_parts if part)

    try:
        try:
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            generation_config = {
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            }
            gemini_model = genai.GenerativeModel(model, generation_config=generation_config)
            resp = gemini_model.generate_content(prompt)
            text = getattr(resp, "text", "") or ""
            usage = getattr(resp, "usage_metadata", None)
            _active_provider = "google"
            return _NormalisedResponse(
                content=[_ContentBlock(text=text)],
                usage=_Usage(
                    input_tokens=getattr(usage, "prompt_token_count", 0) if usage else 0,
                    output_tokens=getattr(usage, "candidates_token_count", 0) if usage else 0,
                ),
                model=model,
                provider="google",
                stop_reason=None,
            )
        except ImportError:
            from google import genai

            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config={
                    "max_output_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            _active_provider = "google"
            return _NormalisedResponse(
                content=[_ContentBlock(text=getattr(resp, "text", "") or "")],
                usage=_Usage(),
                model=model,
                provider="google",
                stop_reason=None,
            )
    except Exception as gemini_err:
        raise RuntimeError(
            f"Gemini call failed (model={model}).\n"
            f"  Error: {gemini_err}"
        ) from gemini_err


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_result":
                    parts.append(str(block.get("content", "")))
                else:
                    parts.append(str(block))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def _strip_thinking_tags(text: str) -> str:
    """Remove <think>…</think> reasoning blocks from model output.

    Many local reasoning models (Qwen3, DeepSeek-R1, etc.) wrap their
    chain-of-thought in <think> tags.  This strips them so downstream
    parsers see only the final answer.
    """
    import re
    # Remove all <think>...</think> blocks (greedy, DOTALL)
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Ollama (local reasoning model via native /api/chat endpoint)
# ---------------------------------------------------------------------------

def _call_ollama(
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    system: Any,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
) -> _NormalisedResponse:
    """Call a local Ollama model via its native /api/chat endpoint.

    Uses the native Ollama REST API instead of the OpenAI-compatible
    endpoint to get control over num_ctx (context window size).  This is
    critical because many reasoning models (Qwen3, DeepSeek-R1) consume
    large amounts of context for thinking tokens, and the default 4096
    is far too small.

    Thinking tags (<think>…</think>) are automatically stripped from
    responses.
    """
    global _active_provider
    import requests as _requests

    base_url = _ollama_url.rstrip("/")
    api_url = f"{base_url}/api/chat"
    print(f"  [Ollama] {model} @ {base_url}  (num_ctx={OLLAMA_NUM_CTX})", file=sys.stderr)

    # Build messages in Ollama's native format
    ollama_messages: List[Dict[str, str]] = []
    if system:
        if isinstance(system, str):
            ollama_messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            text_parts = []
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            ollama_messages.append({"role": "system", "content": "\n\n".join(text_parts)})
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Flatten Anthropic-style content blocks to plain text
            # (Ollama native API doesn't support vision via /api/chat messages
            #  in the same way, but text blocks work fine)
            text_parts = []
            for block in content:
                if isinstance(block, str):
                    text_parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            content = "\n".join(text_parts)
        ollama_messages.append({"role": role, "content": str(content)})

    payload: Dict[str, Any] = {
        "model": model,
        "messages": ollama_messages,
        "stream": False,
        "options": {
            "num_ctx": OLLAMA_NUM_CTX,
            "temperature": temperature,
            # Thinking/reasoning models (Qwen3, DeepSeek-R1) consume
            # many tokens for <think> tags that get stripped.  Multiply
            # the caller's max_tokens budget so the model has room for
            # both reasoning and the actual answer.
            "num_predict": max(max_tokens * 4, 4096),
        },
    }

    try:
        resp = _requests.post(api_url, json=payload, timeout=600)
        resp.raise_for_status()
        data = resp.json()
    except _requests.ConnectionError:
        raise RuntimeError(
            f"Cannot connect to Ollama at {base_url}.\n"
            f"  Make sure Ollama is running: ollama serve"
        )
    except _requests.HTTPError as http_err:
        raise RuntimeError(
            f"Ollama call failed (model={model}, url={api_url}).\n"
            f"  Make sure the model is pulled: ollama pull {model}\n"
            f"  HTTP {resp.status_code}: {resp.text[:300]}"
        ) from http_err
    except Exception as err:
        raise RuntimeError(
            f"Ollama call failed (model={model}, url={api_url}).\n"
            f"  Error: {err}"
        ) from err

    _active_provider = "ollama"

    # Extract response text
    raw_text = data.get("message", {}).get("content", "")
    clean_text = _strip_thinking_tags(raw_text)

    # Extract token usage (Ollama provides these in eval_count / prompt_eval_count)
    input_tokens = data.get("prompt_eval_count", 0) or 0
    output_tokens = data.get("eval_count", 0) or 0

    return _NormalisedResponse(
        content=[_ContentBlock(text=clean_text)],
        usage=_Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
        model=data.get("model", model),
        provider="ollama",
    )


# ---------------------------------------------------------------------------
# Client wrapper (for code that does client.messages.create(...))
# ---------------------------------------------------------------------------

class _MessagesNamespace:
    """Mimics anthropic.Anthropic().messages with a .create() method."""

    def create(self, **kwargs) -> _NormalisedResponse:
        return create_message(**kwargs)


class LLMClient:
    """Drop-in replacement for anthropic.Anthropic with OpenAI fallback.

    Usage:
        client = LLMClient()
        response = client.messages.create(model=..., max_tokens=..., ...)
    """

    def __init__(self):
        self.messages = _MessagesNamespace()


def get_llm_client() -> LLMClient:
    """Return a unified LLM client with automatic fallback."""
    return LLMClient()
