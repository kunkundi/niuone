"""Chinese display labels for internal NiuOne strategy enums.

The scorer and persisted state intentionally keep stable English enum values.
Only prompts and user-facing prose should use the labels in this module.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


MAINLINE_STATE_LABELS = {
    "candidate": "酝酿候选阶段",
    "emerging": "启动阶段",
    "mainline": "主线阶段",
    "diverging": "分歧阶段",
    "fading": "退潮阶段",
    "inactive": "失效阶段",
}

STOCK_ROLE_LABELS = {
    "leader": "领涨股",
    "core": "核心股",
    "follower": "跟随股",
    "strong": "强势股",
    "today_leader": "当日领涨股",
    "today_core": "当日核心股",
    "unknown": "未识别角色",
}

MAINLINE_MODE_LABELS = {
    "dual": "双主线",
    "single": "单主线",
    "none": "无主线",
}

_PROSE_LABELS = {
    **MAINLINE_STATE_LABELS,
    **STOCK_ROLE_LABELS,
    **MAINLINE_MODE_LABELS,
}
_PROSE_ENUM_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    + "|".join(re.escape(key) for key in sorted(_PROSE_LABELS, key=len, reverse=True))
    + r")(?![A-Za-z0-9_])"
)
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def _enum_label(value: Any, labels: Mapping[str, str], fallback: str = "-") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return labels.get(text.lower(), text)


def mainline_state_label(value: Any, fallback: str = "-") -> str:
    return _enum_label(value, MAINLINE_STATE_LABELS, fallback)


def stock_role_label(value: Any, fallback: str = "-") -> str:
    return _enum_label(value, STOCK_ROLE_LABELS, fallback)


def mainline_mode_label(value: Any, fallback: str = "无主线") -> str:
    return _enum_label(value, MAINLINE_MODE_LABELS, fallback)


def localize_strategy_text(value: Any) -> str:
    """Translate standalone internal enums while preserving identifiers/acronyms."""

    text = str(value or "")

    def replace_enum(match: re.Match[str]) -> str:
        token = match.group(0)
        if text.strip() == token:
            return _PROSE_LABELS[token]
        left = text[match.start() - 1] if match.start() > 0 else ""
        right = text[match.end()] if match.end() < len(text) else ""
        if _CJK_RE.fullmatch(left) or _CJK_RE.fullmatch(right):
            return _PROSE_LABELS[token]
        return token

    return _PROSE_ENUM_RE.sub(
        replace_enum,
        text,
    )


def localize_decision_display_fields(decision: dict[str, Any]) -> dict[str, Any]:
    """Normalize model prose before it is persisted or rendered to users."""

    if isinstance(decision.get("summary"), str):
        decision["summary"] = localize_strategy_text(decision["summary"])
    actions = decision.get("actions")
    if isinstance(actions, list):
        for action in actions:
            if isinstance(action, dict) and isinstance(action.get("reason"), str):
                action["reason"] = localize_strategy_text(action["reason"])
    refinement = decision.get("buy_refinement")
    if isinstance(refinement, dict):
        for key in ("summary", "reason"):
            if isinstance(refinement.get(key), str):
                refinement[key] = localize_strategy_text(refinement[key])
        dropped = refinement.get("dropped")
        if isinstance(dropped, list):
            for item in dropped:
                if isinstance(item, dict) and isinstance(item.get("reason"), str):
                    item["reason"] = localize_strategy_text(item["reason"])
    return decision
