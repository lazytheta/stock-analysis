# Stock Analysis — Lazy Theta

Options portfolio tracker + DCF valuation tool. Streamlit app at lazytheta.io.

## Quick Reference

| What | Where |
|------|-------|
| Architecture & module map | `docs/architecture.md` |
| Coding standards & lint rules | `docs/coding-standards.md` |
| Testing guide | `docs/testing.md` |
| DCF methodology & config reference | `SKILL.md` |
| Saved company configs | `configs/` |

## Enforced Rules

1. **Lint before commit**: `python3 -m ruff check .` must pass (config in `ruff.toml`)
2. **Tests before commit**: `python3 -m pytest test_tastytrade_api.py test_ibkr_api.py -v` — 81 tests, all must pass
3. **No secrets in code**: `.env` is gitignored. Use `os.environ` or `st.secrets`
4. **Read-only broker access**: Never request write/trade permissions

## Hooks (automatic)

- **On file edit**: ruff lints the changed file
- **On session stop**: ruff lints full repo + runs test suite
