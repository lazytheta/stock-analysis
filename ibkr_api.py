"""
IBKR API module — fetch portfolio data from Interactive Brokers.
Returns the same data structures as tastytrade_api.py for adapter compatibility.
"""

import logging
import tempfile

import streamlit as st

logger = logging.getLogger(__name__)


def _get_ibkr_client():
    """Create an IBKRHttpClient from stored credentials."""
    from pathlib import Path
    from ibkr_web_client import IBKRConfig, IBKRHttpClient

    creds = st.session_state.get("ibkr_credentials")
    if not creds:
        raise RuntimeError("IBKR credentials not configured")

    # Write key material to temp files (ibkr_web_client expects file paths)
    tmp_dir = tempfile.mkdtemp(prefix="ibkr_")
    enc_path = Path(tmp_dir) / "encryption.pem"
    sig_path = Path(tmp_dir) / "signature.pem"
    dh_path = Path(tmp_dir) / "dhparam.pem"

    enc_path.write_text(creds["ibkr_encryption_key"])
    sig_path.write_text(creds["ibkr_signing_key"])

    # DH params can be generated once; use a default if not provided
    if "ibkr_dh_param" in creds:
        dh_path.write_text(creds["ibkr_dh_param"])
    else:
        import subprocess
        subprocess.run(
            ["openssl", "dhparam", "-out", str(dh_path), "2048"],
            capture_output=True, check=True,
        )

    config = IBKRConfig(
        token_access=creds["ibkr_access_token"],
        token_secret=creds["ibkr_access_token_secret"],
        consumer_key=creds["ibkr_consumer_key"],
        dh_param_path=dh_path,
        dh_private_encryption_path=enc_path,
        dh_private_signature_path=sig_path,
    )
    return IBKRHttpClient(config)


def _get_account_id(client):
    """Get the first brokerage account ID."""
    accounts = client.portfolio_accounts()
    if isinstance(accounts, list) and accounts:
        return accounts[0].get("accountId") or accounts[0].get("id")
    raise RuntimeError("No IBKR accounts found")


# ── Public API (same signatures as tastytrade_api) ──

def fetch_portfolio_data():
    """Fetch IBKR positions and transactions, compute cost basis per ticker.
    Returns: (cost_basis_dict, account_number)
    """
    raise NotImplementedError("IBKR fetch_portfolio_data not yet implemented")


def fetch_account_balances():
    """Fetch IBKR account balances.
    Returns: dict with net_liquidating_value, cash_balance, etc.
    """
    raise NotImplementedError("IBKR fetch_account_balances not yet implemented")


def fetch_margin_requirements():
    """Fetch per-position margin requirements.
    Returns: dict keyed by symbol.
    """
    raise NotImplementedError("IBKR fetch_margin_requirements not yet implemented")


def fetch_margin_for_position(ticker, quantity):
    """Dry-run margin check. IBKR web client lacks this endpoint.
    Returns: None
    """
    return None


def fetch_net_liq_history(time_back="1y"):
    """Fetch net liquidating value history.
    Returns: list of {"time": str, "close": float}
    """
    raise NotImplementedError("IBKR fetch_net_liq_history not yet implemented")


def fetch_portfolio_greeks():
    """Fetch Greeks for open option positions.
    Returns: dict with "positions" list and "totals" dict.
    """
    raise NotImplementedError("IBKR fetch_portfolio_greeks not yet implemented")


def fetch_greeks_and_bwd():
    """Fetch Greeks and Beta-Weighted Delta.
    Returns: (greeks_dict, bwd_dict)
    """
    raise NotImplementedError("IBKR fetch_greeks_and_bwd not yet implemented")


def fetch_beta_weighted_delta():
    """Fetch portfolio Beta-Weighted Delta.
    Returns: dict with positions, portfolio_bwd, spy_price, dollar_per_1pct.
    """
    raise NotImplementedError("IBKR fetch_beta_weighted_delta not yet implemented")


def fetch_yearly_transfers():
    """Fetch net cash transfers by year.
    Returns: dict {year: {"total": float, "months": {month: float}}}
    """
    raise NotImplementedError("IBKR fetch_yearly_transfers not yet implemented")


def fetch_margin_interest():
    """Fetch margin interest charges.
    Returns: dict with current_month, ytd, total, monthly.
    """
    raise NotImplementedError("IBKR fetch_margin_interest not yet implemented")


def fetch_option_chain(ticker, option_type='Put', min_dte=7, max_dte=60,
                       num_strikes=8, fallback_price=0.0):
    """Fetch option chain with Greeks.
    Returns: dict with underlying_price, expirations[].
    """
    raise NotImplementedError("IBKR fetch_option_chain not yet implemented")


def fetch_earnings_dates(tickers):
    """Fetch next earnings dates.
    Returns: dict {ticker: {"date": date, "time": str, "estimated": bool}}
    """
    raise NotImplementedError("IBKR fetch_earnings_dates not yet implemented")
