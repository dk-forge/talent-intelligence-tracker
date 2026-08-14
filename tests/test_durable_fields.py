"""The columns added while the 2026 backfill was mid-flight.

A column added after the backfill lands is NULL on every one of those rows
forever: nothing re-reads an article we have already paid to classify. So these
went in under time pressure, and these tests are what stands in for the slow
review that pressure did not allow.

Written on unittest rather than pytest on purpose: pytest is not installed on
the machine that runs these, so a pytest-only test file is a test file that
never runs.
"""

import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

# publish.py imports requests at module scope but only calls it inside a
# function, and requests may not be installed on the machine that runs these
# tests. A stub keeps the payload allowlist testable here: skipping it instead
# would mean the one test that catches "new column, forgot to send it" never
# runs. Prefer the REAL library when it IS installed: a bare ModuleType here
# shadows it for every module imported later in this process, and collectors
# subclass requests.RequestException at import time, so this file alone failed
# 17 tests while the full suite (which imports real requests first) passed.
try:
    import requests  # noqa: F401
except ImportError:
    _requests_stub = types.ModuleType("requests")
    _requests_stub.RequestException = Exception
    sys.modules.setdefault("requests", _requests_stub)

from pipeline import publish, schema, validate, vocab  # noqa: E402


class FundingUsdParser(unittest.TestCase):
    """funding_amount holds display strings, so nothing could sum, sort or
    chart funding at all. These are the exact values sitting in the live table
    on the day the column was added."""

    def test_live_values(self):
        cases = {
            "$3.6M": 3_600_000,
            "$10M": 10_000_000,
            "$1.45 Million": 1_450_000,
            "$130 Million": 130_000_000,
            "$1,000.0 million": 1_000_000_000,
            "$9.9 billion": 9_900_000_000,
            "$200,000": 200_000,
            "$6.0 million": 6_000_000,
            "$50.0 million": 50_000_000,
            "$71M": 71_000_000,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(vocab.parse_funding_usd(text), expected)

    def test_suffix_spellings(self):
        for text in ("$2.5bn", "$2.5 billion", "USD 2.5B", "US$2.5bn"):
            with self.subTest(text=text):
                self.assertEqual(vocab.parse_funding_usd(text), 2_500_000_000)
        self.assertEqual(vocab.parse_funding_usd("$500K"), 500_000)
        self.assertEqual(vocab.parse_funding_usd("$500 thousand"), 500_000)

    def test_non_usd_is_null_not_converted(self):
        """We would have to pick an exchange rate, and a made-up rate on a
        historical round is a made-up number."""
        for text in ("€5M", "EUR 5 million", "£12.5M", "GBP 12.5 million",
                     "¥800 million", "C$4M", "A$30 million", "₹50 crore",
                     "CHF 10M", "SEK 200 million"):
            with self.subTest(text=text):
                self.assertIsNone(vocab.parse_funding_usd(text))

    def test_us_dollar_prefixes_are_still_usd(self):
        """'US$' contains 'S$', which is the Singapore dollar. Stripping the
        US marker first is what stops a US figure reading as Singaporean."""
        self.assertEqual(vocab.parse_funding_usd("US$10M"), 10_000_000)
        self.assertEqual(vocab.parse_funding_usd("USD 10 million"), 10_000_000)

    def test_junk_is_null(self):
        for text in ("", None, "undisclosed", "not disclosed", "several million",
                     "n/a", "-", "an eight-figure sum"):
            with self.subTest(text=text):
                self.assertIsNone(vocab.parse_funding_usd(text))

    def test_a_range_takes_the_first_number(self):
        """Same rule the sibling tracker uses for headcounts: parse the first
        number, never average a range into a figure nobody printed."""
        self.assertEqual(vocab.parse_funding_usd("$5M to $10M"), 5_000_000)

    def test_implausible_values_are_rejected(self):
        self.assertIsNone(vocab.parse_funding_usd("$0"))
        self.assertIsNone(vocab.parse_funding_usd("$500 trillion"))


class Vocabularies(unittest.TestCase):
    def test_funding_stage(self):
        cases = {
            "seed": "seed", "Seed round": "seed", "seed funding": "seed",
            "pre-seed": "pre_seed", "Pre Seed": "pre_seed", "angel": "pre_seed",
            "Series A": "series_a", "series a": "series_a", "A round": "series_a",
            "Series B": "series_b", "Series C": "series_c",
            "Series D": "series_d_plus", "Series E": "series_d_plus",
            "series f": "series_d_plus", "late stage": "series_d_plus",
            "growth equity": "growth", "private equity": "growth",
            "venture debt": "debt", "debt financing": "debt",
            "government grant": "grant", "grant": "grant",
            "IPO": "ipo", "initial public offering": "ipo",
            "strategic investment": "other", "other": "other",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(vocab.normalize_funding_stage(text), expected)

    def test_funding_stage_rejects_what_it_does_not_know(self):
        for text in ("", None, "a big round", "crowdfunding-ish", "series"):
            with self.subTest(text=text):
                self.assertIsNone(vocab.normalize_funding_stage(text or ""))

    def test_every_stage_normalises_to_itself(self):
        for value in vocab.FUNDING_STAGES:
            self.assertEqual(vocab.normalize_funding_stage(value), value)

    def test_work_mode(self):
        cases = {
            "remote": "remote", "fully remote": "remote", "work from home": "remote",
            "hybrid": "hybrid", "hybrid working": "hybrid",
            "onsite": "onsite", "on-site": "onsite", "in office": "onsite",
            "rto_mandate": "rto_mandate", "RTO": "rto_mandate",
            "return to office": "rto_mandate", "return-to-office": "rto_mandate",
            "flexible": "flexible", "flexible working": "flexible",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(vocab.normalize_work_mode(text), expected)
        self.assertIsNone(vocab.normalize_work_mode("four day week"))
        self.assertIsNone(vocab.normalize_work_mode(""))

    def test_every_work_mode_normalises_to_itself(self):
        for value in vocab.WORK_MODES:
            self.assertEqual(vocab.normalize_work_mode(value), value)

    def test_employer_type(self):
        cases = {
            "public": "public", "publicly traded": "public", "listed": "public",
            "private": "private", "privately held": "private",
            "startup": "startup", "start-up": "startup", "venture backed": "startup",
            "government": "government", "public sector": "government",
            "nonprofit": "nonprofit", "non-profit": "nonprofit", "charity": "nonprofit",
            "education": "education", "university": "education",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(vocab.normalize_employer_type(text), expected)
        self.assertIsNone(vocab.normalize_employer_type("cooperative"))
        self.assertIsNone(vocab.normalize_employer_type(""))

    def test_every_employer_type_normalises_to_itself(self):
        for value in vocab.EMPLOYER_TYPES:
            self.assertEqual(vocab.normalize_employer_type(value), value)

    def test_headcount_scope(self):
        cases = {
            "new_roles": "new_roles", "new roles": "new_roles",
            "new jobs": "new_roles", "jobs created": "new_roles",
            "total_workforce": "total_workforce", "total workforce": "total_workforce",
            "company wide": "total_workforce", "global workforce": "total_workforce",
            "single_site": "single_site", "single site": "single_site",
            "plant": "single_site",
            "affected": "affected", "roles affected": "affected",
            "jobs cut": "affected", "redundancies": "affected",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(vocab.normalize_headcount_scope(text), expected)
        self.assertIsNone(vocab.normalize_headcount_scope("some people"))
        self.assertIsNone(vocab.normalize_headcount_scope(""))

    def test_every_headcount_scope_normalises_to_itself(self):
        for value in vocab.HEADCOUNT_SCOPES:
            self.assertEqual(vocab.normalize_headcount_scope(value), value)

    def test_every_vocabulary_has_a_label(self):
        """A value with no label reaches the page as a raw storage token, and
        'series_d_plus' is not English."""
        pairs = (
            (vocab.FUNDING_STAGES, vocab.FUNDING_STAGE_LABELS),
            (vocab.WORK_MODES, vocab.WORK_MODE_LABELS),
            (vocab.EMPLOYER_TYPES, vocab.EMPLOYER_TYPE_LABELS),
            (vocab.HEADCOUNT_SCOPES, vocab.HEADCOUNT_SCOPE_LABELS),
        )
        for values, labels in pairs:
            self.assertEqual(set(values), set(labels))
            for label in labels.values():
                self.assertNotIn("_", label)


NEW_COLUMNS = (
    "funding_amount_usd", "funding_stage", "effective_date", "ticker", "cik",
    "work_mode", "employer_type", "headcount_scope",
)


class Migration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _old_database(self, name="old.db"):
        """A database created before any of these columns existed."""
        db = self.tmp / name
        old = schema.TABLES
        for column in NEW_COLUMNS:
            old = "\n".join(
                line for line in old.splitlines()
                if not line.strip().startswith(column + " ")
            )
        raw = sqlite3.connect(db)
        raw.executescript(old)
        return db, raw

    def test_migration_adds_every_new_column(self):
        db, raw = self._old_database()
        raw.close()
        check = sqlite3.connect(db)
        before = {r[1] for r in check.execute("PRAGMA table_info(signals)")}
        check.close()
        self.assertFalse(set(NEW_COLUMNS) & before)

        conn = schema.connect(db)
        after = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
        self.assertLessEqual(set(NEW_COLUMNS), after)
        conn.close()

    def test_old_rows_survive_and_stay_null(self):
        """Never backfilled with guesses. An old row says 'we do not know',
        which is queryable; a guessed value is not."""
        db, raw = self._old_database("rows.db")
        raw.execute(
            "INSERT INTO signals (signal_id, headline, summary, talent_readthrough,"
            " company, company_key, pillar, signal_direction, confidence, source_url,"
            " source_name, captured_at, as_of, content_hash, collector)"
            " VALUES ('x','h','s','t','Acme','acme','company_development','hiring',"
            " 'reported','https://e.com/a','E','2026-01-01','2026-01-01','hash','c')"
        )
        raw.commit()
        raw.close()

        conn = schema.connect(db)
        row = conn.execute(
            f"SELECT company, {', '.join(NEW_COLUMNS)} FROM signals"
        ).fetchone()
        self.assertEqual(row["company"], "Acme")
        for column in NEW_COLUMNS:
            self.assertIsNone(row[column], column)
        conn.close()

    def test_migration_is_idempotent(self):
        db = self.tmp / "twice.db"
        schema.connect(db).close()
        conn = schema.connect(db)  # would raise "duplicate column" if unguarded
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0], 0)
        conn.close()


class FundingUsdBackfill(unittest.TestCase):
    """The one new column that IS backfilled, because it invents nothing: it
    re-parses a string we already collected."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "backfill.db"

    def _insert(self, conn, signal_id, funding):
        conn.execute(
            "INSERT INTO signals (signal_id, headline, summary, talent_readthrough,"
            " company, company_key, pillar, signal_direction, confidence, source_url,"
            " source_name, captured_at, as_of, content_hash, collector, funding_amount)"
            " VALUES (?,'h','s','t','Acme','acme','company_development','neutral',"
            " 'reported','https://e.com/a','E','2026-01-01','2026-01-01',?,'c',?)",
            (signal_id, signal_id, funding),
        )

    def test_backfills_from_the_stored_string(self):
        conn = schema.connect(self.db)
        self._insert(conn, "a", "$3.6M")
        self._insert(conn, "b", "$1.45 Million")
        self._insert(conn, "c", "€5M")
        self._insert(conn, "d", None)
        conn.commit()

        filled = schema.backfill_funding_usd(conn)
        self.assertEqual(filled, 2)

        got = dict(conn.execute(
            "SELECT signal_id, funding_amount_usd FROM signals"
        ).fetchall())
        self.assertEqual(got["a"], 3_600_000)
        self.assertEqual(got["b"], 1_450_000)
        self.assertIsNone(got["c"])  # non-USD: never converted at a guessed rate
        self.assertIsNone(got["d"])
        conn.close()

    def test_backfill_is_idempotent_and_never_overwrites(self):
        conn = schema.connect(self.db)
        self._insert(conn, "a", "$3.6M")
        conn.commit()
        schema.backfill_funding_usd(conn)

        # A hand-corrected value must survive a second pass.
        conn.execute("UPDATE signals SET funding_amount_usd = 1 WHERE signal_id = 'a'")
        self.assertEqual(schema.backfill_funding_usd(conn), 0)
        self.assertEqual(
            conn.execute("SELECT funding_amount_usd FROM signals").fetchone()[0], 1
        )
        conn.close()

    def test_connect_runs_the_backfill(self):
        """Wired into connect() because a migration script someone has to
        remember is a migration that stops running."""
        conn = schema.connect(self.db)
        self._insert(conn, "a", "$130 Million")
        conn.commit()
        conn.close()

        conn = schema.connect(self.db)
        self.assertEqual(
            conn.execute("SELECT funding_amount_usd FROM signals").fetchone()[0],
            130_000_000,
        )
        conn.close()


def _raw(**overrides):
    base = {
        "raw_text": "Apple (NASDAQ: AAPL) said Tim Cook will step down as chief "
                    "executive in September. The company employs 164000 people "
                    "worldwide and is ending its remote work policy.",
        "headline": "Apple names a successor to Tim Cook",
        "source_url": "https://www.reuters.com/technology/2026/07/20/apple-cook/",
        "source_name": "Reuters",
        "published_date": "2026-07-20",
    }
    base.update(overrides)
    return base


def _classified(**overrides):
    base = {
        "company": "Apple",
        "pillar": "leadership_change",
        "signal_direction": "neutral",
        "confidence": "reported",
        "headline": "Apple names a successor to Tim Cook",
        "summary": "Tim Cook will step down as chief executive in September.",
        "talent_readthrough": "Apple's next chief executive takes over in September.",
    }
    base.update(overrides)
    return base


class SignalFields(unittest.TestCase):
    def build(self, **overrides):
        raw_overrides = overrides.pop("_raw", {})
        return validate.build_signal(
            _classified(**overrides), _raw(**raw_overrides), "test"
        )

    def test_all_new_fields_default_to_none(self):
        signal = self.build()
        for column in NEW_COLUMNS:
            self.assertIsNone(getattr(signal, column), column)

    def test_effective_date_is_kept_when_the_month_is_in_the_text(self):
        """'Steps down in September' is a July article about a September event.
        Filing it under published_date answers the wrong question."""
        signal = self.build(effective_date="2026-09-01")
        self.assertEqual(signal.effective_date, "2026-09-01")

    def test_effective_date_is_dropped_when_the_month_is_not_in_the_text(self):
        """An inferred effective date is the same class of mistake as an
        inferred headcount."""
        signal = self.build(effective_date="2026-11-01")
        self.assertIsNone(signal.effective_date)

    def test_effective_date_equal_to_published_is_dropped(self):
        signal = self.build(effective_date="2026-07-20")
        self.assertIsNone(signal.effective_date)

    def test_effective_date_ignores_junk(self):
        for value in ("soon", "next quarter", "2026", "", None, "1902-01-01"):
            with self.subTest(value=value):
                self.assertIsNone(self.build(effective_date=value).effective_date)

    def test_ticker_is_kept_only_when_the_text_prints_it(self):
        self.assertEqual(self.build(ticker="AAPL").ticker, "AAPL")
        self.assertEqual(self.build(ticker="NASDAQ: AAPL").ticker, "AAPL")
        self.assertIsNone(self.build(ticker="MSFT").ticker)
        self.assertIsNone(self.build(ticker="").ticker)

    def test_ticker_matching_is_case_sensitive(self):
        """A case-insensitive match lets a ticker of 'IT' or 'ON' be confirmed
        by any ordinary sentence."""
        signal = self.build(
            ticker="IT",
            _raw={"raw_text": "Acme said it will hire in Dublin. Reported by Reuters."},
        )
        self.assertIsNone(signal.ticker)

    def test_cik_comes_from_the_collector_not_the_model(self):
        signal = self.build(_raw={"cik": "0000320193"})
        self.assertEqual(signal.cik, "320193")
        self.assertIsNone(self.build(_raw={"cik": ""}).cik)
        self.assertIsNone(self.build(_raw={"cik": "not-a-cik"}).cik)

    def test_headcount_scope_needs_a_headcount(self):
        """A scope with no number describes nothing, and would put 'Total
        workforce' on a row that never said how many people that is."""
        with_number = self.build(headcount="164000", headcount_scope="total workforce")
        self.assertEqual(with_number.headcount, 164000)
        self.assertEqual(with_number.headcount_scope, "total_workforce")

        without = self.build(headcount_scope="total workforce")
        self.assertIsNone(without.headcount)
        self.assertIsNone(without.headcount_scope)

    def test_funding_usd_is_derived_not_asked_for(self):
        raw_text = "Holobiome raised $10M in a seed round in Boston."
        signal = self.build(
            funding_amount="$10M",
            funding_stage="Seed",
            summary="Holobiome raised $10M in a seed round.",
            _raw={"raw_text": raw_text},
        )
        self.assertEqual(signal.funding_amount, "$10M")   # quotable source form
        self.assertEqual(signal.funding_amount_usd, 10_000_000)
        self.assertEqual(signal.funding_stage, "seed")

    def test_funding_usd_is_null_when_the_string_is_rejected(self):
        """No accepted string, no number. The USD column can never carry a
        figure the verbatim column refused."""
        signal = self.build(funding_amount="$999M")  # not in the source text
        self.assertIsNone(signal.funding_amount)
        self.assertIsNone(signal.funding_amount_usd)

    def test_work_mode_and_employer_type_normalise(self):
        signal = self.build(work_mode="return to office", employer_type="publicly traded")
        self.assertEqual(signal.work_mode, "rto_mandate")
        self.assertEqual(signal.employer_type, "public")


class PublishPayload(unittest.TestCase):
    def test_every_new_column_is_sent_to_wordpress(self):
        """A column missing from the allowlist is invisible to the site however
        well it is populated locally."""
        for column in NEW_COLUMNS:
            self.assertIn(column, publish.FIELDS, column)

    def test_the_allowlist_only_names_real_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = schema.connect(Path(tmp) / "fields.db")
            columns = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
            conn.close()
        self.assertLessEqual(set(publish.FIELDS), columns)

    def test_the_signal_dataclass_matches_the_allowlist(self):
        fields = set(validate.Signal.__dataclass_fields__)
        self.assertLessEqual(set(publish.FIELDS) - {"signal_id"}, fields | {"signal_id"})
        for column in NEW_COLUMNS:
            self.assertIn(column, fields, column)


if __name__ == "__main__":
    unittest.main()
