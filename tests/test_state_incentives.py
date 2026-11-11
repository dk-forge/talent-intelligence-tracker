"""State job-creation incentive awards: the planned-hiring signal.

Two sources, one shape:

  TX  Texas Enterprise Fund awards, read from the Governor's own press
      releases on gov.texas.gov. The release is the source_url.
  NY  Empire State Development's Excelsior Jobs Program rows in the state's
      Database of Economic Incentives (data.ny.gov 26ei-n4eb), which states a
      job-creation commitment as a column.

Every row is a PROMISE, not a hire and not a job posting, so it is stored with
site_event 'planned_hiring', headcount_scope 'promised', money_basis
'state_funding', and it is kept out of every public output unless
PLANNED_HIRING_ENABLED is on. The flag defaults off.

Fixtures in tests/fixtures/state_incentives/ are trimmed real pages and rows,
except tx_recapture_synthetic.html, which says so on its first line.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from collectors import state_incentives as si
from pipeline import planned_hiring, publish, schema, store, validate

FIX = Path(__file__).parent / "fixtures" / "state_incentives"
ROOT = Path(__file__).resolve().parent.parent


def _tx(name: str, url: str = "https://gov.texas.gov/news/post/example"):
    return si.parse_tx_release((FIX / name).read_text(), url)


def _ny() -> list[dict]:
    return si.parse_ny_rows(json.loads((FIX / "ny_excelsior.json").read_text()),
                            since="2026-01-01")


class JobCountIsExplicitOrAbsent(unittest.TestCase):

    def test_create_n_new_jobs(self):
        self.assertEqual(si.explicit_jobs("will create 250 new jobs."), 250)

    def test_create_n_jobs_with_adjectives_and_commas(self):
        self.assertEqual(si.explicit_jobs("and will create 1,000 high-quality jobs"), 1000)

    def test_tef_tied_count(self):
        self.assertEqual(si.explicit_jobs(
            "has been extended to IEM for 448 of the new jobs to be created."), 448)

    def test_hedged_wording_is_never_a_count(self):
        for text in ("will create more than 2,000 new jobs",
                     "will create nearly 500 new jobs",
                     "and up to 3,000 new jobs",
                     "will create approximately 300 jobs",
                     "will create hundreds of jobs",
                     "will create at least 40 new jobs"):
            with self.subTest(text=text):
                self.assertIsNone(si.explicit_jobs(text))

    def test_no_number_no_count(self):
        self.assertIsNone(si.explicit_jobs("to create new, good-paying jobs"))


class TexasEnterpriseFund(unittest.TestCase):

    def test_liteon_award(self):
        row = _tx("tx_liteon.html")
        self.assertEqual(row["company"], "LITEON")
        self.assertEqual(row["jobs"], 500)
        self.assertEqual(row["grant"], "$3.5 million")
        self.assertEqual(row["published_date"], "2026-07-13")
        self.assertEqual(row["city"], "McKinney")
        self.assertEqual(row["state"], "TX")
        self.assertEqual(row["source_url"], "https://gov.texas.gov/news/post/example")
        self.assertIn("create 500 jobs", row["raw_text"])

    def test_hedged_award_is_kept_with_no_headcount(self):
        row = _tx("tx_blue_origin_hedged.html")
        self.assertEqual(row["company"], "Blue Origin")
        self.assertIsNone(row["jobs"])
        self.assertEqual(row["grant"], "$18,171,000")

    def test_county_title_is_not_a_city(self):
        row = _tx("tx_iem_tef_jobs.html")
        self.assertEqual(row["jobs"], 448)
        self.assertEqual(row["city"], "")

    def test_remarks_repeating_an_old_award_is_not_an_award(self):
        self.assertIsNone(_tx("tx_lilly_remarks.html"))

    def test_a_release_with_no_tef_award_is_ignored(self):
        self.assertIsNone(_tx("tx_not_tef.html"))

    def test_recapture_release_is_excluded(self):
        self.assertIsNone(_tx("tx_recapture_synthetic.html"))

    def test_listing_yields_post_urls_only(self):
        urls = si.tx_listing_urls((FIX / "tx_listing.html").read_text())
        self.assertEqual(len(urls), 8)
        self.assertTrue(all(u.startswith("https://gov.texas.gov/news/post/") for u in urls))


class EmpireStateExcelsior(unittest.TestCase):

    def test_active_awards_since_the_window(self):
        names = [r["company"] for r in _ny()]
        # Balchem (2025-12-16) and the 2019 award fall before the window.
        self.assertEqual(sorted(names), ["Marex", "Regeneron Pharmaceuticals, Inc."])

    def test_terminated_rows_are_excluded(self):
        rows = si.parse_ny_rows(json.loads((FIX / "ny_excelsior.json").read_text()),
                                since="2000-01-01")
        self.assertNotIn("H&H Environmental Systems Inc.", [r["company"] for r in rows])

    def test_commitment_is_the_headcount(self):
        regeneron = next(r for r in _ny() if r["company"].startswith("Regeneron"))
        self.assertEqual(regeneron["jobs"], 1000)
        self.assertEqual(regeneron["published_date"], "2026-06-17")
        self.assertIn("1000", regeneron["raw_text"])
        self.assertTrue(regeneron["source_url"].startswith("https://data.ny.gov/"))

    def test_zero_commitment_is_no_headcount(self):
        rows = si.parse_ny_rows(json.loads((FIX / "ny_excelsior.json").read_text()),
                                since="2025-01-01")
        balchem = next(r for r in rows if r["company"] == "Balchem Corporation")
        self.assertIsNone(balchem["jobs"])


class StoredShape(unittest.TestCase):

    def _signal(self, item):
        return validate.build_signal(si.as_classified(item), item, "state_incentives")

    def test_tx_row_shape(self):
        sig = self._signal(_tx("tx_liteon.html"))
        self.assertEqual(sig.site_event, "planned_hiring")
        self.assertEqual(sig.headcount, 500)
        self.assertEqual(sig.headcount_scope, "promised")
        self.assertEqual(sig.money_basis, "state_funding")
        self.assertEqual(sig.source_url, "https://gov.texas.gov/news/post/example")
        self.assertEqual(sig.signal_direction, "hiring")

    def test_label_and_never_jobs_posted(self):
        cls = si.as_classified(_tx("tx_liteon.html"))
        text = " ".join(str(v) for v in cls.values()).lower()
        self.assertIn("planned hiring signal (state incentive award)", text)
        self.assertNotIn("jobs posted", text)
        self.assertEqual(planned_hiring.LABEL,
                         "planned hiring signal (state incentive award)")

    def test_no_count_award_has_no_headcount_or_scope(self):
        sig = self._signal(_tx("tx_blue_origin_hedged.html"))
        self.assertIsNone(sig.headcount)
        self.assertIsNone(sig.headcount_scope)
        self.assertEqual(sig.money_basis, "state_funding")
        self.assertEqual(sig.site_event, "planned_hiring")

    def test_ny_row_shape(self):
        sig = self._signal(next(r for r in _ny() if r["company"] == "Marex"))
        self.assertEqual(sig.headcount, 150)
        self.assertEqual(sig.headcount_scope, "promised")
        self.assertEqual(sig.money_basis, "state_funding")

    def test_no_other_collector_can_claim_planned_hiring(self):
        item = _tx("tx_liteon.html")
        cls = dict(si.as_classified(item), site_event="planned_hiring",
                   headcount_scope="promised")
        sig = validate.build_signal(cls, item, "google_news")
        self.assertNotEqual(sig.site_event, "planned_hiring")
        self.assertNotEqual(sig.headcount_scope, "promised")


class FlagDefaultsOffAndHidesEverything(unittest.TestCase):
    """With PLANNED_HIRING_ENABLED off, every public output is byte-identical
    to a database that never held a planned-hiring row."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _db(self, name, *, with_planned: bool):
        conn = schema.connect(Path(self.tmp.name) / f"{name}.db")
        base = {
            "raw_text": "Acme Corp names Jane Doe chief executive officer.",
            "headline": "Acme Corp names Jane Doe CEO",
            "source_url": "https://acme.example/news/ceo",
            "source_name": "Acme newsroom",
            "published_date": "2026-07-01",
        }
        cls = {"company": "Acme Corp", "pillar": "leadership_change",
               "signal_direction": "neutral", "headline": base["headline"],
               "summary": "Acme Corp named Jane Doe chief executive officer.",
               "talent_readthrough": "A new chief executive.",
               "country": "US", "confidence": "reported"}
        store.store(conn, validate.build_signal(cls, base, "primary_chase"))
        if with_planned:
            for item in [_tx("tx_liteon.html", "https://gov.texas.gov/news/post/a"),
                         _tx("tx_blue_origin_hedged.html",
                             "https://gov.texas.gov/news/post/b")] + _ny():
                store.store(conn, validate.build_signal(si.as_classified(item), item,
                                                         "state_incentives"))
        conn.commit()
        return conn

    def _outputs(self, conn) -> bytes:
        def strip(rows):
            return [{k: v for k, v in r.items() if k != "row_id"} for r in rows]
        pending = strip(publish.unpublished(conn))
        count = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(headcount), 0) FROM signals "
            "WHERE is_current = 1" + planned_hiring.public_sql()).fetchone()
        return json.dumps({"publish": pending, "count": list(count)},
                          sort_keys=True, default=str).encode()

    def test_flag_defaults_off(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(planned_hiring.FLAG, None)
            self.assertFalse(planned_hiring.enabled())

    def test_public_outputs_are_byte_identical_with_the_flag_off(self):
        with mock.patch.dict(os.environ, {planned_hiring.FLAG: "false"}):
            clean = self._outputs(self._db("clean", with_planned=False))
            mixed = self._outputs(self._db("mixed", with_planned=True))
        self.assertEqual(clean, mixed)

    def test_flag_on_releases_the_rows(self):
        with mock.patch.dict(os.environ, {planned_hiring.FLAG: "true"}):
            conn = self._db("on", with_planned=True)
            events = {r["site_event"] for r in publish.unpublished(conn)}
        self.assertIn("planned_hiring", events)

    def test_enrich_never_pushes_a_hidden_row(self):
        guard = {"quarantined": set(), "aggregate": []}
        counts = {}
        for name, planned in (("enrich_clean", False), ("enrich_mixed", True)):
            conn = self._db(name, with_planned=planned)
            conn.execute("UPDATE signals SET published_at = '2026-07-02'")
            conn.commit()
            with mock.patch.dict(os.environ, {planned_hiring.FLAG: "0"}), \
                    mock.patch.object(publish, "_guard", return_value=guard):
                counts[name] = publish.enrich_published(conn, dry_run=True)
        self.assertEqual(counts["enrich_clean"].get("would_send", 0),
                         counts["enrich_mixed"].get("would_send", 0))

    def test_daily_digest_hides_them(self):
        import daily_digest
        conn = self._db("digest", with_planned=True)
        conn.close()
        with mock.patch.dict(os.environ, {planned_hiring.FLAG: ""}):
            now = datetime.now(timezone.utc)
            rows, ytd = daily_digest._load(Path(self.tmp.name) / "digest.db",
                                           now - timedelta(days=1),
                                           now + timedelta(minutes=5))
        self.assertEqual(ytd, 1)
        self.assertTrue(all(r["site_event"] != "planned_hiring" for r in rows))


class Registration(unittest.TestCase):

    def test_registered_in_run_collect(self):
        import run_collect
        self.assertIs(run_collect.SOURCES["state_incentives"], si)

    def test_registered_in_source_registry_but_not_live(self):
        import source_registry as registry
        self.assertIn("state_incentives", registry.DORMANT_COLLECTORS)
        self.assertNotIn("state_incentives",
                         set(registry.COLLECTOR_BY_SOURCE_NAME.values()))

    def test_nothing_schedules_it(self):
        for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
            self.assertNotIn("state_incentives", wf.read_text(), wf.name)


if __name__ == "__main__":
    unittest.main()
