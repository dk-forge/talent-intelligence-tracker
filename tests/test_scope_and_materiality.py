"""Three things the live page exposed on 2026-07-28, plus the deal column.

1. SCOPE. The page footer promises "Layoff and redundancy data is not collected
   here; see the AI Layoff Tracker", and a Spanish Verizon story about 3,000
   cuts was live on it. Two products, one boundary.
2. NAMED EMPLOYER. "no named employer, no record" was already the rule, and
   "$7B firm" was live as a company name anyway.
3. MATERIALITY. 2,015 of 2,362 rows were a bare officer change. Each one is
   correct; together they bury the rows a recruiter came for.
4. DEAL TYPE. "Who just got acquired" had no answer, because every deal sat
   inside company_development with nothing to distinguish it.

unittest rather than pytest on purpose: pytest is not installed on the machine
that runs these, so a pytest-only file is a file that never runs.
"""

import re
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.modules.setdefault("requests", types.ModuleType("requests"))

from pipeline import prefilter, publish, schema, validate, vocab  # noqa: E402
from tests import phpsource  # noqa: E402


def raw(**overrides):
    base = {
        "raw_text": "Stripe to create 300 new jobs at expanded Dublin engineering hub",
        "headline": "Stripe to create 300 new jobs at expanded Dublin engineering hub",
        "source_url": "https://www.irishtimes.com/business/2026/07/20/stripe-dublin/",
        "source_name": "The Irish Times",
        "published_date": "Mon, 20 Jul 2026 08:14:00 GMT",
    }
    base.update(overrides)
    return base


def classified(**overrides):
    base = {
        "company": "Stripe",
        "pillar": "company_development",
        "signal_direction": "hiring",
        "city": "Dublin",
        "country": "Ireland",
        "confidence": "reported",
        "headline": "Stripe to create 300 new jobs at expanded Dublin engineering hub",
        "summary": "Stripe will add 300 roles at its Dublin engineering hub.",
        "talent_readthrough": "300 engineering roles entering the Dublin market.",
    }
    base.update(overrides)
    return base


def build(**overrides):
    """Build a signal whose headline/summary/read-through move together, which
    is how a real record arrives."""
    text = overrides.pop("text", None)
    raw_over = overrides.pop("raw", {})
    if text:
        overrides.setdefault("headline", text)
        overrides.setdefault("summary", text)
        overrides.setdefault("talent_readthrough", text)
        raw_over.setdefault("raw_text", text)
        raw_over.setdefault("headline", text)
    return validate.build_signal(
        classified(**overrides), raw(**raw_over), "google_news"
    )


# The row that was live on the page, verbatim.
VERIZON_HEADLINE = ("Verizon despedirá a 3,000 empleados en EE.UU. para reducir "
                    "gastos por $5,000 millones")
VERIZON_SUMMARY = ("Verizon plans to lay off 3,000 employees in the U.S. to reduce "
                   "expenses by $5,000 million.")
VERIZON_READTHROUGH = "Verizon cuts 3,000 roles in the U.S. to reduce costs."


