# Architecture

## Module Map

| Module | Purpose | Size |
|--------|---------|------|
| `streamlit_app.py` | Main Streamlit app — all pages, UI, reports | ~7500 lines |
| `auth.py` | Supabase Auth (email/password, Google OAuth, remember-me) | ~420 lines |
| `tastytrade_api.py` | Tastytrade API client (OAuth, positions, transactions, net liq) | ~1200 lines |
| `ibkr_api.py` | Interactive Brokers API client (positions, transactions, net liq) | ~500 lines |
| `broker_adapter.py` | Unified broker interface — wraps tastytrade/ibkr APIs | ~150 lines |
| `dcf_template.py` | DCF Excel model generator (Damodaran methodology) | ~1500 lines |
| `gather_data.py` | Automated data pipeline for DCF inputs (EDGAR, Yahoo, Treasury) | ~1300 lines |
| `dcf_calculator.py` | Pure-Python DCF calculator (no Excel) | ~200 lines |
| `config_store.py` | Supabase CRUD for user configs/credentials | ~150 lines |
| `error_logger.py` | Centralized error logging to Supabase | ~50 lines |
| `trade_utils.py` | Shared trade/position helpers | ~80 lines |

## Data Flow

```
User → Streamlit App → auth.py (Supabase Auth)
                      → broker_adapter.py → tastytrade_api.py / ibkr_api.py
                      → config_store.py (Supabase DB)
                      → dcf_template.py → Excel output
```

## Key Patterns

- **Session state**: All portfolio data cached in `st.session_state` with TTL
- **Broker abstraction**: `broker_adapter.py` normalizes data from TT/IBKR into common format
- **Reports**: Monthly/weekly HTML reports generated inline in `streamlit_app.py` (lines ~5000-5600)
- **Cost basis**: `cost_basis` dict from broker API drives P/L, premium, and wheel tracking calculations
- **OAuth**: Tastytrade uses OAuth 2.0 via external microservice at `oauth-server/`

## External Services

| Service | Purpose | Auth |
|---------|---------|------|
| Supabase | Auth, credentials, error logs, configs | Service role key + anon key |
| Tastytrade API | Positions, transactions, net liq history | OAuth refresh token |
| Interactive Brokers | Positions, transactions, net liq | Credentials (host/port/token) |
| Yahoo Finance | Chart data for unrealized P/L | No auth (public API) |
| SEC EDGAR | 10-K filings for DCF data | No auth (User-Agent header) |
| Treasury.gov | Risk-free rate | No auth |
