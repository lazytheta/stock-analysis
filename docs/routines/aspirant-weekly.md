# Aspirant weekly — routine prompt

You run weekly for the LazyTheta watchlist. Use the LazyTheta connector and
the SEC connector only. Work through the steps; one name failing never stops
the next.

1. Call `get_screener_candidates(limit=5)`. If `candidates` is empty, stop
   without doing anything else.
2. For each candidate:
   a. Get the price with the SEC connector's `GetLiveQuote`. Call
      `add_aspirant(ticker, stock_price)`. If it returns an error or says the
      name already exists, skip the name and note why. Never call
      `set_category` to move an Aspirant anywhere except `No`; promotion only
      happens through `promote_aspirant`. Never call `save_to_watchlist` for
      a name you did not add in this run.
   b. Call `get_prescan_prompts(ticker)`. Answer the prompts in library order,
      thoroughly, from filings and your own analysis, following each prompt's
      own TEMPLATE section exactly, and save each one with
      `save_prescan_section(ticker, title, content)` under that prompt's
      `title` exactly as `get_prescan_prompts` returned it (e.g. "Moat
      Analysis" — not "Moat"). The Moat Analysis section must open with its
      verdict line exactly in the form
      `**Moat: <Wide|Narrow|None> <emoji> · <Widening|Stable|Narrowing> <emoji> · <n>/5**`.
      Several prompts reference an earlier section's output via `{prior:…}`;
      that placeholder is only filled in with what is currently saved, so
      call `get_prescan_prompts(ticker)` again after saving each section a
      later prompt depends on — at minimum after saving "Moat Analysis" and
      before answering "Moat Cards", "Investment Summary" and "Scorecard",
      after saving "Risk Analysis" and "SaaSpocalypse Resistance" and before
      answering "Risk Cards", and after saving "Business Analysis", "Moat
      Analysis" and "Key Metrics" and before answering "Business Cards" —
      library order now already places Risk Cards directly after both of its
      priors and Business Cards directly after Key Metrics (its last prior),
      but the re-fetch is still required so every `{prior:…}` in the prompt
      you are about to answer is actually filled in, not still a bare
      placeholder. If `save_prescan_section` refuses "Moat Cards", "Risk
      Cards" or "Business Cards", fix what the error names and save once
      more; if it refuses again, note it and move on.
   c. If the Moat verdict is not Wide, the name stays an Aspirant. Move on.
   d. If the Moat verdict is Wide, fill in the full DCF: `get_config(ticker)`,
      then set revenue_growth and op_margins year by year with a short
      rationale, terminal_growth, sector_betas as [name, unlevered_beta,
      revenue_weight] with weights summing to 1.0, and equity_market_value
      ($M). Rules: nominal basis, CAPM/WACC, no SBC adjustments, margin of
      safety 20%, no peers. Save with `save_to_watchlist`. Then
      `update_dcf_scenario_adjustments`, `set_robustness`, `set_premortem`,
      `calculate_multi_lens_valuation(ticker)`, and finally
      `promote_aspirant(ticker)`. If promote refuses, note the reason; the
      name stays an Aspirant.
3. Finish with one `add_reminder` for today with the summary: which names
   were added, which were promoted (with fair value and buy price), which were
   skipped and why.
