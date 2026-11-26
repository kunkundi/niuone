"""A-share fee assumptions shared by paper trading and historical replay."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping


# The configured paper account is “万一免五”: 0.01% commission without a
# five-yuan minimum. Statutory sell-side stamp duty and bilateral transfer fee
# remain separate so callers can expose the exact assumption to users.
A_SHARE_COMMISSION_RATE = 0.0001
A_SHARE_MINIMUM_COMMISSION = 0.0
A_SHARE_SELL_STAMP_DUTY_RATE = 0.0005
A_SHARE_TRANSFER_FEE_RATE = 0.00001


@dataclass(frozen=True)
class FeeSchedule:
    """Immutable account assumption; never inferred from the current environment."""

    commission_rate: float = A_SHARE_COMMISSION_RATE
    commission_min: float = A_SHARE_MINIMUM_COMMISSION
    stamp_duty_sell_rate: float = A_SHARE_SELL_STAMP_DUTY_RATE
    transfer_fee_rate: float = A_SHARE_TRANSFER_FEE_RATE

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"invalid account fee: {name}")
        if self.commission_rate > 0.003 or self.commission_min > 100:
            raise ValueError("account commission outside supported range")
        if self.stamp_duty_sell_rate > 0.01 or self.transfer_fee_rate > 0.01:
            raise ValueError("account tax/transfer rate outside supported range")

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


LEGACY_FEE_SCHEDULE = FeeSchedule()
RETAIL_FEE_SCHEDULE = FeeSchedule(commission_rate=0.00025, commission_min=5.0)
FEE_PROFILE_ENV = "DASHBOARD_PRACTICE_FEE_PROFILE"


def fee_schedule_for_profile(profile: str) -> FeeSchedule:
    profiles = {"legacy": LEGACY_FEE_SCHEDULE, "retail_025_min5": RETAIL_FEE_SCHEDULE}
    name = str(profile or "legacy").strip()
    if name not in profiles:
        raise ValueError("手续费方案必须是 legacy 或 retail_025_min5")
    return profiles[name]


def account_fee_schedule(state: Mapping[str, Any] | None) -> FeeSchedule:
    """Old accounts retain legacy fees even when new-account defaults change."""
    raw = (state or {}).get("fee_schedule")
    if raw is None:
        return LEGACY_FEE_SCHEDULE
    if not isinstance(raw, Mapping) or set(raw) != set(LEGACY_FEE_SCHEDULE.to_dict()):
        raise ValueError("invalid frozen account fee schedule")
    return FeeSchedule(**{name: float(value) for name, value in raw.items()})


def calculate_a_share_trade_fees(
    amount: Any, side: str, schedule: FeeSchedule = LEGACY_FEE_SCHEDULE,
) -> dict[str, float]:
    """Return rounded paper-account fees for one A-share trade."""
    gross = max(0.0, float(amount or 0.0))
    commission = max(
        gross * schedule.commission_rate,
        schedule.commission_min if gross > 0 else 0.0,
    )
    transfer_fee = gross * schedule.transfer_fee_rate
    stamp_duty = (
        gross * schedule.stamp_duty_sell_rate
        if str(side or "").upper() == "SELL" else 0.0
    )
    return {
        "commission": round(commission, 2),
        "transfer_fee": round(transfer_fee, 2),
        "stamp_duty": round(stamp_duty, 2),
        "total_fee": round(commission + transfer_fee + stamp_duty, 2),
    }


__all__ = [
    "A_SHARE_COMMISSION_RATE",
    "A_SHARE_MINIMUM_COMMISSION",
    "A_SHARE_SELL_STAMP_DUTY_RATE",
    "A_SHARE_TRANSFER_FEE_RATE",
    "calculate_a_share_trade_fees",
    "FeeSchedule", "LEGACY_FEE_SCHEDULE", "RETAIL_FEE_SCHEDULE",
    "FEE_PROFILE_ENV", "fee_schedule_for_profile", "account_fee_schedule",
]
