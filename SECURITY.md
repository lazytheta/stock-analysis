# Security

## Reporting Vulnerabilities
If you discover a security vulnerability, please open a private issue on GitHub or email security@lazytheta.io.

## Security Measures
- User accounts isolated via Supabase Row Level Security (RLS)
- Input sanitization on all user-provided ticker symbols and numeric inputs
- API error messages sanitized to prevent information leakage
- Dependencies are version-pinned to exact versions
- XSRF protection enabled
- Rate limiting on external API calls (10 lookups/minute per session)
- HTTPS enforced via Streamlit Cloud
- Session data clearable by user; automatically destroyed on tab close

## What We Store
Stored per-user in Supabase (isolated via RLS):
- Watchlist configs (saved DCF configurations per ticker)
- User preferences (display settings)
- Tastytrade refresh token (read-only, revocable)

Not stored:
- Portfolio positions, balances, or transaction history (fetched live each session)
- Market data or stock prices
- DCF calculation results
- Your Tastytrade password

## What We Don't Control (Streamlit Cloud limitations)
- Custom HTTP security headers (CSP, HSTS, X-Frame-Options)
- Server-level rate limiting
- Custom WAF rules

## Data Flow
User signs in (Supabase Auth) → Saved configs loaded (RLS-isolated) → Market data fetched from public APIs (SEC EDGAR, Yahoo Finance) → Portfolio data fetched from Tastytrade (read-only token) → Calculations run server-side in Streamlit session → Results displayed → Session data destroyed on tab close
