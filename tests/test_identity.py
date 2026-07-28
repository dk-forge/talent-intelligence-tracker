"""The employer identity spine (pipeline/identity.py).

Every test here runs on recorded fixtures. Not one makes a network call: the
whole point of this module is that a lookup can fail, and a test suite that
needs Wikidata to be up cannot tell "the resolver is broken" from "WDQS is
having an afternoon".

The five behaviours that matter, and what each one is standing guard over:

1. The organisation allow-list. `wbsearchentities` for "NASA" returns the plant
   genus FIRST. Taking the top hit is how a plant acquires a headquarters.
2. SEC beats Wikidata on tickers. Apple's Wikidata P249 is 6689, its Tokyo
   listing — a correct fact and the wrong answer.
3. Sourced beats derived, always. A CIK read out of the EFTS hit is never
   replaced by one this module inferred from a name.
4. Negative results are cached. Otherwise the 400-odd employers Wikidata has
   never heard of are re-asked about on every single run, forever.
5. It fails open. A timeout leaves every field exactly as it was, and nothing
   raises into the pipeline.

Written on unittest rather than pytest on purpose: pytest is not installed on
the machine that runs these, so a pytest-only test file never runs.
"""

import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# publish.py imports requests at module scope and requests is not installed on
# the machine that runs these. identity.py imports it lazily for exactly that
# reason, but validate -> publish still needs the stub in this process.
sys.modules.setdefault("requests", types.ModuleType("requests"))

from pipeline import identity, schema, validate, vocab  # noqa: E402


# --- Recorded fixtures -------------------------------------------------------

# Trimmed from live responses on 2026-07-28. QIDs, roots and place chains are
# verbatim; the shape is exactly what fetch_properties() parses.

NASA_SEARCH = ["Q309751", "Q23548"]          # plant genus first, agency second
NASA_PROPS = {
    "Q309751": {"roots": [], "places": [], "hq_country": "", "country": ""},
    "Q23548": {"roots": ["Q327333", "Q43229"],
               "places": ["Washington, D.C.", "United States"],
               "hq_country": "US", "country": "US"},
}

APPLE_SEARCH = ["Q312"]
APPLE_PROPS = {
    "Q312": {"roots": ["Q783794", "Q43229", "Q891723", "Q4830453"],
             "places": ["Cupertino", "Santa Clara County", "California",
                        "United States"],
             "hq_country": "US", "country": "US"},
}

MAYO_SEARCH = ["Q1130172"]
MAYO_PROPS = {
    "Q1130172": {"roots": ["Q163740", "Q43229", "Q2385804", "Q31855"],
                 "places": ["Minnesota", "United States", "Olmsted County",
                            "Rochester"],
                 "hq_country": "US", "country": "US"},
}

TVA_SEARCH = ["Q1367577"]
TVA_PROPS = {
    "Q1367577": {"roots": ["Q783794", "Q4830453", "Q43229", "Q327333"],
                 "places": ["United States", "Tennessee", "Knoxville"],
                 "hq_country": "US", "country": "US"},
}

# Three rows of the real company_tickers.json, plus the two share classes that
# make one employer look like two.
SEC_PAYLOAD = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
    "3": {"cik_str": 1376986, "ticker": "TVC", "title": "TENNESSEE VALLEY AUTHORITY"},
    # Two genuinely different employers sharing a normalised name.
    "4": {"cik_str": 111111, "ticker": "ACME", "title": "Acme Holdings Inc."},
    "5": {"cik_str": 222222, "ticker": "ACMX", "title": "Acme Holdings Corp"},
    # The second share class, filed far down the ranked file, where the real
    # one sits. Deliberately out of key order too: the index sorts by rank.
    "7478": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
}


class Fixture:
    """Stands in for the two network functions, and counts the calls.

    Patching `search_qids`/`fetch_properties` rather than `requests` keeps the
    tests about resolution logic; `_get_json` has its own tests below.
    """

    def __init__(self, searches, props):
        self.searches = searches
        self.props = props
        self.search_calls = 0
        self.sparql_calls = 0

    def search_qids(self, name, limit=5):
        self.search_calls += 1
        return list(self.searches.get(vocab.company_key(name), []))

    def fetch_properties(self, qids):
        self.sparql_calls += 1
        return {q: self.props[q] for q in qids if q in self.props}


