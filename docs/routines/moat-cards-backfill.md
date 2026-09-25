# Moat Cards backfill — routine prompt

You fill the "Moat Cards" pre-scan section for LazyTheta watchlist tickers that
have a Moat Analysis but no Moat Cards yet. Use only the Lazy-Theta-Remote-MCP
connector. Do not modify, commit or push anything in the repository.

Before enabling: the "Moat Cards" prompt must already be in the user's
prompt library (see `scripts/add_moat_cards_prompt.py`) and the Cloud Run MCP
must be redeployed with `tickers_missing_section` and the Moat Cards save
validation. Without both, step 2a finds no "Moat Cards" prompt to answer.

1. Call `tickers_missing_section(title="Moat Cards", requires="Moat Analysis", limit=10)`.
   If `tickers` is empty, stop and print "Nothing left to backfill."
2. For each ticker:
   a. Call `get_prescan_prompts(ticker)` and take the prompt titled "Moat Cards"
      (its {prior:Moat Analysis} is already filled in).
   b. Call `get_fundamentals(ticker)` for the numbers you cite.
   c. Answer the prompt exactly as it asks: one fenced JSON block, five cards in
      the listed order.
   d. Save with `save_prescan_section(ticker, "Moat Cards", <the JSON block>)`.
      If the server refuses, fix what the error names and save once more; if it
      refuses again, note the ticker and the reason and move on.
3. Print a summary: tickers filled, tickers skipped with reasons, and `remaining`.
