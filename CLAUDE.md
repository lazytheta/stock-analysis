# Stock Analysis — DCF Valuation Models

## Project Overview
Automated DCF valuation tool for public companies. Uses Damodaran methodology with Sales-to-Capital reinvestment, mid-year discounting, weighted sector betas, SBC deduction, and buyback-adjusted shares.

## Key Files
- `dcf_template.py` — Main template (~1500 lines). Call `build_dcf_model(config, output_path)` to generate Excel.
- `SKILL.md` — Full methodology, config reference, and workflow documentation. **Read this before building any DCF.**
- `configs/` — Saved company configs (reuse instead of re-extracting data).
- `msft_config.py` — Example config file.

## Quick Start
```python
exec(open('dcf_template.py').read())
config = { ... }  # See SKILL.md for full config reference
build_dcf_model(config, "output/TICKER_dcf.xlsx")
```

## Workflow
1. Check `configs/` for existing config → reuse if available
2. If new company: gather data (10-K, web search for betas/rates/peers)
3. Build config dict per SKILL.md reference
4. Run `build_dcf_model(config, path)`
5. Save config to `configs/<ticker>_config.py`

## Rules
- All Excel values must use formulas, never hardcoded Python values
- SBC deducted from FCFF → use GROSS buyback rate for shares
- Blue font = editable inputs, Black = formulas, Green = notes
- Always include Peer Comparison tab (needs `peers` in config)
- Dynamic Bull/Bear scenarios linked by formulas to Base Case
- Margin of Safety default: 20%