class _Patched:
    """Swap identity's network functions for a fixture, and put them back."""

    def __init__(self, testcase, fixture, sec_payload=SEC_PAYLOAD):
        self.tc, self.fx, self.sec = testcase, fixture, sec_payload

    def __enter__(self):
        self._saved = (identity.search_qids, identity.fetch_properties,
                       identity._sec_index)
        identity.search_qids = self.fx.search_qids
        identity.fetch_properties = self.fx.fetch_properties
        identity._sec_index = identity._build_sec_index(self.sec)
        identity._consecutive_failures = 0
        return self.fx

    def __exit__(self, *exc):
        (identity.search_qids, identity.fetch_properties,
         identity._sec_index) = self._saved
        return False


_OPEN: list = []


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema.TABLES)
    _OPEN.append(conn)
    return conn


def tearDownModule():
    while _OPEN:
        _OPEN.pop().close()


class OrganisationAllowList(unittest.TestCase):
    """Guard 1. The failure that made this module necessary."""

    def test_a_plant_genus_is_not_an_employer(self):
        self.assertFalse(identity.is_organization(NASA_PROPS["Q309751"]["roots"]))
        self.assertTrue(identity.is_organization(NASA_PROPS["Q23548"]["roots"]))

    def test_nasa_resolves_past_the_plant_to_the_agency(self):
        fx = Fixture({"nasa": NASA_SEARCH}, NASA_PROPS)
        with _Patched(self, fx):
            ident = identity.wikidata_lookup("NASA")
        self.assertEqual(ident.qid, "Q23548")           # not Q309751
        self.assertEqual(ident.employer_type, "government")
        self.assertEqual(ident.hq_country, "US")
        self.assertIn("skipped 1 non-org", ident.detail)

    def test_a_name_with_no_organisation_anywhere_in_it_resolves_to_nothing(self):
        fx = Fixture({"nasa": ["Q309751"]}, NASA_PROPS)
        with _Patched(self, fx):
            ident = identity.wikidata_lookup("NASA")
        self.assertIsNone(ident.qid)
        self.assertFalse(ident.resolved)
        self.assertTrue(ident.is_empty)

    def test_every_type_lands_inside_the_closed_vocabulary(self):
        for _root, kind in identity._TYPE_BY_ROOT:
            if kind is not None:
                self.assertIn(kind, vocab.EMPLOYER_TYPES)

    def test_a_hospital_that_teaches_is_a_nonprofit_not_a_school(self):
        """Mayo Clinic is an instance of both. 'Nonprofit' is the truer label;
        a university (the specific class) still wins as 'education'."""
        self.assertEqual(
            identity.employer_type_from_roots(MAYO_PROPS["Q1130172"]["roots"]),
            "nonprofit")
        self.assertEqual(
            identity.employer_type_from_roots(["Q3918", "Q163740", "Q43229"]),
            "education")


class SecIsTheTickerAuthority(unittest.TestCase):
    """Guard 2. Wikidata's P249 for Apple is 6689 — the Tokyo listing."""

    def test_apple_gets_the_us_ticker_not_a_foreign_listing(self):
        fx = Fixture({"apple": APPLE_SEARCH}, APPLE_PROPS)
        with _Patched(self, fx):
            ident = identity.resolve("Apple Inc.", conn=_db())
        self.assertEqual(ident.ticker, "AAPL")
        self.assertNotEqual(ident.ticker, "6689")
        self.assertEqual(ident.cik, "320193")
        self.assertEqual(ident.qid, "Q312")   # Wikidata still supplied the HQ
        self.assertEqual(ident.hq_country, "US")

    def test_two_share_classes_are_one_employer_and_the_file_picks_which(self):
        """company_tickers.json is RANKED, and the primary listing comes first.
        Choosing alphabetically instead put Customers Bancorp's subordinated
        notes (CUBB) on the row in place of its common stock (CUBI) — a real
        ticker for the wrong instrument."""
        identity._sec_index = identity._build_sec_index(SEC_PAYLOAD)
        try:
            ticker, cik = identity.sec_lookup("Alphabet Inc.", allow_network=False)
        finally:
            identity._sec_index = None
        self.assertEqual(cik, "1652044")
        self.assertEqual(ticker, "GOOGL")   # not GOOG, which sorts first

    def test_two_different_companies_under_one_name_are_dropped_not_guessed(self):
        identity._sec_index = identity._build_sec_index(SEC_PAYLOAD)
        self.assertEqual(identity.sec_lookup("Acme Holdings, Inc.",
                                             allow_network=False), (None, None))
        identity._sec_index = None

    def test_registered_bonds_do_not_make_a_federal_agency_a_public_company(self):
        """TVA has an SEC ticker (TVC) and is a US federal corporation."""
        fx = Fixture({"tennessee valley authority": TVA_SEARCH}, TVA_PROPS)
        with _Patched(self, fx):
            ident = identity.resolve("Tennessee Valley Authority", conn=_db())
        self.assertEqual(ident.ticker, "TVC")
        self.assertEqual(ident.employer_type, "government")

    def test_a_listing_does_settle_public_against_a_vague_wikidata_class(self):
        props = {"Q312": dict(APPLE_PROPS["Q312"], roots=["Q4830453", "Q43229"])}
        fx = Fixture({"apple": APPLE_SEARCH}, props)
        with _Patched(self, fx):
            ident = identity.resolve("Apple Inc.", conn=_db())
        self.assertEqual(ident.employer_type, "public")


