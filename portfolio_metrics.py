"""Portfolio-level deployment figures: how much is invested, how much is left.

Separate from the page that renders it so the arithmetic can be tested without
a Streamlit runtime — these numbers drive a "do I have anything left to buy
with" decision, and being wrong is worse than being absent.
"""

# A position within this fraction of its target counts as full. 4.7% of the
# portfolio against a 5% target is a full position in every sense that matters,
# and listing it as needing a top-up would bury the names that genuinely have
# room.
FILL_BAND = 0.90

# One full position as a percent of the portfolio, until the user says
# otherwise. 5% is twenty positions — enough concentration to matter, few
# enough to follow.
DEFAULT_TARGET_POS_PCT = 5.0


def has_option_legs(trades):
    """True when this ticker has ever had an option written against it.

    A wheel is shares plus options. detect_wheels returns a cycle for any share
    position — a plain buy sits in an "active" one — so a cycle is not evidence
    of a wheel. Without this, an outright purchase like NFLX or MSFT was given
    an adjusted basis and a premium column describing a trade never made.
    """
    return any("Option" in (t.get("instrument_type") or "") for t in trades)


def _equity_lots(trades):
    """Yield (trade, quantity, price, is_buy) for real equity trades.

    Dividends and other cash movements arrive as Equity rows with no quantity.
    They buy and sell nothing, and letting them through is how a dividend debit
    ended up raising the cost basis of shares it never bought, while a dividend
    credit was booked as equity profit that the dividend total already counted.
    """
    for t in trades:
        if t.get("instrument_type") != "Equity":
            continue
        if (t.get("type") or "") == "Money Movement":
            continue
        qty = t.get("quantity") or 0.0
        if not qty:
            continue
        price = t.get("price") or (abs(t.get("net_value") or 0.0) / qty)
        if (t.get("type") or "") == "Receive Deliver":
            is_buy = (t.get("net_value") or 0.0) < 0      # assignment in
        else:
            is_buy = "Buy" in (t.get("action") or "")
        yield t, qty, price, is_buy


def fifo_realized(trades):
    """Realized P/L per equity sale, oldest lot first.

    Returns [{"date", "quantity", "realized"}] in trade order.

    FIFO because that is the lot relief the broker applied: Tastytrade booked
    IBIT's August sale at -388.10, and a running-average walk made it -321.94.
    Two numbers for one sale means the app cannot be reconciled against the
    statement it sits next to.

    A sale with no lot to match against realizes nothing rather than booking
    its whole proceeds as profit — history can start mid-position, and a
    missing purchase is not a gain.
    """
    lots = []      # [remaining_qty, price]
    sales = []
    for t, qty, price, is_buy in _equity_lots(trades):
        if is_buy:
            lots.append([qty, price])
            continue
        remaining = qty
        cost = 0.0
        matched = 0.0
        while remaining > 0 and lots:
            take = min(lots[0][0], remaining)
            cost += take * lots[0][1]
            lots[0][0] -= take
            remaining -= take
            matched += take
            if lots[0][0] <= 0:
                lots.pop(0)
        if not matched:
            continue
        # Proceeds for the shares actually matched, so a partially matched sale
        # is not credited with cash from shares it could not account for.
        proceeds = abs(t.get("net_value") or 0.0) * (matched / qty)
        sales.append({
            "date": t.get("date"),
            "quantity": matched,
            "realized": proceeds - cost,
        })
    return sales


# Beyond this multiple of your own fair value, the number is not a valuation
# disagreement — it is a stale model or an unadjusted split. GEV sits at 1,133
# against a stored 33. Printing "34x overvalued" would drown every real signal
# in the column, so such a row is treated as having no valuation at all.
IMPLAUSIBLE_FV_MULTIPLE = 5.0


def valuation_stance(price, fv_low, fv_mid, fv_high):
    """Where the price sits against your own fair-value band, or None.

    Returns {"stance": "below_band" | "in_band" | "above_band", "vs_mid": pct}.

    None whenever the band cannot be trusted: a missing valuation, a negative
    fair value (AXP, VLO, ABBV, BROS and AXON all carry one), an out-of-order
    band, or a price a multiple away from it. A hold-or-sell column has to be
    silent where it does not know, because a wrong signal here is acted on.
    """
    if not price or price <= 0:
        return None
    if not (fv_low and fv_mid and fv_high):
        return None
    if min(fv_low, fv_mid, fv_high) <= 0:
        return None
    if not fv_low <= fv_mid <= fv_high:
        return None
    if price > fv_mid * IMPLAUSIBLE_FV_MULTIPLE or price * IMPLAUSIBLE_FV_MULTIPLE < fv_mid:
        return None

    if price > fv_high:
        stance = "above_band"
    elif price < fv_low:
        stance = "below_band"
    else:
        stance = "in_band"
    return {"stance": stance, "vs_mid": (price / fv_mid - 1) * 100}


