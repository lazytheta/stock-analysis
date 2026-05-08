"""Shared auto-fetch helpers for valuation_inputs and peer market data.

Used by both the Streamlit refresh flow and the MCP server. Mutates the
config dict in place. Each helper respects the `_auto_filled` precedence
rule: fields in that list (or absent) get overwritten with yfinance values;
user-set fields (present, not in `_auto_filled`) are preserved.
"""

import logging
from datetime import UTC, datetime

import gather_data

logger = logging.getLogger(__name__)


def auto_fill_valuation_inputs(cfg: dict) -> None:
    """Auto-fill `cfg["valuation_inputs"]` from yfinance.

    Combines results from gather_data.fetch_market_inputs (Phase 2-B:
    forward_eps, ttm_ebitda) and gather_data.fetch_historical_multiples
    (Phase 2-B.2: historical_trailing_pe, historical_ev_ebitda, ttm_eps).
    Fields listed in `_auto_filled` or absent are written; user-set fields
    (present, not in _auto_filled) are preserved. Updates `_fetched_at`.
    """
    inputs = cfg.setdefault("valuation_inputs", {})
    auto_filled = list(inputs.get("_auto_filled", []))
    fetched = gather_data.fetch_market_inputs(cfg.get("ticker", ""))
    fetched.update(gather_data.fetch_historical_multiples(cfg.get("ticker", "")))

    for key, value in fetched.items():
        existing = inputs.get(key)
        if existing is None or key in auto_filled:
            inputs[key] = value
            if key not in auto_filled:
                auto_filled.append(key)
        else:
            logger.info(
                "Auto-fill skipped for %s.%s: user-set value preserved",
                cfg.get("ticker", "?"), key,
            )

    inputs["_auto_filled"] = auto_filled
    inputs["_fetched_at"] = datetime.now(UTC).isoformat()


def auto_fill_peer_market_data(cfg: dict) -> None:
    """Auto-fill yfinance fwd_pe and real ev_ebitda for each peer in cfg["peers"].

    fwd_pe: user-set values (present, not in _auto_filled) are preserved.
    ev_ebitda: ALWAYS overwritten when yfinance provides real data. This is an
    intentional Phase-2-B limitation: the existing values come from
    gather_data.fetch_peer_data's oi*1.3 approximation and are never marked
    as _auto_filled, so the standard precedence rule would treat them as
    user-set. To keep the workflow simple we always replace them with the real
    yfinance value.

    Updates peer["_fetched_at"]. Non-dict or ticker-less peers are skipped.
    """
    peers = cfg.get("peers") or []
    fetched_at = datetime.now(UTC).isoformat()

    for peer in peers:
        if not isinstance(peer, dict) or not peer.get("ticker"):
            continue

        auto_filled = list(peer.get("_auto_filled", []))
        enriched = gather_data.enrich_peer_with_market_data(peer)

        for key in ("fwd_pe", "ev_ebitda"):
            yfinance_value = enriched.get(key)
            if yfinance_value is None:
                continue
            original_value = peer.get(key)
            if key == "ev_ebitda" or original_value is None or key in auto_filled:
                peer[key] = yfinance_value
                if key not in auto_filled:
                    auto_filled.append(key)
            else:
                logger.info(
                    "Auto-fill skipped for %s peer %s.%s: user-set value preserved",
                    cfg.get("ticker", "?"), peer["ticker"], key,
                )

        peer["_auto_filled"] = auto_filled
        peer["_fetched_at"] = fetched_at


def auto_fill_dividend_inputs(cfg: dict) -> None:
    """Auto-fill `cfg["valuation_inputs"]` with dividend-history fields.

    Writes ttm_dividend, dividend_5y_cagr, median_5y_yield from
    gather_data.fetch_dividend_history. Respects the same `_auto_filled`
    precedence as auto_fill_valuation_inputs: user-set values are
    preserved on subsequent refreshes. Updates `_fetched_at`.
    """
    inputs = cfg.setdefault("valuation_inputs", {})
    auto_filled = list(inputs.get("_auto_filled", []))
    fetched = gather_data.fetch_dividend_history(cfg.get("ticker", ""))

    for key, value in fetched.items():
        # n_years_available is diagnostic, not a valuation_inputs field
        if key == "n_years_available":
            continue
        existing = inputs.get(key)
        if existing is None or key in auto_filled:
            inputs[key] = value
            if key not in auto_filled:
                auto_filled.append(key)
        else:
            logger.info(
                "Auto-fill skipped for %s.%s: user-set value preserved",
                cfg.get("ticker", "?"), key,
            )

    inputs["_auto_filled"] = auto_filled
    inputs["_fetched_at"] = datetime.now(UTC).isoformat()
