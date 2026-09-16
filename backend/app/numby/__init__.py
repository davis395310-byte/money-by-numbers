"""Numby — the Money By Numbers AI assistant (Phase 7, spec sections 41-45).

Submodules:

- ``retrieval`` — grounding layer: pulls only real records (predictions,
  odds snapshots, injuries, weather, track record, methodology) into a
  structured context bundle. Every fact carries its source.
- ``guardrails`` — refusal rules and responsible-gambling disclosures.
- ``providers`` — LLM provider interface (``AIProvider``); OpenAI-compatible
  chat provider plus an honest not-configured fallback.
- ``memory`` — per-user conversation persistence (``conversations``
  collection) with sane caps.
- ``responder`` — the answer pipeline: guardrails -> retrieval ->
  (LLM if configured, else deterministic grounded templates).

ABSOLUTE RULE: Numby never fabricates a prediction, odd, injury, or stat.
If the data is not in the context bundle, the answer says so.
"""

from . import guardrails, memory, providers, responder, retrieval  # noqa: F401

__all__ = ["guardrails", "memory", "providers", "responder", "retrieval"]
