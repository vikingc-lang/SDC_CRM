"""Private LLM gateway.

Supported providers (``LLM_PROVIDER``):

* ``ollama``      – fully local inference, JSON-schema constrained output.
* ``aws_bedrock`` – Claude inside your AWS account/VPC (Bedrock Mantle endpoint).
* ``anthropic``   – Claude via the Anthropic API.
* ``heuristic``   – no model; callers use their deterministic fallback.

Every call returns ``None`` when the provider is disabled, unreachable, refuses,
or returns output that fails validation, so callers can always degrade to the
deterministic engine and the CRM keeps working with zero external dependencies.
"""
from __future__ import annotations

import json
import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_claude_client = None


def provider_name() -> str:
    return settings.llm_provider


def _get_claude_client():
    """Lazily build the Claude client for the configured provider."""
    global _claude_client
    if _claude_client is None:
        if settings.llm_provider == "aws_bedrock":
            from anthropic import AsyncAnthropicBedrockMantle

            _claude_client = AsyncAnthropicBedrockMantle(aws_region=settings.aws_region, timeout=settings.llm_timeout_seconds)
        else:
            from anthropic import AsyncAnthropic

            _claude_client = AsyncAnthropic(timeout=settings.llm_timeout_seconds)
    return _claude_client


def _claude_model() -> str:
    return settings.bedrock_model_id if settings.llm_provider == "aws_bedrock" else settings.anthropic_model


async def complete_json(system: str, user: str, schema: type[T]) -> T | None:
    """Ask the model for a JSON object matching ``schema``; validate strictly."""
    provider = settings.llm_provider
    if provider == "heuristic":
        return None
    try:
        if provider == "ollama":
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                resp = await client.post(
                    f"{settings.ollama_endpoint}/api/chat",
                    json={
                        "model": settings.ollama_model,
                        "stream": False,
                        "format": schema.model_json_schema(),
                        "options": {"temperature": 0},
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    },
                )
                resp.raise_for_status()
                return schema.model_validate_json(_strip_fences(resp.json()["message"]["content"]))

        client = _get_claude_client()
        response = await client.messages.parse(
            model=_claude_model(),
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            log.warning("LLM declined or returned no structured output (stop_reason=%s)", response.stop_reason)
            return None
        return response.parsed_output
    except (ValidationError, json.JSONDecodeError) as exc:
        log.warning("LLM output failed schema validation: %s", exc)
    except Exception as exc:  # network, auth, provider outage -> deterministic fallback
        log.warning("LLM provider '%s' unavailable: %s", provider, exc)
    return None


async def complete_text(system: str, user: str, max_tokens: int = 4000) -> str | None:
    """Free-text generation (email drafts, briefs, answers)."""
    provider = settings.llm_provider
    if provider == "heuristic":
        return None
    try:
        if provider == "ollama":
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                resp = await client.post(
                    f"{settings.ollama_endpoint}/api/chat",
                    json={
                        "model": settings.ollama_model,
                        "stream": False,
                        "options": {"temperature": 0.3},
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    },
                )
                resp.raise_for_status()
                return resp.json()["message"]["content"].strip() or None

        client = _get_claude_client()
        response = await client.messages.create(
            model=_claude_model(),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            return None
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        return text or None
    except Exception as exc:
        log.warning("LLM provider '%s' unavailable: %s", provider, exc)
    return None


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()