# Share counts are floats and T212 deals in fractions, so an exact comparison
# would fail on rounding alone.
SHARE_TOLERANCE = 0.01


def lots_cover(lot_shares, shares_held):
    """True when the trade history accounts for the shares actually held.

    A broker's history can start after the position did — a transfer in, or an
    API that only returns recent orders. A FIFO basis derived from partial lots
    is a confident wrong purchase price, which is worse than falling back to
    the broker's own average.
    """
    return abs((lot_shares or 0.0) - (shares_held or 0.0)) <= SHARE_TOLERANCE


def average_buy_price(trades):
    """Average price paid across every purchase, held or not.

    held_share_cost() returns nothing once the last share is sold, so a closed
    position had no price to show. What you paid does not stop being a fact
    when you sell.
    """
    cost = shares = 0.0
    for _t, qty, price, is_buy in _equity_lots(trades):
        if is_buy:
            cost += qty * price
            shares += qty
    return (cost / shares) if shares else 0.0


def hindsight(trades, current_price):
    """What the shares you sold would be worth today, or None.

    Returns {"shares_sold", "proceeds", "value_now", "delta"}, where delta is
    what holding on would have added — positive means the sale gave something
    up, negative means it saved you.

    Only the sales that closed the LATEST position count. TTD had 100 shares
    sold in March 2024, then 250 bought back over the following year and sold
    in August 2026. Adding those together claimed $6,845 saved by selling, but
    350 shares were never held at once and the 2024 round trip was a separate
    position: comparing them to one of today's prices compares two things that
    never coexisted.

    None when nothing was sold (there is no counterfactual, and zero would read
    as "selling made no difference") or when the name cannot be priced today
    (guessing would put an invented gain on the card).
    """
    if not current_price or current_price <= 0:
        return None

    shares = 0.0
    shares_sold = proceeds = sale_value = 0.0
    closed_on = None
    for t, qty, price, is_buy in _equity_lots(trades):
        if is_buy:
            # Going from flat back to invested starts a new position, so
            # anything sold before it belonged to a different one.
            if shares <= 0:
                shares_sold = proceeds = sale_value = 0.0
                closed_on = None
            shares += qty
            continue
        shares -= qty
        shares_sold += qty
        # The trade's own cash, so fees are counted the way they were paid.
        # Price times shares, not the cash that landed. The other side of the
        # comparison is a hypothetical holding valued at today's price with no
        # costs at all, so subtracting fees from one side only would compare
        # two different things — and it put 169.95 where AMAT was called away
        # at exactly 170.00.
        proceeds += (qty * price) or abs(t.get("net_value") or 0.0)
        sale_value += qty * price
        closed_on = t.get("date") or closed_on
    if not shares_sold:
        return None
    value_now = shares_sold * current_price
    return {
        "shares_sold": shares_sold,
        "proceeds": proceeds,
        "value_now": value_now,
        "delta": value_now - proceeds,
        # The two prices the comparison rests on. Averaged over the closing
        # sales, so a position sold in pieces still reports one exit price.
        "sale_price": (sale_value / shares_sold) if sale_value else (proceeds / shares_sold),
        "price_now": current_price,
        # When it was sold. GOOGL reads $16,531 given up, which is true and
        # unreadable without knowing that is a twenty-month-old decision.
        "closed_on": closed_on,
    }


def open_lots(trades):
    """The share lots still held, oldest first: [{quantity, price, date}].

    Same FIFO walk as everything else here, but keeping each lot's purchase
    date — relative performance is measured from the day the money went in, and
    a lot without its date cannot be compared to anything.
    """
    lots = []
    for t, qty, price, is_buy in _equity_lots(trades):
        if is_buy:
            lots.append({"quantity": qty, "price": price, "date": t.get("date")})
            continue
        remaining = qty
        while remaining > 0 and lots:
            take = min(lots[0]["quantity"], remaining)
            lots[0]["quantity"] -= take
            remaining -= take
            if lots[0]["quantity"] <= 0:
                lots.pop(0)
    return lots


