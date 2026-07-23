"""Beijing-time retention boundary for daily market display data."""
from __future__ import annotations

from datetime import datetime, timedelta


MARKET_RETENTION_ROLLOVER_HOUR = 9


def market_retention_date_key(now: datetime) -> str:
    """Return the market-display date retained at ``now``.

    Previous-day closing snapshots remain visible through 08:59:59 Beijing
    time.  At 09:00 the display rolls to the new calendar day and waits for
    that day's first valid sample.
    """

    retained = now
    if now.hour < MARKET_RETENTION_ROLLOVER_HOUR:
        retained -= timedelta(days=1)
    return retained.strftime("%Y-%m-%d")


def seconds_until_next_market_retention_rollover(now: datetime) -> float:
    """Return bounded seconds until the next 09:00 display rollover."""

    next_rollover = now.replace(
        hour=MARKET_RETENTION_ROLLOVER_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )
    if now >= next_rollover:
        next_rollover += timedelta(days=1)
    return max(0.1, (next_rollover - now).total_seconds())


__all__ = [
    "MARKET_RETENTION_ROLLOVER_HOUR",
    "market_retention_date_key",
    "seconds_until_next_market_retention_rollover",
]
