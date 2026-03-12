"""
Shared trade analysis utilities used by both Tastytrade and IBKR broker modules.
"""


def detect_wheels(trades):
    """
    Detect completed wheel cycles from a ticker's trade list.

    A wheel cycle = all trades from one "shares at 0" to the next.
    Completed when shares return to 0 after being held.
    Whatever remains with shares > 0 is an active (in-progress) wheel.
    """
    cycles = []
    cycle_trades = []
    cycle_pl = 0.0
    shares = 0
    had_shares = False
    cycle_start = None
    pending_complete = False
    pending_end_date = None

    for trade in trades:
        # If wheel was just completed, check if this trade is same-date cleanup
        if pending_complete:
            if trade["date"] == pending_end_date and trade["net_value"] == 0.0:
                # Same-date zero-value cleanup (e.g. option removal) — keep in this cycle
                cycle_trades.append(trade)
                continue
            else:
                # Finalize the completed wheel
                cycles.append({
                    "status": "completed",
                    "start": cycle_start,
                    "end": pending_end_date,
                    "pl": cycle_pl,
                    "num_trades": len(cycle_trades),
                    "trades": cycle_trades,
                })
                cycle_trades = []
                cycle_pl = 0.0
                cycle_start = None
                had_shares = False
                pending_complete = False

        cycle_trades.append(trade)
        cycle_pl += trade["net_value"]

        if cycle_start is None:
            cycle_start = trade["date"]

        # Track share changes (same logic as main categorization)
        inst = trade["instrument_type"]
        txn_type = trade["type"]
        action = trade["action"]
        qty = trade["quantity"]
        net = trade["net_value"]

        if inst == "Equity":
            if txn_type == "Receive Deliver":
                if net < 0:
                    shares += qty
                elif net > 0:
                    shares -= qty
            else:
                if "Buy" in action:
                    shares += qty
                elif "Sell" in action:
                    shares -= qty

        if shares > 0:
            had_shares = True

        # Wheel complete: shares back to 0 after having held some
        # Don't finalize yet — wait to absorb same-date cleanup trades
        if shares == 0 and had_shares:
            pending_complete = True
            pending_end_date = trade["date"]

    # Finalize any pending completed wheel
    if pending_complete:
        cycles.append({
            "status": "completed",
            "start": cycle_start,
            "end": pending_end_date,
            "pl": cycle_pl,
            "num_trades": len(cycle_trades),
            "trades": cycle_trades,
        })
    elif cycle_trades:
        # Remaining trades = active wheel or CSP-only income
        cycles.append({
            "status": "active" if shares > 0 else "options_only",
            "start": cycle_start,
            "end": cycle_trades[-1]["date"],
            "pl": cycle_pl,
            "num_trades": len(cycle_trades),
            "trades": cycle_trades,
        })

    return cycles
