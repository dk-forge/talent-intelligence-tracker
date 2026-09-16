"""The aggregator rule is about the CANONICAL host, not the requested one.

WHY THIS FILE EXISTS
--------------------
Three rows were live on 2026-07-30 citing `*.yahoo.com`. `_BLOCKED_SOURCE_HOSTS`
listed `news.yahoo.com` by exact host, so `finance.yahoo.com` and
`sg.finance.yahoo.com` walked straight past it. The feed loader in
`collectors/national_press.py` had ALREADY learned to match on the registrable
domain and derives `_AGGREGATOR_DOMAINS` that way; so one rule existed in two
layers, and the layer deciding what may be STORED was the weaker one.

A blanket domain block would have been the wrong fix, and that is measured
rather than asserted. Checking `rel=canonical` on the three:

    finance.yahoo.com/.../7-eleven-names-ceo   -> cstoredive.com  (a publisher
                                                  this project already reads)
    finance.yahoo.com/.../haus-cramer-gruppe   -> HTTP 404, no canonical at all
    sg.finance.yahoo.com/news/hsbc-plans-hire  -> ITSELF

So the correct rule is the canonical host: follow the pointer, store what it
points at, and refuse only what is an aggregator all the way down. That is
CLAUDE.md's "aggregators are discovery pointers" being obeyed rather than
excepted.
"""

import unittest

from pipeline import validate


class TheSubdomainHole(unittest.TestCase):
    def test_every_yahoo_subdomain_is_an_aggregator_now(self):
        # The exact-host list never covered these two and they are the hosts
        # the three live rows actually came from.
        for host in ("news.yahoo.com", "finance.yahoo.com",
                     "sg.finance.yahoo.com", "uk.finance.yahoo.com"):
            self.assertTrue(validate.is_aggregator_host(host), host)

    def test_the_other_listed_aggregators_cover_their_subdomains_too(self):
        for host in ("news.google.com", "www.msn.com", "flipboard.com",
                     "assets.msn.com", "about.flipboard.com"):
            self.assertTrue(validate.is_aggregator_host(host), host)

    def test_a_publisher_is_not_caught_by_any_of_it(self):
        for host in ("www.cstoredive.com", "www.just-drinks.com",
                     "www.sec.gov", "www.calcalistech.com", "blog.google"):
            self.assertFalse(validate.is_aggregator_host(host), host)

    def test_the_domain_set_is_derived_and_not_typed(self):
        # Typed twice, the two lists drift and the subdomain hole reopens. This
        # asserts the derivation rather than the contents: every host the store
        # refuses reduces to a blocked domain, the four general aggregators are
        # among them, and nothing is in the set that no host put there.
        from collectors.national_press import registrable_domain
        derived = frozenset(
            registrable_domain(h) for h in validate._blocked_hosts())
        self.assertEqual(validate._blocked_domains(), derived)
        self.assertLessEqual(
            frozenset({"google.com", "yahoo.com", "msn.com", "flipboard.com"}),
            validate._blocked_domains())

    def test_the_store_and_the_feed_loader_hold_one_list(self):
        # 2026-09-16: 79 published rows cited a commercial data provider's
        # "news note" pages. The feed loader had refused that host since July;
        # this module, which decides what is STORED, had never heard of it.
        # One list now, and this is the assertion that keeps it one.
        from collectors import national_press
        self.assertLessEqual(national_press._AGGREGATOR_HOSTS,
                             validate._blocked_hosts())
        for host in national_press._AGGREGATOR_HOSTS:
            if host in national_press._EDITORIAL_EXCEPTIONS:
                continue
            self.assertTrue(validate.is_aggregator_host(host),
                            "a loader-refused host the store accepts (masked)")


def _raw(url, canonical=None, **kw):
    raw = {
        "source_url": url,
        "raw_text": "Some company said it will hire 100 people in Dublin.",
        "source_name": "Test",
    }
    if canonical is not None:
        raw["canonical_url"] = canonical
    raw.update(kw)
    return raw


