"""Shared accounting rules for simulated trade-ledger consumers."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


INACTIVE_ACCOUNTING_STATUSES = frozenset({
    "cancelled",
    "rejected",
    "reversed",
    "voided",
})

ACCOUNTING_AUDIT_FIELDS = (
    "accounting_status",
    "accounting_rejected",
    "accounting_rejection_reason",
    "accounting_rejected_at",
    "accounting_correction_id",
    "accounting_corrected_at",
    "voided",
    "void_reason",
    "voided_at",
)


def trade_counts_for_account(trade: Mapping[str, Any]) -> bool:
    """Return whether a preserved raw trade still affects account calculations."""
    status = str(trade.get("accounting_status") or "").strip().lower()
    return not (
        trade.get("accounting_rejected") is True
        or trade.get("voided") is True
        or status in INACTIVE_ACCOUNTING_STATUSES
    )