def _close_on_or_before(closes, day):
    """The index close for `day`, or the nearest earlier one.

    Buy on a Saturday and there is no close for that date; skipping the lot
    would drop it from the comparison without saying so.
    """
    if not closes or day is None:
        return None
    if day in closes:
        return closes[day]
    earlier = [d for d in closes if d <= day]
    return closes[max(earlier)] if earlier else None


def relative_performance(lots, current_price, index_closes, today):
    """How the position has done against the index, per lot, money-weighted.

    Each lot faced its own stretch of market, so a January purchase and a
    March top-up are compared to different index windows and then weighted by
    cost. Averaging the two returns equally would let a small late top-up
    decide the verdict on a large long-held position.

    Price return on both sides — dividends are counted in neither, so the
    comparison stays like-for-like.

    A lot older than the index history we hold is reported through
    `uncovered_cost` rather than anchored to the oldest close available, which
    would understate the index and hand the position free alpha.
    """
    cost = index_value = weighted_days = uncovered = 0.0
    for lot in lots:
        lot_cost = lot["quantity"] * lot["price"]
        start = _close_on_or_before(index_closes, lot.get("date"))
        if not start or not lot.get("date"):
            uncovered += lot_cost
            continue
        cost += lot_cost
        index_value += lot_cost * (index_closes[max(index_closes)] / start)
        weighted_days += lot_cost * (today - lot["date"]).days

    if cost <= 0:
        return {"position_return": None, "index_return": None, "alpha": None,
                "days_held": None, "uncovered_cost": uncovered}

    shares = sum(lot["quantity"] for lot in lots
                 if _close_on_or_before(index_closes, lot.get("date")))
    position_return = (current_price * shares / cost - 1) * 100
    index_return = (index_value / cost - 1) * 100
    return {
        "position_return": position_return,
        "index_return": index_return,
        "alpha": position_return - index_return,
        "days_held": round(weighted_days / cost),
        "uncovered_cost": uncovered,
    }


def track_record(trades, current_price, index_closes, today,
                 option_pl=0.0, dividends=0.0):
    """Every lot ever bought, against the index over its own days.

    relative_performance stops at the lots still held. That leaves out the
    positions that were sold -- exactly where a wheel cycle or a change of
    mind shows what it cost. Here every purchase becomes one or more
    "pieces": the shares sold later end their window on the sale date at the
    sale price, the shares still held end it today at the current price.
    Each piece faces the index over the same days, weighted by its cost.

    Two answers, both in percent:

      price      what the shares did, versus what the index did
      total      the same, but with the option premium and the dividends of
                 this name added to the position side. That is what the
                 strategy actually earned on the capital it tied up; the
                 difference with `price` is what the premium contributed.

    A piece older than the index history is reported through uncovered_cost
    rather than anchored to the oldest close we have. `closed` is True when
    nothing is held any more.
    """
    lots = []       # [remaining_qty, price, date]
    pieces = []     # (cost, start, end, value)
    for t, qty, price, is_buy in _equity_lots(trades):
        if is_buy:
            lots.append([qty, price, t.get("date")])
            continue
        remaining, matched = qty, 0.0
        proceeds_total = abs(t.get("net_value") or 0.0)
        while remaining > 0 and lots:
            take = min(lots[0][0], remaining)
            proceeds = proceeds_total * (take / qty) if qty else 0.0
            pieces.append((take * lots[0][1], lots[0][2], t.get("date"), proceeds))
            lots[0][0] -= take
            remaining -= take
            matched += take
            if lots[0][0] <= 0:
                lots.pop(0)
    for qty_left, price, day in lots:
        if qty_left > 0:
            pieces.append((qty_left * price, day, None, qty_left * (current_price or 0.0)))

    latest_close = index_closes[max(index_closes)] if index_closes else None
    cost = value = index_value = weighted_days = uncovered = 0.0
    for piece_cost, start, end, piece_value in pieces:
        idx_start = _close_on_or_before(index_closes, start) if start else None
        idx_end = (_close_on_or_before(index_closes, end) if end else latest_close)
        if not idx_start or not idx_end:
            uncovered += piece_cost
            continue
        cost += piece_cost
        value += piece_value
        index_value += piece_cost * (idx_end / idx_start)
        weighted_days += piece_cost * ((end or today) - start).days

    closed = not any(q > 0 for q, _, _ in lots)
    if cost <= 0:
        return {"price_return": None, "index_return": None, "alpha": None,
                "total_return": None, "total_alpha": None, "days_held": None,
                "closed": closed, "cost": 0.0, "uncovered_cost": uncovered}

    price_return = (value / cost - 1) * 100
    index_return = (index_value / cost - 1) * 100
    total_return = ((value + (option_pl or 0.0) + (dividends or 0.0)) / cost - 1) * 100
    return {
        "price_return": price_return,
        "index_return": index_return,
        "alpha": price_return - index_return,
        "total_return": total_return,
        "total_alpha": total_return - index_return,
        "days_held": round(weighted_days / cost),
        "closed": closed,
        "cost": cost,
        "uncovered_cost": uncovered,
    }