class ScopeBoundary(unittest.TestCase):
    """A workforce reduction belongs to the sibling tracker, in any language."""

    def test_the_live_verizon_row_is_rejected(self):
        with self.assertRaises(validate.Rejected) as caught:
            build(
                company="Verizon",
                signal_direction="displacement",
                headline=VERIZON_HEADLINE,
                summary=VERIZON_SUMMARY,
                talent_readthrough=VERIZON_READTHROUGH,
                city="", country="United States",
                raw={"raw_text": VERIZON_HEADLINE + " " + VERIZON_SUMMARY,
                     "headline": VERIZON_HEADLINE},
            )
        self.assertIn("sibling", str(caught.exception))

    def test_it_never_reaches_the_model_in_the_first_place(self):
        keep, reason = prefilter.passes(VERIZON_HEADLINE)
        self.assertFalse(keep)
        self.assertIn("layoff tracker", reason)

    def test_an_english_cut_headline_is_rejected(self):
        with self.assertRaises(validate.Rejected):
            build(text="Tesco to cut 1,500 jobs across its UK stores",
                  company="Tesco", signal_direction="displacement", city="")

    def test_a_headline_that_hides_the_cut_is_caught_by_the_direction(self):
        """"Restructuring" names no cut, but the model's own reading does, and
        displacement means the source said roles are going."""
        with self.assertRaises(validate.Rejected) as caught:
            build(
                company="IO Biotech",
                signal_direction="displacement",
                headline="IO Biotech announces restructuring plan",
                summary="IO Biotech has announced a restructuring and workforce "
                        "reduction plan to reduce operating expenses.",
                talent_readthrough="IO Biotech is reducing its workforce globally.",
                city="",
                raw={"raw_text": "IO Biotech announces a restructuring and workforce "
                                 "reduction plan.",
                     "headline": "IO Biotech announces restructuring plan"},
            )
        self.assertIn("displacement", str(caught.exception))

    def test_the_boundary_holds_in_six_languages(self):
        cases = {
            "en": "Tesco to cut 1,500 jobs in the UK",
            "es": VERIZON_HEADLINE,
            "fr": "Renault annonce la suppression de 3 000 postes en France",
            "de": "SAP kündigt Stellenabbau von 3.000 Mitarbeitern an",
            "pt": "Banco corta 500 vagas no Brasil",
            "it": "Fiat: tagli ai posti di lavoro in Italia",
        }
        for lang, headline in cases.items():
            with self.subTest(lang=lang):
                self.assertIsNotNone(
                    prefilter.workforce_reduction_term(headline), headline)

    # --- the near misses that must still pass ---------------------------

    NEAR_MISSES = (
        # Hiring is the subject; the cut is context.
        "Klarna to hire 1,000 customer service staff after AI-driven job cuts",
        "Stripe to hire 1,000 in Dublin even as AI reduces staffing needs",
        # Leadership is the subject.
        "Acme Systems appoints new CFO after last year's redundancies",
        # Funding is the subject.
        "Acme Systems raises $50M two years after laying off 200 staff",
        # A cut somewhere else entirely.
        "Government announces new tax cuts for employers",
    )

    def test_near_misses_are_not_treated_as_reductions(self):
        for headline in self.NEAR_MISSES:
            with self.subTest(headline=headline):
                self.assertIsNone(prefilter.workforce_reduction_term(headline))

    def test_a_hiring_story_that_mentions_cuts_still_builds(self):
        text = "Klarna to hire 1,000 customer service staff after AI-driven job cuts"
        signal = build(text=text, company="Klarna", signal_direction="hiring", city="")
        self.assertEqual(signal.company, "Klarna")

    def test_an_appointment_after_redundancies_still_builds(self):
        text = "Acme Systems appoints new CFO after last year's redundancies"
        signal = build(text=text, company="Acme Systems",
                       pillar="leadership_change", signal_direction="neutral", city="")
        self.assertEqual(signal.pillar, "leadership_change")


