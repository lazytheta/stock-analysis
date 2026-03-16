# Coding Standards

## Linting

This project uses **ruff** for linting. Config is in `ruff.toml`.

```bash
python3 -m ruff check .           # lint all files
python3 -m ruff check --fix .     # auto-fix safe issues
python3 -m ruff check path/to.py  # lint single file
```

Claude hooks run ruff automatically on every file edit and at session end.

## Rules

### General
- No unused imports — clean them up
- No unused variables — remove or prefix with `_`
- No undefined names — all imports must be explicit
- No duplicate dict keys
- Re-raise exceptions with `from` in except blocks

### Streamlit-specific
- All HTML in `st.markdown(..., unsafe_allow_html=True)` — never `st.html()` (iframe issues)
- Use `st.session_state` for all cross-rerun state
- Cache expensive calls with `@st.cache_data` or `@st.cache_resource`
- Use `st.form()` for multi-input submissions to avoid reruns

### Testing
- Tests must run fully offline with mocks (~0.1s)
- Run: `python3 -m pytest test_tastytrade_api.py test_ibkr_api.py -v`
- Use `sys.modules[...] = mock` (not `setdefault`) for module-level mocks
- Always `del sys.modules["module"]` before reimport to ensure fresh mocks

### DCF Models
- All Excel values must use formulas, never hardcoded Python values
- Blue font = editable inputs, Black = formulas, Green = notes
- SBC deducted from FCFF → use GROSS buyback rate for shares
- Always include Peer Comparison tab
- Read `SKILL.md` before building any DCF

### Security
- Never commit `.env` files or API keys
- Use Supabase Row Level Security for all user data
- Broker connections are read-only — never request write/trade permissions
- All credentials encrypted in Supabase with per-user isolation
