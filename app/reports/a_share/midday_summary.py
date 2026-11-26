#!/usr/bin/env python3
"""A-share midday / post-close deterministic summary cron script.

Generates a deterministic market report and mirrors it to the dashboard.
Mode is selected from filename:
- contains 'midday' -> 午盘总结
- contains 'close' or 'post' -> 盘后总结
"""
from __future__ import annotations

import contextlib
import datetime as dt
import html
import json
import math
import os
import re
import sys
import time
from http.client import RemoteDisconnected
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from niuone_paths import get_dashboard_home

if __package__ == "app":
    from .reports.a_share import common as report_common
    from .reports.a_share.tencent_spot import fetch_tencent_spot_snapshot
else:
    from reports.a_share import common as report_common
    from reports.a_share.tencent_spot import fetch_tencent_spot_snapshot

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

try:
    import akshare as ak  # type: ignore
except Exception as e:
    print(f"牛牛大王，A股盘中/盘后总结生成失败：本机 akshare 不可用：{e}")
    sys.exit(1)

CN_TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime.now(CN_TZ)
DASHBOARD_HOME = Path(os.environ.get("DASHBOARD_HOME") or get_dashboard_home(Path.cwd())).expanduser()
SCRIPT_NAME = Path(sys.argv[0]).name.lower()
MODE = "midday" if "midday" in SCRIPT_NAME or "noon" in SCRIPT_NAME else "close"
TITLE = "午盘总结" if MODE == "midday" else "盘后总结"


class SpotSnapshotUnavailable(RuntimeError):
    """Raised when a scheduled summary cannot obtain a complete spot snapshot."""


def is_trading_day_guess(day: dt.date) -> bool:
    return report_common.is_trading_day_guess(day)


def safe_float(value: Any, default: float = 0.0) -> float:
    return report_common.safe_float(value, default)


def safe_int(value: Any, default: int = 0) -> int:
    return report_common.safe_int(value, default, safe_number=safe_float)


def fmt_amt_yuan(value: float | int | None) -> str:
    return report_common.fmt_amt_yuan(value)


def parse_money_to_yuan(value: Any) -> float:
    return report_common.parse_money_to_yuan(value, safe_number=safe_float)


def time_limit(seconds: int):
    return report_common.time_limit(seconds)


def first_col(frame, names: list[str]) -> str | None:
    return report_common.first_col(frame, names)


def normalize_code(code: Any) -> str:
    return report_common.normalize_code(code)


def is_full_market_a_share(code: str, name: str) -> bool:
    return report_common.is_full_market_a_share(code, name, code_normalizer=normalize_code)


def normalize_industry_name(name: str) -> str:
    return report_common.normalize_industry_name(name)


def quiet_call(fn, *args, **kwargs):
    from reports.a_share.provider_worker import fetch_frame
    return fetch_frame(fn.__name__, *args, **kwargs)


