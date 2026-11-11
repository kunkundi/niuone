"""Bounded reads of the official Shanghai and Shenzhen A-share listing reports."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO
import html
import json
from pathlib import Path
import re
import time
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zipfile import ZipFile

SH = "https://query.sse.com.cn/sseQuery/commonQuery.do"
SZ = "https://www.szse.cn/api/report/ShowReport"
MAX_BODY = 8 * 1024 * 1024


def _get(url: str, params: dict[str, str], deadline: float) -> bytes:
    ref = "https://www.sse.com.cn/assortment/stock/list/share/" if url == SH else "https://www.szse.cn/market/product/stock/list/index.html"
    err: Exception | None = None
    for _ in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            break
        request = Request(url + "?" + urlencode(params), headers={"Referer": ref, "User-Agent": "Mozilla/5.0"})
        try:
            with urlopen(request, timeout=min(8, remaining)) as response:
                raw = response.read(MAX_BODY + 1)
            if not raw or len(raw) > MAX_BODY:
                raise ValueError("invalid exchange report size")
            return raw
        except (OSError, ValueError) as exc:
            err = exc
    raise RuntimeError("exchange listing request failed") from err


def _sh_board(kind: str, deadline: float, get: Callable) -> list[tuple[str, str]]:
    rows: dict[str, str] = {}
    total = None
    for page in range(1, 9):
        params = {"STOCK_TYPE": kind, "REG_PROVINCE": "", "CSRC_CODE": "", "STOCK_CODE": "",
                  "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L", "COMPANY_STATUS": "2,4,5,7,8",
                  "type": "inParams", "isPagination": "true", "pageHelp.cacheSize": "1",
                  "pageHelp.beginPage": str(page), "pageHelp.endPage": str(page),
                  "pageHelp.pageSize": "2000", "pageHelp.pageNo": str(page)}
        data = json.loads(get(SH, params, deadline))
        meta = data["pageHelp"]
        count = int(meta["total"])
        if not 0 < count < 16000 or (total is not None and total != count) or int(meta["pageNo"]) != page:
            raise ValueError("inconsistent Shanghai exchange pagination")
        total = count
        for row in data["result"]:
            code, name = str(row["A_STOCK_CODE"]).strip(), str(row["SEC_NAME_CN"]).strip()
            if not re.fullmatch(r"6\d{5}", code) or not name or code in rows:
                raise ValueError("invalid Shanghai exchange listing row")
            rows[code] = name
        if len(rows) == total:
            return list(rows.items())
        if not data["result"] or page >= int(meta["pageCount"]):
            break
    raise ValueError("incomplete Shanghai exchange listing")


def _sz_rows(raw: bytes, metadata: Any, accepted_dates: set[str]) -> list[tuple[str, str]]:
    from openpyxl import load_workbook

    metadata = next(tab["metadata"] for tab in metadata if tab.get("metadata", {}).get("tabkey") == "tab1")
    stamp = str(metadata["subname"]).strip()[:10]
    date.fromisoformat(stamp)
    if stamp not in accepted_dates:
        raise ValueError("stale Shenzhen exchange listing date")
    with ZipFile(BytesIO(raw)) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 64 * 1024 * 1024:
            raise ValueError("oversized Shenzhen exchange workbook")
    book = load_workbook(BytesIO(raw), read_only=True, data_only=True)
    try:
        sheet = book.active
        # SZSE exports declare A1 as the used range even for complete workbooks.
        sheet.reset_dimensions()
        cells = sheet.iter_rows(values_only=True)
        header = tuple(str(v or "").strip() for v in next(cells))
        ci, ni = header.index("A股代码"), header.index("A股简称")
        rows: dict[str, str] = {}
        for row in cells:
            if not any(row):
                continue
            code = str(row[ci] or "").strip()
            if not re.fullmatch(r"\d{1,6}", code):
                raise ValueError("invalid Shenzhen exchange listing code")
            code = code.zfill(6)
            name = str(row[ni] or "").strip()
            if not code.startswith(("0", "3")) or not name or code in rows:
                raise ValueError("invalid Shenzhen exchange listing row")
            rows[code] = name
        if not rows or len(rows) != int(metadata["recordcount"]):
            raise ValueError("incomplete Shenzhen exchange workbook")
        return list(rows.items())
    finally:
        book.close()


def _sz_tab(payload: Any, accepted_dates: set[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tab = next(tab for tab in payload if tab.get('metadata', {}).get('tabkey') == 'tab1')
    meta = tab['metadata']
    stamp = str(meta['subname']).strip()[:10]
    date.fromisoformat(stamp)
    if stamp not in accepted_dates:
        raise ValueError('stale Shenzhen exchange listing date')
    return meta, tab.get('data') or []


def _sz_json_rows(first: Any, accepted_dates: set[str], deadline: float, get: Callable) -> list[tuple[str, str]]:
    meta, initial = _sz_tab(first, accepted_dates)
    total, pages, size = (int(meta[key]) for key in ('recordcount', 'pagecount', 'pagesize'))
    if not 0 < total < 16000 or not 0 < pages <= 256 or not 0 < size <= 2000 or (total + size - 1) // size != pages or int(meta['pageno']) != 1:
        raise ValueError('inconsistent Shenzhen exchange pagination')

    def page(number: int) -> list[dict[str, Any]]:
        raw = get(SZ + '/data', {'CATALOGID':'1110', 'TABKEY':'tab1', 'SHOWTYPE':'JSON', 'PAGENO':str(number)}, deadline)
        current, rows = _sz_tab(json.loads(raw), accepted_dates)
        if any(str(current.get(key)) != str(meta.get(key)) for key in ('recordcount','pagecount','pagesize','subname')) or int(current['pageno']) != number:
            raise ValueError('inconsistent Shenzhen exchange pagination')
        return rows

    all_rows = list(initial)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for rows in pool.map(page, range(2, pages + 1)):
            all_rows.extend(rows)
    result: dict[str, str] = {}
    for row in all_rows:
        code = str(row.get('agdm') or '').strip()
        name = html.unescape(re.sub(r'<[^>]+>', '', str(row.get('agjc') or ''))).strip()
        if not re.fullmatch(r'[03]\d{5}', code) or not name or code in result:
            raise ValueError('invalid Shenzhen exchange JSON row')
        result[code] = name
    if len(result) != total:
        raise ValueError('incomplete Shenzhen exchange JSON listing')
    return sorted(result.items())


def listing_error_summary(exc: BaseException) -> str:
    """Keep endpoint stages and error categories without provider bodies or request URLs."""
    if isinstance(exc, ListingFetchError):
        return str(exc)
    parts = []
    while exc is not None and len(parts) < 4:
        code = getattr(exc, 'code', None)
        part = type(exc).__name__ + (f'(HTTP {code})' if isinstance(code, int) else '')
        if type(exc) is ValueError:
            part += ': ' + str(exc)[:120]
        parts.append(part)
        exc = exc.__cause__
    return ' -> '.join(parts)


class ListingFetchError(ValueError):
    pass


def fetch_exchange_listing(accepted_dates: set[str], *, get: Callable = _get,
                           cache_path: Path | None = None, cache_day: str = '') -> list[tuple[str, str]]:
    """Validate each exchange's declared row count before combining its exact universe."""
    from app.core.json_cache import read_json_cache, write_json_cache

    deadline = time.monotonic() + 45
    params = {"CATALOGID": "1110", "TABKEY": "tab1"}
    parts: dict[str, list[tuple[str, str]]] = {}
    patterns = {'Shanghai main':r'60\d{4}', 'Shanghai STAR':r'68\d{4}', 'Shenzhen':r'[03]\d{5}'}
    cached = read_json_cache(cache_path) if cache_path is not None else None
    if cache_day and isinstance(cached, dict) and cached.get('date') == cache_day and isinstance(cached.get('parts'), dict):
        for key, value in cached['parts'].items():
            if key not in patterns or not isinstance(value, dict):
                continue
            rows = value.get('rows')
            if not isinstance(rows, list) or not 0 < len(rows) <= 16000 or len(rows) != value.get('count'):
                continue
            if all(isinstance(row, (list,tuple)) and len(row) == 2 and isinstance(row[0], str) and re.fullmatch(patterns[key],row[0]) and isinstance(row[1],str) and row[1] for row in rows) and len({row[0] for row in rows}) == len(rows):
                parts[key] = [tuple(row) for row in rows]
    errors: list[str] = []

    def shenzhen() -> list[tuple[str, str]]:
        with ThreadPoolExecutor(max_workers=2) as pool:
            xlsx = pool.submit(get, SZ, {**params, 'SHOWTYPE':'xlsx'}, deadline)
            summary = pool.submit(get, SZ + '/data', {**params, 'SHOWTYPE':'JSON', 'PAGENO':'1'}, deadline)
            metadata = json.loads(summary.result())
            try:
                return _sz_rows(xlsx.result(), metadata, accepted_dates)
            except Exception as exc:
                try:
                    return _sz_json_rows(metadata, accepted_dates, deadline, get)
                except Exception as fallback:
                    raise ListingFetchError('workbook=' + listing_error_summary(exc) + '; JSON=' + listing_error_summary(fallback)) from fallback

    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {key:pool.submit(_sh_board, kind, deadline, get) for key,kind in
                 (('Shanghai main','1'), ('Shanghai STAR','8')) if key not in parts}
        if 'Shenzhen' not in parts:
            tasks['Shenzhen'] = pool.submit(shenzhen)
        for key, task in tasks.items():
            try:
                parts[key] = task.result()
            except Exception as exc:
                errors.append(key + ': ' + listing_error_summary(exc))
    if cache_path is not None and cache_day:
        write_json_cache(cache_path, {'date':cache_day, 'parts':{key:{'count':len(rows), 'rows':rows} for key,rows in parts.items()}})
    if errors:
        raise ListingFetchError('; '.join(errors))
    rows = [row for part in parts.values() for row in part]
    if len({code for code, _ in rows}) != len(rows):
        raise ValueError("overlapping exchange listings")
    return sorted(rows)
