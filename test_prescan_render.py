"""Parsing a pre-scan section into the parts a card needs."""

import unittest

from prescan_render import parse_verdict_section

_MOAT = """**Moat: Wide 🛡️ · Stable ➡️ · 4/5**

MercadoLibre's **wide moat** comes from an integrated ecosystem where
marketplace, payments, logistics and lending reinforce each other.

- **Ecosystem Flywheel**: more buyers attract sellers who adopt Pago
- **Regulatory Barriers**: licences across LatAm take years for entrants
- **Full-Stack Integration**: no competitor combines all four

**Weakest link:** no switching costs — buyers can leave tomorrow.

## Sources
[1] MELI 10-K - sec.gov
"""

_RISK = """**Risk: Medium 🟡 · Competition**

Performance running is crowded and HOKA's first-mover lead is narrowing.

- **Brand Concentration**: UGG and HOKA are 97% of revenue
- **Tariff Exposure**: 80bps of gross margin in FY2026
- **Fashion Cycles**: maximalist running could fall out of favour

**What would change this:** gross margin under 54% for two quarters.
"""


class TestMoat(unittest.TestCase):
    def setUp(self):
        self.v = parse_verdict_section(_MOAT)

    def test_the_headline_verdict_is_pulled_out(self):
        self.assertEqual(self.v["label"], "Wide")
        self.assertEqual(self.v["score"], 4.0)
        self.assertEqual(self.v["out_of"], 5.0)

    def test_the_qualifiers_ride_along(self):
        """Direction is part of the verdict — a wide moat narrowing is not the
        same call as a wide moat holding."""
        self.assertIn("Stable", self.v["qualifiers"])

    def test_the_summary_is_the_prose_before_the_bullets(self):
        self.assertTrue(self.v["summary"].startswith("MercadoLibre"))
        self.assertNotIn("- **", self.v["summary"])

    def test_the_bullets_come_back_split_from_their_labels(self):
        self.assertEqual(len(self.v["bullets"]), 3)
        self.assertEqual(self.v["bullets"][0]["label"], "Ecosystem Flywheel")
        self.assertIn("Pago", self.v["bullets"][0]["text"])

    def test_the_mandatory_closing_line_is_kept_separate(self):
        """Weakest link and "what would change this" are the two lines most
        worth not losing in a wall of text."""
        self.assertEqual(self.v["footer_label"], "Weakest link")
        self.assertIn("switching costs", self.v["footer_text"])

    def test_sources_stay_out_of_the_card(self):
        self.assertNotIn("sec.gov", self.v["summary"])
        self.assertNotIn("sec.gov", self.v["footer_text"])


class TestRisk(unittest.TestCase):
    def test_a_verdict_without_a_score_still_parses(self):
        """Risk is a three-level rating. There is no 0-5 score to show, and
        inventing one would put false precision on the card."""
        v = parse_verdict_section(_RISK)
        self.assertEqual(v["label"], "Medium")
        self.assertIsNone(v["score"])
        self.assertIn("Competition", v["qualifiers"])
        self.assertEqual(len(v["bullets"]), 3)
        self.assertEqual(v["footer_label"], "What would change this")


class TestOldFormat(unittest.TestCase):
    def test_a_section_in_the_previous_format_is_left_alone(self):
        """Sixty-odd tickers hold output from the old templates. Half-parsing
        it into a card would look like a rendering bug; returning None lets the
        caller fall back to plain markdown."""
        old = ("# 🏰 Moat Analysis: Deckers (DECK)\n"
               "  * **Moat Size:** Narrow 🤏\n"
               "  * **Summary:** Deckers possesses a narrow moat...\n"
               "## ⚓️ Switching Costs\n  * **Assessment:** ❌ Not Present\n")
        self.assertIsNone(parse_verdict_section(old))

    def test_empty_input(self):
        self.assertIsNone(parse_verdict_section(""))
        self.assertIsNone(parse_verdict_section(None))

    def test_a_verdict_line_with_no_bullets_is_not_a_card(self):
        """A card with an empty body is worse than the text it replaced."""
        self.assertIsNone(parse_verdict_section("**Moat: Wide 🛡️ · 4/5**\n\nJust prose.\n"))