def extract_eastmoney_spot_rows(diff: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in diff or []:
        code = normalize_code(item.get("f12"))
        name = str(item.get("f14") or "").strip()
        if not is_full_market_a_share(code, name):
            continue
        industry = normalize_industry_name(str(item.get("f100") or "")) or "所属方向待复核"
        if industry.lower() == "nan":
            industry = "所属方向待复核"
        rows.append({
            "code": code,
            "name": name,
            "pct": safe_float(item.get("f3")),
            "price": safe_float(item.get("f2")),
            "amount": safe_float(item.get("f6")),
            "vol_ratio": safe_float(item.get("f10")),
            "industry": industry,
            "quote_ts": safe_int(item.get("f124")),
        })
    return rows


def eastmoney_spot_endpoints() -> list[str]:
    configured = os.getenv(
        "A_SHARE_SUMMARY_DIRECT_ENDPOINTS",
        "https://push2.eastmoney.com/api/qt/clist/get,"
        "https://push2delay.eastmoney.com/api/qt/clist/get,"
        "https://82.push2.eastmoney.com/api/qt/clist/get",
    )
    return [item.strip().rstrip("?") for item in configured.split(",") if item.strip()] or [
        "https://push2.eastmoney.com/api/qt/clist/get"
    ]


def fetch_eastmoney_spot_direct() -> tuple[list[dict[str, Any]], str | None]:
    """Fetch a full Eastmoney snapshot with page retries and endpoint failover."""
    fields = "f12,f14,f2,f3,f6,f10,f100,f124"
    page_size = 100
    max_pages = safe_int(os.getenv("A_SHARE_SUMMARY_DIRECT_MAX_PAGES", "70"), 70)
    deadline = time.monotonic() + safe_int(os.getenv("A_SHARE_SUMMARY_DIRECT_DEADLINE", "20"), 20)
    workers = max(1, min(12, safe_int(os.getenv("A_SHARE_SUMMARY_DIRECT_WORKERS", "8"), 8)))
    attempts = max(1, min(5, safe_int(os.getenv("A_SHARE_SUMMARY_DIRECT_RETRIES", "3"), 3)))
    endpoints = eastmoney_spot_endpoints()
    all_items: list[dict[str, Any]] = []
    page_errors: list[str] = []

    def is_retryable_error(exc: Exception) -> bool:
        if isinstance(exc, HTTPError):
            return exc.code in {403, 408, 429, 500, 502, 503, 504}
        return isinstance(exc, (RemoteDisconnected, URLError, TimeoutError, OSError, json.JSONDecodeError))

    def fetch_page_once(page: int, endpoint: str) -> tuple[int, int, list[dict[str, Any]]]:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            raise TimeoutError("东财直连总时限已用尽")
        params = {
            "pn": str(page),
            "pz": str(page_size),
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": fields,
        }
        url = endpoint + "?" + urlencode(params)
        req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
        with urlopen(req, timeout=min(5, max(1, remaining))) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
        data = ((payload or {}).get("data") or {})
        return safe_int(data.get("total"), 0), page, data.get("diff") or []

    def fetch_page(page: int, preferred_endpoint: str | None = None) -> tuple[int, int, list[dict[str, Any]], str]:
        ordered = ([preferred_endpoint] if preferred_endpoint else []) + [
            endpoint for endpoint in endpoints if endpoint != preferred_endpoint
        ]
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            endpoint = ordered[(attempt - 1) % len(ordered)]
            try:
                total, fetched_page, diff = fetch_page_once(page, endpoint)
                if not diff:
                    raise RemoteDisconnected("empty Eastmoney page")
                return total, fetched_page, diff, endpoint
            except Exception as exc:
                last_exc = exc
                if attempt < attempts and is_retryable_error(exc) and (deadline - time.monotonic()) > 1.5:
                    time.sleep(min(0.25 * attempt, max(0.0, deadline - time.monotonic() - 1)))
                    continue
                raise
        raise last_exc or RuntimeError("unknown Eastmoney page error")

    total, _, first_items, preferred_endpoint = fetch_page(1)
    all_items.extend(first_items)
    total_pages = min(max_pages, max(1, math.ceil((total or len(first_items)) / page_size)))
    if total_pages > 1 and time.monotonic() < deadline:
        page_items: dict[int, list[dict[str, Any]]] = {}
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = {
                pool.submit(fetch_page, page, preferred_endpoint): page
                for page in range(2, total_pages + 1)
            }
            try:
                for future in as_completed(futures, timeout=max(1, deadline - time.monotonic())):
                    try:
                        _total, page, diff, _endpoint = future.result()
                    except Exception as e:
                        page_errors.append(f"{futures[future]}页{type(e).__name__}")
                        continue
                    if _total:
                        total = _total
                    if diff:
                        page_items[page] = diff
            except FuturesTimeoutError:
                page_errors.append("分页超时")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        for page in sorted(page_items):
            all_items.extend(page_items[page])
    rows = extract_eastmoney_spot_rows(all_items)
    warnings = []
    if preferred_endpoint != endpoints[0]:
        warnings.append("东财直连已切换备用域名")
    if total and len(all_items) < total:
        warnings.append(f"东财直连只取到 {len(all_items)}/{total} 只，已按现有样本生成")
    elif page_errors:
        warnings.append("东财直连部分分页失败：" + "、".join(page_errors[:3]))
    return rows, "；".join(warnings) or None


def fetch_eastmoney_spot_direct_single_page():
    """Deprecated single-page shape kept only as a last-ditch fallback."""
    fields = "f12,f14,f2,f3,f6,f10,f100,f124"
    params = {
        "pn": "1",
        "pz": "100",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "fields": fields,
    }
    last_err: Exception | None = None
    for endpoint in eastmoney_spot_endpoints():
        try:
            url = endpoint + "?" + urlencode(params)
            req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
            with urlopen(req, timeout=safe_int(os.getenv("A_SHARE_SUMMARY_DIRECT_TIMEOUT", "20"), 20)) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
            diff = (((payload or {}).get("data") or {}).get("diff") or [])
            rows = extract_eastmoney_spot_rows(diff)
            if rows:
                return rows
        except Exception as exc:
            last_err = exc
    if last_err:
        raise last_err
    return []


def fetch_spot():
    errors: list[str] = []
    try:
        rows, direct_warning = fetch_eastmoney_spot_direct()
        if rows:
            issue = spot_snapshot_issue(rows, direct_warning)
            if not issue:
                return rows, direct_warning
            errors.append(f"东财分页直连不完整: {issue}")
        else:
            errors.append("东财分页直连返回空")
    except Exception as e:
        errors.append(f"东财分页直连: {type(e).__name__}: {e}")

    try:
        rows, tencent_warning = fetch_tencent_spot_snapshot(DASHBOARD_HOME)
        if rows:
            issue = spot_snapshot_issue(rows, tencent_warning)
            if not issue:
                msg = "东财现货接口失败，已切换腾讯全市场行情：" + "；".join(errors[-2:])
                if tencent_warning:
                    msg += f"；{tencent_warning}"
                return rows, msg
            errors.append(f"腾讯全市场行情不完整: {issue}")
        else:
            errors.append(tencent_warning or "腾讯全市场行情返回空")
    except Exception as e:
        errors.append(f"腾讯全市场行情: {type(e).__name__}: {e}")

    try:
        df = quiet_call(ak.stock_zh_a_spot_em)
        if df is not None and len(df):
            issue = spot_snapshot_issue(extract_market(df), None)
            if not issue:
                return df, "直连行情暂不可用，已切换 AkShare 东财现货"
            errors.append(f"stock_zh_a_spot_em: {issue}")
        else:
            errors.append("stock_zh_a_spot_em: returned empty")
    except Exception as e:
        errors.append(f"stock_zh_a_spot_em: {type(e).__name__}: {e}")

    try:
        with time_limit(safe_int(os.getenv("A_SHARE_SUMMARY_SINA_TIMEOUT", "10"), 10)):
            df = quiet_call(ak.stock_zh_a_spot)
        if df is not None and len(df):
            issue = spot_snapshot_issue(extract_market(df), None)
            if not issue:
                return df, "东财与腾讯现货接口失败，已切换新浪现货：" + "；".join(errors[-3:])
            errors.append(f"stock_zh_a_spot: {issue}")
        else:
            errors.append("stock_zh_a_spot: returned empty")
    except Exception as e:
        errors.append(f"stock_zh_a_spot: {type(e).__name__}: {e}")
    return [], "现货主接口暂不可用：" + "；".join(errors[-5:])


def fetch_industry_fund_flow():
    if os.getenv("A_SHARE_SUMMARY_FETCH_FUNDS", "1") != "1":
        return None, "行业资金流接口按配置跳过"
    try:
        with time_limit(safe_int(os.getenv("A_SHARE_SUMMARY_FUND_TIMEOUT", "10"), 10)):
            df = quiet_call(ak.stock_fund_flow_industry, symbol="即时")
        if df is None or len(df) == 0:
            return None, "行业资金流返回空"
        return df, None
    except Exception as e:
        return None, f"行业资金流：{type(e).__name__}: {e}"


def fetch_limit_pools():
    """Fetch currently sealed limit-up and limit-down pools for the report date."""

    date = NOW.strftime("%Y%m%d")
    timeout = safe_int(os.getenv("A_SHARE_SUMMARY_LIMIT_POOL_TIMEOUT", "8"), 8)
    pools: list[Any | None] = []
    errors: list[str] = []
    for label, fn in (("封死涨停池", ak.stock_zt_pool_em), ("封死跌停池", ak.stock_zt_pool_dtgc_em)):
        try:
            with time_limit(timeout):
                frame = quiet_call(fn, date=date)
            if frame is None:
                raise RuntimeError("返回空对象")
            pools.append(frame)
        except Exception as e:
            pools.append(None)
            errors.append(f"{label}：{type(e).__name__}: {e}")
    return pools[0], pools[1], "；".join(errors) or None


def sealed_limit_count(pool) -> int | None:
    """Count unique stocks in an Eastmoney pool; ``None`` means unavailable."""

    if pool is None:
        return None
    if len(pool) == 0:
        return 0
    code_col = first_col(pool, ["代码", "股票代码", "code"])
    if code_col:
        codes = {normalize_code(value) for value in pool[code_col] if normalize_code(value)}
        if codes:
            return len(codes)
    return len(pool)


def extract_market(spot):
    if isinstance(spot, list):
        return spot
    code_col = first_col(spot, ["代码", "股票代码", "symbol", "code"])
    name_col = first_col(spot, ["名称", "股票简称", "name"])
    pct_col = first_col(spot, ["涨跌幅", "涨幅"])
    price_col = first_col(spot, ["最新价", "价格"])
    amt_col = first_col(spot, ["成交额", "成交金额"])
    vol_ratio_col = first_col(spot, ["量比"])
    industry_col = first_col(spot, ["行业", "所属行业", "板块"])
    rows = []
    if not (code_col and name_col and pct_col):
        return rows
    for _, r in spot.iterrows():
        code = normalize_code(r.get(code_col))
        name = str(r.get(name_col) or "").strip()
        if not is_full_market_a_share(code, name):
            continue
        pct = safe_float(r.get(pct_col))
        price = safe_float(r.get(price_col)) if price_col else 0.0
        amount = safe_float(r.get(amt_col)) if amt_col else 0.0
        vr = safe_float(r.get(vol_ratio_col)) if vol_ratio_col else 0.0
        ind = normalize_industry_name(str(r.get(industry_col) or "")) if industry_col else "所属方向待复核"
        if not ind or ind.lower() == "nan":
            ind = "所属方向待复核"
        rows.append({"code": code, "name": name, "pct": pct, "price": price, "amount": amount, "vol_ratio": vr, "industry": ind})
    return rows


def spot_snapshot_issue(rows: list[dict[str, Any]], spot_err: str | None) -> str | None:
    """Return a retry-worthy completeness or freshness issue."""
    if not rows:
        return spot_err or "现货行情返回数据缺少有效A股样本"
    min_rows = max(1, safe_int(os.getenv("A_SHARE_SUMMARY_SPOT_MIN_ROWS", "4000"), 4000))
    unique_codes = {normalize_code(row.get("code")) for row in rows if normalize_code(row.get("code"))}
    if len(unique_codes) < min_rows:
        return f"唯一有效A股样本仅 {len(unique_codes)} 只，低于完整性下限 {min_rows} 只"
    default_bse_min = 100 if min_rows >= 4000 else 0
    min_bse_rows = max(
        0,
        safe_int(os.getenv("A_SHARE_SUMMARY_SPOT_MIN_BSE_ROWS", str(default_bse_min)), default_bse_min),
    )
    bse_codes = {code for code in unique_codes if report_common.is_bse_a_share(code)}
    if len(bse_codes) < min_bse_rows:
        return f"北交所A股样本仅 {len(bse_codes)} 只，低于完整性下限 {min_bse_rows} 只"
    if len(unique_codes) < len(rows) * 0.98:
        return f"现货行情重复代码过多：{len(unique_codes)}/{len(rows)}"
    partial_markers = ("只取到", "单页样本", "部分分页失败", "分页超时")
    if spot_err and any(marker in spot_err for marker in partial_markers):
        return spot_err

    price_coverage = sum(1 for row in rows if safe_float(row.get("price")) > 0) / len(rows)
    amount_coverage = sum(1 for row in rows if safe_float(row.get("amount")) > 0) / len(rows)
    movement_coverage = sum(1 for row in rows if abs(safe_float(row.get("pct"))) > 1e-8) / len(rows)
    if price_coverage < 0.80:
        return f"现货最新价有效覆盖率仅 {price_coverage:.1%}"
    if amount_coverage < 0.70:
        return f"现货成交额有效覆盖率仅 {amount_coverage:.1%}"
    if movement_coverage < 0.10:
        return f"现货涨跌幅非零覆盖率仅 {movement_coverage:.1%}"

    stamped_rows = [
        safe_int(row.get("quote_ts"))
        for row in rows
        if safe_float(row.get("amount")) > 0 and safe_int(row.get("quote_ts")) > 0
    ]
    if len(stamped_rows) >= min_rows * 0.5:
        fresh_count = sum(
            1 for timestamp in stamped_rows
            if dt.datetime.fromtimestamp(timestamp, CN_TZ).date() == NOW.date()
        )
        if fresh_count / len(stamped_rows) < 0.80:
            return f"现货快照当日时间戳覆盖率仅 {fresh_count / len(stamped_rows):.1%}"
        max_age_minutes = max(5, safe_int(os.getenv("A_SHARE_MIDDAY_MAX_QUOTE_AGE_MINUTES", "20"), 20))
        cutoff = (NOW - dt.timedelta(minutes=max_age_minutes)).timestamp()
        current_count = sum(1 for timestamp in stamped_rows if timestamp >= cutoff)
        if current_count / len(stamped_rows) < 0.50:
            return f"现货快照最近 {max_age_minutes} 分钟时间戳覆盖率仅 {current_count / len(stamped_rows):.1%}"
    return None


def extract_funds(fund_df):
    out = []
    if fund_df is None or len(fund_df) == 0:
        return out
    ind_col = first_col(fund_df, ["行业"])
    pct_col = first_col(fund_df, ["行业-涨跌幅", "涨跌幅", "阶段涨跌幅"])
    inflow_col = first_col(fund_df, ["流入资金"])
    outflow_col = first_col(fund_df, ["流出资金"])
    net_col = first_col(fund_df, ["净额", "资金流入净额"])
    count_col = first_col(fund_df, ["公司家数"])
    lead_col = first_col(fund_df, ["领涨股"])
    lead_pct_col = first_col(fund_df, ["领涨股-涨跌幅"])
    if not ind_col:
        return out
    for _, r in fund_df.iterrows():
        ind = normalize_industry_name(str(r.get(ind_col) or ""))
        if not ind or ind.lower() == "nan":
            continue
        inflow_raw = r.get(inflow_col) if inflow_col else 0.0
        outflow_raw = r.get(outflow_col) if outflow_col else 0.0
        net_raw = r.get(net_col) if net_col else None
        inflow = safe_float(inflow_raw) * 1e8 if isinstance(inflow_raw, (int, float)) else parse_money_to_yuan(inflow_raw)
        outflow = safe_float(outflow_raw) * 1e8 if isinstance(outflow_raw, (int, float)) else parse_money_to_yuan(outflow_raw)
        net = (safe_float(net_raw) * 1e8 if isinstance(net_raw, (int, float)) else parse_money_to_yuan(net_raw)) if net_col else inflow - outflow
        pct = safe_float(r.get(pct_col)) if pct_col else 0.0
        count = safe_int(r.get(count_col)) if count_col else 0
        lead = str(r.get(lead_col) or "").strip() if lead_col else ""
        lead_pct = safe_float(r.get(lead_pct_col)) if lead_pct_col else 0.0
        heat = pct * 2 + (net / 1e8) * 0.35 + min(count, 150) / 100
        out.append({"industry": ind, "pct": pct, "inflow": inflow, "outflow": outflow, "net": net, "count": count, "lead": "" if lead.lower() == "nan" else lead, "lead_pct": lead_pct, "heat": heat})
    return out


def write_report_pdf(text: str) -> Path | None:
    script_dir = Path(__file__).resolve().parent
    dashboard_home = get_dashboard_home(script_dir.parent)
    out_dir = dashboard_home / "cron" / "attachments" / "a_share_intraday_summary"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = NOW.strftime(f"%Y-%m-%d_%H-%M_A股{TITLE}")
    pdf_path = out_dir / f"{stem}.pdf"
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

        font_candidates = [
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/PingFang.ttc",
        ]
        font_name = "Helvetica"
        for fp in font_candidates:
            if Path(fp).exists():
                try:
                    pdfmetrics.registerFont(TTFont("CNFont", fp, subfontIndex=0))
                    font_name = "CNFont"
                    break
                except Exception:
                    continue
        styles = getSampleStyleSheet()
        normal = ParagraphStyle("CNNormal", parent=styles["Normal"], fontName=font_name, fontSize=10.5, leading=16, spaceAfter=3, textColor=colors.HexColor("#111111"), wordWrap="CJK")
        heading = ParagraphStyle("CNHeading", parent=normal, fontSize=14, leading=20, spaceBefore=8, spaceAfter=5)
        title_style = ParagraphStyle("CNTitle", parent=normal, fontSize=18, leading=24, spaceAfter=12, alignment=1)
        bullet = ParagraphStyle("CNBullet", parent=normal, leftIndent=10, firstLineIndent=-10)
        def inline_markup(s: str) -> str:
            s = html.escape(s)
            s = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", s)
            s = re.sub(r"`([^`]+)`", r"<font backColor='#f2f3f5'>\1</font>", s)
            return s
        story = [Paragraph(html.escape(f"A股{TITLE} {NOW.strftime('%Y-%m-%d %H:%M')}"), title_style)]
        for raw in text.splitlines():
            if not raw.strip():
                story.append(Spacer(1, 4))
            elif raw.startswith("**") and raw.endswith("**"):
                story.append(Paragraph(inline_markup(raw), heading))
            elif raw.startswith("- "):
                story.append(Paragraph("• " + inline_markup(raw[2:]), bullet))
            else:
                story.append(Paragraph(inline_markup(raw), normal))
        story.append(Spacer(1, 10))
        story.append(Paragraph("由牛牛1号自动生成，仅作市场复盘观察，不构成投资建议。", normal))
        doc = SimpleDocTemplate(str(pdf_path), pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm, title=f"A股{TITLE}")
        doc.build(story)
        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            return pdf_path
    except Exception:
        return None
    return None


def build_decision_guidance(
    *,
    mood: str,
    up: int,
    down: int,
    limit_up: int | None,
    limit_down: int | None,
    hot_funds: list[dict[str, Any]],
    inflow_top: list[dict[str, Any]],
) -> list[str]:
    top_hot = "、".join(r.get("industry", "") for r in hot_funds[:3] if r.get("industry")) or "强势板块待确认"
    top_in = "、".join(r.get("industry", "") for r in inflow_top[:3] if r.get("industry")) or top_hot
    limit_pair_available = limit_up is not None and limit_down is not None
    if "行情接口未取到有效" in mood or (up == 0 and down == 0):
        risk = "数据缺失"
        pace = "不根据本报告新增仓位；先用交易软件核对实时涨跌家数、成交额和跌停数量"
        buy = "只保留观察清单，不执行自动化买入判断"
        sell = "已有仓位按实时盘口和既定风控处理，弱于板块或破位的优先降风险"
    elif "空头占优" in mood or (
        limit_pair_available and down > up * 1.25 and limit_down >= max(limit_up, 3)
    ):
        risk = "防守"
        pace = "只卖不买；已有仓位优先处理破位、亏损扩大和高位退潮信号"
        buy = "不追新题材，候选股即使达标也先观察到尾盘或次日确认"
        sell = "弱于板块、跌破BBI/白线、放量回落的持仓优先减仓或退出"
    elif "结构性偏弱" in mood or down >= up:
        risk = "谨慎"
        pace = "午后最多2-3只；本轮新开仓≤1笔，保留现金等主线延续"
        buy = f"只看资金流入且板块联动的方向：{top_in}；买点必须贴近BBI/均线，不追高"
        sell = "冲高不封板、板块掉队或回撤触发防卖飞评分的持仓先降仓"
    elif "多头占优" in mood:
        risk = "进攻"
        pace = "午后可把确认后的强势仓位扩到4-5只；仍需分批，单轮最多2笔"
        buy = f"优先顺着热门板块扩散：{top_hot}；只买回封、回踩不破或右侧确认"
        sell = "强势持仓可跟随，放量滞涨或跌回成本线时执行移动止盈"
    else:
        risk = "平衡"
        pace = "午后最多3-4只；本轮新开仓≤1笔，等13:30后确认承接"
        buy = f"围绕资金净流入方向筛选：{top_in}；弱分支和独立冲高不买"
        sell = "持仓强弱分层，弱于指数/板块的低效仓位给新主线让位"
    return [
        "🎯 **今日买卖指引**",
        f"· 风险级别：{risk}",
        f"· 开仓节奏：{pace}",
        f"· 买入指引：{buy}",
        f"· 卖出/风控：{sell}",
    ]


def build_midday_watchlist(
    *,
    rows: list[dict[str, Any]],
    hot_funds: list[dict[str, Any]],
    breadth_ind: list[Any],
    top_turnover: list[dict[str, Any]],
    top_gain: list[dict[str, Any]],
    total_amt: float,
    limit_up: int | None,
    limit_down: int | None,
) -> list[str]:
    lines = ["🔎 **午后关注**"]
    if not rows:
        lines.append("· 现货行情暂不可用，午后只按交易软件实时盘口确认，不根据本报告加仓")
        lines.append("· 优先观察跌停数量是否扩散、指数是否跌破上午低点、持仓是否弱于所属板块")
        lines.append("· 没有全市场成交额样本时，所有买卖动作降一级执行")
        return lines

    if hot_funds:
        strong_dirs = "、".join(r.get("industry", "") for r in hot_funds[:3] if r.get("industry"))
    else:
        strong_dirs = "、".join(ind for _, ind, *_ in breadth_ind[:3])
    if not strong_dirs:
        strong_dirs = "强势板块待确认"

    active_names = "、".join(
        f"{r['name']} {r['pct']:+.2f}%" for r in top_turnover[:3] if r.get("name")
    ) or "成交额前排股"
    strong_names = "、".join(
        f"{r['name']} {r['pct']:+.2f}%" for r in top_gain[:3] if r.get("name")
    ) or "上午强势股"

    if limit_up is None or limit_down is None:
        risk_line = "封板池数据暂不可用，午后风险端只按交易软件实时盘口确认"
    elif limit_down >= max(limit_up, 3):
        risk_line = "跌停端不弱，午后先看风险票是否开板/止跌，再考虑任何进攻动作"
    elif limit_up >= max(limit_down * 2, 5):
        risk_line = "涨停端占优，午后重点看封板率和炸板后回封能力"
    else:
        risk_line = "涨跌停结构分化，午后不追独立冲高，只看板块联动"

    amount_line = f"上午样本成交额 {fmt_amt_yuan(total_amt)}，午后关注量能是否继续向 `{strong_dirs}` 集中"
    lines.append(f"· 主线延续：`{strong_dirs}` 是否继续有资金净流入/成交额前排")
    lines.append(f"· 承接观察：{active_names} 能否维持红盘、回踩不破上午均价")
    lines.append(f"· 强势股验证：{strong_names} 午后不放量回落才算有效强")
    lines.append(f"· 风险端：{risk_line}")
    lines.append(f"· 量能条件：{amount_line}")
    return lines


def build_report(*, require_complete_spot: bool = False) -> str:
    if not is_trading_day_guess(NOW.date()):
        return ""
    spot, spot_err = fetch_spot()
    rows = extract_market(spot) if spot is not None else []
    # fetch_spot validates each successful source.  Do not let an error message
    # from an earlier provider invalidate a complete later-provider snapshot.
    spot_issue = spot_snapshot_issue(rows, spot_err)
    if require_complete_spot and spot_issue:
        raise SpotSnapshotUnavailable(spot_issue)
    fund_df, fund_err = fetch_industry_fund_flow()
    zt_df, dt_df, limit_pool_err = fetch_limit_pools()

    funds = extract_funds(fund_df)
    if not rows and not spot_issue:
        spot_issue = "现货行情返回数据缺少有效A股样本"
    elif 0 < len(rows) < 1000 and not spot_issue:
        spot_issue = f"有效A股样本仅 {len(rows)} 只，可能不是全市场快照"
    if fund_df is not None and not fund_err:
        if not funds:
            fund_err = "行业资金流返回数据缺少有效行业/资金字段"
        elif not any(abs(r.get("net", 0.0)) > 1 for r in funds):
            fund_err = "行业资金流净额全为0，已忽略"
            funds = []

    issues = []
    if spot_issue:
        issues.append(f"现货行情不完整：{spot_issue}" if rows else "现货行情缺失或不完整")
    if fund_err:
        fund_issue = "行业资金流缺失或不完整"
        if any(marker in str(fund_err) for marker in ("返回空", "缺少有效", "净额全为0", "按配置跳过")):
            fund_issue += f"：{fund_err}"
        issues.append(fund_issue)
    if limit_pool_err:
        missing_pools = []
        if zt_df is None:
            missing_pools.append("封死涨停池")
        if dt_df is None:
            missing_pools.append("封死跌停池")
        issues.append(("、".join(missing_pools) or "涨跌停池") + "缺失或不完整")

    up = sum(1 for r in rows if r["pct"] > 0)
    down = sum(1 for r in rows if r["pct"] < 0)
    flat = max(len(rows) - up - down, 0)
    limit_up = sealed_limit_count(zt_df)
    limit_down = sealed_limit_count(dt_df)
    limit_pair_available = limit_up is not None and limit_down is not None
    total_amt = sum(r["amount"] for r in rows if r["amount"] > 0)

    top_gain = sorted(rows, key=lambda x: (x["pct"], x["amount"]), reverse=True)[:8]
    top_turnover = sorted(rows, key=lambda x: x["amount"], reverse=True)[:8]
    hot_funds = sorted(funds, key=lambda x: x["heat"], reverse=True)[:6]
    inflow_top = sorted(funds, key=lambda x: x["net"], reverse=True)[:5]
    outflow_top = sorted(funds, key=lambda x: x["net"])[:5]

    ind_stats = defaultdict(lambda: {"count": 0, "up": 0, "amount": 0.0, "leaders": []})
    for r in rows:
        st = ind_stats[r["industry"]]
        st["count"] += 1
        st["up"] += 1 if r["pct"] > 0 else 0
        st["amount"] += max(r["amount"], 0)
        if r["pct"] > 0 and len(st["leaders"]) < 3:
            st["leaders"].append((r["pct"], r["name"]))
    breadth_ind = []
    for ind, st in ind_stats.items():
        if st["count"] >= 5:
            leaders = "、".join(name for _, name in sorted(st["leaders"], reverse=True)[:3]) or "-"
            score = st["up"] / max(st["count"], 1) * 100 + st["amount"] / 1e8 * 0.02
            breadth_ind.append((score, ind, st, leaders))
    breadth_ind.sort(reverse=True)

    if len(rows) == 0:
        mood = "行情接口未取到有效现货数据，今天不编造盘面结论。"
    elif limit_pair_available and up > down * 1.4 and limit_up >= max(limit_down * 2, 5):
        mood = "多头占优，题材/赚钱效应较活跃。"
    elif limit_pair_available and down > up * 1.3 and limit_down >= max(limit_up, 3):
        mood = "空头占优，风险端更强，优先控仓。"
    elif up > down:
        mood = "结构性偏强，但仍要看主线延续和量能。"
    else:
        mood = "结构性偏弱或分化，谨慎追高。"

    time_s = NOW.strftime("%Y-%m-%d %H:%M")
    lines = []
    lines.append(f"牛牛大王，A股{TITLE}来了：")
    lines.append("")
    lines.append(f"📊 **市场概况** · {time_s}")
    if rows:
        lines.append(f"全A样本 `{len(rows)}` 只 | 上涨 `{up}` · 下跌 `{down}` · 平盘 `{flat}`")
        limit_up_text = f"`{limit_up}`" if limit_up is not None else "数据缺失"
        limit_down_text = f"`{limit_down}`" if limit_down is not None else "数据缺失"
        lines.append(
            f"封死涨停 {limit_up_text} · 封死跌停 {limit_down_text} | 成交额 `{fmt_amt_yuan(total_amt)}`"
        )
    else:
        lines.append("现货行情未取到有效数据，市场广度、涨跌停和成交额暂不展示")
    lines.append(f"💬 {mood}")
    lines.append("")

    lines.append("🔥 **热门板块**")
    if hot_funds:
        for r in hot_funds[:5]:
            lead_txt = f" · 领涨 {r['lead']} {r['lead_pct']:+.2f}%" if r.get("lead") else ""
            lines.append(f"`{r['industry']}` {r['pct']:+.2f}% | 净额 {fmt_amt_yuan(r['net'])}{lead_txt}")
    elif breadth_ind:
        for _, ind, st, leaders in breadth_ind[:5]:
            lines.append(f"`{ind}` 上涨占比 {st['up']}/{st['count']} | 成交 {fmt_amt_yuan(st['amount'])} | {leaders}")
    else:
        lines.append("行业资金流和现货板块样本暂不可用")
    lines.append("")

    lines.append("💰 **资金流向**")
    if inflow_top:
        in_list = " · ".join([f"{r['industry']} {fmt_amt_yuan(r['net'])}" for r in inflow_top])
        out_list = " · ".join([f"{r['industry']} {fmt_amt_yuan(r['net'])}" for r in outflow_top])
        lines.append(f"流入：{in_list}")
        lines.append(f"流出：{out_list}")
    else:
        lines.append("行业资金流暂不可用")
    lines.append("")

    lines.append("⚡ **强势个股**")
    if top_gain:
        for r in top_gain[:5]:
            lines.append(f"`{r['code']} {r['name']}` {r['pct']:+.2f}% | {fmt_amt_yuan(r['amount'])}")
    else:
        lines.append("数据暂不可用")
    lines.append("")

    lines.append("📈 **成交活跃**")
    if top_turnover:
        for r in top_turnover[:5]:
            lines.append(f"`{r['code']} {r['name']}` {r['pct']:+.2f}% | {fmt_amt_yuan(r['amount'])}")
    else:
        lines.append("数据暂不可用")
    lines.append("")

    lines.extend(build_decision_guidance(
        mood=mood,
        up=up,
        down=down,
        limit_up=limit_up,
        limit_down=limit_down,
        hot_funds=hot_funds,
        inflow_top=inflow_top,
    ))
    lines.append("")

    if MODE == "midday":
        lines.extend(build_midday_watchlist(
            rows=rows,
            hot_funds=hot_funds,
            breadth_ind=breadth_ind,
            top_turnover=top_turnover,
            top_gain=top_gain,
            total_amt=total_amt,
            limit_up=limit_up,
            limit_down=limit_down,
        ))
        lines.append("")

    lines.append("💡 **操作提示**")
    if MODE == "midday":
        lines.append("· 看强板块扩散、龙头回封/不破均线")
        lines.append("· 量能不足则降低追高意愿")
    else:
        lines.append("· 复盘筛：板块强度 + 辨识度 + BBI右侧确认")
        lines.append("· 次日竞价看溢价，无溢价则降低预期")
    lines.append("· 板块联动 + BBI右侧优先，不追单纯J值低")
    lines.append("")

    lines.append("⚠️ **风险**")
    if down > up:
        lines.append("· 下跌家数多，仓位和追高保守")
    if limit_pair_available and limit_down >= limit_up and limit_down > 0:
        lines.append("· 跌停风险不弱，注意高位退潮")
    lines.append("· 数据为快照，以交易软件为准")
    if issues:
        lines.append("")
        lines.append("ℹ️ " + " · ".join(issues[:3]))
    return "\n".join(lines).strip()


def main():
    try:
        text = build_report(require_complete_spot=True)
        if text:
            from a_share_grok_summary import apply_grok_to_a_share_report
            from market_report_store import store_market_report
            text = apply_grok_to_a_share_report(text, title=f"A股{TITLE}")
            store_market_report(text, job_id="192abba7eeb5", title=f"A股{TITLE}", run_dt=NOW)
            print(text)
        else:
            print("")
    except SpotSnapshotUnavailable as e:
        print(f"牛牛大王，A股{TITLE}现货行情缺失或不完整，本轮不入库，等待调度器重试：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"牛牛大王，A股{TITLE}今天没有成功生成：{type(e).__name__}: {e}\n建议先手动看交易软件，稍后我可以帮你补一版。")
        sys.exit(1)


if __name__ == "__main__":
    main()
