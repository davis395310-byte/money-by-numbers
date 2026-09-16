"""Numby answer pipeline (Phase 7).

``respond(db, message, history)``:

1. Guardrails first — refusals for guarantees, chasing losses, minors.
2. Retrieval — grounded fact bundle from real platform data only.
3. Answer — if an LLM provider is configured, it answers from the bundle
   under a strict grounding system prompt; otherwise deterministic
   templates render the bundle's facts directly.
4. Responsible-gambling disclosure appended to betting-related answers.

The deterministic path is the default (no AI key in dev/test) and is what
the test suite exercises for grounding and no-fabrication guarantees.
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import guardrails
from .providers import get_provider
from .retrieval import ContextBundle, gather_context

_SYSTEM_PROMPT = """You are Numby, the AI assistant of Money By Numbers, an NFL analytics platform.
You answer questions about NFL games, model predictions, odds, injuries, weather, and methodology.

STRICT GROUNDING RULES — you must follow these exactly:
1. The CONTEXT section contains the ONLY facts you may state. Every number, team, score, probability, and record in your answer must come from CONTEXT.
2. If the answer is not in CONTEXT, say "I don't have that data" and suggest what the user could ask instead. NEVER invent a statistic, line, prediction, injury, or score.
3. Never claim a guaranteed win, lock, or sure thing. Never encourage chasing losses or betting more than one can afford.
4. Identify yourself as Numby, an AI assistant, when asked who you are.
5. Keep answers concise and conversational. Cite the source names given in CONTEXT when stating facts.
6. Do not give financial advice. You may present model probabilities and measured results; the user decides what to do with them.
"""


def _serialize_context(bundle: ContextBundle) -> str:
    lines = ["CONTEXT (facts you may use; each line ends with its source):"]
    if not bundle.facts:
        lines.append("(no facts available)")
    for fact in bundle.facts:
        lines.append(f"- [{fact.kind} | source: {fact.source}] {fact.text}")
    if bundle.notes:
        lines.append("AVAILABILITY NOTES (explain these honestly to the user):")
        for note in bundle.notes:
            lines.append(f"- {note}")
    return "\n".join(lines)


def _deterministic_answer(bundle: ContextBundle, message: str) -> str:
    """Render the bundle's facts with templates. Never invents content."""
    parts: List[str] = [guardrails.IDENTITY_LINE]
    if bundle.facts:
        parts.append("Here's what I found in our data:")
        for fact in bundle.facts:
            parts.append(f"• {fact.text}")
    else:
        parts.append(
            "I don't have the data to answer that from our records."
        )
    for note in bundle.notes:
        parts.append(f"Note: {note}")
    parts.append(
        "Ask me about a specific matchup (e.g. 'Chiefs vs Broncos prediction'), "
        "this week's edges, injuries or weather for a game, our track record, "
        "or how the model works."
    )
    return "\n".join(parts)


def respond(
    db: Any, message: str, history: List[Dict[str, str]] | None = None
) -> Dict[str, Any]:
    """Run the full pipeline and return the reply payload."""
    history = history or []

    # 1. Guardrails.
    refusal = guardrails.check(message)
    if refusal is not None:
        return {
            "reply": refusal,
            "sources": [],
            "ai_mode": "guardrail",
            "disclaimer": None,
        }

    # 2. Retrieval (grounded facts only).
    bundle = gather_context(db, message)

    # 3. Answer.
    provider = get_provider()
    ai_mode = "basic"
    if provider.name != "not_configured":
        ai_mode = "llm"
        try:
            reply = provider.complete(
                _SYSTEM_PROMPT + "\n" + _serialize_context(bundle),
                history + [{"role": "user", "content": message}],
            ).strip()
            if not reply:
                raise RuntimeError("empty LLM reply")
        except Exception:
            # An LLM failure must never produce an ungrounded answer or a
            # crash: fall back to the deterministic grounded templates.
            reply = _deterministic_answer(bundle, message)
            ai_mode = "basic_fallback"
    else:
        reply = _deterministic_answer(bundle, message)

    # 4. Disclaimer on betting-related answers.
    disclaimer = guardrails.DISCLAIMER if guardrails.needs_disclaimer(message) else None

    sources = sorted({f"{f.kind}:{f.source}" for f in bundle.facts})
    return {
        "reply": reply,
        "sources": sources,
        "ai_mode": ai_mode,
        "disclaimer": disclaimer,
    }
