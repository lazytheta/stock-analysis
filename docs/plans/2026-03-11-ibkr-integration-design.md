# IBKR API Integration Design

## Goal

Add Interactive Brokers as a second broker integration with full feature parity to Tastytrade. Users can connect both brokers and switch between them.

## Architecture: Adapter Pattern

Keep `tastytrade_api.py` unchanged. Build `ibkr_api.py` with the same function signatures. Add `broker_adapter.py` as a thin routing layer that calls the correct module based on the active broker.

### New files

- `ibkr_api.py` — All IBKR API functions, returns same data structures as TT
- `broker_adapter.py` — Routes calls to TT or IBKR based on `st.session_state["active_broker"]`

### Modified files

- `streamlit_app.py` — Import from `broker_adapter` instead of `tastytrade_api`, add account switcher in sidebar, update welcome page and Settings
- `config_store.py` — Store IBKR credentials (same `user_credentials` table)

## Authentication

Library: `ibkr_web_client` (OAuth 1.0a, no local gateway required).

User provides in Settings:
- Consumer Key
- Access Token + Access Token Secret
- Encryption key + Signing key (RSA)

Stored in `user_credentials` table per user (RLS protected), same pattern as TT refresh token.

## IBKR API Functions (parity with TT)

| Function | IBKR source | TT equivalent |
|---|---|---|
| `fetch_portfolio_data()` | Positions + transactions, wheel detection | `fetch_portfolio_data()` |
| `fetch_account_balances()` | Account summary | `fetch_account_balances()` |
| `fetch_current_prices()` | Yahoo Finance (shared) | `fetch_current_prices()` |
| `fetch_portfolio_greeks()` | Positions with Greeks | `fetch_portfolio_greeks()` |
| `fetch_margin_requirements()` | Account summary | `fetch_margin_requirements()` |
| `fetch_net_liq_history()` | Performance endpoint | `fetch_net_liq_history()` |
| `fetch_option_chain()` | Market data endpoint | `fetch_option_chain()` |

All functions return the same data structures as the TT versions. The `cost_basis_dict` format is identical: `total_credits`, `total_debits`, `dividends`, `shares_held`, `option_pl`, `equity_cost`, `total_pl`, `adjusted_cost`, `cost_per_share`, `trades[]`, `wheels[]`.

Wheel detection reuses the same `_detect_wheels()` logic after normalizing IBKR trades to the standard trade record format.

## Broker Adapter

```python
# broker_adapter.py
def get_active_broker():
    return st.session_state.get("active_broker", "tastytrade")

def fetch_portfolio_data():
    if get_active_broker() == "ibkr":
        return ibkr_api.fetch_portfolio_data(...)
    return tastytrade_api.fetch_portfolio_data(...)
```

Same pattern for all `fetch_*` functions. Functions that don't depend on a broker (like `fetch_current_prices`, `fetch_ticker_profiles`) can be shared directly.

## UI Changes

### Sidebar account switcher
- `st.selectbox` below navigation, shows only connected brokers
- On switch: clear cached portfolio data from session state, rerun

### Welcome page
- Add broker choice: Tastytrade or Interactive Brokers
- Both with "Connect" button leading to Settings

### Settings page
- New IBKR section alongside existing TT section
- Step-by-step instructions for creating OAuth credentials in IBKR Client Portal
- Input fields for Consumer Key, Access Token, Access Token Secret
- Upload/paste for encryption and signing keys
- Read-only security messaging
- Disconnect button

### Page guards
- Replace `_get_tt_token()` checks with `_has_active_broker()` via adapter
- No broker connected → welcome page
- Broker connected → load data via adapter

## Multi-broker behavior

- Users can connect both TT and IBKR simultaneously
- Sidebar switcher shows only connected brokers
- Viewing one broker at a time, never merged
- Switching clears cached data and reloads
