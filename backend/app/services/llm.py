"""Private LLM gateway.

Supported providers (``LLM_PROVIDER``):

* ``ollama``      – fully local inference, JSON-schema constrained output.
* ``aws_bedrock`` – Claude inside your AWS account/VPC (Bedrock Mantle endpoint).
* ``anthropic``   – Claude via the Anthropic API.
* ``heuristic``   – no model; callers use their deterministic fallback.

Every call returns ``None`` when the provider is disabled, unreachable, refuses,
or returns output that fails validation, so callers can always degrade to the
deterministic engine and the CRM keeps working with zero external dependencies.

Every call also goes through the trust layer and metering in services/ai_governance.py: feature switches and
budgets, PII masking (restored in the answer), prompt-injection screening, and a usage record with tokens,
cost and outcome.
"""
from __future__ import annotations

import json
import logging
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.audit import current_user_id
from app.core.config import settings
from app.services import ai_governance as gov

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


async def complete_json(system: str, user: str, schema: type[T], feature: str = "other") -> T | None:
    """Ask the model for a JSON object matching ``schema``; validate strictly."""
    return await _call(feature, system, user, schema=schema)


async def complete_text(system: str, user: str, max_tokens: int = 4000, feature: str = "other") -> str | None:
    """Free-text generation (email drafts, briefs, answers)."""
    return await _call(feature, system, user, max_tokens=max_tokens)


async def _call(feature: str, system: str, user: str, schema: type[T] | None = None, max_tokens: int = 4000):
    """One governed model call: policy and budget checks, PII masking, injection screening, the provider call,
    unmasking and validation, then a metered record (services/ai_governance.py). Any failure returns None."""
    provider = settings.llm_provider
    if provider == "heuristic":
        return None
    model = settings.ollama_model if provider == "ollama" else _claude_model()
    pol = await gov.policy()
    meta = {"feature": feature, "provider": provider, "model": model, "pol": pol}
    if not pol["enabled"] or not pol["feature_enabled"].get(feature, True):
        await gov.record(**meta, status="blocked", detail="AI is switched off for this feature")
        return None
    reason = gov.over_budget(pol, await gov.spend(current_user_id.get()))
    if reason:
        await gov.record(**meta, status="blocked", detail=reason)
        return None
    masking = gov.Masking()
    prompt = masking.mask(user) if pol["mask_pii"] else user
    flags = gov.injection_flags(user)
    if flags and pol["block_injection"]:
        await gov.record(**meta, status="blocked", pii=masking.total, flags=flags, prompt=prompt, detail="Possible prompt injection")
        return None
    system = gov.UNTRUSTED_PREAMBLE + system
    started = time.monotonic()
    tokens_in = tokens_out = 0
    try:
        if provider == "ollama":
            body = {"model": settings.ollama_model, "stream": False, "options": {"temperature": 0 if schema else 0.3},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
            if schema:
                body["format"] = schema.model_json_schema()
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                resp = await client.post(f"{settings.ollama_endpoint}/api/chat", json=body)
                resp.raise_for_status()
            data = resp.json()
            tokens_in, tokens_out = int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0)
            raw, refused = data["message"]["content"], False
        else:
            client = _get_claude_client()
            kwargs = {"model": model, "max_tokens": 16000 if schema else max_tokens, "system": system,
                      "messages": [{"role": "user", "content": prompt}]}
            response = await (client.messages.parse(**kwargs, output_format=schema) if schema else client.messages.create(**kwargs))
            usage = getattr(response, "usage", None)
            tokens_in, tokens_out = int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)
            refused = response.stop_reason == "refusal"
            if schema:
                parsed = None if refused else response.parsed_output
                raw = parsed.model_dump_json() if parsed is not None else None
            else:
                raw = "".join(block.text for block in response.content if block.type == "text")
        latency = int((time.monotonic() - started) * 1000)
        if refused or raw is None:
            log.warning("LLM declined or returned no output (feature=%s)", feature)
            await gov.record(**meta, status="refused", tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency,
                             pii=masking.total, flags=flags, prompt=prompt, detail="Model declined or returned no output")
            return None
        restored = masking.unmask(raw)
        result = schema.model_validate_json(_strip_fences(restored)) if schema else (restored.strip() or None)
        await gov.record(**meta, status="ok", tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency, pii=masking.total,
                         flags=flags, prompt=prompt, response=raw)
        return result
    except (ValidationError, json.JSONDecodeError) as exc:
        log.warning("LLM output failed schema validation: %s", exc)
        await gov.record(**meta, status="error", tokens_in=tokens_in, tokens_out=tokens_out, pii=masking.total, flags=flags,
                         prompt=prompt, detail="Output failed validation")
    except Exception as exc:  # network, auth, provider outage -> deterministic fallback
        log.warning("LLM provider '%s' unavailable: %s", provider, exc)
        await gov.record(**meta, status="error", pii=masking.total, flags=flags, prompt=prompt, detail=f"Provider unavailable: {exc.__class__.__name__}")
    return None


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()
