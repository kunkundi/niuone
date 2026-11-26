"""Current limit-up pool statistics used by the simulator's existing hints."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from core.process_lease import FileLease


def read_current_market_sentiment(cache_path: Path) -> dict[str, Any]:
    """Terminate a stalled legacy client and release the cross-process refresh lease."""
    lease = FileLease(cache_path.with_suffix('.fetch.lock'), stale_after_seconds=30)
    if not lease.acquire():
        raise RuntimeError('market sentiment refresh already running')
    try:
        worker = Path(__file__).resolve().parents[1] / 'entrypoints/market_sentiment_worker.py'
        try:
            result = subprocess.run(
                [sys.executable, str(worker)], capture_output=True, text=True,
                encoding='utf-8', timeout=20, check=False,
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError('market sentiment refresh exceeded 20s') from exc
        if result.returncode:
            raise RuntimeError(f'market sentiment worker exited ({result.returncode})')
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError('invalid market sentiment response')
        return value
    finally:
        lease.release()


def fetch_market_sentiment() -> dict[str, Any]:
    import akshare as ak

    frame = ak.stock_zt_pool_em(date=datetime.now().strftime('%Y%m%d'))
    count = len(frame) if frame is not None else 0
    if count >= 80:
        sentiment, detail = 'hot', f'涨停{count}家→市场🔥活跃，可积极建仓'
    elif count >= 30:
        sentiment, detail = 'neutral', f'涨停{count}家→市场正常，正常建仓'
    else:
        sentiment, detail = 'cold', f'涨停{count}家→市场🥶冷清，谨慎建仓'
    sectors: Counter[str] = Counter()
    if frame is not None and not frame.empty:
        for _, row in frame.iterrows():
            sector = str(row.get('所属行业', '')).strip()
            if sector and sector != 'nan':
                sectors[sector] += 1
    return {'limit_up_count': count, 'sentiment': sentiment, 'detail': detail,
            'hot_sectors': [f'{name}({n}只)' for name, n in sectors.most_common(5)]}


if __name__ == '__main__':
    print(json.dumps(fetch_market_sentiment(), ensure_ascii=False))
