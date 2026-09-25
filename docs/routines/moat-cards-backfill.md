# Question cards backfill — routine prompt

You fill the "Moat Cards", "Risk Cards" and "Business Cards" pre-scan
sections for LazyTheta watchlist tickers that have the underlying analysis
but no cards yet. Use only the Lazy-Theta-Remote-MCP connector, plus the
SEC-MCP connector for `GetRevenueBreakdown` in step 5b if it is available. Do
not modify, commit or push anything in the repository.

Before enabling: the "Moat Cards", "Risk Cards" and "Business Cards" prompts
must already be in the user's prompt library. `scripts/add_moat_cards_prompt.py`
only adds "Moat Cards" — "Risk Cards" and "Business Cards" come from the
app's own `DEFAULT_AI_PROMPTS` (added the first time the user opens the
prompt editor) or are added directly by the maintainer. The Cloud Run MCP
must also be redeployed with `tickers_missing_section` and the Moat Cards /
Risk Cards / Business Cards save validation. Without all of this, step 2a (or
5a) finds no matching prompt to answer.

1. Call `tickers_missing_section(title="Moat Cards", requires="Moat Analysis", limit=10)`.
2. For each ticker:
   a. Call `get_prescan_prompts(ticker)` and take the prompt titled "Moat Cards"
      (its {prior:Moat Analysis} is already filled in).
   b. Call `get_fundamentals(ticker)` for the numbers you cite.
   c. Answer the prompt exactly as it asks: one fenced JSON block, five cards in
      the listed order.
   d. Save with `save_prescan_section(ticker, "Moat Cards", <the JSON block>)`.
      If the server refuses, fix what the error names and save once more; if it
      refuses again, note the ticker and the reason and move on.
3. With whatever remains of the budget of 10 names (10 minus the number of
   tickers step 1 returned, filled or skipped), call
   `tickers_missing_section(title="Risk Cards", requires="Risk Analysis",
   limit=<remaining>)`. If that remaining budget is 0, skip step 3 entirely —
   do not call it with limit 0 — and go straight to step 5. If `tickers` is
   empty, note that and continue to step 5.
4. For each ticker from step 3:
   a. Call `get_prescan_prompts(ticker)` and take the prompt titled "Risk Cards"
      (its {prior:Risk Analysis} and {prior:SaaSpocalypse Resistance} are
      already filled in). A ticker without a "SaaSpocalypse Resistance"
      section still gets Risk Cards — its AI-exposure context is simply
      missing, which is acceptable.
   b. Call `get_fundamentals(ticker)` for the numbers you cite.
   c. Answer the prompt exactly as it asks: one fenced JSON block, four cards in
      the listed order.
   d. Save with `save_prescan_section(ticker, "Risk Cards", <the JSON block>)`.
      If the server refuses, fix what the error names and save once more; if it
      refuses again, note the ticker and the reason and move on.
5. With whatever remains of the same budget of 10 (10 minus every ticker
   filled or skipped in steps 1-4), call `tickers_missing_section(title=
   "Business Cards", requires="Business Analysis", limit=<remaining>)`. If
   that remaining budget is 0, skip step 5 entirely — do not call it with
   limit 0 — and go straight to step 6. If `tickers` is empty, note that and
   continue to step 6. For each ticker returned:
   a. Call `get_prescan_prompts(ticker)` and take the prompt titled "Business
      Cards" (its {prior:Business Analysis}, {prior:Moat Analysis} and
      {prior:Key Metrics} are already filled in). The prompt itself lists the
      fixed region vocabulary the "revenue.regions" entries must map onto —
      do not invent region names outside that list.
   b. If the SEC-MCP connector is available, call `GetRevenueBreakdown` for
      the ticker once and use it for the "revenue" segment/geography figures;
      otherwise pull the latest 10-K's segment and geography note via
      `ListFilings` / the filing text.
   c. Call `get_fundamentals(ticker)` for the numbers you cite elsewhere in
      the cards.
   d. Answer the prompt exactly as it asks: one fenced JSON block, with the
      overview/profile/revenue blocks and four cards in the listed order.
      The real "segments" must sum to total_musd and the real "regions"
      shares must sum to 100 — the prompt's own example shows only one entry
      of each.
   e. Save with `save_prescan_section(ticker, "Business Cards", <the JSON
      block>)`. If the server refuses, fix what the error names and save
      once more; if it refuses again, note the ticker and the reason and
      move on.
6. If all three `tickers` lists from steps 1, 3 and 5 were empty, print
   "Nothing left to backfill." Otherwise print a summary: tickers filled (per
   card set), tickers skipped with reasons, and `remaining` for each.