def held_share_cost(trades):
    """FIFO cost of the shares still held: (total_cost, shares).

    Summing every buy in the cycle answers "what did I pay for everything I
    ever bought", which stops being the purchase price the moment part of the
    position is sold. IBIT — assigned 100 at 56.00, added 20 at 35.89, sold 20
    at 36.60 — came out at 52.65 for 120 shares of which 20 were gone. FIFO
    retires the oldest lot first and gives 51.98, matching the broker.
    """
    lots = []  # [remaining_qty, price]
    for _t, qty, price, is_buy in _equity_lots(trades):
        if is_buy:
            lots.append([qty, price])
            continue
        # A sale retires the oldest lots. History can start mid-position, so a
        # sale with nothing to match against simply finds no lot rather than
        # driving the holding negative.
        remaining = qty
        while remaining > 0 and lots:
            take = min(lots[0][0], remaining)
            lots[0][0] -= take
            remaining -= take
            if lots[0][0] <= 0:
                lots.pop(0)

    shares = sum(lot[0] for lot in lots)
    cost = sum(lot[0] * lot[1] for lot in lots)
    return cost, shares


def display_basis(net_cash_per_share):
    """Turn a signed per-share cash flow into the price a column should show.

    Cost basis is carried through the app as cash: buying shares is money out,
    so it is negative, and premiums collected push it back up. A column headed
    "Wheel Basis" wants the price, which is the negation.

    Negation, not abs(): once premiums exceed what the shares cost, the basis
    really is below zero — you have been paid to hold them. abs() would print
    that as a cost and invert the meaning of the one case worth spotting.
    """
    return -net_cash_per_share


def compute_deployment(held, net_liq, cash, target_pct,
                       prices=None, buy_prices=None):
    """Summarise how much of the portfolio is committed and what is left.

    held        {key: position} with "market_value" and optionally "symbol"
    net_liq     total account value across brokers
    cash        actual cash across brokers — NOT net_liq minus market value.
                At a margin broker the remainder also carries short option
                value and any borrowing, so calling it dry powder would promise
                money that cannot be spent.
    target_pct  size of one full position, in percent of net_liq
    prices      {symbol: current price}, for the below-buy-price count
    buy_prices  {symbol: watchlist buy price}; names absent from it are left
                out of the count entirely — no valuation is not the same as
                "not a buy"

    Returns a dict of plain numbers; the page formats them.
    """
    invested = sum(d.get("market_value") or 0.0 for d in held.values())
    target = net_liq * (target_pct / 100.0) if net_liq > 0 else 0.0

    full_count = 0
    partial = []
    for key, data in held.items():
        mv = data.get("market_value") or 0.0
        if target <= 0 or mv >= target * FILL_BAND:
            full_count += 1
            continue
        partial.append({
            "ticker": data.get("symbol") or key,
            "key": key,
            "market_value": mv,
            # Gap to target, floored at zero: an oversized winner needs no
            # cash and must not read as a credit against another name's
            # shortfall.
            "gap": max(target - mv, 0.0),
        })
    partial.sort(key=lambda p: p["gap"], reverse=True)

    top_up_cost = sum(p["gap"] for p in partial)
    leftover = cash - top_up_cost
    new_positions = (leftover / target) if target > 0 and leftover > 0 else 0.0

    below_buy, valued_count = [], 0
    prices = prices or {}
    buy_prices = buy_prices or {}
    for key, data in held.items():
        symbol = data.get("symbol") or key
        buy = buy_prices.get(symbol)
        price = prices.get(symbol)
        if not buy or not price:
            continue
        valued_count += 1
        if price < buy:
            below_buy.append(symbol)

    return {
        "invested": invested,
        "deployed_pct": (invested / net_liq * 100.0) if net_liq > 0 else 0.0,
        "dry_powder": cash,
        "dry_powder_pct": (cash / net_liq * 100.0) if net_liq > 0 else 0.0,
        "target": target,
        "full_count": full_count,
        "partial": partial,
        "top_up_cost": top_up_cost,
        "cash_covers_top_ups": cash >= top_up_cost,
        "new_positions_affordable": round(new_positions, 1),
        # Where spending every available dollar on the names already owned
        # would leave you. The point of the card: whether the powder is really
        # dry or already spoken for.
        "fully_deployed_pct": (
            min((invested + cash) / net_liq * 100.0, 100.0) if net_liq > 0 else 0.0
        ),
        "below_buy": below_buy,
        "valued_count": valued_count,
    }


