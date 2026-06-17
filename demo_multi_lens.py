"""Demo script for multi-lens fair value (Phase 1).

Runs the full orchestrator on a real-world MSFT config + sample valuation_inputs
and prints the summary in human-readable form. Read-only — does not touch
Supabase, does not persist anything.

Usage:
    python3 demo_multi_lens.py
    python3 demo_multi_lens.py --grid    # use the 4x4 bull/bear grid mode
"""

import argparse
import json
import sys

sys.path.insert(0, "configs")

# Load the MSFT fixture cfg
import msft_config

import valuation_lenses

cfg = dict(msft_config.cfg)

# Inject sample valuation_inputs so the multiples lens activates.
# (In real use, Claude Desktop fills these via the MCP tool.)
cfg["valuation_inputs"] = {
    "forward_eps": 14.20,           # consensus FY2026 EPS
    "historical_fwd_pe": 28.0,      # MSFT 5-yr avg fwd P/E
    "ttm_ebitda": 145_000.0,        # ~$145B TTM EBITDA, in $M
    "target_dividend_yield": 0.008,
    "current_dividend": 3.32,
    "expected_dividend_growth": 0.10,
}

# Add fwd_pe to peers (Phase-1 schema extension).
peer_fwd_pes = {"AAPL": 30.5, "GOOGL": 22.0, "AMZN": 38.0, "META": 23.0}
for p in cfg["peers"]:
    p["fwd_pe"] = peer_fwd_pes.get(p["ticker"])


def fmt_money(x):
    if x is None:
        return "    —"
    return f"${x:7.2f}"


def fmt_pct(x):
    if x is None:
        return "   —"
    return f"{x*100:+6.2f}%"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid", action="store_true", help="use 4x4 scenario grid for DCF lens")
    parser.add_argument("--json", action="store_true", help="print raw JSON summary instead of pretty table")
    args = parser.parse_args()

    summary = valuation_lenses.calculate_multi_lens_valuation(cfg, scenario_grid=args.grid)

    if args.json:
        print(json.dumps(summary, default=str, indent=2))
        return

    print(f"\n{cfg['company']} ({cfg['ticker']}) — Multi-Lens Fair Value")
    print(f"  stock price:       {fmt_money(summary['stock_price'])}")
    print(f"  scenario_grid:     {summary['scenario_grid']}")
    print(f"  calculated at:     {summary['calculated_at']}")
    print()
    print(f"  {'lens':<14} {'fv_low':>9} {'fv_mid':>9} {'fv_high':>9}  {'weight':>6}  {'weight_norm':>11}")
    print(f"  {'-'*14} {'-'*9} {'-'*9} {'-'*9}  {'-'*6}  {'-'*11}")
    for name, lens in summary["lenses"].items():
        if lens is None:
            print(f"  {name:<14} {'(skipped)':>34}")
            continue
        print(
            f"  {name:<14} {fmt_money(lens['fv_low'])} {fmt_money(lens['fv_mid'])} "
            f"{fmt_money(lens['fv_high'])}  {lens['weight']:>6.2f}  {lens['weight_normalized']:>11.3f}"
        )
    print()
    print(f"  weighted FV range: {fmt_money(summary['weighted_fv_low'])} → "
          f"{fmt_money(summary['weighted_fv_mid'])} → {fmt_money(summary['weighted_fv_high'])}")
    print(f"  buy price:         {fmt_money(summary['buy_price'])}")
    print(f"  current vs mid:    {fmt_pct(summary['current_vs_mid'])}")

    # Show details from the multiples lens (the most interesting Phase-1 addition)
    mult = summary["lenses"].get("multiples")
    if mult:
        d = mult["details"]
        print("\n  multiples sub-anchors:")
        print(f"    own fwd P/E × forward_eps: {fmt_money(d['fwd_pe_own'])}")
        print(f"    peer fwd P/E median × eps: {fmt_money(d['fwd_pe_peer_median'])}")
        print(f"    peer EV/EBITDA median:     {fmt_money(d['ev_ebitda_peer_median'])}")
        print(f"    closest peer:              {d['closest_peer']}")
        if d["skipped"]:
            for s in d["skipped"]:
                print(f"    skipped: {s}")

    rev = summary["lenses"].get("reverse_dcf")
    if rev:
        d = rev["details"]
        print("\n  reverse DCF (what's priced in):")
        print(f"    implied growth:  {d['implied_growth']*100:+5.2f}%")
        print(f"    implied margin:  {d['implied_margin']*100:5.2f}%")


if __name__ == "__main__":
    main()