class TheCanonicalDecides(unittest.TestCase):
    def test_an_aggregator_url_whose_canonical_is_a_publisher_is_kept(self):
        raw = _raw("https://finance.yahoo.com/small-business/articles/7-eleven-names-ceo-171946816.html",
                   "https://www.cstoredive.com/news/7-eleven-names-new-ceo/826096/")
        validate.precheck(raw)   # must not raise
        # ...and the row now cites the PUBLISHER, not the pointer. This is the
        # half that matters: a rule that only refused would lose the story.
        self.assertEqual(raw["source_url"],
                         "https://www.cstoredive.com/news/7-eleven-names-new-ceo/826096/")

    def test_an_aggregator_that_canonicalises_to_itself_is_still_refused(self):
        raw = _raw("https://sg.finance.yahoo.com/news/hsbc-plans-hire-100-ai-190530552.html",
                   "https://sg.finance.yahoo.com/news/hsbc-plans-hire-100-ai-190530552.html")
        with self.assertRaises(validate.Rejected):
            validate.precheck(raw)

    def test_an_aggregator_with_no_canonical_at_all_is_refused(self):
        # The 404 case. Nothing to follow, so nothing to credit.
        raw = _raw("https://finance.yahoo.com/small-business/articles/warsteiner-owner-haus-cramer-gruppe-130726533")
        with self.assertRaises(validate.Rejected):
            validate.precheck(raw)

    def test_a_canonical_pointing_at_another_aggregator_is_not_followed(self):
        raw = _raw("https://finance.yahoo.com/news/x-123.html",
                   "https://news.google.com/articles/abc")
        with self.assertRaises(validate.Rejected):
            validate.precheck(raw)
        # and the row was not quietly rewritten to the second aggregator
        self.assertIn("finance.yahoo.com", raw["source_url"])

    def test_a_canonical_that_is_a_bare_domain_is_ignored(self):
        # A homepage is not a receipt, so it cannot be an upgrade either.
        raw = _raw("https://finance.yahoo.com/news/x-123.html",
                   "https://www.cstoredive.com/")
        with self.assertRaises(validate.Rejected):
            validate.precheck(raw)
        self.assertIn("finance.yahoo.com", raw["source_url"])

    def test_a_publisher_url_is_untouched_whether_or_not_it_states_a_canonical(self):
        for canonical in (None, "https://www.cstoredive.com/news/x/1/"):
            raw = _raw("https://www.cstoredive.com/news/x/1/", canonical)
            validate.precheck(raw)
            self.assertEqual(raw["source_url"], "https://www.cstoredive.com/news/x/1/")

    def test_a_canonical_never_rescues_a_row_that_fails_another_check(self):
        # The canonical is not a bypass. A job advert is still a job advert.
        raw = _raw("https://finance.yahoo.com/news/x-123.html",
                   "https://www.example.com/careers/engineer-dublin")
        with self.assertRaises(validate.Rejected):
            validate.precheck(raw)


class ThePlantedProviderRow(unittest.TestCase):
    """A provider URL of exactly the shape the 79 live rows carry is refused
    at precheck, before any model is paid; the same company's exchange filing
    and a named outlet's article about a provider are not.

    Hosts come from the loader's encoded list and are never typed here: a test
    that spells the thing it forbids trips the tree-wide name guard.
    """

    def _provider_app_host(self):
        from collectors import national_press
        hosts = sorted(h for h in national_press._AGGREGATOR_HOSTS
                       if h.startswith("app."))
        self.assertTrue(hosts, "the loader's list holds no app.* provider host")
        return hosts[0]

    def test_a_provider_news_note_is_refused_as_a_stored_source(self):
        host = self._provider_app_host()
        raw = _raw(f"https://{host}/news/note/acme-raises-20m-series-a")
        with self.assertRaises(validate.Rejected) as caught:
            validate.precheck(raw)
        self.assertIn("aggregator stored as source", str(caught.exception))

    def test_a_provider_note_whose_canonical_is_a_publisher_is_kept(self):
        # The pointer is followed, exactly as for the general aggregators.
        host = self._provider_app_host()
        raw = _raw(f"https://{host}/news/note/acme-raises-20m-series-a",
                   "https://www.publisher-example.com/2026/09/acme-raises-20m/")
        validate.precheck(raw)
        self.assertEqual(raw["source_url"],
                         "https://www.publisher-example.com/2026/09/acme-raises-20m/")

    def test_an_exchange_filing_is_a_primary_document_whatever_the_filer_is_called(self):
        # bse_india: SEBI Regulation 30 filings served by the exchange. Two
        # live rows are for a listed company whose name contains a provider
        # name; the source is the exchange and the exchange is not an
        # aggregator. The company name is not typed here for the same reason
        # as above; the point is the host.
        raw = _raw("https://www.bseindia.com/corporates/anndet_new?newsid=89fb219e-ccb8-4298",
                   company="Some Technologies Ltd")
        validate.precheck(raw)
        self.assertFalse(validate.is_aggregator_host("www.bseindia.com"))

    def test_a_bylined_newsroom_on_a_provider_domain_is_still_a_publisher(self):
        from collectors import national_press
        for host in national_press._EDITORIAL_EXCEPTIONS:
            self.assertFalse(validate.is_aggregator_host(host))
            validate.precheck(_raw(f"https://{host}/venture/acme-raises-75m/"))

    def test_the_narrowed_classifier_asks_only_who_served_the_document(self):
        from analysis.extraction import frame
        host = self._provider_app_host()
        self.assertTrue(frame.sourced_from_an_aggregator(
            {"source_url": f"https://{host}/news/note/x"}))
        self.assertFalse(frame.sourced_from_an_aggregator(
            {"source_url": "https://www.bseindia.com/corporates/anndet_new?newsid=1",
             "company": "Named Like A Provider Technologies Ltd"}))
        self.assertFalse(frame.sourced_from_an_aggregator(
            {"source_url": "https://www.finsmes.com/2026/01/x-raises-7m.html"}))
        self.assertFalse(frame.sourced_from_an_aggregator({"source_url": ""}))


if __name__ == "__main__":
    unittest.main()
