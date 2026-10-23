"""The index yardstick and the return a price implies (owner request 2026-10-23)."""
import zipfile
from io import BytesIO

import pytest

import dcf_calculator as dc
import index_return as ir
import summary_page as sp

_HEADER = ["Start of month", "S&P 500", "T.Bond Rate", "$ Riskfree Rate", "ERP (T12m)"]


def _xlsx(rows, date1904=True):
    """A minimal workbook shaped like Damodaran's ERPbymonth.xlsx."""
    strings = _HEADER
    def cell(col, r, v, s=False):
        t = ' t="s"' if s else ""
        return f'<c r="{col}{r}"{t}><v>{v}</v></c>'
    cols = "ABCDE"
    xml_rows = ['<row r="1">' + "".join(cell(cols[i], 1, i, True) for i in range(5)) + "</row>"]
    for n, (serial, sp500, tb, rf, erp) in enumerate(rows, start=2):
        xml_rows.append(f'<row r="{n}">' + "".join(
            cell(cols[i], n, v) for i, v in enumerate((serial, sp500, tb, rf, erp))) + "</row>")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook><workbookPr date1904="{int(date1904)}"/></workbook>')
        z.writestr("xl/sharedStrings.xml", "<sst>" + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", "<worksheet><sheetData>" + "".join(xml_rows) + "</sheetData></worksheet>")
    return buf.getvalue()


def test_parse_workbook_reads_the_latest_month():
    # 44834 in the 1904 system = 2026-10-01 (Damodaran's file uses 1904 dates)
    data = _xlsx([(44804, 7686.14, 0.0475, 0.0453, 0.0409), (44834, 7652, 0.0529, 0.0507, 0.0370)])
    rows = ir.parse_workbook(data)
    assert rows[-1] == {"date": "2026-10-01", "sp500": 7652.0, "tbond": 0.0529,
                        "erp": 0.0370, "expected_return": 0.0899}


def test_current_falls_back_to_the_last_known_figure_dated():
    ir._cache.clear()
    def boom():
        raise OSError("offline")
    got = ir.current(fetch=boom, now=1.0)
    assert got["source"] == "last known" and got["expected_return"] == ir.LAST_KNOWN["expected_return"]
    ir._cache.clear()
    good = ir.current(fetch=lambda: _xlsx([(44834, 7652, 0.0529, 0.0507, 0.0370)]), now=2.0)
    assert good["source"].startswith("Damodaran") and good["date"] == "2026-10-01"
    assert "8.99%" in ir.label(good) and "Oct 2026" in ir.label(good)
    ir._cache.clear()


def _cfg(price):
    return {
        "stock_price": price, "shares_outstanding": 100.0, "base_revenue": 1000.0,
        "base_year": 2025, "revenue_growth": [0.06] * 10, "op_margins": [0.25] * 10,
        "terminal_growth": 0.03, "terminal_margin": 0.25, "sales_to_capital": 2.0,
        "tax_rate": 0.21, "cash_bridge": 0.0, "securities": 0.0, "equity_investments": 0.0,
        "minority_interest": 0.0, "unfunded_pension": 0.0, "debt_market_value": 0.0,
        "debt_breakdown": [], "margin_of_safety": 0.2, "discount_mode": "hurdle",
        "erp": 0.047, "risk_free_rate": 0.0465, "sector_betas": [["X", 1.0, 1.0]],
        "equity_market_value": price * 100.0, "credit_spread": 0.01,
    }


def test_implied_return_is_the_rate_that_gives_back_the_price():
    fair_at_9 = dc.compute_intrinsic_value(dict(_cfg(1.0), hurdle_rate=0.09))["intrinsic_value"]
    r = dc.implied_return(_cfg(fair_at_9))
    assert r == pytest.approx(0.09, abs=1e-4)
    # a higher price means a lower return, and a stored per-year hurdle is ignored
    assert dc.implied_return(dict(_cfg(fair_at_9 * 1.5), wacc_per_year=[0.2] * 10)) < 0.09
    assert dc.implied_return(dict(_cfg(0.0))) is None


def test_summary_line_compares_with_the_index():
    idx = {"expected_return": 0.0899, "date": "2026-10-01"}
    assert "the index is the better buy" in sp.return_vs_index_html(0.064, idx)
    assert "beats the index by 1.0 points" in sp.return_vs_index_html(0.0999, idx)
    assert "about the same" in sp.return_vs_index_html(0.085, idx)
    assert sp.return_vs_index_html(None, idx) == ""
    html = sp.verdict_section_html("", {}, 100.0, {}, implied=0.064, index=idx)
    assert "Priced to return 6.4% a year" in html
