# Aspirant nightly — routine prompt

You run every evening for the LazyTheta watchlist and handle ONE name per
run, thoroughly: full pre-scan and a full DCF. Use the LazyTheta connector
and the SEC connector only.

1. Call `get_screener_candidates(limit=1)`. If `candidates` is empty, stop
   without doing anything else.
2. For the candidate:
   a. If the candidate's `source` is `"requested"`, it was added by hand and
      is already on the list as an Aspirant: do NOT call `add_aspirant`; go
      straight to step b (it counts as a name you added in this run for the
      `save_to_watchlist` rule below). Otherwise call `add_aspirant(ticker)`;
      the server fetches the price itself. Only
      if it returns an error mentioning `No price`, get the price with the
      SEC connector's `GetLiveQuote` and call `add_aspirant(ticker,
      stock_price)` once more. If it returns an error or says the
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
      Analysis", "Key Metrics" and "Long-Term Potential" and before
      answering "Business Cards", "Company Profile" and "Growth Cards" —
      library order now already places Risk Cards directly after both of
      its priors and Business Cards directly after Key Metrics (its last
      prior), with Company Profile right after Business Cards (its own
      priors, Business Analysis and Moat Analysis, are the same ones
      already re-fetched for Business Cards) and Growth Cards right after
      Company Profile (its priors are Long-Term Potential, Business
      Analysis and Key Metrics), but the re-fetch is still required so
      every `{prior:…}` in the prompt you are about to answer is actually
      filled in, not still a bare placeholder. For "Growth Cards", the
      "consensus" block comes only from the SEC connector's
      `GetAnalystEstimates`, which returns level estimates (revenue and EPS
      with mean/median, analyst count and accounting basis), not growth
      rates: derive revenue_growth_pct / eps_growth_pct = (consensus mean
      for the FIRST fiscal year not yet reported ÷ the last REPORTED
      fiscal-year actual − 1) × 100, one decimal, on the same accounting
      basis (adjusted/non-GAAP EPS estimate without a same-basis actual →
      eps_growth_pct null; null too when the change is outside −100% to
      1000% or the prior-year value is ≤ 0); fiscal_year = that first
      unreported year's label; analysts = the estimate's count; source
      names basis, source and date (e.g. "compiled consensus, adjusted
      EPS, Equibles 2026-09-24"). Computing this ratio is required; only
      inventing or estimating the consensus numbers themselves is
      forbidden. If the call is unavailable or returns nothing usable, omit
      the "consensus" key entirely — never estimate it by hand. If `save_prescan_section`
      refuses "Moat Cards", "Risk Cards", "Business Cards", "Company
      Profile" or "Growth Cards", fix what the error names and save once
      more; if it refuses again, note it and move on.
   c. Always fill in the full DCF, whatever the Moat verdict. Read
      `docs/routines/dcf-method.md` in this repository and follow it
      exactly, sections 1–7: anchors (history, consensus, guidance), growth
      and margin fades set by the Moat verdict, reinvestment, balance check,
      save, scenarios,
      valuation, reverse-DCF check and the "DCF Rationale" section.
      `save_to_watchlist` is allowed for an Aspirant; it keeps the category
      and clears the placeholder marker.
   d. Only if the Moat verdict is Wide, call `promote_aspirant(ticker)`. If
      promote refuses, note the reason. Otherwise the name stays an Aspirant
      — now with its fair value and buy price visible.
3. Finish with one `add_reminder` for today with the summary: the name, its
   Moat verdict, fair value and buy price, and whether it was promoted — or,
   if it was skipped, why.
