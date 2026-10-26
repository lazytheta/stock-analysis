"""Temporary per-page load profiler.

Lives in its own module so removing it later is deleting one file and a
handful of call sites, rather than unpicking timers from thirteen thousand
lines. Added 2026-08-27 after the Portfolio page turned out to be spending 32
of its 42 seconds waiting out a rate limit on one endpoint it asked for twice
— a cause that survived three plausible wrong theories and only fell to
measurement.

What it shows per page:
  • wall clock for the whole page, and for whatever blocks a page names
  • Trading 212 per endpoint: calls, 429s, seconds waited
  • Tastytrade sessions opened (each one is an OAuth exchange)
  • Yahoo quote round trips

The counters are what caught the bug: a repeated call is invisible in a total
and obvious in a per-endpoint tally.
"""

import contextlib
import time

import streamlit as st

import gather_data
import t212_api
import tastytrade_api

_STEPS_KEY = "_lp_steps"
_START_KEY = "_lp_start"
_PAGE_KEY = "_lp_page"

# Logo lookups. Cheap on Portfolio (11 calls, 0.49s) but Screener and Watchlist
# draw dozens, and each symbol without an ISIN is a HEAD request to parqet with
# a 3-second timeout before the day-long cache takes over.
LOGOS = {"calls": 0, "seconds": 0.0}


def count_logo(seconds):
    LOGOS["calls"] += 1
    LOGOS["seconds"] += seconds


def start(page):
    """Begin timing a page render. Resets every counter this module reads."""
    st.session_state[_STEPS_KEY] = []
    st.session_state[_START_KEY] = time.perf_counter()
    st.session_state[_PAGE_KEY] = page
    t212_api.reset_call_stats()
    tastytrade_api.reset_call_stats()
    LOGOS.update({"calls": 0, "seconds": 0.0})
    gather_data.reset_daily_closes_stats()


def mark(label, seconds):
    """Record a block that was timed by hand."""
    st.session_state.setdefault(_STEPS_KEY, []).append((label, seconds))


@contextlib.contextmanager
def timed(label):
    """Time a block and record it under `label`."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        mark(label, time.perf_counter() - t0)


def _table(rows, headers):
    head = "| " + " | ".join(headers) + " |\n|" + "---|" * len(headers) + "\n"
    return head + "\n".join("| " + " | ".join(r) + " |" for r in rows)


def panel():
    """Render the diagnostic expander. Safe to call on a page that never
    started timing — it simply shows nothing."""
    start_t = st.session_state.get(_START_KEY)
    if start_t is None:
        return
    total = time.perf_counter() - start_t
    steps = st.session_state.get(_STEPS_KEY) or []
    measured = sum(s for _, s in steps)

    with st.expander(f"⏱ Laadtijd {total:.1f}s (tijdelijk)", expanded=False):
        rows = [(n, f"{s:.2f}s") for n, s in steps]
        if steps:
            rows.append(("overig (opmaak, widgets)",
                         f"{max(0.0, total - measured):.2f}s"))
        rows.append(("**hele pagina**", f"**{total:.2f}s**"))
        st.markdown(_table(rows, ["blok", "tijd"]))

        t212 = t212_api.LAST_CALL_STATS
        by_path = t212.get("by_path") or {}
        if by_path:
            st.markdown("**Trading 212 per endpoint**")
            st.markdown(_table(
                [(f"`{p}`", str(r["n"]), str(r["rate_limited"]),
                  f"{r['slept_s']:.1f}s")
                 for p, r in sorted(by_path.items(),
                                    key=lambda kv: -kv[1]["slept_s"])],
                ["endpoint", "calls", "429", "gewacht"],
            ))

        import quotes
        if quotes.LAST_RUN:
            st.markdown("**Koersen per bron** (laatste echte ophaalronde)")
            st.markdown(_table(
                [(name, str(asked), str(hits), f"{secs:.1f}s")
                 for name, asked, hits, secs in quotes.LAST_RUN],
                ["bron", "gevraagd", "gevonden", "tijd"],
            ))

        tt = tastytrade_api.CALL_STATS
        bits = []
        if tt.get("sessions"):
            # Every session is an OAuth exchange. More than one per page load
            # means the same handshake is being paid for repeatedly.
            bits.append(f"Tastytrade: {tt['sessions']} sessies "
                        f"({tt['session_s']:.1f}s)")
        if LOGOS["calls"]:
            bits.append(f"Logo's: {LOGOS['calls']} lookups "
                        f"({LOGOS['seconds']:.1f}s)")
        dc = gather_data.DAILY_CLOSES_STATS
        if dc["calls"]:
            # One request per candidate ticker per symbol, serially. The
            # Results page rebuilds its whole curve from these.
            bits.append(f"Yahoo dagkoersen: {dc['calls']} calls "
                        f"({dc['seconds']:.1f}s, {dc['empty']} leeg)")
        if tt.get("quote_calls"):
            bits.append(f"Yahoo: {tt['quote_calls']} quote-rondes "
                        f"over {tt['quote_symbols']} tickers "
                        f"({tt['quote_s']:.1f}s)")
        if bits:
            st.caption(" · ".join(bits))

        st.caption(
            "Alleen servertijd. Is dit laag terwijl het traag voelt, dan is het "
            "de app die uit slaapstand komt of de browser — niet deze code. "
            "Herhaalde calls naar hetzelfde endpoint zijn het patroon om op te letten."
        )