class SourcedBeatsDerived(unittest.TestCase):
    """Guard 3. The rule the whole module is subordinate to."""

    def _signal(self, **overrides):
        blank = {f: None for f in validate.Signal.__dataclass_fields__}
        blank.update({
            "signal_id": "x", "headline": "h", "summary": "s",
            "talent_readthrough": "t", "company": "Apple Inc.",
            "company_key": "apple", "pillar": "leadership",
            "signal_direction": "neutral", "confidence": "reported",
            "source_url": "https://example.com/a", "source_name": "Example",
            "captured_at": "2026-07-28", "as_of": "2026-07-28",
            "content_hash": "x", "collector": "test",
        })
        blank.update(overrides)
        return validate.Signal(**blank)

    def test_a_cik_from_the_filing_is_never_replaced(self):
        conn = _db()
        fx = Fixture({"apple": APPLE_SEARCH}, APPLE_PROPS)
        with _Patched(self, fx):
            identity.resolve("Apple Inc.", conn=conn)      # caches 320193
            signal = self._signal(cik="9999999", ticker="ZZZZ")
            filled = identity.enrich(signal, conn)
        self.assertEqual(signal.cik, "9999999")            # the SEC filing's
        self.assertEqual(signal.ticker, "ZZZZ")            # the article's
        self.assertNotIn("cik", filled)
        self.assertNotIn("ticker", filled)

    def test_it_fills_the_blanks_beside_the_value_it_left_alone(self):
        conn = _db()
        fx = Fixture({"apple": APPLE_SEARCH}, APPLE_PROPS)
        with _Patched(self, fx):
            identity.resolve("Apple Inc.", conn=conn)
            signal = self._signal(cik="9999999")
            filled = identity.enrich(signal, conn)
        self.assertEqual(signal.cik, "9999999")
        self.assertEqual(signal.ticker, "AAPL")
        self.assertEqual(signal.hq_country, "US")
        self.assertEqual(set(filled), {"ticker", "hq_country", "employer_type"})

    def test_with_no_connection_it_does_nothing_at_all(self):
        """build_signal must stay a pure function of two dicts. An earlier
        draft opened the live database itself, and four unrelated unit tests
        started passing or failing by what happened to be cached on the box."""
        with _Patched(self, Fixture({"apple": APPLE_SEARCH}, APPLE_PROPS)) as fx:
            signal = self._signal()
            self.assertEqual(identity.enrich(signal, None), [])
        self.assertIsNone(signal.ticker)
        self.assertEqual(fx.search_calls, 0)

    def test_an_empty_string_counts_as_blank_not_as_a_value(self):
        conn = _db()
        fx = Fixture({"apple": APPLE_SEARCH}, APPLE_PROPS)
        with _Patched(self, fx):
            identity.resolve("Apple Inc.", conn=conn)
            signal = self._signal(ticker="")
            identity.enrich(signal, conn)
        self.assertEqual(signal.ticker, "AAPL")

    def test_the_backfill_update_carries_the_same_rule_in_sql(self):
        conn = _db()
        conn.executescript(identity.CACHE_TABLE)
        for cik in ("9999999", None):
            conn.execute(
                """INSERT INTO signals (signal_id, headline, summary,
                     talent_readthrough, company, company_key, pillar,
                     signal_direction, confidence, source_url, source_name,
                     captured_at, as_of, content_hash, collector, cik)
                   VALUES ('s','h','s','t','Apple Inc.','apple','leadership',
                           'neutral','reported','https://e.com/a','E',
                           '2026-07-28','2026-07-28',?,'test',?)""",
                (f"hash-{cik}", cik))

        identity.apply_identity(conn, identity.Identity(
            company_key="apple", ticker="AAPL", cik="320193", hq_country="US"))

        ciks = sorted(r[0] for r in conn.execute("SELECT cik FROM signals"))
        self.assertEqual(ciks, ["320193", "9999999"])   # blank filled, other kept
        # The blank columns beside it were filled on BOTH rows.
        self.assertEqual(
            [r[0] for r in conn.execute("SELECT ticker FROM signals")],
            ["AAPL", "AAPL"])