# Fields that are money or share counts, and therefore add up when the same
# holding is split across brokers. Everything else about a merged row is either
# derived from these or has to be decided rather than summed.
_SUMMED_FIELDS = (
    "shares_held", "equity_cost", "option_pl", "total_pl",
    "dividends", "total_credits", "total_debits",
)


# Short forms, used only when two brokers share a row. A single-broker row
# keeps its full name: the abbreviation exists so one merged label cannot set
# the width of a column every other row has to live in — "Tastytrade + Trading
# 212" pushed the weight figure onto a second line. An unknown broker keeps its
# name rather than being guessed at.
_BROKER_SHORT = {
    "Tastytrade": "TT",
    "Trading 212": "T212",
    "Interactive Brokers": "IBKR",
}


def _trade_day(trade):
    """A sortable day for a trade, whatever shape its date arrived in.

    Both brokers hand back date objects today, but a merged list is precisely
    where a string from one side would meet a date from the other and raise on
    comparison. Normalising to YYYY-MM-DD sorts either, and a trade with no
    date sorts first rather than taking the list down.
    """
    stamp = trade.get("date")
    if stamp is None:
        return ""
    if hasattr(stamp, "strftime"):
        return stamp.strftime("%Y-%m-%d")
    return str(stamp)[:10]


def merge_by_symbol(positions):
    """Collapse rows that are the same holding at different brokers.

    A ticker held at two brokers is two rows in the data — deliberately, since
    that is what the accounts actually say — but one position in every sense
    the owner cares about: one company, one holding, one average price paid.
    Two rows make its weight read half its size and its cost basis read as two
    different answers.

    Grouped on `symbol`, so this knows no ticker names and covers whatever ends
    up at two brokers next. Rows that are alone under their symbol pass through
    untouched, key included.

    Callers merge for a combined view and skip it for a per-broker one: a
    position has to stay checkable against the broker's own statement, and that
    is what the per-broker tabs are for.
    """
    groups = {}
    for key, row in (positions or {}).items():
        groups.setdefault(row.get("symbol") or key, []).append((key, row))

    out = {}
    for symbol, members in groups.items():
        if len(members) == 1:
            key, row = members[0]
            out[key] = row
            continue
        out[symbol] = _merge_rows(symbol, [row for _key, row in members])
    return out


def _merge_rows(symbol, rows):
    """One row out of several for the same instrument."""
    from trade_utils import detect_wheels

    merged = dict(rows[0])
    merged["symbol"] = symbol

    for field in _SUMMED_FIELDS:
        merged[field] = sum((r.get(field) or 0) for r in rows)

    # Oldest first, so FIFO retires the oldest lot of the whole holding rather
    # than the oldest lot at one broker.
    trades = [t for r in rows for t in (r.get("trades") or [])]
    trades.sort(key=_trade_day)
    merged["trades"] = trades
    merged["wheels"] = detect_wheels(trades)

    # Recomputed, never added: the sum of two averages is the average of
    # nothing. Same formulas the per-broker rows were built with.
    merged["adjusted_cost"] = merged["equity_cost"] + merged["option_pl"]
    shares = merged["shares_held"]
    merged["cost_per_share"] = (
        float(merged["adjusted_cost"] / shares) if shares else 0.0)

    merged["broker"] = " + ".join(
        _BROKER_SHORT.get(n, n)
        for n in dict.fromkeys(r["broker"] for r in rows if r.get("broker")))

    # The rows describe one instrument, so a quote either agrees across them or
    # one side simply has none.
    for field in ("current_price", "previous_close", "broker_price", "isin"):
        merged[field] = next(
            (r[field] for r in rows if r.get(field)),
            rows[0].get(field))

    # Only when every row agrees. A blended exchange rate describes no
    # transaction that happened, and absent beats wrong.
    for field in ("currency", "fx_rate"):
        values = {r.get(field) for r in rows}
        if len(values) == 1:
            merged[field] = values.pop()
        else:
            merged.pop(field, None)

    if merged.get("current_price") is not None:
        merged["market_value"] = merged["current_price"] * shares
        merged["total_pl_real"] = merged["total_pl"] + merged["market_value"]
    return merged
