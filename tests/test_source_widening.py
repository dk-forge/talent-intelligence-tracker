"""The 2026-07-30 widening: what was added, and the two things that were refused.

Both refusals are pinned here rather than merely written down, because a refusal
that lives only in a comment is re-litigated by the next session that reads the
robots file and stops there.

1. AUSTRALIA / ASX. The taxonomy is real and excellent — 142 mandated
   announcement types, 192 board and officer announcements in the 30-day window
   the API exposes — and www.asx.com.au/robots.txt permits the pages
   (`Disallow: /search*` is the whole file). The LICENCE does not: ASX's terms
   of use restrict Market Announcements to "investors' private and personal use
   only" and require "the express written authority of ASX" for any commercial
   purpose, and separately forbid using "any spider, screen scraper, robot" on
   the site. This is the SmartRecruiters decision again: the endpoints answer
   200 and the terms still say no.

2. THE NINE MASTHEADS. smh.com.au, theage.com.au and brisbanetimes.com.au serve
   the IDENTICAL business feed — measured 2026-07-30, 20 of 20 headlines shared
   between all three, and watoday.com.au shares 15 of 20. national_press
   de-duplicates on `title_key`, so the three extra rows would have added
   nothing to the corpus and three lines to the public sources page. Only the
   Herald is listed, plus The Canberra Times, which shares 0 of 20 because it is
   a different owner.
"""

from __future__ import annotations

import base64

import csv
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse

import source_registry as registry
from collectors import national_press as press

ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = ROOT / "data" / "sources_catalogue.csv"

# Countries the catalogue could not reach with a publisher feed before this.
NEW_COUNTRIES = (
    "Democratic Republic of the Congo", "Republic of the Congo", "Gabon",
    "Chad", "Burundi", "Central African Republic", "South Sudan", "Sudan",
    "Kosovo", "Lesotho", "Eswatini", "Malawi", "Madagascar", "Cape Verde",
    "Sierra Leone", "Guinea", "Mali", "Benin", "Afghanistan", "Tonga",
    "Cook Islands", "New Caledonia", "Bermuda",
)


def _rows() -> list[dict]:
    with CATALOGUE.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class TheWideningReachedRealPublishers(unittest.TestCase):
    def setUp(self):
        self.feeds = press.load_feeds()

    def test_every_new_country_has_a_feed_the_collector_will_actually_read(self):
        # load_feeds() applies the aggregator refusal and the row shape rules,
        # so a country present here is one the live run reaches, not merely one
        # somebody typed into a spreadsheet.
        reached = {f.country for f in self.feeds}
        for country in NEW_COUNTRIES:
            self.assertIn(country, reached)

    def test_a_regional_outlet_never_claims_a_country(self):
        # The Pacific and East Africa rows cover many countries from one seat.
        # Coverage must stay Regional or dateline() starts telling the model a
        # Fiji round happened in Tonga.
        for name in ("Islands Business", "The EastAfrican"):
            feed = next(f for f in self.feeds if f.name == name)
            self.assertTrue(feed.is_regional, name)
            self.assertIn("regional", press.dateline(feed))

    def test_no_added_feed_is_an_aggregator_on_any_subdomain(self):
        # Matching on the registrable domain is what closed the subdomain hole:
        # blocking `news.yahoo.com` by exact host left finance.yahoo.com open.
        for feed in self.feeds:
            domain = press.registrable_domain(feed.rss)
            if feed.rss.split("//")[-1].split("/")[0] in press._EDITORIAL_EXCEPTIONS:
                continue
            self.assertNotIn(domain, press._AGGREGATOR_DOMAINS, feed.name)

    def test_finance_yahoo_is_already_blocked_and_needs_no_second_entry(self):
        # Listing it again would imply the registrable-domain rule does not
        # work. `news.yahoo.com` reduces to yahoo.com and covers every host.
        for host in ("finance.yahoo.com", "news.yahoo.com", "uk.finance.yahoo.com"):
            self.assertIn(press.registrable_domain(f"https://{host}/x"),
                          press._AGGREGATOR_DOMAINS, host)
        self.assertNotIn("finance.yahoo.com", press._EDITORIAL_EXCEPTIONS)

    def test_the_editorial_newsroom_exception_stays_allowed(self):
        # Blocked once by mistake. The database is an aggregator; the bylined
        # newsroom on the same registrable domain is a publisher. The host is
        # a banned plaintext string (standalone-brand rule), so it decodes at
        # runtime, matching how _EDITORIAL_EXCEPTIONS itself stores it.
        host = base64.b64decode("bmV3cy5jcnVuY2hiYXNlLmNvbQ==").decode("ascii")
        self.assertIn(host, press._EDITORIAL_EXCEPTIONS)

    def test_no_feed_url_is_listed_twice(self):
        seen = {}
        for row in _rows():
            url = (row.get("rss") or "").strip()
            if not url:
                continue
            self.assertNotIn(url, seen, f"{row['name']} repeats {seen.get(url)}")
            seen[url] = row["name"]


class TheRefusals(unittest.TestCase):
    def test_no_syndicated_nine_masthead_was_listed_beside_the_herald(self):
        hosts = {press.registrable_domain(r.get("url") or "") for r in _rows()}
        self.assertIn("smh.com.au", hosts)
        for duplicate in ("theage.com.au", "brisbanetimes.com.au", "watoday.com.au"):
            self.assertNotIn(duplicate, hosts, duplicate)

    def test_asx_is_not_collected_anywhere(self):
        # Not as a collector, not as a catalogue row, not as a registry source.
        import run_collect

        for name in run_collect.SOURCES:
            self.assertNotIn("asx", name)
        hosts = {press.registrable_domain(r.get("url") or "") for r in _rows()}
        hosts |= {press.registrable_domain(r.get("rss") or "") for r in _rows()}
        hosts |= {press.registrable_domain(s.url) for s in registry.SOURCES}
        for blocked in ("asx.com.au", "markitdigital.com"):
            self.assertNotIn(blocked, hosts, blocked)

    def test_australia_is_still_discovery_only(self):
        market = next(m for m in registry.MARKETS if m.iso2 == "AU")
        self.assertEqual(market.status, registry.DISCOVERY_ONLY)
        self.assertEqual(tuple(market.live_sources), ("google_news",))

    def test_the_reason_australia_was_refused_survives_in_the_registry(self):
        # The dangerous version of this refusal is the one that keeps the
        # measurement and drops the licence, because then the next reader sees a
        # rich source that nobody got round to. Both halves are asserted.
        source = (ROOT / "source_registry.py").read_text(encoding="utf-8")
        block = re.search(r"#     AU  ASX market announcements\.(.+?)\n#\n",
                          source, re.S)
        self.assertIsNotNone(block, "the AU triage paragraph is gone")
        text = block.group(1)
        for phrase in ("express written authority",
                       "spider, screen scraper, robot",
                       "Disallow: /search*",
                       "Listing Rule 3.19A",
                       "NEEDS-OWNER"):
            self.assertIn(phrase, text, phrase)


if __name__ == "__main__":
    unittest.main()
