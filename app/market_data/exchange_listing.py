"""Bounded reads of the official Shanghai and Shenzhen A-share listing reports."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO
import json
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
            with urlopen(request, timeout=min(16, remaining)) as response:
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


def fetch_exchange_listing(accepted_dates: set[str], *, get: Callable = _get) -> list[tuple[str, str]]:
    """Validate each exchange's declared row count before combining its exact universe."""
    deadline = time.monotonic() + 37
    params = {"CATALOGID": "1110", "TABKEY": "tab1"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        boards = [pool.submit(_sh_board, kind, deadline, get) for kind in ("1", "8")]
        xlsx = pool.submit(get, SZ, {**params, "SHOWTYPE": "xlsx"}, deadline)
        summary = pool.submit(get, SZ + "/data", {**params, "SHOWTYPE": "JSON", "PAGENO": "1"}, deadline)
        rows = _sz_rows(xlsx.result(), json.loads(summary.result()), accepted_dates)
        for board in boards:
            rows.extend(board.result())
    if len({code for code, _ in rows}) != len(rows):
        raise ValueError("overlapping exchange listings")
    return sorted(rows)