class NegativeCaching(unittest.TestCase):
    """Guard 4. An unresolvable name is asked about once."""

    def test_an_unresolvable_name_is_recorded_and_not_retried(self):
        conn = _db()
        fx = Fixture({}, {})       # Wikidata knows nothing
        with _Patched(self, fx):
            first = identity.resolve("Blorptech Holdings Inc.", conn=conn)
            self.assertFalse(first.resolved)
            self.assertEqual(fx.search_calls, 2)   # bare name, then stripped

            second = identity.resolve("Blorptech Holdings Inc.", conn=conn)
            self.assertFalse(second.resolved)
            self.assertEqual(fx.search_calls, 2)   # unchanged: cache answered

        row = conn.execute(
            "SELECT resolved, detail FROM employer_identity WHERE company_key = ?",
            ("blorptech holdings",)).fetchone()
        self.assertEqual(row[0], 0)
        self.assertTrue(row[1])                    # the reason is kept

    def test_retry_negative_is_what_asks_again(self):
        conn = _db()
        with _Patched(self, Fixture({}, {})) as fx:
            identity.resolve("Blorptech Holdings Inc.", conn=conn)
            identity.resolve("Blorptech Holdings Inc.", conn=conn,
                             retry_negative=True)
            self.assertEqual(fx.search_calls, 4)

    def test_a_positive_result_is_never_re_resolved(self):
        conn = _db()
        with _Patched(self, Fixture({"apple": APPLE_SEARCH}, APPLE_PROPS)) as fx:
            identity.resolve("Apple Inc.", conn=conn)
            for _ in range(5):
                ident = identity.resolve("Apple Inc.", conn=conn)
        self.assertEqual(fx.sparql_calls, 1)
        self.assertEqual(ident.ticker, "AAPL")

    def test_the_ingestion_path_reads_the_cache_and_never_the_network(self):
        conn = _db()
        with _Patched(self, Fixture({"apple": APPLE_SEARCH}, APPLE_PROPS)) as fx:
            identity.resolve("Apple Inc.", conn=conn)
            calls = (fx.search_calls, fx.sparql_calls)
            hit = identity.resolve("Apple Inc.", conn=conn, allow_network=False)
            miss = identity.resolve("Nobody Ltd", conn=conn, allow_network=False)
        self.assertEqual((fx.search_calls, fx.sparql_calls), calls)
        self.assertEqual(hit.ticker, "AAPL")
        self.assertTrue(miss.is_empty)