class TestGauge(unittest.TestCase):
    def test_the_arc_fills_in_proportion_to_the_score(self):
        from prescan_render import gauge_fraction
        self.assertAlmostEqual(gauge_fraction(4.0, 5.0), 0.8)
        self.assertAlmostEqual(gauge_fraction(0.0, 5.0), 0.0)

    def test_a_score_beyond_the_scale_is_clamped(self):
        from prescan_render import gauge_fraction
        self.assertAlmostEqual(gauge_fraction(7.0, 5.0), 1.0)
        self.assertAlmostEqual(gauge_fraction(-1.0, 5.0), 0.0)


if __name__ == "__main__":
    unittest.main()


class TestThreeState(unittest.TestCase):
    """The weak / mixed / strong selector."""

    def test_the_active_state_is_the_one_named(self):
        from prescan_render import three_state_html
        html = three_state_html("robust")
        # The chosen circle is filled; the other two are outlines.
        self.assertEqual(html.count("data-active=\"1\""), 1)
        self.assertEqual(html.count("data-active=\"0\""), 2)

    def test_every_band_name_the_data_actually_uses_resolves(self):
        """robustness stores robust/mid/fragile, the scorecard green/yellow/red,
        and the verdict borderline. One vocabulary in, one widget out."""
        from prescan_render import three_state_html
        for name in ("robust", "mid", "fragile", "green", "yellow", "red",
                     "borderline", "strong", "weak"):
            html = three_state_html(name)
            self.assertEqual(html.count('data-active="1"'), 1, name)

    def test_an_unknown_band_lights_nothing(self):
        """Better three grey circles than confidently lighting the wrong one —
        an unrated axis is not the same as a middling one."""
        from prescan_render import three_state_html
        html = three_state_html("not-a-band")
        self.assertEqual(html.count('data-active="1"'), 0)

    def test_the_labels_can_be_renamed_per_question(self):
        """"Weak/Mixed/Strong" suits financial health; a moat is
        "None/Narrow/Wide". Same widget, question-specific words."""
        from prescan_render import three_state_html
        html = three_state_html("robust", labels=("None", "Narrow", "Wide"))
        self.assertIn("Wide", html)
        self.assertNotIn("STRONG", html.upper().replace("WIDE", ""))

    def test_it_escapes_what_it_is_given(self):
        from prescan_render import three_state_html
        html = three_state_html("mid", labels=("<b>x</b>", "b", "c"))
        self.assertNotIn("<b>x</b>", html)


class TestBandTone(unittest.TestCase):
    """The colour a verdict label earns."""

    def test_mixed_is_the_middle_colour(self):
        """It rendered green because the card kept its own small lookup that
        only knew wide/narrow/none, and everything else fell through to the
        good colour. One vocabulary, one source of colour."""
        from prescan_render import band_tone, _TONES
        self.assertEqual(band_tone("Mixed"), _TONES[1])

    def test_every_label_the_templates_emit_lands_somewhere(self):
        from prescan_render import band_tone, _TONES
        cases = {
            "None": 0, "Narrow": 1, "Wide": 2,
            "Opaque": 0, "Understandable": 1, "Simple": 2,
            "Short": 0, "Moderate": 1, "Long": 2,
            "Weak": 0, "Mixed": 1, "Strong": 2,
            "Bearish": 0, "Bullish": 2,
            "Exposed": 0, "Resilient": 1, "Anti-fragile": 2,
            "Pass": 0, "Revisit": 1, "Deep dive": 2,
        }
        for label, idx in cases.items():
            self.assertEqual(band_tone(label), _TONES[idx], label)

    def test_risk_runs_the_other_way(self):
        """High risk is bad and low risk is good — the opposite of every other
        scale here. Colouring by position alone would paint High green."""
        from prescan_render import band_tone, _TONES
        self.assertEqual(band_tone("High"), _TONES[0])
        self.assertEqual(band_tone("Medium"), _TONES[1])
        self.assertEqual(band_tone("Low"), _TONES[2])

    def test_a_business_phase_gets_no_judgement_colour(self):
        """Phase 5 is not better than phase 3 — it is a different stage. A
        green circle would turn a description into a compliment."""
        from prescan_render import band_tone
        for phase in ("Capital return", "Growth", "Margin expansion",
                      "Decline", "Loss-making", "Profitable growth",
                      "Startup", "Hypergrowth", "Self Funding",
                      "Operating Leverage", "Capital Return"):
            self.assertIsNone(band_tone(phase), phase)

    def test_an_unknown_label_is_not_coloured(self):
        from prescan_render import band_tone
        self.assertIsNone(band_tone("Fettuccine"))
        self.assertIsNone(band_tone(""))
        self.assertIsNone(band_tone(None))


