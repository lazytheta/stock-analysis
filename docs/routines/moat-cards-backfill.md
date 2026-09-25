# Question cards backfill — routine prompt

You fill the "Moat Cards" and "Risk Cards" pre-scan sections for LazyTheta
watchlist tickers that have the underlying analysis but no cards yet. Use
only the Lazy-Theta-Remote-MCP connector. Do not modify, commit or push
anything in the repository.

Before enabling: both the "Moat Cards" and "Risk Cards" prompts must already
be in the user's prompt library (see `scripts/add_moat_cards_prompt.py`) and
the Cloud Run MCP must be redeployed with `tickers_missing_section` and the
Moat Cards / Risk Cards save validation. Without both, step 2a finds no
matching prompt to answer.

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
3. With whatever remains of the budget of 10 names (10 minus the names done
   in step 2), call `tickers_missing_section(title="Risk Cards",
   requires="Risk Analysis", limit=<remaining>)`. If nothing remains of the
   budget, skip straight to step 5. If `tickers` is empty, note that and
   continue to step 5.
4. For each ticker from step 3:
   a. Call `get_prescan_prompts(ticker)` and take the prompt titled "Risk Cards"
      (its {prior:Risk Analysis} and {prior:SaaSpocalypse Resistance} are
      already filled in).
   b. Call `get_fundamentals(ticker)` for the numbers you cite.
   c. Answer the prompt exactly as it asks: one fenced JSON block, four cards in
      the listed order.
   d. Save with `save_prescan_section(ticker, "Risk Cards", <the JSON block>)`.
      If the server refuses, fix what the error names and save once more; if it
      refuses again, note the ticker and the reason and move on.
5. If both `tickers` lists from steps 1 and 3 were empty, print "Nothing left
   to backfill." Otherwise print a summary: tickers filled (per card set),
   tickers skipped with reasons, and `remaining` for each.
