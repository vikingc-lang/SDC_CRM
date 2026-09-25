"""1536-dimensional semantic embeddings for pgvector memory.

``EMBEDDING_PROVIDER``:
* ``hash``        – offline feature hashing with CRM-aware synonym expansion.
                    Deterministic, zero dependencies, good lexical recall.
* ``ollama``      – local embedding model, zero-padded to 1536 dims.
* ``aws_bedrock`` – Amazon Titan text embeddings v1 (native 1536 dims).

Vectors from different providers are not comparable, so if a model provider
fails we store ``None`` (keyword search still finds the record) rather than
mixing vector spaces.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9]+(?:[.'-][a-z0-9]+)*")
_STOP = set(
    "a an the and or but of to in on at for with from by is are was were be been it this that these those we our "
    "they their them he she his her i me my you your us as about into over after before so if not no do does did "
    "has have had will would can could should just also than then there here what when where who which how".split()
)
# Concept groups: any member contributes the shared concept feature, so
# "pricing pushback" and "budget concerns" land close together.
_CONCEPTS = {
    "cost": "price pricing cost costs budget budgets expensive discount discounts spend quote quotes dollars commercial",
    "security": "security infosec soc2 soc compliance gdpr hipaa pen pentest audit risk questionnaire legal",
    "competition": "competitor competitors salesforce hubspot dynamics pipedrive zoho oracle sap incumbent alternative",
    "timeline": "timeline deadline delay delayed slip slipped postpone postponed pushed quarter q1 q2 q3 q4 eoq",
    "champion": "champion sponsor advocate supporter",
    "blocker": "blocker skeptical skeptic pushback objection objections concern concerns concerned resist",
    "demo": "demo demonstration walkthrough poc pilot trial proof evaluation",
    "contract": "contract msa po purchase order signed signature redlines terms agreement renewal",
    "onboarding": "onboarding implementation kickoff rollout deployment golive go-live integration",
    "positive": "great excited love happy impressed strong positive enthusiastic aligned",
    "negative": "angry frustrated unhappy churn cancel disappointed negative worried",
}
_CONCEPT_OF = {w: c for c, words in _CONCEPTS.items() for w in words.split()}


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def hash_embedding(text: str, dim: int | None = None) -> list[float]:
    dim = dim or settings.embedding_dim
    vec = [0.0] * dim
    tokens = [t for t in _WORD.findall((text or "").lower()) if t not in _STOP]

    def add(feature: str, weight: float) -> None:
        h = hashlib.blake2b(feature.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % dim
        sign = 1.0 if h[4] & 1 else -1.0
        vec[idx] += sign * weight

    for i, tok in enumerate(tokens):
        stem = _stem(tok)
        add(f"w:{stem}", 1.0)
        if tok in _CONCEPT_OF:
            add(f"c:{_CONCEPT_OF[tok]}", 1.5)
        if i + 1 < len(tokens):
            add(f"b:{stem}|{_stem(tokens[i + 1])}", 0.5)

    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def _fit(vec: list[float]) -> list[float]:
    dim = settings.embedding_dim
    vec = vec[:dim] + [0.0] * max(0, dim - len(vec))
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


async def embed(text: str) -> list[float] | None:
    provider = settings.embedding_provider
    if provider == "hash":
        return hash_embedding(text)
    try:
        if provider == "ollama":
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                resp = await client.post(
                    f"{settings.ollama_endpoint}/api/embed",
                    json={"model": settings.ollama_embed_model, "input": text},
                )
                resp.raise_for_status()
                return _fit(resp.json()["embeddings"][0])
        if provider == "aws_bedrock":
            return _fit(await asyncio.to_thread(_titan_embed, text))
    except Exception as exc:
        log.warning("Embedding provider '%s' unavailable: %s", provider, exc)
    return None


def _titan_embed(text: str) -> list[float]:
    import boto3

    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    resp = client.invoke_model(modelId=settings.bedrock_embed_model_id, body=json.dumps({"inputText": text[:8000]}))
    return json.loads(resp["body"].read())["embedding"]
