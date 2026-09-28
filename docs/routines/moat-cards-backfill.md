# Question cards backfill — routine prompt

You fill the "Moat Cards", "Risk Cards", "Business Cards", "Company
Profile" and "Growth Cards" pre-scan sections for LazyTheta watchlist
tickers that have the underlying analysis but no cards yet. Use only the
Lazy-Theta-Remote-MCP connector, plus the SEC-MCP connector for the
"revenue" block in step 5b and the "consensus" block in step 9b if it is
available. Do not modify, commit or push anything in the repository.

Before enabling: the "Moat Cards", "Risk Cards", "Business Cards", "Company
Profile" and "Growth Cards" prompts must already be in the user's prompt
library. `scripts/add_moat_cards_prompt.py` only adds "Moat Cards" — "Risk
Cards", "Business Cards", "Company Profile" and "Growth Cards" come from
the app's own `DEFAULT_AI_PROMPTS` (added the first time the user opens the
prompt editor) or are added directly by the maintainer. The Cloud Run MCP
must also be redeployed with `tickers_missing_section` and the Moat Cards /
Risk Cards / Business Cards / Company Profile / Growth Cards save
validation. Without all of this, step 2a (or 5a, 7a, or 9a) finds no
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
   limit 0 — and go straight to step 7. If `tickers` is empty, note that and
   continue to step 7. For each ticker returned:
   a. Call `get_prescan_prompts(ticker)` and take the prompt titled "Business
      Cards" (its {prior:Business Analysis}, {prior:Moat Analysis} and
      {prior:Key Metrics} are already filled in when those pre-scan sections
      exist for the ticker; Moat Analysis and Key Metrics feed the "overview"
      and "profile" text and the four cards when present, and a missing one
      shows as "(no prior Moat Analysis available)" or similar in the
      prompt — that is acceptable, answer with what is there). The prompt
      itself lists the fixed region vocabulary the "revenue.regions" entries
      must map onto — do not invent region names outside that list.
   b. SEC-MCP (`GetRevenueBreakdown`, or `ListFilings` / `ReadDocumentLines`
      to read the latest 10-K's segment and geography note directly) is the
      source for the "revenue" block. If the SEC-MCP connector is available,
      use it to fill "revenue" from real filing numbers. If SEC-MCP is
      unavailable, omit the "revenue" key entirely — per the prompt's own
      instruction, never estimate the numbers by hand.
   c. Call `get_fundamentals(ticker)` for the numbers you cite elsewhere in
      the cards.
   d. Answer the prompt exactly as it asks: one fenced JSON block, with the
      overview/profile/revenue blocks and four cards in the listed order.
      The real "segments" must sum to total_musd and the real "regions"
      shares must sum to 100 — the prompt's own example shows only one entry
      of each; combine any of the company's own regions that map onto the
      same fixed region into one entry instead of listing it twice.
   e. Save with `save_prescan_section(ticker, "Business Cards", <the JSON
      block>)`. If the server refuses, fix what the error names and save
      once more; if it refuses again, note the ticker and the reason and
      move on.
6. With whatever remains of the same budget of 10 (10 minus every ticker
   filled or skipped in steps 1-5), call `tickers_missing_section(title=
   "Company Profile", requires="Business Analysis", limit=<remaining>)` —
   the same `requires` as step 5's Business Cards set. If that remaining
   budget is 0, skip step 6 entirely — do not call it with limit 0 — and go
   straight to step 8. If `tickers` is empty, note that and continue to
   step 8.
7. For each ticker from step 6:
   a. Call `get_prescan_prompts(ticker)` and take the prompt titled "Company
      Profile" (its {prior:Business Analysis} and {prior:Moat Analysis} are
      already filled in when those pre-scan sections exist for the ticker;
      a missing one shows as "(no prior Moat Analysis available)" or
      similar in the prompt — that is acceptable, answer with what is
      there).
   b. Answer the prompt exactly as it asks: one fenced JSON block with the
      eight fields. Never estimate — use null for founded/employees when the
      filing doesn't state them.
   c. Save with `save_prescan_section(ticker, "Company Profile", <the JSON
      block>)`. If the server refuses, fix what the error names and save
      once more; if it refuses again, note the ticker and the reason and
      move on.
8. With whatever remains of the same budget of 10 (10 minus every ticker
   filled or skipped in steps 1-7), call `tickers_missing_section(title=
   "Growth Cards", requires="Long-Term Potential", limit=<remaining>)`. If
   that remaining budget is 0, skip step 8 entirely — do not call it with
   limit 0 — and go straight to step 10. If `tickers` is empty, note that
   and continue to step 10.
9. For each ticker from step 8:
   a. Call `get_prescan_prompts(ticker)` and take the prompt titled "Growth
      Cards" (its {prior:Long-Term Potential}, {prior:Business Analysis} and
      {prior:Key Metrics} are already filled in when those pre-scan sections
      exist for the ticker; a missing one shows as "(no prior Long-Term
      Potential analysis available for this ticker)" or similar in the
      prompt — that is acceptable, answer with what is there).
   b. SEC-MCP's `GetAnalystEstimates` is the only source for the "consensus"
      block. It returns level estimates (revenue and EPS, each with a
      mean/median, an analyst count and an accounting basis), not growth
      rates, so derive the block — computing this ratio is required; only
      inventing or estimating the consensus numbers themselves is forbidden:
      fiscal_year = the label of the FIRST fiscal year not yet reported;
      revenue_growth_pct / eps_growth_pct = (consensus mean for that year ÷
      the last REPORTED fiscal-year actual − 1) × 100, one decimal, using the
      actual on the same accounting basis as the estimate (EPS estimate
      adjusted/non-GAAP and no same-basis actual available → eps_growth_pct
      null); null for a growth figure when the change is outside −100% to
      1000% or the prior-year value is ≤ 0; analysts = the estimate's
      analyst count; source names the basis plus source and date (e.g.
      "compiled consensus, adjusted EPS, Equibles 2026-09-24"). If SEC-MCP
      is unavailable, or `GetAnalystEstimates` returns nothing usable for
      that fiscal year, omit the "consensus" key entirely — per the
      prompt's own instruction, never estimate the consensus numbers by
      hand.
   c. Answer the prompt exactly as it asks: one fenced JSON block with the
      "analysis" block (score 1-5, summary, three points), the "consensus"
      block when available, and the two cards in the listed order.
   d. Save with `save_prescan_section(ticker, "Growth Cards", <the JSON
      block>)`. If the server refuses, fix what the error names and save
      once more; if it refuses again, note the ticker and the reason and
      move on.
10. If all five `tickers` lists from steps 1, 3, 5, 6 and 8 were empty,
    print "Nothing left to backfill." Otherwise print a summary: tickers
    filled (per card set), tickers skipped with reasons, and `remaining`
    for each.