class FailsOpen(unittest.TestCase):
    """Guard 5. Identity is a nice-to-have; ingestion is not."""

    def test_a_timeout_leaves_every_field_as_it_was(self):
        def boom(*a, **k):
            raise TimeoutError("connection timed out")

        conn = _db()
        saved = identity.search_qids
        identity.search_qids = boom
        try:
            ident = identity.resolve("Apple Inc.", conn=conn)
        finally:
            identity.search_qids = saved
        self.assertTrue(ident.is_empty)
        self.assertIn("error", ident.detail)

    def test_enrich_swallows_anything_the_resolver_can_throw(self):
        class Exploding:
            company = "Apple Inc."

            def __getattr__(self, name):
                raise RuntimeError("nope")

        self.assertEqual(identity.enrich(Exploding()), [])

    def test_a_missing_requests_library_is_a_blank_column_not_a_crash(self):
        saved_modules = sys.modules.pop("requests", None)
        saved_failures = identity._consecutive_failures
        sys.modules["requests"] = None      # import raises ImportError
        try:
            self.assertIsNone(identity._get_json("https://query.wikidata.org/sparql"))
        finally:
            sys.modules.pop("requests", None)
            if saved_modules is not None:
                sys.modules["requests"] = saved_modules
            identity._consecutive_failures = saved_failures

    def test_the_failure_budget_stops_a_blocked_network_after_a_few_tries(self):
        saved = identity._consecutive_failures
        try:
            identity._consecutive_failures = identity._FAILURE_BUDGET
            self.assertIsNone(identity._get_json("https://www.sec.gov/x.json"))
        finally:
            identity._consecutive_failures = saved

    def test_garbage_from_the_endpoint_parses_to_nothing(self):
        saved = identity._get_json
        identity._get_json = lambda *a, **k: {"results": {"bindings": [
            {"item": {"value": "not-a-uri"}},
            "not even a dict",
        ]}}
        try:
            self.assertEqual(identity.fetch_properties(["Q312"]), {})
        finally:
            identity._get_json = saved

    def test_build_signal_still_returns_a_signal_when_identity_is_dead(self):
        """The whole point: a broken resolver costs a column, not a record.

        `resolve` is patched rather than `enrich`, because enrich's own
        try/except IS the guarantee under test — patching it out would test the
        patch.
        """
        def boom(*a, **k):
            raise RuntimeError("resolver is on fire")

        saved = identity.resolve
        identity.resolve = boom
        try:
            signal = validate.build_signal(
                {"company": "Apple Inc.", "pillar": "leadership_change",
                 "signal_direction": "neutral", "headline": "Apple names a CFO",
                 "summary": "Apple named a CFO.",
                 "talent_readthrough": "Watch finance hiring."},
                {"raw_text": "Apple named a CFO.",
                 "source_url": "https://example.com/a", "source_name": "Example"},
                "test")
        finally:
            identity.resolve = saved

        self.assertEqual(signal.company, "Apple Inc.")
        self.assertIsNone(signal.ticker)     # the column is blank, not wrong


class SecIndexBuilding(unittest.TestCase):

    def test_legal_suffixes_do_not_stop_a_match(self):
        index = identity._build_sec_index(SEC_PAYLOAD)
        self.assertIn("apple", index)
        self.assertIn("tennessee valley authority", index)
        self.assertNotIn("acme holdings", index)   # ambiguous, dropped

    def test_a_disk_cache_is_used_without_touching_the_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sec.json"
            path.write_text(json.dumps(SEC_PAYLOAD))
            identity._sec_index = None
            try:
                index = identity.sec_ticker_index(path=path, allow_network=False)
            finally:
                identity._sec_index = None
        self.assertEqual(index["apple"], ("AAPL", "320193"))

    def test_no_cache_and_no_network_is_an_empty_index_not_an_error(self):
        identity._sec_index = None
        try:
            index = identity.sec_ticker_index(
                path=Path("/nonexistent/nope.json"), allow_network=False)
        finally:
            identity._sec_index = None
        self.assertEqual(index, {})


class HeadquartersPlacement(unittest.TestCase):

    def test_the_containment_chain_is_walked_past_a_building(self):
        """Alphabet's P159 is 'Googleplex'. The chain has the city."""
        city, iso2 = identity._first_vocabulary_city(
            ["Googleplex", "Santa Clara County", "New York", "United States"], "US")
        self.assertEqual((city, iso2), ("New York", "US"))

    def test_a_place_in_the_wrong_country_cannot_win(self):
        """GROUP_CONCAT has no defined order, so 'first' is not 'innermost'."""
        city, _iso2 = identity._first_vocabulary_city(["London", "Ontario"], "CA")
        self.assertIsNone(city)

    def test_a_city_outside_the_vocabulary_is_left_blank_not_invented(self):
        city, iso2 = identity._first_vocabulary_city(
            ["Cupertino", "Santa Clara County", "California"], "US")
        self.assertIsNone(city)
        self.assertIsNone(iso2)

    def test_toronto_is_in_canada(self):
        """It was mapped to US, so every Toronto signal filed itself under the
        US country filter. Caught by this module reading the table back."""
        self.assertEqual(vocab.normalize_city("Toronto")[2], "CA")


