"""Guardrails for Numby (Phase 7, spec sections 43-44).

Hard rules, enforced before any answer is generated:

- No guaranteed-win language: requests for locks/guarantees/sure things are
  refused and reframed with the model's measured accuracy.
- No chasing-losses encouragement: detected and refused with a
  responsible-gambling nudge.
- No betting advice to self-reported minors.
- Responsible-gambling disclosure appended to betting-related answers.
- Numby always identifies itself as an AI assistant.

``check(message)`` returns a refusal text when the message trips a
guardrail, else ``None``. ``needs_disclaimer(message)`` reports whether a
betting-related answer needs the disclosure appended.
"""

from __future__ import annotations

import re
from typing import Optional

IDENTITY_LINE = "I'm Numby, the Money By Numbers AI assistant."

DISCLAIMER = (
    "A quick note: no model can guarantee a winning bet — ours picked "
    "62.16% of winners in backtesting, which doesn't clear the sportsbook "
    "vig on its own. Never bet more than you can afford to lose. If gambling "
    "stops being fun, call or text 1-800-GAMBLER for free, confidential help."
)

_GUARANTEE_PATTERNS = [
    r"\bguarantee(d|s)?\b",
    r"\bsure thing\b",
    r"\bsurefire\b",
    r"\block of the (week|day|century)\b",
    r"\bcan'?t lose\b",
    r"\bcan'?t miss\b",
    r"\b100%\s*(win|winner|accurate|guarantee)",
    r"\bdefinitely win\b",
    r"\bwill definitely\b",
]

_CHASING_PATTERNS = [
    r"\bchase (my |the )?loss",
    r"\bwin (it|my money) back\b",
    r"\bdouble down\b.*\bloss",
    r"\blost .*\$",
    r"\bi'?m down \$",
    r"\bmake up for\b.*\bloss",
]

_MINOR_PATTERNS = [
    r"\bi'?m (1[0-7]|[0-9])\b",
    r"\bi am (1[0-7]|[0-9])\b",
    r"\bunder ?21\b",
    r"\bunder ?18\b",
    r"\bminor\b",
]

_BETTING_PATTERNS = [
    r"\bbet\b",
    r"\bwager\b",
    r"\bparlay\b",
    r"\bshould i (bet|take|pick)\b",
    r"\bbest bet\b",
    r"\bedge\b",
    r"\bodds\b",
    r"\bstake\b",
]


def _matches(patterns, message: str) -> bool:
    lowered = message.lower()
    return any(re.search(p, lowered) for p in patterns)


def check(message: str) -> Optional[str]:
    """Return a refusal response if the message trips a guardrail."""
    if _matches(_MINOR_PATTERNS, message):
        return (
            f"{IDENTITY_LINE} I can't help with betting — sports betting is "
            "only for adults 21+ (18+ in some places), and I don't give "
            "gambling advice to minors. If you're looking for how the model "
            "works, I'm happy to explain that instead."
        )
    if _matches(_CHASING_PATTERNS, message):
        return (
            f"{IDENTITY_LINE} I can't help with that. Chasing losses — betting "
            "more to win back what you lost — is one of the fastest ways "
            "gambling becomes harmful, and I won't encourage it. If gambling "
            "is stressing you out, call or text 1-800-GAMBLER for free, "
            "confidential support."
        )
    if _matches(_GUARANTEE_PATTERNS, message):
        return (
            f"{IDENTITY_LINE} I don't do guarantees — nobody honest can. Our "
            "model picked 62.16% of winners in a 10-year backtest, and even "
            "that doesn't clear the sportsbook's vig by itself. I can show "
            "you the model's actual probabilities and edges so you can judge "
            "the risk yourself."
        )
    return None


def needs_disclaimer(message: str) -> bool:
    """True when the message is betting-related and the answer should carry
    the responsible-gambling disclosure."""
    return _matches(_BETTING_PATTERNS, message)
