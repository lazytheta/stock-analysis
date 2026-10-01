"""The shipped default pre-scan prompt library.

Pure data plus one helper: no Streamlit, no Supabase. streamlit_app re-exports
DEFAULT_AI_PROMPTS, and mcp_server uses merge_defaults to seed a user's prompt
library (user_prefs["ai_prompts"]) the first time get_prescan_prompts runs, and
to add defaults shipped after the library was saved.
"""

import business_cards
import company_explainer
import company_profile
import growth_cards
import management_cards
import moat_cards
import prompt_style
import risk_cards

# Default AI research prompts, in run order: later prompts read earlier
# sections through {prior:Title}.
DEFAULT_AI_PROMPTS: list[dict] = [
    {
        "title": "Robustness",
        "prompt": (
            "You are scoring **{company} ({ticker})** on Pulak Prasad's robustness "
            "framework (risk first). Judge ONLY these four qualitative axes; the ROCE "
            "and net-debt axes are computed from data elsewhere — do not output them.\n\n"
            "For each axis pick a band: \"robust\" (most robust pole), \"mid\", or "
            "\"fragile\" (least robust pole), and a one-line note grounded in the prior "
            "analysis.\n\n"
            "- **customers**: customer & supplier base — robust = highly fragmented (no "
            "dependence on any single party); fragile = concentrated.\n"
            "- **barriers**: competitive barriers / moat — robust = wide/widening; "
            "fragile = none/eroding.\n"
            "- **management**: stability & honesty of management/governance — robust = "
            "stable, honest signals, clean capital allocation; fragile = dubious, "
            "serial acquirer, turnaround.\n"
            "- **industry**: pace of industry change — robust = slow-changing/predictable; "
            "fragile = fast-changing.\n\n"
            "Use the prior sections as evidence:\n"
            "Moat: {prior:Moat Analysis}\n\n"
            "Risk: {prior:Risk Analysis}\n\n"
            "Disruption resilience: {prior:SaaSpocalypse Resistance}\n\n"
            "Business: {prior:Business Analysis}\n\n"
            + prompt_style.HOW_TO_WRITE + "\n"
            "Respond with ONLY a fenced JSON block, no prose:\n"
            "```json\n"
            "{\n"
            '  "axes": {\n'
            '    "customers":  {"band": "robust|mid|fragile", "note": "..."},\n'
            '    "barriers":   {"band": "robust|mid|fragile", "note": "..."},\n'
            '    "management": {"band": "robust|mid|fragile", "note": "..."},\n'
            '    "industry":   {"band": "robust|mid|fragile", "note": "..."}\n'
            "  }\n"
            "}\n"
            "```"
        ),
    },
    {
        "title": "Business Phase Analysis",
        "prompt": """# BUSINESS PHASE ANALYSIS — {company} ({ticker})

Place the company in one of six growth phases, based on its operating income,
revenue and capital returns.

## DATA
Use the most recent quarterly report (10-Q) of the current year; if there is
none yet, the most recent annual report (10-K). Name the one you used in the
Sources list. SEC EDGAR first, the company's own investor-relations reports
second; no third-party aggregators.

Collect for the latest period and the same period a year earlier: revenue and
operating income. From the cash flow statement: dividends and buybacks.

## DECISION TREE (apply in order; stop at the first match)
1. Returning capital (dividends or buybacks)? → Phase 5 · Capital Return
2. Operating income positive and revenue lower than a year earlier?
   → Phase 6 · Decline
3. Operating margin (operating income ÷ revenue) between −5% and +5%?
   → Phase 3 · Self Funding
4. Operating loss larger than a year earlier? → Phase 1 · Startup
5. Operating loss smaller than a year earlier? → Phase 2 · Hypergrowth
6. Otherwise (clearly profitable, revenue flat or growing)
   → Phase 4 · Operating Leverage

## THE SIX PHASES
1 · Startup — losses widening while it looks for a product that sells.
    Valued on revenue and market size; earnings-based methods do not apply.
2 · Hypergrowth — losses shrinking as the model proves itself. Valued on revenue
    and gross profit.
3 · Self Funding — near breakeven, margins climbing. Valued on revenue
    and gross profit; earnings are not yet a reliable anchor.
4 · Operating Leverage — clearly profitable, widening margins. Valued on
    forward earnings and free cash flow.
5 · Capital Return — mature, paying shareholders. Valued on trailing
    earnings and free cash flow.
6 · Decline — revenue falling. Valued on assets (book or liquidation value);
    growth and earnings methods mislead.

You still do the full analysis above, but report only what decides the answer:
EXACTLY three bullets, no tables, no subsections.

""" + prompt_style.HOW_TO_WRITE + """
# TEMPLATE (output exactly this shape, nothing before or after)

**Phase: [Startup / Hypergrowth / Self Funding / Operating Leverage / Capital Return / Decline] · [N]/6**

[ONE sentence. What the company is doing with its money right now, and how you
know. Not a definition of the phase.]

- **[Two-to-three word label]**: [one line with the figure that places it in
  this phase — operating income, revenue growth, buybacks, dividend]
- **[Two-to-three word label]**: [one line]
- **[Two-to-three word label]**: [one line]

**What moves it on:** [one line — the observable change that would put it in
the next phase, or back into the previous one. Name the metric and the level.]

## Sources
[1] Source - domain.com

# RULES
- The number is the phase itself (1-6), not a rating — the name goes
  before it, the number after, so neither is written twice.
- Valuation method belongs in the bullets only if the phase changes which one
  applies. Do not restate the DCF's job.
""",
    },
    {
        "title": "Business Analysis",
        "prompt": """# BUSINESS ANALYSIS — {company} ({ticker})

Explain the business model from the latest annual report (10-K), updated with
the most recent quarterly report (10-Q) of the current year if there is one;
otherwise use earnings releases or investor presentations. Name the documents
you used in the Sources list.

Work through these seven questions:
1. What does the company do? (what its products and services do for customers)
2. How does it make money? (revenue streams and segments, largest first, with shares)
3. Who are its customers? (consumers, small businesses, large companies, governments)
4. Where does it operate? (main regions, with shares)
5. How often do customers buy? (recurring or one-time, contracts, how many stay)
6. Can it raise prices? (margins, pricing comments, risk factors)
7. What happens in a recession? (past cycles, management warnings)

You still do the full analysis above, but report only what decides the answer:
EXACTLY three bullets, no tables, no subsections.

""" + prompt_style.HOW_TO_WRITE + """
# TEMPLATE (output exactly this shape, nothing before or after)

**Business: [Simple 🟢 / Understandable 🟡 / Opaque 🔴] · [how it earns, in two or three words]**

[ONE sentence. What you buy when you buy this share — the product, the payer,
and the geography. Someone who has never heard of the company should be able to
repeat it.]

- **Revenue Mix**: [the segments or geographies that matter, with their share]
- **[Two-to-three word label]**: [pricing power, repeat purchase, or whatever
  actually drives the economics — with the number that shows it]
- **[Two-to-three word label]**: [one line]

**In a downturn:** [one line — what happens to this business when customers
spend less, with evidence from a past cycle if there is one.]

## Sources
[1] Source - domain.com

# RULES
- "Simple" means you could explain it to someone at dinner. "Opaque" means the
  filings do not let you see how the money is made — say which part is dark.
""",
    },
    {
        "title": "Moat Analysis",
        "prompt": """# MOAT ANALYSIS — {company} ({ticker})

Judge whether the company has a durable competitive advantage (a moat), how
wide it is and which way it is moving.

## SOURCES
The latest annual and quarterly reports (10-K, 10-Q), recent earnings calls,
a Morningstar moat report if available, and key figures: revenue growth,
margins, customer retention, market share.

## THE FIVE MOAT SOURCES
Start from "no moat" for each source and look for evidence against that.
- Switching costs — how painful it is for a customer to leave.
- Network effect — each new user makes the product more valuable to others.
- Intangible assets — a brand, patent or licence that lets it charge more.
- Low-cost production — costs rivals cannot match.
- Counter-positioning — rivals cannot copy the model without hurting
  themselves (Netflix streaming vs Blockbuster stores). Being different or
  innovative is not enough.

Size: Wide = the advantage should last 10+ years; Narrow = 3-10 years;
None = no durable advantage.
Direction: Widening = engagement rising, margins expanding, brand extending;
Stable = flat; Narrowing = customers leaving, margins shrinking, brand
weakening.

You still assess all five sources, but report only what decides the answer.

""" + prompt_style.HOW_TO_WRITE + """
# TEMPLATE (output exactly this shape, nothing before or after)

**Moat: [None ❌ / Narrow 🤏 / Wide 🛡️] · [Widening ↗️ / Stable ➡️ / Narrowing ↘️] · [0-5]/5**

[ONE sentence. Name the company, bold the verdict, and say where the moat comes
from — or why there isn't one. No preamble, no restating the question.]

- **[Two-to-three word label]**: [one line, with the number or fact that makes
  it true. No citation clutter — the source list is below.]
- **[Two-to-three word label]**: [one line]
- **[Two-to-three word label]**: [one line]

**Weakest link:** [one line — the moat source you looked for and did not find,
or the one most likely to erode first. This line is mandatory: a moat analysis
with nothing against it has not been done.]

## Sources
[1] Source - domain.com
[2] Source - domain.com

# RULES FOR THE TEMPLATE
- The score is the moat's strength on the five sources you assessed, not a
  confidence rating: 0 = none, 1-2 = narrow, 3-4 = wide, 5 = wide and widening.
- EXACTLY three bullets. Not four because a fourth is interesting. If two
  sources are strong and three are absent, three bullets still — use one to
  say what is absent and why it does not matter.
- A bullet says why THIS company has the advantage, not just which category
  it falls in.
- No tables. No per-source subsections. No "Assessment: Present" lines.
""",
    },
    {
        # Five question cards for the Moat tab, built on the analysis above.
        "title": moat_cards.TITLE,
        "prompt": moat_cards.PROMPT,
    },
    {
        "title": "Long-Term Potential",
        "prompt": """# LONG-TERM POTENTIAL — {company} ({ticker})

Judge how long the company can keep growing, and where that growth comes from.

## SOURCES
The latest annual report (10-K: segments, strategy, management discussion),
the latest quarterly report (10-Q), investor-day material and recent earnings
calls. Name what you used in the Sources list.

## THE SEVEN GROWTH DRIVERS
Winning new customers:
1. Marketing and sales spending
2. New sales channels
3. New regions or markets
4. Acquisitions
Existing customers spending more:
5. Raising prices
6. New products for the same customers
7. Keeping customers longer

Rate each driver Strong (clear evidence and real investment), Moderate
(mentioned, not emphasised), Weak (little evidence) or Not applicable.
Evaluate only these seven drivers; do not add others.

You still do the full analysis above, but report only what decides the answer:
EXACTLY three bullets, no tables, no subsections.

""" + prompt_style.HOW_TO_WRITE + """
# TEMPLATE (output exactly this shape, nothing before or after)

**Runway: [Long ↗️ / Moderate ➡️ / Short ↘️] · [0-5]/5**

[ONE sentence. Where the next decade of growth comes from — new customers, more
per customer, or price — and which of those is doing the work now.]

- **[Two-to-three word label]**: [the driver, with the growth rate or
  penetration figure that makes it credible]
- **[Two-to-three word label]**: [one line]
- **[Two-to-three word label]**: [one line]

**Biggest assumption:** [one line — the thing that must stay true for this
runway to exist. If it is "the market keeps growing", say what happens if it
does not.]

## Sources
[1] Source - domain.com

# RULES
- 0-1 = the business grows with GDP at best. 4-5 = a decade of reinvestment at
  high returns is visible in today's disclosures, not in management's ambition.
- An untapped opportunity the company is not pursuing is not runway. Say so.
""",
    },
    {
        "title": "Key Metrics",
        "prompt": """# KEY METRICS — {company} ({ticker})

Score the five metrics that matter for the company's growth phase, each Red,
Yellow or Green against the thresholds below.

## PHASE
Take the phase number (1-6) from the Business Phase Analysis below. If it is
missing, use the phase the latest filings clearly point to and say so.

{prior:Business Phase Analysis}

## DATA
The most recent quarterly report (10-Q) of the current year, else the latest
annual report (10-K); recent 8-K filings; optionally the last two earnings
calls. You need: revenue (now and three years back), gross margin by quarter,
operating income, free cash flow, shares outstanding (now and three years
back), dividends and buybacks, return on capital, and cash, debt and interest
expense.

## THRESHOLDS BY PHASE
### Phase 1 · Startup
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue** | None | Positive | Positive and >30% YoY Growth |
| **Gross Margin** | Negative | Positive | Positive and Improving (>0pp YoY) |
| **Cash Runway** | Less than 1.5 Years | Between 1.5 and 3 Years | 3+ Years (or FCF Positive) |
| **Revenue vs. Estimates** | <5 of last 8 beats | 5-7 of last 8 beats | 4 of last 4 beats |
| **Shares Outstanding 3YR CAGR** | Over 7% | Between 4% and 7% | Less than 4% |
### Phase 2 · Hypergrowth
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue 3YR CAGR** | Less than 20% | 20%-30% | 30%+ |
| **Gross Margin Direction** | Declining or Erratic (>3pp variance QoQ) | Stable (within ±1pp YoY) | Rising |
| **Cash Runway** | Less than 2 Years | Between 2 and 4 Years | 4+ Years (or FCF Positive) |
| **Revenue vs. Estimates** | <5 of last 8 beats | 5-7 of last 8 beats | 4 of last 4 beats |
| **Shares Outstanding 3YR CAGR** | Over 5% | Between 3% and 5% | Less than 3% |
### Phase 3 · Self Funding
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue 3YR CAGR** | Less than 15% | Between 15% and 25% | Over 25% |
| **Gross Margin Direction** | Declining | Stable (within ±1pp YoY) | Rising |
| **Operating Margin** | Declining or <-2% | Between -2% and +2% | >2% and Rising |
| **Free Cash Flow** | Negative | Positive | Positive and Rising |
| **Shares Outstanding 3YR CAGR** | More than 3% | Between 1% and 3% | Below 1% |
### Phase 4 · Operating Leverage
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue 3YR CAGR** | Less than 10% | Between 10% and 20% | Over 20% |
| **Operating Margin** | Declining or Cyclical | Positive and Stable (within ±1pp YoY) | Positive and Rising |
| **Free Cash Flow Margin** | Contracting or Negative | Positive | Positive and Rising |
| **Earnings vs. Estimates** | <5 of last 8 beats | 5-7 of last 8 beats | 4 of last 4 beats |
| **ROIC** | <0% or Declining | 0%-5% (no clear trend) | >5% and Rising (3 of 4 quarters) |
### Phase 5 · Capital Return
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue 3YR CAGR** | Less than 5% | Between 5% and 10% | Over 10% |
| **Free Cash Flow / Net Income** | Less than 50% | Between 50% and 90% | Over 90% |
| **EBIT / Interest Expense** | Less than 2 | Between 2 and 5 | 5+ (or debt-free) |
| **ROIC** | Less than 10% | Between 10% and 20% | Over 20% |
| **Capital Returns** | None | Yes, <5 Years | Yes, 5+ Years |
### Phase 6 · Decline
No gates. Score 0/5 and use the bullets for the revenue, margin and cash flow
trends.

## DEFINITIONS
- Stable = within ±1 percentage point year over year; Erratic = moves of more
  than 3 points between quarters.
- ROIC may be replaced by ROCE (operating profit ÷ capital employed), the
  measure the rest of the app uses; name the one you report. Rising = improved
  in 3 of the last 4 quarters.
- Positive free cash flow makes Cash Runway Green; no debt makes EBIT /
  Interest Green. Exactly on a threshold = the better rating.
- A metric passes ONLY when it is Green; Yellow and Red do not pass. The score
  N/5 counts the passes. 4-5 = Strong, 2-3 = Mixed, 0-1 = Weak.

You still score all five metrics, but report only what decides the answer:
EXACTLY three bullets, no tables, no subsections.

""" + prompt_style.HOW_TO_WRITE + """
# TEMPLATE (output exactly this shape, nothing before or after)

**Metrics: [Strong 🟢 / Mixed 🟡 / Weak 🔴] · [N]/5**

[ONE sentence. What the five phase metrics say together — not a list of them.]

- **[Metric name]**: [value, target, and the direction it is moving]
- **[Metric name]**: [value, target, direction]
- **[Metric name]**: [value, target, direction]

**Weakest metric:** [one line — the metric closest to failing its gate, with
the level at which it would. This line is mandatory even when all five pass.]

## Sources
[1] Source - domain.com

# RULES
- The score is how many of the five phase metrics pass (are Green).
- The three bullets are the three that decide it: the failures first, then the
  ones nearest their threshold. A metric passing comfortably needs no line.
- Each bullet ends with what the number means for the business, in plain
  words (e.g. "profits cover interest many times over").
""",
    },
    {
        # Business overview, customer profile, revenue breakdown and four
        # question cards for the Business tab, built on Business Analysis,
        # Moat Analysis and Key Metrics.
        "title": business_cards.TITLE,
        "prompt": business_cards.PROMPT,
    },
    {
        # Sector/industry, capital type, difficulty and a few quick facts
        # for the Overview tab's profile panel, built on Business Analysis
        # and Moat Analysis.
        "title": company_profile.TITLE,
        "prompt": company_profile.PROMPT,
    },
    {
        # Scored growth analysis, analyst consensus (when available) and two
        # question cards for the Growth tab, built on Long-Term Potential,
        # Business Analysis and Key Metrics.
        "title": growth_cards.TITLE,
        "prompt": growth_cards.PROMPT,
    },
    {
        # Five-heading explanation of what the company does, for the Overview
        # tab, built on Business Analysis, Business Cards and Key Metrics.
        "title": company_explainer.TITLE,
        "prompt": company_explainer.PROMPT,
    },
    {
        "title": "Risk Analysis",
        "prompt": """# RISK ANALYSIS — {company} ({ticker})

Judge what could go wrong in the business itself, across four risks.

## SOURCES
The latest annual report (10-K: risk factors, management discussion) and
quarterly report (10-Q). Search the web only for data the filings lack.
Prefer filing data from the last 12 months. Where the company does not break
something out, say "limited disclosure".

## THE FOUR RISKS
Concentration (customers)
- 🔴 Red: largest customer above 20% of revenue
- 🟡 Yellow: largest customer 10% to 20% of revenue
- 🟢 Green: no customer at 10% or more
Disruption
- 🔴 Red: an identifiable threat that could make the product obsolete
- 🟡 Yellow: normal change in the industry
- 🟢 Green: the company is the disruptor
Outside forces (regulation, commodities, government, the economy, rates)
- 🔴 Red: high exposure · 🟡 Yellow: normal · 🟢 Green: low
Competition
- 🔴 Red: severe price pressure, many rivals
- 🟡 Yellow: normal competition
- 🟢 Green: one or two players dominate

Overall rating: average the four (Red = 3, Yellow = 2, Green = 1);
2.5 and up = High, 1.5 to 2.4 = Medium, below 1.5 = Low. When the evidence is
thin, default to Medium and say the evidence is thin.

You rate all four risks, but report only the ones that decide the answer. A
risk that is Yellow because nothing is wrong does not need a line.

""" + prompt_style.HOW_TO_WRITE + """
# TEMPLATE (output exactly this shape, nothing before or after)

**Risk: [High 🔴 / Medium 🟡 / Low 🟢] · [Concentration / Disruption / Outside forces / Competition — the one that drives the rating]**

[ONE sentence. What would actually have to go wrong for this to hurt, and how
exposed the company is to it. Not a list of risk categories.]

- **[Two-to-three word label]**: [one line, with the number that makes it real
  — a customer share, a margin move, a revenue concentration]
- **[Two-to-three word label]**: [one line]
- **[Two-to-three word label]**: [one line]

**What would change this:** [one line — the specific, observable thing that
would move the rating up or down. "Macro improves" is not an answer; "gross
margin back above 54% for two quarters" is.]

## Sources
[1] [Company] 10-K [Date] - sec.gov
[2] Source - domain.com

# RULES FOR THE TEMPLATE
- EXACTLY three bullets, ordered worst first. If only two risks are real, use
  the third to name the one you expected to find and did not — an analysis
  that finds nothing reassuring is as incomplete as one that finds nothing
  wrong.
- Every bullet names concrete evidence. "Faces competition" is not a risk;
  "profit margins fell by a fifth in two years while two new rivals grew" is.
- No tables, no matrix, no per-factor subsections.
""",
    },
    {
        "title": "Price & Sentiment Analysis",
        "prompt": """# PRICE & SENTIMENT — {company} ({ticker})

Explain why the stock moved over the past 12 months and where market mood sits
now. No speculation, no hype; every statement must be checkable.

## GATHER
- The 1-year price move, where it sits in its 52-week range, and how it
  compares with its 50- and 200-day moving averages and the index.
- What moved it: earnings reactions, analyst actions, product launches,
  regulation or economic news.
- Mood: analyst ratings and targets, institutional versus retail buying,
  media and forum tone.
Weigh the two or three strongest arguments on each side, bull and bear. With
fewer than two sources on a side, say "limited recent coverage".

You still do the full analysis above, but report only what decides the answer:
EXACTLY three bullets, no tables, no subsections.

""" + prompt_style.HOW_TO_WRITE + """
# TEMPLATE (output exactly this shape, nothing before or after)

**Sentiment: [Bullish 🟢 / Mixed 🟡 / Bearish 🔴] · [what moved the price]**

[ONE sentence. Why the price is where it is, and what the market is currently
arguing about.]

- **Price Action**: [1-year move, distance from the 52-week range, versus the
  index]
- **Bull Case**: [the strongest argument the buyers are making, in one line]
- **Bear Case**: [the strongest argument against, in one line]

**Next catalyst:** [one line — the dated event that resolves part of the
argument. "Earnings" is not enough; say which quarter and what to watch in it.]

## Sources
[1] Source - domain.com

# RULES
- Sentiment describes the market's mood, not your verdict on the company. A
  bearish tape on a good business is information, not a warning.
- No price targets as a recommendation. Report the consensus as a fact if it
  is relevant, and never as agreement.
""",
    },
    {
        "title": "SaaSpocalypse Resistance",
        "prompt": """# AI DISRUPTION RESISTANCE — {company} ({ticker})

Judge whether AI strengthens or threatens what this company sells, through
four lenses. Put failure points and structural risks first.

Rate each lens:
- 🔴 Exposed: AI can replace or cheapen what the company sells.
- 🟡 Resilient: defensible and stable, but no real upside from AI.
- 🟢 Anti-fragile: the business gets stronger as AI spreads.

1. Liability — is a mistake costly? Green: a 90%-right answer is a disaster
   (medical diagnosis, cybersecurity, running a power grid). Red: 90% right
   is fine (marketing copy, simple code, graphic design).
2. Business model — does it charge for work done or per user? Green: over 80%
   of current revenue is tied to usage, so if AI agents replace ten analysts,
   the revenue follows the agents' usage. Red: over 80% comes from per-user
   subscriptions, so if one person can do the work of ten, nine
   subscriptions go. Rate the current revenue mix, not planned changes.
3. Physical world — does it need real-world hardware or infrastructure? Green:
   software tied to physical equipment is hard to replace with pure AI. Red:
   pure software that an AI agent could copy at near-zero cost.
4. Data — does it own data AI needs? Green: unique, non-public data or a
   two-sided network. Red: data that is public or easy to move elsewhere.

If the company does not sell software or office work, say so plainly in the
one-sentence summary and keep the lenses short; still output the verdict line
in the format below.

You still do the full analysis above, but report only what decides the answer:
EXACTLY three bullets, no tables, no subsections.

""" + prompt_style.HOW_TO_WRITE + """
# TEMPLATE (output exactly this shape, nothing before or after)

**AI exposure: [Anti-fragile 🟢 / Resilient 🟡 / Exposed 🔴] · [N]/4**

[ONE sentence. Whether AI is a tool this company uses, a threat to what it
sells, or irrelevant to it — and why.]

- **[Lens name]**: [one line on the lens that decides it, with evidence]
- **[Lens name]**: [one line]
- **[Lens name]**: [one line]

**Where it would break:** [one line — the specific development that would turn
this from resilient to exposed. Mandatory even for a physical-goods business:
if nothing plausible exists, say what you looked for.]

## Sources
[1] Source - domain.com

# RULES
- The score is how many of the four lenses (liability, business model,
  physical world, data) come back Resilient or Anti-fragile.
- Seat-based pricing on knowledge work is the exposure that matters. A company
  selling units of a physical thing is not exposed just because it uses
  software.
""",
    },
    {
        # Four question cards for the Risk tab, built on Risk Analysis and
        # SaaSpocalypse Resistance.
        "title": risk_cards.TITLE,
        "prompt": risk_cards.PROMPT,
    },
    {
        # Three question cards for the Management tab plus the insider and
        # pay facts behind them, from the SEC connector's executive feeds.
        "title": management_cards.TITLE,
        "prompt": management_cards.PROMPT,
    },
    {
        "title": "Investment Summary",
        "prompt": """# INVESTMENT SUMMARY & VERDICT — {company} ({ticker})

Read all the prior research below and give one decisive verdict. Synthesize;
do not repeat the underlying analyses.

## PRIOR RESEARCH

### Business Phase Analysis
{prior:Business Phase Analysis}

### Business Analysis
{prior:Business Analysis}

### Moat Analysis
{prior:Moat Analysis}

### Long-Term Potential
{prior:Long-Term Potential}

### Key Metrics
{prior:Key Metrics}

### Risk Analysis
{prior:Risk Analysis}

### Price & Sentiment Analysis
{prior:Price & Sentiment Analysis}

### SaaSpocalypse Resistance
{prior:SaaSpocalypse Resistance}

---

Report only what decides the answer: EXACTLY three bullets, no tables, no
subsections.

""" + prompt_style.HOW_TO_WRITE + """
# TEMPLATE (output exactly this shape, nothing before or after)

**Verdict: [Deep dive 🟢 / Revisit 🟡 / Pass 🔴] · [High / Medium / Low] conviction**

[ONE sentence thesis. What this company is and why it is or is not worth more
work. This sentence is the one that gets quoted back — make it carry.]

- **Strongest point**: [one line, with the figure behind it]
- **Biggest concern**: [one line, with the figure behind it]
- **[Two-to-three word label]**: [the third thing that actually moves the
  decision — not a filler strength]

**What would change this:** [one line — the observable event that flips the
verdict, in either direction. Name the metric and the level.]

## Sources
[1] Source - domain.com

# RULES
- Say nothing about whether the shares are cheap. The DCF owns that judgement,
  and a prose opinion next to a computed fair value is the one that gets
  quoted. Verdict means "is this worth the work", not "is this a buy".
- Conviction is about the evidence, not the outcome: Low conviction on a Deep
  dive is a legitimate and useful answer.
""",
    },
    {
        "title": "Scorecard",
        "prompt": """# SCORECARD DATA EXTRACTOR — {company} ({ticker})

## YOUR MISSION
Read all prior analyses for this company and extract a structured JSON scorecard. This JSON is parsed programmatically — output EXACTLY the JSON block, nothing else before or after.

## PRIOR RESEARCH

### Business Phase Analysis
{prior:Business Phase Analysis}

### Business Analysis
{prior:Business Analysis}

### Moat Analysis
{prior:Moat Analysis}

### Long-Term Potential
{prior:Long-Term Potential}

### Key Metrics
{prior:Key Metrics}

### Risk Analysis
{prior:Risk Analysis}

### Investment Summary
{prior:Investment Summary}

---

""" + prompt_style.HOW_TO_WRITE + """
## OUTPUT INSTRUCTIONS
Output ONLY a fenced JSON code block. No prose, no explanations. Use the exact schema below. Use lowercase color names: "red", "yellow", or "green".

Rating meanings:
- green = positive / strong / low risk / fairly or undervalued
- yellow = neutral / moderate / mixed signals
- red = negative / weak / high risk / overvalued

For `verdict`: use "deep_dive" (strongly interesting), "revisit" (park for later), or "pass" (skip).

```json
{
  "phase": {
    "number": 5,
    "name": "Capital Return"
  },
  "all_phases": {
    "business_description": {
      "rating": "green",
      "note": "Clear business model, well understood"
    },
    "moat": {
      "rating": "green",
      "note": "Wide / Expanding"
    },
    "long_term_potential": {
      "rating": "yellow",
      "note": "Moderate growth runway"
    }
  },
  "key_metrics": [
    {"name": "Revenue 3YR CAGR", "rating": "green", "value": "Over 10%"},
    {"name": "FCF / Net Income", "rating": "yellow", "value": "Between 50% and 90%"},
    {"name": "EBIT / Interest Expense", "rating": "green", "value": "5+"},
    {"name": "ROIC", "rating": "green", "value": "Over 20%"},
    {"name": "Capital Returns", "rating": "green", "value": "Yes, 5+ Years"}
  ],
  "execution_risk": {
    "rating": "yellow",
    "note": "Medium"
  },
  "verdict": "revisit",
  "summary": "Three concise sentences summarizing the investment case. Sentence 1: the core business and what makes it interesting or not. Sentence 2: the biggest strength or concern. Sentence 3: the verdict rationale — why deep dive, revisit, or pass."
}
```

## GUARDRAILS
- Output ONLY the JSON code block. Nothing else.
- Use EXACTLY the keys shown above — do not rename or add keys.
- The `key_metrics` list should contain exactly the 5 metrics used by the phase from the Key Metrics analysis.
- If a prior analysis is missing or cannot be interpreted, use rating "yellow" and note "Insufficient data".
- Derive `verdict` from the Investment Summary if present; otherwise base it on the overall pattern of ratings.
- `summary` must be EXACTLY 3 sentences, concise, plain English, derived from the Investment Summary.
""",
    },
]


def merge_defaults(library):
    """Add every default whose title is missing from `library`.

    Returns (new_library, changed). A missing default goes right after the
    nearest preceding default that is in the library (or to the front when
    none is), so the order follows DEFAULT_AI_PROMPTS. Existing prompts are
    never overwritten, custom prompts keep their place, and the input list is
    not mutated.
    """
    new = [dict(p) if isinstance(p, dict) else p for p in (library or [])]
    changed = False
    pos = 0  # insertion point for the next missing default
    for default in DEFAULT_AI_PROMPTS:
        title = default["title"]
        idx = next((i for i, p in enumerate(new)
                    if isinstance(p, dict) and p.get("title") == title), None)
        if idx is not None:
            pos = idx + 1
            continue
        new.insert(pos, {"title": title, "prompt": default["prompt"]})
        pos += 1
        changed = True
    return new, changed
