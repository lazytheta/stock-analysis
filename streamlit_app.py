"""
Streamlit web app for Stock Analysis tools — v2.
- DCF Valuation Model Generator
- Portfolio Cost Basis Tracker (Tastytrade)
"""

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import io
import logging
import os
import sys
import contextlib
import tempfile
import time
from datetime import date, datetime, timedelta
from collections import defaultdict
import re

logger = logging.getLogger(__name__)

from error_logger import log_error, log_error_with_trace
from dcf_calculator import compute_wacc, compute_intrinsic_value, compute_reverse_dcf
from config_store import save_config, load_config, list_watchlist, remove_from_watchlist, load_user_prefs, save_user_prefs, load_credential, save_credential, delete_credential, load_ibkr_credentials, save_ibkr_credentials, delete_ibkr_credentials, IBKR_CREDENTIAL_KEYS, log_page_view
from gather_data import (
    get_cik,
    fetch_company_submissions,
    fetch_company_facts,
    parse_financials,
    fetch_stock_price,
    fetch_treasury_yield,
    synthetic_credit_rating,
    fetch_sector_betas,
    fetch_sector_margins,
    fetch_sector_s2c,
    fetch_consensus_estimates,
    find_peers,
    fetch_peer_data,
    build_config,
    SIC_TO_SECTOR,
    ERP_DEFAULT,
    TERMINAL_GROWTH_DEFAULT,
    MARGIN_OF_SAFETY_DEFAULT,
    fetch_fundamentals,
)
from broker_adapter import (
    fetch_portfolio_data, fetch_current_prices, fetch_account_balances,
    fetch_net_liq_history, fetch_sp500_yearly_returns, fetch_benchmark_returns,
    fetch_ticker_profiles, fetch_yearly_transfers, fetch_portfolio_greeks,
    fetch_margin_interest, fetch_margin_for_position, fetch_margin_requirements,
    fetch_beta_weighted_delta, fetch_greeks_and_bwd, fetch_option_chain,
    fetch_earnings_dates, has_active_broker, get_active_broker,
    fetch_benchmark_monthly_returns,
)
import plotly.graph_objects as go

# ── Input sanitization ──
def sanitize_ticker(raw: str) -> str | None:
    """Validate and clean a ticker symbol. Returns None if invalid."""
    cleaned = raw.strip().upper()
    if re.match(r'^[A-Z]{1,5}$', cleaned):
        return cleaned
    return None


# ── AI provider helpers (Groq primary, Gemini Flash fallback) ──
def _secret_or_env(name: str) -> str | None:
    try:
        v = st.secrets.get(name)
        if v:
            return v
    except Exception:
        pass
    return os.environ.get(name)


def _gemini_api_key() -> str | None:
    return _secret_or_env("GEMINI_API_KEY")


def _groq_api_key() -> str | None:
    return _secret_or_env("GROQ_API_KEY")


def _ai_ready() -> bool:
    return bool(_groq_api_key()) or bool(_gemini_api_key())


_RETRY_SUBSTRINGS = (
    "rate", "quota", "429", "resource_exhausted",
    "503", "unavailable", "overloaded", "high demand",
)


def _groq_call(prompt: str) -> tuple[str, str | None]:
    key = _groq_api_key()
    if not key:
        return "", "GROQ_API_KEY niet ingesteld."
    import urllib.request
    import urllib.error
    import json as _json
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=_json.dumps({
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key.strip()}",
            "Content-Type": "application/json",
            "User-Agent": "lazytheta-stock-analysis/1.0",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"].strip()
        return (text, None) if text else ("", "groq: empty response")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return "", f"groq: HTTP {e.code} {e.reason} — {body[:500]}"
    except Exception as e:
        return "", f"groq: {e}"


def _gemini_flash_call(prompt: str) -> tuple[str, str | None]:
    key = _gemini_api_key()
    if not key:
        return "", "GEMINI_API_KEY niet ingesteld."
    try:
        from google import genai
    except ImportError:
        return "", "google-genai pakket niet geïnstalleerd."
    try:
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt,
        )
        text = (getattr(resp, "text", "") or "").strip()
        return (text, None) if text else ("", "gemini-2.5-flash: empty response")
    except Exception as e:
        return "", f"gemini-2.5-flash: {e}"


def _gemini_run(prompt: str, prefer_pro: bool = False) -> tuple[str, str | None]:
    """Run a prompt against the AI providers.

    Order: Groq Llama 3.3 70B → Gemini 2.5 Flash (fallback on rate limit/overload).
    Returns (text, error).
    """
    errors: list[str] = []
    for fn, name in (
        (_groq_call, "Groq Llama 3.3 70B"),
        (_gemini_flash_call, "Gemini 2.5 Flash"),
    ):
        text, err = fn(prompt)
        if text:
            return text, None
        if err:
            errors.append(f"{name} — {err}")
            low = err.lower()
            if any(s in low for s in _RETRY_SUBSTRINGS):
                continue  # try next provider
            return "", "AI error:\n\n" + "\n\n".join(errors)
    return "", "Alle AI providers faalden:\n\n" + "\n\n".join(errors)


# Backwards-compat alias (used by existing UI code)
def _gemini_ready() -> bool:
    return _ai_ready()


# Default AI research prompts loaded via "Load default prompts" button
DEFAULT_AI_PROMPTS: list[dict] = [
    {
        "title": "Business Phase Analysis",
        "prompt": """# BUSINESS PHASE ANALYSIS v18.7

## YOUR IDENTITY
Financial analyst classifying companies into six growth phases based on operating income dynamics.

## YOUR MISSION
1. Request company name from user
2. Retrieve most recent financial data from SEC filings
3. Apply simple decision tree
4. Output ONLY the template below - nothing more

## EXECUTION TRIGGER
- If company name/ticker provided: Begin analysis
- If not provided: Output EXACTLY: "What company (name or ticker) would you like me to analyze?"
- WAIT FOR USER RESPONSE

## DATA ACQUISITION

### Priority (CRITICAL)
1. Identify current year from today's date
2. Search for MOST RECENT 10-Q from current year
3. If no current year 10-Q, use most recent 10-K
4. State explicitly: "Using [Q# YYYY 10-Q] filed on [date]" or "No 2025 10-Q available, using [FY YYYY 10-K]"

### Required Data
- Current period Revenue
- Prior year same period Revenue
- Current period Operating Income
- Prior year same period Operating Income
- Capital Returns (dividends + buybacks from Cash Flow Statement)

### Source Priority
- PRIMARY: SEC EDGAR only
- SECONDARY: Company IR page (official reports only)
- FORBIDDEN: Third-party aggregators

## CLASSIFICATION LOGIC (USE INTERNALLY ONLY)

### DECISION TREE (Apply in exact order)

STEP 1: Check Capital Returns
- Returning capital (dividends OR buybacks)? → Phase 5: CAPITAL RETURN [STOP]
- Otherwise → Continue

STEP 2: Check Operating Income
- Negative? → Go to Step 3
- Positive? → Go to Step 4

STEP 3: Analyze Losses (for negative Operating Income)
- Current loss worse than prior year? → Phase 1: STARTUP [STOP]
- Current loss same or better? → Phase 2: HYPERGROWTH [STOP]

STEP 4: Check Revenue Growth (for positive Operating Income)
- Revenue declining? → Phase 6: DECLINE [STOP]
- Revenue flat/growing? → Phase 4: OPERATING LEVERAGE [STOP]

## PHASE DEFINITIONS & VALUATION METHODS

### 🌱 Phase 1: STARTUP
- Characteristics: Losses expanding, finding product-market fit
- Valuation Methods: Forward Price to Sales, Total Addressable Market (TAM)
- Why These Fit: Company is pre-profit with expanding losses. Valuation relies on future revenue potential and market opportunity size.
- Avoid: P/E ratios, DCF models, any earnings-based methods

### 🚀 Phase 2: HYPERGROWTH
- Characteristics: Losses improving, proving viability
- Valuation Methods: Forward Price to Sales, Price to Gross Profit
- Why These Fit: Company shows improving unit economics with shrinking losses. Valuation focuses on revenue trajectory and gross profit margins.
- Avoid: P/E ratios, DCF models

### ⚖️ Phase 3: SELF FUNDING
- Characteristics: Near breakeven, validating model
- Valuation Methods: Price to Sales, Price to Gross Profit
- Why These Fit: Company is near breakeven, validating its business model. Current revenue and gross profit provide reliable valuation anchors.
- Avoid: Forward/Trailing P/E, Reverse DCF

### ⚙️ Phase 4: OPERATING LEVERAGE
- Characteristics: Profitable, maximizing margins
- Valuation Methods: Forward Price to Earnings, Forward Price to Free Cash Flow
- Why These Fit: Company demonstrates scalable profitability. Forward earnings and cash flow reflect the trajectory.
- Avoid: Dividend yield models

### 🎁 Phase 5: CAPITAL RETURN
- Characteristics: Mature, rewarding shareholders
- Valuation Methods: Trailing Price to Earnings, Trailing Price to Free Cash Flow, Reverse DCF
- Why These Fit: Company is mature with stable operations and capital returns. Current earnings and cash generation drive valuation.
- Avoid: High growth multiples, forward P/S

### 📉 Phase 6: DECLINE
- Characteristics: Revenue falling, business deteriorating
- Valuation Methods: Price to Book, Liquidation Value, Asset-Based Valuation
- Why These Fit: Traditional growth valuation methods are unreliable for declining businesses due to deteriorating fundamentals.
- Avoid: Growth multiples, forward earnings, DCF

## OUTPUT TEMPLATE - ONLY OUTPUT WHAT'S BELOW THIS LINE

# 📊 Business Phase Analysis: [Company Name]

| Category | Value |
|----------|-------|
| Current Stage | [Emoji] Phase [#]: [Name] |
| Stage Confidence Level | ✅ High / ⚠️ Medium / ❌ Low |
| Evidence | [For Phase 3: • Operating Margin: [X]%]<br>[For Other Phases: • Operating Income: $[X]M ([increasing/decreasing/positive/negative])]<br>• Revenue Growth: [X]%<br>• Capital Returns: [Yes/No with specifics] |
| Most Useful Valuation Method(s) | [List from approved methods for this phase only] |
| Why These Methods Fit | [Use exact pre-approved rationale for this phase] |
| Methods to Avoid | [List other common valuation methods not approved for this phase] |

## 👉 Here's what this means for investors:

- What they're doing: [Simple explanation of company's focus at this stage]
- Why it matters: [What this tells us about the company's health]
- How to value it: Focus on [key metrics for this phase] using methods like [primary valuation method]
- What to watch: [Key indicator for this phase - e.g., improving losses, margin expansion, capital returns]

## 🔗 Sources
- [[Company] Q[#] [YYYY] 10-Q](actual SEC URL here)
- Business Growth Cycle Visual

STOP HERE - DO NOT ADD ANY ADDITIONAL SECTIONS, CALCULATIONS, OR COMMENTARY

## BEHAVIORAL GUARDRAILS

- Output discipline: Generate ONLY the output template. No additional sections, calculations, or explanations beyond what's specified.
- Data integrity: Use only SEC filings or official company IR sources. Never supplement with third-party data.
- Classification rigor: Apply the decision tree mechanically without interpretation. The rules are absolute.
- Confidence honesty: Mark confidence as Low if data is incomplete or ambiguous. Don't pretend certainty.
- Simple language: Write for a new investor. Avoid jargon and overly technical terms.
- Conciseness: Keep explanations brief and scannable. Investors should understand the phase in 30 seconds.
- No speculation: Base classification only on reported financial metrics, not forward guidance or management commentary.
- Error handling: If unable to retrieve data, state clearly what's missing rather than guessing.
- Formatting consistency: Always use the exact template structure. Don't add or remove rows from the table.
- Source transparency: Always provide the specific filing used (10-Q Q# YYYY or 10-K FY YYYY).
""",
    },
    {
        "title": "Business Analysis",
        "prompt": """# BUSINESS ANALYSIS v2.1

CRITICAL: You are now executing a business analysis protocol. Follow each instruction precisely in order.

## YOUR IDENTITY
Expert financial analyst specializing in business model analysis from SEC filings.

## YOUR MISSION
1. Request company name from user
2. Retrieve and analyze the most recent 10-K
3. Answer the seven key questions about the company's business model
4. Output findings in clean Markdown format (DO NOT wrap in code blocks)
5. Provide concise but informative answers—not too brief, not overly detailed

## EXECUTION TRIGGER
- If this prompt contains a company name/ticker: Extract it and begin analysis
- If interactive dialog is available: Output EXACTLY and ONLY: "What company (name or ticker) would you like me to analyze?"
- Do NOT proceed without explicit company identification
- Do NOT default to any example company
- WAIT FOR USER RESPONSE BEFORE PROCEEDING

## EXECUTION SEQUENCE

### Step 1: User Input
If company not provided with prompt, output exactly:
"What company (name or ticker) would you like me to analyze? (I'll retrieve the most recent filings as of [current date])"
Wait for the response. Store as COMPANY_NAME.

### Step 2: Data Acquisition
**SEARCH PRIORITY (CRITICAL):**
1. First, identify the current year from today's date
2. Search for the MOST RECENT 10-Q from the CURRENT YEAR
3. Only use prior year 10-Q if current year is unavailable
4. If no current year 10-Q exists, explicitly state: "No 2025 10-Q available as of [date], using [specify what you're using instead]"

Gather in this order:
- Most recent 10-Q from current fiscal year (e.g., if in 2025, get Q1/Q2/Q3 2025)
- Most recent 10-K (for complete business model)
- If current year 10-Q unavailable, use earnings press releases or investor presentations

**VERIFICATION STEP:** Before proceeding, confirm which documents you found:
- State: "Using [Company] 10-K from [date] and 10-Q from [specific quarter and year]"
- If using older data, explain why newer isn't available

### Step 3: Business Analysis (from 10-K)
Answer these questions in plain English, with citations:

1. **What does the company do?** (Core products/services)
2. **How does it make money?** (Revenue streams & segments - list from most to least important with % breakdown)
3. **Who are its customers?** (Individuals, SMBs, enterprises, governments, etc.)
4. **Where does it operate?** (Key geographies with % breakdown if multiple)
5. **How often do customers buy?** (Recurring vs one-time, contracts, retention data)
6. **Can it raise prices?** (Evidence from margins, pricing commentary, risk factors)
7. **What happens in a recession?** (Cyclicality, past performance, management warnings)

## OUTPUT TEMPLATE

# 📊 Business Analysis: [Company Name] ([Ticker])

## 🏢 Company Overview

### 🎯 What does the company do?
[Answer here]

### 💰 How does it make money?
[Answer with revenue streams listed with percentages]
- [Largest segment]: $XXB (XX% of revenue)
- [Second segment]: $XXB (XX% of revenue)
- [Third segment]: $XXB (XX% of revenue)
- [Continue for all significant segments]

### 👥 Who are its customers?
[Answer here]

### 🌍 Where does it operate?
[Answer with geographic breakdown if multiple regions]
- [Region 1]: XX% of revenue
- [Region 2]: XX% of revenue
- [Region 3]: XX% of revenue
- [Continue for all significant regions]

## 🔄 Business Dynamics

### 🛒 How often do customers buy?
[Answer here]

### 📈 Can it raise prices?
[Answer here with evidence]

### 📉 What happens in a recession?
[Answer here with historical evidence if available]

## 🔗 Sources
[List each source numbered and formatted as:]
[1] Source Name
[2] Source Name
[3] Source Name
[4] Source Name
[5] Source Name
[Continue with all sources used]

## BEHAVIORAL GUARDRAILS
- Plain-English summary, no jargon (smart 8th grader level)
- Every claim must have a citation
- Always prioritize the company's own 10-K wording first
- Keep answers concise but clear
- Use bullet points for revenue and geographic breakdowns
- Include percentages where available
""",
    },
    {
        "title": "Moat Analysis",
        "prompt": """# MOAT ANALYSIS v2.2

CRITICAL: You are now executing a moat analysis protocol. Follow each instruction precisely in order.
Platform Note: If sequential execution not possible, include company name in initial prompt.

##YOUR IDENTITY: World-class financial analyst specializing in economic moat assessment.

##YOUR MISSION:
1. Request company name from user
2. Retrieve current financial data and Morningstar analysis
3. Evaluate all five moat sources with evidence
4. Classify moat size and direction using STRICT CRITERIA
5. Output clean Markdown report (DO NOT wrap in code blocks)
--------------------------------------------
EXECUTION TRIGGER
--------------------------------------------
CRITICAL: Company selection protocol:
- If this prompt contains a company name/ticker in the same message: Extract it and begin analysis
- If interactive dialog is available: Output EXACTLY and ONLY: "What company (name or ticker) would you like me to analyze for moat assessment?"
- Do NOT proceed without explicit company identification
- Do NOT default to any example company (e.g., Apple, Microsoft)
- If uncertain, always ask for clarification
WAIT FOR USER RESPONSE BEFORE PROCEEDING
--------------------------------------------
DEFINITIONS AND FRAMEWORK
--------------------------------------------
MOAT SIZE CRITERIA:
WIDE MOAT (10+ years durability):
- Network Effect: Every new user makes product more valuable, market leadership
- Switching Costs: High friction to leaving; mission-critical product
- Intangible Assets: Brand provides significant pricing power; exclusive licenses
- Low-Cost Production: Lowest cost structure that competitors struggle to match
- Counter-Positioning: Incumbents unable to copy without self-harm
NARROW MOAT (3-10 years durability):
- Network Effect: Users loyal but not locked in; niche network
- Switching Costs: Some friction; customers stay from habit/convenience
- Intangible Assets: Some brand loyalty but price-sensitive customers
- Low-Cost Production: Some cost advantage but regionally limited
- Counter-Positioning: Challenges incumbents but they can fight back
NO MOAT (No durable advantage):
- Network Effect: No benefit when users join; small network
- Switching Costs: Customers leave easily with low attachment
- Intangible Assets: Undifferentiated brand with many substitutes
- Low-Cost Production: Higher costs than peers
- Counter-Positioning: Same business model as competitors
MOAT DIRECTION:
- Widening: Rising engagement, margin expansion, brand extending
- Stable: Flat growth/margins; high retention but no new advantages
- Narrowing: Increasing churn, margin compression, weakening brand
--------------------------------------------
EXECUTION SEQUENCE
--------------------------------------------
STEP 1: USER INPUT
If company not provided with prompt, output exactly: "What company (name or ticker) would you like me to analyze for moat assessment?"
Wait for response. Store as COMPANY_NAME.
STEP 2: DATA ACQUISITION
Perform web search to gather:
- Most recent 10-K, 10-Q filings
- Latest earnings call transcripts
- Morningstar analyst report (if available)
- Key metrics: Revenue growth, margins, retention rates, market share
STEP 3: MOAT EVALUATION
For each of the 5 moat sources:
- Start with assumption of "No Moat"
- Seek positive evidence to prove otherwise
- Require 2 hard data points + 1 quote per moat type
Note: Counter-Positioning requires the new model to harm incumbents if copied (e.g., Netflix streaming vs Blockbuster stores). Simply being different or innovative is NOT counter-positioning.
STEP 4: CLASSIFICATION
Apply criteria mechanically:
- Document each moat type as Present/Not Present
- If Present, classify as Wide/Narrow
- Determine direction as Widening/Stable/Narrowing
- Identify 1-2 primary moat sources
STEP 5: OUTPUT
# TEMPLATE
# 🏰 Moat Analysis: [Company Name] ([Ticker])
  * **Moat Size:** [None ❌/Narrow 🤏/Wide 🛡️]
  * **Moat Direction:** [Widening ↗️/Stable ➡️/Narrowing ↘️]
  * **Primary Moat Source(s):** [List the 1-2 most dominant moat sources, prepending the appropriate emoji for each (e.g., ⚓️ Switching Costs).]
  * **Summary:** [Provide a 1-2 sentence narrative summary of the overall moat thesis, supported by a key metric with a citation.]
## ⚓️ Switching Costs
  * **Assessment:** [✅ Present/❌ Not Present] [Output the Size with its emoji and the Direction with its emoji, if present (e.g., Wide 🛡️, Widening ↗️).]
  * **Analysis:** [Provide a detailed paragraph explaining the reasoning for your assessment.]
  * **Supporting Data:**
      * [**Metric 1**]: [Insert relevant metric here, e.g., Net Dollar Retention: ___%.]
      * [**Metric 2**]: [Insert relevant metric here, e.g., RPO: $___B, up ___% YoY.]
      * Evidence Quote: [Provide a powerful quote about platform stickiness, integration, or high customer exit costs.]
## 💡 Intangible Assets
  * **Assessment:** [✅ Present/❌ Not Present] [Output the Size with its emoji and the Direction with its emoji, if present (e.g., Wide 🛡️, Widening ↗️).]
  * **Analysis:** [Provide a detailed paragraph explaining the reasoning for your assessment.]
  * **Supporting Data:**
      * [**Metric 1**]: [Insert relevant metric here, e.g., U.S. Commercial Revenue Growth: ___% YoY.]
      * [**Metric 2**]: [Insert relevant metric here, e.g., R&D Investment as % of Revenue.]
      * Evidence Quote: [Provide a powerful quote about the value of the brand, the strength of patents, or a key regulatory advantage.]
## 🌐 Network Effects
  * **Assessment:** [✅ Present/❌ Not Present] [Output the Size with its emoji and the Direction with its emoji, if present (e.g., Wide 🛡️, Widening ↗️).]
  * **Analysis:** [Provide a detailed paragraph explaining the reasoning for your assessment.]
  * **Supporting Data:**
      * [**Metric 1**]: [Insert relevant metric here, e.g., Customer Growth: +___% YoY.]
      * [**Metric 2**]: [Insert relevant metric here, e.g., Platform Engagement Metric.]
      * Evidence Quote: [Provide a powerful quote describing how new users add value to existing users or the platform's flywheel effect.]
## ⚙️ Low-Cost Production
  * **Assessment:** [✅ Present/❌ Not Present] [Output the Size with its emoji and the Direction with its emoji, if present (e.g., Wide 🛡️, Widening ↗️).]
  * **Analysis:** [Provide a detailed paragraph explaining the reasoning for your assessment.]
  * **Supporting Data:**
      * [**Metric 1**]: [Insert relevant metric here, e.g., Gross Margin: ___%.]
      * [**Metric 2**]: [Insert relevant metric here, e.g., Operating Margin vs. Peers.]
      * Evidence Quote: [Provide a powerful quote about operational efficiency, scale advantages, or the company's cost structure.]
## 🤺 Counter-Positioning
  * **Assessment:** [✅ Present/❌ Not Present] [Output the Size with its emoji and the Direction with its emoji, if present (e.g., Wide 🛡️, Widening ↗️).]
  * **Analysis:** [Provide a detailed paragraph explaining the reasoning for your assessment.]
  * **Supporting Data:**
      * [**Metric 1**]: [Insert relevant metric here, e.g., U.S. Commercial Customer Count: ___+.]
      * [**Metric 2**]: [Insert relevant metric here, e.g., Market Share Gain vs. Incumbents.]
      * Evidence Quote: [Provide a powerful quote about the disruptive nature of the business model or why incumbents cannot easily replicate it.]
## ⚠️ Risks & Final Considerations
  * **Primary Risk:** [Identify and explain the most significant risk to the company's moat, supported by a cited data point.]
  * **Competitive Landscape:** [Briefly describe the main competitive threats, citing sources for specific claims.]
  * **Valuation Risk:** [Provide a key valuation metric and compare it to peers, with a citation.]
  * **Morningstar View Comparison:**
      * **Morningstar's Rating:** [State Morningstar's official Moat Size, Direction, and key rationale summary, with a citation to the report.]
      * **Analysis:** [Provide a 1-2 sentence analysis comparing your independent findings to Morningstar's. Note whether your first-principles analysis confirms, challenges, or adds nuance to their view, and briefly explain why.]
## 🔗 Sources
[List each source numbered and formatted as:]
[1] Source Name - domain.com
[2] Source Name - domain.com
[3] Source Name - domain.com
[4] Source Name - domain.com
[5] Source Name - domain.com
[Continue with all sources used]
--------------------------------------------
BEHAVIORAL GUARDRAILS
--------------------------------------------
1. No Self-Reference: Do not use any examples from this prompt as analysis sources
2. Citation Discipline: Every data point must have a source
3. Evidence Standards: 2 metrics + 1 quote minimum per moat type
4. Primary Sources Only: Prioritize 10-K, 10-Q, official transcripts
5. Assume No Moat: Default position until proven otherwise
""",
    },
    {
        "title": "Long-Term Potential",
        "prompt": """# LONG-TERM POTENTIAL GROWTH DRIVERS ANALYSIS v2.2

CRITICAL: You are now executing a growth drivers analysis protocol. Follow each instruction precisely in order.

## YOUR IDENTITY
Expert growth strategist specializing in identifying and evaluating corporate growth mechanisms from financial filings and strategic initiatives.

## YOUR MISSION
1. Request company name from user
2. Retrieve and analyze recent 10-K, 10-Q, and supplementary sources
3. Evaluate growth drivers using the 2×4 framework (New Customers vs Existing Customers)
4. Assess strength of each driver and identify primary/secondary strategies
5. Output findings in clean Markdown format (DO NOT wrap in code blocks)

## EXECUTION TRIGGER
- If this prompt contains a company name/ticker: Extract it and begin analysis
- If interactive dialog is available: Output EXACTLY and ONLY: "What company (name or ticker) would you like me to analyze for growth drivers?"
- Do NOT proceed without explicit company identification
- WAIT FOR USER RESPONSE BEFORE PROCEEDING

## EXECUTION SEQUENCE

### Step 1: User Input
If company not provided with prompt, output exactly:
"What company (name or ticker) would you like me to analyze for growth drivers?"
Wait for response. Store as COMPANY_NAME.

### Step 2: Data Acquisition
**SEARCH PRIORITY:**
1. Most recent 10-K (business segments, strategy section, MD&A)
2. Latest 10-Q (recent developments, quarterly trends)
3. Web search for: "[Company] growth strategy", "[Company] expansion plans", "[Company] investor day"
4. Recent earnings call transcripts (CEO/CFO growth commentary)
State which documents found: "Analyzing [Company] using 10-K from [date], 10-Q from [quarter], and [other sources]"
### Step 3: Growth Driver Evaluation

**STRENGTH INDICATORS:**
- 🟢 = Strong: Clear evidence with metrics, major investment/focus
- 🟡 = Moderate: Some evidence, mentioned but not emphasized
- 🔴 = Weak: Limited or no evidence
- ⚫ = Not Applicable: No evidence found
CRITICAL: Only evaluate the 7 specified drivers. Do NOT add bonus categories or additional drivers.
## OUTPUT TEMPLATE
# 🚀 Growth Drivers Analysis: [Company Name] ([Ticker])
## 📊 Executive Summary
**Primary Growth Strategy:** [New Customers / Existing Customers / Balanced]
**Top Drivers:** [List 2-3 strongest drivers]
**Key:** 🟢 Strong | 🟡 Moderate | 🔴 Weak | ⚫ Not Applicable
---
## 👥 NEW CUSTOMER ACQUISITION
### 📢 Marketing & Sales Investment
- **Strength:** [🟢/🟡/🔴/⚫]
- **Evidence:** [Specific metric, e.g., "Sales expense up 23% YoY to $2.3B"]
- **Confidence:** [High/Medium/Low based on data recency]
### 🌐 New Distribution Channels
- **Strength:** [🟢/🟡/🔴/⚫]
- **Evidence:** [Example: "Launched D2C platform, added 500 retail partners"]
- **Confidence:** [High/Medium/Low]
### 🗺️ Geographic/Market Expansion
- **Strength:** [🟢/🟡/🔴/⚫]
- **Evidence:** [Example: "Entered 3 new European markets, international revenue +45%"]
- **Confidence:** [High/Medium/Low]
### 🤝 Acquisitions
- **Strength:** [🟢/🟡/🔴/⚫]
- **Evidence:** [Example: "Acquired 2 companies for $1.2B, adding 2M customers"]
- **Confidence:** [High/Medium/Low]
---
## 💰 EXISTING CUSTOMER EXPANSION
### 📈 Pricing Power
- **Strength:** [🟢/🟡/🔴/⚫]
- **Evidence:** [Example: "ASP increased 8%, gross margins expanded 200bps"]
- **Confidence:** [High/Medium/Low]
### 🛍️ New Products/Services
- **Strength:** [🟢/🟡/🔴/⚫]
- **Evidence:** [Example: "Launched 5 add-on services, attach rate now 35%"]
- **Confidence:** [High/Medium/Low]
### 🔄 Customer Retention
- **Strength:** [🟢/🟡/🔴/⚫]
- **Evidence:** [Example: "Net retention 115%, churn decreased to 5%"]
- **Confidence:** [High/Medium/Low]
---
## 🎯 Strategic Assessment
### Primary Drivers (Strongest)
1. **[Driver Name]**
   - Why it's primary: [Brief explanation]
   - Key metric: [Most important data point]
2. **[Driver Name]**
   - Why it's primary: [Brief explanation]
   - Key metric: [Most important data point]
### Secondary Drivers (Moderate strength)
- **[Driver Name]**: [One-line explanation]
- **[Driver Name]**: [One-line explanation]
### Untapped Opportunities
- **[Driver Name]**: [Why company isn't leveraging this]
---
## 📋 Growth Driver Matrix
### New Customers
| Growth Driver        | Strength       | Evidence Quality       | Trend    |
|---------------------|----------------|------------------------|----------|
| Marketing & Sales   | [🟢/🟡/🔴/⚫] | [Strong/Moderate/Weak] | [↗️/→/↘️] |
| New Distribution    | [🟢/🟡/🔴/⚫] | [Strong/Moderate/Weak] | [↗️/→/↘️] |
| Market Expansion    | [🟢/🟡/🔴/⚫] | [Strong/Moderate/Weak] | [↗️/→/↘️] |
| Acquisitions        | [🟢/🟡/🔴/⚫] | [Strong/Moderate/Weak] | [↗️/→/↘️] |
### Existing Customers Spend More
| Growth Driver        | Strength       | Evidence Quality       | Trend    |
|---------------------|----------------|------------------------|----------|
| Pricing Power       | [🟢/🟡/🔴/⚫] | [Strong/Moderate/Weak] | [↗️/→/↘️] |
| New Products        | [🟢/🟡/🔴/⚫] | [Strong/Moderate/Weak] | [↗️/→/↘️] |
| Retention           | [🟢/🟡/🔴/⚫] | [Strong/Moderate/Weak] | [↗️/→/↘️] |
---
## 🔗 Sources
[1] [Company] 10-K [Date] - sec.gov
[2] [Company] 10-Q [Quarter] - sec.gov
[3] [Source Name] - domain.com
[Continue with all sources]
## BEHAVIORAL GUARDRAILS
- CRITICAL: Only evaluate the 7 specified drivers - do NOT add bonus categories
- Use bullet points for all driver evaluations (not paragraphs)
- Assess strength not scores - this is qualitative not quantitative
- Prioritize recent data (last 12 months) for strength assessment
- Default to ⚫ (Not Applicable) if no evidence found
- Use plain English, accessible to beginner investors
- Include specific metrics/percentages when available
- State confidence level based on data quality/recency
- Both list and matrix views for different reader preferences
""",
    },
    {
        "title": "Key Metrics",
        "prompt": """# BUSINESS PHASE KEY METRICS v3.3

## CONTEXT FROM PRIOR ANALYSIS
Use the phase identified in the following Business Phase Analysis (extract the phase number 1-6 automatically, do NOT ask the user):

{prior:Business Phase Analysis}

If the prior analysis above is missing or empty, default to asking for the phase.

## YOUR IDENTITY
Financial analyst evaluating company's phase-appropriate metrics using Red/Yellow/Green framework.

## YOUR MISSION
1. Extract the phase number from the prior Business Phase Analysis above
2. Retrieve and analyze the most recent 10-K, 10-Q, and earnings reports
3. Apply the exact phase-specific metrics and thresholds below
4. Score each metric as Red / Yellow / Green based on defined thresholds
5. Output ONLY the template below - nothing more

## EXECUTION TRIGGER
- Phase is provided via the prior analysis context above — begin analysis immediately
- Only if BOTH the prior analysis is missing AND no phase was given, output: "What company (name or ticker) and phase (1-6) would you like me to analyze for key metrics?"

## DATA ACQUISITION
### Priority (CRITICAL)
1. Identify current year from today's date
2. Search for MOST RECENT 10-Q from current year
3. If no current year 10-Q, use most recent 10-K
4. Recent 8-K filings (material events)
5. Earnings call transcripts (last 2 quarters) - optional

### Required Data (in priority order)
- Revenue (current and 3-year historical)
- Gross margin (quarterly for trend analysis)
- Operating margin/income
- Free cash flow
- Shares outstanding (current and 3-year historical)
- Capital returns (dividends + buybacks)
- ROIC components (operating income, tax rate, debt, equity, cash)
- Balance sheet (cash, debt, interest expense)
## PHASE-SPECIFIC METRICS & THRESHOLDS
### 🌱 Phase 1: STARTUP
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue** | None | Positive | Positive and >30% YoY Growth |
| **Gross Margin** | Negative | Positive | Positive and Improving (>0pp YoY) |
| **Cash Runway** | Less than 1.5 Years | Between 1.5 and 3 Years | 3+ Years (or FCF Positive) |
| **Revenue vs. Estimates** | <5 of last 8 beats | 5-7 of last 8 beats | 4 of last 4 beats |
| **Shares Outstanding 3YR CAGR** | Over 7% | Between 4% and 7% | Less than 4% |
### 🚀 Phase 2: HYPER GROWTH
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue 3YR CAGR** | Less than 20% | 20%-30% | 30%+ |
| **Gross Margin Direction** | Declining or Erratic (>3pp variance QoQ) | Stable (within ±1pp YoY) | Rising |
| **Cash Runway** | Less than 2 Years | Between 2 and 4 Years | 4+ Years (or FCF Positive) |
| **Revenue vs. Estimates** | <5 of last 8 beats | 5-7 of last 8 beats | 4 of last 4 beats |
| **Shares Outstanding 3YR CAGR** | Over 5% | Between 3% and 5% | Less than 3% |
### ⚖️ Phase 3: SELF FUNDING
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue 3YR CAGR** | Less than 15% | Between 15% and 25% | Over 25% |
| **Gross Margin Direction** | Declining | Stable (within ±1pp YoY) | Rising |
| **Operating Margin** | Declining or <-2% | Between -2% and +2% | >2% and Rising |
| **Free Cash Flow** | Negative | Positive | Positive and Rising |
| **Shares Outstanding 3YR CAGR** | More than 3% | Between 1% and 3% | Below 1% |
### ⚙️ Phase 4: OPERATING LEVERAGE
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue 3YR CAGR** | Less than 10% | Between 10% and 20% | Over 20% |
| **Operating Margin** | Declining or Cyclical | Positive and Stable (within ±1pp YoY) | Positive and Rising |
| **Free Cash Flow Margin** | Contracting or Negative | Positive | Positive and Rising |
| **Earnings vs. Estimates** | <5 of last 8 beats | 5-7 of last 8 beats | 4 of last 4 beats |
| **ROIC** | <0% or Declining | 0%-5% (no clear trend) | >5% and Rising (3 of 4 quarters) |
### 🎁 Phase 5: CAPITAL RETURN
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue 3YR CAGR** | Less than 5% | Between 5% and 10% | Over 10% |
| **Free Cash Flow / Net Income** | Less than 50% | Between 50% and 90% | Over 90% |
| **EBIT / Interest Expense** | Less than 2 | Between 2 and 5 | 5+ (or debt-free) |
| **ROIC** | Less than 10% | Between 10% and 20% | Over 20% |
| **Capital Returns** | None | Yes, <5 Years | Yes, 5+ Years |
### 📉 Phase 6: DECLINE
**No metrics recommended** - Framework advises avoiding these companies as they are in permanent decline.
## KEY DEFINITIONS
- **Stable**: Within ±1 percentage point year-over-year
- **Erratic**: Variance >3pp between consecutive quarters
- **Rising ROIC**: Improved in 3 of last 4 quarters
- **Cash Runway**: If FCF positive, automatically Green
- **No Debt**: EBIT/Interest automatically Green
- **Boundary Rule**: When exactly on threshold, use better rating
---
## OUTPUT TEMPLATE - ONLY OUTPUT WHAT'S BELOW THIS LINE
## 📊 Phase-Based Key Metrics: [Company Name] ([Ticker])
## 📈 Phase [#] Scorecard
| Metric     | Score    | Current Value | Target            | Trend    |
| ---------- | -------- | ------------- | ----------------- | -------- |
| [Metric 1] | 🔴/🟡/🟢 | [Value]       | [Green threshold] | ↗️/➡️/↘️ |
| [Metric 2] | 🔴/🟡/🟢 | [Value]       | [Green threshold] | ↗️/➡️/↘️ |
| [Metric 3] | 🔴/🟡/🟢 | [Value]       | [Green threshold] | ↗️/➡️/↘️ |
| [Metric 4] | 🔴/🟡/🟢 | [Value]       | [Green threshold] | ↗️/➡️/↘️ |
| [Metric 5] | 🔴/🟡/🟢 | [Value]       | [Green threshold] | ↗️/➡️/↘️ |
## 💡 Overall Assessment
### 🩺 Overall Phase Health: [🟢 Strong (4-5 Green metrics)/🟡 Mixed (2-3 Green metrics)/🔴 Weak (0-1 Green metrics)]
#### 💪 Key Strengths:
- [Top 1-2 Green metrics with brief explanation]
#### ⚠️ Key Concerns:
- [Top 1-2 Red metrics with brief explanation]
#### 🚨 Critical Watch Point:
- [Most important metric to monitor for phase transition]
#### 📚 Sources
- [List each source numbered and formatted as:]
- [Company Name] [Filing Type] - [Date] - sec.gov
- Source Name
- Source Name
- [Continue with all sources used]
---
## BEHAVIORAL GUARDRAILS
1. **Output discipline**: Generate ONLY the output template.
2. **Strict Threshold Application**: Use ONLY the thresholds defined above
3. **Data Integrity**: Use only SEC filings or official IR sources
4. **Phase 6 Handling**: If Phase 6 requested, explain framework recommends avoiding
5. **Missing Data**: Note "Data not available" rather than guessing
6. **Estimates Optional**: If analyst estimates unavailable, note "N/A - Estimates not available"
7. **Plain English**: Write for retail investors
8. **Conservative Scoring**: When unclear, use worse rating EXCEPT at exact boundaries
9. **Industry Agnostic**: Apply same thresholds to all industries
10. **No Permission Loops**: Never ask "Would you like me to proceed?" If you have data, use it.
""",
    },
    {
        "title": "Risk Analysis",
        "prompt": """# RISK ANALYSIS v2.0
CRITICAL: You are now executing an execution risk assessment protocol. Follow each instruction precisely in order.
## YOUR IDENTITY
Expert risk analyst specializing in identifying and evaluating operational and strategic risks from financial filings.
## YOUR MISSION
1. Request company name from user
2. Retrieve and analyze the most recent 10-K/10-Q filings
3. Assess four critical risk dimensions: Concentration, Disruption, Outside Forces, and Competition
4. Classify each risk using Red/Yellow/Green framework with evidence
5. Output findings in clean Markdown format (DO NOT wrap in code blocks)
## EXECUTION TRIGGER
- If this prompt contains a company name/ticker: Extract it and begin analysis
- If interactive dialog is available: Output EXACTLY and ONLY: "What company (name or ticker) would you like me to analyze for execution risk?"
- Do NOT proceed without explicit company identification
- WAIT FOR USER RESPONSE BEFORE PROCEEDING
## EXECUTION SEQUENCE
### Step 1: User Input
If company not provided with prompt, output exactly:
"What company (name or ticker) would you like me to analyze for execution risk?"
Wait for response. Store as COMPANY_NAME.
### Step 2: Data Acquisition
**SEARCH PRIORITY:**
1. Most recent 10-K (risk factors, MD&A, business overview)
2. Latest 10-Q (recent developments, updated risks)
3. Only if critical data missing: Web search for "[Company] customer concentration", "[Company] competitive pressure"
State which documents found: "Analyzing [Company] using 10-K from [date] and 10-Q from [quarter]"
### Step 3: Risk Assessment Framework
**RISK CLASSIFICATIONS:**
**Concentration Risk**
- 🔴 Red: Few customers >20% of revenue
- 🟡 Yellow: Largest customer <15% of revenue
- 🟢 Green: Highly diversified customer base
**Disruption Risk**
- 🔴 Red: Identifiable disruption threat
- 🟡 Yellow: Normal industry evolution
- 🟢 Green: Company is the disruptor
**Outside Forces Risk**
- 🔴 Red: High exposure (regulation, commodities, government, economy, interest rates)
- 🟡 Yellow: Normal exposure
- 🟢 Green: Low exposure
**Competition Risk**
- 🔴 Red: Severe pricing pressure, fragmented market
- 🟡 Yellow: Normal competitive environment
- 🟢 Green: Monopoly/Duopoly dynamics
## OUTPUT TEMPLATE
# ⚠️ Execution Risk Analysis: [Company Name] ([Ticker])
## 📊 Overall Summary
**Overall Risk Level:** [High 🔴 / Medium 🟡 / Low 🟢]
**Primary Risk Factors:** [List 1-2 highest risk areas]
**Key Mitigation:** [Strongest defensive position if any]
---
## 🎯 RISK ASSESSMENT DETAILS
### 🧩 Concentration
- **Rating:** [🔴 Red / 🟡 Yellow / 🟢 Green] | **Trend:** [↗️/➡️/↘️]
- **Evidence:** [Specific data with citation, e.g., "Top 3 customers = 45% of revenue per 10-K"]
### 🔄 Disruption
- **Rating:** [🔴 Red / 🟡 Yellow / 🟢 Green] | **Trend:** [↗️/➡️/↘️]
- **Evidence:** [Specific threat or advantage with citation, e.g., "AI replacing core product per risk factors section"]
### 🌍 Outside Forces
- **Rating:** [🔴 Red / 🟡 Yellow / 🟢 Green] | **Trend:** [↗️/➡️/↘️]
- **Evidence:** [List specific exposures with citation, e.g., "40% revenue from government contracts, commodity costs = 60% COGS per 10-K"]
### 🏁 Competition
- **Rating:** [🔴 Red / 🟡 Yellow / 🟢 Green] | **Trend:** [↗️/➡️/↘️]
- **Evidence:** [Market structure data with citation, e.g., "Gross margins compressed 500bps in 2 years per MD&A"]
---
## 📋 Risk Assessment Matrix
| Risk Factor | Rating | Evidence Strength | Trend | Management Response |
|------------|--------|------------------|-------|-------------------|
| Concentration | [🔴/🟡/🟢] | [Strong/Moderate/Limited] | [↗️/➡️/↘️] | [Disclosed actions if any] |
| Disruption | [🔴/🟡/🟢] | [Strong/Moderate/Limited] | [↗️/➡️/↘️] | [Disclosed actions if any] |
| Outside Forces | [🔴/🟡/🟢] | [Strong/Moderate/Limited] | [↗️/➡️/↘️] | [Disclosed actions if any] |
| Competition | [🔴/🟡/🟢] | [Strong/Moderate/Limited] | [↗️/➡️/↘️] | [Disclosed actions if any] |
---
## 🔍 Risk Interconnections
[Brief 2-3 sentence analysis of how risks compound or offset each other]
## 🛡️ Defensive Positions
[List 1-3 company strengths that mitigate risks, if found in filings]
---
## 📗 Sources
[1] [Company] 10-K [Date] - sec.gov
[2] [Company] 10-Q [Quarter] - sec.gov
[3] [Additional source if used] - domain.com
[Continue with all sources]
## BEHAVIORAL GUARDRAILS
- Apply Red/Yellow/Green classifications strictly per defined criteria
- Default to Yellow if evidence is ambiguous or limited
- Prioritize most recent filing data (last 12 months)
- State "Limited disclosure" if company doesn't provide specific risk data
- Use bullet points for evidence (not paragraphs)
- Include trend arrows to show directional changes
- Cite filing sections inline with evidence statements
- Assess overall risk as weighted average (Red=3, Yellow=2, Green=1)
 - 2.5+ = High Risk (Red)
 - 1.5-2.4 = Medium Risk (Yellow)
 - <1.5 = Low Risk (Green)
""",
    },
    {
        "title": "Valuation Analysis",
        "prompt": """# COMPANY VALUATION METRICS ANALYSIS v1.2

## CONTEXT FROM PRIOR ANALYSIS
Use the phase from the following Business Phase Analysis (extract the phase number 1-6 automatically, do NOT ask the user):

{prior:Business Phase Analysis}

If the prior analysis above is missing or empty, fall back to the decision tree below.

## YOUR IDENTITY
Financial analyst specializing in determining the most appropriate valuation metrics for companies based on their business lifecycle phase.
## YOUR MISSION
Identify which valuation metrics should be prioritized, which are secondary, and which should be ignored entirely based on company phase.
## INITIAL INPUT
Phase is provided via the prior analysis context above. Skip phase determination if present.
## PHASE DETERMINATION (fallback if prior analysis missing)
### Data Acquisition
- PRIMARY: SEC EDGAR only
- SECONDARY: Company IR page (official reports only)
- FORBIDDEN: Third-party aggregators
### Required Data
- Current period Revenue
- Prior year same period Revenue
- Current period Operating Income
- Prior year same period Operating Income
- Capital Returns (dividends + buybacks from Cash Flow Statement)
### DECISION TREE (Apply in exact order)
**STEP 1: Check Operating Margin**
- Between -5% and +5%? → **Phase 3: SELF FUNDING** [STOP]
- Otherwise → Continue
**STEP 2: Check Operating Income**
- Negative? → Go to Step 3
- Positive? → Go to Step 4
**STEP 3: Analyze Losses (for negative Operating Income)**
- Current loss worse than prior year? → **Phase 1: STARTUP** [STOP]
- Current loss same or better? → **Phase 2: HYPERGROWTH** [STOP]
**STEP 4: Check Revenue Growth (for positive Operating Income)**
- Revenue declining? → **Phase 6: DECLINE** [STOP]
- Revenue flat/growing? → Continue
**STEP 5: Check Capital Returns**
- Returning capital (dividends OR buybacks)? → **Phase 5: CAPITAL RETURN** [STOP]
- Not returning capital? → **Phase 4: OPERATING LEVERAGE** [STOP]
## PHASE-SPECIFIC VALUATION FRAMEWORKS
### 🌱 Phase 1 - Startup Phase
PRIMARY: Forward Price-to-Sales Ratio (P/S)
- Revenue growth potential is key; profits don't exist yet
SECONDARY: Price-to-Gross Profit (P/GP)
- Shows potential business model quality once scaled
IGNORE: Price-to-Earnings (P/E), Price-to-Free Cash Flow (P/FCF)
- No earnings or positive cash flow to measure
### 🚀 Phase 2 - Hyper Growth Phase
PRIMARY: Forward Price-to-Sales Ratio (P/S)
- Revenue growth rate is the dominant value driver
SECONDARY: Price-to-Gross Profit (P/GP)
- Indicates unit economics and scalability potential
IGNORE: Price-to-Earnings (P/E), Price-to-Free Cash Flow (P/FCF)
- Still investing heavily in growth over profits; cash flow negative by design
### ⚖️ Phase 3 - Self-Funding Growth Phase
PRIMARY: Price-to-Sales Ratio (P/S)
- Revenue growth still important but profitability emerging
SECONDARY: Price-to-Gross Profit (P/GP)
- Gross margin stability indicates business model maturity
IGNORE: Price-to-Earnings (P/E), Price-to-Free Cash Flow (P/FCF)
- Profits still volatile and reinvested; FCF just beginning to emerge
### ⚙️ Phase 4 - Operating Leverage Phase
PRIMARY: Forward Price-to-Earnings Ratio (P/E)
- Consistent profitability with expanding margins is now measurable
SECONDARY: Forward Price-to-Free Cash Flow (P/FCF)
- Strong cash generation beginning to drive value
IGNORE: Price-to-Sales (P/S), Price-to-Gross Profit (P/GP)
- Profitability metrics more relevant than revenue or gross profit multiples
### 🎁 Phase 5 - Capital Return Phase
PRIMARY: Price-to-Earnings Ratio (P/E)
- Stable, predictable earnings drive valuation
SECONDARY: Price-to-Free Cash Flow (P/FCF)
- Cash generation funds dividends and buybacks
IGNORE: Price-to-Sales (P/S), Price-to-Gross Profit (P/GP)
- Growth too low for revenue multiples; mature margins fully reflected in earnings
### 📉 Phase 6 - Decline Phase
PRIMARY: No reliable valuation metrics
- Declining fundamentals make traditional multiples unreliable
SECONDARY: None recommended
- Any multiple can be misleading when the denominator is shrinking
IGNORE: All traditional metrics (P/S, P/E, P/FCF, P/GP)
- Focus on liquidation value, asset coverage, or turnaround potential instead
## OUTPUT TEMPLATE - ONLY OUTPUT WHAT'S BELOW THIS LINE
# 📊 Valuation Metrics: [Company Name]
## [Emoji] Phase [X]: [Phase Name]
### 🥇 Primary Valuation Metric: [Full Name (Abbreviation)]
- **Why this matters:** [Brief explanation of why this metric is most relevant]
- **What to look for:** [Key benchmarks or comparisons]
### 🥈 Secondary Valuation Metric: [Full Name (Abbreviation)]
- **Why this matters:** [Brief explanation for additional insight]
- **What to look for:** [Key benchmarks or comparisons]
### ❌ Metrics to Ignore:
- **[Metric Name (Abbreviation)]:** [Why it's not relevant at this phase]
- **[Additional metric]:** [Why to avoid]
### 💡 Quick Valuation Guide:
- **Current Phase Focus:** [What the company is prioritizing]
- **Key Driver:** [Primary value driver at this phase]
- **Red Flag:** [What would make these metrics unreliable]
## 🔗 Sources
[If phase was determined: [[Company] 10-Q Q[#] [YYYY]](actual SEC URL here)]
[If phase was provided by prior analysis: Phase provided by prior Business Phase Analysis - no SEC filing analyzed]
---
## BEHAVIORAL GUARDRAILS
### Core Principles:
1. **Always determine a phase** - Use the decision tree exactly as specified. No interpretation.
2. **Focus on valuation metrics only** - Do not provide business analysis beyond phase determination.
3. **Be decisive** - Exactly ONE primary and ONE secondary metric per phase.
### When Information is Limited:
- State what additional information would improve the analysis
- Make reasonable assumptions based on available data
- Note if using older data (10-K instead of recent 10-Q)
### Output Consistency:
- ALWAYS use the structured format provided
- ALWAYS include all sections (Primary, Secondary, Ignore)
- NEVER output phase determination calculations or reasoning
- Keep explanations concise and practical
### Edge Cases:
- **Pre-revenue startups**: Default to Phase 1
- **Conglomerates**: Analyze dominant business segment
- **Recent IPOs**: Apply decision tree regardless of company age
- **Turnaround situations**: Note if Phase 6 with potential recovery
### Quality Checks:
✓ Did I determine the phase using the decision tree?
✓ Did I specify exactly ONE primary metric?
✓ Did I specify exactly ONE secondary metric?
✓ Did I list which metrics to ignore?
✓ Is my output focused solely on valuation metrics?
### Red Flags to Avoid:
❌ Showing phase determination calculations in output
❌ Providing business analysis beyond metrics
❌ Suggesting multiple primary metrics
❌ Using growth multiples for declining businesses
❌ Adding sections not in the template
Remember: Output ONLY the valuation metrics template. Phase determination is internal only.
""",
    },
    {
        "title": "Price & Sentiment Analysis",
        "prompt": """PRICE & SENTIMENT ANALYSIS v1.9

YOUR IDENTITY
Expert market analyst focused on price causation and layered sentiment (analyst / investor / media) over the past 12 months.

YOUR MISSION
Identify why the stock moved over the last year and where sentiment sits now.
 Deliver a scan-friendly, citation-backed Markdown analysis.
 Never speculate. Never hype. Every statement must be verifiable.

INITIAL INPUT
Begin with:
 "What company (name or ticker) would you like me to analyze for price and sentiment changes over the past year?"
 If the user provides a company/ticker, begin immediately.
EXECUTION SEQUENCE
Step 1 – Input
Store company name as COMPANY_NAME and ticker as TICKER.
Step 2 – Data Acquisition (Priority Order)
Retrieve:
• 1-year price performance (% change, 52-week range, vs 50 / 200-day MAs)


• Major catalysts (earnings reactions, analyst actions, product launches, macro/regulatory headlines)


• Sentiment signals:


    ◦ Analyst reports (targets & ratings)
    ◦ Investor flows (institutional vs retail)
    ◦ Media tone (headlines, social, forums)


Step 3 – Perspective Analysis
Summarize 2–3 concise arguments for both the bullish and bearish cases.
 Use bullet points only. Include citations when available.
 If fewer than 2 sources per side → note "Limited recent coverage."


OUTPUT TEMPLATE – ONLY OUTPUT WHAT'S BELOW THIS LINE
# 📊 Price & Sentiment Analysis: [Company Name] ([Ticker])
Using price data from [Start Date] – [End Date] and news from the same period

## 🧠 1) Overall Takeaway
- **Why It Moved:** [2 sentences linking cause → effect → impact]
- **Next Catalyst:** [Upcoming event / earnings date / major launch]
- **Outlook (12M):** 🟢 Bullish / 🟡 Neutral / 🔴 Bearish — [Plain-English summary referencing bull/bear case]

## 🧭 2) Sentiment Snapshot

| Perspective | Tone | Confidence |
|-------------|------|------------|
| Analyst | 🟢 Bullish / 🟡 Neutral / 🔴 Bearish | High / Med / Low |
| Investor | 🟢 Bullish / 🟡 Neutral / 🔴 Bearish | High / Med / Low |
| Media | 🟢 Positive / 🟡 Mixed / 🔴 Negative | — |

**Tone Summary (12 Months):** [1-sentence evolution of mood]

**Key Signals:**
- [Signal] → [Brief summary + link]
- [Signal] → [Brief summary + link]

## 🐂 3) What the Bulls Say
- [Reason 1]
- [Reason 2]
- [Reason 3]

## 🐻 4) What the Bears Say
- [Reason 1]
- [Reason 2]
- [Reason 3]

## 💹 5) 1-Year Price Overview

| Metric | Value |
|--------|-------|
| 1-Year Change | +X% / −X% |
| 52-Week Range | $LOW – $HIGH |
| Current Price | $XXX |
| vs 200-Day MA | Above / Below / At |
| vs S&P 500 | Outperform / Underperform by X pp |

**Price Context:** [Short reason for current level]

## 📚 6) Sources & Links
1. Bloomberg / Reuters / FactSet / MarketBeat
2. TipRanks / Yahoo Finance / CNBC
3. Social / forum sentiment trackers
4. Recent bull / bear commentary (≤ 90 days old)


BEHAVIORAL GUARDRAILS
Core Principles
1. Use 8th-grade English.
2. No fake links or made-up data.
3. Separate fact from interpretation.
4. Bold headers and tables for scan readability.
5. Always include at least 2 bullish + 2 bearish arguments.
Output Consistency
- Follow the structure exactly.
- Keep explanations concise, factual, and verifiable.
- Do not include speculation or opinions.


Quality Checks
✓ Clear color-coded sentiment
 ✓ ≥ 2 bull + 2 bear points
 ✓ Valid, recent citations
 ✓ Neutral, factual tone
""",
    },
    {
        "title": "SaaSpocalypse Resistance",
        "prompt": """YOUR IDENTITY
Act as a financial analyst who is focused on the long-term viability of a company's moat, or competitive advantage.

YOUR MISSION
Your task is to perform a viability and risk assessment of this company in the context of the AI revolution.

EXECUTION TRIGGER
If company name/ticker provided: Begin analysis. If not provided: Output EXACTLY: "What company (name or ticker) would you like me to analyze?" WAIT FOR USER RESPONSE.

DATA ACQUISITION
Evaluate the company across the following four lenses using the following rating scale. Provide a logical justification for each rating, prioritizing failure points and structural risks.

## The Rating Scale
- 🔴 **Fragile (Red):** High risk of disruption or structural weakness.
- 🟡 **Robust (Yellow):** Defensible and stable, but lacks significant upside from AI.
- 🟢 **Anti-Fragile (Green):** Structurally benefits from AI and gains strength from disruption.

## 1. Liability Lens (The Hallucination Risk)
**Assessment:** Is the cost of failure high?

Scale:
- 🟢 **Anti-Fragile (Green):** High cost of error. "If it's 90% right, that's catastrophic." Examples: Medical diagnostics, cybersecurity, grid management.
- 🔴 **Fragile (Red):** Low cost of error. "If it's 90% right, that's fine." Examples: Marketing copy, basic code generation, graphic design.

## 2. Business Model Lens (The Monetization Structure)
**Assessment:** Does the company charge for work (usage), or per worker (seats)?

Scale:
- 🟢 **Anti-Fragile (Green):** Verified Usage-Based. >80% of current revenue is explicitly tied to usage/credits. If AI agents replace 10 analysts, the revenue shifts to the compute/credits used by those agents.
- 🔴 **Fragile (Red):** Seat-Based Dominance. >80% of revenue is derived from per-user subscriptions. If AI allows 1 person to do the work of 10, the company loses 9 revenue streams.
- Note: Do not rate Green based on "planned" transitions; use current revenue mix.

## 3. Physical World Lens (Integration)
**Assessment:** Can an agent simulate this, or does it require real-world feedback?

Scale:
- 🟢 **Anti-Fragile (Green):** Hardware Integration. Software used in conjunction with tangible hardware or physical infrastructure cannot be easily replaced by pure AI agents.
- 🔴 **Fragile (Red):** Purely Software. Software is approaching zero marginal cost; it can be easily replicated or simulated by an agent.

## 4. Network Lens (Data Gravity)
**Assessment:** Does the data get better as more agents join?

Scale:
- 🟢 **Anti-Fragile (Green):** Proprietary Context. The company owns unique, non-public data that AI needs to be effective. Two-sided networks or proprietary security databases cannot be easily replicated.
- 🔴 **Fragile (Red):** Public Knowledge. The company relies on data that can be quickly migrated to a cheaper platform or scraped from the public web.

## OUTPUT FORMAT

# 🤖 SaaSpocalypse Resistance: [Company Name] ([Ticker])

## Overview
- **Overall Rating:** [🟢 Anti-Fragile / 🟡 Robust / 🔴 Fragile]
- **Confidence:** High / Medium / Low
- **Key Points:**
  - [Bullet 1]
  - [Bullet 2]
  - [Bullet 3]

## Scorecard

| Lens | Rating | Justification |
|------|--------|---------------|
| Liability Lens | [🟢/🟡/🔴] | [Brief reason] |
| Business Model Lens | [🟢/🟡/🔴] | [Brief reason with current revenue mix] |
| Physical World Lens | [🟢/🟡/🔴] | [Brief reason] |
| Network Lens | [🟢/🟡/🔴] | [Brief reason] |

## ⚠️ Critical Failure Point
[Identify the single biggest threat to this stock's longevity in an AI-first world.]

## 📚 Sources
- [Specific filing(s) used, e.g. 10-K FY2024, 10-Q Q2 2025]

## BEHAVIORAL GUARDRAILS
- **Revenue Reality Check:** If a company's financial filings show that the vast majority of revenue is still seat-based, you MUST ignore management's "AI-first" marketing and rate the Business Model Lens as 🔴 Fragile.
- **Confidence honesty:** Mark confidence as Low if data is incomplete or ambiguous. Don't pretend certainty.
- **Simple language:** Write for a new investor. Avoid jargon and overly technical terms.
- **Concise:** Keep explanations brief and scannable.
- **No speculation:** Base classification only on reported financial metrics, not forward guidance.
- **Formatting consistency:** Always use the exact template structure.
- **Source transparency:** Always provide the specific filing used (10-Q Q# YYYY or 10-K FY YYYY).
""",
    },
    {
        "title": "Investment Summary",
        "prompt": """# INVESTMENT SUMMARY & VERDICT

## YOUR IDENTITY
Senior portfolio manager synthesizing multiple prior analyses into a single, actionable investment verdict.

## YOUR MISSION
Read all the prior research below for this company and produce a concise, decisive summary with an overall investment verdict. Do NOT repeat the underlying analyses — synthesize them.

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

### Valuation Analysis
{prior:Valuation Analysis}

### Price & Sentiment Analysis
{prior:Price & Sentiment Analysis}

### SaaSpocalypse Resistance
{prior:SaaSpocalypse Resistance}

---

## OUTPUT TEMPLATE

# 🎯 Prescan Verdict: [Company Name] ([Ticker])

## ⭐ Verdict
**[🟢 DEEP DIVE / 🟡 REVISIT / 🔴 PASS]** — Conviction: [High / Medium / Low]

- **DEEP DIVE:** Worth investing hours in full due diligence right now.
- **REVISIT:** Not compelling enough now — park it and come back when something changes (valuation pullback, earnings confirmation, improving fundamentals, catalyst).
- **PASS:** Move on; unlikely to be worth the time unless the thesis fundamentally changes.

**One-line thesis:** [Single sentence capturing the core investment case.]

## 🧭 Key Findings

### 💪 Strengths (top 3)
1. **[Strength]** — [Brief evidence drawn from prior analyses, citing which one]
2. **[Strength]** — [...]
3. **[Strength]** — [...]

### ⚠️ Concerns (top 3)
1. **[Concern]** — [Brief evidence]
2. **[Concern]** — [Brief evidence]
3. **[Concern]** — [Brief evidence]

## 📐 Quality Snapshot

| Dimension | Score | Notes |
|-----------|-------|-------|
| Business Quality | 🟢/🟡/🔴 | [1-line] |
| Moat Strength | 🟢/🟡/🔴 | [1-line] |
| Growth Runway | 🟢/🟡/🔴 | [1-line] |
| Financial Health | 🟢/🟡/🔴 | [1-line] |
| Risk Profile | 🟢/🟡/🔴 | [1-line] |
| Valuation | 🟢/🟡/🔴 | [1-line] |
| AI Resilience | 🟢/🟡/🔴 | [1-line] |

## 🎯 Recommended Next Step
- **Action:** [Deep dive now / Add to revisit list / Skip + reasoning, 1-2 sentences]
- **If REVISIT, what to wait for:** [Specific condition, e.g. "P/E below 25", "Q3 earnings", "margin stabilization"]

## 🚨 Thesis Triggers
- **DEEP DIVE → PASS:** [Single most important development that would invalidate the thesis]
- **REVISIT → DEEP DIVE:** [Single most important development that would unlock value]

## 📋 Watch List
3 specific metrics or events to monitor over the next 12 months:
1. [Metric/event]
2. [Metric/event]
3. [Metric/event]

## BEHAVIORAL GUARDRAILS
- Synthesize, don't repeat. Reference the prior analyses, but do not re-explain them.
- Be decisive: pick BUY, HOLD, or AVOID. No fence-sitting.
- If a prior analysis is missing or empty, note it explicitly and lower confidence.
- Use plain English (smart 8th grader).
- Keep the output scannable: bullet points and tables, no walls of text.
- Cite which prior analysis supports each claim (e.g. "per Moat Analysis", "per Risk Analysis").
- Do NOT fabricate data — only use what the prior analyses contain.
""",
    },
]


# ── Rate limiting ──
def rate_limited_lookup() -> bool:
    """Returns True if the lookup is allowed, False if rate limited."""
    now = time.time()
    key = '_api_call_times'
    if key not in st.session_state:
        st.session_state[key] = []
    # Clean entries older than 60 seconds
    st.session_state[key] = [t for t in st.session_state[key] if now - t < 60]
    # Max 10 lookups per minute
    if len(st.session_state[key]) >= 10:
        st.warning("Too many requests. Please wait a moment before trying again.")
        return False
    st.session_state[key].append(now)
    return True


# ── Page config ──
from pathlib import Path as _Path
_favicon = _Path(__file__).parent / "assets" / "favicon.png"
st.set_page_config(
    page_title="Lazy Theta",
    page_icon=str(_favicon) if _favicon.exists() else "\U0001f4ca",
    layout="wide",
)

# ── Authentication gate ──
from auth import render_login_page, logout, inject_remember_me_handler, handle_remember_me, save_session_to_browser

if "supabase_client" not in st.session_state:
    # Try to restore session from browser localStorage
    inject_remember_me_handler()
    client, user = handle_remember_me()
    if client and user:
        st.session_state["supabase_client"] = client
        st.session_state["user"] = {"id": str(user.id), "email": user.email}
        st.rerun()
    else:
        render_login_page()
        st.stop()

# Save remember-me token to browser if flagged during login
_sb_client = st.session_state["supabase_client"]
if st.session_state.pop("_save_remember_token", False):
    save_session_to_browser(_sb_client)

# Validate session still active (check at most once per 5 minutes)
_last_auth_check = st.session_state.get("_auth_checked_at", 0)
if time.time() - _last_auth_check > 300:
    try:
        _sb_client.auth.get_user()
        st.session_state["_auth_checked_at"] = time.time()
    except Exception:
        # Try refreshing the session before giving up
        try:
            _sb_client.auth.refresh_session()
            st.session_state["_auth_checked_at"] = time.time()
        except Exception as e2:
            log_error("AUTH_ERROR", f"Session expired and refresh failed: {e2}")
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


def _get_tt_token():
    """Get per-user Tastytrade refresh token from session or DB."""
    if "tt_refresh_token" not in st.session_state:
        st.session_state["tt_refresh_token"] = load_credential(_sb_client, "tastytrade_refresh_token")
    return st.session_state.get("tt_refresh_token")


def _get_ibkr_credentials():
    """Get per-user IBKR credentials from session or DB."""
    if "ibkr_credentials" not in st.session_state:
        st.session_state["ibkr_credentials"] = load_ibkr_credentials(_sb_client)
    return st.session_state.get("ibkr_credentials")


def _is_auth_error(exc):
    """Detect if an exception is a broker authentication/token error."""
    msg = str(exc).lower()
    return any(p in msg for p in (
        "401", "unauthorized", "invalid_token", "token expired",
        "refresh_token", "authentication", "forbidden",
        "invalid_grant", "grant revoked",
    ))


def _render_welcome_page():
    """Full welcome page for users without a Tastytrade connection."""
    st.markdown(
        "<style>.block-container { max-width: 900px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="hero-card">'
        '<p class="hero-value" style="font-size:2.4rem;letter-spacing:-0.02em">Welcome to Lazy Theta</p>'
        '<p class="hero-sub" style="font-size:1.05rem;max-width:560px;margin:12px auto 0">'
        'Track your wheel strategy, analyze positions, and optimize your options income.</p>'
        '</div>',
        unsafe_allow_html=True,
    )

    _num = (
        'display:inline-flex;align-items:center;justify-content:center;'
        'width:36px;height:36px;border-radius:50%;'
        'color:#fff;font-weight:700;font-size:1rem;margin-bottom:12px'
    )
    _card = (
        'background:var(--card);border:1px solid var(--border-medium);'
        'border-radius:16px;padding:28px 20px;text-align:center'
    )

    st.markdown(
        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px">'
        f'<div style="{_card}">'
        f'<div style="{_num};background:var(--accent)">1</div>'
        f'<h4 style="margin:0 0 8px 0;font-size:1rem">Connect your Broker</h4>'
        f'<p style="color:var(--text-muted);font-size:0.85rem;margin:0">'
        f'Link your Tastytrade or Interactive Brokers account to see positions, P&L, and wheel cycles in real-time.</p>'
        f'<p style="color:var(--accent);font-size:0.8rem;font-weight:600;margin:10px 0 0 0">'
        f'Important: please read below</p>'
        f'</div>'
        f'<div style="{_card}">'
        f'<div style="{_num};background:var(--accent)">2</div>'
        f'<h4 style="margin:0 0 8px 0;font-size:1rem">Track your Portfolio</h4>'
        f'<p style="color:var(--text-muted);font-size:0.85rem;margin:0">'
        f'Monitor positions, Greeks, margin usage, and wheel progress</p>'
        f'</div>'
        f'<div style="{_card}">'
        f'<div style="{_num};background:var(--accent)">3</div>'
        f'<h4 style="margin:0 0 8px 0;font-size:1rem">Build your Watchlist</h4>'
        f'<p style="color:var(--text-muted);font-size:0.85rem;margin:0">'
        f'Run DCF valuations and find the best options to sell</p>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div style="height:24px"></div>', unsafe_allow_html=True)
    _, btn1, _, btn2, _ = st.columns([1, 1.2, 0.6, 1.2, 1])
    with btn1:
        st.button("Connect Account", type="primary", use_container_width=True,
                   key="welcome_connect",
                   on_click=lambda: st.session_state.update({"_account_page": "Connect your Broker"}))
    with btn2:
        st.button("Explore Watchlist", type="primary", use_container_width=True,
                   key="welcome_watchlist",
                   on_click=lambda: st.session_state.update({"nav_radio": "Watchlist", "_account_page": None}))

    st.markdown(
        '<div style="background:var(--card);border:1px solid var(--border-medium);'
        'border-radius:16px;padding:24px 28px;margin-top:8px;'
        'display:flex;align-items:flex-start;gap:16px">'
        '<span style="font-size:1.6rem;line-height:1">&#x1f512;</span>'
        '<div>'
        '<p style="margin:0 0 6px 0;font-weight:600;font-size:0.95rem">'
        '<span style="color:var(--accent);font-weight:700">Important!</span> Read-only connection</p>'
        '<p style="margin:0;color:var(--text-muted);font-size:0.85rem;line-height:1.5">'
        'Lazy Theta uses <b>read-only</b> API access for both Tastytrade and Interactive Brokers. '
        'We can only <b>view</b> your positions and history, '
        'we cannot place trades, move funds, or modify your account in any way. '
        'Your credentials are encrypted and stored securely. '
        'You can disconnect at any time in Connect your Broker.</p>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _render_connect_prompt():
    """Compact prompt shown on pages that require Tastytrade connection."""
    st.markdown(
        '<div style="background:var(--card);border:1px solid var(--border-medium);'
        'border-radius:16px;padding:32px;text-align:center;max-width:520px;margin:80px auto">'
        '<p style="font-size:1.6rem;margin:0 0 8px 0">&#x1f512;</p>'
        '<h3 style="margin:0 0 8px 0">Connect a Broker</h3>'
        '<p style="color:var(--text-muted);font-size:0.9rem;margin:0 0 20px 0">'
        'This page requires a broker connection (Tastytrade or Interactive Brokers). '
        'We use <b>read-only</b> access, no trades can be placed through this app.</p>'
        '</div>',
        unsafe_allow_html=True,
    )
    _, btn_col, _ = st.columns([1, 1, 1])
    with btn_col:
        st.button("Connect your Broker", type="primary", use_container_width=True,
                   key=f"connect_btn_{st.session_state.get('nav_radio', '')}",
                   on_click=lambda: st.session_state.update({"_account_page": "Connect your Broker"}))
    st.stop()


# ── Theme ──
if 'dark_mode' not in st.session_state:
    st.session_state.dark_mode = False

THEME = {
    'light': {
        'bg':             '#fafaf8',
        'bg_secondary':   '#f5f4f0',
        'card':           '#fff',
        'card_alt':       '#f9f9fb',
        'text':           '#1d1d1f',
        'text_muted':     '#86868b',
        'border':         'rgba(0,0,0,0.04)',
        'border_medium':  '#d2d2d7',
        'border_light':   '#e8e8ed',
        'shadow':         '0 1px 3px rgba(0,0,0,0.04)',
        'shadow_hover':   '0 2px 8px rgba(0,0,0,0.06)',
        'accent':         '#81b29a',
        'accent_hover':   '#6fa88a',
        'accent_light':   'rgba(129,178,154,0.06)',
        'accent_fill':    'rgba(129,178,154,0.15)',
        'accent_focus':   'rgba(129,178,154,0.2)',
        'red':            '#e07a5f',
        'red_light':      'rgba(224,122,95,0.15)',
        'pill_bg':        'rgba(255,255,255,0.7)',
        'pill_border':    'rgba(255,255,255,0.5)',
        'scrollbar':      '#c4c4c6',
        'grid':           '#f0f0f2',
        'input_bg':       '#fafafa',
        'info_bg':        '#f7f8fa',
        'noise_opacity':  '0.03',
        'divider':        'rgba(0,0,0,0.06)',
        'separator':      'rgba(128,128,128,0.25)',
        'row_alt':        '#f9f9fb',
        'spinner_border': '#e5e5ea',
        'overlay_bg':     '#fafaf8',
        'delete_bg':      '#fee2e2',
        'delete_border':  '#ef4444',
        'delete_text':    '#dc2626',
        'chart_font':     '#1d1d1f',
        'chart_grid':     '#f0f0f2',
        'chart_paper':    'rgba(0,0,0,0)',
        'chart_plot':     'rgba(0,0,0,0)',
        'chart_zero':     '#d2d2d7',
        'tv_bg':          'rgba(0,0,0,0.03)',
    },
    'dark': {
        'bg':             '#1c1c1e',
        'bg_secondary':   '#2c2c2e',
        'card':           '#2c2c2e',
        'card_alt':       '#3a3a3c',
        'text':           '#f5f5f7',
        'text_muted':     '#98989d',
        'border':         'rgba(255,255,255,0.06)',
        'border_medium':  '#5a5a5e',
        'border_light':   '#3a3a3c',
        'shadow':         '0 1px 3px rgba(0,0,0,0.3)',
        'shadow_hover':   '0 2px 8px rgba(0,0,0,0.4)',
        'accent':         '#81b29a',
        'accent_hover':   '#93c4ac',
        'accent_light':   'rgba(129,178,154,0.12)',
        'accent_fill':    'rgba(129,178,154,0.25)',
        'accent_focus':   'rgba(129,178,154,0.3)',
        'red':            '#e07a5f',
        'red_light':      'rgba(224,122,95,0.25)',
        'pill_bg':        'transparent',
        'pill_border':    'transparent',
        'scrollbar':      '#48484a',
        'grid':           '#636366',
        'input_bg':       '#3a3a3c',
        'info_bg':        '#2c2c2e',
        'noise_opacity':  '0.015',
        'divider':        'rgba(255,255,255,0.08)',
        'separator':      'rgba(128,128,128,0.25)',
        'row_alt':        '#252527',
        'spinner_border': '#48484a',
        'overlay_bg':     '#1c1c1e',
        'delete_bg':      'rgba(220,38,38,0.15)',
        'delete_border':  '#ef4444',
        'delete_text':    '#f87171',
        'chart_font':     '#f5f5f7',
        'chart_grid':     '#3a3a3c',
        'chart_paper':    'rgba(0,0,0,0)',
        'chart_plot':     'rgba(0,0,0,0)',
        'chart_zero':     '#48484a',
        'tv_bg':          'rgba(255,255,255,0.04)',
    },
}

_mode = 'dark' if st.session_state.dark_mode else 'light'
T = THEME[_mode]

# ── Custom CSS ──
st.markdown(f"""
<style>
:root {{
    --bg: {T['bg']};
    --bg-secondary: {T['bg_secondary']};
    --card: {T['card']};
    --card-alt: {T['card_alt']};
    --text: {T['text']};
    --text-muted: {T['text_muted']};
    --border: {T['border']};
    --border-medium: {T['border_medium']};
    --border-light: {T['border_light']};
    --shadow: {T['shadow']};
    --shadow-hover: {T['shadow_hover']};
    --accent: {T['accent']};
    --accent-hover: {T['accent_hover']};
    --accent-light: {T['accent_light']};
    --accent-fill: {T['accent_fill']};
    --accent-focus: {T['accent_focus']};
    --red: {T['red']};
    --red-light: {T['red_light']};
    --pill-bg: {T['pill_bg']};
    --pill-border: {T['pill_border']};
    --scrollbar: {T['scrollbar']};
    --grid: {T['grid']};
    --input-bg: {T['input_bg']};
    --info-bg: {T['info_bg']};
    --noise-opacity: {T['noise_opacity']};
    --divider: {T['divider']};
    --row-alt: {T['row_alt']};
    --spinner-border: {T['spinner_border']};
    --overlay-bg: {T['overlay_bg']};
}}

    /* ── Theme overrides — force Streamlit containers to use our palette ── */
    .stApp {{
        background-color: var(--bg) !important;
    }}
    .stApp > header {{
        background-color: var(--bg) !important;
    }}
    [data-testid="stHeader"] {{
        background-color: var(--bg) !important;
    }}
    [data-testid="stToolbar"] {{
        background-color: var(--bg) !important;
    }}
    .stApp [data-testid="stAppViewContainer"] {{
        background-color: var(--bg) !important;
    }}
    .stApp [data-testid="stMain"] {{
        background-color: var(--bg) !important;
    }}
    section[data-testid="stSidebar"] > div {{
        background-color: var(--bg-secondary) !important;
    }}

    /* ── Refined with Edge ── */

    /* Global typography — DM Serif Display (headers) + DM Sans (body) */
    @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {{
        font-family: 'DM Sans', -apple-system, BlinkMacSystemFont,
                     'Helvetica Neue', Arial, sans-serif;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }}

    /* Subtle noise texture overlay */
    body::before {{
        content: "";
        position: fixed;
        inset: 0;
        pointer-events: none;
        z-index: 0;
        opacity: var(--noise-opacity);
        background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='1'/%3E%3C/svg%3E");
        background-repeat: repeat;
        background-size: 256px 256px;
    }}

    /* Page load animation */
    @keyframes fadeInUp {{
        from {{ opacity: 0; transform: translateY(12px); }}
        to {{ opacity: 1; transform: translateY(0); }}
    }}

    /* Custom scrollbar */
    ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
    ::-webkit-scrollbar-track {{ background: transparent; }}
    ::-webkit-scrollbar-thumb {{ background: var(--scrollbar); border-radius: 3px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: var(--accent); }}

    /* Focus states */
    *:focus-visible {{
        outline: 2px solid var(--accent) !important;
        outline-offset: 2px !important;
    }}

    /* Main content area */
    .main .block-container {{
        padding-top: 3rem;
    }}

    /* Headings — Editorial serif */
    h1, h2, h3 {{
        font-family: 'DM Serif Display', Georgia, 'Times New Roman', serif !important;
        color: var(--text) !important;
        font-weight: 400 !important;
        letter-spacing: -0.01em !important;
    }}
    h2 {{ font-size: 2rem !important; }}
    h3 {{ font-size: 1.4rem !important; }}

    p, li, label, span {{
        color: var(--text);
    }}

    /* Metric cards — with subtle depth */
    [data-testid="stMetric"] {{
        background: var(--card);
        border: none;
        border-radius: 18px;
        padding: 20px 24px;
        box-shadow: var(--shadow);
        animation: fadeInUp 0.4s ease-out both;
    }}
    [data-testid="stMetric"]:nth-child(1) {{ animation-delay: 0s; }}
    [data-testid="stMetric"]:nth-child(2) {{ animation-delay: 0.05s; }}
    [data-testid="stMetric"]:nth-child(3) {{ animation-delay: 0.1s; }}
    [data-testid="stMetric"]:nth-child(4) {{ animation-delay: 0.15s; }}
    [data-testid="stMetric"] label {{
        color: var(--text-muted);
        font-size: 0.75rem;
        font-weight: 500;
        letter-spacing: 0.01em;
        text-transform: uppercase;
    }}
    [data-testid="stMetric"] [data-testid="stMetricValue"] {{
        font-weight: 600;
        color: var(--text);
        font-size: 1.3rem;
    }}

    /* Hero card — editorial with green accent */
    .hero-card {{
        background: var(--card);
        border-radius: 24px;
        border-top: 3px solid var(--accent);
        padding: 48px 32px;
        box-shadow: var(--shadow);
        text-align: center;
        margin-bottom: 32px;
        animation: fadeInUp 0.4s ease-out both;
    }}
    .hero-card .hero-label {{
        color: var(--text-muted);
        font-size: 0.85rem;
        font-weight: 500;
        margin: 0 0 8px 0;
        letter-spacing: 0.01em;
        text-transform: uppercase;
    }}
    .hero-card .hero-value {{
        font-family: 'DM Sans', -apple-system, sans-serif;
        font-size: 3.2rem;
        font-weight: 700;
        margin: 0;
        letter-spacing: -0.03em;
    }}
    .hero-card .hero-sub {{
        color: var(--text-muted);
        font-size: 0.95rem;
        font-weight: 400;
        margin: 12px 0 0 0;
    }}
    .hero-green {{ color: var(--accent); }}
    .hero-red {{ color: var(--red); }}

    /* Stat pills — frosted glass */
    .stat-row {{
        display: flex;
        justify-content: center;
        gap: 16px;
        margin: 20px 0 0 0;
        flex-wrap: wrap;
    }}
    .stat-pill {{
        background: var(--pill-bg);
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        border: 1px solid var(--pill-border);
        border-radius: 980px;
        padding: 8px 18px;
        font-size: 0.95rem;
        color: var(--text-muted);
        font-weight: 400;
    }}
    .stat-pill b {{
        color: var(--text);
        font-weight: 600;
    }}

    /* Tabs card — wraps tab bar + content in a card */
    [data-testid="stTabs"] {{
        background: var(--card);
        border-radius: 24px;
        border-top: 3px solid var(--accent);
        padding: 28px 24px;
        box-shadow: var(--shadow);
        margin-bottom: 8px;
        animation: fadeInUp 0.4s ease-out both;
    }}
    /* Inputs inside tabs card — subtle spreadsheet cell style */
    [data-testid="stTabs"] .stNumberInput > div,
    [data-testid="stTabs"] .stNumberInput > div > div,
    [data-testid="stTabs"] .stNumberInput [data-baseweb="input"],
    [data-testid="stTabs"] .stNumberInput [data-baseweb="input"] > div {{
        background: {T['bg_secondary']} !important;
        border: none !important;
        border-radius: 4px !important;
        box-shadow: none !important;
    }}
    [data-testid="stTabs"] .stNumberInput > div > div {{
        border: 1px solid var(--grid) !important;
    }}
    [data-testid="stTabs"] .stNumberInput > div > div:focus-within {{
        border-color: var(--accent) !important;
    }}
    [data-testid="stTabs"] .stNumberInput > div > div > input,
    [data-testid="stTabs"] .stNumberInput input[type="number"] {{
        background: transparent !important;
        border: none !important;
        border-radius: 0 !important;
        padding: 4px 6px !important;
        font-size: 0.82rem !important;
        text-align: right !important;
        box-shadow: none !important;
    }}
    [data-testid="stTabs"] .stNumberInput button {{
        display: none !important;
    }}

    /* Success banner (DCF page) */
    .success-banner {{
        background: var(--card);
        border: none;
        border-radius: 24px;
        padding: 40px 32px;
        margin: 24px 0;
        text-align: center;
        box-shadow: var(--shadow);
        animation: fadeInUp 0.4s ease-out both;
    }}
    .success-banner h2 {{
        color: var(--text);
        margin: 0 0 8px 0;
        font-size: 1.5rem;
        font-weight: 600;
    }}
    .success-banner p {{
        color: var(--text-muted);
        margin: 0;
        font-size: 0.95rem;
        font-weight: 400;
    }}

    /* Chart container */
    .chart-label {{
        color: var(--text-muted);
        font-size: 0.75rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 8px;
    }}

    /* Hide streamlit branding */
    #MainMenu {{visibility: hidden;}}
    footer {{visibility: hidden;}}

    /* Form styling — Apple clean */
    .stForm {{
        border: none !important;
        border-radius: 0 !important;
        padding: 0 !important;
        background: transparent !important;
        box-shadow: none !important;
    }}
    [data-testid="stFormBorder"] {{
        border: none !important;
        padding: 0 !important;
        background: transparent !important;
        box-shadow: none !important;
    }}

    /* Buttons — Green accent */
    .stButton > button[kind="primary"],
    .stDownloadButton > button[kind="primary"],
    .stFormSubmitButton > button[kind="primary"] {{
        background-color: var(--accent) !important;
        color: white !important;
        border: none !important;
        border-radius: 980px !important;
        padding: 12px 24px !important;
        font-size: 0.95rem !important;
        font-weight: 500 !important;
        letter-spacing: 0 !important;
        transition: background-color 0.2s ease !important;
    }}
    .stButton > button[kind="primary"]:hover,
    .stDownloadButton > button[kind="primary"]:hover,
    .stFormSubmitButton > button[kind="primary"]:hover {{
        background-color: var(--accent-hover) !important;
    }}

    .stButton > button[kind="secondary"],
    .stDownloadButton > button[kind="secondary"] {{
        background-color: transparent !important;
        color: var(--accent) !important;
        border: none !important;
        border-radius: 980px !important;
        padding: 12px 24px !important;
        font-size: 0.95rem !important;
        font-weight: 500 !important;
    }}
    .stButton > button[kind="secondary"]:hover,
    .stDownloadButton > button[kind="secondary"]:hover {{
        background-color: var(--accent-light) !important;
    }}

    /* Text inputs — clean Apple style */
    .stTextInput > div > div,
    .stNumberInput > div > div {{
        border: 1px solid var(--border-medium) !important;
        border-radius: 12px !important;
        background: var(--card) !important;
        transition: border-color 0.2s ease !important;
    }}
    .stTextInput > div > div:focus-within,
    .stNumberInput > div > div:focus-within {{
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px var(--accent-focus) !important;
    }}
    .stTextInput > div > div > input,
    .stNumberInput > div > div > input {{
        border: none !important;
        padding: 10px 14px !important;
        font-size: 0.95rem !important;
        background: transparent !important;
        color: var(--text) !important;
        outline: none !important;
        box-shadow: none !important;
    }}

    /* Widget labels — force theme color */
    [data-testid="stWidgetLabel"],
    [data-testid="stWidgetLabel"] p,
    [data-testid="stWidgetLabel"] label,
    .stNumberInput label,
    .stTextInput label,
    .stSelectbox label,
    .stTextArea label,
    .stSlider label,
    .stCheckbox label,
    .stMultiSelect label {{
        color: var(--text) !important;
    }}

    /* Number input — full override for dark mode */
    .stNumberInput > div > div > div > button,
    .stNumberInput button {{
        background-color: var(--card) !important;
        border-color: var(--border-medium) !important;
        color: var(--text) !important;
    }}
    .stNumberInput > div > div,
    .stNumberInput > div > div > div {{
        background-color: var(--card) !important;
    }}
    .stNumberInput [data-baseweb="input"],
    .stNumberInput [data-baseweb="input"] > div {{
        background-color: var(--card) !important;
        border: none !important;
        box-shadow: none !important;
    }}
    .stNumberInput input[type="number"] {{
        background-color: var(--card) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }}
    .stTextInput [data-baseweb="input"],
    .stTextInput [data-baseweb="input"] > div {{
        background-color: var(--card) !important;
        border: none !important;
        box-shadow: none !important;
    }}
    .stTextInput input[type="text"] {{
        background-color: var(--card) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }}
    /* Text area */
    .stTextArea textarea {{
        background-color: var(--card) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
        border: 1px solid var(--border-medium) !important;
    }}
    .stTextArea [data-baseweb="textarea"],
    .stTextArea [data-baseweb="textarea"] > div {{
        background-color: var(--card) !important;
    }}

    /* Catch-all for any remaining white inputs */
    [data-baseweb="input"],
    [data-baseweb="input"] > div,
    [data-baseweb="input"] > div > div,
    [data-baseweb="select"] > div,
    [data-baseweb="select"] > div > div {{
        background-color: var(--card) !important;
        background: var(--card) !important;
    }}
    [data-baseweb="input"] input {{
        background-color: var(--card) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }}

    /* Container with border=True */
    [data-testid="stVerticalBlockBorderWrapper"] > div,
    [data-testid="stVerticalBlockBorderWrapper"] {{
        background-color: var(--card) !important;
        border-color: var(--border-medium) !important;
        color: var(--text) !important;
    }}

    /* All text inside containers */
    [data-testid="stVerticalBlockBorderWrapper"] p,
    [data-testid="stVerticalBlockBorderWrapper"] span,
    [data-testid="stVerticalBlockBorderWrapper"] label,
    [data-testid="stVerticalBlockBorderWrapper"] div {{
        color: var(--text);
    }}

    /* Tabs — text color */
    .stTabs [data-baseweb="tab-list"] button {{
        color: var(--text-muted) !important;
    }}
    .stTabs [data-baseweb="tab-list"] button[aria-selected="true"] {{
        color: var(--text) !important;
    }}

    /* Markdown text inside widgets and expanders */
    [data-testid="stExpanderDetails"] p,
    [data-testid="stExpanderDetails"] span,
    [data-testid="stExpanderDetails"] label,
    [data-testid="stExpanderDetails"] div {{
        color: var(--text);
    }}

    /* Global table styling for dark mode */
    table td, table th {{
        color: var(--text) !important;
        border-color: var(--grid) !important;
    }}
    table tr {{
        border-color: var(--grid) !important;
    }}
    table {{
        color: var(--text) !important;
        border-color: var(--grid) !important;
    }}
    table thead tr {{
        border-bottom: 1px solid var(--grid) !important;
    }}
    table tbody tr {{
        border-top: 1px solid var(--grid) !important;
    }}

    /* Slider label + value */
    .stSlider label, .stSlider [data-testid="stTickBarMin"],
    .stSlider [data-testid="stTickBarMax"] {{
        color: var(--text) !important;
    }}

    /* Form submit button */
    [data-testid="stFormSubmitButton"] button {{
        background-color: var(--accent) !important;
        color: white !important;
        border: none !important;
    }}

    [data-baseweb="select"] {{
        background-color: var(--card) !important;
    }}

    /* Multiselect */
    .stMultiSelect > div > div,
    .stMultiSelect [data-baseweb="select"],
    .stMultiSelect [data-baseweb="select"] > div,
    .stMultiSelect [data-baseweb="input"],
    .stMultiSelect [data-baseweb="input"] > div {{
        background-color: var(--card) !important;
        background: var(--card) !important;
        border-color: var(--border-medium) !important;
        color: var(--text) !important;
    }}
    .stMultiSelect > div > div {{
        border-radius: 12px !important;
    }}
    .stMultiSelect [data-baseweb="tag"] {{
        background-color: var(--accent-fill) !important;
        color: var(--text) !important;
    }}
    .stMultiSelect [data-baseweb="tag"] span {{
        color: var(--text) !important;
    }}
    .stMultiSelect input {{
        background-color: var(--card) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }}
    .stMultiSelect svg {{
        fill: var(--text-muted) !important;
    }}
    /* Form submit button */
    .stFormSubmitButton button {{
        background-color: var(--accent) !important;
        color: #fff !important;
        border: none !important;
    }}

    /* Select boxes */
    .stSelectbox > div > div {{
        border-radius: 12px !important;
        border-color: var(--border-medium) !important;
        background-color: var(--card) !important;
    }}
    .stSelectbox > div > div > div {{
        color: var(--text) !important;
    }}
    /* Selectbox placeholder text */
    .stSelectbox [data-baseweb="select"] [data-testid="stMarkdownContainer"],
    .stSelectbox [data-baseweb="select"] span[aria-live="polite"] {{
        color: var(--text-muted) !important;
    }}
    /* Selectbox / multiselect dropdown list — cover all Streamlit/BaseWeb variants */
    [data-baseweb="popover"],
    [data-baseweb="popover"] > div,
    [data-baseweb="menu"],
    [data-baseweb="menu"] > div,
    [data-baseweb="list"],
    [data-baseweb="list"] > div,
    [role="listbox"],
    ul[id^="bui-"] {{
        background-color: var(--card) !important;
        border: 1px solid var(--border-medium) !important;
        color: var(--text) !important;
    }}
    [data-baseweb="popover"] li,
    [data-baseweb="menu"] li,
    [data-baseweb="list"] li,
    [role="listbox"] li,
    [role="option"],
    ul[id^="bui-"] li {{
        color: var(--text) !important;
        background-color: var(--card) !important;
    }}
    [data-baseweb="popover"] li:hover,
    [data-baseweb="menu"] li:hover,
    [data-baseweb="list"] li:hover,
    [role="option"]:hover,
    ul[id^="bui-"] li:hover {{
        background-color: var(--accent-light) !important;
        color: var(--text) !important;
    }}
    /* Highlighted/focused/selected option in dropdown —
       BaseWeb sets inline styles on focused items; override every possible state */
    [data-baseweb="menu"] li[aria-selected="true"],
    [data-baseweb="list"] li[aria-selected="true"],
    [role="option"][aria-selected="true"],
    [role="option"][data-highlighted="true"],
    [data-baseweb="menu"] [data-highlighted="true"],
    [data-baseweb="list"] [data-highlighted="true"],
    [role="option"]:focus,
    [role="option"]:focus-visible,
    [role="option"][aria-current="true"],
    li[aria-selected="true"],
    li[data-highlighted="true"] {{
        background-color: var(--accent-light) !important;
        color: var(--text) !important;
    }}
    /* Force all selectbox dropdown overlay elements dark */
    div[data-baseweb="popover"] *,
    div[data-baseweb="select"] [role="listbox"] *,
    .stSelectbox div[data-baseweb] ul,
    .stSelectbox div[data-baseweb] ul li,
    .stMultiSelect div[data-baseweb] ul,
    .stMultiSelect div[data-baseweb] ul li {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    .stSelectbox div[data-baseweb] ul li:hover,
    .stMultiSelect div[data-baseweb] ul li:hover {{
        background-color: var(--accent-light) !important;
        color: var(--text) !important;
    }}
    /* Nuclear override: BaseWeb applies inline background-color on highlighted
       items via style attribute. Target every possible li inside dropdown
       containers with attribute selectors to beat inline specificity. */
    [data-baseweb="popover"] li[style],
    [data-baseweb="menu"] li[style],
    [data-baseweb="list"] li[style],
    [role="listbox"] li[style],
    ul[id^="bui-"] li[style] {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    [data-baseweb="popover"] li[style]:hover,
    [data-baseweb="menu"] li[style]:hover,
    [data-baseweb="list"] li[style]:hover,
    [role="listbox"] li[style]:hover,
    ul[id^="bui-"] li[style]:hover,
    [data-baseweb="popover"] li[style][aria-selected="true"],
    [data-baseweb="menu"] li[style][aria-selected="true"],
    [data-baseweb="list"] li[style][aria-selected="true"],
    [role="listbox"] li[style][aria-selected="true"],
    ul[id^="bui-"] li[style][aria-selected="true"] {{
        background-color: var(--accent-light) !important;
        color: var(--text) !important;
    }}
    /* Ultra-aggressive: target Streamlit emotion-cache classes inside dropdowns.
       Streamlit injects CSS-in-JS classes (st-emotion-cache-*) that set white
       backgrounds on highlighted items. Boost specificity with :where(:root) hack. */
    :root [data-baseweb="popover"] [class*="st-emotion-cache"],
    :root [data-baseweb="menu"] [class*="st-emotion-cache"],
    :root [data-baseweb="list"] [class*="st-emotion-cache"],
    :root [role="listbox"] [class*="st-emotion-cache"],
    :root ul[id^="bui-"] [class*="st-emotion-cache"] {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    :root [data-baseweb="popover"] [class*="st-emotion-cache"]:hover,
    :root [data-baseweb="menu"] [class*="st-emotion-cache"]:hover,
    :root [data-baseweb="list"] [class*="st-emotion-cache"]:hover,
    :root [role="listbox"] [class*="st-emotion-cache"]:hover {{
        background-color: var(--accent-light) !important;
        color: var(--text) !important;
    }}
    /* Also target Streamlit's auto-generated st-XX classes on option items */
    :root [role="option"][class*="st-"] {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    :root [role="option"][class*="st-"]:hover,
    :root [role="option"][class*="st-"][aria-selected="true"] {{
        background-color: var(--accent-light) !important;
        color: var(--text) !important;
    }}
    /* Final fallback: any element inside a popover/listbox with white-ish bg */
    :root :is([data-baseweb="popover"], [data-baseweb="menu"], [role="listbox"]) li {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    :root :is([data-baseweb="popover"], [data-baseweb="menu"], [role="listbox"]) li:hover {{
        background-color: var(--accent-light) !important;
        color: var(--text) !important;
    }}
    /* Streamlit popover (st.popover) button & content */
    [data-testid="stPopover"] button,
    [data-testid="stPopover"] button * {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    [data-testid="stPopover"] button {{
        border: 1px solid var(--border-medium) !important;
    }}
    [data-testid="stPopoverBody"],
    [data-testid="stPopoverBody"] > div {{
        background-color: var(--card) !important;
        border-color: var(--border-medium) !important;
    }}
    /* st.pills — dark mode overrides */
    [data-testid="stBaseButton-pills"],
    [data-testid="stBaseButton-pills"] * {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    [data-testid="stBaseButton-pills"] {{
        border: 1px solid var(--border-medium) !important;
    }}
    [data-testid="stBaseButton-pillsActive"],
    [data-testid="stBaseButton-pillsActive"] * {{
        background-color: var(--accent) !important;
        color: white !important;
    }}
    [data-testid="stBaseButton-pillsActive"] {{
        border-color: var(--accent) !important;
    }}
    /* Toggle styling */
    .stToggle label span {{
        color: var(--text) !important;
    }}
    /* Metric card text overrides */
    [data-testid="stMetricValue"] {{
        color: var(--text) !important;
    }}
    /* Tab labels */
    .stTabs [data-baseweb="tab-list"] button {{
        color: var(--text-muted) !important;
    }}
    .stTabs [data-baseweb="tab-list"] button[aria-selected="true"] {{
        color: var(--text) !important;
    }}
    .stTabs [data-baseweb="tab-highlight"] {{
        background-color: var(--accent) !important;
    }}
    .stTabs [data-baseweb="tab-border"] {{
        background-color: var(--border-medium) !important;
    }}

    /* Sliders — Green accent */
    .stSlider [data-baseweb="slider"] [role="slider"] {{
        background-color: var(--accent) !important;
    }}

    /* Expanders — card style with accent left border */
    [data-testid="stExpander"] {{
        background-color: var(--card) !important;
        border: 1px solid var(--border-medium);
        border-left: 3px solid var(--accent);
        border-radius: 12px;
        box-shadow: var(--shadow);
        overflow: hidden;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
        animation: fadeInUp 0.4s ease-out both;
    }}
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary * {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    [data-testid="stExpander"] [data-testid="stExpanderDetails"] {{
        background-color: var(--card) !important;
    }}
    [data-testid="stExpander"]:hover {{
        transform: translateY(-2px);
        box-shadow: var(--shadow-hover);
    }}

    /* Dataframes — rounded, clean */
    [data-testid="stDataFrame"] {{
        border-radius: 14px;
        overflow: hidden;
    }}

    /* Sidebar — minimal Apple style */
    section[data-testid="stSidebar"] {{
        background: var(--card);
        border-right: none;
    }}
    section[data-testid="stSidebar"] [data-testid="stRadio"] label {{
        font-weight: 500;
        color: var(--text);
        transition: background-color 0.2s ease;
    }}
    /* Radio / checkbox accent — green */
    [data-testid="stRadio"] [role="radiogroup"] label[data-checked="true"]::before,
    .stRadio div[role="radiogroup"] label span[data-checked="true"] {{
        background-color: var(--accent) !important;
        border-color: var(--accent) !important;
    }}
    input[type="radio"]:checked {{
        accent-color: var(--accent) !important;
    }}
    /* Pills active state */
    button[data-active="true"],
    [data-testid="stPills"] button[aria-pressed="true"],
    [data-testid="stPills"] button[aria-selected="true"] {{
        background-color: var(--accent) !important;
        color: white !important;
        border-color: var(--accent) !important;
    }}
    /* Streamlit primary color override */
    :root {{
        --primary-color: var(--accent) !important;
    }}

    /* Toolbar: remove gap between buttons */
    .st-key-toolbar_inline [data-testid="stHorizontalBlock"] {{
        gap: 0 !important;
    }}
    .st-key-toolbar_inline [data-testid="stColumn"] {{
        flex: 0 0 auto !important;
        width: auto !important;
        min-width: 0 !important;
    }}

    /* Dividers — consistent subtle separators */
    hr {{
        border-color: var(--divider) !important;
        opacity: 1;
    }}

    /* Links */
    a {{
        color: var(--accent) !important;
        text-decoration: none !important;
    }}
    a:hover {{
        text-decoration: underline !important;
    }}

    /* Status widget */
    [data-testid="stStatusWidget"] {{
        border-radius: 18px;
    }}

    /* Cumulative Returns — white block, green accent */
    .st-key-cumulative_block {{
        background: var(--card);
        border-radius: 24px;
        border-top: 3px solid var(--accent);
        padding: 32px;
        box-shadow: var(--shadow);
    }}
    .st-key-cumulative_block .performer-block {{
        background: none;
        border-radius: 0;
        padding: 0;
        box-shadow: none;
    }}
    .st-key-cumulative_block .performer-block:hover {{
        transform: none;
        box-shadow: none;
    }}

    /* Results hero + chart — single continuous white block */
    .st-key-results_hero {{
        background: var(--card);
        border-radius: 24px;
        border-top: 3px solid var(--accent);
        padding: 32px;
        box-shadow: var(--shadow);
    }}
    .st-key-results_hero .hero-card {{
        background: none;
        border-top: none;
        border-radius: 0;
        padding: 0;
        box-shadow: none;
        margin-bottom: 0;
        animation: none;
    }}

    /* Valuation Bridge — same card style as tabs */
    .st-key-valuation_bridge_card {{
        background: var(--card);
        border-radius: 24px;
        border-top: 3px solid var(--accent);
        padding: 28px 24px;
        box-shadow: var(--shadow);
        margin-bottom: 32px;
        animation: fadeInUp 0.4s ease-out both;
    }}
    .st-key-valuation_bridge_card .stNumberInput > div,
    .st-key-valuation_bridge_card .stNumberInput > div > div,
    .st-key-valuation_bridge_card .stNumberInput [data-baseweb="input"],
    .st-key-valuation_bridge_card .stNumberInput [data-baseweb="input"] > div {{
        background: var(--bg) !important;
        border: none !important;
        border-radius: 4px !important;
        box-shadow: none !important;
    }}
    .st-key-valuation_bridge_card .stNumberInput > div > div {{
        border: 1px solid var(--grid) !important;
    }}
    .st-key-valuation_bridge_card .stNumberInput > div > div:focus-within {{
        border-color: var(--accent) !important;
    }}
    .st-key-valuation_bridge_card .stNumberInput input[type="number"] {{
        background: transparent !important;
    }}

    /* Portfolio Allocation — white block, no outer frame */
    .st-key-allocation_block {{
        background: var(--card);
        border-radius: 24px;
        padding: 32px;
        box-shadow: var(--shadow);
    }}

    /* Greeks / BWD / Interest — CSS Grid, equal-height cards */
    .greeks-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
        gap: 16px;
        margin-bottom: 24px;
    }}
    .greeks-grid .hero-card {{
        height: 100%;
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
    }}

    /* Margin overview — single continuous white block */
    .st-key-margin_block {{
        background: var(--card);
        border-radius: 24px;
        border-top: 3px solid var(--accent);
        padding: 32px;
        box-shadow: var(--shadow);
    }}
    .st-key-margin_block .stTextInput > div > div > input,
    .st-key-margin_block .stNumberInput > div > div > input,
    .st-key-margin_block .stNumberInput input[type="number"] {{
        background: var(--bg-secondary) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
        border: 1px solid var(--border-medium) !important;
        border-radius: 12px !important;
        box-shadow: none !important;
    }}
    .st-key-margin_block .stTextInput > div > div {{
        border: none !important;
    }}
    .st-key-margin_block .stTextInput input::placeholder {{
        -webkit-text-fill-color: var(--text-muted) !important;
        color: var(--text-muted) !important;
    }}
    .st-key-margin_block .stNumberInput button {{
        color: var(--text) !important;
        background: var(--bg-secondary) !important;
        border-color: var(--border-medium) !important;
    }}
    .st-key-margin_block .hero-card {{
        background: none;
        border-top: none;
        border-radius: 0;
        padding: 0;
        box-shadow: none;
        margin-bottom: 0;
        animation: none;
    }}

    /* ── Ticker cards (Wheel Cost Basis) ── */
    [class*="st-key-wheel_card_"] {{
        background: var(--card);
        border-radius: 24px;
        border-top: 3px solid var(--accent);
        padding: 24px 32px;
        box-shadow: var(--shadow);
        margin-bottom: 16px;
    }}
    .card-header {{
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        margin-bottom: 16px;
        max-width: 700px;
    }}
    .card-left .tk-title {{
        display: flex;
        align-items: center;
        gap: 8px;
    }}
    .card-left .tk-logo {{
        width: 28px;
        height: 28px;
        border-radius: 50%;
        object-fit: cover;
        flex-shrink: 0;
    }}
    .card-left .tk-name {{
        font-size: 1.1rem;
        font-weight: 700;
        color: var(--text);
        margin: 0;
    }}
    .card-left .tk-sub {{
        font-size: 0.8rem;
        color: var(--text-muted);
        margin: 2px 0;
    }}
    .card-center {{
        text-align: center;
    }}
    .card-center .shares-count {{
        font-size: 1.05rem;
        font-weight: 600;
        color: var(--text);
    }}
    .card-center .shares-label {{
        font-size: 0.78rem;
        color: var(--text-muted);
    }}
    .pl-badge {{
        display: inline-block;
        padding: 6px 16px;
        border-radius: 8px;
        font-weight: 600;
        font-size: 0.9rem;
        color: #fff;
    }}
    .pl-badge-green {{ background: var(--accent); }}
    .pl-badge-red {{ background: var(--red); }}

    .trade-row {{
        display: flex;
        align-items: baseline;
        gap: 28px;
        padding: 12px 0;
        border-bottom: 1px solid var(--divider);
    }}
    .trade-row:last-child {{ border-bottom: none; }}
    .trade-row .tr-desc {{
        min-width: 160px;
    }}
    .trade-row .tr-desc .tr-label {{
        font-weight: 600;
        font-size: 0.92rem;
        color: var(--text);
        margin: 0;
    }}
    .trade-row .tr-desc .tr-date {{
        font-size: 0.78rem;
        color: var(--text-muted);
        margin: 0;
    }}
    .trade-row .tr-cell {{
        text-align: left;
        min-width: 70px;
    }}
    .trade-row .tr-cell .tr-val {{
        font-size: 0.92rem;
        font-weight: 500;
        color: var(--text);
        margin: 0;
    }}
    .trade-row .tr-cell .tr-lbl {{
        font-size: 0.72rem;
        color: var(--text-muted);
        margin: 0;
    }}
    .trade-row .status-badge {{
        display: inline-block;
        padding: 3px 10px;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 600;
        color: #fff;
    }}
    .status-closed {{ background: var(--accent); }}
    .status-open {{ background: var(--accent); }}
    .status-assigned {{ background: var(--text-muted); }}

    /* Section title bar */
    .section-title-bar {{
        background: var(--card);
        border-radius: 14px;
        padding: 18px 20px;
        margin-bottom: 10px;
        font-family: 'DM Serif Display', Georgia, serif;
        font-size: 1.1rem;
        font-weight: 400;
        color: var(--text);
        box-shadow: var(--shadow);
    }}

    /* Returns header — selectbox overlaid on title bar */
    .st-key-ret_pick_wrap {{
        position: relative;
        top: -52px;
        margin-bottom: -66px;
        padding-left: 55%;
        padding-right: 16px;
        z-index: 10;
    }}
    .st-key-ret_pick_wrap [data-testid="stVerticalBlock"] {{
        gap: 0 !important;
    }}
    /* Returns selectbox — ensure dark-mode readability */
    .st-key-ret_pick_wrap [data-baseweb="select"] {{
        background-color: var(--card) !important;
        border-color: var(--border-medium) !important;
        border-radius: 12px !important;
    }}
    .st-key-ret_pick_wrap [data-baseweb="select"] * {{
        color: var(--text) !important;
    }}
    .st-key-ret_pick_wrap [data-baseweb="select"] [data-testid="stMarkdownContainer"],
    .st-key-ret_pick_wrap [data-baseweb="select"] input::placeholder {{
        color: var(--text-muted) !important;
    }}

    /* ── Performer block — with hover lift ── */
    .performer-block {{
        background: var(--card);
        border-radius: 18px;
        padding: 24px;
        box-shadow: var(--shadow);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }}
    .performer-block:hover {{
        transform: translateY(-2px);
        box-shadow: var(--shadow-hover);
    }}
    .performer-block h4 {{
        margin: 0 0 12px 0;
        font-size: 1rem !important;
    }}
    .performer-block .portfolio-cards {{
        flex-direction: row;
        flex-wrap: wrap;
        justify-content: center;
        align-items: stretch;
    }}
    .performer-block .portfolio-card {{
        flex: 1;
        min-width: 180px;
    }}

    /* ── Portfolio strip cards ── */
    .portfolio-cards {{
        display: flex;
        flex-direction: column;
        align-items: stretch;
        gap: 8px;
    }}
    .portfolio-card {{
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 16px;
        padding: 12px 16px;
        background: var(--card);
        border: 1px solid var(--border-medium);
        border-left: 3px solid var(--accent);
        border-radius: 14px;
        flex-wrap: wrap;
        width: 100%;
        box-sizing: border-box;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }}
    .portfolio-card:hover {{
        transform: translateY(-2px);
        box-shadow: var(--shadow-hover);
    }}
    .portfolio-card .pf-logo {{
        width: 30px;
        height: 30px;
        border-radius: 50%;
        object-fit: cover;
        flex-shrink: 0;
    }}
    .portfolio-card .pf-ticker {{
        font-weight: 700;
        font-size: 1.05rem;
        color: var(--text);
        min-width: 52px;
        flex-shrink: 0;
    }}
    .portfolio-card .pf-cell {{
        display: flex;
        flex-direction: column;
        align-items: center;
        text-align: center;
    }}
    .portfolio-card .pf-label {{
        font-size: 0.7rem;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.03em;
        line-height: 1.1;
        white-space: nowrap;
    }}
    .portfolio-card .pf-val {{
        font-size: 0.95rem;
        font-weight: 600;
        color: var(--text);
        line-height: 1.3;
        white-space: nowrap;
    }}
    .portfolio-card .pf-green {{ color: var(--accent); }}
    .portfolio-card .pf-red {{ color: var(--red); }}


    /* ── Performer grid (Top/Bottom side by side, stacked on mobile) ── */
    .performer-grid {{
        display: flex;
        gap: 12px;
    }}
    .performer-grid > div {{ flex: 1; min-width: 0; }}
    @media (max-width: 768px) {{
        .performer-grid {{ flex-direction: column; gap: 24px; }}
        .portfolio-card {{ gap: 10px; padding: 10px 12px; }}
        .portfolio-card .pf-cell {{ flex: 1; }}
    }}

    /* ── CSS tooltip ── */
    .css-tip {{
        position: relative;
        cursor: help;
    }}
    .css-tip::after {{
        content: attr(data-tip);
        position: absolute;
        bottom: 130%;
        right: 0;
        background: var(--card);
        color: var(--text);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 8px 12px;
        font-size: 0.72rem;
        font-weight: 400;
        white-space: nowrap;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        opacity: 0;
        pointer-events: none;
        transition: opacity 0.15s;
        z-index: 100;
    }}
    .css-tip:hover::after {{
        opacity: 1;
    }}

    /* ── Expandable position cards ── */
    .pf-details {{ width: 100%; }}
    .pf-details summary {{
        list-style: none;
        cursor: pointer;
    }}
    .pf-details summary::-webkit-details-marker {{ display: none; }}
    .pf-details summary .portfolio-card {{
        border-bottom-left-radius: 14px;
        border-bottom-right-radius: 14px;
        transition: border-radius 0.15s ease;
        position: relative;
    }}
    .pf-details summary .portfolio-card::after {{
        content: "›";
        font-size: 1.6rem;
        color: var(--text-muted);
        flex-shrink: 0;
        position: absolute;
        right: 16px;
        transition: transform 0.2s ease;
    }}
    .pf-details[open] summary .portfolio-card::after {{
        transform: rotate(90deg);
    }}
    .pf-details[open] summary .portfolio-card {{
        border-bottom-left-radius: 0;
        border-bottom-right-radius: 0;
        border-bottom: none;
    }}
    .pf-details[open] > .portfolio-card {{
        border-top-left-radius: 0;
        border-top-right-radius: 0;
        margin-top: 0 !important;
    }}

    /* ── Page transition loading overlay (only on full rerun, not fragment) ── */
    @keyframes pf-spin {{
        to {{ transform: rotate(360deg); }}
    }}
    body:has([data-testid="stSidebar"] [data-stale="true"]) [data-testid="stMain"]::before {{
        content: "";
        position: fixed;
        inset: 0;
        background: var(--overlay-bg);
        z-index: 9998;
    }}
    body:has([data-testid="stSidebar"] [data-stale="true"]) [data-testid="stMain"]::after {{
        content: "";
        position: fixed;
        top: 50%;
        left: 50%;
        width: 28px;
        height: 28px;
        margin: -14px 0 0 -14px;
        border: 3px solid var(--spinner-border);
        border-top-color: var(--accent);
        border-radius: 50%;
        animation: pf-spin 0.6s linear infinite;
        z-index: 9999;
    }}

</style>
""", unsafe_allow_html=True)


# ── Helper functions ──
def _flush_clean(buf, prev_pos, status):
    """Write only key progress lines (skip noisy debug output)."""
    buf.seek(prev_pos)
    new_text = buf.read()
    # We don't show the raw output — status steps handle the display
    return buf.tell()


def _build_excel_bytes(cfg):
    """Build DCF Excel model in memory, return bytes."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, "dcf_template.py")

    ns = {}
    with open(template_path, "r") as f:
        exec(f.read(), ns)

    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        ns["build_dcf_model"](cfg, tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _watchlist_overview():
    st.markdown("## Watchlist")
    st.markdown(
        f'<p style="color: {T["text_muted"]}; font-size: 1.05rem; line-height: 1.6; max-width: 560px;">'
        'Track intrinsic value vs market price for your watchlist. '
        'Click a ticker to edit the full DCF model.'
        '</p>',
        unsafe_allow_html=True,
    )

    # Red hover effect for delete buttons
    st.markdown(f"""<style>
    button[data-testid="stBaseButton-secondary"]:has(span[data-testid="stIconMaterial"]):hover {{
        background: {T['delete_bg']} !important;
        border-color: {T['delete_border']} !important;
        color: {T['delete_text']} !important;
    }}
    </style>""", unsafe_allow_html=True)

    # ── Add ticker ──
    st.markdown("")
    wl_add_col1, wl_add_col2 = st.columns([3, 1], vertical_alignment="center")
    with wl_add_col1:
        wl_ticker = st.text_input(
            "Add ticker",
            placeholder="e.g. AAPL",
            label_visibility="collapsed",
            key="wl_ticker_input",
        )
    with wl_add_col2:
        wl_add = st.button("Add to Watchlist", use_container_width=True, type="primary")

    if wl_add and wl_ticker:
        ticker_clean = sanitize_ticker(wl_ticker)
        if ticker_clean is None:
            st.warning("Invalid ticker. Use 1–5 letters only (e.g. AAPL).")
        elif not rate_limited_lookup():
            pass
        else:
            try:
                _, wl_cfg, _ = run_analysis(
                    ticker_clean,
                    peer_mode="Auto-discover",
                    manual_peers="",
                    margin_of_safety=MARGIN_OF_SAFETY_DEFAULT,
                    terminal_growth=TERMINAL_GROWTH_DEFAULT,
                    n_peers=6,
                )
                save_config(_sb_client, ticker_clean, wl_cfg)
                st.success(f"{ticker_clean} added to watchlist")
                st.rerun()
            except ValueError as e:
                err_msg = str(e)
                if "not found in SEC" in err_msg:
                    st.warning(f"**{ticker_clean}** is not available for DCF analysis. "
                               "Only individual stocks with SEC filings (10-K) can be added. "
                               "ETFs, mutual funds, and indices are not supported.")
                else:
                    logger.error("Watchlist analysis failed for %s: %s", ticker_clean, e)
                    log_error("WATCHLIST_ERROR", str(e), page="Watchlist", metadata={"ticker": ticker_clean})
                    st.error(f"Could not analyse {ticker_clean}. Please try again. ({type(e).__name__})")
            except Exception as e:
                import traceback; traceback.print_exc()
                logger.error("Watchlist analysis failed for %s: %s", ticker_clean, e)
                log_error_with_trace("WATCHLIST_ERROR", e, page="Watchlist", metadata={"ticker": ticker_clean})
                st.error(f"Could not analyse {ticker_clean}. Please try again. ({type(e).__name__})")

    # ── Overview table ──
    watchlist = list_watchlist(_sb_client)
    if not watchlist:
        st.info("Your watchlist is empty. Add a ticker above or use 'Add to Watchlist' on the DCF page.")
        return

    @st.cache_data(ttl=60)
    def _fetch_prices_batch(tickers_tuple):
        prices = fetch_current_prices(list(tickers_tuple))
        return {t: (p["price"] if p else 0.0) for t, p in prices.items()}

    # Load all configs once (avoid redundant load_config calls)
    # Clear cache if returning from editor
    if st.session_state.pop("_wl_config_dirty", False):
        st.cache_data.clear()

    @st.cache_data(ttl=30, show_spinner=False)
    def _load_all_configs(user_id, tickers_tuple):
        from concurrent.futures import ThreadPoolExecutor
        def _load_one(t):
            c = load_config(_sb_client, t)
            return (t, c) if c is not None else None
        cfgs = {}
        with ThreadPoolExecutor(max_workers=6) as ex:
            for result in ex.map(_load_one, tickers_tuple):
                if result:
                    cfgs[result[0]] = result[1]
        return cfgs

    _wl_configs = _load_all_configs(st.session_state["user"]["id"], tuple(item['ticker'] for item in watchlist))
    wl_tickers = list(_wl_configs.keys())
    batch_prices = _fetch_prices_batch(tuple(wl_tickers)) if wl_tickers else {}

    @st.cache_data(ttl=86400, show_spinner=False)
    def _cached_fundamentals(t):
        try:
            return fetch_fundamentals(t, n_years=2)
        except Exception as e:
            logger.debug("Fundamentals fetch failed for %s: %s", t, e)
            return {}

    # Pre-fetch fundamentals in parallel (cached 24h, only slow on first load)
    from concurrent.futures import ThreadPoolExecutor
    _fund_map = {}
    with ThreadPoolExecutor(max_workers=6) as _fund_exec:
        _fund_futures = {t: _fund_exec.submit(_cached_fundamentals, t) for t in wl_tickers}
    for t, f in _fund_futures.items():
        try:
            _fund_map[t] = f.result(timeout=10)
        except Exception as e:
            logger.debug("Fundamentals parallel fetch failed for %s: %s", t, e)
            _fund_map[t] = {}

    rows = []
    for t, cfg_wl in _wl_configs.items():
        try:
            live_price = batch_prices.get(t, 0.0)
            if live_price > 0:
                cfg_wl['stock_price'] = live_price
            # Use Valuation Bridge values if available, otherwise compute
            if '_computed_intrinsic' in cfg_wl:
                _wl_intrinsic = cfg_wl['_computed_intrinsic']
                _wl_buy = cfg_wl['_computed_buy']
            else:
                val = compute_intrinsic_value(cfg_wl)
                _wl_intrinsic = val['intrinsic_value']
                _wl_buy = val['buy_price']
            upside = (_wl_intrinsic / live_price - 1) if live_price > 0 else 0
            ni = cfg_wl.get('hist_net_income', [])
            sh = cfg_wl.get('shares_outstanding', 0)
            eps = ni[-1] / sh if ni and sh else 0
            pe = live_price / eps if eps > 0 else None
            # FCF Yield — from fundamentals (cached 24h)
            fcf_yield_val = None
            _fund = _fund_map.get(t, {})
            _fcf_vals = [v for v in _fund.get('fcf', []) if v is not None]
            _sh_vals = [v for v in _fund.get('shares', []) if v and v > 0]
            if _fcf_vals and _sh_vals and live_price > 0:
                fcf_yield_val = (_fcf_vals[-1] * 1e6 / _sh_vals[-1]) / live_price
        except Exception as e:
            logger.warning("Watchlist row build failed for %s: %s", t, e)
            continue
        rows.append({
            'ticker': t,
            'company': cfg_wl.get('company', t),
            'notes': cfg_wl.get('notes', ''),
            'category': cfg_wl.get('category', 'Uncategorized'),
            'price': live_price,
            'intrinsic': _wl_intrinsic,
            'buy_price': _wl_buy,
            'upside': upside,
            'pe': pe,
            'fcf_yield': fcf_yield_val,
        })

    rows.sort(key=lambda r: r['upside'], reverse=True)

    # Fetch earnings dates (cached 5 min)
    @st.cache_data(ttl=3600, show_spinner=False)
    def _cached_earnings(tickers_tuple):
        return fetch_earnings_dates(list(tickers_tuple))

    _earnings_map = _cached_earnings(tuple(wl_tickers)) if wl_tickers else {}

    # ── Category definitions ──
    _categories = ["Yes", "Maybe", "Watch Later", "No", "Uncategorized"]
    _cat_icons = {"Yes": "✅", "Maybe": "🤔", "Watch Later": "⏳", "No": "❌", "Uncategorized": ""}

    def _render_wl_header():
        hdr = st.columns([0.3, 1.0, 1.6, 0.8, 0.8, 0.8, 0.7, 0.6, 0.7, 0.7, 0.3])
        _wl_hdr = ["", "Ticker", "Company", "Price", "Intrinsic", "Buy Price", "Upside", "P/E", "FCF Yield", "Earnings", ""]
        for col, label in zip(hdr, _wl_hdr):
            if label:
                col.markdown(f"**{label}**")

    def _render_wl_row(row):
        t = row['ticker']
        up_color = "green" if row['upside'] > 0 else "red"
        cols = st.columns([0.3, 1.0, 1.6, 0.8, 0.8, 0.8, 0.7, 0.6, 0.7, 0.7, 0.3], vertical_alignment="center")
        with cols[0]:
            if st.button("", key=f"wl_edit_{t}", icon=":material/edit:"):
                st.query_params["edit"] = t
                st.rerun()
        logo_url = f"https://assets.parqet.com/logos/symbol/{t}"
        cols[1].markdown(
            f'<img src="{logo_url}" style="width:24px;height:24px;border-radius:50%;object-fit:cover;vertical-align:middle;margin-right:6px" onerror="this.style.display=\'none\'"><strong>{t}</strong>',
            unsafe_allow_html=True,
        )
        _note = row.get('notes', '')
        if _note:
            _note_preview = _note[:50].replace('\n', ' ') + ('...' if len(_note) > 50 else '')
            cols[2].markdown(
                f'{row["company"]}<br><span style="font-size:0.78rem;color:{T["text_muted"]}">{_note_preview}</span>',
                unsafe_allow_html=True,
            )
        else:
            cols[2].markdown(row['company'])
        cols[3].markdown(f"${row['price']:.2f}")
        cols[4].markdown(f"${row['intrinsic']:.2f}")
        cols[5].markdown(f"${row['buy_price']:.2f}")
        cols[6].markdown(f":{up_color}[{row['upside']:+.1%}]")
        cols[7].markdown(f"{row['pe']:.1f}x" if row['pe'] else "—")
        cols[8].markdown(f"{row['fcf_yield']:.1%}" if row['fcf_yield'] else "—")
        _earn = _earnings_map.get(t)
        if _earn and _earn.get('date'):
            _days_to_earn = (_earn['date'] - date.today()).days
            if _days_to_earn >= 0:
                _earn_est = " (est)" if _earn.get('estimated') else ""
                if _days_to_earn <= 7:
                    _earn_col = T['red']
                elif _days_to_earn <= 14:
                    _earn_col = T['text_muted']
                else:
                    _earn_col = T['text']
                cols[9].markdown(
                    f'<span style="color:{_earn_col}">{_earn["date"].strftime("%b %d")}{_earn_est}</span>',
                    unsafe_allow_html=True,
                )
            else:
                _next_est = _earn['date'] + timedelta(days=91)
                cols[9].markdown(
                    f'<span style="color:{T["text_muted"]};font-size:0.85rem">~{_next_est.strftime("%b %d")}</span>',
                    unsafe_allow_html=True,
                )
        else:
            cols[9].markdown("—")
        with cols[10]:
            if st.button("", key=f"wl_rm_row_{t}", icon=":material/close:"):
                remove_from_watchlist(_sb_client, t)
                st.rerun()

    # ── Group rows by category and render ──
    _grouped = {c: [] for c in _categories}
    for row in rows:
        _cat = row.get('category', 'Uncategorized')
        if _cat not in _grouped:
            _cat = 'Uncategorized'
        _grouped[_cat].append(row)

    # Hero-card style per category
    _cat_keys = {_cat: f"wl_cat_{i}" for i, _cat in enumerate(_categories)}
    _active_cats = [c for c in _categories if _grouped[c]]
    st.markdown(
        '<style>'
        + ''.join(
            f'.st-key-{_cat_keys[c]} {{'
            f'  background: {T["card"]};'
            f'  border-radius: 24px;'
            f'  border-top: 3px solid {T["accent"]};'
            f'  padding: 32px;'
            f'  box-shadow: {T["shadow"]};'
            f'  margin-bottom: 20px;'
            f'}}'
            for c in _active_cats
        )
        + '</style>',
        unsafe_allow_html=True,
    )

    for _cat in _active_cats:
        _cat_rows = _grouped[_cat]
        with st.container(key=_cat_keys[_cat]):
            st.markdown(
                f'<div style="font-size:0.95rem;font-weight:700;color:{T["text"]};margin-bottom:4px">'
                f'{_cat} <span style="font-weight:400;color:{T["text_muted"]};font-size:0.85rem">'
                f'{len(_cat_rows)}</span></div>',
                unsafe_allow_html=True,
            )
            _render_wl_header()
            for row in _cat_rows:
                _render_wl_row(row)

    st.markdown("")


def _dcf_editor(ticker):
    """Full DCF editor page for a single ticker."""
    cfg = load_config(_sb_client, ticker)
    if cfg is None:
        st.error(f"No config found for {ticker}")
        if st.button("\u2190 Watchlist", key="editor_back_err"):
            del st.query_params["edit"]
            st.rerun()
        return

    # ── Back button ──
    if st.button("\u2190 Watchlist", key="editor_back"):
        st.session_state["_wl_config_dirty"] = True
        del st.query_params["edit"]
        st.rerun()

    # ── Live price ──
    @st.cache_data(ttl=30)
    def _price(t):
        try:
            p, _, _ = fetch_stock_price(t)
            return p
        except Exception as e:
            logger.debug("Stock price fetch failed for %s: %s", t, e)
            return 0.0

    live_price = _price(ticker)
    if live_price > 0:
        cfg['stock_price'] = live_price

    # ── Valuation summary (hero card) — placeholder, filled after tabs with updated values ──
    val = compute_intrinsic_value(cfg)  # initial calc for tabs that need it
    _hero_placeholder = st.empty()

    # ── Status pills (inside hero card area) ──
    _cat_options = ["Uncategorized", "Yes", "Maybe", "Watch Later", "No"]
    _cur_cat = cfg.get('category', 'Uncategorized')
    _cat_idx = _cat_options.index(_cur_cat) if _cur_cat in _cat_options else 0
    _new_cat = st.pills(
        "Status", _cat_options, default=_cat_options[_cat_idx],
        key="dcf_category",
    )
    if _new_cat and _new_cat != _cur_cat:
        cfg['category'] = _new_cat
        save_config(_sb_client, ticker, cfg)
        st.rerun()

    # ── Earnings warning (hero card) ──
    @st.cache_data(ttl=300, show_spinner=False)
    def _cached_earnings_single(t):
        return fetch_earnings_dates([t])

    _earn_data = _cached_earnings_single(ticker).get(ticker)
    _days_to_earn = None
    if _earn_data and _earn_data.get('date') and _earn_data['date'] >= date.today():
        _days_to_earn = (_earn_data['date'] - date.today()).days
        if _days_to_earn <= 14:
            _earn_color = T['red'] if _days_to_earn <= 7 else T['text_muted']
            _earn_label = "Earnings" if not _earn_data.get('estimated') else "Earnings (est)"
            _earn_time = " BMO" if _earn_data.get('time') == 'bmo' else (" AMC" if _earn_data.get('time') == 'amc' else "")
            st.markdown(
                f'<div style="text-align:center;margin:-8px 0 12px">'
                f'<span class="stat-pill" style="color:{_earn_color};border-color:{_earn_color}">'
                f'{_earn_label}: {_earn_data["date"].strftime("%b %d")}{_earn_time} ({_days_to_earn}d)</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # Projections data (needed inside and after expander)
    base_year = cfg.get('base_year', 2025)
    growth = list(cfg.get('revenue_growth', []))
    margins = list(cfg.get('op_margins', []))

    # ── Tabs: DCF / Reverse DCF / Peer Comparison ──
    _tab_dcf, _tab_rdcf, _tab_peers, _tab_fundamentals, _tab_notes = st.tabs(["DCF", "Reverse DCF", "Peer Comparison", "Fundamentals", "Notes"])

    with _tab_dcf:
        st.markdown("#### Discounting Cash Flows")

        # ── WACC Inputs (collapsible) ──
        _ww_val = f'<div style="display:flex;justify-content:space-between;padding:6px 0;color:{T["text"]}"><span style="color:{T["text"]};{{extra}}">{{label}}</span><span style="color:{T["text"]};{{extra}}">{{value}}</span></div>'
        _ww_sep = f'<div style="border-top:1px solid {T["separator"]};margin:2px 0"></div>'

        with st.expander("### WACC", expanded=False):
          with st.container(border=True):
            cfg['risk_free_rate'] = st.number_input(
                "Risk-Free Rate %", value=cfg.get('risk_free_rate', 0.04) * 100,
                step=0.1, format="%.2f", key="ed_rfr",
            ) / 100
            cfg['erp'] = st.number_input(
                "Equity Risk Premium %", value=cfg.get('erp', 0.055) * 100,
                step=0.1, format="%.2f", key="ed_erp",
            ) / 100
            cfg['credit_spread'] = st.number_input(
                "Credit Spread %", value=cfg.get('credit_spread', 0.01) * 100,
                step=0.1, format="%.2f", key="ed_cs",
            ) / 100
            cfg['tax_rate'] = st.number_input(
                "Tax Rate %", value=cfg.get('tax_rate', 0.21) * 100,
                step=0.5, format="%.1f", key="ed_tax",
            ) / 100

            st.markdown(_ww_sep, unsafe_allow_html=True)

            cfg['equity_market_value'] = int(st.number_input(
                "Equity Market Value ($M)", value=int(cfg.get('equity_market_value', 0)),
                step=1000, key="ed_eq_val",
            ))
            cfg['debt_market_value'] = int(st.number_input(
                "Debt Market Value ($M)", value=int(cfg.get('debt_market_value', 0)),
                step=100, key="ed_debt_val",
            ))

            _eq_val = cfg['equity_market_value']
            _debt_val = cfg['debt_market_value']
            _total_cap = _eq_val + _debt_val
            _eq_wt = _eq_val / _total_cap if _total_cap > 0 else 0
            _debt_wt = _debt_val / _total_cap if _total_cap > 0 else 0
            st.markdown(_ww_val.format(label="Equity Weight", value=f"{_eq_wt:.1%}", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
            st.markdown(_ww_val.format(label="Debt Weight", value=f"{_debt_wt:.1%}", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)

            st.markdown(_ww_sep, unsafe_allow_html=True)

            # Sector betas
            @st.cache_data(ttl=3600, show_spinner=False)
            def _damodaran_betas():
                return fetch_sector_betas()

            dam_betas = _damodaran_betas()
            sector_list = sorted(dam_betas.keys()) if dam_betas else []

            betas = list(cfg.get('sector_betas', []))
            # Auto-detect sector from SIC code if no betas configured yet
            if not betas:
                _sic = cfg.get('sic_code', 0)
                if _sic and _sic in SIC_TO_SECTOR:
                    _auto_name, _auto_beta = SIC_TO_SECTOR[_sic]
                    betas = [(_auto_name, _auto_beta, 1.0)]
                elif dam_betas:
                    _sic_desc = cfg.get('sic_description', '')
                    if _sic_desc:
                        _sic_words = set(_sic_desc.lower().split())
                        _best, _best_score = None, 0
                        for _s, _b in dam_betas.items():
                            _overlap = len(_sic_words & set(_s.lower().split()))
                            if _overlap > _best_score:
                                _best_score = _overlap
                                _best = (_s, _b)
                        if _best and _best_score > 0:
                            betas = [(_best[0], _best[1], 1.0)]
                    if not betas:
                        betas = [("Market", dam_betas.get("Market", 1.0), 1.0)]
                else:
                    betas = [("Market", 1.0, 1.0)]
            st.markdown("**Sector Betas**")
            updated_betas = []
            for i, (name, beta, weight) in enumerate(betas):
                bc1, bc2, bc3, bc4 = st.columns([3, 2, 2, 0.5])
                with bc1:
                    if sector_list:
                        if name and name not in sector_list:
                            sector_list = [name] + sector_list
                        idx = sector_list.index(name) if name in sector_list else 0
                        new_name = st.selectbox(
                            "Sector", sector_list, index=idx, key=f"ed_bn_{i}",
                        )
                        new_beta = dam_betas.get(new_name, float(beta))
                    else:
                        new_name = st.text_input("Sector", value=name, key=f"ed_bn_{i}")
                        new_beta = float(beta)
                with bc2:
                    new_beta = st.number_input(
                        "Unlevered Beta", value=float(new_beta), step=0.01,
                        format="%.2f", key=f"ed_bb_{i}",
                    )
                with bc3:
                    new_weight = st.number_input(
                        "Revenue Weight", value=float(weight), step=0.05,
                        format="%.2f", key=f"ed_bw_{i}",
                    )
                with bc4:
                    st.markdown("<div style='height:32px'></div>", unsafe_allow_html=True)
                    if st.button("\u2212", key=f"ed_bdel_{i}"):
                        continue
                updated_betas.append((new_name, new_beta, new_weight))
            if st.button("+ Add sector", key="ed_badd"):
                default_name = sector_list[0] if sector_list else "Market"
                default_beta = dam_betas.get(default_name, 1.0) if dam_betas else 1.0
                updated_betas.append((default_name, default_beta, 1.0))
            cfg['sector_betas'] = updated_betas

            _wu_beta = sum(ub * wt for _, ub, wt in cfg['sector_betas']) if cfg['sector_betas'] else 1.0
            _de_ratio = _debt_val / _eq_val if _eq_val > 0 else 0
            _lev_beta = _wu_beta * (1 + (1 - cfg['tax_rate']) * _de_ratio)
            st.markdown(_ww_val.format(label="Weighted Unlevered \u03b2", value=f"{_wu_beta:.2f}", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
            st.markdown(_ww_val.format(label="Levered \u03b2", value=f"{_lev_beta:.2f}", extra="font-weight:700;"), unsafe_allow_html=True)

            st.markdown(_ww_sep, unsafe_allow_html=True)

            _ke = cfg['risk_free_rate'] + _lev_beta * cfg['erp']
            _kd = (cfg['risk_free_rate'] + cfg['credit_spread']) * (1 - cfg['tax_rate'])
            st.markdown(_ww_val.format(label="Cost of Equity", value=f"{_ke:.2%}", extra="font-weight:700;"), unsafe_allow_html=True)
            st.markdown(_ww_val.format(label="Cost of Debt (after-tax)", value=f"{_kd:.2%}", extra="font-weight:700;"), unsafe_allow_html=True)

            st.markdown(_ww_sep, unsafe_allow_html=True)

            if _total_cap > 0:
                _wacc_computed = _eq_wt * _ke + _debt_wt * _kd
                st.markdown(_ww_val.format(label="WACC", value=f"{_wacc_computed:.2%}",
                                           extra=f"font-weight:700;font-size:1.15rem;color:{T['accent']};"), unsafe_allow_html=True)
            else:
                st.warning("Equity + Debt market value must be > 0 to compute WACC")

        _s2c_val = f'<div style="display:flex;justify-content:space-between;padding:6px 0;color:{T["text"]}"><span style="color:{T["text"]};{{extra}}">{{label}}</span><span style="color:{T["text"]};{{extra}}">{{value}}</span></div>'
        _s2c_sep = f'<div style="border-top:1px solid {T["separator"]};margin:2px 0"></div>'

        with st.expander("### Sales-to-Capital", expanded=False):
          with st.container(border=True):
            _s2c_years = cfg.get('ic_years', [])
            _s2c_rev = cfg.get('hist_revenue', [])
            _s2c_ca = cfg.get('current_assets', [])
            _s2c_cash = cfg.get('cash', [])
            _s2c_si = cfg.get('st_investments', [])
            _s2c_cl = cfg.get('current_liabilities', [])
            _s2c_sd = cfg.get('st_debt', [])
            _s2c_sl = cfg.get('st_leases', [])
            _s2c_ppe = cfg.get('net_ppe', [])
            _s2c_gi = cfg.get('goodwill_intang', [])
            _s2c_n = len(_s2c_years)

            if _s2c_n >= 2 and len(_s2c_rev) >= _s2c_n:
                _s2c_ratios = []
                for _si in range(1, _s2c_n):
                    _rev_chg = _s2c_rev[_si] - _s2c_rev[_si - 1]
                    _ncwc_now = (_s2c_ca[_si] - _s2c_cash[_si] - _s2c_si[_si]) - (_s2c_cl[_si] - _s2c_sd[_si] - _s2c_sl[_si])
                    _ncwc_prev = (_s2c_ca[_si-1] - _s2c_cash[_si-1] - _s2c_si[_si-1]) - (_s2c_cl[_si-1] - _s2c_sd[_si-1] - _s2c_sl[_si-1])
                    _delta_ncwc = _ncwc_now - _ncwc_prev
                    _delta_ppe = _s2c_ppe[_si] - _s2c_ppe[_si - 1]
                    _delta_gi = _s2c_gi[_si] - _s2c_gi[_si - 1]
                    _ic_chg = _delta_ncwc + _delta_ppe + _delta_gi

                    _yr_label = f"{_s2c_years[_si-1]}\u2192{_s2c_years[_si]}"
                    st.markdown(_s2c_val.format(label=f"**{_yr_label}**", value="", extra="font-weight:700;"), unsafe_allow_html=True)
                    st.markdown(_s2c_val.format(label="\u2003\u0394 Revenue", value=f"${_rev_chg:,.0f}", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
                    st.markdown(_s2c_val.format(label="\u2003\u0394 Non-cash WC", value=f"${_delta_ncwc:,.0f}", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
                    st.markdown(_s2c_val.format(label="\u2003\u0394 Net PP&E", value=f"${_delta_ppe:,.0f}", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
                    st.markdown(_s2c_val.format(label="\u2003\u0394 Goodwill & Intang.", value=f"${_delta_gi:,.0f}", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
                    st.markdown(_s2c_val.format(label="\u2003\u0394 Invested Capital", value=f"${_ic_chg:,.0f}", extra="font-weight:700;"), unsafe_allow_html=True)
                    if _ic_chg > 0 and _rev_chg != 0:
                        _yr_s2c = _rev_chg / _ic_chg
                        _s2c_ratios.append(_yr_s2c)
                        st.markdown(_s2c_val.format(label="\u2003Sales-to-Capital", value=f"{_yr_s2c:.2f}", extra="font-weight:700;"), unsafe_allow_html=True)
                    else:
                        st.markdown(_s2c_val.format(label="\u2003Sales-to-Capital", value="n/a", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
                    st.markdown(_s2c_sep, unsafe_allow_html=True)

                if _s2c_ratios:
                    _s2c_ratios.sort()
                    _s2c_median = _s2c_ratios[len(_s2c_ratios) // 2]
                    st.markdown(_s2c_val.format(label="Median Sales-to-Capital", value=f"{_s2c_median:.2f}",
                                               extra=f"font-weight:700;font-size:1.15rem;color:{T['accent']};"), unsafe_allow_html=True)
                    st.markdown(_s2c_val.format(label="Used in DCF", value=f"{cfg.get('sales_to_capital', 1.0):.2f}",
                                               extra="font-weight:700;font-size:1.05rem;"), unsafe_allow_html=True)
            else:
                st.info("Not enough historical data to compute Sales-to-Capital breakdown")

            # Sector reference from Damodaran
            st.markdown(_s2c_sep, unsafe_allow_html=True)
            @st.cache_data(ttl=3600, show_spinner=False)
            def _damodaran_s2c():
                return fetch_sector_s2c()

            _dam_s2c = _damodaran_s2c()
            if _dam_s2c:
                _sector_names = [name for name, _, _ in cfg.get('sector_betas', [])]
                _matched = []
                for _sn in _sector_names:
                    # Exact match first
                    if _sn in _dam_s2c:
                        _matched.append((_sn, _dam_s2c[_sn]))
                    else:
                        # Fuzzy: match on first word(s) before parentheses or common prefix
                        _sn_base = _sn.split("(")[0].strip().lower()
                        _sn_words = set(_sn.lower().split())
                        _best_name, _best_score = None, 0
                        for _ds in _dam_s2c:
                            _ds_base = _ds.split("(")[0].strip().lower()
                            _ds_words = set(_ds.lower().split())
                            _overlap = len(_sn_words & _ds_words)
                            if _sn_base == _ds_base:
                                _overlap += 5  # strong boost for matching base name
                            if _overlap > _best_score:
                                _best_score = _overlap
                                _best_name = _ds
                        if _best_name and _best_score >= 1:
                            _matched.append((_best_name, _dam_s2c[_best_name]))
                st.markdown("**Sector Reference (Damodaran)**")
                if _matched:
                    for _sn, _sv in _matched:
                        st.markdown(_s2c_val.format(label=f"\u2003{_sn}", value=f"{_sv:.2f}",
                                                   extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
                else:
                    st.markdown(f'<p style="color:{T["text_muted"]};font-size:0.85rem">No matching sector found</p>', unsafe_allow_html=True)

        st.markdown(f'<p style="color:{T["text_muted"]};font-size:0.85rem">In millions</p>', unsafe_allow_html=True)

        _n = len(growth)
        growth = [float(g) for g in growth]
        margins = [float(m) for m in margins]
        _base_rev = cfg.get('base_revenue', 0)
        _base_oi = cfg.get('base_oi', 0)
        _tg = float(cfg.get('terminal_growth', 0.03))
        _tm = float(cfg.get('terminal_margin', margins[-1] if margins else 0.30))

        # Expand single-value assumptions to per-year lists (all floats)
        _default_wacc = float(compute_wacc(cfg) if cfg.get('equity_market_value', 0) + cfg.get('debt_market_value', 0) > 0 else 0.08)
        _wacc_list = [float(x) for x in cfg.get('wacc_per_year', [_default_wacc] * _n)]
        if len(_wacc_list) < _n:
            _wacc_list.extend([_wacc_list[-1] if _wacc_list else _default_wacc] * (_n - len(_wacc_list)))
        _default_tax = float(cfg.get('tax_rate', 0.21))
        _tax_list = [float(x) for x in cfg.get('tax_per_year', [_default_tax] * _n)]
        if len(_tax_list) < _n:
            _tax_list.extend([_tax_list[-1] if _tax_list else _default_tax] * (_n - len(_tax_list)))
        _default_stc = float(cfg.get('sales_to_capital', 1.0))
        _stc_list = [float(x) for x in cfg.get('stc_per_year', [_default_stc] * _n)]
        if len(_stc_list) < _n:
            _stc_list.extend([_stc_list[-1] if _stc_list else _default_stc] * (_n - len(_stc_list)))
        _default_sbc = float(cfg.get('sbc_pct', 0.004))
        _sbc_list = [float(x) for x in cfg.get('sbc_per_year', [_default_sbc] * _n)]
        if len(_sbc_list) < _n:
            _sbc_list.extend([_sbc_list[-1] if _sbc_list else _default_sbc] * (_n - len(_sbc_list)))

        # Terminal column editable values (defaults from config or last year)
        _tv_tax_default = float(cfg.get('terminal_tax', _tax_list[-1] if _tax_list else _default_tax))
        _tv_stc_default = float(cfg.get('terminal_stc', _stc_list[-1] if _stc_list else _default_stc))
        _tv_sbc_default = float(cfg.get('terminal_sbc', _sbc_list[-1] if _sbc_list else _default_sbc))
        # Pre-read terminal WACC from session state (widget rendered after TV calc)
        _tv_wacc_default = cfg.get('terminal_wacc', _wacc_list[-1] if _wacc_list else _default_wacc)
        _tv_wacc = st.session_state.get("ed_w_tv", _tv_wacc_default * 100) / 100

        # Column layout: label + base year + 10 projection years + terminal
        _cw = [1.8] + [1] * (_n + 2)
        _tv_col = _n + 2  # terminal column index
        _cs = f'font-size:0.78rem;padding:2px 0;min-height:28px;display:flex;align-items:center;justify-content:right;color:{T["text"]}'
        _cs_bold = _cs + ';font-weight:700'
        _cs_label = f'font-size:0.78rem;padding:2px 0;min-height:28px;display:flex;align-items:center;color:{T["text"]}'
        _cs_label_bold = _cs_label + ';font-weight:700'
        _cs_sep = f'border-top:2px solid {T["border_medium"]};' + _cs
        _cs_hdr = f'font-size:0.78rem;padding:4px 0;min-height:32px;display:flex;align-items:center;justify-content:right;font-weight:700;border-bottom:2px solid {T["border_medium"]};color:{T["text"]}'
        _cs_hdr_label = f'font-size:0.78rem;padding:4px 0;min-height:32px;display:flex;align-items:center;font-weight:700;border-bottom:2px solid {T["border_medium"]};color:{T["text"]}'
        _tv_bg = f'border-left:2px solid {T["border_medium"]};padding-left:8px'

        def _dcf_row_label(cols, label, bold=False):
            with cols[0]:
                st.markdown(f"<div style='{_cs_label_bold if bold else _cs_label}'>{label}</div>", unsafe_allow_html=True)

        def _dcf_row_val(cols, idx, text, bold=False, sep=False, tv=False):
            style = _cs_sep if sep else (_cs_bold if bold else _cs)
            if tv or idx == _tv_col:
                style += f';{_tv_bg}'
            with cols[idx]:
                st.markdown(f"<div style='{style}'>{text}</div>", unsafe_allow_html=True)

        def _dcf_row_input(cols, idx, key, value, step, fmt, is_pct=True):
            with cols[idx]:
                val = round(float(value) * 100, 6) if is_pct else round(float(value), 6)
                stp = round(float(step), 6)
                # Force session state to float to prevent type mismatch on re-render
                if key in st.session_state and not isinstance(st.session_state[key], float):
                    st.session_state[key] = float(st.session_state[key])
                v = st.number_input(key, value=val, step=stp, format=fmt,
                                    key=key, label_visibility="collapsed")
                return v / 100 if is_pct else v

        def _dcf_divider():
            st.markdown(f"<div style='border-top:1px solid {T['spinner_border']};margin:2px 0'></div>", unsafe_allow_html=True)

        # ── Year header row ──
        with st.container(border=True):
            hdr = st.columns(_cw)
            with hdr[0]:
                st.markdown(f"<div style='{_cs_hdr_label}'></div>", unsafe_allow_html=True)
            with hdr[1]:
                st.markdown(f"<div style='{_cs_hdr}'>{base_year}</div>", unsafe_allow_html=True)
            for i in range(_n):
                with hdr[i + 2]:
                    st.markdown(f"<div style='{_cs_hdr}'>{base_year + i + 1}</div>", unsafe_allow_html=True)
            with hdr[_tv_col]:
                st.markdown(f"<div style='{_cs_hdr};{_tv_bg}'>Terminal</div>", unsafe_allow_html=True)

            # ── Period row ──
            pr = st.columns(_cw)
            _dcf_row_label(pr, "Period")
            _dcf_row_val(pr, 1, "0")
            for i in range(_n):
                _dcf_row_val(pr, i + 2, f"{0.5 + i:.1f}")
            _dcf_row_val(pr, _tv_col, "")

            # ── Revenue Growth (editable) ──
            gr = st.columns(_cw)
            _dcf_row_label(gr, "Revenue Growth", bold=True)
            _dcf_row_val(gr, 1, "")
            for i in range(_n):
                growth[i] = _dcf_row_input(gr, i + 2, f"ed_g_{i}", growth[i], 0.5, "%.2f")
            _tg = _dcf_row_input(gr, _tv_col, "ed_tg_tv", _tg, 0.5, "%.2f")

            _revs = [_base_rev]
            for g in growth:
                _revs.append(_revs[-1] * (1 + g))

            # ── Revenue (computed) ──
            rv = st.columns(_cw)
            _dcf_row_label(rv, "Revenue")
            _dcf_row_val(rv, 1, f"{_base_rev:,.0f}")
            for i in range(_n):
                _dcf_row_val(rv, i + 2, f"{_revs[i + 1]:,.0f}")
            _tv_rev = _revs[-1] * (1 + _tg)
            _dcf_row_val(rv, _tv_col, f"{_tv_rev:,.0f}")

            # ── Operating Margin (editable) ──
            mr = st.columns(_cw)
            _dcf_row_label(mr, "Operating Margin", bold=True)
            _base_margin = cfg.get('base_op_margin', 0)
            _dcf_row_val(mr, 1, f"{_base_margin:.2%}")
            for i in range(_n):
                margins[i] = _dcf_row_input(mr, i + 2, f"ed_m_{i}", margins[i], 0.5, "%.2f")
            _tm = _dcf_row_input(mr, _tv_col, "ed_tm_tv", _tm, 0.5, "%.2f")

            # ── Operating Income (computed) ──
            oi_row = st.columns(_cw)
            _dcf_row_label(oi_row, "Operating Income")
            _dcf_row_val(oi_row, 1, f"{_base_oi:,.0f}")
            _oi_vals = [_revs[i + 1] * margins[i] for i in range(_n)]
            for i in range(_n):
                _dcf_row_val(oi_row, i + 2, f"{_oi_vals[i]:,.0f}")
            _tv_oi = _tv_rev * _tm
            _dcf_row_val(oi_row, _tv_col, f"{_tv_oi:,.0f}")

            _dcf_divider()  # ── Revenue → NOPAT ──

            # ── Tax Rate (editable) ──
            tr = st.columns(_cw)
            _dcf_row_label(tr, "Tax Rate", bold=True)
            _dcf_row_val(tr, 1, f"{_default_tax:.2%}")
            for i in range(_n):
                _tax_list[i] = _dcf_row_input(tr, i + 2, f"ed_t_{i}", _tax_list[i], 0.5, "%.2f")
            _tv_tax = _dcf_row_input(tr, _tv_col, "ed_t_tv", _tv_tax_default, 0.5, "%.2f")

            # ── NOPAT (computed) ──
            np_row = st.columns(_cw)
            _dcf_row_label(np_row, "NOPAT")
            _base_nopat = _base_oi * (1 - _default_tax)
            _dcf_row_val(np_row, 1, f"{_base_nopat:,.0f}")
            _nopat_vals = [_oi_vals[i] * (1 - _tax_list[i]) for i in range(_n)]
            for i in range(_n):
                _dcf_row_val(np_row, i + 2, f"{_nopat_vals[i]:,.0f}")
            _tv_nopat = _tv_oi * (1 - _tv_tax)
            _dcf_row_val(np_row, _tv_col, f"{_tv_nopat:,.0f}")

            _dcf_divider()  # ── NOPAT → Reinvestment ──

            # ── Sales-to-Capital (editable) ──
            sc_row = st.columns(_cw)
            _dcf_row_label(sc_row, "Sales-to-Capital", bold=True)
            _dcf_row_val(sc_row, 1, "")
            for i in range(_n):
                _stc_list[i] = _dcf_row_input(sc_row, i + 2, f"ed_s_{i}", _stc_list[i], 0.05, "%.2f", is_pct=False)
            _tv_stc = _dcf_row_input(sc_row, _tv_col, "ed_s_tv", _tv_stc_default, 0.05, "%.2f", is_pct=False)

            # ── Reinvestment (computed) ──
            ri_row = st.columns(_cw)
            _dcf_row_label(ri_row, "Reinvestment")
            _dcf_row_val(ri_row, 1, "")
            _reinvest_vals = [(_revs[i + 1] - _revs[i]) / _stc_list[i] if _stc_list[i] else 0 for i in range(_n)]
            for i in range(_n):
                _dcf_row_val(ri_row, i + 2, f"{_reinvest_vals[i]:,.0f}")
            _tv_reinvest = (_tv_rev - _revs[-1]) / _tv_stc if _tv_stc else 0
            _dcf_row_val(ri_row, _tv_col, f"{_tv_reinvest:,.0f}")

            # ── SBC % (editable) ──
            sbc_row = st.columns(_cw)
            _dcf_row_label(sbc_row, "SBC % of Revenue", bold=True)
            _dcf_row_val(sbc_row, 1, "")
            for i in range(_n):
                _sbc_list[i] = _dcf_row_input(sbc_row, i + 2, f"ed_sbc_{i}", _sbc_list[i], 0.1, "%.2f")
            _tv_sbc_pct = _dcf_row_input(sbc_row, _tv_col, "ed_sbc_tv", _tv_sbc_default, 0.1, "%.2f")

            # ── SBC After-Tax (computed) ──
            sbc_at_row = st.columns(_cw)
            _dcf_row_label(sbc_at_row, "SBC (after-tax)")
            _dcf_row_val(sbc_at_row, 1, "")
            _sbc_vals = [_revs[i + 1] * _sbc_list[i] * (1 - _tax_list[i]) for i in range(_n)]
            for i in range(_n):
                _dcf_row_val(sbc_at_row, i + 2, f"{_sbc_vals[i]:,.0f}")
            _tv_sbc = _tv_rev * _tv_sbc_pct * (1 - _tv_tax)
            _dcf_row_val(sbc_at_row, _tv_col, f"{_tv_sbc:,.0f}")

            _dcf_divider()  # ── Reinvestment → FCFF ──

            # ── FCFF (computed) ──
            fcff_row = st.columns(_cw)
            _dcf_row_label(fcff_row, "FCFF")
            _dcf_row_val(fcff_row, 1, "")
            _fcff_vals = [_nopat_vals[i] - _reinvest_vals[i] - _sbc_vals[i] for i in range(_n)]
            for i in range(_n):
                _dcf_row_val(fcff_row, i + 2, f"{_fcff_vals[i]:,.0f}")
            _tv_fcff = _tv_nopat - _tv_reinvest - _tv_sbc
            _dcf_row_val(fcff_row, _tv_col, f"{_tv_fcff:,.0f}")

            # ── Undiscounted TV ──
            tv_row = st.columns(_cw)
            _dcf_row_label(tv_row, "Undiscounted TV")
            for i in range(_n + 1):
                _dcf_row_val(tv_row, i + 1, "")
            _tv_undiscounted = _tv_fcff / (_tv_wacc - _tg) if (_tv_wacc - _tg) > 0 else 0
            _dcf_row_val(tv_row, _tv_col, f"{_tv_undiscounted:,.0f}")

            _dcf_divider()  # ── FCFF → Discounting ──

            # ── WACC (editable) ──
            wr = st.columns(_cw)
            _dcf_row_label(wr, "WACC", bold=True)
            _dcf_row_val(wr, 1, "")
            for i in range(_n):
                _wacc_list[i] = _dcf_row_input(wr, i + 2, f"ed_w_{i}", _wacc_list[i], 0.1, "%.2f")
            _tv_wacc = _dcf_row_input(wr, _tv_col, "ed_w_tv", _tv_wacc, 0.1, "%.2f")

            # ── Cumulative Discount Factor (computed) ──
            df_row = st.columns(_cw)
            _dcf_row_label(df_row, "Cum. Discount Factor")
            _dcf_row_val(df_row, 1, "1")
            _df_vals = []
            for i in range(_n):
                period = 0.5 + i
                df = 1 / (1 + _wacc_list[i]) ** period if _wacc_list[i] > 0 else 1
                _df_vals.append(df)
                _dcf_row_val(df_row, i + 2, f"{df:.2f}")
            _dcf_row_val(df_row, _tv_col, "")

            # ── PV of FCFF (computed, with separator) ──
            pv_row = st.columns(_cw)
            _dcf_row_label(pv_row, "PV of FCFF", bold=True)
            _dcf_row_val(pv_row, 1, "", sep=True)
            _pv_vals = [_fcff_vals[i] * _df_vals[i] for i in range(_n)]
            for i in range(_n):
                _dcf_row_val(pv_row, i + 2, f"{_pv_vals[i]:,.0f}", sep=True)
            _tv_df = 1 / (1 + _tv_wacc) ** (0.5 + _n - 1) if _tv_wacc > 0 and _n > 0 else 1
            _pv_tv = _tv_undiscounted * _tv_df
            _dcf_row_val(pv_row, _tv_col, f"{_pv_tv:,.0f}", sep=True)

            # ── Enterprise Value ──
            _sum_pv = sum(_pv_vals)
            _ev = _sum_pv + _pv_tv
            ev_row = st.columns(_cw)
            _dcf_row_label(ev_row, "Enterprise Value", bold=True)
            _dcf_row_val(ev_row, 1, f"{_ev:,.0f}", bold=True)
            for i in range(_n + 1):
                _dcf_row_val(ev_row, i + 2, "")

        # Write back edited values and auto-save
        _prev_snapshot = (
            tuple(cfg.get('revenue_growth', [])), tuple(cfg.get('op_margins', [])),
            tuple(cfg.get('wacc_per_year', [])), tuple(cfg.get('tax_per_year', [])),
            tuple(cfg.get('stc_per_year', [])), tuple(cfg.get('sbc_per_year', [])),
            cfg.get('terminal_growth'), cfg.get('terminal_margin'),
            cfg.get('terminal_tax'), cfg.get('terminal_stc'),
            cfg.get('terminal_wacc'), cfg.get('terminal_sbc'),
        )
        cfg['revenue_growth'] = growth
        cfg['op_margins'] = margins
        cfg['wacc_per_year'] = _wacc_list
        cfg['tax_per_year'] = _tax_list
        cfg['stc_per_year'] = _stc_list
        cfg['sbc_per_year'] = _sbc_list
        cfg['terminal_growth'] = _tg
        cfg['terminal_margin'] = _tm
        cfg['terminal_tax'] = _tv_tax
        cfg['terminal_stc'] = _tv_stc
        cfg['terminal_wacc'] = _tv_wacc
        cfg['terminal_sbc'] = _tv_sbc_pct
        _new_snapshot = (
            tuple(growth), tuple(margins),
            tuple(_wacc_list), tuple(_tax_list),
            tuple(_stc_list), tuple(_sbc_list),
            _tg, _tm, _tv_tax, _tv_stc, _tv_wacc, _tv_sbc_pct,
        )
        if _new_snapshot != _prev_snapshot:
            save_config(_sb_client, ticker, cfg)

    with _tab_rdcf:
        import pandas as pd

        st.markdown("#### Reverse DCF")

        # ── Adjustable ranges (expander) ──
        _rdcf_g_range = None
        _rdcf_m_range = None
        with st.expander("Adjust ranges"):
            _rc1, _rc2, _rc3 = st.columns(3)
            with _rc1:
                st.markdown("**Revenue CAGR**")
                _g_base = sum(cfg.get('revenue_growth', [0])) / max(len(cfg.get('revenue_growth', [0])), 1) * 100
                _rg_min = st.number_input("Min %", value=max(0.0, float(round(_g_base - 5))), step=1.0, format="%.0f", key="rdcf_gmin") / 100
                _rg_max = st.number_input("Max %", value=float(round(_g_base + 5)), step=1.0, format="%.0f", key="rdcf_gmax") / 100
                _rg_step = st.number_input("Step %", value=0.5, step=0.5, format="%.1f", key="rdcf_gstep") / 100
                if _rg_step > 0 and _rg_max > _rg_min:
                    _rdcf_g_range = (_rg_min, _rg_max, _rg_step)
            with _rc2:
                st.markdown("**Operating Margin**")
                _m_base = sum(cfg.get('op_margins', [0])) / max(len(cfg.get('op_margins', [0])), 1) * 100
                _rm_min = st.number_input("Min %", value=max(1.0, float(round(_m_base - 5))), step=1.0, format="%.0f", key="rdcf_mmin") / 100
                _rm_max = st.number_input("Max %", value=float(round(_m_base + 5)), step=1.0, format="%.0f", key="rdcf_mmax") / 100
                _rm_step = st.number_input("Step %", value=0.5, step=0.5, format="%.1f", key="rdcf_mstep") / 100
                if _rm_step > 0 and _rm_max > _rm_min:
                    _rdcf_m_range = (_rm_min, _rm_max, _rm_step)
            with _rc3:
                st.markdown("**WACC**")
                _rdcf_wacc = st.number_input(
                    "WACC %", value=val['wacc'] * 100,
                    step=0.1, format="%.2f", key="rdcf_wacc",
                ) / 100

        # ── Compute reverse DCF ──
        _rdcf = compute_reverse_dcf(cfg, wacc=_rdcf_wacc,
                                     growth_range=_rdcf_g_range,
                                     margin_range=_rdcf_m_range)

        # ── Market vs Your Base Case comparison ──
        _bc = _rdcf['base_cagr']
        _bm = _rdcf['base_margin']
        _closest = _rdcf['closest']
        _impl_g, _impl_m = _closest if _closest else (_bc, _bm)

        _card_border = f'border-top:1px solid {T["border_medium"]};border-right:1px solid {T["border_medium"]};border-bottom:1px solid {T["border_medium"]};border-left:3px solid {T["accent"]}'
        _mc1, _mc2 = st.columns(2)
        with _mc1:
            st.markdown(
                f'<div style="{_card_border};border-radius:12px;padding:20px;text-align:center;background:{T["card"]};box-shadow:{T["shadow"]}">'
                f'<div style="color:{T["text_muted"]};font-size:0.75rem;text-transform:uppercase;letter-spacing:0.05em;font-weight:600">Market implies</div>'
                f'<div style="font-size:1.8rem;font-weight:700;margin:8px 0;color:{T["text"]}">{_impl_g:.1%} CAGR &nbsp;+&nbsp; {_impl_m:.1%} Margin</div>'
                f'<div style="color:{T["text_muted"]};font-size:0.85rem">to justify ${_rdcf["market_price"]:.2f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with _mc2:
            st.markdown(
                f'<div style="{_card_border};border-radius:12px;padding:20px;text-align:center;background:{T["card"]};box-shadow:{T["shadow"]}">'
                f'<div style="color:{T["text_muted"]};font-size:0.75rem;text-transform:uppercase;letter-spacing:0.05em;font-weight:600">Your base case</div>'
                f'<div style="font-size:1.8rem;font-weight:700;margin:8px 0;color:{T["text"]}">{_bc:.1%} CAGR &nbsp;+&nbsp; {_bm:.1%} Margin</div>'
                f'<div style="color:{T["text_muted"]};font-size:0.85rem">DCF value ${val["intrinsic_value"]:.2f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ── Conclusion ──
        if _impl_g > _bc * 1.1 or _impl_m > _bm * 1.1:
            _conclusion = (f"Market is more optimistic — it prices in "
                           f"{_impl_g:.1%} CAGR / {_impl_m:.1%} margin "
                           f"vs your {_bc:.1%} / {_bm:.1%}.")
        elif _impl_g < _bc * 0.9 or _impl_m < _bm * 0.9:
            _conclusion = (f"Potential undervaluation — market only requires "
                           f"{_impl_g:.1%} CAGR / {_impl_m:.1%} margin, "
                           f"below your {_bc:.1%} / {_bm:.1%} base case.")
        else:
            _conclusion = (f"Fairly priced — market-implied assumptions "
                           f"({_impl_g:.1%} CAGR / {_impl_m:.1%} margin) "
                           f"are close to your base case ({_bc:.1%} / {_bm:.1%}).")
        st.markdown(
            f'<div style="color:{T["text_muted"]};font-size:0.85rem;text-align:center;margin:12px 0 16px">{_conclusion}</div>',
            unsafe_allow_html=True,
        )

        # ── Sensitivity matrix ──
        st.markdown(f"**Sensitivity Matrix** — WACC: {_rdcf['wacc']:.2%} | Market: ${_rdcf['market_price']:.2f}")

        _g_tests = _rdcf['growth_tests']
        _m_tests = _rdcf['margin_tests']
        _closest = _rdcf['closest']
        _mkt = _rdcf['market_price']

        # Build pivot lookup
        _matrix_data = {}
        for entry in _rdcf['matrix']:
            _matrix_data[(entry['growth'], entry['margin'])] = entry['price']

        # Render as HTML table for full dark-mode support
        _hdr_style = f'background:{T["card"]};color:{T["text_muted"]};font-size:0.7rem;font-weight:600;padding:6px 8px;text-align:center;position:sticky;top:0;z-index:1'
        _row_hdr = f'background:{T["card"]};color:{T["text"]};font-size:0.75rem;font-weight:600;padding:6px 8px;text-align:left;position:sticky;left:0;z-index:1'
        _html = f'<div style="overflow-x:auto;border:1px solid {T["border_medium"]};border-radius:12px;background:{T["card"]}">'
        _html += '<table style="border-collapse:collapse;width:100%;font-size:0.75rem">'
        # Header row
        _html += f'<thead><tr><th style="{_hdr_style};text-align:left">CAGR \\ Margin</th>'
        for mg in _m_tests:
            _html += f'<th style="{_hdr_style}">{mg:.1%}</th>'
        _html += '</tr></thead><tbody>'
        # Data rows
        for g in _g_tests:
            _html += f'<tr><td style="{_row_hdr}">{g:.1%}</td>'
            for mg in _m_tests:
                price = _matrix_data.get((g, mg), 0)
                if (g, mg) == _closest:
                    _bg = T["accent"]
                    _fg = '#fff'
                    _fw = 'bold'
                elif price >= _mkt:
                    _bg = T["accent_fill"]
                    _fg = T["text"]
                    _fw = 'normal'
                else:
                    _bg = T["red_light"]
                    _fg = T["text"]
                    _fw = 'normal'
                _html += f'<td style="background:{_bg};color:{_fg};font-weight:{_fw};padding:6px 8px;text-align:center">${price:,.0f}</td>'
            _html += '</tr>'
        _html += '</tbody></table></div>'
        st.markdown(_html, unsafe_allow_html=True)

        # ── Legend ──
        st.markdown(
            f'<div style="display:flex;gap:20px;font-size:0.8rem;color:{T["text_muted"]};margin-top:4px">'
            f'<span><span style="display:inline-block;width:12px;height:12px;background:{T["accent"]};border-radius:2px;vertical-align:middle;margin-right:4px"></span>Market-implied</span>'
            f'<span><span style="display:inline-block;width:12px;height:12px;background:{T["accent_fill"]};border:1px solid {T["accent"]};border-radius:2px;vertical-align:middle;margin-right:4px"></span>Undervalued</span>'
            f'<span><span style="display:inline-block;width:12px;height:12px;background:{T["red_light"]};border:1px solid {T["red"]};border-radius:2px;vertical-align:middle;margin-right:4px"></span>Overvalued</span>'
            '</div>',
            unsafe_allow_html=True,
        )

    with _tab_peers:
        _base_margin_p = cfg.get('base_op_margin', 0)
        _rev_growth_p = growth[0] if growth else 0
        _ev_rev_p = _ev / _base_rev if _base_rev else 0
        st.markdown("#### Peer Comparison")

        # Compute metrics for current ticker
        _mkt_cap_p = cfg.get('equity_market_value', 0)
        _debt_p = cfg.get('debt_market_value', 0)
        _cash_p = cfg.get('cash_bridge', 0)
        _ev_calc_p = _mkt_cap_p + _debt_p - _cash_p
        _ebitda_p = _base_oi * 1.3 if _base_oi > 0 else 0
        _ev_ebitda_p = _ev_calc_p / _ebitda_p if _ebitda_p > 0 else 0
        _ni_list = cfg.get('hist_net_income', [])
        _ni_p = _ni_list[-1] if _ni_list else 0
        _pe_p = _mkt_cap_p / _ni_p if _ni_p > 0 else 0
        # ROIC: NOPAT / Invested Capital
        _ca_list = cfg.get('current_assets', [])
        _cl_list = cfg.get('current_liabilities', [])
        _ppe_list = cfg.get('net_ppe', [])
        _gi_list = cfg.get('goodwill_intang', [])
        _sd_list = cfg.get('st_debt', [])
        _ca_p = _ca_list[-1] if _ca_list else 0
        _cl_p = _cl_list[-1] if _cl_list else 0
        _ppe_p = _ppe_list[-1] if _ppe_list else 0
        _gi_p = _gi_list[-1] if _gi_list else 0
        _sd_p = _sd_list[-1] if _sd_list else 0
        _ic_p = (_ca_p - _cash_p) + _ppe_p + _gi_p - (_cl_p - _sd_p)
        _nopat_p = _base_oi * (1 - 0.21)
        _roic_p = _nopat_p / _ic_p if _ic_p > 0 else 0

        # Build all rows: current ticker first, then peers
        peers = cfg.get('peers', [])
        _peer_rows = [
            {"ticker": ticker, "ev_revenue": _ev_rev_p, "ev_ebitda": _ev_ebitda_p,
             "pe": _pe_p, "op_margin": _base_margin_p, "rev_growth": _rev_growth_p,
             "roic": _roic_p, "is_self": True},
        ] + [dict(**p, is_self=False) for p in peers]

        _peer_metrics = [
            ("EV/Rev", "ev_revenue", "x", 1),
            ("EV/EBITDA", "ev_ebitda", "x", 1),
            ("P/E", "pe", "x", 1),
            ("Op Margin", "op_margin", "%", 1),
            ("Rev Growth", "rev_growth", "%", 1),
            ("ROIC", "roic", "%", 0),
        ]

        _th_style = (f'text-align:right;padding:8px 12px;border-bottom:2px solid {T["border_medium"]};color:{T["text_muted"]};'
                     'font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em')
        _ptable = (
            '<div style="overflow-x:auto">'
            '<table style="width:100%;border-collapse:collapse;font-size:0.9rem">'
            '<thead><tr>'
            f'<th style="text-align:left;padding:8px 12px;border-bottom:2px solid {T["border_medium"]};color:{T["text_muted"]};'
            f'font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em">Company</th>'
        )
        for mlabel, _, _, _ in _peer_metrics:
            _ptable += f'<th style="{_th_style}">{mlabel}</th>'
        _ptable += '</tr></thead><tbody>'

        for idx_p, pr in enumerate(_peer_rows):
            _is_self = pr.get("is_self", False)
            _pt = pr.get("ticker", "")
            _logo_url = f"https://assets.parqet.com/logos/symbol/{_pt}"
            _row_bg = f'background:{T["row_alt"]};' if _is_self else ''
            _fw = 'font-weight:700;' if _is_self else ''
            _ptable += f'<tr style="{_row_bg}">'
            _ptable += (
                f'<td style="padding:10px 12px;border-bottom:1px solid {T["border_light"]};color:{T["text"]};{_fw}">'
                f'<div style="display:flex;align-items:center;gap:10px">'
                f'<img src="{_logo_url}" style="width:28px;height:28px;border-radius:50%;object-fit:cover" '
                f'onerror="this.style.display=\'none\'">'
                f'<span>{_pt}</span>'
                f'</div></td>'
            )
            for _, mkey, mfmt, mdec in _peer_metrics:
                _mv = pr.get(mkey, 0)
                if mfmt == "%" and _mv:
                    _mstr = f'{_mv:.{mdec}%}'
                elif mfmt == "x" and _mv:
                    _mstr = f'{_mv:.{mdec}f}x'
                else:
                    _mstr = "—"
                _ptable += (
                    f'<td style="text-align:right;padding:10px 12px;border-bottom:1px solid {T["border_light"]};color:{T["text"]};{_fw}">'
                    f'{_mstr}</td>'
                )
            _ptable += '</tr>'
        _ptable += '</tbody></table></div>'
        st.markdown(_ptable, unsafe_allow_html=True)

        # ── Manage peers ──
        st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
        _peer_tickers = [p.get("ticker", "") for p in peers]
        if _peer_tickers:
            # Key includes the current peer set so the widget re-initializes
            # when peers are added/removed externally (avoids stale selection
            # silently dropping just-added peers).
            _ms_key = "ed_peer_select_" + "_".join(sorted(_peer_tickers))
            _kept = st.multiselect(
                "Remove peers (deselect to remove)",
                options=_peer_tickers, default=_peer_tickers,
                key=_ms_key,
                help="Deselect a ticker to remove it from this peer group.",
            )
            if set(_kept) != set(_peer_tickers):
                cfg['peers'] = [p for p in peers if p.get("ticker") in _kept]
                save_config(_sb_client, ticker, cfg)
                st.rerun()

        st.markdown(
            f'<style>'
            f'div[data-testid="stForm"] div[data-testid="stTextInput"] input {{'
            f'  background-color: {T["row_alt"]} !important;'
            f'  border: 1px solid {T["border_medium"]} !important;'
            f'}}'
            f'</style>',
            unsafe_allow_html=True,
        )
        with st.form("add_peer_form"):
            _ac1, _ac2 = st.columns([5, 1])
            with _ac1:
                _new_peer = st.text_input(
                    "Add peer ticker",
                    key="ed_add_peer",
                    placeholder="e.g. MSFT, GOOG",
                )
            with _ac2:
                st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
                _add_clicked = st.form_submit_button("+ Add", use_container_width=True)
        if _add_clicked and _new_peer:
            _new_tickers = [t for t in (sanitize_ticker(t) for t in _new_peer.split(",")) if t]
            if not _new_tickers:
                st.warning("Invalid ticker(s). Use 1–5 letters only (e.g. MSFT, GOOG).")
            _existing = {p.get("ticker") for p in peers}
            _to_fetch = [t for t in _new_tickers if t not in _existing and t != ticker]
            if _to_fetch:
                with st.spinner(f"Fetching data for {', '.join(_to_fetch)}..."):
                    _new_peers = fetch_peer_data(_to_fetch)
                if _new_peers:
                    peers.extend(_new_peers)
                    cfg['peers'] = peers
                    save_config(_sb_client, ticker, cfg)
                    st.rerun()
                else:
                    st.warning("Could not fetch peer data. Check the ticker(s).")

    with _tab_fundamentals:
        st.markdown("#### Fundamentals")

        @st.cache_data(ttl=300, show_spinner="Loading fundamentals...")
        def _cached_fundamentals(t):
            return fetch_fundamentals(t, n_years=11)

        fund = _cached_fundamentals(ticker)
        _yrs = fund['years']
        _n = len(_yrs)

        # Chart style constants
        _COLORS = {
            'primary': T['accent'],
            'secondary': T['red'],
            'accent': '#3d405b',
            'tertiary': '#f2cc8f',
            'text_muted': T['text_muted'],
        }

        def _base_layout(fig, height=280):
            fig.update_layout(
                margin=dict(t=10, b=20, l=50, r=20),
                height=height,
                font=dict(
                    family="-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
                    color=T['chart_font'],
                ),
                paper_bgcolor=T['chart_paper'],
                plot_bgcolor=T['chart_plot'],
                xaxis=dict(gridcolor=T['chart_grid'], dtick=1),
                yaxis=dict(gridcolor=T['chart_grid']),
                legend=dict(
                    orientation="h", yanchor="top", y=-0.15,
                    xanchor="center", x=0.5, font=dict(size=11),
                ),
                hovermode="x unified",
            )
            return fig

        def _pct_growth(values):
            result = [None]
            for i in range(1, len(values)):
                prev, curr = values[i - 1], values[i]
                if prev and prev != 0 and curr is not None:
                    result.append((curr / prev) - 1)
                else:
                    result.append(None)
            return result

        # ── Operating Leverage ──
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:6px">'
            f'<span style="font-weight:700">Operating Leverage</span>'
            f'<span class="ol-tip" style="position:relative;cursor:help">'
            f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
            f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
            f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
            f'</svg>'
            f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
            f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
            f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
            f'font-weight:400;width:260px;z-index:999;box-shadow:{T["shadow_hover"]};'
            f'pointer-events:none;transition:opacity 0.15s ease">'
            f'DOL = OI Growth / Revenue Growth — measures how much operating income amplifies revenue growth.<br><br>'
            f'<b>&gt;1.0x</b> scale advantage (costs grow slower than revenue)<br>'
            f'<b>=1.0x</b> neutraal<br>'
            f'<b>&lt;1.0x</b> costs growing faster than revenue'
            f'</span></span></div>'
            f'<style>.ol-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
            unsafe_allow_html=True,
        )
        rev_g = _pct_growth(fund['revenue'])
        oi_g = _pct_growth(fund['operating_income'])
        if _n >= 3:
            # Chart: DOL (Degree of Operating Leverage)
            dol_values = []
            for r, o in zip(rev_g[1:], oi_g[1:]):
                if r is not None and o is not None and r != 0:
                    dol_values.append(round(o / r, 2))
                else:
                    dol_values.append(None)
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=_yrs[1:], y=dol_values,
                name='DOL',
                line=dict(color=_COLORS['accent'], width=2.5),
                hovertemplate='%{y:.1f}x<extra>DOL</extra>',
            ))
            # Reference line at 1.0x
            fig.add_hline(y=1.0, line_dash="dot", line_color=_COLORS['text_muted'],
                          annotation_text="1.0x", annotation_position="right")
            fig.update_yaxes(ticksuffix='x')
            _base_layout(fig)
            st.plotly_chart(fig, use_container_width=True)

            with st.expander("Details", expanded=False):
                _tbl_border = f'border-top:1px solid {T["grid"]}'
                _ol_cell = f'text-align:right;padding:6px 10px;font-size:0.85rem;color:{T["text"]};{_tbl_border}'
                _ol_hdr = f'text-align:right;padding:6px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                _ol_label = f'text-align:left;padding:6px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;{_tbl_border}'
                _ol_avg_style = f'{_ol_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                _ol_div = f'border-top:3px solid {T["text"]}'
                _ol_html = (
                    '<div style="overflow-x:auto">'
                    '<table style="width:100%;border-collapse:collapse">'
                    '<thead><tr>'
                    f'<th style="{_ol_hdr};text-align:left"></th>'
                )
                for yr in _yrs[1:]:
                    _ol_html += f'<th style="{_ol_hdr}">{yr}</th>'
                _ol_html += f'<th style="{_ol_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                _ol_html += '</tr></thead><tbody>'

                # Revenue Growth row
                _rev_vals = [rev_g[i] for i in range(1, _n) if rev_g[i] is not None]
                _rev_avg = sum(_rev_vals) / len(_rev_vals) if _rev_vals else None
                _ol_html += f'<tr><td style="{_ol_label}">Revenue Growth</td>'
                for i in range(1, _n):
                    v = rev_g[i]
                    _ol_html += f'<td style="{_ol_cell}">{v*100:.1f}%</td>' if v is not None else f'<td style="{_ol_cell}">—</td>'
                _ol_html += f'<td style="{_ol_avg_style}">{_rev_avg*100:.1f}%</td>' if _rev_avg is not None else f'<td style="{_ol_avg_style}">—</td>'
                _ol_html += '</tr>'

                # OI Growth row — green when OI growth > Rev growth, red when below
                _oi_vals = [oi_g[i] for i in range(1, _n) if oi_g[i] is not None]
                _oi_avg = sum(_oi_vals) / len(_oi_vals) if _oi_vals else None
                _ol_html += f'<tr><td style="{_ol_label}">OI Growth</td>'
                for i in range(1, _n):
                    r, o = rev_g[i], oi_g[i]
                    if o is not None:
                        if r is not None and o > r:
                            color = T['accent']
                        elif r is not None and o < r:
                            color = T['red']
                        else:
                            color = T['text']
                        weight = 'font-weight:600;' if color != T['text'] else ''
                        _ol_html += f'<td style="{_ol_cell};color:{color};{weight}">{o*100:.1f}%</td>'
                    else:
                        _ol_html += f'<td style="{_ol_cell}">—</td>'
                if _oi_avg is not None and _rev_avg is not None:
                    _oi_avg_color = T['accent'] if _oi_avg > _rev_avg else T['red']
                    _ol_html += f'<td style="{_ol_avg_style};color:{_oi_avg_color}">{_oi_avg*100:.1f}%</td>'
                else:
                    _ol_html += f'<td style="{_ol_avg_style}">—</td>'
                _ol_html += '</tr>'

                # DOL row — thick top border
                _dol_vals = []
                for i in range(1, _n):
                    r, o = rev_g[i], oi_g[i]
                    if r and o and r != 0:
                        _dol_vals.append(o / r)
                _dol_avg = sum(_dol_vals) / len(_dol_vals) if _dol_vals else None
                _ol_html += f'<tr><td style="{_ol_label};{_ol_div}">DOL</td>'
                for i in range(1, _n):
                    r, o = rev_g[i], oi_g[i]
                    if r and o and r != 0:
                        dol = o / r
                        color = T['accent'] if dol > 1 else T['red']
                        _ol_html += f'<td style="{_ol_cell};{_ol_div};color:{color};font-weight:600">{dol:.1f}x</td>'
                    else:
                        _ol_html += f'<td style="{_ol_cell};{_ol_div}">—</td>'
                if _dol_avg is not None:
                    _dol_avg_color = T['accent'] if _dol_avg > 1 else T['red']
                    _ol_html += f'<td style="{_ol_avg_style};{_ol_div};color:{_dol_avg_color}">{_dol_avg:.1f}x</td>'
                else:
                    _ol_html += f'<td style="{_ol_avg_style};{_ol_div}">—</td>'
                _ol_html += '</tr>'

                _ol_html += '</tbody></table></div>'
                st.markdown(_ol_html, unsafe_allow_html=True)
                st.caption("DOL > 1 = each % revenue growth translates into more than 1% earnings growth (scale advantage)")
        else:
            st.info("Insufficient data for Operating Leverage (need 3+ years)")

        # ── Margins ──
        st.markdown("")
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:6px">'
            f'<span style="font-weight:700">Margins</span>'
            f'<span class="mg-tip" style="position:relative;cursor:help">'
            f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
            f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
            f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
            f'</svg>'
            f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
            f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
            f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
            f'font-weight:400;width:240px;z-index:999;box-shadow:{T["shadow_hover"]};'
            f'pointer-events:none;transition:opacity 0.15s ease">'
            f'Gross = (Revenue − COGS) / Revenue<br>'
            f'Operating = OI / Revenue<br>'
            f'FCF = Free Cash Flow / Revenue<br><br>'
            f'Rising margins = pricing power and economies of scale.'
            f'</span></span></div>'
            f'<style>.mg-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
            unsafe_allow_html=True,
        )
        if _n >= 3:
            rev = fund['revenue']
            _gp = fund.get('gross_profit') or [None] * _n
            _cor = fund.get('cost_of_revenue') or [None] * _n
            gross_m = []
            for i in range(_n):
                if not rev[i]:
                    gross_m.append(None)
                elif _gp[i] is not None:
                    gross_m.append(_gp[i] / rev[i] * 100)
                elif _cor[i] is not None:
                    gross_m.append((rev[i] - _cor[i]) / rev[i] * 100)
                else:
                    gross_m.append(None)
            op_m = [fund['operating_income'][i] / rev[i] * 100
                    if rev[i] and fund['operating_income'][i] is not None else None
                    for i in range(_n)]
            fcf_m = [fund['fcf'][i] / rev[i] * 100
                     if rev[i] and fund['fcf'][i] is not None else None
                     for i in range(_n)]
            # Detect when gross == operating (happens for financials like V
            # where cost_of_revenue falls back to CostsAndExpenses, which
            # already includes all operating costs). In that case the gross
            # line carries no extra info and just obscures the operating line.
            _gross_dup = (
                any(v is not None for v in gross_m)
                and all(
                    (g is None and o is None)
                    or (g is not None and o is not None and abs(g - o) < 0.5)
                    for g, o in zip(gross_m, op_m)
                )
            )
            _series = [
                ('Operating', op_m, _COLORS['accent']),
                ('FCF', fcf_m, _COLORS['tertiary']),
            ]
            if not _gross_dup:
                _series.insert(0, ('Gross', gross_m, _COLORS['primary']))
            fig = go.Figure()
            for name, vals, color in _series:
                fig.add_trace(go.Scatter(
                    x=_yrs, y=vals, name=name,
                    line=dict(color=color, width=2.5),
                    hovertemplate='%{y:.1f}%<extra>' + name + ' Margin</extra>',
                ))
            fig.update_yaxes(ticksuffix='%')
            _base_layout(fig)
            st.plotly_chart(fig, use_container_width=True)
            if _gross_dup:
                st.caption("Gross margin weggelaten: dit bedrijf rapporteert geen aparte COGS, waardoor gross gelijk is aan operating margin.")

            with st.expander("Details", expanded=False):
                _m_cell = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text"]};border-top:1px solid {T["grid"]}'
                _m_hdr = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                _m_label = f'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;border-top:1px solid {T["grid"]}'
                _m_avg_style = f'{_m_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                _m_html = (
                    '<div style="overflow-x:auto">'
                    '<table style="width:100%;border-collapse:collapse">'
                    '<thead><tr>'
                    f'<th style="{_m_hdr};text-align:left"></th>'
                )
                for yr in _yrs:
                    _m_html += f'<th style="{_m_hdr}">{yr}</th>'
                _m_html += f'<th style="{_m_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                _m_html += '</tr></thead><tbody>'

                for label, vals in [('Gross', gross_m), ('Operating', op_m), ('FCF', fcf_m)]:
                    _valid = [v for v in vals if v is not None]
                    _avg = sum(_valid) / len(_valid) if _valid else None
                    _m_html += f'<tr style="border-top:1px solid {T["grid"]}"><td style="{_m_label}">{label}</td>'
                    for v in vals:
                        _m_html += f'<td style="{_m_cell}">{v:.1f}%</td>' if v is not None else f'<td style="{_m_cell}">—</td>'
                    _m_html += f'<td style="{_m_avg_style}">{_avg:.1f}%</td>' if _avg is not None else f'<td style="{_m_avg_style}">—</td>'
                    _m_html += '</tr>'

                # Operating Margin delta row — expanding margin = operating leverage
                _m_div = f'border-top:3px solid {T["text"]}'
                _m_html += f'<tr><td style="{_m_label};{_m_div}">Op Margin \u0394</td>'
                _delta_vals = []
                for i in range(_n):
                    if i == 0:
                        _m_html += f'<td style="{_m_cell};{_m_div}">—</td>'
                    elif op_m[i] is not None and op_m[i - 1] is not None:
                        d = op_m[i] - op_m[i - 1]
                        _delta_vals.append(d)
                        color = T['accent'] if d > 0 else T['red']
                        sign = '+' if d > 0 else ''
                        _m_html += f'<td style="{_m_cell};{_m_div};color:{color};font-weight:600">{sign}{d:.1f}pp</td>'
                    else:
                        _m_html += f'<td style="{_m_cell};{_m_div}">—</td>'
                _d_avg = sum(_delta_vals) / len(_delta_vals) if _delta_vals else None
                if _d_avg is not None:
                    d_color = T['accent'] if _d_avg > 0 else T['red']
                    d_sign = '+' if _d_avg > 0 else ''
                    _m_html += f'<td style="{_m_avg_style};{_m_div};color:{d_color}">{d_sign}{_d_avg:.1f}pp</td>'
                else:
                    _m_html += f'<td style="{_m_avg_style};{_m_div}">—</td>'
                _m_html += '</tr>'

                _m_html += '</tbody></table></div>'
                st.markdown(_m_html, unsafe_allow_html=True)
                st.caption("Op Margin \u0394 > 0 with growing revenue = operating leverage (cost scale advantage)")
        else:
            st.info("Insufficient data for Margins (need 3+ years)")

        # ── ROIC ──
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:6px">'
            f'<span style="font-weight:700">ROIC</span>'
            f'<span class="roic-tip" style="position:relative;cursor:help">'
            f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
            f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
            f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
            f'</svg>'
            f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
            f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
            f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
            f'font-weight:400;width:240px;z-index:999;box-shadow:{T["shadow_hover"]};'
            f'pointer-events:none;transition:opacity 0.15s ease">'
            f'NOPAT / Invested Capital — measures how well a company generates returns on its capital.<br><br>'
            f'<b>&gt;WACC</b> creates value<br>'
            f'<b>&gt;20%</b> excellent<br>'
            f'<b>&lt;WACC</b> destroys value'
            f'</span></span></div>'
            f'<style>.roic-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
            unsafe_allow_html=True,
        )
        if _n >= 3:
            roic_vals = []
            _nopat_tbl = []
            _ic_tbl = []
            for i in range(_n):
                oi = fund['operating_income'][i]
                eq = fund['total_equity'][i]
                debt = fund['total_debt'][i]
                cash_v = fund['cash'][i]
                tp = fund['tax_provision'][i]
                pti = fund['pretax_income'][i]
                tax_rate = tp / pti if pti and pti != 0 else 0.21
                nopat = oi * (1 - tax_rate) if oi is not None else None
                ic = (eq or 0) + (debt or 0) - (cash_v or 0)
                _nopat_tbl.append(nopat)
                _ic_tbl.append(ic if ic != 0 else None)
                roic_vals.append(nopat / ic * 100 if nopat is not None and ic and ic > 0 else None)

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=_yrs, y=roic_vals, name='ROIC',
                line=dict(color=_COLORS['primary'], width=2.5),
                hovertemplate='%{y:.1f}%<extra>ROIC</extra>',
            ))
            wacc_pct = val.get('wacc', 0) * 100
            if wacc_pct > 0:
                fig.add_hline(
                    y=wacc_pct, line_dash="dash",
                    line_color=_COLORS['secondary'],
                    annotation_text=f"WACC {wacc_pct:.1f}%",
                    annotation_position="top right",
                )
            fig.update_yaxes(ticksuffix='%')
            _base_layout(fig)
            st.plotly_chart(fig, use_container_width=True)

            with st.expander("Details", expanded=False):
                _rc_cell = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text"]};border-top:1px solid {T["grid"]}'
                _rc_hdr = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                _rc_label = f'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;border-top:1px solid {T["grid"]}'
                _rc_avg = f'{_rc_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                _rc_div = f'border-top:3px solid {T["text"]}'
                _rc_html = (
                    '<div style="overflow-x:auto">'
                    '<table style="width:100%;border-collapse:collapse">'
                    '<thead><tr>'
                    f'<th style="{_rc_hdr};text-align:left"></th>'
                )
                for yr in _yrs:
                    _rc_html += f'<th style="{_rc_hdr}">{yr}</th>'
                _rc_html += f'<th style="{_rc_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                _rc_html += '</tr></thead><tbody>'

                # NOPAT row
                _np_valid = [v for v in _nopat_tbl if v is not None]
                _np_avg = sum(_np_valid) / len(_np_valid) if _np_valid else None
                _rc_html += f'<tr><td style="{_rc_label}">NOPAT</td>'
                for v in _nopat_tbl:
                    _rc_html += f'<td style="{_rc_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_rc_cell}">—</td>'
                _rc_html += f'<td style="{_rc_avg}">{_np_avg:,.0f}</td>' if _np_avg is not None else f'<td style="{_rc_avg}">—</td>'
                _rc_html += '</tr>'

                # Invested Capital row
                _ic_valid = [v for v in _ic_tbl if v is not None]
                _ic_avg = sum(_ic_valid) / len(_ic_valid) if _ic_valid else None
                _rc_html += f'<tr><td style="{_rc_label}">Invested Capital</td>'
                for v in _ic_tbl:
                    _rc_html += f'<td style="{_rc_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_rc_cell}">—</td>'
                _rc_html += f'<td style="{_rc_avg}">{_ic_avg:,.0f}</td>' if _ic_avg is not None else f'<td style="{_rc_avg}">—</td>'
                _rc_html += '</tr>'

                # ROIC % row — thick top border
                _roic_valid = [v for v in roic_vals if v is not None]
                _roic_avg = sum(_roic_valid) / len(_roic_valid) if _roic_valid else None
                _rc_html += f'<tr><td style="{_rc_label};{_rc_div}">ROIC</td>'
                for v in roic_vals:
                    if v is not None:
                        _r_color = T['accent'] if v >= 15 else (T['red'] if v < wacc_pct else T['text'])
                        _rc_html += f'<td style="{_rc_cell};{_rc_div};color:{_r_color};font-weight:600">{v:.1f}%</td>'
                    else:
                        _rc_html += f'<td style="{_rc_cell};{_rc_div}">—</td>'
                if _roic_avg is not None:
                    _ra_color = T['accent'] if _roic_avg >= 15 else (T['red'] if _roic_avg < wacc_pct else T['text'])
                    _rc_html += f'<td style="{_rc_avg};{_rc_div};color:{_ra_color}">{_roic_avg:.1f}%</td>'
                else:
                    _rc_html += f'<td style="{_rc_avg};{_rc_div}">—</td>'
                _rc_html += '</tr>'

                _rc_html += '</tbody></table></div>'
                st.markdown(_rc_html, unsafe_allow_html=True)
                st.caption("In $M. NOPAT = Operating Income × (1 − Tax Rate). IC = Equity + Debt − Cash.")
        else:
            st.info("Insufficient data for ROIC (need 3+ years)")

        # ── FCF Conversion ──
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:6px">'
            f'<span style="font-weight:700">FCF Conversion</span>'
            f'<span class="fcf-tip" style="position:relative;cursor:help">'
            f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
            f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
            f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
            f'</svg>'
            f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
            f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
            f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
            f'font-weight:400;width:240px;z-index:999;box-shadow:{T["shadow_hover"]};'
            f'pointer-events:none;transition:opacity 0.15s ease">'
            f'FCF / Net Income — measures how efficiently earnings convert into cash.<br><br>'
            f'<b>&gt;80%</b> high quality earnings<br>'
            f'<b>50–80%</b> acceptable<br>'
            f'<b>&lt;50%</b> potential red flag'
            f'</span></span></div>'
            f'<style>.fcf-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
            unsafe_allow_html=True,
        )
        if _n >= 3:
            conv = [fund['fcf'][i] / fund['net_income'][i] * 100
                    if fund['net_income'][i] and fund['net_income'][i] != 0
                       and fund['fcf'][i] is not None
                    else None
                    for i in range(_n)]
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=_yrs, y=conv, name='FCF / Net Income',
                line=dict(color=_COLORS['primary'], width=2.5),
                hovertemplate='%{y:.0f}%<extra>FCF Conversion</extra>',
            ))
            fig.add_hline(y=80, line_dash="dash", line_color=_COLORS['accent'],
                          annotation_text="80% (high quality)", annotation_position="top right")
            fig.add_hline(y=50, line_dash="dot", line_color=_COLORS['secondary'],
                          annotation_text="50% (red flag)", annotation_position="bottom right")
            fig.update_yaxes(ticksuffix='%')
            _base_layout(fig)
            st.plotly_chart(fig, use_container_width=True)

            with st.expander("Details", expanded=False):
                _fc_cell = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text"]};border-top:1px solid {T["grid"]}'
                _fc_hdr = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                _fc_label = f'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;border-top:1px solid {T["grid"]}'
                _fc_avg = f'{_fc_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                _fc_div = f'border-top:3px solid {T["text"]}'
                _fc_html = (
                    '<div style="overflow-x:auto">'
                    '<table style="width:100%;border-collapse:collapse">'
                    '<thead><tr>'
                    f'<th style="{_fc_hdr};text-align:left"></th>'
                )
                for yr in _yrs:
                    _fc_html += f'<th style="{_fc_hdr}">{yr}</th>'
                _fc_html += f'<th style="{_fc_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                _fc_html += '</tr></thead><tbody>'

                # Net Income row
                _ni_vals = fund['net_income']
                _ni_valid = [v for v in _ni_vals if v is not None]
                _ni_avg = sum(_ni_valid) / len(_ni_valid) if _ni_valid else None
                _fc_html += f'<tr><td style="{_fc_label}">Net Income</td>'
                for v in _ni_vals:
                    _fc_html += f'<td style="{_fc_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_fc_cell}">—</td>'
                _fc_html += f'<td style="{_fc_avg}">{_ni_avg:,.0f}</td>' if _ni_avg is not None else f'<td style="{_fc_avg}">—</td>'
                _fc_html += '</tr>'

                # FCF row
                _fcf_vals = fund['fcf']
                _fcf_valid = [v for v in _fcf_vals if v is not None]
                _fcf_avg = sum(_fcf_valid) / len(_fcf_valid) if _fcf_valid else None
                _fc_html += f'<tr><td style="{_fc_label}">Free Cash Flow</td>'
                for v in _fcf_vals:
                    _fc_html += f'<td style="{_fc_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_fc_cell}">—</td>'
                _fc_html += f'<td style="{_fc_avg}">{_fcf_avg:,.0f}</td>' if _fcf_avg is not None else f'<td style="{_fc_avg}">—</td>'
                _fc_html += '</tr>'

                # Conversion % row — thick top border as divider
                _conv_valid = [v for v in conv if v is not None]
                _conv_avg = sum(_conv_valid) / len(_conv_valid) if _conv_valid else None
                _fc_html += f'<tr><td style="{_fc_label};{_fc_div}">Conversion</td>'
                for v in conv:
                    if v is not None:
                        _c_color = T['accent'] if v >= 80 else (T['red'] if v < 50 else T['text'])
                        _fc_html += f'<td style="{_fc_cell};{_fc_div};color:{_c_color};font-weight:600">{v:.0f}%</td>'
                    else:
                        _fc_html += f'<td style="{_fc_cell};{_fc_div}">—</td>'
                if _conv_avg is not None:
                    _ca_color = T['accent'] if _conv_avg >= 80 else (T['red'] if _conv_avg < 50 else T['text'])
                    _fc_html += f'<td style="{_fc_avg};{_fc_div};color:{_ca_color}">{_conv_avg:.0f}%</td>'
                else:
                    _fc_html += f'<td style="{_fc_avg};{_fc_div}">—</td>'
                _fc_html += '</tr>'

                _fc_html += '</tbody></table></div>'
                st.markdown(_fc_html, unsafe_allow_html=True)
                st.caption("In $M. Conversion = FCF / Net Income.")
        else:
            st.info("Insufficient data for FCF Conversion (need 3+ years)")

        # ── Revenue per Share Growth ──
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:6px">'
            f'<span style="font-weight:700">Revenue per Share Growth</span>'
            f'<span class="rps-tip" style="position:relative;cursor:help">'
            f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
            f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
            f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
            f'</svg>'
            f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
            f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
            f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
            f'font-weight:400;width:260px;z-index:999;box-shadow:{T["shadow_hover"]};'
            f'pointer-events:none;transition:opacity 0.15s ease">'
            f'Compares total revenue growth with revenue per share.<br><br>'
            f'<b>Rev/Share &gt; Revenue</b> buybacks boost per-share growth<br>'
            f'<b>Rev/Share &lt; Revenue</b> dilution from share issuance'
            f'</span></span></div>'
            f'<style>.rps-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
            unsafe_allow_html=True,
        )
        if _n >= 3:
            # Derive shares from EPS / Net Income when the direct share tags
            # are missing (e.g. Visa only reports shares in non-standard tags)
            _shares_eff = []
            _eps_l = fund.get('eps') or [None] * _n
            _ni_l = fund.get('net_income') or [None] * _n
            for i in range(_n):
                s = fund['shares'][i]
                if not s or s <= 0:
                    if _eps_l[i] and _ni_l[i] is not None and _eps_l[i] != 0:
                        s = (_ni_l[i] * 1e6) / _eps_l[i]
                _shares_eff.append(s if s and s > 0 else None)
            rps = [fund['revenue'][i] * 1e6 / _shares_eff[i]
                   if _shares_eff[i] and fund['revenue'][i] is not None
                   else None
                   for i in range(_n)]
            rps_g = _pct_growth(rps)
            rev_g_clean = _pct_growth(fund['revenue'])
            _has_shares = any(s is not None for s in _shares_eff)
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=_yrs[1:], y=[r * 100 if r is not None else None for r in rev_g_clean[1:]],
                name='Revenue Growth',
                line=dict(color=_COLORS['primary'], width=2.5),
                hovertemplate='%{y:.1f}%<extra>Rev Growth</extra>',
            ))
            if _has_shares:
                fig.add_trace(go.Scatter(
                    x=_yrs[1:], y=[r * 100 if r is not None else None for r in rps_g[1:]],
                    name='Rev/Share Growth',
                    line=dict(color=_COLORS['accent'], width=2.5, dash='dash'),
                    hovertemplate='%{y:.1f}%<extra>Rev/Share Growth</extra>',
                ))
            fig.update_yaxes(ticksuffix='%')
            _base_layout(fig)
            st.plotly_chart(fig, use_container_width=True)
            if not _has_shares:
                st.caption("Rev/Share niet beschikbaar: dit bedrijf rapporteert geen share-counts in EDGAR (bv. V).")

            with st.expander("Details", expanded=False):
                _rps_cell = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text"]};border-top:1px solid {T["grid"]}'
                _rps_hdr = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                _rps_label = f'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;border-top:1px solid {T["grid"]}'
                _rps_avg_s = f'{_rps_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                _rps_div = f'border-top:3px solid {T["text"]}'
                _rps_html = (
                    '<div style="overflow-x:auto">'
                    '<table style="width:100%;border-collapse:collapse">'
                    '<thead><tr>'
                    f'<th style="{_rps_hdr};text-align:left"></th>'
                )
                for yr in _yrs:
                    _rps_html += f'<th style="{_rps_hdr}">{yr}</th>'
                _rps_html += f'<th style="{_rps_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                _rps_html += '</tr></thead><tbody>'

                # Revenue row ($M)
                _rev_valid = [v for v in fund['revenue'] if v is not None]
                _rev_avg2 = sum(_rev_valid) / len(_rev_valid) if _rev_valid else None
                _rps_html += f'<tr><td style="{_rps_label}">Revenue ($M)</td>'
                for v in fund['revenue']:
                    _rps_html += f'<td style="{_rps_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_rps_cell}">—</td>'
                _rps_html += f'<td style="{_rps_avg_s}">{_rev_avg2:,.0f}</td>' if _rev_avg2 is not None else f'<td style="{_rps_avg_s}">—</td>'
                _rps_html += '</tr>'

                # Shares row
                _sh_vals = [_shares_eff[i] / 1e6 if _shares_eff[i] else None for i in range(_n)]
                _sh_valid = [v for v in _sh_vals if v is not None]
                _sh_avg = sum(_sh_valid) / len(_sh_valid) if _sh_valid else None
                _rps_html += f'<tr><td style="{_rps_label}">Shares (M)</td>'
                for v in _sh_vals:
                    _rps_html += f'<td style="{_rps_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_rps_cell}">—</td>'
                _rps_html += f'<td style="{_rps_avg_s}">{_sh_avg:,.0f}</td>' if _sh_avg is not None else f'<td style="{_rps_avg_s}">—</td>'
                _rps_html += '</tr>'

                # Rev/Share row — thick border
                _rps_vals = [fund['revenue'][i] / _sh_vals[i] if _sh_vals[i] and _sh_vals[i] > 0 and fund['revenue'][i] is not None else None for i in range(_n)]
                _rps_valid2 = [v for v in _rps_vals if v is not None]
                _rps_avg2 = sum(_rps_valid2) / len(_rps_valid2) if _rps_valid2 else None
                _rps_html += f'<tr><td style="{_rps_label};{_rps_div}">Rev/Share ($)</td>'
                for v in _rps_vals:
                    _rps_html += f'<td style="{_rps_cell};{_rps_div}">${v:,.2f}</td>' if v is not None else f'<td style="{_rps_cell};{_rps_div}">—</td>'
                _rps_html += f'<td style="{_rps_avg_s};{_rps_div}">${_rps_avg2:,.2f}</td>' if _rps_avg2 is not None else f'<td style="{_rps_avg_s};{_rps_div}">—</td>'
                _rps_html += '</tr>'

                _rps_html += '</tbody></table></div>'
                st.markdown(_rps_html, unsafe_allow_html=True)
                st.caption("Revenue in $M. Rev/Share = Revenue ($M) / Shares (M).")
        else:
            st.info("Insufficient data for Revenue per Share (need 3+ years)")

        # ── Debt / FCF ──
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:6px">'
            f'<span style="font-weight:700">Debt / FCF</span>'
            f'<span class="df-tip" style="position:relative;cursor:help">'
            f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
            f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
            f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
            f'</svg>'
            f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
            f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
            f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
            f'font-weight:400;width:240px;z-index:999;box-shadow:{T["shadow_hover"]};'
            f'pointer-events:none;transition:opacity 0.15s ease">'
            f'Years of FCF needed to repay all debt.<br><br>'
            f'<b>&lt;3x</b> healthy balance sheet<br>'
            f'<b>3–5x</b> acceptabel<br>'
            f'<b>&gt;5x</b> high debt burden'
            f'</span></span></div>'
            f'<style>.df-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
            unsafe_allow_html=True,
        )
        if _n >= 3:
            debt_fcf = []
            for i in range(_n):
                fcf_v = fund['fcf'][i]
                debt_v = fund['total_debt'][i]
                if fcf_v and fcf_v > 0 and debt_v is not None:
                    debt_fcf.append(debt_v / fcf_v)
                else:
                    debt_fcf.append(None)
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=_yrs, y=debt_fcf, name='Debt/FCF',
                line=dict(color=_COLORS['accent'], width=2.5),
                hovertemplate='%{y:.1f}x<extra>Debt/FCF</extra>',
            ))
            fig.add_hline(y=3, line_dash="dash", line_color=_COLORS['primary'],
                          annotation_text="3x", annotation_position="top right")
            fig.add_hline(y=5, line_dash="dash", line_color=_COLORS['secondary'],
                          annotation_text="5x", annotation_position="top right")
            fig.update_yaxes(ticksuffix='x')
            _base_layout(fig)
            st.plotly_chart(fig, use_container_width=True)

            with st.expander("Details", expanded=False):
                _df_cell = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text"]};border-top:1px solid {T["grid"]}'
                _df_hdr = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                _df_label = f'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;border-top:1px solid {T["grid"]}'
                _df_avg_s = f'{_df_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                _df_div = f'border-top:3px solid {T["text"]}'
                _df_html = (
                    '<div style="overflow-x:auto">'
                    '<table style="width:100%;border-collapse:collapse">'
                    '<thead><tr>'
                    f'<th style="{_df_hdr};text-align:left"></th>'
                )
                for yr in _yrs:
                    _df_html += f'<th style="{_df_hdr}">{yr}</th>'
                _df_html += f'<th style="{_df_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                _df_html += '</tr></thead><tbody>'

                # Total Debt row
                _debt_vals = fund['total_debt']
                _debt_valid = [v for v in _debt_vals if v is not None]
                _debt_avg = sum(_debt_valid) / len(_debt_valid) if _debt_valid else None
                _df_html += f'<tr><td style="{_df_label}">Total Debt</td>'
                for v in _debt_vals:
                    _df_html += f'<td style="{_df_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_df_cell}">—</td>'
                _df_html += f'<td style="{_df_avg_s}">{_debt_avg:,.0f}</td>' if _debt_avg is not None else f'<td style="{_df_avg_s}">—</td>'
                _df_html += '</tr>'

                # FCF row
                _fcf2_vals = fund['fcf']
                _fcf2_valid = [v for v in _fcf2_vals if v is not None]
                _fcf2_avg = sum(_fcf2_valid) / len(_fcf2_valid) if _fcf2_valid else None
                _df_html += f'<tr><td style="{_df_label}">Free Cash Flow</td>'
                for v in _fcf2_vals:
                    _df_html += f'<td style="{_df_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_df_cell}">—</td>'
                _df_html += f'<td style="{_df_avg_s}">{_fcf2_avg:,.0f}</td>' if _fcf2_avg is not None else f'<td style="{_df_avg_s}">—</td>'
                _df_html += '</tr>'

                # Debt/FCF row — thick border
                _df_valid2 = [v for v in debt_fcf if v is not None]
                _df_avg2 = sum(_df_valid2) / len(_df_valid2) if _df_valid2 else None
                _df_html += f'<tr><td style="{_df_label};{_df_div}">Debt / FCF</td>'
                for v in debt_fcf:
                    if v is not None:
                        _d_color = T['accent'] if v < 3 else (T['red'] if v > 5 else T['text'])
                        _df_html += f'<td style="{_df_cell};{_df_div};color:{_d_color};font-weight:600">{v:.1f}x</td>'
                    else:
                        _df_html += f'<td style="{_df_cell};{_df_div}">—</td>'
                if _df_avg2 is not None:
                    _da_color = T['accent'] if _df_avg2 < 3 else (T['red'] if _df_avg2 > 5 else T['text'])
                    _df_html += f'<td style="{_df_avg_s};{_df_div};color:{_da_color}">{_df_avg2:.1f}x</td>'
                else:
                    _df_html += f'<td style="{_df_avg_s};{_df_div}">—</td>'
                _df_html += '</tr>'

                _df_html += '</tbody></table></div>'
                st.markdown(_df_html, unsafe_allow_html=True)
                st.caption("In $M. Debt/FCF = Total Debt / Free Cash Flow.")
        else:
            st.info("Insufficient data for Debt/FCF (need 3+ years)")

        # ── FCF Yield ──
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:6px">'
            f'<span style="font-weight:700">FCF Yield</span>'
            f'<span class="fy-tip" style="position:relative;cursor:help">'
            f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
            f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
            f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
            f'</svg>'
            f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
            f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
            f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
            f'font-weight:400;width:240px;z-index:999;box-shadow:{T["shadow_hover"]};'
            f'pointer-events:none;transition:opacity 0.15s ease">'
            f'(FCF per Share / Price) × 100.<br>'
            f'Cash return percentage on your investment.<br><br>'
            f'<b>&gt;5%</b> attractively priced<br>'
            f'<b>3–5%</b> redelijk<br>'
            f'<b>&lt;1%</b> expensive or low cash generation'
            f'</span></span></div>'
            f'<style>.fy-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
            unsafe_allow_html=True,
        )
        if _n >= 2 and live_price > 0:
            # Prefer per-share path when shares are available; otherwise fall
            # back to FCF / Market Cap using the current market cap from cfg
            # (e.g. V doesn't report shares in XBRL).
            _mcap_total = cfg.get('equity_market_value', 0) or 0  # $M
            fcf_yield = []
            _fcf_ps = []
            for i in range(_n):
                sh = fund['shares'][i]
                if sh and sh > 0 and fund['fcf'][i] is not None:
                    fps = fund['fcf'][i] * 1e6 / sh
                    _fcf_ps.append(fps)
                    fcf_yield.append(fps / live_price * 100)
                elif fund['fcf'][i] is not None and _mcap_total > 0:
                    _fcf_ps.append(None)
                    fcf_yield.append(fund['fcf'][i] / _mcap_total * 100)
                else:
                    _fcf_ps.append(None)
                    fcf_yield.append(None)

            current_fy = fcf_yield[-1] if fcf_yield[-1] is not None else 0
            fy_color = T['accent'] if current_fy > 3 else (T['red'] if current_fy < 1 else T['text'])
            st.markdown(
                f'<div style="text-align:center;padding:8px 0">'
                f'<span style="font-size:2rem;font-weight:700;color:{fy_color}">{current_fy:.1f}%</span>'
                f'<span style="color:{T["text_muted"]};font-size:0.9rem;margin-left:8px">current FCF Yield</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=_yrs, y=fcf_yield, name='FCF Yield',
                line=dict(color=_COLORS['primary'], width=2.5),
                fill='tozeroy', fillcolor=T['accent_fill'],
                hovertemplate='%{y:.1f}%<extra>FCF Yield</extra>',
            ))
            fig.update_yaxes(ticksuffix='%')
            _base_layout(fig, height=250)
            st.plotly_chart(fig, use_container_width=True)

            with st.expander("Details", expanded=False):
                _fy_cell = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text"]};border-top:1px solid {T["grid"]}'
                _fy_hdr = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                _fy_label = f'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;border-top:1px solid {T["grid"]}'
                _fy_avg_s = f'{_fy_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                _fy_div = f'border-top:3px solid {T["text"]}'
                _fy_html = (
                    '<div style="overflow-x:auto">'
                    '<table style="width:100%;border-collapse:collapse">'
                    '<thead><tr>'
                    f'<th style="{_fy_hdr};text-align:left"></th>'
                )
                for yr in _yrs:
                    _fy_html += f'<th style="{_fy_hdr}">{yr}</th>'
                _fy_html += f'<th style="{_fy_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                _fy_html += '</tr></thead><tbody>'

                # FCF/Share row
                _fps_valid = [v for v in _fcf_ps if v is not None]
                _fps_avg = sum(_fps_valid) / len(_fps_valid) if _fps_valid else None
                _fy_html += f'<tr><td style="{_fy_label}">FCF / Share</td>'
                for v in _fcf_ps:
                    _fy_html += f'<td style="{_fy_cell}">${v:,.2f}</td>' if v is not None else f'<td style="{_fy_cell}">—</td>'
                _fy_html += f'<td style="{_fy_avg_s}">${_fps_avg:,.2f}</td>' if _fps_avg is not None else f'<td style="{_fy_avg_s}">—</td>'
                _fy_html += '</tr>'

                # Price row
                _fy_html += f'<tr><td style="{_fy_label}">Price</td>'
                for _ in _yrs:
                    _fy_html += f'<td style="{_fy_cell}">${live_price:,.2f}</td>'
                _fy_html += f'<td style="{_fy_avg_s}">${live_price:,.2f}</td>'
                _fy_html += '</tr>'

                # Yield row — thick border
                _fyl_valid = [v for v in fcf_yield if v is not None]
                _fyl_avg = sum(_fyl_valid) / len(_fyl_valid) if _fyl_valid else None
                _fy_html += f'<tr><td style="{_fy_label};{_fy_div}">FCF Yield</td>'
                for v in fcf_yield:
                    if v is not None:
                        _y_color = T['accent'] if v > 3 else (T['red'] if v < 1 else T['text'])
                        _fy_html += f'<td style="{_fy_cell};{_fy_div};color:{_y_color};font-weight:600">{v:.1f}%</td>'
                    else:
                        _fy_html += f'<td style="{_fy_cell};{_fy_div}">—</td>'
                if _fyl_avg is not None:
                    _ya_color = T['accent'] if _fyl_avg > 3 else (T['red'] if _fyl_avg < 1 else T['text'])
                    _fy_html += f'<td style="{_fy_avg_s};{_fy_div};color:{_ya_color}">{_fyl_avg:.1f}%</td>'
                else:
                    _fy_html += f'<td style="{_fy_avg_s};{_fy_div}">—</td>'
                _fy_html += '</tr>'

                _fy_html += '</tbody></table></div>'
                st.markdown(_fy_html, unsafe_allow_html=True)
                st.caption("FCF Yield = (FCF per Share / Price) × 100. Price = current price for all years.")
        else:
            st.info("Insufficient data for FCF Yield")

    with _tab_notes:
        _notes_val = cfg.get('notes', '')
        _new_notes = st.text_area(
            "Investment notes",
            value=_notes_val,
            height=200,
            key="ed_notes",
            placeholder="Investment thesis, key risks, catalysts, reminders...",
        )
        if _new_notes != _notes_val:
            cfg['notes'] = _new_notes
            save_config(_sb_client, ticker, cfg)

        st.markdown("---")
        _ai_h1, _ai_h2 = st.columns([5, 2])
        with _ai_h1:
            st.markdown("#### AI Research Sections")
            st.caption(
                "Run prompts tegen Groq Llama 3.3 70B (met fallback naar Gemini 2.5 Flash) "
                "of plak handmatig output. Output is markdown."
            )
        with _ai_h2:
            _gem_ok = _gemini_ready()
            if _gem_ok:
                _providers = []
                if _groq_api_key():
                    _providers.append("Groq")
                if _gemini_api_key():
                    _providers.append("Gemini")
                st.success(" + ".join(_providers) + " ready", icon="✅")
            else:
                st.warning("GROQ_API_KEY of GEMINI_API_KEY ontbreekt", icon="⚠️")

        _company_name = cfg.get('company', ticker)

        # ── Load library (globaal, via user_prefs) ──
        _prefs = load_user_prefs(_sb_client)
        _library = list(_prefs.get('ai_prompts') or [])

        # ── Results per ticker ──
        # New format: cfg['ai_notes'] is dict {title: content}
        # Old format (legacy): list of {"title","prompt","content"}
        _raw = cfg.get('ai_notes') or {}
        _results: dict[str, str] = {}
        if isinstance(_raw, list):
            # Migrate old list format → library + results
            for _sec in _raw:
                _t = _sec.get('title')
                if not _t:
                    continue
                _results[_t] = _sec.get('content', '')
                # Promote prompt to library if not present yet
                if _sec.get('prompt') and not any(
                    p.get('title') == _t for p in _library
                ):
                    _library.append({"title": _t, "prompt": _sec['prompt']})
            cfg['ai_notes'] = _results
            _prefs['ai_prompts'] = _library
            save_user_prefs(_sb_client, _prefs)
            save_config(_sb_client, ticker, cfg)
        else:
            _results = dict(_raw)

        # ── Auto-load shipped defaults into library ──
        _lib_titles = {p.get('title', '') for p in _library}
        _missing_defaults = [p for p in DEFAULT_AI_PROMPTS if p['title'] not in _lib_titles]
        if _missing_defaults:
            for p in _missing_defaults:
                _library.append({"title": p['title'], "prompt": p['prompt']})
            _prefs['ai_prompts'] = _library
            save_user_prefs(_sb_client, _prefs)

        # ── Per-ticker: render each library prompt with Run + result ──
        _results_changed = False
        for _li, _lp in enumerate(_library):
            _title = _lp.get('title', f'Prompt {_li + 1}')
            _prompt = _lp.get('prompt', '')
            _content = _results.get(_title, '')
            with st.expander(_title, expanded=True):
                _rb1, _rb2, _rb3 = st.columns([1, 1, 3])
                with _rb1:
                    _run_clicked = st.button(
                        "▶ Run", key=f"ed_ai_run_{_li}",
                        use_container_width=True, type="primary",
                        disabled=not _gem_ok,
                    )
                with _rb2:
                    _clear_clicked = st.button(
                        "Clear", key=f"ed_ai_clear_{_li}",
                        use_container_width=True,
                        disabled=not _content,
                    )

                _widget_key = f"ed_ai_res_{_li}"

                if _run_clicked:
                    if not _prompt.strip():
                        st.error("Prompt is leeg. Vul 'm in via de 📚 Prompt Library expander bovenaan.")
                        st.stop()
                    import re as _re
                    def _sub_prior(_m):
                        _t = _m.group(1).strip()
                        _c = _results.get(_t, '').strip()
                        if not _c:
                            return f"(no prior '{_t}' analysis available for this ticker)"
                        return _c
                    _filled = _re.sub(r'\{prior:([^}]+)\}', _sub_prior, _prompt)
                    _filled = _filled.replace("{ticker}", ticker).replace(
                        "{company}", _company_name
                    )
                    if "{ticker}" not in _prompt and "{company}" not in _prompt and "{prior:" not in _prompt:
                        _filled = (
                            f"**IMPORTANT OVERRIDE:** The company to analyze is "
                            f"**{_company_name} (ticker: {ticker})**. "
                            f"Do NOT ask the user for a company — it is provided here. "
                            f"Begin the analysis immediately using this company.\n\n"
                            f"---\n\n{_filled}"
                        )
                    with st.spinner(f"AI aan het werk ({_title})..."):
                        _ans, _err = _gemini_run(_filled)
                    if _err:
                        st.error(_err)
                    elif not _ans or not _ans.strip():
                        st.warning(f"AI call returned empty response for {_title}")
                    else:
                        _results[_title] = _ans
                        cfg['ai_notes'] = _results
                        save_config(_sb_client, ticker, cfg)
                        st.session_state.pop(_widget_key, None)
                        st.rerun()

                if _clear_clicked:
                    _results.pop(_title, None)
                    cfg['ai_notes'] = _results
                    save_config(_sb_client, ticker, cfg)
                    st.session_state.pop(_widget_key, None)
                    st.rerun()

                if _content.strip():
                    st.markdown(_content)
                else:
                    st.caption("_Nog geen output. Klik ▶ Run of plak handmatig via Edit._")
                with st.expander("Edit / paste", expanded=False):
                    # Sync widget state to saved content when they diverge
                    # (e.g. after Run or a switch between tickers)
                    if _widget_key not in st.session_state:
                        st.session_state[_widget_key] = _content
                    _new_content = st.text_area(
                        "Content",
                        height=280,
                        key=_widget_key,
                        label_visibility="collapsed",
                        placeholder="Plak of bewerk output hier (markdown)...",
                    )
                    if _new_content != _content:
                        _results[_title] = _new_content
                        _results_changed = True

        if _results_changed:
            cfg['ai_notes'] = _results
            save_config(_sb_client, ticker, cfg)

    with _tab_dcf:
        # ── Valuation Bridge (inside DCF tab) ──
        _bridge_keys = "ed_cash,ed_sec,ed_eqi,ed_debt,ed_min,ed_pen,ed_shares,ed_mos"
        _bk = _bridge_keys.split(",")
        _sel_input = ",\n".join(f'.st-key-{k} .stNumberInput input[type="number"]' for k in _bk)
        _sel_label = ",\n".join(
            f'.st-key-{k} [data-testid="stWidgetLabel"],\n'
            f'.st-key-{k} [data-testid="stWidgetLabel"] p,\n'
            f'.st-key-{k} .stNumberInput label' for k in _bk)
        st.markdown(f"""<style>
        {_sel_input} {{
            text-align: right !important;
            font-size: 1.15rem !important;
        }}
        {_sel_label} {{
            text-align: right !important;
            width: 100% !important;
            display: block !important;
        }}
        </style>""", unsafe_allow_html=True)
        _wf_val = f'<div style="display:flex;justify-content:space-between;padding:6px 0;color:{T["text"]}"><span style="color:{T["text"]};{{extra}}">{{label}}</span><span style="color:{T["text"]};{{extra}}">{{value}}</span></div>'
        _wf_sep = f'<div style="border-top:1px solid {T["separator"]};margin:2px 0"></div>'

        with st.container(key="valuation_bridge_card"):
            st.markdown("#### Valuation Bridge")

            st.markdown(_wf_val.format(label="Enterprise Value", value=f"${_ev:,.0f}",
                                       extra="font-weight:700;font-size:1.05rem;"), unsafe_allow_html=True)
            st.markdown(_wf_sep, unsafe_allow_html=True)

            # Bridge inputs: adds and subtracts side by side
            _bc1, _bc2, _bc3 = st.columns(3)
            with _bc1:
                cfg['cash_bridge'] = int(st.number_input(
                    "+ Cash ($M)", value=int(cfg.get('cash_bridge', 0)),
                    step=100, key="ed_cash",
                ))
            with _bc2:
                cfg['securities'] = int(st.number_input(
                    "+ Securities ($M)", value=int(cfg.get('securities', 0)),
                    step=100, key="ed_sec",
                ))
            with _bc3:
                cfg['equity_investments'] = int(st.number_input(
                    "+ Equity Inv. ($M)", value=int(cfg.get('equity_investments', 0)),
                    step=100, key="ed_eqi",
                ))
            _cash_sec = cfg['cash_bridge'] + cfg['securities'] + cfg['equity_investments']

            _bc4, _bc5, _bc6 = st.columns(3)
            with _bc4:
                cfg['debt_market_value'] = int(st.number_input(
                    "\u2212 Debt ($M)", value=int(cfg.get('debt_market_value', 0)),
                    step=100, key="ed_debt",
                ))
            with _bc5:
                cfg['minority_interest'] = int(st.number_input(
                    "\u2212 Minority Int. ($M)", value=int(cfg.get('minority_interest', 0)),
                    step=100, key="ed_min",
                ))
            with _bc6:
                cfg['unfunded_pension'] = int(st.number_input(
                    "\u2212 Unfunded Pen. ($M)", value=int(cfg.get('unfunded_pension', 0)),
                    step=100, key="ed_pen",
                ))
            _debt = cfg['debt_market_value'] + cfg['minority_interest'] + cfg['unfunded_pension']

            st.markdown(_wf_sep, unsafe_allow_html=True)
            _equity = _ev + _cash_sec - _debt
            st.markdown(_wf_val.format(label="Equity Value", value=f"${_equity:,.0f}",
                                       extra="font-weight:700;font-size:1.05rem;"), unsafe_allow_html=True)
            st.markdown(_wf_sep, unsafe_allow_html=True)

            # Shares and margin of safety side by side
            _bc7, _bc9 = st.columns(2)
            with _bc7:
                cfg['shares_outstanding'] = int(st.number_input(
                    "\u00f7 Shares Outstanding (M)", value=int(cfg.get('shares_outstanding', 0)),
                    step=10, key="ed_shares",
                ))
            with _bc9:
                cfg['margin_of_safety'] = st.number_input(
                    "\u00d7 Margin of Safety %", value=int(cfg.get('margin_of_safety', 0.20) * 100),
                    step=5, key="ed_mos",
                ) / 100
            _intrinsic = _equity / cfg['shares_outstanding'] if cfg['shares_outstanding'] > 0 else 0
            _mos = cfg['margin_of_safety']
            _buy = _intrinsic * (1 - _mos)

            # Store computed values for hero card and watchlist
            cfg['_computed_intrinsic'] = _intrinsic
            cfg['_computed_buy'] = _buy
            cfg['_computed_ev'] = _ev
            cfg['_computed_equity'] = _equity

            # Results summary
            _cur_price = cfg.get('stock_price', 0)
            _upside = (_intrinsic / _cur_price - 1) * 100 if _cur_price > 0 else 0
            _up_color = T['accent'] if _upside >= 0 else T['red']
            _up_label = "upside" if _upside >= 0 else "downside"

            st.markdown(
                f'<div style="border-top:2px solid {T["border_medium"]};margin:12px 0 8px 0;padding-top:12px">'
                f'<span style="font-size:1.05rem;font-weight:700;color:{T["text"]}">Result</span></div>',
                unsafe_allow_html=True,
            )

            _result_html = (
                f'<div style="display:flex;align-items:baseline;gap:32px;padding:4px 0;flex-wrap:wrap">'
                f'<div><span style="color:{T["text_muted"]};font-size:0.85rem">Intrinsic Value</span>'
                f'<br><span style="color:{T["text"]};font-weight:700;font-size:1.4rem">${_intrinsic:,.2f}</span></div>'
                f'<div><span style="color:{T["text_muted"]};font-size:0.85rem">Buy Price</span>'
                f'<br><span style="color:{T["accent"]};font-weight:700;font-size:1.4rem">${_buy:,.2f}</span></div>'
            )
            if _cur_price > 0:
                _result_html += (
                    f'<div><span style="color:{T["text_muted"]};font-size:0.85rem">Current Price</span>'
                    f'<br><span style="color:{T["text"]};font-weight:700;font-size:1.4rem">${_cur_price:,.2f}</span></div>'
                    f'<div><span style="color:{T["text_muted"]};font-size:0.85rem">{_up_label.title()}</span>'
                    f'<br><span style="color:{_up_color};font-weight:700;font-size:1.4rem">{_upside:+.1f}%</span></div>'
                )
            _result_html += '</div>'
            st.markdown(_result_html, unsafe_allow_html=True)

    # ── Action buttons ──
    st.markdown("---")
    btn1, btn2, btn3 = st.columns(3)
    with btn1:
        if st.button("Save", key="ed_save", use_container_width=True, type="primary"):
            # Pick up any unsubmitted peer ticker from the add-peer text field
            _pending_peer = (st.session_state.get("ed_add_peer") or "").strip()
            if _pending_peer:
                _pending = [t for t in (sanitize_ticker(x) for x in _pending_peer.split(",")) if t]
                _existing_t = {p.get("ticker") for p in cfg.get('peers', [])}
                _to_fetch_t = [t for t in _pending if t not in _existing_t and t != ticker]
                if _to_fetch_t:
                    with st.spinner(f"Fetching data for {', '.join(_to_fetch_t)}..."):
                        _added = fetch_peer_data(_to_fetch_t)
                    if _added:
                        cfg.setdefault('peers', []).extend(_added)
            save_config(_sb_client, ticker, cfg)
            st.success(f"{ticker} saved")
            st.rerun()
    with btn2:
        @st.cache_data(ttl=60, show_spinner=False)
        def _cached_excel(ticker_key, cfg_json):
            import json as _json
            return _build_excel_bytes(_json.loads(cfg_json))

        import json as _json
        excel_bytes = _cached_excel(ticker, _json.dumps(cfg, sort_keys=True, default=str))
        st.download_button(
            label="Download Excel",
            data=excel_bytes,
            file_name=f"{ticker}_DCF.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="ed_dl",
            type="primary",
        )
    with btn3:
        if st.button("Remove from Watchlist", key="ed_remove", use_container_width=True, type="primary"):
            remove_from_watchlist(_sb_client, ticker)
            del st.query_params["edit"]
            st.rerun()

    # Auto-save config at end of every editor render
    save_config(_sb_client, ticker, cfg)

    # ── Fill hero card placeholder with Valuation Bridge values ──
    _h_intrinsic = cfg.get('_computed_intrinsic', val['intrinsic_value'])
    _h_buy = cfg.get('_computed_buy', val['buy_price'])
    _h_ev = cfg.get('_computed_ev', val['enterprise_value'])
    _h_equity = cfg.get('_computed_equity', val['equity_value'])
    _h_upside = (_h_intrinsic / live_price - 1) if live_price > 0 else 0
    _h_up_color = T['accent'] if _h_upside >= 0 else T['red']
    _h_up_sign = "+" if _h_upside >= 0 else ""
    _h_wacc = val['wacc']
    _h_tv_pct = val['tv_pct']
    _hero_placeholder.markdown(
        f'<div class="hero-card">'
        f'<p class="hero-label">{cfg.get("company", ticker)}</p>'
        f'<div style="display:flex;align-items:center;justify-content:center;gap:12px">'
        f'<img src="https://assets.parqet.com/logos/symbol/{ticker}" '
        f'style="width:36px;height:36px;border-radius:50%;object-fit:cover" '
        f'onerror="this.style.display=\'none\'">'
        f'<p class="hero-value" style="font-size:2rem;margin:0">{ticker}</p>'
        f'</div>'
        f'<div class="stat-row">'
        f'<span class="stat-pill">Price <b>${live_price:.2f}</b></span>'
        f'<span class="stat-pill">Intrinsic Value <b>${_h_intrinsic:.2f}</b></span>'
        f'<span class="stat-pill">Buy Price <b>${_h_buy:.2f}</b></span>'
        f'<span class="stat-pill">Upside <b style="color:{_h_up_color}">{_h_up_sign}{_h_upside:.1%}</b></span>'
        f'<span class="stat-pill">WACC <b>{_h_wacc:.1%}</b></span>'
        f'<span class="stat-pill">EV <b>${_h_ev:,.0f}M</b></span>'
        f'<span class="stat-pill">Equity Value <b>${_h_equity:,.0f}M</b></span>'
        f'<span class="stat-pill">TV % of EV <b>{_h_tv_pct:.0%}</b></span>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def run_analysis(ticker, peer_mode, manual_peers, margin_of_safety, terminal_growth, n_peers):
    """Run the full DCF pipeline and return (excel_bytes, cfg, credit_rating)."""

    buf = io.StringIO()
    _cr = "N/A"

    with st.status("Analyzing " + ticker + "...", expanded=True) as status:
        pos = 0

        # ── Step 1: Company lookup ──
        status.write("\u23f3 Looking up company in SEC EDGAR...")
        with contextlib.redirect_stdout(buf):
            cik = get_cik(ticker)
            time.sleep(0.2)
            submissions = fetch_company_submissions(cik)
        company_name = submissions.get("name", ticker)
        sic_code = int(submissions.get("sic", 0))
        sic_desc = submissions.get("sicDescription", "")
        pos = _flush_clean(buf, pos, status)
        status.write(f"\u2705 **{company_name}** \u2014 {sic_desc}")

        # ── Step 2: Sector betas ──
        status.write("\u23f3 Determining sector & beta...")
        with contextlib.redirect_stdout(buf):
            if sic_code in SIC_TO_SECTOR:
                sector_name, sector_beta = SIC_TO_SECTOR[sic_code]
                sector_betas = [(sector_name, sector_beta, 1.0)]
            else:
                dam_betas = fetch_sector_betas()
                if dam_betas:
                    best_match, best_score = None, 0
                    sic_words = set(sic_desc.lower().split())
                    for sector, beta in dam_betas.items():
                        sector_words = set(sector.lower().split())
                        overlap = len(sic_words & sector_words)
                        if overlap > best_score:
                            best_score = overlap
                            best_match = (sector, beta)
                    if best_match and best_score > 0:
                        sector_name, sector_beta = best_match
                        sector_betas = [(sector_name, sector_beta, 1.0)]
                    else:
                        sector_betas = [("Market", 1.0, 1.0)]
                else:
                    sector_betas = [("Market", 1.0, 1.0)]
        pos = _flush_clean(buf, pos, status)
        status.write(f"\u2705 Sector: {sector_betas[0][0]} (beta {sector_betas[0][1]:.2f})")

        # ── Step 3: Financials ──
        status.write("\u23f3 Fetching financial statements from EDGAR...")
        with contextlib.redirect_stdout(buf):
            facts = fetch_company_facts(cik)
            financials = parse_financials(facts, n_years=6)
        pos = _flush_clean(buf, pos, status)
        years = financials.get("years", [])
        if years:
            status.write(f"\u2705 {len(years)} years of data ({years[0]}\u2013{years[-1]})")
        else:
            status.write(f"\u2705 Financial data loaded")

        # ── Step 4: Market data ──
        status.write("\u23f3 Fetching market data...")
        with contextlib.redirect_stdout(buf):
            stock_price, market_cap, shares_yahoo = fetch_stock_price(ticker)
            risk_free_rate = fetch_treasury_yield()
        pos = _flush_clean(buf, pos, status)
        status.write(f"\u2705 ${stock_price:.2f} per share \u2014 10Y Treasury: {risk_free_rate:.2%}")

        # ── Step 5: Credit rating + sector margin + consensus ──
        status.write("\u23f3 Analyzing credit, margins & analyst estimates...")
        with contextlib.redirect_stdout(buf):
            oi_latest = financials["operating_income"][-1] if financials["operating_income"] else 0
            ie_latest = financials["interest_expense_latest"]
            credit_rating, credit_spread = synthetic_credit_rating(oi_latest, ie_latest)
            _cr = credit_rating

            if market_cap == 0 and stock_price > 0:
                edgar_shares = financials["shares"][-1] if financials["shares"] and financials["shares"][-1] and financials["shares"][-1] > 0 else 0
                if edgar_shares > 0:
                    market_cap = round(stock_price * edgar_shares, 0)

            sector_margin = None
            sector_name_for_margin = sector_betas[0][0] if sector_betas else ""
            if sector_name_for_margin:
                dam_margins = fetch_sector_margins()
                if dam_margins:
                    if sector_name_for_margin in dam_margins:
                        sector_margin = dam_margins[sector_name_for_margin]
                    else:
                        target_words = set(sector_name_for_margin.lower().replace("/", " ").split())
                        best_m, best_s = None, 0
                        for sec_name, sec_margin in dam_margins.items():
                            sec_words = set(sec_name.lower().replace("/", " ").split())
                            overlap = len(target_words & sec_words)
                            if overlap > best_s:
                                best_s = overlap
                                best_m = (sec_name, sec_margin)
                        if best_m and best_s > 0:
                            sector_margin = best_m[1]

            consensus = fetch_consensus_estimates(ticker)
        pos = _flush_clean(buf, pos, status)
        status.write(f"\u2705 Credit: {credit_rating} (spread {credit_spread:.2%})")

        # ── Step 6: Peers ──
        peers = []
        peer_tickers = []
        if peer_mode == "Auto-discover":
            status.write(f"\u23f3 Auto-discovering {n_peers} comparable companies...")
            with contextlib.redirect_stdout(buf):
                peer_tickers = find_peers(
                    sic_code=sic_code,
                    target_ticker=ticker,
                    target_market_cap=market_cap,
                    n_peers=n_peers,
                )
            pos = _flush_clean(buf, pos, status)
            if peer_tickers:
                status.write(f"\u2705 Found peers: {', '.join(peer_tickers)}")
        elif peer_mode == "Manual" and manual_peers:
            peer_tickers = [t.strip().upper() for t in manual_peers.split(",") if t.strip()]

        if peer_tickers:
            status.write(f"\u23f3 Gathering data for {len(peer_tickers)} peers...")
            with contextlib.redirect_stdout(buf):
                peers = fetch_peer_data(peer_tickers)
            pos = _flush_clean(buf, pos, status)
            status.write(f"\u2705 {len(peers)} peer profiles loaded")
        elif peer_mode != "None":
            status.write("\u2705 No peers found")
        else:
            status.write("\u2705 Peer analysis skipped")

        # ── Step 7: Build config ──
        status.write("\u23f3 Building DCF model assumptions...")
        with contextlib.redirect_stdout(buf):
            cfg = build_config(
                ticker=ticker,
                financials=financials,
                stock_price=stock_price,
                market_cap=market_cap,
                shares_yahoo=shares_yahoo,
                risk_free_rate=risk_free_rate,
                sector_betas=sector_betas,
                credit_spread=credit_spread,
                credit_rating=credit_rating,
                peers=peers,
                company_name=company_name,
                margin_of_safety=margin_of_safety,
                terminal_growth=terminal_growth,
                sector_margin=sector_margin,
                consensus=consensus,
            )
        pos = _flush_clean(buf, pos, status)

        # Validate: enough data to build a meaningful DCF?
        base_rev = cfg.get("base_revenue", 0)
        base_year = cfg.get("base_year", 0)
        if base_rev <= 0:
            raise ValueError(
                f"{company_name} has no revenue data (or $0). "
                "A DCF model requires a company with revenue history."
            )
        if base_year < 2018:
            raise ValueError(
                f"{company_name}'s most recent filing is from {base_year}. "
                "The data is too old for a meaningful DCF analysis."
            )
        if market_cap <= 0 and stock_price <= 0:
            raise ValueError(
                f"Could not determine market cap or stock price for {ticker}. "
                "The company may be delisted or have no trading data."
            )

        status.write(f"\u2705 Configuration complete")

        # ── Step 8: Generate Excel ──
        status.write("\u23f3 Generating Excel DCF model...")
        with contextlib.redirect_stdout(buf):
            excel_bytes = _build_excel_bytes(cfg)
        pos = _flush_clean(buf, pos, status)

        status.update(label=f"Analysis complete \u2014 {company_name} ({ticker})", state="complete", expanded=False)

    return excel_bytes, cfg, _cr


# ══════════════════════════════════════════════════════
#  SIDEBAR — Navigation + page-specific settings
# ══════════════════════════════════════════════════════

# Eagerly load credentials into session_state so has_active_broker() works
_tt = _get_tt_token()
_ibkr = _get_ibkr_credentials()
logger.debug("Broker check: tt_token=%s ibkr_creds=%s", bool(_tt), bool(_ibkr))

with st.sidebar:
    st.toggle("Dark mode", key="dark_mode")

    def _on_nav_change():
        """Clear account page override when user clicks a main nav item."""
        st.session_state.pop("_account_page", None)

    _all_pages = ["Portfolio", "Wheel Cost Basis", "Results", "Watchlist", "Option Finder"]

    # CSS to add a visual separator after "Results" (3rd item)
    st.markdown(
        '<style>'
        '.st-key-nav_radio [role="radiogroup"] > label:nth-child(3) {'
        f'  border-bottom: 1px solid {T["separator"]};'
        '  padding-bottom: 8px;'
        '  margin-bottom: 4px;'
        '}'
        '</style>',
        unsafe_allow_html=True,
    )

    # Apply pending navigation from quick-link buttons (can't set widget key
    # after the widget is rendered, so we apply it before via default)
    if "_pending_nav" in st.session_state:
        st.session_state["nav_radio"] = st.session_state.pop("_pending_nav")

    _nav = st.radio(
        "Navigate",
        _all_pages,
        label_visibility="collapsed",
        key="nav_radio",
        on_change=_on_nav_change,
    )
    # ── Handle OAuth redirect (before page routing) ──
    _tt_connected = st.query_params.get("tt_connected")
    _tt_error = st.query_params.get("tt_error")
    if _tt_connected or _tt_error:
        st.session_state["_account_page"] = "Connect your Broker"
        # Persist OAuth result in session state so it survives the rerun
        # triggered by st.query_params.clear() (Streamlit ≥1.37)
        if _tt_connected:
            st.session_state["_tt_oauth_result"] = "success"
        else:
            st.session_state["_tt_oauth_result"] = _tt_error
        st.query_params.clear()

    page = st.session_state.get("_account_page") or _nav

    # ── Page view tracking ──
    if st.session_state.get("_last_page") != page:
        st.session_state["_last_page"] = page
        log_page_view(_sb_client, page)

    # ── Broker switcher (only if multiple brokers connected) ──
    _has_tt = bool(st.session_state.get("tt_refresh_token"))
    _has_ibkr = bool(st.session_state.get("ibkr_credentials"))
    if _has_tt and _has_ibkr:
        _broker_options = ["Tastytrade", "Interactive Brokers"]
        _broker_keys = ["tastytrade", "ibkr"]
        _current = get_active_broker()
        _idx = _broker_keys.index(_current) if _current in _broker_keys else 0
        _selected = st.selectbox(
            "Active Broker",
            _broker_options,
            index=_idx,
            key="_broker_select",
            label_visibility="collapsed",
        )
        _new_broker = _broker_keys[_broker_options.index(_selected)]
        if _new_broker != _current:
            st.session_state["active_broker"] = _new_broker
            for k in ["portfolio_data", "portfolio_account", "portfolio_prices",
                       "net_liq_all", "yearly_transfers", "benchmark_returns",
                       "portfolio_fetched_at"]:
                st.session_state.pop(k, None)
            for k in [k for k in st.session_state if k.startswith("net_liq_")]:
                st.session_state.pop(k, None)
            st.rerun()
    elif _has_tt:
        st.session_state["active_broker"] = "tastytrade"
    elif _has_ibkr:
        st.session_state["active_broker"] = "ibkr"

    st.markdown("---")

    if page in ("Portfolio", "Wheel Cost Basis", "Results"):
        _broker_label = "Interactive Brokers" if get_active_broker() == "ibkr" else "Tastytrade"
        st.markdown(f"### {_broker_label}")
        if st.button("Refresh Data", use_container_width=True, type="primary"):
            st.session_state.pop("portfolio_data", None)
            st.session_state.pop("portfolio_account", None)
            st.session_state.pop("portfolio_prices", None)
            st.session_state.pop("net_liq_all", None)
            st.session_state.pop("yearly_transfers", None)
            st.session_state.pop("benchmark_returns", None)
            st.session_state.pop("portfolio_fetched_at", None)
            st.session_state.pop("_ibkr_flex_cache", None)
            for k in [k for k in st.session_state if k.startswith("net_liq_")]:
                st.session_state.pop(k, None)
            st.rerun()

        if st.button("Clear Session Data", use_container_width=True, type="primary"):
            _preserve = {"dark_mode", "nav_radio", "_account_page",
                         "supabase_client", "user", "_user_id", "tt_refresh_token",
                         "ibkr_credentials", "active_broker"}
            for key in [k for k in st.session_state if k not in _preserve]:
                del st.session_state[key]
            st.rerun()

    st.markdown("---")

    def _on_acct_change():
        """Map account radio selection to _account_page."""
        sel = st.session_state.get("_acct_radio")
        if sel == "Connect your Broker":
            st.session_state["_account_page"] = "Connect your Broker"
        elif sel == "Security & Privacy":
            st.session_state["_account_page"] = "🔒 Security & Privacy"
        elif sel == "Sign Out":
            logout()

    _acct_default = None
    _active_acct = st.session_state.get("_account_page", "")
    if _active_acct == "Connect your Broker":
        _acct_default = 0
    elif _active_acct == "🔒 Security & Privacy":
        _acct_default = 1

    st.radio(
        "Account",
        ["Connect your Broker", "Security & Privacy", "Sign Out"],
        index=_acct_default,
        label_visibility="collapsed",
        key="_acct_radio",
        on_change=_on_acct_change,
    )

    try:
        from assets.logo_footer_b64 import LOGO_FOOTER_B64
        _dark = st.session_state.get("dark_mode", False)
        _filter = "filter: invert(1) brightness(2);" if _dark else ""
        st.markdown(
            f'<style>'
            f'.lt-sidebar-footer {{'
            f'  position: fixed; bottom: 16px; text-align: center;'
            f'  width: var(--sidebar-width, 245px); left: 0; opacity: 0.5;'
            f'  pointer-events: none;'
            f'}}'
            f'</style>'
            f'<div class="lt-sidebar-footer">'
            f'<img src="data:image/png;base64,{LOGO_FOOTER_B64}" '
            f'style="width: 36px; {_filter}" />'
            f'</div>',
            unsafe_allow_html=True,
        )
    except ImportError:
        pass


# ══════════════════════════════════════════════════════
#  SHARED DATA LOADING FOR PORTFOLIO PAGES
# ══════════════════════════════════════════════════════

def _load_portfolio_data():
    """Fetch and enrich portfolio data (cached in session_state, auto-refreshes every 5 min)."""
    # Auto-refresh after 5 minutes
    fetched_at = st.session_state.get("portfolio_fetched_at", 0)
    if "portfolio_data" in st.session_state and time.time() - fetched_at > 300:
        for k in ["portfolio_data", "portfolio_account", "portfolio_prices",
                   "net_liq_all", "yearly_transfers", "benchmark_returns"]:
            st.session_state.pop(k, None)
        for k in [k for k in st.session_state if k.startswith("net_liq_")]:
            st.session_state.pop(k, None)

    if "portfolio_data" not in st.session_state:
        _broker_name = "Interactive Brokers" if get_active_broker() == "ibkr" else "Tastytrade"
        with st.spinner(f"Fetching portfolio data from {_broker_name}..."):
            try:
                cost_basis, acct = fetch_portfolio_data()
                st.session_state.portfolio_data = cost_basis
                st.session_state.portfolio_account = acct
                st.session_state.portfolio_fetched_at = time.time()
            except Exception as e:
                if _is_auth_error(e):
                    logger.warning("Broker auth failed — clearing token so user can reconnect")
                    log_error("AUTH_ERROR", "Broker session expired", page="Portfolio", metadata={"broker": get_active_broker()})
                    st.session_state.pop("tt_refresh_token", None)
                    st.session_state.pop("portfolio_data", None)
                    st.error("Your broker session has expired. Please reconnect via **Account > Broker Connections**.")
                else:
                    logger.error("Portfolio fetch failed: %s", e, exc_info=True)
                    log_error_with_trace("PORTFOLIO_ERROR", e, page="Portfolio", metadata={"broker": get_active_broker()})
                    st.error(f"Failed to fetch portfolio data. Please try again. ({type(e).__name__})")
                st.stop()

    cost_basis = st.session_state.portfolio_data
    acct = st.session_state.get("portfolio_account", "")

    if not cost_basis:
        st.info("No transactions found.")
        st.stop()

    if "portfolio_prices" not in st.session_state:
        active_tickers = [
            t for t, d in cost_basis.items()
            if d["shares_held"] > 0 or _has_open_options(d)
        ]
        if active_tickers:
            with st.spinner("Fetching current prices..."):
                st.session_state.portfolio_prices = fetch_current_prices(active_tickers)
        else:
            st.session_state.portfolio_prices = {}

    prices = st.session_state.portfolio_prices

    for ticker, data in cost_basis.items():
        price_data = prices.get(ticker)
        shares = data["shares_held"]
        if price_data and shares > 0:
            price = price_data["price"]
            data["current_price"] = price
            data["previous_close"] = price_data.get("previousClose") or price
            data["market_value"] = price * shares
            data["total_pl_real"] = data["total_pl"] + data["market_value"]
        elif price_data:
            # Options-only position — store underlying price for reference
            data["current_price"] = price_data["price"]
            data["previous_close"] = price_data.get("previousClose") or price_data["price"]
            data["market_value"] = 0.0
            data["total_pl_real"] = data["total_pl"]
        else:
            data["current_price"] = 0.0
            data["previous_close"] = 0.0
            data["market_value"] = 0.0
            data["total_pl_real"] = data["total_pl"]

    return cost_basis


def _color_val(val):
    if isinstance(val, (int, float)):
        if val > 0:
            return f"color: {T['accent']}"
        elif val < 0:
            return f"color: {T['red']}"
    return ""


def _parse_option_symbol(symbol):
    """Extract strike, expiration, and type from OCC option symbol like MSFT  250321C00420000."""
    if not symbol:
        return None, None, None
    m = re.match(r'^(.+?)\s*(\d{6})([CP])(\d{8})$', symbol.strip())
    if not m:
        return None, None, None
    date_str, cp, strike_raw = m.group(2), m.group(3), m.group(4)
    strike = int(strike_raw) / 1000
    try:
        exp = datetime.strptime(date_str, "%y%m%d")
        return strike, exp.strftime("%d-%m-%Y"), cp
    except ValueError:
        return strike, None, cp


def _has_open_options(data):
    """Check if a ticker has any open option positions."""
    return bool(_find_open_options(data.get("trades", [])))


def _find_open_options(trades):
    """Find currently open option positions from a ticker's trade list.

    Returns list of dicts with keys: symbol, type (CSP/CC/Put/Call),
    strike, expiration, quantity, premium.
    """
    positions = {}  # keyed by symbol
    for t in trades:
        inst = t.get("instrument_type", "")
        if "Option" not in inst:
            continue
        symbol = t.get("symbol", "")
        action = t.get("action", "")
        label = t.get("label", "")
        qty = t["quantity"]
        net = t["net_value"]

        if symbol not in positions:
            positions[symbol] = {"qty": 0, "premium": 0.0, "label": "", "trades": []}

        pos = positions[symbol]
        if action == "Sell to Open":
            pos["qty"] += qty
            pos["premium"] += net
            pos["label"] = label  # CSP or CC
        elif action == "Buy to Open":
            pos["qty"] += qty
            pos["premium"] += net
            pos["label"] = label
        elif action in ("Buy to Close", "Sell to Close"):
            pos["qty"] -= qty
            pos["premium"] += net
        elif label in ("Expired", "Assignment"):
            pos["qty"] -= qty

    result = []
    for symbol, pos in positions.items():
        if pos["qty"] > 0:
            strike, exp, cp = _parse_option_symbol(symbol)
            opt_type = pos["label"] or ("Put" if cp == "P" else "Call" if cp == "C" else "Option")
            result.append({
                "symbol": symbol,
                "type": opt_type,
                "strike": strike,
                "expiration": exp,
                "cp": cp,
                "quantity": int(pos["qty"]),
                "premium": pos["premium"],
            })
    return result



# ══════════════════════════════════════════════════════
#  WATCHLIST PAGE — Track multiple DCF valuations
# ══════════════════════════════════════════════════════

GITHUB_REPO_URL = "https://github.com/lazytheta/stock-analysis"
CONTACT_EMAIL = "security@lazytheta.io"


def _global_exception_handler(exc_type, exc_value, exc_tb):
    """Log any unhandled exception to Supabase before Streamlit's default handler."""
    import traceback as _tb
    log_error(
        "UNHANDLED_ERROR",
        str(exc_value),
        page=st.session_state.get("nav_radio"),
        stack_trace="".join(_tb.format_exception(exc_type, exc_value, exc_tb)),
    )
    # Fall through to Streamlit's default handler
    _original_excepthook(exc_type, exc_value, exc_tb)


_original_excepthook = sys.excepthook
sys.excepthook = _global_exception_handler


# ── Monthly detail helpers ──

def _fmt_k(val):
    """Format dollar amount: $1,234 -> '$1.2K', $500 -> '$500'."""
    sign = "+" if val > 0 else "-" if val < 0 else ""
    av = abs(val)
    if av >= 1000:
        return f"{sign}${av / 1000:.1f}K"
    return f"{sign}${av:,.0f}"


def _aggregate_month_trades(cost_basis, year, month):
    """Aggregate trade data for a specific month from cost_basis.

    Returns dict with:
        premium_total, premium_trades, leaders_premium, leaders_pl, laggards_pl
    """
    from datetime import datetime

    ticker_data = defaultdict(lambda: {
        "cc": 0.0, "put": 0.0, "equity_pl": 0.0, "net_pl": 0.0,
        "premium": 0.0, "premium_trades": 0, "contracts": 0,
        "dte_sum": 0.0, "dte_count": 0, "collateral_sum": 0.0,
        "has_options": False, "has_equity": False,
    })

    for ticker, data in cost_basis.items():
        # ── First: compute realized equity P/L via average cost basis ──
        # Walk ALL equity trades chronologically to build running avg cost,
        # then capture realized P/L for sells in target month.
        eq_trades = sorted(
            [t for t in data.get("trades", []) if t.get("instrument_type") == "Equity"],
            key=lambda t: t["date"],
        )
        _running_shares = 0.0
        _running_cost = 0.0  # total cost of shares held (positive = money spent)
        _month_equity_pl = 0.0
        _had_equity_trade = False
        for t in eq_trades:
            nv = t.get("net_value", 0.0)
            qty = abs(t.get("quantity", 0.0))
            td = t["date"]
            if hasattr(td, "year"):
                t_year, t_month = td.year, td.month
            else:
                _dt = datetime.strptime(str(td)[:10], "%Y-%m-%d")
                t_year, t_month = _dt.year, _dt.month

            if nv < 0:
                # Buy: increase position and cost
                _running_shares += qty
                _running_cost += abs(nv)
            elif nv > 0 and _running_shares > 0:
                # Sell: compute realized P/L based on avg cost
                avg_cost = _running_cost / _running_shares if _running_shares > 0 else 0
                sell_qty = min(qty, _running_shares)
                realized = nv - (avg_cost * sell_qty)
                _running_shares -= sell_qty
                _running_cost -= avg_cost * sell_qty
                if _running_cost < 0:
                    _running_cost = 0
                # Only count if this sell is in the target month
                if t_year == year and t_month == month:
                    _month_equity_pl += realized
                    _had_equity_trade = True
            # Mark buys in target month too (for has_equity flag)
            if t_year == year and t_month == month and nv < 0:
                _had_equity_trade = True

        if _had_equity_trade:
            ticker_data[ticker]["equity_pl"] += _month_equity_pl
            ticker_data[ticker]["net_pl"] += _month_equity_pl
            ticker_data[ticker]["has_equity"] = True

        # ── Then: process non-equity trades in target month ──
        for t in data.get("trades", []):
            if t.get("instrument_type") == "Equity":
                continue  # already handled above
            td = t["date"]
            if hasattr(td, "year"):
                t_year, t_month = td.year, td.month
            else:
                dt = datetime.strptime(str(td)[:10], "%Y-%m-%d")
                t_year, t_month = dt.year, dt.month
            if t_year != year or t_month != month:
                continue

            label = t.get("label", "")
            nv = t.get("net_value", 0.0)
            td_obj = ticker_data[ticker]
            td_obj["net_pl"] += nv

            if label in ("CC", "BTC CC"):
                td_obj["cc"] += nv
            elif label in ("CSP", "BTC CSP"):
                td_obj["put"] += nv

            if "Option" in (t.get("instrument_type") or ""):
                td_obj["has_options"] = True

            if label in ("CSP", "CC", "BTC CSP", "BTC CC"):
                td_obj["premium"] += nv
                td_obj["premium_trades"] += 1
                if label in ("CSP", "CC"):
                    td_obj["contracts"] += abs(int(t.get("quantity", 0)))
                if label in ("CSP", "CC"):
                    strike, exp_str, cp = _parse_option_symbol(t.get("symbol"))
                    if exp_str and hasattr(td, "year"):
                        try:
                            exp_dt = datetime.strptime(exp_str, "%d-%m-%Y")
                            trade_dt = datetime(td.year, td.month, td.day) if hasattr(td, "day") else datetime.strptime(str(td)[:10], "%Y-%m-%d")
                            dte = (exp_dt - trade_dt).days
                            if dte > 0:
                                td_obj["dte_sum"] += dte
                                td_obj["dte_count"] += 1
                                qty = abs(int(t.get("quantity", 1))) or 1
                                if strike and strike > 0:
                                    td_obj["collateral_sum"] += strike * 100 * qty
                        except (ValueError, TypeError):
                            pass

    # ── Unrealized equity P/L for tickers with shares held and NO equity trades this month ──
    import calendar
    import ssl as _ssl
    import json as _json
    import urllib.request as _urllib

    # Tickers that had equity (stock buy/sell) trades this month — already have realized P/L
    _equity_traded = {t for t, d in ticker_data.items() if d["has_equity"]}

    tickers_with_shares = {}
    for ticker, data in cost_basis.items():
        current_shares = data.get("shares_held", 0)
        if current_shares > 0 and ticker not in _equity_traded:
            tickers_with_shares[ticker] = current_shares

    if tickers_with_shares:
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        for ticker, shares in tickers_with_shares.items():
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5y&interval=1mo"
                req = _urllib.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with _urllib.urlopen(req, context=ctx, timeout=10) as resp:
                    cdata = _json.loads(resp.read())
                result = cdata["chart"]["result"][0]
                timestamps = result["timestamp"]
                closes = result["indicators"]["quote"][0]["close"]
                month_prices = {}
                for ts, close in zip(timestamps, closes):
                    if close is None:
                        continue
                    dt = datetime.utcfromtimestamp(ts)
                    month_prices[(dt.year, dt.month)] = close
                prev_month = month - 1 if month > 1 else 12
                prev_year = year if month > 1 else year - 1
                price_start = month_prices.get((prev_year, prev_month))
                price_end = month_prices.get((year, month))
                if price_start and price_end:
                    unrealized = shares * (price_end - price_start)
                    if abs(unrealized) >= 1.0:
                        ticker_data[ticker]["equity_pl"] += unrealized
                        ticker_data[ticker]["net_pl"] += unrealized
                        ticker_data[ticker]["has_equity"] = True
            except Exception:
                pass

    premium_list = []
    for ticker, d in ticker_data.items():
        if d["premium"] <= 0:
            continue
        avg_dte = int(d["dte_sum"] / d["dte_count"]) if d["dte_count"] > 0 else 0
        est_roc = 0.0
        if d["collateral_sum"] > 0 and avg_dte > 0:
            est_roc = (d["premium"] / d["collateral_sum"]) * (365 / avg_dte) * 100
        premium_list.append({
            "ticker": ticker, "trades": d["premium_trades"],
            "contracts": d["contracts"], "avg_dte": avg_dte,
            "est_roc": round(est_roc, 1), "premiums": d["premium"],
        })
    premium_list.sort(key=lambda x: x["premiums"], reverse=True)

    # P/L list: only tickers where we hold shares or traded equity (not pure-option positions)
    pl_list = [{"ticker": t, "cc": d["cc"], "put": d["put"], "equity_pl": d["equity_pl"], "net_pl": d["net_pl"]}
               for t, d in ticker_data.items()
               if d["net_pl"] != 0 and d["has_equity"]]
    pl_list.sort(key=lambda x: x["net_pl"], reverse=True)

    total_premium = sum(d["premium"] for d in ticker_data.values())
    total_premium_trades = sum(d["premium_trades"] for d in ticker_data.values())

    return {
        "premium_total": total_premium,
        "premium_trades": total_premium_trades,
        "leaders_premium": premium_list[:5],
        "leaders_pl": [x for x in pl_list[:5] if x["net_pl"] > 0],
        "laggards_pl": [x for x in pl_list[-5:] if x["net_pl"] < 0],
    }


def _aggregate_week_trades(cost_basis, wk_start, wk_end):
    """Aggregate trade data for a specific week from cost_basis.

    Returns dict with same structure as _aggregate_month_trades.
    """
    from datetime import datetime

    ticker_data = defaultdict(lambda: {
        "cc": 0.0, "put": 0.0, "equity_pl": 0.0, "net_pl": 0.0,
        "premium": 0.0, "premium_trades": 0, "contracts": 0,
        "dte_sum": 0.0, "dte_count": 0, "collateral_sum": 0.0,
        "has_options": False, "has_equity": False,
    })

    wk_start_dt = wk_start if isinstance(wk_start, datetime) else datetime.combine(wk_start, datetime.min.time())
    wk_end_dt = wk_end if isinstance(wk_end, datetime) else datetime.combine(wk_end, datetime.max.time())
    # Normalize to date for comparison
    wk_start_d = wk_start_dt.date() if hasattr(wk_start_dt, 'date') else wk_start_dt
    wk_end_d = wk_end_dt.date() if hasattr(wk_end_dt, 'date') else wk_end_dt

    def _to_date(td):
        if hasattr(td, "date") and callable(td.date):
            return td.date()
        elif hasattr(td, "year"):
            return datetime(td.year, td.month, td.day).date()
        return datetime.strptime(str(td)[:10], "%Y-%m-%d").date()

    _traded_tickers = set()
    for ticker, data in cost_basis.items():
        # ── Realized equity P/L via average cost basis ──
        eq_trades = sorted(
            [t for t in data.get("trades", []) if t.get("instrument_type") == "Equity"],
            key=lambda t: t["date"],
        )
        _running_shares = 0.0
        _running_cost = 0.0
        _wk_equity_pl = 0.0
        _had_equity_trade = False
        for t in eq_trades:
            nv = t.get("net_value", 0.0)
            qty = abs(t.get("quantity", 0.0))
            t_date = _to_date(t["date"])
            if nv < 0:
                _running_shares += qty
                _running_cost += abs(nv)
            elif nv > 0 and _running_shares > 0:
                avg_cost = _running_cost / _running_shares if _running_shares > 0 else 0
                sell_qty = min(qty, _running_shares)
                realized = nv - (avg_cost * sell_qty)
                _running_shares -= sell_qty
                _running_cost -= avg_cost * sell_qty
                if _running_cost < 0:
                    _running_cost = 0
                if wk_start_d <= t_date <= wk_end_d:
                    _wk_equity_pl += realized
                    _had_equity_trade = True
            if wk_start_d <= t_date <= wk_end_d and nv < 0:
                _had_equity_trade = True

        if _had_equity_trade:
            ticker_data[ticker]["equity_pl"] += _wk_equity_pl
            ticker_data[ticker]["net_pl"] += _wk_equity_pl
            ticker_data[ticker]["has_equity"] = True
            _traded_tickers.add(ticker)

        # ── Non-equity trades in this week ──
        for t in data.get("trades", []):
            if t.get("instrument_type") == "Equity":
                continue
            t_date = _to_date(t["date"])
            if t_date < wk_start_d or t_date > wk_end_d:
                continue
            _traded_tickers.add(ticker)

            label = t.get("label", "")
            nv = t.get("net_value", 0.0)
            td_obj = ticker_data[ticker]
            td_obj["net_pl"] += nv

            if label in ("CC", "BTC CC"):
                td_obj["cc"] += nv
            elif label in ("CSP", "BTC CSP"):
                td_obj["put"] += nv

            if "Option" in (t.get("instrument_type") or ""):
                td_obj["has_options"] = True

            if label in ("CSP", "CC", "BTC CSP", "BTC CC"):
                td_obj["premium"] += nv
                td_obj["premium_trades"] += 1
                if label in ("CSP", "CC"):
                    td_obj["contracts"] += abs(int(t.get("quantity", 0)))
                if label in ("CSP", "CC"):
                    td = t["date"]
                    strike, exp_str, cp = _parse_option_symbol(t.get("symbol"))
                    if exp_str and hasattr(td, "year"):
                        try:
                            exp_dt = datetime.strptime(exp_str, "%d-%m-%Y")
                            trade_dt = datetime(td.year, td.month, td.day) if hasattr(td, "day") else datetime.strptime(str(td)[:10], "%Y-%m-%d")
                            dte = (exp_dt - trade_dt).days
                            if dte > 0:
                                td_obj["dte_sum"] += dte
                                td_obj["dte_count"] += 1
                                qty = abs(int(t.get("quantity", 1))) or 1
                                if strike and strike > 0:
                                    td_obj["collateral_sum"] += strike * 100 * qty
                        except (ValueError, TypeError):
                            pass

    # ── Unrealized equity P/L for tickers where we hold shares ──
    import ssl as _ssl
    import json as _json
    import urllib.request as _urllib

    # Only add unrealized for tickers with shares held and NO equity trades this week
    _equity_traded_wk = {t for t, d in ticker_data.items() if d["has_equity"]}

    tickers_with_shares = {}
    for ticker, data in cost_basis.items():
        current_shares = data.get("shares_held", 0)
        if current_shares > 0 and ticker not in _equity_traded_wk:
            tickers_with_shares[ticker] = current_shares

    if tickers_with_shares:
        _days_back = (datetime.now() - datetime(wk_start_d.year, wk_start_d.month, wk_start_d.day)).days + 14
        _range = "1mo" if _days_back < 25 else ("3mo" if _days_back < 80 else ("1y" if _days_back < 350 else "5y"))
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        for ticker, shares in tickers_with_shares.items():
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={_range}&interval=1d"
                req = _urllib.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with _urllib.urlopen(req, context=ctx, timeout=10) as resp:
                    cdata = _json.loads(resp.read())
                result = cdata["chart"]["result"][0]
                timestamps = result["timestamp"]
                closes = result["indicators"]["quote"][0]["close"]
                daily_prices = []
                for ts, close in zip(timestamps, closes):
                    if close is None:
                        continue
                    dt = datetime.utcfromtimestamp(ts).date()
                    daily_prices.append((dt, close))
                daily_prices.sort()
                price_before = None
                price_end = None
                for dt, close in daily_prices:
                    if dt < wk_start_d:
                        price_before = close
                    if dt <= wk_end_d:
                        price_end = close
                if price_before and price_end:
                    unrealized = shares * (price_end - price_before)
                    if abs(unrealized) >= 1.0:
                        ticker_data[ticker]["equity_pl"] += unrealized
                        ticker_data[ticker]["net_pl"] += unrealized
                        ticker_data[ticker]["has_equity"] = True
            except Exception:
                pass

    premium_list = []
    for ticker, d in ticker_data.items():
        if d["premium"] <= 0:
            continue
        avg_dte = int(d["dte_sum"] / d["dte_count"]) if d["dte_count"] > 0 else 0
        est_roc = 0.0
        if d["collateral_sum"] > 0 and avg_dte > 0:
            est_roc = (d["premium"] / d["collateral_sum"]) * (365 / avg_dte) * 100
        premium_list.append({
            "ticker": ticker, "trades": d["premium_trades"],
            "contracts": d["contracts"], "avg_dte": avg_dte,
            "est_roc": round(est_roc, 1), "premiums": d["premium"],
        })
    premium_list.sort(key=lambda x: x["premiums"], reverse=True)

    # P/L list: only tickers where we hold shares or traded equity (not pure-option positions)
    pl_list = [{"ticker": t, "cc": d["cc"], "put": d["put"], "equity_pl": d["equity_pl"], "net_pl": d["net_pl"]}
               for t, d in ticker_data.items()
               if d["net_pl"] != 0 and d["has_equity"]]
    pl_list.sort(key=lambda x: x["net_pl"], reverse=True)

    total_premium = sum(d["premium"] for d in ticker_data.values())
    total_premium_trades = sum(d["premium_trades"] for d in ticker_data.values())

    return {
        "premium_total": total_premium,
        "premium_trades": total_premium_trades,
        "leaders_premium": premium_list[:5],
        "leaders_pl": [x for x in pl_list[:5] if x["net_pl"] > 0],
        "laggards_pl": [x for x in pl_list[-5:] if x["net_pl"] < 0],
    }


@st.dialog("Weekly Detail", width="large")
def _show_week_detail(year, iso_wk, wk_start, wk_end, cost_basis, nl_all, transfers, weekly_returns, T):
    """Render weekly detail modal — same format as monthly report."""
    import pandas as pd
    import base64 as _b64
    import streamlit.components.v1 as components

    wk_label = f"W{iso_wk} · {wk_start.strftime('%b %d')}–{wk_end.strftime('%b %d, %Y')}"

    agg = _aggregate_week_trades(cost_basis, wk_start, wk_end)

    # Weekly return %
    _wk_key = (year, wk_start.month)
    wk_ret_pct = 0.0
    for _wiso, _wret, _ws, _we in weekly_returns.get(_wk_key, []):
        if _wiso == iso_wk:
            wk_ret_pct = _wret
            break

    # Net P/L from net_liq
    net_pl_dollar = 0.0
    _period_capital = 0.0
    if nl_all:
        df = pd.DataFrame(nl_all)
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time")
        # Normalize timezone: strip tz from df if wk_start is naive, or vice versa
        _wk_s = pd.Timestamp(wk_start)
        _wk_e = pd.Timestamp(wk_end) + pd.Timedelta(days=1)
        if df["time"].dt.tz is not None and _wk_s.tz is None:
            _wk_s = _wk_s.tz_localize(df["time"].dt.tz)
            _wk_e = _wk_e.tz_localize(df["time"].dt.tz)
        elif df["time"].dt.tz is None and _wk_s.tz is not None:
            _wk_s = _wk_s.tz_localize(None)
            _wk_e = _wk_e.tz_localize(None)
        wk_data = df[(df["time"] >= _wk_s) & (df["time"] <= _wk_e)]
        if not wk_data.empty:
            end_val = wk_data["close"].iloc[-1]
            prev = df[df["time"] < wk_data["time"].iloc[0]]
            start_val = prev["close"].iloc[-1] if not prev.empty else end_val
            _period_capital = start_val
            # Approximate deposits for this week
            _wk_yr, _wk_mo = wk_start.year, wk_start.month
            yr_tr = transfers.get(_wk_yr, {})
            mo_dep_total = yr_tr.get("months", {}).get(_wk_mo, 0) if isinstance(yr_tr, dict) else 0
            import calendar as _cal
            _days_in_mo = _cal.monthrange(_wk_yr, _wk_mo)[1]
            _wk_days = (wk_end - wk_start).days + 1
            wk_dep = mo_dep_total * (_wk_days / _days_in_mo) if _days_in_mo > 0 else 0
            net_pl_dollar = end_val - start_val - wk_dep

    # Premium ROC
    _prem_roc = (agg["premium_total"] / _period_capital * 100) if _period_capital > 0 else 0.0

    # ── Colors ──
    _green = T['accent']
    _red = T['red']
    _muted = T['text_muted']
    _card = T['card']
    _border = T['border']
    _text = T['text']
    _bg = T['bg']

    def _c(val):
        return _green if val >= 0 else _red

    # Premium table rows
    prem_rows = ""
    if agg["leaders_premium"]:
        for lp in agg["leaders_premium"]:
            dte_str = f'{lp["avg_dte"]}d' if lp["avg_dte"] > 0 else "\u2014"
            prem_rows += (
                f'<tr><td class="tk">{lp["ticker"]}</td><td>{lp["trades"]}</td>'
                f'<td>{lp["contracts"]}</td><td>{dte_str}</td>'
                f'<td style="color:{_c(lp["est_roc"])}">{lp["est_roc"]:.1f}%</td>'
                f'<td style="color:{_green}">{_fmt_k(lp["premiums"])}</td></tr>'
            )

    # P/L table rows
    def _pl_html(items):
        if not items:
            return f'<tr><td colspan="5" style="text-align:center;color:{_muted};padding:20px">\u2014</td></tr>'
        r = ""
        for it in items:
            r += (
                f'<tr><td class="tk">{it["ticker"]}</td>'
                f'<td style="color:{_c(it["cc"])}">{_fmt_k(it["cc"])}</td>'
                f'<td style="color:{_c(it["put"])}">{_fmt_k(it["put"])}</td>'
                f'<td style="color:{_c(it["equity_pl"])}">{_fmt_k(it["equity_pl"])}</td>'
                f'<td style="color:{_c(it["net_pl"])};font-weight:700">{_fmt_k(it["net_pl"])}</td></tr>'
            )
        return r

    w_rows = _pl_html(agg["leaders_pl"])
    l_rows = _pl_html(agg["laggards_pl"])

    # Logo
    with open("assets/logo_footer.png", "rb") as _lf:
        _logo_b64 = _b64.b64encode(_lf.read()).decode()

    has_premium = bool(agg["leaders_premium"])
    has_pl = bool(agg["leaders_pl"] or agg["laggards_pl"])

    premium_section = ""
    if has_premium:
        premium_section = f'''
        <div class="section">
            <div class="section-title">Winners — By Premium</div>
            <table>
                <tr><th class="left">Ticker</th><th>Trades</th><th>Contracts</th><th>Avg DTE</th><th>Ann. ROC</th><th>Net Premiums</th></tr>
                {prem_rows}
            </table>
        </div>'''

    pl_section = ""
    if has_pl:
        pl_section = f'''
        <div class="section">
            <div class="section-title">Winners &amp; Losers — By P/L</div>
            <div class="pl-grid">
                <div class="pl-half">
                    <div class="pl-label" style="color:{_green}">Winners</div>
                    <table>
                        <tr><th class="left">Ticker</th><th>CC</th><th>PUT</th><th>Pos P/L</th><th>Net P/L</th></tr>
                        {w_rows}
                    </table>
                </div>
                <div class="pl-divider"></div>
                <div class="pl-half">
                    <div class="pl-label" style="color:{_red}">Losers</div>
                    <table>
                        <tr><th class="left">Ticker</th><th>CC</th><th>PUT</th><th>Pos P/L</th><th>Net P/L</th></tr>
                        {l_rows}
                    </table>
                </div>
            </div>
        </div>'''

    _dl_name = f'lazytheta-W{iso_wk}-{year}'

    report_html = f'''<!DOCTYPE html>
<html><head>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, sans-serif; background:{_bg}; color:{_text}; }}
#report {{ padding: 32px; max-width: 800px; margin: 0 auto; }}

.header {{ text-align:center; padding-bottom:20px; border-bottom:1px solid {_border}; margin-bottom:24px; }}
.header h1 {{ font-size:1.5rem; font-weight:700; letter-spacing:-0.01em; margin-bottom:2px; }}
.header .sub {{ font-size:0.82rem; color:{_muted}; }}

.heroes {{ display:flex; gap:12px; margin-bottom:24px; }}
.hero {{ flex:1; background:{_card}; border-radius:12px; padding:20px; border:1px solid {_border}; border-top:3px solid {_green}; display:flex; flex-direction:column; }}
.hero-label {{ font-size:0.7rem; color:{_muted}; text-transform:uppercase; letter-spacing:0.06em; font-weight:600; margin-bottom:8px; }}
.hero-val {{ font-size:1.7rem; font-weight:700; line-height:1.15; }}
.hero-detail {{ font-size:0.8rem; color:{_muted}; margin-top:4px; }}

.section {{ background:{_card}; border-radius:12px; padding:20px; border:1px solid {_border}; border-top:3px solid {_green}; margin-bottom:16px; }}
.section-title {{ font-size:0.82rem; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; padding-bottom:10px; margin-bottom:14px; border-bottom:1px solid {_border}; }}

table {{ width:100%; border-collapse:collapse; font-size:0.8rem; }}
th {{ text-align:right; padding:8px 10px; color:{_muted}; font-weight:600; font-size:0.7rem; text-transform:uppercase; letter-spacing:0.04em; border-bottom:2px solid {_border}; }}
th.left {{ text-align:left; }}
td {{ padding:10px 10px; border-bottom:1px solid {_border}; text-align:right; }}
td.tk {{ text-align:left; font-weight:600; }}
tr:last-child td {{ border-bottom:none; }}

.pl-grid {{ display:flex; gap:0; }}
.pl-half {{ flex:1; }}
.pl-divider {{ width:1px; background:{_border}; margin:0 16px; }}
.pl-label {{ font-size:0.72rem; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:10px; }}

.footer {{ display:flex; align-items:center; justify-content:center; gap:10px; padding:20px 0 8px 0; border-top:1px solid {_border}; margin-top:8px; }}
.footer img {{ height:28px; opacity:0.7; }}
.footer span {{ font-size:0.8rem; color:{_muted}; letter-spacing:0.02em; }}

#dl-btn {{ background:{_green}; color:#fff; border:none; padding:12px 24px; border-radius:10px; cursor:pointer; font-size:0.85rem; font-weight:600; width:100%; margin-top:16px; letter-spacing:0.02em; }}
#dl-btn:hover {{ opacity:0.9; }}
</style></head><body>
<div id="report">
    <div class="header">
        <h1>Week {iso_wk}</h1>
        <div class="sub">{wk_start.strftime('%B %d')} – {wk_end.strftime('%B %d, %Y')} · Weekly Performance Report</div>
    </div>

    <div class="heroes">
        <div class="hero">
            <div class="hero-label">Net Premiums</div>
            <div class="hero-val" style="color:{_c(agg["premium_total"])}">{_fmt_k(agg["premium_total"])}</div>
            <div class="hero-detail"><span style="color:{_c(_prem_roc)};font-weight:600">{_prem_roc:+.1f}%</span> ROC</div>
        </div>
        <div class="hero">
            <div class="hero-label">Net P/L</div>
            <div class="hero-val" style="color:{_c(net_pl_dollar)}">{_fmt_k(net_pl_dollar)}</div>
            <div class="hero-detail"><span style="color:{_c(wk_ret_pct)};font-weight:600">{wk_ret_pct:+.1f}%</span> return</div>
        </div>
    </div>

    {premium_section}
    {pl_section}

    <div class="footer" id="logo-footer">
        <img src="data:image/png;base64,{_logo_b64}">
        <span>lazytheta.io</span>
    </div>

    <button id="dl-btn">Download as PNG</button>
</div>

<script>
document.getElementById('dl-btn').addEventListener('click', function() {{
    const btn = this;
    btn.textContent = 'Generating...';
    btn.style.opacity = '0.6';
    const report = document.getElementById('report');
    btn.style.display = 'none';

    html2canvas(report, {{
        backgroundColor: '{_bg}',
        scale: 2,
        useCORS: true,
        logging: false,
    }}).then(function(canvas) {{
        btn.style.display = 'block';
        btn.textContent = 'Download as PNG';
        btn.style.opacity = '1';
        const link = document.createElement('a');
        link.download = '{_dl_name}.png';
        link.href = canvas.toDataURL('image/png');
        link.click();
    }}).catch(function() {{
        btn.style.display = 'block';
        btn.textContent = 'Download as PNG';
        btn.style.opacity = '1';
    }});
}});
</script>
</body></html>'''

    _h = 350  # header + heroes
    if has_premium:
        _h += 60 + len(agg["leaders_premium"]) * 42
    if has_pl:
        _h += 80 + max(len(agg["leaders_pl"]), len(agg["laggards_pl"]), 1) * 42
    _h += 80

    components.html(report_html, height=_h, scrolling=True)



@st.dialog("Monthly Detail", width="large")
def _show_month_detail(year, month, cost_basis, nl_all, transfers, monthly_returns, T):
    """Render monthly detail modal — polished shareable report card."""
    import pandas as pd
    import base64 as _b64
    import streamlit.components.v1 as components

    MONTH_NAMES_FULL = ["", "January", "February", "March", "April", "May", "June",
                        "July", "August", "September", "October", "November", "December"]
    MONTH_NAMES_SHORT = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    month_label = f"{MONTH_NAMES_SHORT[month]} {year}"
    month_full = f"{MONTH_NAMES_FULL[month]} {year}"

    agg = _aggregate_month_trades(cost_basis, year, month)

    # Net P/L from net_liq
    mo_ret_pct = monthly_returns.get(year, {}).get(month, 0.0)
    net_pl_dollar = 0.0
    _period_capital = 0.0
    if nl_all:
        df = pd.DataFrame(nl_all)
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time")
        mo_data = df[(df["time"].dt.year == year) & (df["time"].dt.month == month)]
        if not mo_data.empty:
            end_val = mo_data["close"].iloc[-1]
            prev = df[df["time"] < mo_data["time"].iloc[0]]
            start_val = prev["close"].iloc[-1] if not prev.empty else end_val
            _period_capital = start_val
            yr_transfers = transfers.get(year, {})
            mo_dep = yr_transfers.get("months", {}).get(month, 0) if isinstance(yr_transfers, dict) else 0
            net_pl_dollar = end_val - start_val - mo_dep

    # Premium ROC
    _prem_roc = (agg["premium_total"] / _period_capital * 100) if _period_capital > 0 else 0.0

    # Benchmark monthly returns (cached)
    if "benchmark_monthly" not in st.session_state:
        try:
            st.session_state["benchmark_monthly"] = fetch_benchmark_monthly_returns()
        except Exception:
            st.session_state["benchmark_monthly"] = {}
    bench = st.session_state["benchmark_monthly"]

    # ── Color helpers ──
    _green = T['accent']
    _red = T['red']
    _muted = T['text_muted']
    _card = T['card']
    _border = T['border']
    _text = T['text']
    _bg = T['bg']

    def _c(val):
        return _green if val >= 0 else _red

    # ── Build entire report as one HTML string ──
    # Benchmark rows
    bench_rows_html = (
        f'<div class="bench-row">'
        f'<span>Portfolio</span><span style="color:{_c(mo_ret_pct)}">{mo_ret_pct:+.1f}%</span></div>'
    )
    for bname, bdata in bench.items():
        b_ret = bdata.get((year, month), 0.0)
        bench_rows_html += (
            f'<div class="bench-row">'
            f'<span>{bname}</span><span style="color:{_c(b_ret)}">{b_ret:+.1f}%</span></div>'
        )

    # Premium table rows
    prem_rows = ""
    if agg["leaders_premium"]:
        for lp in agg["leaders_premium"]:
            dte_str = f'{lp["avg_dte"]}d' if lp["avg_dte"] > 0 else "\u2014"
            prem_rows += (
                f'<tr><td class="tk">{lp["ticker"]}</td><td>{lp["trades"]}</td>'
                f'<td>{lp["contracts"]}</td><td>{dte_str}</td>'
                f'<td style="color:{_c(lp["est_roc"])}">{lp["est_roc"]:.1f}%</td>'
                f'<td style="color:{_green}">{_fmt_k(lp["premiums"])}</td></tr>'
            )

    # P/L table rows
    def _pl_html(items):
        if not items:
            return f'<tr><td colspan="5" style="text-align:center;color:{_muted};padding:20px">\u2014</td></tr>'
        r = ""
        for it in items:
            r += (
                f'<tr><td class="tk">{it["ticker"]}</td>'
                f'<td style="color:{_c(it["cc"])}">{_fmt_k(it["cc"])}</td>'
                f'<td style="color:{_c(it["put"])}">{_fmt_k(it["put"])}</td>'
                f'<td style="color:{_c(it["equity_pl"])}">{_fmt_k(it["equity_pl"])}</td>'
                f'<td style="color:{_c(it["net_pl"])};font-weight:700">{_fmt_k(it["net_pl"])}</td></tr>'
            )
        return r

    w_rows = _pl_html(agg["leaders_pl"])
    l_rows = _pl_html(agg["laggards_pl"])

    # Logo
    with open("assets/logo_footer.png", "rb") as _lf:
        _logo_b64 = _b64.b64encode(_lf.read()).decode()

    # Sections visibility
    has_premium = bool(agg["leaders_premium"])
    has_pl = bool(agg["leaders_pl"] or agg["laggards_pl"])

    premium_section = ""
    if has_premium:
        premium_section = f'''
        <div class="section">
            <div class="section-title">Winners — By Premium</div>
            <table>
                <tr><th class="left">Ticker</th><th>Trades</th><th>Contracts</th><th>Avg DTE</th><th>Ann. ROC</th><th>Net Premiums</th></tr>
                {prem_rows}
            </table>
        </div>'''

    pl_section = ""
    if has_pl:
        pl_section = f'''
        <div class="section">
            <div class="section-title">Winners &amp; Losers — By P/L</div>
            <div class="pl-grid">
                <div class="pl-half">
                    <div class="pl-label" style="color:{_green}">Winners</div>
                    <table>
                        <tr><th class="left">Ticker</th><th>CC</th><th>PUT</th><th>Pos P/L</th><th>Net P/L</th></tr>
                        {w_rows}
                    </table>
                </div>
                <div class="pl-divider"></div>
                <div class="pl-half">
                    <div class="pl-label" style="color:{_red}">Losers</div>
                    <table>
                        <tr><th class="left">Ticker</th><th>CC</th><th>PUT</th><th>Pos P/L</th><th>Net P/L</th></tr>
                        {l_rows}
                    </table>
                </div>
            </div>
        </div>'''

    report_html = f'''<!DOCTYPE html>
<html><head>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, sans-serif; background:{_bg}; color:{_text}; }}
#report {{ padding: 32px; max-width: 800px; margin: 0 auto; }}

.header {{ text-align:center; padding-bottom:20px; border-bottom:1px solid {_border}; margin-bottom:24px; }}
.header h1 {{ font-size:1.5rem; font-weight:700; letter-spacing:-0.01em; margin-bottom:2px; }}
.header .sub {{ font-size:0.82rem; color:{_muted}; }}

.heroes {{ display:flex; gap:12px; margin-bottom:24px; }}
.hero {{ flex:1; background:{_card}; border-radius:12px; padding:20px; border:1px solid {_border}; border-top:3px solid {_green}; display:flex; flex-direction:column; }}
.hero-label {{ font-size:0.7rem; color:{_muted}; text-transform:uppercase; letter-spacing:0.06em; font-weight:600; margin-bottom:8px; }}
.hero-val {{ font-size:1.7rem; font-weight:700; line-height:1.15; }}
.hero-detail {{ font-size:0.8rem; color:{_muted}; margin-top:4px; }}

.bench-row {{ display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid {_border}; font-size:0.82rem; }}
.bench-row:last-child {{ border-bottom:none; }}
.bench-row span:last-child {{ font-weight:600; }}

.section {{ background:{_card}; border-radius:12px; padding:20px; border:1px solid {_border}; border-top:3px solid {_green}; margin-bottom:16px; }}
.section-title {{ font-size:0.82rem; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; padding-bottom:10px; margin-bottom:14px; border-bottom:1px solid {_border}; }}

table {{ width:100%; border-collapse:collapse; font-size:0.8rem; }}
th {{ text-align:right; padding:8px 10px; color:{_muted}; font-weight:600; font-size:0.7rem; text-transform:uppercase; letter-spacing:0.04em; border-bottom:2px solid {_border}; }}
th.left {{ text-align:left; }}
td {{ padding:10px 10px; border-bottom:1px solid {_border}; text-align:right; }}
td.tk {{ text-align:left; font-weight:600; }}
tr:last-child td {{ border-bottom:none; }}

.pl-grid {{ display:flex; gap:0; }}
.pl-half {{ flex:1; }}
.pl-divider {{ width:1px; background:{_border}; margin:0 16px; }}
.pl-label {{ font-size:0.72rem; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:10px; }}

.footer {{ display:flex; align-items:center; justify-content:center; gap:10px; padding:20px 0 8px 0; border-top:1px solid {_border}; margin-top:8px; }}
.footer img {{ height:28px; opacity:0.7; }}
.footer span {{ font-size:0.8rem; color:{_muted}; letter-spacing:0.02em; }}

#dl-btn {{ background:{_green}; color:#fff; border:none; padding:12px 24px; border-radius:10px; cursor:pointer; font-size:0.85rem; font-weight:600; width:100%; margin-top:16px; letter-spacing:0.02em; }}
#dl-btn:hover {{ opacity:0.9; }}
</style></head><body>
<div id="report">
    <div class="header">
        <h1>{month_full}</h1>
        <div class="sub">Monthly Performance Report</div>
    </div>

    <div class="heroes">
        <div class="hero">
            <div class="hero-label">Net Premiums</div>
            <div class="hero-val" style="color:{_c(agg["premium_total"])}">{_fmt_k(agg["premium_total"])}</div>
            <div class="hero-detail"><span style="color:{_c(_prem_roc)};font-weight:600">{_prem_roc:+.1f}%</span> ROC</div>
        </div>
        <div class="hero">
            <div class="hero-label">Net P/L</div>
            <div class="hero-val" style="color:{_c(net_pl_dollar)}">{_fmt_k(net_pl_dollar)}</div>
            <div class="hero-detail"><span style="color:{_c(mo_ret_pct)};font-weight:600">{mo_ret_pct:+.1f}%</span> return</div>
        </div>
        <div class="hero">
            <div class="hero-label">Benchmark</div>
            <div style="margin-top:4px">{bench_rows_html}</div>
        </div>
    </div>

    {premium_section}
    {pl_section}

    <div class="footer" id="logo-footer">
        <img src="data:image/png;base64,{_logo_b64}">
        <span>lazytheta.io</span>
    </div>

    <button id="dl-btn">Download as PNG</button>
</div>

<script>
document.getElementById('dl-btn').addEventListener('click', function() {{
    const btn = this;
    btn.textContent = 'Generating...';
    btn.style.opacity = '0.6';
    const report = document.getElementById('report');
    btn.style.display = 'none';

    html2canvas(report, {{
        backgroundColor: '{_bg}',
        scale: 2,
        useCORS: true,
        logging: false,
    }}).then(function(canvas) {{
        btn.style.display = 'block';
        btn.textContent = 'Download as PNG';
        btn.style.opacity = '1';
        const link = document.createElement('a');
        link.download = 'lazytheta-{month_label.replace(" ", "-")}.png';
        link.href = canvas.toDataURL('image/png');
        link.click();
    }}).catch(function() {{
        btn.style.display = 'block';
        btn.textContent = 'Download as PNG';
        btn.style.opacity = '1';
    }});
}});
</script>
</body></html>'''

    # Calculate approximate height based on content
    _h = 400  # header + heroes
    if has_premium:
        _h += 60 + len(agg["leaders_premium"]) * 42
    if has_pl:
        _h += 80 + max(len(agg["leaders_pl"]), len(agg["laggards_pl"]), 1) * 42
    _h += 80  # button + padding

    components.html(report_html, height=_h, scrolling=True)


if page == "Watchlist":

    st.markdown(
        "<style>.block-container { max-width: 1400px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )

    # ── Route: editor or overview ──
    edit_ticker = st.query_params.get("edit")
    if edit_ticker:
        _dcf_editor(edit_ticker.upper())
    else:
        _watchlist_overview()


# ══════════════════════════════════════════════════════
#  PORTFOLIO PAGE — Active positions overview
# ══════════════════════════════════════════════════════

elif page == "Portfolio":

    if not has_active_broker():
        _render_welcome_page()
        st.stop()

    st.markdown(
        "<style>.block-container { max-width: 1200px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )
    st.markdown("")
    cost_basis = _load_portfolio_data()

    held = {
        t: d for t, d in cost_basis.items()
        if d["shares_held"] > 0 or _has_open_options(d)
    }

    if not held:
        st.info("No active positions.")
        st.stop()

    held_tickers = list(held.keys())

    # ── Margin / Buying Power (with integrated simulator) ──
    @st.cache_data(ttl=60, show_spinner=False)
    def _cached_account_balances():
        return fetch_account_balances()

    @st.cache_data(ttl=120, show_spinner=False)
    def _cached_margin_requirements():
        return fetch_margin_requirements()

    def _margin_overview():
        st.markdown("")
        try:
            bal = _cached_account_balances()
        except Exception as e:
            logger.warning("Account balances fetch failed: %s", e)
            bal = None

        if not bal:
            return

        net_liq = bal["net_liquidating_value"]
        maint_excess = bal["maintenance_excess"]
        bp = bal["derivative_buying_power"]
        used_bp = bal["used_derivative_buying_power"]
        total_bp = bp + used_bp

        # ── Compute assignment exposure from open short options ──
        total_assignment = 0.0
        total_assign_margin = 0.0
        assignment_entries = []
        portfolio = st.session_state.get("portfolio_data", {})
        prices_cache = st.session_state.get("portfolio_prices", {})

        for ticker, data in portfolio.items():
            opts = _find_open_options(data["trades"])
            for opt in opts:
                if opt["cp"] == "P":
                    shares = opt["quantity"] * 100
                    exposure = opt["strike"] * shares
                    # Fetch margin requirement for holding assigned shares
                    _amk = f"_assign_margin_{ticker.upper()}_{shares}"
                    if _amk not in st.session_state:
                        _amr = fetch_margin_for_position(ticker, shares)
                        st.session_state[_amk] = _amr
                    _amr = st.session_state[_amk]
                    margin = abs(_amr["change_in_margin"]) if _amr else exposure * 0.50
                    total_assignment += exposure
                    total_assign_margin += margin
                    _apct = margin / exposure * 100 if exposure > 0 else 0
                    assignment_entries.append(f'{opt["quantity"]}x {ticker} ${opt["strike"]:.0f}P = ${margin:,.0f} ({_apct:.0f}%)')
                elif opt["cp"] == "C" and opt["type"] != "CC":
                    # Naked short calls only — covered calls don't need extra margin
                    shares = opt["quantity"] * 100
                    cur_price = prices_cache.get(ticker, {}).get("price", 0) if prices_cache else 0
                    exposure = cur_price * shares
                    _amk = f"_assign_margin_{ticker.upper()}_{shares}"
                    if _amk not in st.session_state:
                        _amr = fetch_margin_for_position(ticker, shares)
                        st.session_state[_amk] = _amr
                    _amr = st.session_state[_amk]
                    margin = abs(_amr["change_in_margin"]) if _amr else exposure * 0.50
                    total_assignment += exposure
                    total_assign_margin += margin
                    _apct = margin / exposure * 100 if exposure > 0 else 0
                    assignment_entries.append(f'{opt["quantity"]}x {ticker} ${opt["strike"]:.0f}C = ${margin:,.0f} ({_apct:.0f}%)')

        # ── Compute simulation impact from session state ──
        if "sim_rows" not in st.session_state:
            st.session_state["sim_rows"] = 1

        total_sim_cost = 0.0
        total_sim_margin = 0.0
        sim_entries = []

        for i in range(st.session_state["sim_rows"]):
            ticker = sanitize_ticker(st.session_state.get(f"sim_tick_{i}", "") or "")
            try:
                shares = int(st.session_state.get(f"sim_sh_{i}", "100"))
            except (ValueError, TypeError):
                shares = 0
            try:
                price = float(st.session_state.get(f"sim_pr_{i}", "0"))
            except (ValueError, TypeError):
                price = 0.0
            if ticker and price > 0 and shares > 0:
                cost = price * shares
                _margin_key = f"_sim_margin_{ticker.upper()}_{shares}"
                if _margin_key not in st.session_state:
                    _mr = fetch_margin_for_position(ticker, shares)
                    st.session_state[_margin_key] = _mr
                _mr = st.session_state[_margin_key]
                margin = abs(_mr["change_in_margin"]) if _mr else cost * 0.50
                total_sim_cost += cost
                total_sim_margin += margin
                _pct = margin / cost * 100 if cost > 0 else 0
                sim_entries.append(f'{shares}x {ticker.upper()} @ ${price:,.2f} ({_pct:.0f}%)')

        # ── Compute final values (base + simulation + assignment) ──
        show_used = used_bp + total_sim_margin + total_assign_margin
        show_bp = bp - total_sim_margin - total_assign_margin
        show_excess = maint_excess - total_sim_margin - total_assign_margin
        show_usage = (show_used / total_bp * 100) if total_bp > 0 else 0
        show_drop = (show_excess / net_liq * 100) if net_liq > 0 else 0

        # Margin call line: point on bar where maintenance excess = 0
        margin_call_pct = ((show_used + show_excess) / total_bp * 100) if total_bp > 0 else 100

        if show_usage < 50:
            bar_color = T['accent']
            status = "Cash"
        elif show_usage < 75:
            bar_color = "#f2cc8f"
            status = "Margin"
        else:
            bar_color = T['red']
            status = "High Leverage"

        # Simulation subtitle
        sim_note = ""
        if total_sim_cost > 0:
            sim_label = " + ".join(sim_entries)
            sim_note = (
                f'<div style="margin-bottom:12px;padding:8px 12px;background:{T["info_bg"]};border-radius:8px;'
                f'border:1px dashed {T["border_medium"]};font-size:0.85rem">'
                f'<span style="color:{T["text_muted"]}">Simulating: </span>'
                f'<b>{sim_label}</b>'
                f'<span style="color:{T["text_muted"]}"> = ${total_sim_cost:,.0f} — margin ${total_sim_margin:,.0f}</span>'
                f'</div>'
            )

        # Assignment risk info block
        assign_note = ""
        if total_assignment > 0:
            assign_label = " | ".join(assignment_entries)
            assign_note = (
                f'<div style="margin-bottom:12px;padding:8px 12px;background:{T["info_bg"]};border-radius:8px;'
                f'border:1px dashed {T["border_medium"]};font-size:0.85rem">'
                f'<span style="color:{T["text_muted"]}">Assignment Risk: </span>'
                f'<b style="color:{T["text"]}">{assign_label}</b>'
                f'<span style="color:{T["text_muted"]}"> — margin ${total_assign_margin:,.0f}</span>'
                f'</div>'
            )

        st.markdown(
            f'<div class="hero-card">'
            f'<h4>Margin Overview</h4>'
            f'{assign_note}'
            f'{sim_note}'
            f'<div style="margin:16px 0">'
            f'  <div style="display:flex;justify-content:space-between;margin-bottom:6px">'
            f'    <span style="font-size:0.85rem;color:{T["text_muted"]}">BP Used: ${show_used:,.0f} / ${total_bp:,.0f}</span>'
            f'    <span style="font-size:0.85rem;font-weight:600;color:{bar_color}">{status} ({show_usage:.0f}%) · <span style="color:{T["red"]}">MC at {margin_call_pct:.0f}%</span></span>'
            f'  </div>'
            f'  <div style="position:relative;height:28px">'
            f'    <div style="position:absolute;top:8px;left:0;right:0;background:{T["grid"]};border-radius:8px;height:12px;overflow:hidden">'
            f'      <div style="background:{bar_color};width:{min(show_usage, 100):.0f}%;height:100%;border-radius:8px;'
            f'           transition:width 0.3s ease"></div>'
            f'    </div>'
            f'    <div style="position:absolute;left:50%;top:0;height:28px;display:flex;flex-direction:column;align-items:center;transform:translateX(-50%)">'
            f'      <div style="width:2px;height:28px;background:#f2cc8f"></div>'
            f'    </div>'
            f'    <div style="position:absolute;left:75%;top:0;height:28px;display:flex;flex-direction:column;align-items:center;transform:translateX(-50%)">'
            f'      <div style="width:2px;height:28px;background:{T["red"]}"></div>'
            f'    </div>'
            f'  </div>'
            f'  <div style="position:relative;height:16px;font-size:0.7rem;color:{T["text_muted"]}">'
            f'    <span style="position:absolute;left:50%;transform:translateX(-50%)">50%</span>'
            f'    <span style="position:absolute;left:75%;transform:translateX(-50%)">75%</span>'
            f'  </div>'
            f'</div>'
            f'<div class="stat-row">'
            f'<span class="stat-pill">Buying Power <b>${show_bp:,.0f}</b></span>'
            f'<span class="stat-pill">BP in Use <b>${show_used:,.0f}</b></span>'
            f'<span class="stat-pill">Buffer <b>${show_excess:,.0f}</b></span>'
            f'<span class="stat-pill">Margin Call at <b>-{show_drop:.0f}%</b></span>'
            + (f'<span class="stat-pill">Assignment Margin <b>${total_assign_margin:,.0f}</b></span>' if total_assign_margin > 0 else '') +
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Simulator inputs (below the overview bar) ──
        st.markdown(f'<p style="font-weight:600;margin-top:24px;margin-bottom:4px;font-size:0.9rem;color:{T["text_muted"]};text-transform:uppercase;letter-spacing:0.03em">Simulate Positions</p>', unsafe_allow_html=True)

        for i in range(st.session_state["sim_rows"]):
            c1, c2, c3 = st.columns([1.2, 0.8, 0.8], gap="small")
            with c1:
                ticker = st.text_input("Ticker", placeholder="AAPL", key=f"sim_tick_{i}", label_visibility="collapsed")
            with c2:
                shares = st.text_input("Shares", value="100", placeholder="100", key=f"sim_sh_{i}", label_visibility="collapsed")
            # Auto-fetch price when ticker is entered and price not yet set
            price_key = f"sim_pr_{i}"
            _sim_ticker_clean = sanitize_ticker(ticker) if ticker else None
            if _sim_ticker_clean and st.session_state.get(price_key, "0") in ("0", "0.00", "") and rate_limited_lookup():
                try:
                    _sp = fetch_current_prices([_sim_ticker_clean])
                    _spd = _sp.get(_sim_ticker_clean)
                    if _spd and _spd["price"]:
                        st.session_state[price_key] = f"{float(_spd['price']):.2f}"
                except Exception as e:
                    logger.debug("Sim price fetch failed for %s: %s", ticker, e)
            with c3:
                price = st.text_input("Price", value="0.00", key=price_key, label_visibility="collapsed")

        _, btn_add, btn_reset, _ = st.columns([1, 1, 1, 1])
        with btn_add:
            if st.button("Add row", key="sim_add_row", type="primary", use_container_width=True):
                st.session_state["sim_rows"] += 1
                st.rerun()
        with btn_reset:
            if st.button("Reset", key="sim_reset", type="primary", use_container_width=True):
                for i in range(st.session_state["sim_rows"]):
                    st.session_state.pop(f"sim_tick_{i}", None)
                    st.session_state.pop(f"sim_sh_{i}", None)
                    st.session_state.pop(f"sim_pr_{i}", None)
                st.session_state["sim_rows"] = 1
                st.rerun()

        # Store balance for other cards
        st.session_state["_margin_cash"] = bal["cash_balance"]
        st.session_state["_net_liq"] = bal["net_liquidating_value"]

    @st.fragment(run_every=timedelta(seconds=30))
    def _portfolio_cards():
        # Fetch fresh prices + account balances
        prices = fetch_current_prices(held_tickers)
        try:
            balances = fetch_account_balances()
        except Exception as e:
            logger.warning("Account balances fetch failed: %s", e)
            balances = None

        for ticker, data in held.items():
            price_data = prices.get(ticker)
            shares = data["shares_held"]
            if price_data and shares > 0:
                p = price_data["price"]
                data["current_price"] = p
                data["previous_close"] = price_data.get("previousClose") or p
                data["market_value"] = p * shares
            elif price_data:
                data["current_price"] = price_data["price"]
                data["previous_close"] = price_data.get("previousClose") or price_data["price"]

        # ── Hero card ──
        if balances:
            net_liq = balances["net_liquidating_value"]
            cash = balances["cash_balance"]
        else:
            net_liq = sum(d["market_value"] for d in held.values())
            cash = 0.0

        total_value = sum(d["market_value"] for d in held.values())
        total_prev = sum(d.get("previous_close", 0) * d["shares_held"] for d in held.values())
        day_chg_pct = ((total_value - total_prev) / total_prev * 100) if total_prev else 0.0
        day_chg_cls = "hero-green" if day_chg_pct >= 0 else "hero-red"
        day_chg_sign = "+" if day_chg_pct >= 0 else ""

        day_chg_dollar = total_value - total_prev
        day_dollar_sign = "+" if day_chg_dollar >= 0 else ""
        nlv_cls = "hero-green" if net_liq >= 0 else "hero-red"

        st.markdown(
            f'<div class="hero-card">'
            f'<p class="hero-label">Net Liquidating Value</p>'
            f'<p class="hero-value {nlv_cls}">${net_liq:,.0f}</p>'
            f'<p class="hero-sub"><span class="{day_chg_cls}">{day_chg_sign}{day_chg_pct:.2f}% ({day_dollar_sign}${abs(day_chg_dollar):,.0f})</span> today &nbsp;·&nbsp; {len(held)} active positions</p>'
            f'<div class="stat-row">'
            f'<span class="stat-pill">Cash <b>${cash:,.0f}</b></span>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Column picker & Sort ──
        all_cols = ["Shares", "Cost Basis", "Wheel Basis", "Break-even", "Current Price",
                    "Day %", "Mkt Value", "Unrealized P/L", "Return %", "Ann. %",
                    "Premie", "Days", "Weight", "Margin", "Margin %"]
        default_cols = ["Shares", "Cost Basis", "Wheel Basis", "Current Price", "Day %",
                        "Mkt Value", "Unrealized P/L", "Return %", "Weight"]
        sort_options = ["Ticker", "Weight", "Day %", "Return %", "Unrealized P/L", "Mkt Value", "Ann. %", "Margin %"]

        with st.container(key="toolbar_inline"):
            col_left, col_right = st.columns(2)
            with col_left:
                with st.popover("\u2699 Columns"):
                    selected = st.pills(
                        "Toggle columns",
                        all_cols,
                        default=default_cols,
                        selection_mode="multi",
                        label_visibility="collapsed",
                    )
            with col_right:
                with st.popover("\u2195 Sort"):
                    sort_by = st.pills(
                        "Sort by",
                        sort_options,
                        default="Ticker",
                        label_visibility="collapsed",
                    )

        # ── Build rows ──
        rows = []
        for ticker, data in held.items():
            wheels = data.get("wheels", [])
            last_wheel = wheels[-1] if wheels else None

            purchase_price = 0.0
            wheel_equity_cost = 0.0
            wheel_option_pl = 0.0
            wheel_shares = 0
            total_buy_price = 0.0

            if last_wheel:
                for t in last_wheel["trades"]:
                    if t["instrument_type"] == "Equity":
                        wheel_equity_cost += t["net_value"]
                        action = t.get("action", "")
                        txn_type = t.get("type", "")
                        qty = t["quantity"]
                        price = t["price"] if t["price"] else abs(t["net_value"]) / qty if qty else 0.0
                        if txn_type == "Receive Deliver":
                            if t["net_value"] < 0:
                                wheel_shares += qty
                                total_buy_price += price * qty
                        elif "Buy" in action:
                            wheel_shares += qty
                            total_buy_price += price * qty
                    elif "Option" in t["instrument_type"]:
                        wheel_option_pl += t["net_value"]

                if wheel_shares > 0:
                    purchase_price = total_buy_price / wheel_shares
                shares = data["shares_held"]
                wheel_cps = (wheel_equity_cost + wheel_option_pl) / shares if shares else 0.0
            else:
                wheel_cps = data["cost_per_share"]

            unrealized = data["market_value"] + wheel_equity_cost if last_wheel else data["market_value"] + data["equity_cost"]
            days_held = (date.today() - last_wheel["start"]).days if last_wheel else 0

            prev = data.get("previous_close", 0)
            cur = data["current_price"]
            day_change_pct = ((cur - prev) / prev * 100) if prev else 0.0

            initial_investment = abs(wheel_equity_cost) if last_wheel else abs(data["equity_cost"])
            return_pct = (unrealized / initial_investment * 100) if initial_investment else 0.0
            if days_held > 0 and initial_investment:
                ann_return = ((1 + unrealized / initial_investment) ** (365 / days_held) - 1) * 100
            else:
                ann_return = 0.0

            shares = data["shares_held"]
            break_even = abs(wheel_cps) if last_wheel and shares else purchase_price

            rows.append({
                "Logo": f"https://assets.parqet.com/logos/symbol/{ticker}",
                "Ticker": ticker,
                "Shares": shares,
                "Cost Basis": purchase_price,
                "Wheel Basis": wheel_cps,
                "Break-even": break_even,
                "Current Price": cur,
                "Day %": day_change_pct,
                "Mkt Value": data["market_value"],
                "Unrealized P/L": unrealized,
                "Return %": return_pct,
                "Ann. %": ann_return,
                "Premie": wheel_option_pl if last_wheel else 0.0,
                "Days": days_held,
            })

        # ── Per-position margin requirements ──
        try:
            _margin_reqs = _cached_margin_requirements()
        except Exception as e:
            logger.debug("Margin requirements fetch failed: %s", e)
            _margin_reqs = {}

        for row in rows:
            row["Weight"] = row["Mkt Value"] / total_value * 100 if total_value else 0.0
            _mr = _margin_reqs.get(row["Ticker"], {})
            row["Margin"] = _mr.get("margin_requirement", 0)
            _mv = row["Mkt Value"]
            row["Margin %"] = (row["Margin"] / _mv * 100) if _mv > 0 else 0

        # ── Sort rows ──
        if sort_by == "Ticker":
            rows.sort(key=lambda r: r["Ticker"])
        else:
            rows.sort(key=lambda r: r.get(sort_by, 0), reverse=True)

        # ── Format helpers ──
        color_cols_set = {"Unrealized P/L", "Day %", "Return %", "Ann. %"}

        def _fmt_cell(col, val):
            cls = ""
            if col in color_cols_set:
                cls = " pf-green" if val > 0 else " pf-red" if val < 0 else ""
            if col in ("Cost Basis", "Wheel Basis", "Break-even", "Current Price"):
                return f"${val:,.2f}", cls
            if col == "Mkt Value":
                return f"${val:,.0f}", cls
            if col == "Unrealized P/L":
                return f"${val:+,.0f}", cls
            if col == "Premie":
                return f"${val:,.0f}", cls
            if col in ("Day %", "Return %", "Ann. %"):
                return f"{val:+.2f}%", cls
            if col == "Weight":
                return f"{val:.1f}%", cls
            if col == "Margin":
                return f"${val:,.0f}", cls
            if col == "Margin %":
                return f"{val:.0f}%", cls
            if col == "Shares":
                return f"{int(val)}", cls
            if col == "Days":
                return f"{int(val)}", cls
            return f"{val}", cls

        # ── Detect open options per ticker ──
        opts_by_ticker = {}
        for ticker, data in held.items():
            open_opts = _find_open_options(data.get("trades", []))
            if open_opts:
                opts_by_ticker[ticker] = open_opts

        # ── Render cards ──
        cards_html = '<div class="portfolio-cards">'
        for row in rows:
            cells = ""
            for col in selected:
                fval, cls = _fmt_cell(col, row[col])
                cells += (
                    f'<div class="pf-cell">'
                    f'<span class="pf-label">{col}</span>'
                    f'<span class="pf-val{cls}">{fval}</span>'
                    f'</div>'
                )
            card_inner = (
                f'<div class="portfolio-card">'
                f'<img class="pf-logo" src="{row["Logo"]}" onerror="this.style.display=\'none\'">'
                f'<span class="pf-ticker">{row["Ticker"]}</span>'
                f'{cells}'
                f'</div>'
            )

            ticker = row["Ticker"]
            open_opts = opts_by_ticker.get(ticker)

            if open_opts:
                # Build option sub-cards
                opt_cards = ''
                for opt in open_opts:
                    strike_str = f"${opt['strike']:,.2f}" if opt["strike"] else "\u2014"
                    exp_str = opt["expiration"] or "\u2014"
                    prem_cls = " pf-green" if opt["premium"] > 0 else " pf-red" if opt["premium"] < 0 else ""
                    opt_cards += (
                        f'<div class="portfolio-card" style="border-style:dashed;margin-top:6px">'
                        f'<span class="pf-ticker" style="min-width:40px">{opt["type"]}</span>'
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Strike</span>'
                        f'<span class="pf-val">{strike_str}</span>'
                        f'</div>'
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Expiration</span>'
                        f'<span class="pf-val">{exp_str}</span>'
                        f'</div>'
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Qty</span>'
                        f'<span class="pf-val">{opt["quantity"]}</span>'
                        f'</div>'
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Premium</span>'
                        f'<span class="pf-val{prem_cls}">${opt["premium"]:+,.0f}</span>'
                        f'</div>'
                        f'</div>'
                    )
                cards_html += (
                    f'<details class="pf-details">'
                    f'<summary>{card_inner}</summary>'
                    f'{opt_cards}'
                    f'</details>'
                )
            else:
                cards_html += card_inner

        cards_html += '</div>'
        st.markdown(cards_html, unsafe_allow_html=True)

    _portfolio_cards()

    # ── Quick-Links — jump to Option Finder for held tickers ──
    _ql_items = []
    _ql_map = {}
    for _ql_t in held_tickers:
        _pf_data = cost_basis.get(_ql_t, {})
        _put_label = f"{_ql_t} Sell Put"
        _ql_items.append(_put_label)
        _ql_map[_put_label] = (_ql_t, "Sell Put")
        if _pf_data.get("shares_held", 0) > 0:
            _call_label = f"{_ql_t} Write Call"
            _ql_items.append(_call_label)
            _ql_map[_call_label] = (_ql_t, "Write Call")
    if _ql_items:
        if st.session_state.pop("_reset_ql_pills", False):
            st.session_state["ql_pills"] = None
        st.markdown(
            '<style>'
            '.st-key-ql_pills { display:flex; justify-content:center }'
            '.st-key-ql_pills [role="radiogroup"] { justify-content:center }'
            '.st-key-ql_pills button,'
            '.st-key-ql_pills button * {'
            '  border-radius:20px !important;'
            '  color:var(--text) !important; background:transparent !important;'
            '  font-weight:400 !important; transition:all 0.15s'
            '}'
            '.st-key-ql_pills button { border:1px solid var(--border-medium) !important }'
            '.st-key-ql_pills button:hover,'
            '.st-key-ql_pills button:hover *,'
            '.st-key-ql_pills button[aria-selected="true"],'
            '.st-key-ql_pills button[aria-selected="true"] * {'
            '  background:var(--accent) !important; color:#fff !important'
            '}'
            '</style>',
            unsafe_allow_html=True,
        )
        _ql_pick = st.pills("Find options", _ql_items, default=None,
                            key="ql_pills", label_visibility="collapsed")
        if _ql_pick and _ql_pick in _ql_map:
            _ql_t, _ql_strat = _ql_map[_ql_pick]
            st.session_state["of_ticker_input"] = _ql_t
            st.session_state["of_strategy"] = _ql_strat
            st.session_state["_pending_nav"] = "Option Finder"
            st.session_state["_reset_ql_pills"] = True
            st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)
    with st.container(key="margin_block"):
        _margin_overview()

    # ── Portfolio Greeks, BWD & Margin Interest ──
    gk = None
    bwd = None
    mi = None
    try:
        from concurrent.futures import ThreadPoolExecutor
        # Combined greeks+BWD uses one DXLink streamer (avoids concurrent
        # websocket conflicts); margin interest runs in parallel (no streamer).
        executor = ThreadPoolExecutor(max_workers=2)
        f_combo = executor.submit(fetch_greeks_and_bwd)
        f_mi = executor.submit(fetch_margin_interest)
        try:
            gk, bwd = f_combo.result(timeout=30)
        except Exception as e:
            if not _is_auth_error(e):
                logger.warning("Greeks/BWD fetch failed: %s", e)
                log_error_with_trace("PORTFOLIO_ERROR", e, page="Portfolio", metadata={"component": "greeks_bwd"})
            else:
                logger.debug("Greeks/BWD skipped — broker auth expired")
            gk, bwd = None, None
        try:
            mi = f_mi.result(timeout=10)
        except Exception as e:
            if not _is_auth_error(e):
                logger.debug("Margin interest fetch failed: %s", e)
                log_error("PORTFOLIO_ERROR", str(e), page="Portfolio", metadata={"component": "margin_interest"})
            mi = None
        executor.shutdown(wait=False, cancel_futures=True)
    except Exception as e:
        if not _is_auth_error(e):
            logger.warning("Risk dashboard data fetch failed: %s", e)
            log_error_with_trace("PORTFOLIO_ERROR", e, page="Portfolio", metadata={"component": "risk_dashboard"})

    cash = st.session_state.get("_margin_cash", 0.0)
    debt = abs(cash) if cash < 0 else 0.0
    has_greeks = gk and gk["positions"]
    has_bwd = bwd and bwd["spy_price"] > 0
    has_interest = debt > 0 or (mi and mi["total"] < 0)
    _broker_active = has_active_broker()
    greeks_unavailable = not has_greeks and _broker_active
    bwd_unavailable = not has_bwd and _broker_active

    _cards = []
    if has_greeks or greeks_unavailable:
        _cards.append("greeks")
    if has_bwd or bwd_unavailable:
        _cards.append("bwd")
    if has_interest:
        _cards.append("interest")

    if _cards:
        # Build card HTML fragments
        _card_htmls = []

        if has_greeks:
            tot = gk["totals"]
            theta = tot["theta"]
            delta = tot["delta"]
            vega = tot["vega"]
            theta_color = T['accent'] if theta >= 0 else T['red']
            _card_htmls.append(
                f'<div class="hero-card">'
                f'<h4>Portfolio Greeks</h4>'
                f'<div style="text-align:center;margin-bottom:16px">'
                f'  <span style="font-size:1.6rem;font-weight:700;color:{theta_color}">${theta:,.0f}</span>'
                f'  <span style="font-size:0.85rem;color:{T["text_muted"]}">theta / day</span>'
                f'</div>'
                f'<div class="stat-row">'
                f'<span class="stat-pill">Delta <b>{delta:,.0f}</b>'
                f'  <span style="font-size:0.7rem;color:{T["text_muted"]}">$ per $1 move</span></span>'
                f'<span class="stat-pill">Vega <b>${vega:,.0f}</b>'
                f'  <span style="font-size:0.7rem;color:{T["text_muted"]}">per 1%% IV</span></span>'
                f'</div>'
                f'</div>'
            )
        elif greeks_unavailable:
            _card_htmls.append(
                f'<div class="hero-card">'
                f'<h4>Portfolio Greeks</h4>'
                f'<div style="text-align:center;margin-bottom:16px">'
                f'  <span style="font-size:1.1rem;color:{T["text_muted"]}">Market closed</span>'
                f'</div>'
                f'<div style="text-align:center;font-size:0.83rem;color:{T["text_muted"]}">'
                f'Live Greeks available during US market hours<br>(9:30 AM \u2013 4:00 PM ET)</div>'
                f'</div>'
            )

        if has_bwd:
            _bwd_total = bwd["portfolio_bwd"]
            _spy_p = bwd["spy_price"]
            _dollar_1pct = bwd["dollar_per_1pct"]
            _nlv = st.session_state.get("_net_liq", 0)
            _port_pct = (_dollar_1pct / _nlv * 100) if _nlv > 0 else 0
            _pct_color = T['red'] if _port_pct > 0 else T['accent']

            _td = f'padding:4px 8px;border-bottom:1px solid {T["divider"]}'
            _bwd_rows = ""
            for bp in bwd["positions"]:
                _bp_loss = -bp["dollar_per_1pct"]
                _bp_color = T['red'] if _bp_loss < 0 else T['accent']
                _bwd_rows += (
                    f'<tr>'
                    f'<td style="{_td}">{bp["ticker"]}</td>'
                    f'<td style="{_td};text-align:right">{bp["beta"]:.2f}</td>'
                    f'<td style="{_td};text-align:right">{bp["bwd"]:+,.1f}</td>'
                    f'<td style="{_td};text-align:right;color:{_bp_color}">${_bp_loss:+,.0f}</td>'
                    f'</tr>'
                )
            _card_htmls.append(
                f'<div class="hero-card">'
                f'<h4>Beta-Weighted Delta</h4>'
                f'<div style="text-align:center;margin-bottom:16px">'
                f'  <span style="font-size:1.6rem;font-weight:700;color:{_pct_color}">-{_port_pct:.2f}%</span>'
                f'  <span style="font-size:0.85rem;color:{T["text_muted"]}">if S&P 500 drops 1%</span>'
                f'</div>'
                f'<div class="stat-row">'
                f'<span class="stat-pill">P/L <b style="color:{_pct_color}">-${abs(_dollar_1pct):,.0f}</b></span>'
                f'<span class="stat-pill">BWD <b>{_bwd_total:+,.1f}</b></span>'
                f'<span class="stat-pill">SPY <b>${_spy_p:,.0f}</b></span>'
                f'</div>'
                f'<details style="margin-top:8px">'
                f'<summary style="cursor:pointer;font-size:0.8rem;color:{T["text_muted"]}">Breakdown</summary>'
                f'<table style="width:100%;border-collapse:collapse;font-size:0.8rem;margin-top:6px">'
                f'<thead><tr style="color:{T["text_muted"]};font-size:0.7rem;text-transform:uppercase">'
                f'<th style="text-align:left;padding:3px 8px;border-bottom:1px solid {T["border_medium"]}">Ticker</th>'
                f'<th style="text-align:right;padding:3px 8px;border-bottom:1px solid {T["border_medium"]}">Beta</th>'
                f'<th style="text-align:right;padding:3px 8px;border-bottom:1px solid {T["border_medium"]}">BWD</th>'
                f'<th style="text-align:right;padding:3px 8px;border-bottom:1px solid {T["border_medium"]}">P/L</th>'
                f'</tr></thead>'
                f'<tbody>{_bwd_rows}</tbody>'
                f'</table>'
                f'</details>'
                f'</div>'
            )
        elif bwd_unavailable:
            _card_htmls.append(
                f'<div class="hero-card">'
                f'<h4>Beta-Weighted Delta</h4>'
                f'<div style="text-align:center;margin-bottom:16px">'
                f'  <span style="font-size:1.1rem;color:{T["text_muted"]}">Market closed</span>'
                f'</div>'
                f'<div style="text-align:center;font-size:0.83rem;color:{T["text_muted"]}">'
                f'Live BWD available during US market hours<br>(9:30 AM \u2013 4:00 PM ET)</div>'
                f'</div>'
            )

        if has_interest:
            cur_mo = abs(mi["current_month"]) if mi else 0
            ytd = abs(mi["ytd"]) if mi else 0
            total_int = abs(mi["total"]) if mi else 0
            _card_htmls.append(
                f'<div class="hero-card">'
                f'<h4>Margin Interest</h4>'
                f'<div style="text-align:center;margin-bottom:16px">'
                f'  <span style="font-size:1.6rem;font-weight:700;color:{T["red"]}">${debt:,.0f}</span>'
                f'  <span style="font-size:0.85rem;color:{T["text_muted"]}">margin debt</span>'
                f'</div>'
                f'<div class="stat-row">'
                f'<span class="stat-pill">This Month <b style="color:{T["red"]}">-${cur_mo:,.0f}</b></span>'
                f'<span class="stat-pill">YTD <b style="color:{T["red"]}">-${ytd:,.0f}</b></span>'
                f'<span class="stat-pill">All Time <b style="color:{T["red"]}">-${total_int:,.0f}</b></span>'
                f'</div>'
                f'</div>'
            )

        # Render as single HTML grid
        st.markdown(
            f'<div class="greeks-grid">{"".join(_card_htmls)}</div>',
            unsafe_allow_html=True,
        )

    # ── Portfolio Exposure (loads independently via fragment) ──
    @st.cache_data(ttl=86400, show_spinner=False)
    def _cached_ticker_profiles(tickers_tuple, _v=2):
        return fetch_ticker_profiles(list(tickers_tuple))

    @st.fragment
    def _portfolio_exposure():
        st.markdown("<h4 style='text-align:center'>Portfolio Allocation</h4>", unsafe_allow_html=True)
        try:
            with st.spinner("Loading sector & country data..."):
                profiles = _cached_ticker_profiles(tuple(held_tickers), _v=2)
            total_mv = sum(d["market_value"] for d in held.values())

            if total_mv > 0:
                sector_values = {}
                country_values = {}
                for ticker, data in held.items():
                    mv = data["market_value"]
                    profile = profiles.get(ticker, {})
                    sector = profile.get("sector", "Unknown")
                    country = profile.get("country", "Unknown")
                    sector_values[sector] = sector_values.get(sector, 0) + mv
                    country_values[country] = country_values.get(country, 0) + mv

                sector_sorted = sorted(sector_values.items(), key=lambda x: x[1], reverse=True)
                country_sorted = sorted(country_values.items(), key=lambda x: x[1], reverse=True)

                EXPOSURE_COLORS = [
                    '#81b29a', '#3d405b', '#e07a5f', '#f2cc8f', '#9b8ec4',
                    '#64b5f6', '#e57373', '#81c784', '#ffb74d', '#4dd0e1',
                    '#ba68c8', '#a1887f',
                ]

                def _donut_chart(labels, values):
                    fig = go.Figure(data=[go.Pie(
                        labels=labels,
                        values=values,
                        hole=0.55,
                        textinfo='label+percent',
                        textposition='outside',
                        marker=dict(colors=EXPOSURE_COLORS[:len(labels)]),
                        hovertemplate='%{label}<br>$%{value:,.0f}<br>%{percent}<extra></extra>',
                        pull=[0.02] * len(labels),
                    )])
                    fig.update_layout(
                        showlegend=True,
                        legend=dict(
                            orientation="h",
                            yanchor="top",
                            y=-0.12,
                            xanchor="center",
                            x=0.5,
                            font=dict(size=12, color=T['chart_font']),
                        ),
                        margin=dict(t=40, b=60, l=20, r=20),
                        height=520,
                        font=dict(
                            family="-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
                            color=T['chart_font'],
                        ),
                        paper_bgcolor=T['chart_paper'],
                        plot_bgcolor=T['chart_plot'],
                    )
                    return fig

                tab_sector, tab_country = st.tabs(["By Sector", "By Country"])

                with tab_sector:
                    labels = [s[0] for s in sector_sorted]
                    values = [s[1] for s in sector_sorted]
                    st.plotly_chart(_donut_chart(labels, values), use_container_width=True, key="donut_sector")

                with tab_country:
                    labels = [c[0] for c in country_sorted]
                    values = [c[1] for c in country_sorted]
                    st.plotly_chart(_donut_chart(labels, values), use_container_width=True, key="donut_country")

        except Exception as e:
            st.warning(f"Could not load portfolio exposure: {e}")

    with st.container(key="allocation_block"):
        _portfolio_exposure()


# ══════════════════════════════════════════════════════
#  OPTION FINDER PAGE — Standalone option recommendation
# ══════════════════════════════════════════════════════

elif page == "Option Finder":

    st.markdown(
        "<style>.block-container { max-width: 900px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )

    # ── Hero header ──
    st.markdown(
        '<div class="hero-card">'
        '<p class="hero-value" style="font-size:1.8rem;letter-spacing:-0.02em">Option Finder</p>'
        '<p class="hero-sub" style="font-size:0.95rem;max-width:480px;margin:8px auto 0">'
        'Find the best put or call to sell for any ticker.</p>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── Ticker input ──
    _of_raw = st.text_input("Ticker", placeholder="e.g. AAPL", key="of_ticker_input",
                             label_visibility="collapsed")
    _of_ticker = sanitize_ticker(_of_raw) if _of_raw else None

    if _of_ticker:
        if not rate_limited_lookup():
            st.stop()

        # ── Live price ──
        @st.cache_data(ttl=30)
        def _of_price(t):
            try:
                p, _, _ = fetch_stock_price(t)
                return p
            except Exception:
                return 0.0

        _of_live_price = _of_price(_of_ticker)
        if _of_live_price <= 0:
            st.error(f"Could not fetch price for {_of_ticker}. Check the ticker and try again.")
            st.stop()

        _of_logo_url = f"https://assets.parqet.com/logos/symbol/{_of_ticker}"
        st.markdown(
            f'<div style="text-align:center;margin-bottom:20px">'
            f'<img src="{_of_logo_url}" style="width:48px;height:48px;border-radius:50%%;object-fit:cover;clip-path:circle();margin-bottom:8px" '
            f'onerror="this.style.display=\'none\'">'
            f'<div style="font-size:1.3rem;font-weight:700;color:{T["text"]}">'
            f'{_of_ticker} <span style="color:{T["text_muted"]};font-weight:400">${_of_live_price:.2f}</span></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Earnings check ──
        @st.cache_data(ttl=300, show_spinner=False)
        def _of_earnings(t):
            return fetch_earnings_dates([t])

        _of_earn_data = _of_earnings(_of_ticker).get(_of_ticker)
        _of_days_to_earn = None
        if _of_earn_data and _of_earn_data.get('date') and _of_earn_data['date'] >= date.today():
            _of_days_to_earn = (_of_earn_data['date'] - date.today()).days
            if _of_days_to_earn <= 14:
                _earn_color = T['red'] if _of_days_to_earn <= 7 else T['text_muted']
                _earn_label = "Earnings" if not _of_earn_data.get('estimated') else "Earnings (est)"
                _earn_time = " BMO" if _of_earn_data.get('time') == 'bmo' else (" AMC" if _of_earn_data.get('time') == 'amc' else "")
                st.markdown(
                    f'<span style="display:inline-block;padding:4px 10px;border-radius:6px;'
                    f'border:1px solid {_earn_color};color:{_earn_color};font-size:0.88rem">'
                    f'{_earn_label}: {_of_earn_data["date"].strftime("%b %d")}{_earn_time} ({_of_days_to_earn}d)</span>',
                    unsafe_allow_html=True,
                )

        # ── User preferences ──
        _uprefs = load_user_prefs(_sb_client)
        if _uprefs.get('dte_min') == 0 and _uprefs.get('dte_max') == 20:
            _uprefs['dte_min'] = 25
            _uprefs['dte_max'] = 45
            save_user_prefs(_sb_client, _uprefs)

        _sl1, _sl2 = st.columns(2)
        with _sl1:
            _delta_range = st.slider(
                "Delta range", 0.10, 0.50, (_uprefs['delta_min'], _uprefs['delta_max']),
                step=0.05, format="%.2f", key="of_delta_range",
            )
        with _sl2:
            _dte_range = st.slider(
                "DTE range", 0, 90, (_uprefs['dte_min'], _uprefs['dte_max']),
                step=1, key="of_dte_range",
            )

        if (_delta_range[0] != _uprefs['delta_min'] or _delta_range[1] != _uprefs['delta_max']
                or _dte_range[0] != _uprefs['dte_min'] or _dte_range[1] != _uprefs['dte_max']):
            save_user_prefs(_sb_client, {
                'delta_min': _delta_range[0], 'delta_max': _delta_range[1],
                'dte_min': _dte_range[0], 'dte_max': _dte_range[1],
            })

        _usr_dlo, _usr_dhi = _delta_range
        _usr_dte_lo, _usr_dte_hi = _dte_range

        # ── Strategy toggle ──
        if "of_strategy" not in st.session_state:
            st.session_state["of_strategy"] = "Sell Put"
        _chain_strategy = st.pills(
            "Strategy", ["Sell Put", "Write Call"],
            key="of_strategy",
        )
        _opt_type = "Call" if _chain_strategy == "Write Call" else "Put"
        _opt_label = "Put" if _opt_type == "Put" else "Call"

        # ── Cost basis for Write Call ──
        _call_cost_basis = 0.0
        if _chain_strategy == "Write Call":
            _call_cost_basis = st.number_input(
                "Min. strike price", value=0.0, min_value=0.0,
                step=1.0, key="of_cost_basis",
                help="Strikes below this price are excluded from recommendations.",
            )

        # ── Fetch option chain ──
        @st.cache_data(ttl=60, show_spinner="Loading option chain...")
        def _of_cached_chain(t, opt_type, fb_price, dte_lo, dte_hi, n_strikes=8):
            return fetch_option_chain(t, option_type=opt_type, fallback_price=fb_price,
                                      min_dte=dte_lo, max_dte=dte_hi, num_strikes=n_strikes)

        _num_strikes = 15 if _opt_type == 'Call' else 8

        try:
            _chain_data = _of_cached_chain(_of_ticker, _opt_type, _of_live_price, _usr_dte_lo, _usr_dte_hi, _num_strikes)
        except Exception as _chain_err:
            st.error(f"Failed to load option chain: {_chain_err}")
            _chain_data = {'underlying_price': 0, 'expirations': []}
        _ch_price = _chain_data['underlying_price']
        _ch_exps = _chain_data['expirations']

        if not _ch_exps:
            st.info(
                "No option chain data available. This usually means the market is closed "
                "or the streaming connection timed out. Try again during US market hours "
                "(9:30 AM \u2013 4:00 PM ET)."
            )
        else:
            # Build ALL rows across ALL expirations
            _all_rows = []
            _rows_by_exp = {}
            for _ei, _exp in enumerate(_ch_exps):
                _dte = _exp['dte']
                _rows_by_exp[_ei] = []
                for _s in _exp['strikes']:
                    _strike = _s['strike']
                    _bid = _s['bid']
                    _delta = _s['delta']

                    if _dte <= 0 or _bid <= 0:
                        continue

                    _prem_day = _bid / _dte

                    if _opt_type == 'Put':
                        _ann_roc = (_bid / _strike) * (365 / _dte) * 100 if _strike > 0 else 0
                        _breakeven = _strike - _bid
                        _dist = (_ch_price - _strike) / _ch_price * 100 if _ch_price > 0 else 0
                    else:
                        _ann_roc = (_bid / _ch_price) * (365 / _dte) * 100 if _ch_price > 0 else 0
                        _breakeven = _strike + _bid
                        _dist = (_strike - _ch_price) / _ch_price * 100 if _ch_price > 0 else 0

                    _row = {
                        'strike': _strike, 'bid': _bid, 'ask': _s['ask'], 'mid': _s['mid'],
                        'delta': _delta, 'theta': _s['theta'], 'gamma': _s['gamma'],
                        'vega': _s['vega'], 'iv': _s['iv'],
                        'prem_day': _prem_day, 'ann_roc': _ann_roc,
                        'breakeven': _breakeven, 'dist': _dist,
                        'dte': _dte, 'exp_date': _exp['expiration_date'],
                        'exp_type': _exp['expiration_type'],
                    }
                    _all_rows.append(_row)
                    _rows_by_exp[_ei].append(_row)

            # ── Recommendation engine ──
            _eligible = []
            for _r in _all_rows:
                if _opt_type == 'Call' and _call_cost_basis > 0 and _r['strike'] < _call_cost_basis:
                    continue
                _ad = abs(_r['delta'])
                if _ad < _usr_dlo or _ad > _usr_dhi:
                    continue
                _eligible.append(_r)

            _by_exp = {}
            for _r in _eligible:
                _by_exp.setdefault(_r['exp_date'], []).append(_r)

            _picks = {}
            if _eligible and _by_exp:
                _exp_dates_sorted = sorted(_by_exp.keys(), key=lambda e: _by_exp[e][0]['dte'])
                _longest_exp = _exp_dates_sorted[-1]
                _shortest_exp = _exp_dates_sorted[0]

                _cons_candidates = sorted(_by_exp[_longest_exp], key=lambda r: abs(r['delta']))
                _picks['conservative'] = _cons_candidates[0]

                _aggr_candidates = sorted(_by_exp[_shortest_exp], key=lambda r: abs(r['delta']), reverse=True)
                _picks['aggressive'] = _aggr_candidates[0]

                if _longest_exp == _shortest_exp:
                    _all_sorted_delta = sorted(_by_exp[_longest_exp], key=lambda r: abs(r['delta']))
                    _picks['conservative'] = _all_sorted_delta[0]
                    _picks['aggressive'] = _all_sorted_delta[-1]

                # Scoring: no DCF MoS in standalone mode
                _max_roc = max((r['ann_roc'] for r in _eligible), default=1) or 1
                _max_ppd = max((r['prem_day'] for r in _eligible), default=1) or 1

                def _rec_score(r):
                    _roc_n = min(r['ann_roc'], 60) / min(_max_roc, 60) if _max_roc > 0 else 0
                    _delta_n = 1 - abs(r['delta'])
                    _ppd_n = r['prem_day'] / _max_ppd if _max_ppd > 0 else 0
                    return _roc_n * 0.50 + _delta_n * 0.30 + _ppd_n * 0.20

                _cons_pick = _picks.get('conservative')
                _aggr_pick = _picks.get('aggressive')
                _best_rec = None
                _best_rec_sc = -999
                for _r in _eligible:
                    if _r is _cons_pick or _r is _aggr_pick:
                        continue
                    _sc = _rec_score(_r)
                    if _sc > _best_rec_sc:
                        _best_rec_sc = _sc
                        _best_rec = _r
                if _best_rec is None:
                    _best_rec = max(_eligible, key=_rec_score)
                _picks['recommended'] = _best_rec

            if not _picks:
                _n_total = len(_all_rows)
                _n_cost = sum(1 for r in _all_rows if _opt_type == 'Call' and _call_cost_basis > 0 and r['strike'] < _call_cost_basis)
                _n_delta = sum(1 for r in _all_rows if not (_usr_dlo <= abs(r['delta']) <= _usr_dhi))
                _hints = []
                if _n_total == 0:
                    _hints.append("No strikes with valid bids were found in the chain.")
                else:
                    if _n_cost > 0:
                        _hints.append(f"{_n_cost}/{_n_total} strikes filtered by min strike (${_call_cost_basis:.0f}).")
                    if _n_delta > 0:
                        _deltas = [abs(r['delta']) for r in _all_rows if r['delta'] != 0]
                        _drange = f"{min(_deltas):.2f}\u2013{max(_deltas):.2f}" if _deltas else "n/a"
                        _hints.append(f"{_n_delta}/{_n_total} strikes outside delta range "
                                      f"{_usr_dlo:.2f}\u2013{_usr_dhi:.2f} (available: {_drange}).")
                _hint_text = " ".join(_hints) if _hints else "Try widening your delta or DTE range."
                st.info(f"No suitable strikes match your filters. {_hint_text}")
            else:
                # ── Recommendation cards ──
                _pill = (
                    'display:inline-block;padding:4px 10px;border-radius:6px;'
                    f'background:{T["pill_bg"]};border:1px solid {T["pill_border"]};'
                    'margin:3px 4px 3px 0;font-size:0.88rem;white-space:nowrap;'
                )

                # CSS tooltip for earnings warning — inject once
                st.markdown(
                    f'<style>'
                    f'.earn-warn {{position:relative;display:inline-flex;align-items:center;'
                    f'justify-content:center;width:22px;height:22px;border-radius:50%;'
                    f'background:#fde68a;color:#92400e;font-weight:700;font-size:0.8rem;'
                    f'margin-left:8px;cursor:help;vertical-align:middle;flex-shrink:0}}'
                    f'.earn-warn .earn-tip {{display:none;position:absolute;bottom:130%;'
                    f'left:50%;transform:translateX(-50%);background:{T["card"]};'
                    f'color:{T["text"]};border:1px solid {T["border_medium"]};'
                    f'border-radius:6px;padding:4px 10px;font-size:0.78rem;font-weight:400;'
                    f'white-space:nowrap;z-index:10;box-shadow:{T["shadow"]}}}'
                    f'.earn-warn:hover .earn-tip {{display:block}}'
                    f'</style>',
                    unsafe_allow_html=True,
                )

                def _earn_icon(r):
                    if _of_days_to_earn is not None and _of_days_to_earn <= r['dte']:
                        _earn_date = _of_earn_data['date'].strftime('%b %d')
                        return (
                            f'<span class="earn-warn">!'
                            f'<span class="earn-tip">Earnings on {_earn_date} ({_of_days_to_earn}d)</span>'
                            f'</span>'
                        )
                    return ''

                def _metric_pills(r):
                    return (
                        f'<div style="display:flex;flex-wrap:wrap;margin-top:8px">'
                        f'<span style="{_pill}color:{T["text"]}">Premium <b>${r["bid"]:.2f}</b></span>'
                        f'<span style="{_pill}color:{T["text"]}">$/Day <b>${r["prem_day"]:.2f}</b></span>'
                        f'<span style="{_pill}color:{T["accent"] if r["ann_roc"] >= 15 else T["text"]}">'
                        f'Ann. ROC <b>{r["ann_roc"]:.1f}%%</b></span>'
                        f'<span style="{_pill}color:{T["text"]}">Delta <b>{abs(r["delta"]):.2f}</b></span>'
                        f'<span style="{_pill}color:{T["text"]}">Buffer <b>{r["dist"]:.1f}%%</b></span>'
                        f'<span style="{_pill}color:{T["text"]}">Breakeven <b>${r["breakeven"]:.2f}</b></span>'
                        f'</div>'
                    )

                # Primary recommendation
                if 'recommended' in _picks:
                    _rec = _picks['recommended']
                    _html = (
                        f'<div style="background:{T["accent_light"]};border:1px solid {T["accent"]};'
                        f'border-radius:10px;padding:18px 22px;margin-bottom:14px">'
                        f'<div style="display:flex;align-items:center;font-weight:700;font-size:1.15rem;color:{T["text"]}">'
                        f'Recommended: ${_rec["strike"]:.0f} {_opt_label} \u2014 {datetime.strptime(_rec["exp_date"], "%Y-%m-%d").strftime("%d %b %Y")} ({_rec["dte"]}d){_earn_icon(_rec)}</div>'
                        f'{_metric_pills(_rec)}'
                        f'<div style="font-size:0.83rem;color:{T["text_muted"]};margin-top:10px">'
                        f'Based on delta targeting and annualized return.</div>'
                        f'</div>'
                    )
                    st.markdown(_html, unsafe_allow_html=True)

                # Conservative + Aggressive side by side
                _alt_cards = []
                if 'conservative' in _picks and _picks['conservative'] != _picks.get('recommended'):
                    _c = _picks['conservative']
                    _alt_cards.append((
                        f'<div style="background:{T["card_alt"]};border:1px solid {T["border_medium"]};'
                        f'border-radius:10px;padding:14px 18px">'
                        f'<div style="display:flex;align-items:center;font-weight:700;font-size:0.97rem;color:{T["text"]}">'
                        f'Conservative: ${_c["strike"]:.0f} {_opt_label} \u2014 {datetime.strptime(_c["exp_date"], "%Y-%m-%d").strftime("%d %b %Y")} ({_c["dte"]}d){_earn_icon(_c)}</div>'
                        f'{_metric_pills(_c)}'
                        f'<div style="font-size:0.8rem;color:{T["text_muted"]};margin-top:8px">'
                        f'Lower delta, more downside buffer, less premium.</div>'
                        f'</div>'
                    ))
                if 'aggressive' in _picks and _picks['aggressive'] != _picks.get('recommended'):
                    _a = _picks['aggressive']
                    _alt_cards.append((
                        f'<div style="background:{T["card_alt"]};border:1px solid {T["border_medium"]};'
                        f'border-radius:10px;padding:14px 18px">'
                        f'<div style="display:flex;align-items:center;font-weight:700;font-size:0.97rem;color:{T["text"]}">'
                        f'Aggressive: ${_a["strike"]:.0f} {_opt_label} \u2014 {datetime.strptime(_a["exp_date"], "%Y-%m-%d").strftime("%d %b %Y")} ({_a["dte"]}d){_earn_icon(_a)}</div>'
                        f'{_metric_pills(_a)}'
                        f'<div style="font-size:0.8rem;color:{T["text_muted"]};margin-top:8px">'
                        f'Higher delta, more premium, tighter buffer.</div>'
                        f'</div>'
                    ))

                if _alt_cards:
                    _cols = st.columns(len(_alt_cards))
                    for _ci, _ch in enumerate(_alt_cards):
                        with _cols[_ci]:
                            st.markdown(_ch, unsafe_allow_html=True)

            # ── Full chain expander ──
            st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
            with st.expander("Show full chain"):
                _exp_labels = []
                for _e in _ch_exps:
                    _lbl = f"{_e['expiration_date']} \u00b7 {_e['dte']}d"
                    if _e['expiration_type'] != 'Regular':
                        _lbl += " (W)"
                    _exp_labels.append(_lbl)

                _sel_exp = st.pills(
                    "Expiration", _exp_labels, default=_exp_labels[0],
                    key="of_chain_expiration",
                )
                _exp_idx = _exp_labels.index(_sel_exp) if _sel_exp in _exp_labels else 0
                _chain_rows = _rows_by_exp.get(_exp_idx, [])

                if not _chain_rows:
                    st.info("No strikes with valid bids for this expiration.")
                else:
                    _rec_strike = _picks.get('recommended', {}).get('strike')
                    _rec_dte = _picks.get('recommended', {}).get('dte')

                    _th = f'padding:8px 10px;text-align:right;color:{T["text_muted"]};font-weight:600;white-space:nowrap'
                    _ct_hdr = (
                        f'<div style="overflow-x:auto"><table style="width:100%%;border-collapse:collapse;font-size:0.85rem">'
                        f'<thead><tr style="border-bottom:2px solid {T["border_medium"]}">'
                    )
                    _chain_cols = ["Strike", "Bid", "Mid", "Delta", "$/Day", "Ann. ROC", "Breakeven", "Distance"]
                    if _of_days_to_earn is not None:
                        _chain_cols.append("")
                    for _col in _chain_cols:
                        _ct_hdr += f'<th style="{_th}">{_col}</th>'
                    _ct_hdr += '</tr></thead><tbody>'

                    _td = 'padding:8px 10px;text-align:right;white-space:nowrap;'
                    _ct_body = ''
                    for _r in _chain_rows:
                        _is_rec = (_r['strike'] == _rec_strike and _r['dte'] == _rec_dte)
                        _row_bg = f'background:{T["accent_light"]};' if _is_rec else ''
                        _row_fw = 'font-weight:700;' if _is_rec else ''
                        _roc_color = T['accent'] if _r['ann_roc'] >= 15 else (T['red'] if _r['ann_roc'] < 8 else T['text'])

                        _ct_body += f'<tr style="{_row_bg}border-bottom:1px solid {T["border"]}">'
                        _ct_body += f'<td style="{_td}{_row_fw}color:{T["text"]}">${_r["strike"]:.0f}</td>'
                        _ct_body += f'<td style="{_td}{_row_fw}color:{T["text"]}">${_r["bid"]:.2f}</td>'
                        _ct_body += f'<td style="{_td}{_row_fw}color:{T["text"]}">${_r["mid"]:.2f}</td>'
                        _ct_body += f'<td style="{_td}{_row_fw}color:{T["text"]}">{_r["delta"]:.2f}</td>'
                        _ct_body += f'<td style="{_td}{_row_fw}color:{T["text"]}">${_r["prem_day"]:.2f}</td>'
                        _ct_body += f'<td style="{_td}{_row_fw}color:{_roc_color}">{_r["ann_roc"]:.1f}%%</td>'
                        _ct_body += f'<td style="{_td}{_row_fw}color:{T["text"]}">${_r["breakeven"]:.2f}</td>'
                        _ct_body += f'<td style="{_td}{_row_fw}color:{T["text"]}">{_r["dist"]:.1f}%%</td>'
                        if _of_days_to_earn is not None:
                            _ct_body += f'<td style="{_td}text-align:center">{_earn_icon(_r)}</td>'
                        _ct_body += '</tr>'

                    _ct_html = _ct_hdr + _ct_body + '</tbody></table></div>'
                    st.markdown(_ct_html, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════
#  WHEEL COST BASIS PAGE — Detailed trade history
# ══════════════════════════════════════════════════════

elif page == "Wheel Cost Basis":

    if not has_active_broker():
        _render_connect_prompt()


    st.markdown("")
    cost_basis = _load_portfolio_data()

    def _is_put(t):
        """Check if trade is put via OCC symbol, fallback to description."""
        _, _, cp = _parse_option_symbol(t.get("symbol"))
        if cp:
            return cp == "P"
        return "Put" in (t.get("description") or "")

    def _is_call(t):
        """Check if trade is call via OCC symbol, fallback to description."""
        _, _, cp = _parse_option_symbol(t.get("symbol"))
        if cp:
            return cp == "C"
        return "Call" in (t.get("description") or "")

    # ── Helper: detect if a ticker has an active position ──
    def _is_active(data):
        """Active = shares held or any open option positions."""
        if data["shares_held"] > 0:
            return True
        return _has_open_options(data)

    # ── Helper: categorize trades ──
    def _categorize(trades):
        csp = [t for t in trades if t.get("label") in ("CSP", "BTC CSP") or (t.get("label") == "Expired" and "Put" in (t.get("description") or ""))]
        cc = [t for t in trades if t.get("label") in ("CC", "BTC CC") or (t.get("label") == "Expired" and "Call" in (t.get("description") or ""))]
        sh = [t for t in trades if t.get("instrument_type") == "Equity" or t.get("label") == "Dividend"]
        return csp, cc, sh

    # ── Helper: render trade rows ──
    def _render_trades(trades):
        if not trades:
            st.caption("No trades.")
            return
        html = ""
        for t in reversed(trades):
            qty_val = int(t["quantity"]) if t["quantity"] == int(t["quantity"]) else t["quantity"]
            price_str = f'{t["price"]:,.2f}' if t["price"] else "\u2014"
            net = t["net_value"]
            net_color = T['accent'] if net >= 0 else T['red']
            trade_date = t["date"].strftime("%d-%m-%Y") if hasattr(t["date"], "strftime") else t["date"]

            # Friendly labels for equity trades
            label_raw = t["label"]
            if t.get("instrument_type") == "Equity":
                if label_raw == "Assignment":
                    label_raw = "Buy Shares" if t["net_value"] < 0 else "Sell Shares"
                elif label_raw == "Stock Buy":
                    label_raw = "Buy Shares"
                elif label_raw == "Stock Sell":
                    label_raw = "Sell Shares"

            # Option info: strike + expiration
            strike, exp, _cp = _parse_option_symbol(t.get("symbol"))
            if strike is not None:
                label_str = f'{label_raw} @ {strike:,.2f}'
                date_str = f'{trade_date} &nbsp; exp {exp}' if exp else trade_date
            else:
                label_str = label_raw
                date_str = trade_date

            html += (
                f'<div class="trade-row">'
                f'  <div class="tr-desc">'
                f'    <p class="tr-label">{label_str}</p>'
                f'    <p class="tr-date">{date_str}</p>'
                f'  </div>'
                f'  <div class="tr-cell">'
                f'    <p class="tr-val">{qty_val}</p>'
                f'    <p class="tr-lbl">Qty</p>'
                f'  </div>'
                f'  <div class="tr-cell">'
                f'    <p class="tr-val">{price_str}</p>'
                f'    <p class="tr-lbl">Fill</p>'
                f'  </div>'
                f'  <div class="tr-cell">'
                f'    <p class="tr-val" style="color:{net_color}">${abs(net):,.2f}</p>'
                f'    <p class="tr-lbl">P/L</p>'
                f'  </div>'
                f'</div>'
            )
        st.markdown(html, unsafe_allow_html=True)

    # ── Helper: render tabs per trade category ──
    def _render_tabs(trades, key_suffix):
        csp, cc, sh = _categorize(trades)
        tab_csp, tab_cc, tab_shares = st.tabs([
            f"CSP ({len(csp)})",
            f"CC ({len(cc)})",
            f"Shares ({len(sh)})",
        ])
        with tab_csp:
            _render_trades(csp)
        with tab_cc:
            _render_trades(cc)
        with tab_shares:
            _render_trades(sh)

    # ── Helper: render a ticker card ──
    def _render_ticker_card(ticker, data):
        pl = data["total_pl_real"]
        shares = data["shares_held"]
        wheels = data.get("wheels", [])
        cur_price = data["current_price"]
        prev_close = data.get("previous_close", cur_price)

        # Calculate buy price and adjusted cost from last wheel
        last_wheel = wheels[-1] if wheels else None
        buy_price = 0.0
        adj_cost = data["cost_per_share"]
        wheel_equity = 0.0
        wheel_option = 0.0
        w_shares = 0
        w_buy_total = 0.0
        if last_wheel:
            for t in last_wheel["trades"]:
                if t["instrument_type"] == "Equity":
                    wheel_equity += t["net_value"]
                    action = t.get("action", "")
                    txn_type = t.get("type", "")
                    qty = t["quantity"]
                    p = t["price"] if t["price"] else abs(t["net_value"]) / qty if qty else 0.0
                    if txn_type == "Receive Deliver" and t["net_value"] < 0:
                        w_shares += qty
                        w_buy_total += p * qty
                    elif "Buy" in action:
                        w_shares += qty
                        w_buy_total += p * qty
                elif "Option" in t["instrument_type"]:
                    wheel_option += t["net_value"]
            if w_shares > 0:
                buy_price = w_buy_total / w_shares
            if shares > 0:
                adj_cost = (wheel_equity + wheel_option) / shares

        # Day change
        day_chg = ((cur_price - prev_close) / prev_close * 100) if prev_close else 0.0
        day_color = T['accent'] if day_chg >= 0 else T['red']

        with st.container(key=f"wheel_card_{ticker}"):
            all_trades = data.get("trades", [])

            # Toggle: per wheel vs all transactions
            per_wheel = st.toggle("Per wheel", key=f"wheel_toggle_{ticker}") if all_trades else False

            # P/L: last wheel only when toggled, otherwise total
            if per_wheel and last_wheel:
                display_pl = last_wheel["pl"]
                if shares > 0:
                    display_pl += data["market_value"]
            else:
                display_pl = pl

            pl_badge = "pl-badge-green" if display_pl >= 0 else "pl-badge-red"
            pl_sign = "+$" if display_pl >= 0 else "-$"

            logo_url = f"https://assets.parqet.com/logos/symbol/{ticker}"
            st.markdown(
                f'<div class="card-header">'
                f'  <div class="card-left">'
                f'    <div class="tk-title">'
                f'      <img class="tk-logo" src="{logo_url}" onerror="this.style.display=\'none\'">'
                f'      <p class="tk-name">{ticker} @ {buy_price:,.2f}</p>'
                f'    </div>'
                f'    <p class="tk-sub">(Adjusted: {abs(adj_cost):,.2f})</p>'
                f'    <p class="tk-sub">Current Price</p>'
                f'    <p class="tk-sub" style="color:{day_color}; font-weight:500">'
                f'      {cur_price:,.2f} ({day_chg:+.2f}%)</p>'
                f'  </div>'
                f'  <div class="card-center">'
                f'    <p class="shares-count">{shares}</p>'
                f'    <p class="shares-label">shares held</p>'
                f'  </div>'
                f'  <div>'
                f'    <span class="pl-badge {pl_badge}">{pl_sign}{abs(display_pl):,.2f}</span>'
                f'  </div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            if not all_trades:
                return

            if per_wheel:
                for i, wheel in reversed(list(enumerate(wheels))):
                    status = wheel["status"]
                    w_pl = wheel["pl"]
                    w_pl_sign = "+$" if w_pl >= 0 else "-$"
                    w_start = wheel['start'].strftime("%d-%m-%Y") if hasattr(wheel['start'], 'strftime') else wheel['start']
                    w_end = wheel['end'].strftime("%d-%m-%Y") if hasattr(wheel['end'], 'strftime') else wheel['end']
                    if status == "completed":
                        label = f"Wheel {i + 1} \u2014 {w_start} \u2192 {w_end}"
                    elif status == "active":
                        label = f"Wheel {i + 1} (active) \u2014 {w_start} \u2192 now"
                    else:
                        label = f"CSP Income \u2014 {w_start} \u2192 {w_end}"
                    with st.expander(f"{label}  \u2014  {w_pl_sign}{abs(w_pl):,.2f}"):
                        _render_tabs(wheel["trades"], f"{ticker}_w{i}")
            else:
                n_total = len(all_trades)
                with st.expander(f"Transactions ({n_total})"):
                    _render_tabs(all_trades, f"{ticker}_all")

    # ── Two-column card layout ──
    st.markdown(
        "<style>.block-container { max-width: 1200px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )

    # ── Client-side live search (pure JS, no server roundtrip) ──
    st.markdown(
        f'<input type="text" id="ticker-live-search" placeholder="Search ticker..." '
        f'style="width:100%;padding:10px 14px;font-size:16px;border:1px solid #ddd;'
        f'border-radius:8px;margin-bottom:12px;outline:none;box-sizing:border-box;'
        f'background:{T["input_bg"]};" onfocus="this.style.borderColor=\'#4a90d9\'" '
        f'onblur="this.style.borderColor=\'#ddd\'">',
        unsafe_allow_html=True,
    )

    # ── Split tickers into active / closed ──
    active_tickers = {t: d for t, d in cost_basis.items() if _is_active(d)}
    closed_tickers = {t: d for t, d in cost_basis.items() if not _is_active(d)}

    def _render_grid(tickers):
        items = list(tickers.items())
        for i in range(0, len(items), 2):
            cols = st.columns(2)
            for j, col in enumerate(cols):
                if i + j < len(items):
                    with col:
                        _render_ticker_card(items[i + j][0], items[i + j][1])

    st.markdown(f"### Active ({len(active_tickers)})")
    if active_tickers:
        _render_grid(active_tickers)
    else:
        st.caption("No active positions.")

    st.markdown(f"### Closed ({len(closed_tickers)})")
    if closed_tickers:
        _render_grid(closed_tickers)
    else:
        st.caption("No closed positions.")

    # ── JS: instant client-side card filtering ──
    components.html(
        """
        <script>
        (function() {
            const doc = window.parent.document;
            const input = doc.getElementById("ticker-live-search");
            if (!input || input.dataset.bound) return;
            input.dataset.bound = "1";
            input.addEventListener("input", function() {
                const q = this.value.toUpperCase();
                // Find all ticker name elements
                const names = doc.querySelectorAll(".tk-name");
                names.forEach(function(el) {
                    // Extract ticker from "TICKER @ 123.45"
                    const ticker = el.textContent.split(" ")[0].toUpperCase();
                    // Traverse up to the stColumn container
                    let col = el.closest('[data-testid="stColumn"]');
                    if (col) {
                        col.style.display = (!q || ticker.includes(q)) ? "" : "none";
                    }
                });
                // Hide empty rows (both columns hidden)
                doc.querySelectorAll('[data-testid="stHorizontalBlock"]').forEach(function(row) {
                    const cols = row.querySelectorAll('[data-testid="stColumn"]');
                    if (cols.length === 0) return;
                    const anyVisible = Array.from(cols).some(c => c.style.display !== "none");
                    row.style.display = anyVisible ? "" : "none";
                });
            });
        })();
        </script>
        """,
        height=0,
    )

# ══════════════════════════════════════════════════════
#  RESULTS PAGE — P/L performance overview
# ══════════════════════════════════════════════════════

elif page == "Results":

    st.markdown(
        "<style>.block-container { max-width: 1200px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )

    if not has_active_broker():
        _render_connect_prompt()

    st.markdown("")
    cost_basis = _load_portfolio_data()

    # ── Compute aggregates ──
    total_pl_real = sum(d["total_pl_real"] for d in cost_basis.values())
    total_option_pl = sum(d["option_pl"] for d in cost_basis.values())
    total_dividends = sum(d["dividends"] for d in cost_basis.values())
    active_positions = sum(
        1 for d in cost_basis.values()
        if d["shares_held"] > 0 or _has_open_options(d)
    )

    realized_pl = sum(
        w["pl"] for d in cost_basis.values()
        for w in d.get("wheels", []) if w["status"] == "completed"
    )
    unrealized_pl = sum(
        d["market_value"] + d["equity_cost"]
        for d in cost_basis.values() if d["shares_held"] > 0
    )

    pl_color_class = "hero-green" if total_pl_real >= 0 else "hero-red"
    pl_sign = "+" if total_pl_real >= 0 else ""

    # ── Compute CAGR from net liq history (deposit-adjusted) ──
    cagr_pill = ""
    if "net_liq_all" not in st.session_state:
        try:
            with st.spinner("Loading full net liq history..."):
                st.session_state["net_liq_all"] = fetch_net_liq_history("all")
        except Exception as e:
            if not _is_auth_error(e):
                logger.warning("Net liq history fetch failed: %s", e)
                log_error_with_trace("PORTFOLIO_ERROR", e, page="Portfolio", metadata={"component": "net_liq_history"})
            st.session_state["net_liq_all"] = None
    if "yearly_transfers" not in st.session_state:
        try:
            with st.spinner("Loading cash transfer history..."):
                st.session_state["yearly_transfers"] = fetch_yearly_transfers()
        except Exception as e:
            if not _is_auth_error(e):
                logger.warning("Yearly transfers fetch failed: %s", e)
                log_error_with_trace("PORTFOLIO_ERROR", e, page="Portfolio", metadata={"component": "yearly_transfers"})
            st.session_state["yearly_transfers"] = {}

    nl_all_early = st.session_state.get("net_liq_all")
    transfers_early = st.session_state.get("yearly_transfers", {})
    if nl_all_early:
        # Yearly Simple Dietz returns, then compound to CAGR
        df_cagr = pd.DataFrame(nl_all_early)
        df_cagr["time"] = pd.to_datetime(df_cagr["time"])
        df_cagr = df_cagr.sort_values("time")
        df_cagr["year"] = df_cagr["time"].dt.year
        yr_close = df_cagr.groupby("year")["close"].last()
        yr_list = sorted(yr_close.index)
        compound = 1.0
        for i in range(1, len(yr_list)):
            prev_yr, cur_yr = yr_list[i - 1], yr_list[i]
            start_v = yr_close[prev_yr]
            end_v = yr_close[cur_yr]
            yr_data = transfers_early.get(cur_yr, {})
            net_dep = yr_data["total"] if isinstance(yr_data, dict) and "total" in yr_data else 0.0
            denom = start_v + 0.5 * net_dep
            if denom > 0:
                compound *= (1 + (end_v - start_v - net_dep) / denom)
        days = (df_cagr["time"].iloc[-1] - df_cagr["time"].iloc[0]).days
        n_years = days / 365.25
        if n_years > 0:
            cagr = (compound ** (1 / n_years) - 1) * 100
            cagr_sign = "+" if cagr >= 0 else ""
            cagr_pill = f'<span class="stat-pill">CAGR <b>{cagr_sign}{cagr:.1f}%</b></span>'

    # ── Hero card ──
    portfolio_val_pill = ""
    total_dep_pill = ""
    ytd_pill = ""
    if nl_all_early:
        pv = df_cagr["close"].iloc[-1]
        portfolio_val_pill = f'<span class="stat-pill">Portfolio Value <b>${pv:,.0f}</b></span>'

        total_dep = sum(v["total"] for v in transfers_early.values()) if transfers_early else 0
        total_dep_pill = f'<span class="stat-pill">Total Deposited <b>${total_dep:,.0f}</b></span>'

        true_pl = pv - total_dep
        true_pl_sign = "+" if true_pl >= 0 else ""
        true_pl_class = "hero-green" if true_pl >= 0 else "hero-red"

        # Compute monthly & yearly returns once (reused by Returns section below)
        from datetime import datetime as _dt_cls
        _cur_year = _dt_cls.now().year
        _df_mr = pd.DataFrame(nl_all_early)
        _df_mr["time"] = pd.to_datetime(_df_mr["time"])
        _df_mr = _df_mr.sort_values("time")
        _df_mr["year"] = _df_mr["time"].dt.year
        _df_mr["month"] = _df_mr["time"].dt.month
        _month_close = _df_mr.groupby(["year", "month"])["close"].last()
        _month_periods = list(_month_close.index)
        _monthly_rets = {}
        for _i in range(1, len(_month_periods)):
            _prev_yr, _prev_mo = _month_periods[_i - 1]
            _yr, _mo = _month_periods[_i]
            _sv = _month_close[(_prev_yr, _prev_mo)]
            _ev = _month_close[(_yr, _mo)]
            _yt = transfers_early.get(_yr, {})
            _md = _yt.get("months", {}).get(_mo, 0) if isinstance(_yt, dict) else 0
            _dn = _sv + 0.5 * _md
            if _dn > 0:
                _ret = (_ev - _sv - _md) / _dn * 100
            else:
                _ret = 0.0
            _monthly_rets.setdefault(_yr, {})[_mo] = _ret
        _yearly_rets = {}
        for _yr, _months in _monthly_rets.items():
            _factor = 1.0
            for _mo in sorted(_months):
                _factor *= (1 + _months[_mo] / 100)
            _yearly_rets[_yr] = round((_factor - 1) * 100, 1)
        # Store for reuse in Returns section
        st.session_state["_cached_monthly_returns"] = _monthly_rets
        st.session_state["_cached_yearly_returns"] = _yearly_rets
        # YTD pill from the cached yearly return
        ytd_ret = _yearly_rets.get(_cur_year)
        if ytd_ret is not None and abs(ytd_ret) > 0.01:
            ytd_sign = "+" if ytd_ret >= 0 else ""
            ytd_pill = f'<span class="stat-pill">YTD <b>{ytd_sign}{ytd_ret:.1f}%</b></span>'

    hero_pl = true_pl if nl_all_early else total_pl_real
    hero_pl_class = true_pl_class if nl_all_early else pl_color_class
    hero_pl_sign = true_pl_sign if nl_all_early else pl_sign

    with st.container(key="results_hero"):
      st.markdown(
        f'<div class="hero-card">'
        f'<p class="hero-label">Total P/L</p>'
        f'<p class="hero-value {hero_pl_class}">{hero_pl_sign}${abs(hero_pl):,.0f}</p>'
        f'<p class="hero-sub">{active_positions} active positions</p>'
        f'<div class="stat-row">'
        f'{portfolio_val_pill}'
        f'{total_dep_pill}'
        f'{cagr_pill}'
        f'{ytd_pill}'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
      )

      # ── Net Liq History chart ──
      period_map = {"1M": "1m", "3M": "3m", "6M": "6m", "YTD": "ytd", "1Y": "1y", "All": "all"}
      selected_period = st.pills(
          "Period", options=list(period_map.keys()), default="YTD",
      )
      time_back = period_map[selected_period]
      # YTD uses 1y data filtered client-side to Jan 1 of current year
      api_time_back = "1y" if time_back == "ytd" else time_back
      cache_key = f"net_liq_{api_time_back}"
      if cache_key not in st.session_state:
          try:
              with st.spinner("Loading net liq history..."):
                  st.session_state[cache_key] = fetch_net_liq_history(api_time_back)
          except Exception as e:
              logger.warning("Net liq history fetch failed (%s): %s", api_time_back, e)
              st.session_state[cache_key] = None

      net_liq_data = st.session_state[cache_key]
      if net_liq_data:
          df_liq = pd.DataFrame(net_liq_data)
          df_liq["time"] = pd.to_datetime(df_liq["time"])
          df_liq = df_liq.set_index("time")
          if time_back == "ytd":
              df_liq = df_liq[df_liq.index >= f"{pd.Timestamp.now().year}-01-01"]
          first_close = df_liq["close"].iloc[0]
          last_close = df_liq["close"].iloc[-1]
          # For YTD, use the deposit-adjusted yearly return (matches hero & Returns)
          _cached_yr = st.session_state.get("_cached_yearly_returns", {})
          if time_back == "ytd" and pd.Timestamp.now().year in _cached_yr:
              pct_change = _cached_yr[pd.Timestamp.now().year]
          else:
              pct_change = ((last_close - first_close) / first_close * 100) if first_close else 0
          pct_color = T['accent'] if pct_change >= 0 else T['red']
          pct_sign = "+" if pct_change >= 0 else ""
          st.markdown(
              f'<span style="font-size:1.3rem;font-weight:700;color:{pct_color}">'
              f'{pct_sign}{pct_change:.1f}%</span> '
              f'<span style="color:{T["text_muted"]};font-size:0.85rem">{selected_period}</span>',
              unsafe_allow_html=True,
          )
          fig_liq = go.Figure()
          fig_liq.add_trace(go.Scatter(
              x=df_liq.index,
              y=df_liq["close"],
              mode="lines",
              line=dict(color=T['accent'], width=2),
              fill="tozeroy",
              fillcolor=T['accent_fill'],
              hovertemplate="$%{y:,.0f}<extra></extra>",
          ))
          fig_liq.update_layout(
              margin=dict(t=10, b=20, l=40, r=20),
              height=300,
              font=dict(
                  family="-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
                  color=T['chart_font'],
              ),
              paper_bgcolor=T['chart_paper'],
              plot_bgcolor=T['chart_plot'],
              xaxis=dict(gridcolor=T['chart_grid']),
              yaxis=dict(gridcolor=T['chart_grid']),
              showlegend=False,
          )
          st.plotly_chart(fig_liq, use_container_width=True)
      else:
          st.info("Net liquidation history unavailable.")

    # ── Top / Bottom performers ──
    sorted_tickers = sorted(
        cost_basis.items(), key=lambda x: x[1]["total_pl_real"], reverse=True,
    )
    top5 = sorted_tickers[:5]
    bottom5 = sorted_tickers[-5:][::-1]

    def _performer_cards(items):
        cards = ''
        for ticker, data in items:
            logo = f"https://assets.parqet.com/logos/symbol/{ticker}"
            pl = data["total_pl_real"]
            pl_cls = " pf-green" if pl > 0 else " pf-red" if pl < 0 else ""
            opt = data["option_pl"]
            opt_cls = " pf-green" if opt > 0 else " pf-red" if opt < 0 else ""
            cards += (
                f'<div class="portfolio-card">'
                f'<img class="pf-logo" src="{logo}" onerror="this.style.display=\'none\'">'
                f'<span class="pf-ticker">{ticker}</span>'
                f'<div class="pf-cell"><span class="pf-label">Total P/L</span>'
                f'<span class="pf-val{pl_cls}">${pl:+,.0f}</span></div>'
                f'<div class="pf-cell"><span class="pf-label">Options P/L</span>'
                f'<span class="pf-val{opt_cls}">${opt:+,.0f}</span></div>'
                f'<div class="pf-cell"><span class="pf-label">Dividends</span>'
                f'<span class="pf-val">${data["dividends"]:,.0f}</span></div>'
                f'</div>'
            )
        return cards

    st.markdown(
        f'<div class="performer-grid" style="margin:24px 0">'
        f'<div>'
        f'<div class="section-title-bar">Top Performers</div>'
        f'<div class="portfolio-cards">{_performer_cards(top5)}</div>'
        f'</div>'
        f'<div>'
        f'<div class="section-title-bar" style="border-left-color:{T["red"]}">Bottom Performers</div>'
        f'<div class="portfolio-cards">{_performer_cards(bottom5)}</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    with st.container(key="cumulative_block"):
        if "benchmark_returns" not in st.session_state:
            try:
                with st.spinner("Loading benchmark data..."):
                    st.session_state["benchmark_returns"] = fetch_benchmark_returns()
            except Exception as e:
                logger.warning("Benchmark returns fetch failed: %s", e)
                st.session_state["benchmark_returns"] = {}

        nl_all = st.session_state.get("net_liq_all")
        bench_returns = st.session_state["benchmark_returns"]
        transfers = st.session_state.get("yearly_transfers", {})

        if nl_all:
            df_nl = pd.DataFrame(nl_all)
            df_nl["time"] = pd.to_datetime(df_nl["time"])
            df_nl["year"] = df_nl["time"].dt.year
            # Last close per year
            year_close = df_nl.groupby("year")["close"].last()
            port_returns = {}
            years = sorted(year_close.index)
            for i in range(1, len(years)):
                prev_yr, cur_yr = years[i - 1], years[i]
                start_val = year_close[prev_yr]
                end_val = year_close[cur_yr]
                yr_data = transfers.get(cur_yr, {})
                net_dep = yr_data["total"] if isinstance(yr_data, dict) and "total" in yr_data else 0.0
                # Simple Dietz: adjust for deposits/withdrawals (assume mid-year)
                denominator = start_val + 0.5 * net_dep
                if denominator > 0:
                    port_returns[cur_yr] = (end_val - start_val - net_dep) / denominator * 100
                else:
                    port_returns[cur_yr] = 0.0

            # Collect all years across portfolio + all benchmarks
            all_years_set = set(port_returns.keys())
            for b_returns in bench_returns.values():
                all_years_set |= set(b_returns.keys())
            all_years = sorted(all_years_set)

            rows_yr = []
            for yr in all_years:
                if yr in port_returns:
                    row = {"year": str(yr), "portfolio": round(port_returns[yr], 1)}
                    for bench_name, b_returns in bench_returns.items():
                        row[bench_name] = round(b_returns.get(yr, 0), 1) if yr in b_returns else None
                    rows_yr.append(row)
            if rows_yr:
                # ── Cards ──
                cards_html = '<div class="portfolio-cards">'
                for row in reversed(rows_yr):
                    port_val = row["portfolio"]
                    port_cls = " pf-green" if port_val >= 0 else " pf-red"
                    cells = (
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Portfolio</span>'
                        f'<span class="pf-val{port_cls}">{port_val:+.1f}%</span>'
                        f'</div>'
                    )
                    for bench_name in bench_returns:
                        bval = row.get(bench_name)
                        if bval is not None:
                            b_cls = " pf-green" if bval >= port_val else " pf-red"
                            cells += (
                                f'<div class="pf-cell">'
                                f'<span class="pf-label">{bench_name}</span>'
                                f'<span class="pf-val{b_cls}">{bval:+.1f}%</span>'
                                f'</div>'
                            )
                        else:
                            cells += (
                                f'<div class="pf-cell">'
                                f'<span class="pf-label">{bench_name}</span>'
                                f'<span class="pf-val">\u2014</span>'
                                f'</div>'
                            )
                    # Add info icon after last benchmark cell
                    cells += (
                        f'<div class="pf-cell" style="flex:0;min-width:auto;padding:0 4px">'
                        f'<span class="css-tip" data-tip="Green = beat your portfolio · Red = underperformed" '
                        f'style="font-size:0.75rem;color:{T["text_muted"]}">&#9432;</span>'
                        f'</div>'
                    )
                    cards_html += (
                        f'<div class="portfolio-card" style="justify-content:center;text-align:center">'
                        f'<span class="pf-ticker">{row["year"]}</span>'
                        f'{cells}'
                        f'</div>'
                    )
                cards_html += '</div>'

                # ── Total Profit line chart ──
                chart_years = [row["year"] for row in rows_yr]
                LINE_COLORS = ["#81b29a", "#86868b", "#e07a5f", "#f2cc8f"]

                def _cumulative(yearly_pcts):
                    """Compound yearly % returns into cumulative %."""
                    cum = []
                    factor = 1.0
                    for pct in yearly_pcts:
                        if pct is None:
                            cum.append(None)
                        else:
                            factor *= (1 + pct / 100)
                            cum.append(round((factor - 1) * 100, 1))
                    return cum

                cum_port = _cumulative([row["portfolio"] for row in rows_yr])

                fig_yr = go.Figure()
                fig_yr.add_trace(go.Scatter(
                    x=chart_years,
                    y=cum_port,
                    name="Portfolio",
                    mode="lines+markers",
                    line=dict(color=LINE_COLORS[0], width=3),
                    marker=dict(size=7),
                ))
                for idx, bench_name in enumerate(bench_returns):
                    cum_bench = _cumulative([row.get(bench_name, None) for row in rows_yr])
                    fig_yr.add_trace(go.Scatter(
                        x=chart_years,
                        y=cum_bench,
                        name=bench_name,
                        mode="lines+markers",
                        line=dict(color=LINE_COLORS[(idx + 1) % len(LINE_COLORS)], width=2),
                        marker=dict(size=5),
                    ))
                fig_yr.add_hline(y=0, line_dash="dot", line_color=T['chart_zero'], line_width=1)
                fig_yr.update_layout(
                    hovermode="x unified",
                    yaxis_title="Cumulative Return %",
                    yaxis_ticksuffix="%",
                    xaxis=dict(
                        type="category",
                        gridcolor=T['chart_grid'],
                    ),
                    legend=dict(
                        orientation="h",
                        yanchor="bottom",
                        y=1.02,
                        xanchor="center",
                        x=0.5,
                        font=dict(size=12, color=T['chart_font']),
                    ),
                    margin=dict(t=40, b=20, l=40, r=20),
                    height=380,
                    font=dict(
                        family="-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
                        color=T['chart_font'],
                    ),
                    paper_bgcolor=T['chart_paper'],
                    plot_bgcolor=T['chart_plot'],
                    yaxis=dict(gridcolor=T['chart_grid'], zerolinecolor=T['chart_zero']),
                )

                st.markdown(
                    '<div class="performer-block">'
                    '<h4>Cumulative Returns vs Benchmarks</h4>',
                    unsafe_allow_html=True,
                )
                st.plotly_chart(fig_yr, use_container_width=True)
                st.markdown(cards_html, unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)
            else:
                st.info("Not enough history for yearly returns.")
        else:
            st.info("Net liq history unavailable for yearly returns.")

    # ── Returns per year / month ──
    MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    if nl_all and transfers is not None:
        # Reuse cached monthly/yearly returns computed above (same data source)
        monthly_returns = st.session_state.get("_cached_monthly_returns")
        yearly_returns = st.session_state.get("_cached_yearly_returns")
        if monthly_returns is None or yearly_returns is None:
            # Fallback: compute if cache unavailable (e.g. nl_all_early was None)
            df_ret = pd.DataFrame(nl_all)
            df_ret["time"] = pd.to_datetime(df_ret["time"])
            df_ret["year"] = df_ret["time"].dt.year
            df_ret["month"] = df_ret["time"].dt.month
            df_ret = df_ret.sort_values("time")
            month_last_close = df_ret.groupby(["year", "month"])["close"].last()
            month_periods = list(month_last_close.index)
            monthly_returns = {}
            for i in range(1, len(month_periods)):
                prev_yr, prev_mo = month_periods[i - 1]
                yr, mo = month_periods[i]
                start_val = month_last_close[(prev_yr, prev_mo)]
                end_val = month_last_close[(yr, mo)]
                yr_transfers = transfers.get(yr, {})
                mo_dep = yr_transfers.get("months", {}).get(mo, 0) if isinstance(yr_transfers, dict) else 0
                denom = start_val + 0.5 * mo_dep
                if denom > 0:
                    ret = (end_val - start_val - mo_dep) / denom * 100
                else:
                    ret = 0.0
                monthly_returns.setdefault(yr, {})[mo] = ret
            yearly_returns = {}
            for yr, months in monthly_returns.items():
                factor = 1.0
                for mo in sorted(months):
                    factor *= (1 + months[mo] / 100)
                yearly_returns[yr] = round((factor - 1) * 100, 1)

        # Round monthly returns for display only (work on a copy)
        monthly_returns = {yr: dict(months) for yr, months in monthly_returns.items()}
        for yr in monthly_returns:
            for mo in monthly_returns[yr]:
                monthly_returns[yr][mo] = round(monthly_returns[yr][mo], 1)

        # ── Weekly returns (TWR, deposit-adjusted like monthly) ──
        _df_wk = pd.DataFrame(nl_all)
        _df_wk["time"] = pd.to_datetime(_df_wk["time"])
        _df_wk = _df_wk.sort_values("time")
        _df_wk["iso_yr"] = _df_wk["time"].dt.isocalendar().year.astype(int)
        _df_wk["iso_wk"] = _df_wk["time"].dt.isocalendar().week.astype(int)
        _wk_last = _df_wk.groupby(["iso_yr", "iso_wk"]).agg(
            close=("close", "last"),
        )
        _wk_periods = list(_wk_last.index)
        weekly_returns = {}  # {(year, month): [(iso_wk, ret, wk_start, wk_end), ...]}
        for i in range(1, len(_wk_periods)):
            prev_iso = _wk_periods[i - 1]
            cur_iso = _wk_periods[i]
            start_val = _wk_last.loc[prev_iso, "close"]
            end_val = _wk_last.loc[cur_iso, "close"]
            # Use actual ISO week boundaries (Monday–Sunday) instead of data points
            from datetime import datetime as _dt_cls, timedelta as _td_cls
            _iso_year, _iso_week = cur_iso
            wk_start = _dt_cls.strptime(f"{_iso_year}-W{_iso_week:02d}-1", "%G-W%V-%u")
            wk_end = wk_start + _td_cls(days=6)  # Sunday
            # Approximate weekly deposits from monthly data
            _wk_yr, _wk_mo = wk_start.year, wk_start.month
            yr_tr = transfers.get(_wk_yr, {})
            mo_dep_total = yr_tr.get("months", {}).get(_wk_mo, 0) if isinstance(yr_tr, dict) else 0
            # Spread monthly deposits evenly across ~4.3 weeks
            import calendar as _cal
            _days_in_mo = _cal.monthrange(_wk_yr, _wk_mo)[1]
            _wk_days = (wk_end - wk_start).days + 1
            wk_dep = mo_dep_total * (_wk_days / _days_in_mo) if _days_in_mo > 0 else 0
            denom = start_val + 0.5 * wk_dep
            if denom > 0:
                ret = (end_val - start_val - wk_dep) / denom * 100
            else:
                ret = 0.0
            key = (_wk_yr, _wk_mo)
            weekly_returns.setdefault(key, []).append((
                cur_iso[1], round(ret, 1), wk_start, wk_end
            ))

        # Compound annual returns — same method as benchmark lines in the chart
        _cum_factor = 1.0
        for yr in sorted(port_returns):
            _cum_factor *= (1 + port_returns[yr] / 100)
        total_return = round((_cum_factor - 1) * 100, 1)
        total_ret_cls = " pf-green" if total_return >= 0 else " pf-red"

        # ── Returns & Deposits side by side ──
        has_deposits = bool(transfers)
        sorted_transfers = sorted(transfers.items(), reverse=True) if has_deposits else []
        total_deposited = sum(v["total"] for v in transfers.values()) if has_deposits else 0
        total_dep_cls = " pf-green" if total_deposited >= 0 else " pf-red"

        col_ret, col_dep = st.columns(2)

        with col_ret:
            # Build month + week options for report picker
            _rpt_opts = []
            _rpt_map = {}  # label → ("month", yr, mo) or ("week", yr, mo, iso_wk, wk_start, wk_end)
            for _yr in sorted(yearly_returns, reverse=True):
                for _mo in range(12, 0, -1):
                    _mr = monthly_returns.get(_yr, {}).get(_mo)
                    if _mr is not None:
                        # Month entry (uppercase to distinguish)
                        _lbl = f"▸ {MONTH_NAMES[_mo]} {_yr}"
                        _rpt_opts.append(_lbl)
                        _rpt_map[_lbl] = ("month", _yr, _mo)
                        # Week entries under this month
                        _wks = weekly_returns.get((_yr, _mo), [])
                        for _iso_wk, _wk_ret, _ws, _we in sorted(_wks, key=lambda x: x[2], reverse=True):
                            _wk_lbl = f"    W{_iso_wk}: {_ws.strftime('%b %d')}–{_we.strftime('%d')}"
                            _rpt_opts.append(_wk_lbl)
                            _rpt_map[_wk_lbl] = ("week", _yr, _mo, _iso_wk, _ws, _we)

            # Returns header with inline report picker
            st.markdown(
                f'<div class="section-title-bar" style="margin-bottom:0">Returns &nbsp;<span style="font-weight:400;font-size:0.85rem;color:{T["text_muted"]}">'
                f'Cumulative: <span class="pf-val{total_ret_cls}" style="font-size:0.85rem">{total_return:+.1f}%</span>'
                f'</span></div>',
                unsafe_allow_html=True,
            )
            with st.container(key="ret_pick_wrap"):
                @st.fragment
                def _report_picker():
                    sel = st.selectbox(
                        "report", _rpt_opts, index=None,
                        placeholder="View report...",
                        label_visibility="collapsed",
                    )
                    if sel:
                        _entry = _rpt_map[sel]
                        if _entry[0] == "month":
                            _show_month_detail(_entry[1], _entry[2], cost_basis, nl_all, transfers, monthly_returns, T)
                        else:
                            _, _yr, _mo, _iso_wk, _ws, _we = _entry
                            _show_week_detail(_yr, _iso_wk, _ws, _we, cost_basis, nl_all, transfers, weekly_returns, T)
                _report_picker()

            # Returns: HTML <details> per year (identical to deposits)
            for yr in sorted(yearly_returns, reverse=True):
                yr_ret = yearly_returns[yr]
                yr_color = T['accent'] if yr_ret >= 0 else T['red']
                mo_html = ""
                for mo in range(1, 13):
                    mo_ret = monthly_returns.get(yr, {}).get(mo)
                    if mo_ret is None:
                        continue
                    mo_color = T['accent'] if mo_ret >= 0 else T['red']
                    mo_html += (
                        f'<div style="border-left:3px solid {mo_color};padding:6px 12px;margin-bottom:2px">'
                        f'<span style="font-weight:600;color:{T["text"]}">{MONTH_NAMES[mo]}</span> &nbsp; '
                        f'<span style="color:{mo_color};font-weight:600">{mo_ret:+.1f}%</span>'
                        f'</div>'
                    )
                st.markdown(
                    f'<details class="yr-details" style="background:{T["card"]};border:1px solid {T["border"]};border-left:3px solid {yr_color};'
                    f'border-radius:8px;padding:10px 14px;margin-bottom:6px">'
                    f'<summary style="font-weight:600;color:{T["text"]}">'
                    f'{yr} \u2014 <span style="color:{yr_color}">{yr_ret:+.1f}%</span></summary>'
                    f'<div style="margin-top:8px">{mo_html}</div>'
                    f'</details>',
                    unsafe_allow_html=True)


        with col_dep:
            if has_deposits:
                st.markdown(
                    f'<div class="section-title-bar dep-title-bar">Deposits &nbsp;<span style="font-weight:400;font-size:0.85rem;color:{T["text_muted"]}">'
                    f'Total: <span class="pf-val{total_dep_cls}" style="font-size:0.85rem">${total_deposited:+,.0f}</span>'
                    f'</span></div>',
                    unsafe_allow_html=True,
                )
                for yr, yr_data in sorted_transfers:
                    amount = yr_data["total"]
                    months = yr_data.get("months", {})
                    dep_color = T['accent'] if amount >= 0 else T['red']
                    mo_html = ""
                    for mo in range(1, 13):
                        mo_val = months.get(mo)
                        if mo_val is None:
                            continue
                        mo_color = T['accent'] if mo_val >= 0 else T['red']
                        mo_html += (
                            f'<div style="border-left:3px solid {mo_color};padding:6px 12px;margin-bottom:2px">'
                            f'<span style="font-weight:600;color:{T["text"]}">{MONTH_NAMES[mo]}</span> &nbsp; '
                            f'<span style="color:{mo_color};font-weight:600">${mo_val:+,.0f}</span>'
                            f'</div>'
                        )
                    st.markdown(
                        f'<details class="yr-details" style="background:{T["card"]};border:1px solid {T["border"]};border-left:3px solid {dep_color};'
                        f'border-radius:8px;padding:10px 14px;margin-bottom:6px">'
                        f'<summary style="font-weight:600;color:{T["text"]}">'
                        f'{yr} \u2014 <span style="color:{dep_color}">${amount:+,.0f}</span></summary>'
                        f'<div style="margin-top:8px">{mo_html}</div>'
                        f'</details>',
                        unsafe_allow_html=True)

    st.markdown("")

    # ── Per-ticker cards (sorted by Total P/L, best first) ──
    with st.expander(f"All Positions ({len(sorted_tickers)})"):
        def _fmt_result_cell(col, val):
            cls = ""
            if col in ("Options P/L", "Total P/L"):
                cls = " pf-green" if val > 0 else " pf-red" if val < 0 else ""
            if col in ("Options P/L", "Equity Cost", "Total P/L", "Dividends"):
                return f"${val:+,.0f}" if col in ("Options P/L", "Total P/L") else f"${val:,.0f}", cls
            if col == "Mkt Value":
                return f"${val:,.0f}", cls
            if col == "Wheels":
                return f"{val}", cls
            return f"{val}", cls

        result_cols = ["Wheels", "Options P/L", "Equity Cost", "Mkt Value", "Total P/L", "Dividends"]

        rows = []
        for ticker, data in sorted_tickers:
            wheels = data.get("wheels", [])
            completed = sum(1 for w in wheels if w["status"] == "completed")
            active = any(w["status"] == "active" for w in wheels)
            wheel_str = str(completed) + (" +1 active" if active else "")
            rows.append({
                "Logo": f"https://assets.parqet.com/logos/symbol/{ticker}",
                "Ticker": ticker,
                "Wheels": wheel_str,
                "Options P/L": data["option_pl"],
                "Equity Cost": data["equity_cost"],
                "Mkt Value": data["market_value"],
                "Total P/L": data["total_pl_real"],
                "Dividends": data["dividends"],
            })

        cards_html = '<div class="portfolio-cards">'
        for row in rows:
            cells = ""
            for col in result_cols:
                fval, cls = _fmt_result_cell(col, row[col])
                cells += (
                    f'<div class="pf-cell">'
                    f'<span class="pf-label">{col}</span>'
                    f'<span class="pf-val{cls}">{fval}</span>'
                    f'</div>'
                )
            cards_html += (
                f'<div class="portfolio-card">'
                f'<img class="pf-logo" src="{row["Logo"]}" onerror="this.style.display=\'none\'">'
                f'<span class="pf-ticker">{row["Ticker"]}</span>'
                f'{cells}'
                f'</div>'
            )
        cards_html += '</div>'

        st.markdown(cards_html, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════
#  SETTINGS PAGE — Per-user configuration
# ══════════════════════════════════════════════════════

elif page == "Connect your Broker":

    st.markdown(
        "<style>.block-container { max-width: 700px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )
    st.markdown("## Connect your Broker")

    # ── Tastytrade connection ──
    st.markdown("### Tastytrade")

    # Handle OAuth redirect results (read from session state — query params
    # were already captured and cleared in the nav section to avoid the rerun
    # race that st.query_params.clear() triggers in Streamlit ≥1.37).
    _oauth_result = st.session_state.pop("_tt_oauth_result", None)
    if _oauth_result == "success":
        log_page_view(_sb_client, "broker_connect:tastytrade:success")
        st.success("Tastytrade connected successfully!")
        st.session_state.pop("tt_refresh_token", None)  # force reload from DB
        st.session_state.pop("_account_page", None)
        st.rerun()
    elif _oauth_result == "access_denied":
        log_page_view(_sb_client, "broker_connect:tastytrade:error:access_denied")
        st.error("Connection was cancelled. Click 'Connect with Tastytrade' to try again.")
    elif _oauth_result == "connection_failed":
        log_page_view(_sb_client, "broker_connect:tastytrade:error:connection_failed")
        st.error("Could not connect to Tastytrade. Please try again.")
    elif _oauth_result == "session_expired":
        log_page_view(_sb_client, "broker_connect:tastytrade:error:session_expired")
        st.error("Your login session timed out. Please try connecting again.")
    elif _oauth_result == "token_exchange_failed":
        log_page_view(_sb_client, "broker_connect:tastytrade:error:token_exchange_failed")
        st.error("Tastytrade rejected the connection request. Please try again.")
    elif _oauth_result == "storage_failed":
        log_page_view(_sb_client, "broker_connect:tastytrade:error:storage_failed")
        st.error("Connected to Tastytrade but failed to save credentials. Please try again.")

    _existing_token = _get_tt_token()
    if _existing_token:
        st.success("Tastytrade account connected.")
        if st.button("Disconnect Tastytrade", type="primary"):
            delete_credential(_sb_client, "tastytrade_refresh_token")
            st.session_state.pop("tt_refresh_token", None)
            for k in ["portfolio_data", "portfolio_account", "portfolio_prices",
                       "net_liq_all", "yearly_transfers", "benchmark_returns",
                       "portfolio_fetched_at"]:
                st.session_state.pop(k, None)
            for k in [k for k in st.session_state if k.startswith("net_liq_")]:
                st.session_state.pop(k, None)
            st.rerun()
    else:
        st.info("Connect your Tastytrade account to view your portfolio, cost basis, and options data. "
                "Click the button below to log in securely via Tastytrade — we only request **read-only** access.")

        # OAuth connect button
        _oauth_url = os.environ.get("OAUTH_SERVER_URL", "http://localhost:8000")
        _user = st.session_state.get("user")
        _user_id = _user["id"] if _user and isinstance(_user, dict) else ""
        if _user_id:
            _connect_url = f"{_oauth_url}/auth/tastytrade/login?user_id={_user_id}"
            st.link_button("Connect with Tastytrade", _connect_url, type="primary")
            st.caption("You'll be redirected to Tastytrade to log in. We never see your password.")

        st.caption("Having trouble? Contact us at support@lazytheta.io")

    # ── Interactive Brokers connection ──
    st.markdown("---")
    st.markdown("### Interactive Brokers")
    _ibkr_creds = _get_ibkr_credentials()
    if _ibkr_creds:
        st.success("Interactive Brokers account connected.")
        if st.button("Disconnect Interactive Brokers", type="primary"):
            delete_ibkr_credentials(_sb_client)
            st.session_state.pop("ibkr_credentials", None)
            st.session_state.pop("_ibkr_flex_cache", None)
            if get_active_broker() == "ibkr":
                st.session_state.pop("active_broker", None)
            for k in ["portfolio_data", "portfolio_account", "portfolio_prices",
                       "net_liq_all", "yearly_transfers", "benchmark_returns",
                       "portfolio_fetched_at"]:
                st.session_state.pop(k, None)
            for k in [k for k in st.session_state if k.startswith("net_liq_")]:
                st.session_state.pop(k, None)
            st.rerun()
    else:
        st.info("Connect your Interactive Brokers account to view your portfolio, cost basis, and options data. "
                "We use **read-only** Flex Query access — this app cannot place trades or modify your account in any way.")
        with st.expander("How to set up your IBKR Flex Query", expanded=True):
            st.markdown(
                "**Step 1 — Create a Flex Query:**\n"
                "1. Log in to [Client Portal](https://www.interactivebrokers.com/portal)\n"
                "2. Go to **Performance & Reports → Flex Queries**\n"
                "3. Click **+ Create** under Activity Flex Queries\n"
                "4. Give it a name (e.g. *Lazy Theta*)\n"
                "5. In **Sections**, click each of these and select all fields:\n"
                "   - **Open Positions**\n"
                "   - **Trades**\n"
                "   - **Cash Transactions**\n"
                "   - **Net Asset Value (NAV) Summary in Base**\n"
                "   - **Change in NAV**\n"
                "   - **Account Information**\n"
                "6. Under **Delivery Configuration**, set the period to **Last 365 Calendar Days**\n"
                "7. Set format to **XML**\n"
                "8. Save the query — note the **Query ID** number\n\n"
                "**Step 2 — Enable the Flex Web Service:**\n"
                "1. Go to **Performance & Reports → Flex Queries**\n"
                "2. Click the **⚙ gear icon** next to Flex Web Service\n"
                "3. Toggle it **on** and copy the **token** shown\n\n"
                "**Step 3 — Paste both values below:**"
            )
        with st.form("ibkr_creds_form"):
            _ibkr_token = st.text_input("Flex Web Service Token", type="password",
                                        placeholder="Your Flex Web Service token")
            _ibkr_query_id = st.text_input("Flex Query ID",
                                           placeholder="e.g. 123456")
            _ibkr_submitted = st.form_submit_button("Save", type="primary")

        if _ibkr_submitted and _ibkr_token and _ibkr_query_id:
            if not _ibkr_query_id.strip().isdigit():
                log_page_view(_sb_client, "broker_connect:ibkr:error:invalid_query_id")
                st.error("Flex Query ID must be numeric (e.g. 123456).")
            else:
                _creds = {
                    "ibkr_flex_token": _ibkr_token.strip(),
                    "ibkr_flex_query_id": _ibkr_query_id.strip(),
                }
                save_ibkr_credentials(_sb_client, _creds)
                st.session_state["ibkr_credentials"] = _creds
                # Clear stale Flex cache so new credentials are used immediately
                st.session_state.pop("_ibkr_flex_cache", None)
                log_page_view(_sb_client, "broker_connect:ibkr:success")
                st.success("Interactive Brokers connected.")
                st.rerun()

# ══════════════════════════════════════════════════════
#  SECURITY & PRIVACY PAGE
# ══════════════════════════════════════════════════════

elif page == "🔒 Security & Privacy":

    st.markdown(
        f"""<style>
        .block-container {{ max-width: 800px; margin: auto; }}
        /* Force Streamlit columns to stretch to equal height */
        [data-testid="stHorizontalBlock"]:has(.sec-card) {{
            align-items: stretch;
        }}
        [data-testid="stHorizontalBlock"]:has(.sec-card) [data-testid="stColumn"] {{
            height: auto !important;
        }}
        [data-testid="stHorizontalBlock"]:has(.sec-card) [data-testid="stColumn"] div {{
            height: 100%;
        }}
        .sec-card {{
            background: {T['card']};
            border-radius: 18px;
            padding: 28px 24px;
            box-shadow: {T['shadow']};
            height: 100%;
            display: flex;
            flex-direction: column;
            justify-content: flex-start;
            animation: fadeInUp 0.4s ease-out both;
        }}
        .sec-card h4 {{
            font-family: 'DM Serif Display', Georgia, serif;
            color: {T['text']};
            font-weight: 400;
            font-size: 1.1rem;
            margin: 12px 0 8px 0;
        }}
        .sec-card p {{
            color: {T['text_muted']};
            font-size: 0.88rem;
            line-height: 1.6;
            margin: 0;
            flex: 1;
        }}
        .sec-card a {{
            color: {T['accent']};
            text-decoration: none;
            font-weight: 500;
            font-size: 0.85rem;
        }}
        .sec-card a:hover {{ text-decoration: underline; }}
        .sec-icon {{
            font-size: 1.8rem;
            display: block;
        }}
        .sec-badge {{
            background: {T['card']};
            border: 1px solid {T['border_light']};
            border-radius: 980px;
            padding: 10px 0;
            text-align: center;
            font-size: 0.82rem;
            font-weight: 500;
            color: {T['text']};
        }}
        </style>""",
        unsafe_allow_html=True,
    )
    st.markdown("## 🔒 Security & Privacy")

    # ── Hero section ──
    st.markdown(
        f"""<div style="
            background: {T['card']};
            border-radius: 24px;
            border-top: 3px solid {T['accent']};
            padding: 36px 32px;
            box-shadow: {T['shadow']};
            text-align: center;
            margin-bottom: 24px;
            animation: fadeInUp 0.4s ease-out both;
        ">
            <p style="font-size: 1.6rem; margin: 0 0 8px 0;">🛡️</p>
            <p style="
                color: {T['text']};
                font-size: 1.05rem;
                font-weight: 500;
                margin: 0;
                line-height: 1.5;
            ">We never sell or share your data.</p>
            <p style="
                color: {T['text_muted']};
                font-size: 0.9rem;
                margin: 6px 0 0 0;
            ">Your account is isolated with Row Level Security, and we only store the minimum needed to run the app.</p>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── Three columns ──
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown(
            f"""<div class="sec-card" style="animation-delay: 0.05s;">
                <span class="sec-icon">🗄️</span>
                <h4>Minimal Data Storage</h4>
                <p>We store only what's needed: your watchlist configs, preferences, and
                broker connection tokens. Portfolio data and analysis results are fetched
                live each session — we don't keep copies of your financial data.</p>
            </div>""",
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            f"""<div class="sec-card" style="animation-delay: 0.1s;">
                <span class="sec-icon">👁️‍🗨️</span>
                <h4>No Tracking</h4>
                <p>We run zero analytics, zero cookies, zero third-party tracking scripts.
                No Google Analytics, no Mixpanel, no pixel trackers.
                Your usage is your business.</p>
            </div>""",
            unsafe_allow_html=True,
        )

    with col3:
        st.markdown(
            f"""<div class="sec-card" style="animation-delay: 0.15s;">
                <span class="sec-icon">🔓</span>
                <h4>Open Source</h4>
                <p>Our entire codebase is publicly available on GitHub.
                Every line of code can be inspected, audited, and verified.
                Transparency is our default.</p>
                <a href="{GITHUB_REPO_URL}" target="_blank">View on GitHub →</a>
            </div>""",
            unsafe_allow_html=True,
        )

    st.markdown('<div style="height: 12px"></div>', unsafe_allow_html=True)

    # ── Expander sections ──
    with st.expander("How your data flows"):
        st.markdown(
            "1. You sign in — your account is managed by Supabase with Row Level Security\n"
            "2. Watchlist configs and preferences are stored in Supabase, isolated per user\n"
            "3. Market data is fetched live from public APIs (SEC EDGAR, Yahoo Finance)\n"
            "4. Portfolio data is fetched from Tastytrade using your stored refresh token\n"
            "5. Calculations (DCF, Greeks, P&L) run server-side in your Streamlit session\n"
            "6. Results are displayed — raw portfolio data is not persisted\n"
            "7. When you close the tab, session-level data (fetched prices, calculations) is cleared"
        )

    with st.expander("What about the Tastytrade integration?"):
        st.markdown(
            "The Tastytrade integration uses **OAuth 2.0** — the same standard your bank uses. "
            "This means:\n\n"
            "- You authenticate directly with Tastytrade (we never see your password)\n"
            "- We store a **read-only** refresh token, encrypted in Supabase with per-user isolation\n"
            "- The token only grants read access — this app cannot place trades or modify your account\n"
            "- You can revoke access at any time from your Tastytrade account or disconnect in Connect your Broker\n\n"
            "We will never request write/trade permissions unless you explicitly enable this."
        )

    with st.expander("What we store"):
        st.markdown(
            "Stored **per-user** in Supabase (isolated via Row Level Security):\n\n"
            "- **Watchlist configs** — your saved DCF configurations per ticker\n"
            "- **User preferences** — display settings\n"
            "- **Tastytrade refresh token** — encrypted, read-only, revocable\n\n"
            "**Not** stored:\n\n"
            "- Portfolio positions, balances, or transaction history (fetched live each session)\n"
            "- Market data or stock prices\n"
            "- DCF calculation results\n"
            "- Your Tastytrade password"
        )

    with st.expander("HTTPS & Infrastructure"):
        st.markdown(
            "This app runs on **Streamlit Community Cloud** with enforced HTTPS/TLS encryption. "
            "All data in transit between your browser and the app is encrypted. "
            "The hosting infrastructure is managed by Streamlit (Snowflake) with SOC 2 compliance."
        )

    with st.expander("What we'd need to improve for production"):
        st.markdown(
            "We believe in transparency about what's not yet perfect:\n\n"
            "- **Custom security headers** (CSP, HSTS) — not configurable on Streamlit Cloud\n"
            "- **Rate limiting on API calls** — planned for future release\n"
            "- **Formal security audit** — planned before any paid tier launch"
        )

    st.markdown('<div style="height: 8px"></div>', unsafe_allow_html=True)

    # ── Trust badges ──
    b1, b2, b3, b4 = st.columns(4)
    with b1:
        st.markdown('<div class="sec-badge">🔒 HTTPS Encrypted</div>', unsafe_allow_html=True)
    with b2:
        st.markdown('<div class="sec-badge">🚫 No Data Selling</div>', unsafe_allow_html=True)
    with b3:
        st.markdown('<div class="sec-badge">📖 Open Source</div>', unsafe_allow_html=True)
    with b4:
        st.markdown('<div class="sec-badge">🛡️ Per-User Isolation</div>', unsafe_allow_html=True)

    st.markdown('<div style="height: 4px"></div>', unsafe_allow_html=True)

    # ── Legal links ──
    _lc, _rc = st.columns(2)
    with _lc:
        if st.button("Privacy Policy", use_container_width=True, type="primary"):
            st.session_state["_account_page"] = "Privacy Policy"
            st.rerun()
    with _rc:
        if st.button("Terms of Service", use_container_width=True, type="primary"):
            st.session_state["_account_page"] = "Terms of Service"
            st.rerun()

    # ── Footer ──
    st.caption(
        f"Last updated: {date.today().strftime('%B %d, %Y')}. "
        f"Questions about our security practices? "
        f"[Open an issue on GitHub]({GITHUB_REPO_URL}/issues) "
        f"or reach out at {CONTACT_EMAIL}."
    )

elif page == "Privacy Policy":

    st.markdown(
        f"""<style>
        .legal-container {{ max-width: 800px; margin: auto; }}
        .legal-card {{
            background: {T['card']};
            border-radius: 18px;
            border-top: 3px solid {T['accent']};
            padding: 32px 28px;
            box-shadow: {T['shadow']};
            margin-bottom: 16px;
        }}
        .legal-card h4 {{
            font-family: 'DM Serif Display', Georgia, serif;
            color: {T['text']};
            font-weight: 400;
            font-size: 1.15rem;
            margin: 0 0 12px 0;
        }}
        .legal-card p, .legal-card li {{
            color: {T['text_muted']};
            font-size: 0.92rem;
            line-height: 1.6;
        }}
        .legal-card table {{
            width: 100%;
            font-size: 0.88rem;
            border-collapse: collapse;
        }}
        .legal-card th {{
            text-align: left;
            color: {T['text']};
            border-bottom: 1px solid {T['border']};
            padding: 6px 8px;
        }}
        .legal-card td {{
            color: {T['text_muted']};
            border-bottom: 1px solid {T['border']};
            padding: 6px 8px;
        }}
        .legal-card a {{ color: {T['accent']}; text-decoration: none; }}
        .legal-card a:hover {{ text-decoration: underline; }}
        </style>""",
        unsafe_allow_html=True,
    )

    st.markdown(f'<p style="font-family: \'DM Serif Display\', Georgia, serif; font-size: 2rem; color: {T["text"]}; margin-bottom: 4px;">Privacy Policy</p>', unsafe_allow_html=True)
    st.caption(f"Effective date: March 4, 2026 — Last updated: {date.today().strftime('%B %d, %Y')}")

    st.markdown(f"""<div class="legal-card">
<h4>1. Who we are</h4>
<p>Lazy Theta ("we", "us") operates the stock analysis platform at <a href="https://lazytheta.io">lazytheta.io</a>.<br>
Contact: <a href="mailto:info@lazytheta.io">info@lazytheta.io</a></p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>2. What we collect</h4>
<table>
<tr><th>Data</th><th>Purpose</th><th>Stored where</th></tr>
<tr><td>Email address</td><td>Account login</td><td>Supabase Auth</td></tr>
<tr><td>Name, title, date of birth, country</td><td>Account profile</td><td>Supabase Auth metadata</td></tr>
<tr><td>Password</td><td>Authentication (hashed, we never see it)</td><td>Supabase Auth</td></tr>
<tr><td>Watchlist configurations</td><td>Save your DCF valuations</td><td>Supabase database</td></tr>
<tr><td>Display preferences</td><td>Remember your settings</td><td>Supabase database</td></tr>
<tr><td>Tastytrade refresh token</td><td>Read-only portfolio access</td><td>Supabase database (encrypted at rest)</td></tr>
</table>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>3. What we do NOT collect</h4>
<ul>
<li>Portfolio positions, balances, or transaction history (fetched live, never stored)</li>
<li>Market data or stock prices</li>
<li>DCF calculation results</li>
<li>Your Tastytrade password</li>
<li>Analytics, cookies, or tracking data of any kind</li>
</ul>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>4. How we protect your data</h4>
<ul>
<li>All data is isolated per user via <strong>Row Level Security</strong> (RLS)</li>
<li>All connections are <strong>HTTPS encrypted</strong></li>
<li>Passwords are hashed by Supabase Auth (bcrypt) &mdash; we never store or see plaintext passwords</li>
<li>Tastytrade tokens are <strong>read-only</strong> and revocable from your Tastytrade account at any time</li>
</ul>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>5. Third-party services</h4>
<table>
<tr><th>Service</th><th>Purpose</th><th>Privacy policy</th></tr>
<tr><td>Supabase</td><td>Authentication &amp; database</td><td><a href="https://supabase.com/privacy">supabase.com/privacy</a></td></tr>
<tr><td>Streamlit Cloud</td><td>App hosting</td><td><a href="https://streamlit.io/privacy-policy">streamlit.io/privacy-policy</a></td></tr>
<tr><td>SEC EDGAR</td><td>Financial statements</td><td>Public government data</td></tr>
<tr><td>Tastytrade</td><td>Portfolio data (opt-in)</td><td><a href="https://tastytrade.com/privacy-policy">tastytrade.com/privacy-policy</a></td></tr>
</table>
<p>We do <strong>not</strong> use Google Analytics, Mixpanel, or any tracking service.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>6. Your rights</h4>
<p>You can at any time:</p>
<ul>
<li><strong>View</strong> your data in the app (Connect your Broker page)</li>
<li><strong>Delete</strong> your session data (Clear Session Data button)</li>
<li><strong>Revoke</strong> Tastytrade access from your Tastytrade account</li>
<li><strong>Request deletion</strong> of your account and all data by emailing <a href="mailto:info@lazytheta.io">info@lazytheta.io</a></li>
</ul>
<p>Under GDPR (EU) and similar regulations, you also have the right to data portability and to lodge a complaint with your local data protection authority.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>7. Data retention</h4>
<ul>
<li>Account data is retained as long as your account exists</li>
<li>Session data (portfolio, calculations) is destroyed when you close the browser tab</li>
<li>We do not keep backups of session data</li>
</ul>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>8. Changes</h4>
<p>We may update this policy. Material changes will be communicated via the app. Continued use after changes constitutes acceptance.</p>
</div>""", unsafe_allow_html=True)

    if st.button("Back to Security & Privacy", type="primary"):
        st.session_state["_account_page"] = "🔒 Security & Privacy"
        st.rerun()

elif page == "Terms of Service":

    st.markdown(
        f"""<style>
        .legal-container {{ max-width: 800px; margin: auto; }}
        .legal-card {{
            background: {T['card']};
            border-radius: 18px;
            border-top: 3px solid {T['accent']};
            padding: 32px 28px;
            box-shadow: {T['shadow']};
            margin-bottom: 16px;
        }}
        .legal-card h4 {{
            font-family: 'DM Serif Display', Georgia, serif;
            color: {T['text']};
            font-weight: 400;
            font-size: 1.15rem;
            margin: 0 0 12px 0;
        }}
        .legal-card p, .legal-card li {{
            color: {T['text_muted']};
            font-size: 0.92rem;
            line-height: 1.6;
        }}
        .legal-card a {{ color: {T['accent']}; text-decoration: none; }}
        .legal-card a:hover {{ text-decoration: underline; }}
        </style>""",
        unsafe_allow_html=True,
    )

    st.markdown(f'<p style="font-family: \'DM Serif Display\', Georgia, serif; font-size: 2rem; color: {T["text"]}; margin-bottom: 4px;">Terms of Service</p>', unsafe_allow_html=True)
    st.caption(f"Effective date: March 4, 2026 — Last updated: {date.today().strftime('%B %d, %Y')}")

    st.markdown(f"""<div class="legal-card">
<h4>1. Acceptance</h4>
<p>By creating an account or using Lazy Theta ("the Service"), you agree to these terms.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>2. What the Service provides</h4>
<p>Lazy Theta is a stock analysis and portfolio management tool for personal, informational use. It provides:</p>
<ul>
<li>DCF valuation models based on public SEC filings</li>
<li>Portfolio overview via Tastytrade API integration</li>
<li>Wheel strategy cost basis tracking</li>
</ul>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>3. Not financial advice</h4>
<p><strong>The Service does not provide financial, investment, tax, or legal advice.</strong> All valuations, calculations, and data are for informational purposes only. You are solely responsible for your investment decisions. We are not a registered investment adviser, broker-dealer, or financial planner.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>4. Your account</h4>
<ul>
<li>You must provide accurate information when creating an account</li>
<li>You are responsible for keeping your credentials secure</li>
<li>One account per person</li>
<li>We may suspend or terminate accounts that violate these terms</li>
</ul>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>5. Acceptable use</h4>
<p>You agree not to:</p>
<ul>
<li>Use the Service for any illegal purpose</li>
<li>Attempt to access other users' data</li>
<li>Reverse-engineer, scrape, or overload the Service</li>
<li>Use automated tools to access the Service beyond normal use</li>
</ul>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>6. Data accuracy</h4>
<ul>
<li>Financial data is sourced from SEC EDGAR, Yahoo Finance, and Tastytrade</li>
<li>We do not guarantee the accuracy, completeness, or timeliness of any data</li>
<li>DCF valuations are models with assumptions &mdash; they are not predictions of future stock prices</li>
</ul>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>7. Availability</h4>
<p>The Service is provided "as is" on Streamlit Cloud. We do not guarantee uptime or availability. We may modify or discontinue the Service at any time.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>8. Limitation of liability</h4>
<p>To the maximum extent permitted by law, Lazy Theta and its operators shall not be liable for any indirect, incidental, special, or consequential damages, including but not limited to financial losses from investment decisions made using the Service.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>9. Intellectual property</h4>
<p>The source code is available on <a href="{GITHUB_REPO_URL}">GitHub</a>. All rights reserved unless otherwise specified. You may not copy, modify, or redistribute the code without permission.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>10. Changes</h4>
<p>We may update these terms. Continued use after changes constitutes acceptance. Material changes will be communicated via the app.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>11. Governing law</h4>
<p>These terms are governed by the laws of the Netherlands.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>12. Contact</h4>
<p>Questions? Email <a href="mailto:info@lazytheta.io">info@lazytheta.io</a>.</p>
</div>""", unsafe_allow_html=True)

    if st.button("Back to Security & Privacy", type="primary"):
        st.session_state["_account_page"] = "🔒 Security & Privacy"
        st.rerun()

