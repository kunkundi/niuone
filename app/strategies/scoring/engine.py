"""Pure orchestration for running and comparing registered strategy scorers."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


StrategyScorer = Callable[..., dict[str, Any] | None]


def analyze_enriched_rows(
    rows: list[dict[str, Any]],
    scorers: Mapping[str, StrategyScorer],
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Run scorers against enriched OHLCV rows and choose the best result.

    Legacy scorers keep their one-argument API. Cross-sectional strategies opt
    into the shared scan context by setting ``requires_context = True``.
    """
    strategies: dict[str, dict[str, Any]] = {}
    for strategy_id, scorer in scorers.items():
        # Each scorer may annotate rows, so isolate its shallow mutations.
        scorer_rows = [dict(row) for row in rows]
        if getattr(scorer, "requires_context", False):
            scored = scorer(scorer_rows, context or {})
        else:
            scored = scorer(scorer_rows)
        if scored:
            strategies[strategy_id] = scored

    if not strategies:
        return None

    # Prefer strategies that meet their own entry threshold, then score and
    # finally their registered certainty/priority.
    def best_strategy_key(name: str) -> tuple[int, float, int]:
        item = strategies[name]
        score = float(item.get("score") or 0)
        threshold = float(item.get("entry_threshold") or 8)
        priority = int(item.get("strategy_priority") or 0)
        return (1 if score >= threshold else 0, score, priority)

    best_name = max(strategies, key=best_strategy_key)
    best_score = strategies[best_name]["score"]
    best_verdict = strategies[best_name]["verdict"]
    best_decision_score = strategies[best_name].get("decision_score", best_score)

    consensus_count = sum(1 for strategy in strategies.values() if strategy["score"] >= 7)
    consensus_boost = 1 if consensus_count >= 3 else (0.5 if consensus_count >= 2 else 0)

    return {
        "best_strategy": best_name,
        "best_score": best_score,
        "best_decision_score": best_decision_score,
        "best_verdict": best_verdict,
        "strategies": strategies,
        "consensus_count": consensus_count,
        "consensus_boost": consensus_boost,
    }