class NamedEmployer(unittest.TestCase):
    """A description is not a name. Numbers and symbols in a name are."""

    PLACEHOLDERS = (
        "$7B firm",          # the row that was live
        "PR firm",           # also live, from the same rule's blind spot
        "the company",
        "The Company",
        "a major bank",
        "an undisclosed buyer",
        "unidentified employer",
        "tech giant",
        "€500M retailer",
        "a leading fintech startup",
    )

    REAL_NAMES = (
        "7-Eleven", "3M", "23andMe", "$1 Dollar Stores", "374Water Inc.",
        "8x8, Inc.", "National Bank Holdings Corp", "US Bank",
        "The Kroger Co.", "Deere & Company", "Legal & General",
        "Investment Technology Group", "Massimo Group", "Apple Inc.",
        "The Walt Disney Company", "Erie Indemnity Company",
        "American Tower Corp", "Global Payments Inc.",
        "International Paper Company", "Air Industries Group", "AT&T Inc.",
    )

    def test_placeholders_are_rejected(self):
        for name in self.PLACEHOLDERS:
            with self.subTest(name=name):
                with self.assertRaises(validate.Rejected):
                    validate.assert_employer_is_named(name)

    def test_real_names_survive(self):
        """Every one of these is, or reads exactly like, a name already in the
        table. A false reject here loses a real employer silently."""
        for name in self.REAL_NAMES:
            with self.subTest(name=name):
                validate.assert_employer_is_named(name)

    def test_the_live_row_is_rejected_end_to_end(self):
        with self.assertRaises(validate.Rejected):
            build(text="$7B firm expands Cary office space, plans to hire",
                  company="$7B firm", city="")

    def test_a_name_with_no_letters_or_digits_is_rejected(self):
        with self.assertRaises(validate.Rejected):
            validate.assert_employer_is_named("---")


