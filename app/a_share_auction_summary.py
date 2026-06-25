#!/usr/bin/env python3
"""A-share 9:25 auction summary cron script.

Generates a deterministic market-open report and mirrors it to the dashboard.
Designed to avoid LLM/model-gateway calls during market-open peak load.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sys
import time
import contextlib
import html
import signal
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from niuone_paths import get_dashboard_home

# Avoid user proxy breaking Eastmoney when possible.
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

try:
    import akshare as ak  # type: ignore
    import pandas as pd  # type: ignore
except Exception as e:  # durable environment problem: still alert user briefly
    print(f"牛牛大王，A股竞价总结生成失败：本机 akshare/pandas 不可用：{e}")
    sys.exit(1)

CN_TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime.now(CN_TZ)
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
STATE_DIR = DASHBOARD_HOME / "cron" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = STATE_DIR / "a_share_auction_summary.json"


def is_trading_day_guess(day: dt.date) -> bool:
    # Holiday calendars are imperfect; weekend guard is enough for cron weekday schedule.
    return day.weekday() < 5


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.replace("%", "").replace(",", "").strip()
            if x in {"", "-", "--", "None", "nan"}:
                return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def safe_int(x: Any, default: int = 0) -> int:
    return int(safe_float(x, default))


def fmt_amt_yuan(v: float | int | None) -> str:
    if v is None:
        return "-"
    v = float(v)
    if abs(v) >= 1e8:
        return f"{v/1e8:.2f}亿"
    if abs(v) >= 1e4:
        return f"{v/1e4:.0f}万"
    return f"{v:.0f}元"


def parse_money_to_yuan(x: Any) -> float:
    """Parse Chinese money strings like '1.23亿', '4567万', '-2.1亿' to yuan."""
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return safe_float(x)
    s = str(x).replace(",", "").strip()
    if not s or s in {"-", "--", "nan", "None"}:
        return 0.0
    m = re.search(r"([-+]?\d+(?:\.\d+)?)", s)
    if not m:
        return 0.0
    v = float(m.group(1))
    if "亿" in s:
        v *= 1e8
    elif "万" in s:
        v *= 1e4
    return v


@contextlib.contextmanager
def time_limit(seconds: int):
    """Unix-only short timeout for optional slow data sources."""
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return
    def _handler(signum, frame):
        raise TimeoutError(f"timed out after {seconds}s")
    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def normalize_industry_name(name: str) -> str:
    s = str(name or "").strip()
    for suffix in ["行业", "板块", "概念"]:
        if s.endswith(suffix) and len(s) > len(suffix) + 1:
            s = s[: -len(suffix)]
    return s


def normalize_code(code: Any) -> str:
    s = str(code).strip()
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else s


def is_normal_a_share(code: str, name: str) -> bool:
    code = normalize_code(code)
    if not re.match(r"^(60|68|00|30)\d{4}$", code):
        return False
    bad = ("ST" in name.upper()) or ("退" in name) or ("N" == name[:1])
    return not bad


def first_col(df, names: list[str]) -> str | None:
    cols = {str(c): c for c in df.columns}
    for n in names:
        if n in cols:
            return cols[n]
    # fuzzy contains fallback
    for n in names:
        for c in df.columns:
            if n in str(c):
                return c
    return None


def fetch_zt_pool() -> tuple[Any | None, str | None]:
    # Try multiple akshare names/date formats. Around 9:25 the latest pool may be sparse but useful.
    funcs = [
        ("stock_zt_pool_em", {"date": NOW.strftime("%Y%m%d")}),
        ("stock_zt_pool_em", {}),
    ]
    last_err = None
    for fname, kwargs in funcs:
        try:
            fn = getattr(ak, fname)
            df = fn(**kwargs)
            if df is not None and len(df) >= 0:
                return df, None
        except Exception as e:
            last_err = f"{fname}: {type(e).__name__}: {e}"
    return None, last_err


def fetch_dt_pool() -> tuple[Any | None, str | None]:
    funcs = [
        ("stock_zt_pool_dtgc_em", {"date": NOW.strftime("%Y%m%d")}),
        ("stock_zt_pool_dtgc_em", {}),
    ]
    last_err = None
    for fname, kwargs in funcs:
        try:
            fn = getattr(ak, fname)
            df = fn(**kwargs)
            return df, None
        except Exception as e:
            last_err = f"{fname}: {type(e).__name__}: {e}"
    return None, last_err


def fetch_industry_boards() -> tuple[Any | None, str | None]:
    # Full industry board fetch is often slow around 9:25 and can dominate runtime.
    # Keep it opt-in; the report can still be useful from zt/dt pools alone.
    if os.getenv("A_SHARE_AUCTION_FETCH_BOARDS", "0") != "1":
        return None, "行业板块全量接口已跳过以保证9:25稳定性"
    try:
        df = ak.stock_board_industry_name_em()
        return df, None
    except Exception as e:
        return None, f"stock_board_industry_name_em: {type(e).__name__}: {e}"


def fetch_spot() -> tuple[Any | None, str | None]:
    # Full A-share spot list can take 40-60s via akshare in this environment.
    # Skip by default; use conservative name-based direction labels instead.
    if os.getenv("A_SHARE_AUCTION_FETCH_SPOT", "0") != "1":
        return None, "现货全量列表已跳过以保证9:25稳定性"
    for fname in ["stock_zh_a_spot_em", "stock_zh_a_spot"]:
        try:
            fn = getattr(ak, fname)
            df = fn()
            if df is not None and len(df):
                return df, None
        except Exception as e:
            last_err = f"{fname}: {type(e).__name__}: {e}"
    return None, locals().get("last_err", "no spot function")


def fetch_industry_fund_flow() -> tuple[Any | None, str | None]:
    """Fetch industry fund-flow/heat data with a hard short timeout.

    Uses TongHuaShun via akshare. This is optional; if it times out or the
    upstream rejects, the auction report still succeeds.
    """
    timeout_s = safe_int(os.getenv("A_SHARE_AUCTION_FUND_TIMEOUT", "5"), 5)
    if os.getenv("A_SHARE_AUCTION_FETCH_FUNDS", "1") != "1":
        return None, "行业资金流接口已按配置跳过"
    try:
        with time_limit(timeout_s):
            df = ak.stock_fund_flow_industry(symbol="即时")
        if df is None or len(df) == 0:
            return None, "行业资金流返回空"
        return df, None
    except Exception as e:
        return None, f"行业资金流：{type(e).__name__}: {e}"


def get_code_name_cols(df):
    return first_col(df, ["代码", "股票代码", "symbol", "code"]), first_col(df, ["名称", "股票简称", "name"])


def estimate_seal_amount(row, df) -> float:
    # Eastmoney zt pool often has 封单资金/封板资金/最后封板资金/封单额; otherwise estimate by 最新价 * 封单量.
    for col in ["封单资金", "封板资金", "最后封板资金", "封单额", "涨停封单额", "封单金额"]:
        c = first_col(df, [col])
        if c is not None:
            val = safe_float(row.get(c))
            if val > 0:
                return val
    vol_col = first_col(df, ["封单量", "封板量", "买一量", "委买量"])
    price_col = first_col(df, ["最新价", "涨停价", "价格", "今开"])
    if vol_col is not None and price_col is not None:
        return safe_float(row.get(vol_col)) * safe_float(row.get(price_col))
    return 0.0


def enrich_industries_from_spot(spot) -> dict[str, str]:
    out = {}
    if spot is None:
        return out
    code_col, name_col = get_code_name_cols(spot)
    ind_col = first_col(spot, ["行业", "所属行业", "板块"])
    if code_col is None or ind_col is None:
        return out
    for _, r in spot.iterrows():
        code = normalize_code(r.get(code_col))
        ind = str(r.get(ind_col) or "").strip()
        if code and ind and ind.lower() != "nan":
            out[code] = ind
    return out


def simple_industry_guess(name: str) -> str:
    # Very conservative fallback for common auction labels.
    table = [
        ("银行", "银行/大金融"), ("证券", "证券/大金融"), ("保险", "保险/大金融"),
        ("通信", "通信设备"), ("光", "光通信/光电方向"), ("电子", "电子/元件"),
        ("科技", "科技/信息技术"), ("软件", "软件/信创"), ("芯", "半导体/芯片"),
        ("电力", "电力/公用事业"), ("能源", "能源方向"), ("煤", "煤炭/资源"),
        ("稀土", "稀土/小金属"), ("黄金", "黄金/贵金属"), ("铜", "有色金属"),
        ("汽车", "汽车产业链"), ("机器人", "机器人/高端制造"),
        ("药", "医药"), ("生物", "生物医药"), ("食品", "食品消费"),
        ("旅游", "旅游消费"), ("地产", "房地产"), ("建设", "基建/建筑"),
        ("水泥", "水泥/建材"), ("玻璃", "玻璃/建材"),
    ]
    for k, v in table:
        if k in name:
            return v
    return "所属方向待复核"


def markdown_to_html(text: str) -> str:
    """Tiny Markdown subset renderer suitable for this deterministic report."""
    out = []
    for raw in text.splitlines():
        line = html.escape(raw)
        line = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        if line.startswith("- "):
            out.append(f"<p class='bullet'>• {line[2:]}</p>")
        elif line.strip() == "":
            out.append("<div class='gap'></div>")
        else:
            out.append(f"<p>{line}</p>")
    return "\n".join(out)


def write_report_pdf(text: str) -> Path | None:
    """Render the report to PDF via reportlab with a Chinese system font."""
    out_dir = DASHBOARD_HOME / "cron" / "attachments" / "a_share_auction_summary"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = NOW.strftime("%Y-%m-%d_%H-%M_A股竞价盘前总结")
    pdf_path = out_dir / f"{stem}.pdf"
    title = f"A股竞价盘前总结 {NOW.strftime('%Y-%m-%d %H:%M')}"
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
        normal = ParagraphStyle(
            "CNNormal", parent=styles["Normal"], fontName=font_name, fontSize=10.5,
            leading=16, spaceAfter=3, textColor=colors.HexColor("#111111"),
            wordWrap="CJK",
        )
        heading = ParagraphStyle(
            "CNHeading", parent=normal, fontName=font_name, fontSize=14,
            leading=20, spaceBefore=8, spaceAfter=5, textColor=colors.HexColor("#111111"),
        )
        title_style = ParagraphStyle(
            "CNTitle", parent=normal, fontName=font_name, fontSize=18,
            leading=24, spaceAfter=12, alignment=1,
        )
        bullet = ParagraphStyle(
            "CNBullet", parent=normal, leftIndent=10, firstLineIndent=-10,
        )
        code_style = "<font backColor='#f2f3f5'>\\1</font>"

        def inline_markup(s: str) -> str:
            s = html.escape(s)
            s = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", s)
            s = re.sub(r"`([^`]+)`", code_style, s)
            return s

        story = [Paragraph(html.escape(title), title_style)]
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
        story.append(Paragraph("由牛牛大作手自动生成，仅作盘前观察，不构成投资建议。", normal))
        doc = SimpleDocTemplate(str(pdf_path), pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm, title=title)
        doc.build(story)
        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            return pdf_path
    except Exception:
        return None
    return None


def build_report() -> str:
    if not is_trading_day_guess(NOW.date()):
        return ""

    zt, zt_err = fetch_zt_pool()
    dtpool, dt_err = fetch_dt_pool()
    boards, board_err = fetch_industry_boards()
    fund_df, fund_err = fetch_industry_fund_flow()
    spot, spot_err = fetch_spot()
    industry_map = enrich_industries_from_spot(spot)

    issues = []
    if zt_err:
        issues.append(f"涨停池：{zt_err}")
    if board_err:
        issues.append(f"行业板块：{board_err}")
    if fund_err:
        issues.append(f"行业资金流：{fund_err}")
    if spot_err:
        issues.append(f"现货列表：{spot_err}")

    zt_rows = []
    if zt is not None and len(zt):
        code_col, name_col = get_code_name_cols(zt)
        pct_col = first_col(zt, ["涨跌幅", "涨幅"])
        price_col = first_col(zt, ["最新价", "价格"])
        if code_col and name_col:
            for _, row in zt.iterrows():
                code = normalize_code(row.get(code_col))
                name = str(row.get(name_col) or "").strip()
                if not is_normal_a_share(code, name):
                    continue
                amt = estimate_seal_amount(row, zt)
                ind = industry_map.get(code) or simple_industry_guess(name)
                zt_rows.append({
                    "code": code,
                    "name": name,
                    "industry": ind,
                    "amount": amt,
                    "pct": safe_float(row.get(pct_col)) if pct_col else 10.0,
                    "price": safe_float(row.get(price_col)) if price_col else 0.0,
                })
    zt_rows.sort(key=lambda x: x["amount"], reverse=True)

    dt_count = 0
    if dtpool is not None and len(dtpool):
        ccol, ncol = get_code_name_cols(dtpool)
        if ccol and ncol:
            for _, r in dtpool.iterrows():
                if is_normal_a_share(normalize_code(r.get(ccol)), str(r.get(ncol) or "")):
                    dt_count += 1

    board_lines = []
    if boards is not None and len(boards):
        name_col = first_col(boards, ["板块名称", "名称", "行业名称"])
        pct_col = first_col(boards, ["涨跌幅", "涨幅"])
        up_col = first_col(boards, ["上涨家数", "上涨数"])
        down_col = first_col(boards, ["下跌家数", "下跌数"])
        lead_col = first_col(boards, ["领涨股票", "领涨股"])
        if name_col and pct_col:
            tmp = []
            for _, r in boards.iterrows():
                pct = safe_float(r.get(pct_col))
                up = safe_int(r.get(up_col)) if up_col else 0
                down = safe_int(r.get(down_col)) if down_col else 0
                breadth = up - down
                score = pct * 2 + max(min(breadth, 50), -50) / 20
                tmp.append((score, pct, up, down, str(r.get(name_col)), str(r.get(lead_col) or "")))
            tmp.sort(reverse=True)
            for _, pct, up, down, name, lead in tmp[:5]:
                breadth_txt = f"，上涨/下跌 {up}/{down}" if up or down else ""
                lead_txt = f"，领涨 {lead}" if lead and lead != "nan" else ""
                board_lines.append(f"- {name}：{pct:+.2f}%{breadth_txt}{lead_txt}")

    by_ind = defaultdict(lambda: {"amount": 0.0, "names": []})
    for r in zt_rows:
        by_ind[r["industry"]]["amount"] += r["amount"]
        if len(by_ind[r["industry"]]["names"]) < 3:
            by_ind[r["industry"]]["names"].append(r["name"])
    ind_top = sorted(by_ind.items(), key=lambda kv: kv[1]["amount"], reverse=True)[:5]

    fund_rows = []
    if fund_df is not None and len(fund_df):
        ind_col = first_col(fund_df, ["行业"])
        pct_col = first_col(fund_df, ["行业-涨跌幅", "涨跌幅", "阶段涨跌幅"])
        inflow_col = first_col(fund_df, ["流入资金"])
        outflow_col = first_col(fund_df, ["流出资金"])
        net_col = first_col(fund_df, ["净额", "资金流入净额"])
        count_col = first_col(fund_df, ["公司家数"])
        lead_col = first_col(fund_df, ["领涨股"])
        lead_pct_col = first_col(fund_df, ["领涨股-涨跌幅"])
        if ind_col:
            for _, r in fund_df.iterrows():
                name = normalize_industry_name(str(r.get(ind_col) or ""))
                if not name or name.lower() == "nan":
                    continue
                inflow_raw = r.get(inflow_col) if inflow_col else 0.0
                outflow_raw = r.get(outflow_col) if outflow_col else 0.0
                net_raw = r.get(net_col) if net_col else None
                # akshare.stock_fund_flow_industry returns numeric money fields in 亿元.
                inflow = safe_float(inflow_raw) * 1e8 if isinstance(inflow_raw, (int, float)) else parse_money_to_yuan(inflow_raw)
                outflow = safe_float(outflow_raw) * 1e8 if isinstance(outflow_raw, (int, float)) else parse_money_to_yuan(outflow_raw)
                if net_col:
                    net = safe_float(net_raw) * 1e8 if isinstance(net_raw, (int, float)) else parse_money_to_yuan(net_raw)
                else:
                    net = inflow - outflow
                pct = safe_float(r.get(pct_col)) if pct_col else 0.0
                count = safe_int(r.get(count_col)) if count_col else 0
                lead = str(r.get(lead_col) or "").strip() if lead_col else ""
                lead_pct = safe_float(r.get(lead_pct_col)) if lead_pct_col else 0.0
                heat = pct * 2 + (net / 1e8) * 0.35 + min(count, 150) / 100
                fund_rows.append({
                    "industry": name,
                    "pct": pct,
                    "inflow": inflow,
                    "outflow": outflow,
                    "net": net,
                    "count": count,
                    "lead": "" if lead.lower() == "nan" else lead,
                    "lead_pct": lead_pct,
                    "heat": heat,
                })
    fund_hot = sorted(fund_rows, key=lambda x: x["heat"], reverse=True)[:5]
    fund_in_top = sorted(fund_rows, key=lambda x: x["net"], reverse=True)[:5]
    fund_out_top = sorted(fund_rows, key=lambda x: x["net"])[:5]

    seal_lookup = {normalize_industry_name(k): v for k, v in by_ind.items()}
    composite_rows = []
    for fr in fund_rows:
        seal = seal_lookup.get(fr["industry"], {"amount": 0.0, "names": []})
        comp_score = fr["heat"] + (seal["amount"] / 1e8) * 0.6
        composite_rows.append((comp_score, fr, seal))
    for ind, seal in seal_lookup.items():
        if not any(fr["industry"] == ind for fr in fund_rows):
            composite_rows.append(((seal["amount"] / 1e8) * 0.6, {"industry": ind, "pct": 0.0, "net": 0.0, "inflow": 0.0, "outflow": 0.0, "lead": "", "heat": 0.0}, seal))
    composite_top = sorted(composite_rows, key=lambda x: x[0], reverse=True)[:5]

    time_s = NOW.strftime("%Y-%m-%d %H:%M")
    zt_count = len(zt_rows)
    if zt_count >= 30 and dt_count <= 5:
        mood = "竞价进攻较强，涨停扩散明显。"
    elif zt_count >= 10:
        mood = "竞价有一定进攻，但仍要看开盘承接。"
    elif zt_count <= 3 and dt_count > zt_count:
        mood = "竞价偏弱，风险端强于进攻端。"
    else:
        mood = "竞价中性偏谨慎，结构性机会为主。"

    lines = []
    lines.append("牛牛大王，9:25竞价总结来了：")
    lines.append("")
    lines.append(f"📊 **竞价情绪** · {time_s}")
    lines.append(f"涨停池 `{zt_count}` 只 · 跌停池 `{dt_count}` 只")
    lines.append(f"💬 {mood}")
    lines.append("")

    lines.append("🔥 **热门板块**")
    if fund_hot:
        for r in fund_hot:
            lead_txt = f" · 领涨 {r['lead']} {r['lead_pct']:+.2f}%" if r.get("lead") else ""
            lines.append(f"`{r['industry']}` {r['pct']:+.2f}% | 净额 {fmt_amt_yuan(r['net'])}{lead_txt}")
    elif board_lines:
        lines.extend(board_lines)
    else:
        lines.append("数据暂不可用")
    lines.append("")

    lines.append("💰 **资金流向**")
    if fund_in_top:
        in_txt = " · ".join([f"{r['industry']} {fmt_amt_yuan(r['net'])}" for r in fund_in_top[:5]])
        out_txt = " · ".join([f"{r['industry']} {fmt_amt_yuan(r['net'])}" for r in fund_out_top[:5]])
        lines.append(f"流入：{in_txt}")
        lines.append(f"流出：{out_txt}")
    else:
        lines.append("数据暂不可用")
    lines.append("")

    lines.append("🌡️ **复合热度Top5**")
    if composite_top:
        for _, fr, seal in composite_top:
            names = "、".join(seal.get("names") or []) or "-"
            seal_amt = fmt_amt_yuan(seal.get("amount", 0.0)) if seal.get("amount", 0.0) > 0 else "-"
            lines.append(f"`{fr['industry']}` 净额 {fmt_amt_yuan(fr.get('net', 0.0))} · 封单 {seal_amt} | {names}")
    else:
        lines.append("数据暂不可用")
    lines.append("")

    lines.append("📌 **涨停封单Top5板块**")
    if ind_top:
        for ind, v in ind_top:
            names = "、".join(v["names"])
            amt = fmt_amt_yuan(v["amount"]) if v["amount"] > 0 else "-"
            lines.append(f"`{ind}` {amt} | {names}")
    else:
        lines.append("数据暂不可用")
    lines.append("")

    lines.append("⚡ **封单Top5个股**")
    if zt_rows[:5]:
        for r in zt_rows[:5]:
            amt = fmt_amt_yuan(r["amount"]) if r["amount"] > 0 else "-"
            lines.append(f"`{r['code']} {r['name']}` 封单 {amt}")
    else:
        lines.append("数据暂不可用")
    lines.append("")

    lines.append("👀 **重点观察**")
    if zt_rows[:3]:
        for r in zt_rows[:3]:
            lines.append(f"· `{r['name']}` 辨识度靠前，看板块跟风+开盘不炸")
    elif board_lines:
        lines.append("· 看涨幅靠前+上涨家数占优板块，别追独苗")
    else:
        lines.append("· 强信号不密集，等开盘5-15分钟确认")
    lines.append("")

    lines.append("⚠️ **风险**")
    if zt_count <= 3:
        lines.append("· 涨停数量少，独苗/孤立高开不追")
    if dt_count > zt_count:
        lines.append("· 跌停/风险票不低，情绪非无脑强")
    lines.append("· 高开过度、封单虚胖、开盘撤单等回踩")
    lines.append("· 板块联动 + BBI右侧优先，不追单纯J值低")
    if issues:
        lines.append("")
        lines.append("ℹ️ " + " · ".join(issues[:3]))

    return "\n".join(lines).strip()


def main():
    try:
        text = build_report()
        if text:
            from niuone_dashboard_archive import archive_market_report
            archive_market_report(text, job_id="8453b3f28cd3", title="A股竞价盘前总结", run_dt=NOW)
            print(text)
        else:
            # Weekend/holiday silence.
            print("")
    except Exception as e:
        print(f"牛牛大王，A股竞价总结今天没有成功生成：{type(e).__name__}: {e}\n建议先手动看东方财富涨停池/行业板块，稍后我可以帮你补一版盘中总结。")
        sys.exit(1)


if __name__ == "__main__":
    main()
