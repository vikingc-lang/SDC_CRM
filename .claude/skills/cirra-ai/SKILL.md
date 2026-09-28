---
name: cirra-ai
description: Cirra AI layer (Aiden) - private LLM gateway (ollama / aws_bedrock / anthropic / heuristic fallback), Quick-Log extraction and commit, stage-gate triggers, next-best actions, briefings, Ask Aiden Q&A, email drafts, hybrid RAG search (pgvector + full text), embeddings, voice transcription. Use for /ai, /search, services llm.py, ai_extractor.py, insights.py, search.py, embeddings.py, voice.py.
---

# AI / agentic (module 19)

## Files
- `services/llm.py` – `complete_json`, `complete_text`; returns `None` on any provider problem so callers fall
  back to deterministic logic. `LLM_PROVIDER`: ollama | aws_bedrock | anthropic | heuristic.
- `services/ai_extractor.py` – `extract()` raw notes → `QuickLogResponse` (LLM with JSON schema, else
  `heuristic_extract`), `detect_signals`.
- `api/v1/ai.py` – `/ai/quick-log` (extract + match existing records), `/ai/commit-log` (persist the confirmed
  log), `/search/semantic`, `/search/global`, briefing, alerts, ask, drafts.
- `services/insights.py` – stage triggers, next-best actions, pipeline risk scan (job `risk_scan`), briefing,
  `ask`, `draft_email`, `account_brief`, `reindex` (job).
- `services/search.py` – `hybrid_search` (vector + keyword, Reciprocal Rank Fusion, scoped by principal,
  role-hidden custom values excluded), `account_document`.
- `services/embeddings.py` – `hash` (offline) | ollama | aws_bedrock, 1536 dims; never mix vector spaces.
- `services/voice.py` – whisper_asr | faster_whisper | disabled.
- Frontend: `components/QuickLogModal.tsx`, `CopilotPanel.tsx`, `AskAnswer.tsx`, `app/(app)/ask`.

## Rules
- Every AI capability must work offline with the heuristic path; tests run with `LLM_PROVIDER=heuristic`,
  `EMBEDDING_PROVIDER=hash`.
- AI never bypasses scope: retrieval, briefings, drafts and quick-log commit use the caller's principal.
  Quick-log commit: named deal outside scope → 404, suggested match outside scope ignored, contacts matched only
  on the chosen account, create/update only where the role allows (skipped otherwise).
- No data leaves the network unless a cloud provider is explicitly configured.

## Tests
`tests/test_extractor.py`, `test_ops.py` (AI operations), `test_scope_regressions.py`, `test_wave5.py`
(quick-log scope).

## Gotchas
- Not built: autonomous agents, AI actions with approval, an AI trust layer.