class Materiality(unittest.TestCase):
    """Deterministic, in Python, from values already on the row."""

    def call(self, **over):
        base = dict(headcount=None, funding_usd=None, ticker=None, cik=None,
                    pillar="leadership_change", headline="Acme names new CFO",
                    city=None)
        base.update(over)
        return validate.compute_materiality(**base)

    def test_a_stated_headcount_is_high(self):
        self.assertEqual(self.call(headcount=300), "high")

    def test_a_ten_million_round_is_high(self):
        self.assertEqual(self.call(funding_usd=10_000_000,
                                   pillar="company_development"), "high")

    def test_a_small_round_is_not_high_on_size_alone(self):
        self.assertEqual(self.call(funding_usd=250_000,
                                   pillar="company_development"), "medium")

    def test_a_bare_officer_change_is_routine(self):
        self.assertEqual(
            self.call(headline="ACME CORP 8-K filing (Item 5.02): officer or "
                               "director change"),
            "routine")

    def test_a_bare_officer_change_at_a_filer_is_still_routine(self):
        """A ticker does not make a Form 8-K item 5.02 interesting."""
        self.assertEqual(
            self.call(ticker="ACME", cik="1234567",
                      headline="ACME CORP 8-K filing (Item 5.02): officer or "
                               "director change"),
            "routine")

    def test_a_city_lifts_an_officer_change_out_of_routine(self):
        self.assertEqual(
            self.call(city="Dublin", ticker="ACME",
                      headline="ACME CORP 8-K filing (Item 5.02): officer or "
                               "director change"),
            "high")

    def test_a_filer_with_a_real_story_is_high(self):
        self.assertEqual(
            self.call(cik="1234567", pillar="company_development",
                      headline="Acme opens Dublin engineering hub"),
            "high")

    def test_everything_else_is_medium(self):
        self.assertEqual(
            self.call(pillar="company_development",
                      headline="Acme opens Dublin engineering hub"),
            "medium")

    def test_every_value_is_in_the_vocabulary(self):
        for value in ("high", "medium", "routine"):
            self.assertEqual(vocab.normalize_materiality(value), value)
        self.assertIsNone(vocab.normalize_materiality("very important"))

    def test_a_built_signal_carries_one(self):
        signal = build()
        self.assertIn(signal.materiality, vocab.MATERIALITY_LEVELS)

    def test_the_backfill_recomputes_without_refetching(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mat.db"
            conn = schema.connect(db)
            conn.execute(
                "INSERT INTO signals (signal_id, headline, summary,"
                " talent_readthrough, company, company_key, pillar,"
                " signal_direction, confidence, source_url, source_name,"
                " captured_at, as_of, content_hash, collector)"
                " VALUES ('x','ACME CORP 8-K filing (Item 5.02): officer or"
                " director change','s','t','Acme','acme','leadership_change',"
                "'neutral','verified','https://sec.gov/a','SEC EDGAR',"
                "'2026-01-01','2026-01-01','hash','sec_edgar')"
            )
            conn.commit()
            self.assertEqual(schema.backfill_materiality(conn), 1)
            row = conn.execute("SELECT materiality FROM signals").fetchone()
            self.assertEqual(row["materiality"], "routine")
            # Idempotent: a second pass touches nothing.
            self.assertEqual(schema.backfill_materiality(conn), 0)
            conn.close()


class DealType(unittest.TestCase):
    """Direction is the whole point: the buyer and the bought company mean
    opposite things to a recruiter."""

    def test_the_buyer_and_the_target_are_different_values(self):
        buyer = build(text="Acme Systems acquires Beta Systems for $400M",
                      company="Acme Systems", deal_type="acquisition", city="")
        target = build(text="Beta Systems to be acquired by Acme Systems for $400M",
                       company="Beta Systems", deal_type="acquired by", city="")
        self.assertEqual(buyer.deal_type, "acquisition")
        self.assertEqual(target.deal_type, "acquired")
        self.assertNotEqual(buyer.deal_type, target.deal_type)

    def test_a_divestiture_is_not_an_acquisition(self):
        signal = build(text="Acme Systems to spin off its logistics unit",
                       company="Acme Systems", deal_type="spin-off", city="")
        self.assertEqual(signal.deal_type, "divestiture")

    def test_a_plain_funding_round_gets_no_deal_type(self):
        signal = build(
            text="Acme Systems raises $50M in a Series B round",
            company="Acme Systems", funding_amount="$50M",
            funding_stage="Series B", city="")
        self.assertIsNone(signal.deal_type)
        self.assertEqual(signal.funding_stage, "series_b")

    def test_a_deal_never_changes_the_direction(self):
        """An acquisition says nothing about headcount until the source does."""
        signal = build(text="Acme Systems acquires Beta Systems",
                       company="Acme Systems", deal_type="acquisition",
                       signal_direction="neutral", city="")
        self.assertEqual(signal.signal_direction, "neutral")
        self.assertIsNone(signal.headcount)

    def test_every_deal_type_normalises_to_itself(self):
        for value in vocab.DEAL_TYPES:
            self.assertEqual(vocab.normalize_deal_type(value), value)

    def test_unknown_wording_is_not_invented(self):
        for text in ("", "a big deal", "partnership", "restructuring"):
            with self.subTest(text=text):
                self.assertIsNone(vocab.normalize_deal_type(text))

    def test_every_deal_type_has_a_label(self):
        self.assertEqual(set(vocab.DEAL_TYPE_LABELS), set(vocab.DEAL_TYPES))


class SiteEvent(unittest.TestCase):
    """A place of work opening or closing is the earliest geographic hiring
    signal there is, and it is not a headcount claim. Both halves matter."""

    def test_an_opening_is_recorded_without_becoming_hiring(self):
        text = "Siemens opens automation factory in Cairo"
        signal = build(text=text, company="Siemens", site_event="opened",
                       signal_direction="neutral", city="", country="Egypt")
        self.assertEqual(signal.site_event, "opened")
        self.assertEqual(signal.signal_direction, "neutral")
        self.assertIsNone(signal.headcount)

    def test_a_closure_is_recorded_without_becoming_displacement(self):
        """Plenty of closures are a consolidation into another site. The source
        has to say roles are going before the row does."""
        text = "Acme Systems closes its Cork office and moves the work to Dublin"
        signal = build(text=text, company="Acme Systems", site_event="closed",
                       signal_direction="neutral", city="", country="Ireland")
        self.assertEqual(signal.site_event, "closed")
        self.assertEqual(signal.signal_direction, "neutral")

    def test_a_planned_site_is_not_an_open_one(self):
        """"Announced" is not a softer word for "opened": a plant promised for
        2028 and a building open this morning are different answers to the
        question the page is for."""
        signal = build(text="Electra to build a manufacturing plant in Ohio",
                       company="Electra", site_event="to build",
                       signal_direction="neutral", city="", country="United States")
        self.assertEqual(signal.site_event, "announced")

    def test_a_site_event_carries_the_city_the_source_named(self):
        signal = build(text="Sixth Street opens an office in Dublin",
                       company="Sixth Street", site_event="opened",
                       signal_direction="neutral", city="Dublin")
        self.assertEqual(signal.site_event, "opened")
        self.assertEqual(signal.city, "Dublin")
        self.assertEqual(signal.country, "IE")

    def test_a_story_with_no_site_event_gets_none(self):
        signal = build(text="Stripe appoints a new chief financial officer",
                       company="Stripe", pillar="leadership_change",
                       signal_direction="neutral", city="")
        self.assertIsNone(signal.site_event)

    def test_every_site_event_normalises_to_itself(self):
        for value in vocab.SITE_EVENTS:
            self.assertEqual(vocab.normalize_site_event(value), value)

    def test_unknown_wording_is_not_invented(self):
        for text in ("", "a new chapter", "refurbished", "visited"):
            with self.subTest(text=text):
                self.assertIsNone(vocab.normalize_site_event(text))

    def test_every_site_event_has_a_label(self):
        self.assertEqual(set(vocab.SITE_EVENT_LABELS), set(vocab.SITE_EVENTS))

    def test_the_wordpress_filter_offers_exactly_these_values(self):
        """A value the pipeline stores and the API refuses is a filter that
        silently returns nothing."""
        api = (Path(__file__).resolve().parent.parent / "wordpress-plugin"
               / "talent-intelligence-tracker" / "includes" / "api.php").read_text()
        block = phpsource.balanced_block(
            api[api.index("function tit_allowed_site_events"):], "array(",
            what="tit_allowed_site_events",
        )
        offered = set(re.findall(r"'([a-z_]+)'", block))
        self.assertEqual(offered, set(vocab.SITE_EVENTS))


class ColumnsTravel(unittest.TestCase):
    """A column missing from any one of these three places is a column the site
    can never show, however well it is populated locally."""

    NEW_COLUMNS = ("materiality", "deal_type", "site_event")

    def test_the_dataclass_carries_them(self):
        for column in self.NEW_COLUMNS:
            self.assertIn(column, validate.Signal.__dataclass_fields__, column)

    def test_the_table_carries_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = schema.connect(Path(tmp) / "cols.db")
            columns = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
            conn.close()
        for column in self.NEW_COLUMNS:
            self.assertIn(column, columns, column)

    def test_the_publish_allowlist_carries_them(self):
        for column in self.NEW_COLUMNS:
            self.assertIn(column, publish.FIELDS, column)

    def test_the_wordpress_table_carries_them(self):
        db_php = (Path(__file__).resolve().parent.parent / "wordpress-plugin"
                  / "talent-intelligence-tracker" / "includes" / "db.php").read_text()
        for column in self.NEW_COLUMNS:
            self.assertIn(column, db_php, column)

    def test_a_pre_existing_table_gets_them_by_migration(self):
        """The database is committed to the repo, so it outlives every schema
        change and CREATE TABLE IF NOT EXISTS does nothing to it."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "old.db"
            old = schema.TABLES
            for column in self.NEW_COLUMNS:
                old = "\n".join(
                    line for line in old.splitlines()
                    if not line.strip().startswith(column + " ")
                )
            raw_conn = sqlite3.connect(db)
            raw_conn.executescript(old)
            raw_conn.close()

            conn = schema.connect(db)
            columns = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
            conn.close()
        for column in self.NEW_COLUMNS:
            self.assertIn(column, columns, column)


if __name__ == "__main__":
    unittest.main()