class TestTriggerSplit(unittest.TestCase):
    """Pulling the category off a pre-mortem trigger."""

    def test_a_leading_category_is_separated(self):
        from prescan_render import split_trigger
        cat, text = split_trigger(
            "MOAT EROSIE — Gross margin onder 54% voor 2 kwartalen (nu 57.7%).")
        self.assertEqual(cat, "MOAT EROSIE")
        self.assertTrue(text.startswith("Gross margin"))

    def test_a_trigger_without_one_keeps_its_whole_text(self):
        """NVDA's triggers carry no category and must not lose their first
        clause to a dash that happens to be punctuation."""
        from prescan_render import split_trigger
        cat, text = split_trigger(
            "Gross margin onder 70% — dan is de pricing power weg")
        self.assertIsNone(cat)
        self.assertIn("pricing power", text)

    def test_a_long_lead_is_prose_not_a_label(self):
        """A category is a tag, not a sentence. Anything long enough to be a
        clause stays part of the trigger."""
        from prescan_render import split_trigger
        cat, _ = split_trigger(
            "De concurrentie in performance running wordt heviger — marge daalt")
        self.assertIsNone(cat)

    def test_double_hyphens_count_too(self):
        from prescan_render import split_trigger
        cat, text = split_trigger("FUNDAMENTEEL -- Schuld aangaan voor acquisities")
        self.assertEqual(cat, "FUNDAMENTEEL")
        self.assertTrue(text.startswith("Schuld"))

    def test_a_colon_separates_a_category_too(self):
        """CPRT writes them with a colon rather than a dash; both are the same
        habit and neither should need the other's punctuation."""
        from prescan_render import split_trigger
        cat, text = split_trigger(
            "STRUCTUREEL VOLUME: bewijs dat verzekeraars remarketing verschuiven")
        self.assertEqual(cat, "STRUCTUREEL VOLUME")
        self.assertTrue(text.startswith("bewijs"))

    def test_a_mid_sentence_colon_is_not_a_category(self):
        """"Koers bereikt buy price $86.42 terwijl: GM >55%" — a colon is
        ordinary punctuation far more often than it is a tag, so the lead has
        to look like a tag: short, and written in capitals."""
        from prescan_render import split_trigger
        cat, text = split_trigger(
            "Koers bereikt buy price $86.42 terwijl: GM >55%, HOKA groei >5%")
        self.assertIsNone(cat)
        self.assertIn("terwijl", text)

    def test_a_lowercase_lead_is_never_a_category(self):
        from prescan_render import split_trigger
        self.assertIsNone(split_trigger("marge: onder 54%")[0])

    def test_a_bare_number_is_not_a_category(self):
        """Digits survive .upper() unchanged, so an all-caps test alone would
        promote "2026" to a tag."""
        from prescan_render import split_trigger
        self.assertIsNone(split_trigger("2026: omzet daalt")[0])

    def test_a_list_number_is_not_part_of_the_category(self):
        """RACE numbers its triggers, which turned the tag into
        "1. ORDERBOEK KRIMPT"."""
        from prescan_render import split_trigger
        self.assertEqual(
            split_trigger("1. ORDERBOEK KRIMPT: orders dalen")[0],
            "ORDERBOEK KRIMPT")

    def test_empty_and_none(self):
        from prescan_render import split_trigger
        self.assertEqual(split_trigger(""), (None, ""))
        self.assertEqual(split_trigger(None), (None, ""))
