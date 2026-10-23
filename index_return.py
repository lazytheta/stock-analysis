"""What the S&P 500 is priced to return from today, the yardstick for every
name on the watchlist (owner request 2026-10-23).

The hurdle was a fixed 9% because that is what the index earned over the
long run. But the question "why this share and not the index" compares a
share's expected return from TODAY's price with the index's expected return
from today's price, and that number moves with rates and prices. Damodaran
publishes it monthly: the discount rate at which the index level equals the
present value of its expected cash yield (dividends + buybacks), analysts'
growth for five years, then growth at the risk-free rate. The same kind of
number our DCF produces for one company.

Source: his ERPbymonth.xlsx ("ERP in last 12 months" sheet; the headline
"ERP (T12m)" column plus the T-bond rate). Read with the standard library --
an .xlsx is a zip of XML -- so no new dependency. Cached for a day; when the
fetch fails the last known figure stands, marked with its date, never a
silent fallback to 9%.
"""

import html
import logging
import re
import threading
import time
import zipfile
from datetime import date, timedelta
from io import BytesIO

logger = logging.getLogger(__name__)

URL = "https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx"
SHEET = "xl/worksheets/sheet1.xml"
# Reviewed by hand against his October 2026 post: 5.29% + 3.70% = 8.99%.
LAST_KNOWN = {"date": "2026-10-01", "sp500": 7652.0, "tbond": 0.0529,
              "erp": 0.0370, "expected_return": 0.0899, "source": "last known"}
_TTL = 86400
_cache: dict = {}
_lock = threading.Lock()


def _excel_date(serial, date1904):
    base = date(1904, 1, 1) if date1904 else date(1899, 12, 30)
    return base + timedelta(days=int(float(serial)))


def parse_workbook(data: bytes) -> list:
    """Every month row as {"date", "sp500", "tbond", "erp", "expected_return"},
    oldest first. Columns by header name, so a shifted column is caught
    rather than misread."""
    z = zipfile.ZipFile(BytesIO(data))
    wb = z.read("xl/workbook.xml").decode()
    date1904 = bool(re.search(r'date1904="(1|true)"', wb))
    strings = []
    if "xl/sharedStrings.xml" in z.namelist():
        ss = z.read("xl/sharedStrings.xml").decode()
        strings = [html.unescape(re.sub(r"<[^>]+>", "", s))
                   for s in re.findall(r"<si>(.*?)</si>", ss, re.S)]
    sheet = z.read(SHEET).decode()

    def cells(row_xml):
        out = {}
        for ref, attrs, body in re.findall(r'<c r="([A-Z]+)\d+"([^>]*)>(.*?)</c>', row_xml, re.S):
            v = re.search(r"<v>(.*?)</v>", body)
            if not v:
                continue
            val = v.group(1)
            out[ref] = strings[int(val)] if 't="s"' in attrs else val
        return out

    rows = re.findall(r"<row [^>]*>(.*?)</row>", sheet, re.S)
    if not rows:
        return []
    header = {v.strip().lower(): k for k, v in cells(rows[0]).items()}
    col = {
        "date": header.get("start of month"),
        "sp500": header.get("s&p 500"),
        "tbond": header.get("t.bond rate"),
        "erp": header.get("erp (t12m)"),
    }
    if any(v is None for v in col.values()):
        raise ValueError(f"unexpected columns: {sorted(header)}")
    exp_col = header.get("expected return")      # his own figure, when present
    out = []
    for r in rows[1:]:
        c = cells(r)
        try:
            tbond, erp = float(c[col["tbond"]]), float(c[col["erp"]])
            try:
                expected = float(c[exp_col]) if exp_col else tbond + erp
            except (KeyError, ValueError):
                expected = tbond + erp
            out.append({"date": _excel_date(c[col["date"]], date1904).isoformat(),
                        "sp500": float(c[col["sp500"]]), "tbond": tbond, "erp": erp,
                        "expected_return": round(expected, 4)})
        except (KeyError, ValueError):
            continue
    return out


def _fetch():
    import urllib.request

    from gather_data import _ssl_ctx
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (LazyTheta)"})
    with urllib.request.urlopen(req, timeout=20, context=_ssl_ctx) as resp:
        return resp.read()


def current(fetch=None, now=None) -> dict:
    """The latest month: {"date", "sp500", "tbond", "erp", "expected_return",
    "source"}. Never raises; on failure the last known figure, dated."""
    now = now or time.monotonic()
    with _lock:
        hit = _cache.get("latest")
        if hit and now - hit[0] < _TTL:
            return hit[1]
    try:
        rows = parse_workbook((fetch or _fetch)())
        if not rows:
            raise ValueError("no rows")
        latest = dict(rows[-1], source="Damodaran, implied ERP")
    except Exception as e:
        logger.warning("Index return unavailable (%s); last known %s", e, LAST_KNOWN["date"])
        latest = dict(LAST_KNOWN)
    with _lock:
        _cache["latest"] = (now, latest)
    return latest


def label(figure: dict | None = None) -> str:
    """'S&P 500 8.99% (Damodaran, Oct 2026)' for captions."""
    f = figure or current()
    try:
        when = date.fromisoformat(f["date"]).strftime("%b %Y")
    except (TypeError, ValueError):
        when = "?"
    return f"S&P 500 {f['expected_return'] * 100:.2f}% (Damodaran, {when})"