class TheCacheTable(unittest.TestCase):

    def test_schema_connect_creates_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = schema.connect(Path(tmp) / "t.db")
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            conn.close()
        self.assertIn("employer_identity", tables)

    def test_it_appears_on_a_database_that_predates_it(self):
        """The database is committed to the repo, so it outlives this change."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "old.db"
            old = schema.TABLES.split("CREATE TABLE IF NOT EXISTS employer_identity")[0]
            raw = sqlite3.connect(db)
            raw.executescript(old)
            raw.close()
            conn = schema.connect(db)
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            conn.close()
        self.assertIn("employer_identity", tables)

    def test_a_round_trip_preserves_every_field(self):
        conn = _db()
        ident = identity.Identity(
            company_key="apple", company="Apple Inc.", qid="Q312", ticker="AAPL",
            cik="320193", hq_city="San Francisco", hq_country="US",
            employer_type="public", resolved=True, detail="wikidata Q312")
        identity.cache_put(conn, ident)
        back = identity.cache_get(conn, "apple")
        self.assertEqual(back, ident)


class Backfill(unittest.TestCase):

    def _seed(self, conn, rows):
        for i, (company, key) in enumerate(rows):
            conn.execute(
                """INSERT INTO signals (signal_id, headline, summary,
                     talent_readthrough, company, company_key, pillar,
                     signal_direction, confidence, source_url, source_name,
                     captured_at, as_of, content_hash, collector)
                   VALUES (?,'h','s','t',?,?,'leadership','neutral','reported',
                           'https://e.com/a','E','2026-07-28','2026-07-28',?,'test')""",
                (f"s{i}", company, key, f"hash{i}"))

    def test_it_fills_rows_and_reports_what_it_filled(self):
        conn = _db()
        self._seed(conn, [("Apple Inc.", "apple"), ("Apple Inc.", "apple"),
                          ("Blorptech Holdings Inc.", "blorptech holdings")])
        fx = Fixture({"apple": APPLE_SEARCH}, APPLE_PROPS)
        with _Patched(self, fx):
            stats = identity.backfill(conn, verbose=False)

        self.assertEqual(stats["employers"], 2)
        self.assertEqual(stats["resolved"], 1)
        self.assertEqual(stats["unresolved"], 1)
        self.assertEqual(stats["rows"]["ticker"], 2)
        self.assertEqual(stats["rows"]["cik"], 2)
        tickers = {r[0] for r in conn.execute(
            "SELECT ticker FROM signals WHERE company_key = 'apple'")}
        self.assertEqual(tickers, {"AAPL"})

    def test_chunking_moves_forward_instead_of_re_walking_the_same_names(self):
        """`--limit 240` twice must cover 480 employers, not 240 twice.

        Selecting on "any identity column is NULL" did the latter: hq_city
        stays NULL for most employers forever (45 cities in the vocabulary),
        so the same names matched every run and the tail was never reached.
        """
        conn = _db()
        self._seed(conn, [(f"Co{i} Inc.", f"co{i}") for i in range(6)])
        searches = {f"co{i}": APPLE_SEARCH for i in range(6)}
        with _Patched(self, Fixture(searches, APPLE_PROPS)):
            first = identity.backfill(conn, limit=3, verbose=False)
            second = identity.backfill(conn, limit=3, verbose=False)
            third = identity.backfill(conn, limit=3, verbose=False)
        self.assertEqual((first["employers"], second["employers"]), (3, 3))
        self.assertEqual(third["employers"], 0)      # all six are done
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM employer_identity").fetchone()[0], 6)

    def test_a_second_run_is_a_no_op(self):
        conn = _db()
        self._seed(conn, [("Apple Inc.", "apple")])
        fx = Fixture({"apple": APPLE_SEARCH}, APPLE_PROPS)
        with _Patched(self, fx):
            identity.backfill(conn, verbose=False)
            calls = fx.sparql_calls
            again = identity.backfill(conn, verbose=False)
        self.assertEqual(fx.sparql_calls, calls)      # cache answered
        self.assertEqual(sum(again["rows"].values()), 0)

    def test_dry_run_writes_nothing(self):
        conn = _db()
        self._seed(conn, [("Apple Inc.", "apple")])
        with _Patched(self, Fixture({"apple": APPLE_SEARCH}, APPLE_PROPS)):
            identity.backfill(conn, verbose=False, dry_run=True)
        self.assertIsNone(conn.execute("SELECT ticker FROM signals").fetchone()[0])

    def test_the_limit_takes_the_employers_with_the_most_rows(self):
        conn = _db()
        self._seed(conn, [("Apple Inc.", "apple"), ("Apple Inc.", "apple"),
                          ("Nobody Ltd", "nobody")])
        with _Patched(self, Fixture({"apple": APPLE_SEARCH}, APPLE_PROPS)):
            stats = identity.backfill(conn, limit=1, verbose=False)
        self.assertEqual(stats["employers"], 1)
        self.assertEqual(stats["resolved"], 1)


if __name__ == "__main__":
    unittest.main()
