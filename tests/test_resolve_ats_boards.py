"""The board resolver proposes watchlist entries, so its job is to be WRONG
rarely rather than to find many.

Two failures are the ones that matter, and both are asserted here:

* a slug that exists but belongs to somebody else. `uniti` on Ashby is a
  startup called Uniti AI, not the Uniti Group REIT whose filings we hold. The
  name published by the board is the only thing that catches it, and for Ashby
  that name is a page title where "ours is contained in theirs" is not evidence
  of anything.
* a board too small to watch. Below ten roles a normal quiet week empties it,
  and the collector cannot tell an empty board from a dead slug.

Everything here is offline: the probes are replaced at the dict, never in
`sys.modules`.
"""

from __future__ import annotations

import threading
import time
import unittest

import resolve_ats_boards as resolver


class CleanBoardTitle(unittest.TestCase):
    def test_strips_what_a_board_page_appends(self):
        self.assertEqual(resolver.clean_board_title("Deliveroo Jobs"), "Deliveroo")
        self.assertEqual(resolver.clean_board_title("Ramp Careers"), "Ramp")
        self.assertEqual(resolver.clean_board_title("Jobs at Monzo"), "Monzo")
        self.assertEqual(resolver.clean_board_title("Abridge - Job Board"), "Abridge")

    def test_leaves_a_name_that_is_only_a_name(self):
        self.assertEqual(resolver.clean_board_title("Uniti AI"), "Uniti AI")

    def test_survives_nothing(self):
        self.assertEqual(resolver.clean_board_title(""), "")
        self.assertEqual(resolver.clean_board_title(None), "")


class NamesAgree(unittest.TestCase):
    def test_containment_holds_for_an_account_name(self):
        # Greenhouse and Workable publish the account's own spelling, so a legal
        # name and a brand name are the same employer.
        self.assertTrue(resolver.names_agree("Recursion Pharmaceuticals, Inc.",
                                             "Recursion"))
        self.assertTrue(resolver.names_agree("Cloudflare, Inc.", "Cloudflare"))

    def test_exact_refuses_a_qualifier_the_loose_rule_would_allow(self):
        # 'BYD North America' is BYD's board on Greenhouse, which publishes an
        # account name. As a bare page title it would be evidence of nothing.
        self.assertTrue(resolver.names_agree("BYD", "BYD North America"))
        self.assertFalse(resolver.names_agree("BYD", "BYD North America",
                                              exact=True))

    def test_a_regional_arm_of_the_same_employer_still_agrees(self):
        # greenhouse:byd publishes 'BYD North America'. That is BYD's board.
        self.assertTrue(resolver.names_agree("BYD", "BYD North America"))
        self.assertTrue(resolver.names_agree("Sanofi", "Sanofi US"))

    def test_an_extra_word_that_is_not_a_qualifier_is_a_different_company(self):
        self.assertFalse(resolver.names_agree("Uniti Group Inc.", "Uniti AI"))
        self.assertFalse(resolver.names_agree("Apple", "Apple Hospitality"))

    def test_exact_still_accepts_the_same_employer(self):
        self.assertTrue(resolver.names_agree("Deliveroo plc", "Deliveroo",
                                             exact=True))

    def test_ashby_is_the_one_on_the_exact_rule(self):
        self.assertEqual(resolver.EXACT_NAME_MATCH, {"ashby"})


class NeedsReview(unittest.TestCase):
    """An exactly matching name proves nothing when the name is a word other
    companies also use. `ashby:ditto` published "DITTO", matched our DITTO
    exactly, and is a different company: ours sells menstrual-health
    supplements and the board is hiring Bluetooth and database engineers."""

    def test_a_short_one_word_name_is_sent_to_a_human(self):
        for name in ("DITTO", "Assured", "Corgi", "Uniti Group Inc.",
                     "Antares", "Symphony Ltd"):
            self.assertTrue(resolver.needs_review(name), name)

    def test_a_coined_name_stands_on_its_own(self):
        for name in ("Cloudflare, Inc.", "Fluidstack Ltd", "Anthropic",
                     "10x Genomics, Inc."):
            self.assertFalse(resolver.needs_review(name), name)

    def test_two_words_are_already_two_pieces_of_evidence(self):
        self.assertFalse(resolver.needs_review("Blue Moon Metals Inc."))

    def test_nothing_is_not_reviewable(self):
        self.assertFalse(resolver.needs_review(""))


class Pacing(unittest.TestCase):
    def test_one_host_is_serialised_however_many_workers(self):
        pace = resolver.Pace({"slowhost": 0.05})
        stamps: list[float] = []
        guard = threading.Lock()

        def hit():
            pace.wait("slowhost")
            with guard:
                stamps.append(time.monotonic())

        threads = [threading.Thread(target=hit) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        stamps.sort()
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertEqual(len(gaps), 3)
        for gap in gaps:
            self.assertGreaterEqual(gap, 0.04)

    def test_an_unknown_host_is_not_an_error(self):
        resolver.Pace({}).wait("nothing-configured")


class _Probing(unittest.TestCase):
    """`probe_company` against stubbed probes. The dict is restored on teardown,
    so nothing leaks into the tests that run after this one."""

    results: dict = {}

    def setUp(self):
        self._real = dict(resolver.PROBES)
        for ats in resolver.PROBES:
            resolver.PROBES[ats] = (
                lambda slug, _ats=ats: self.results.get(f"{_ats}:{slug}"))
        self.pace = resolver.Pace({ats: 0.0 for ats in resolver.PROBES})

    def tearDown(self):
        resolver.PROBES.clear()
        resolver.PROBES.update(self._real)

    def probe(self, company, *, ats=("greenhouse", "ashby"), min_count=10):
        return resolver.probe_company(company, 3, allowed=list(ats),
                                      known=set(), pace=self.pace,
                                      min_count=min_count)


class ProbeCompany(_Probing):
    results = {
        "greenhouse:cloudflare": (41, "Cloudflare, Inc."),
        "greenhouse:tinyco": (4, "Tiny Co"),
        "ashby:uniti": (10, "Uniti AI"),
        "ashby:deliveroo": (182, "Deliveroo"),
    }

    def test_a_real_board_is_a_hit_with_its_name_evidence(self):
        hit = self.probe("Cloudflare, Inc.")
        self.assertEqual(hit["outcome"], "hit")
        self.assertEqual(hit["ats"], "greenhouse")
        self.assertEqual(hit["evidence"], "board_name")
        self.assertEqual(hit["verified_count"], 41)

    def test_somebody_elses_board_is_a_mismatch_and_never_a_hit(self):
        reject = self.probe("Uniti Group Inc.")
        self.assertEqual(reject["outcome"], "mismatch")
        self.assertEqual(reject["published_name"], "Uniti AI")

    def test_a_board_under_the_bar_is_recorded_not_proposed(self):
        reject = self.probe("Tiny Co")
        self.assertEqual(reject["outcome"], "too_small")
        self.assertEqual(reject["verified_count"], 4)

    def test_the_bar_is_a_setting_not_a_law_of_nature(self):
        hit = self.probe("Tiny Co", min_count=2)
        self.assertEqual(hit["outcome"], "hit")

    def test_no_board_anywhere_is_no_row(self):
        self.assertIsNone(self.probe("A Company With No Board"))

    def test_a_known_slug_is_never_re_probed(self):
        result = resolver.probe_company(
            "Deliveroo", 3, allowed=["ashby"], known={"ashby:deliveroo"},
            pace=self.pace, min_count=10)
        self.assertIsNone(result)


class ProbeErrors(_Probing):
    def setUp(self):
        super().setUp()

        def explode(slug):
            raise resolver.requests.RequestException("host is down")

        resolver.PROBES["greenhouse"] = explode
        resolver.PROBES["ashby"] = lambda slug: (12, "Deliveroo")

    def test_one_dead_ats_does_not_lose_the_employer(self):
        hit = self.probe("Deliveroo", ats=("greenhouse", "ashby"))
        self.assertEqual(hit["outcome"], "hit")
        self.assertEqual(hit["ats"], "ashby")

    def test_a_swallowed_error_is_counted_rather_than_forgotten(self):
        # Otherwise a throttled sweep and an empty pool are the same silence.
        tally = resolver.Tally()
        resolver.probe_company("Deliveroo", 3, allowed=["greenhouse", "ashby"],
                               known=set(), pace=self.pace, min_count=10,
                               tally=tally)
        self.assertEqual(sum(tally.counts.values()), 1)
        self.assertIn("greenhouse RequestException", tally.counts)
        self.assertIn("1 x greenhouse RequestException", tally.report())


class RateLimitIsNotAnEmptyPool(_Probing):
    def setUp(self):
        super().setUp()

        class Throttled(resolver.requests.HTTPError):
            def __init__(self):
                super().__init__("429")
                self.response = type("R", (), {"status_code": 429})()

        def throttle(slug):
            raise Throttled()

        resolver.PROBES["workable"] = throttle

    def test_a_429_is_reported_as_a_429(self):
        tally = resolver.Tally()
        resolver.probe_company("Somebody Ltd", 1, allowed=["workable"],
                               known=set(), pace=self.pace, min_count=10,
                               tally=tally)
        self.assertIn("workable HTTP 429", tally.counts)


class MergeCandidates(unittest.TestCase):
    """The merge is where a verified candidate becomes a watched board, so
    every rule the watchlist states has to hold HERE, not in a human's head."""

    def merge(self, candidates, boards=(), **kwargs):
        payload = {"boards": [dict(b) for b in boards]}
        added, refused = resolver.merge_candidates(candidates, payload, **kwargs)
        return added, refused, payload

    def hit(self, **over):
        base = {"ats": "greenhouse", "slug": "cloudflare",
                "company": "Cloudflare, Inc.", "verified_count": 41,
                "evidence": "board_name", "published_name": "Cloudflare",
                "signals": 3}
        base.update(over)
        return base

    def test_a_verified_board_lands(self):
        added, refused, payload = self.merge([self.hit()])
        self.assertEqual(refused, [])
        self.assertEqual(added[0]["slug"], "cloudflare")
        self.assertEqual(payload["boards"][0]["company"], "Cloudflare")

    def test_the_spelling_that_is_not_shouting_wins(self):
        # Both are the employer's own name and both key the same. Only one of
        # them belongs on a page.
        added, _refused, _ = self.merge([self.hit(
            slug="agilysys", company="AGILYSYS, INC.",
            published_name="Agilysys", reviewed=True)])
        self.assertEqual(added[0]["company"], "Agilysys")

    def test_a_shouting_board_does_not_overwrite_a_readable_name(self):
        added, _refused, _ = self.merge([self.hit(
            slug="cerus", company="Cerus Corporation",
            published_name="CERUS CORPORATION", reviewed=True)])
        self.assertEqual(added[0]["company"], "Cerus Corporation")

    def test_under_the_bar_never_lands(self):
        added, refused, _ = self.merge([self.hit(verified_count=6)])
        self.assertEqual(added, [])
        self.assertIn("under 10", refused[0]["refused_because"])

    def test_slug_only_is_a_human_decision_not_a_merge(self):
        added, refused, _ = self.merge(
            [self.hit(evidence="slug_only", published_name=None)])
        self.assertEqual(added, [])
        self.assertIn("no published name", refused[0]["refused_because"])

    def test_slug_only_can_be_taken_deliberately(self):
        added, _refused, _ = self.merge(
            [self.hit(evidence="slug_only", published_name=None)],
            allow_slug_only=True)
        self.assertEqual(len(added), 1)

    def test_the_merge_re_checks_the_name_rather_than_trusting_the_label(self):
        # The candidate says board_name because the rule said so when it was
        # probed. The rule can tighten afterwards, and the merge is the last
        # place that can catch it.
        added, refused, _ = self.merge([self.hit(
            slug="uniti", company="Uniti Group Inc.", ats="ashby",
            published_name="Uniti AI", evidence="board_name")])
        self.assertEqual(added, [])
        self.assertIn("Uniti AI", refused[0]["refused_because"])

    def test_what_the_board_called_itself_travels_into_the_file(self):
        added, _refused, _ = self.merge([self.hit(
            slug="byd", company="BYD", published_name="BYD North America",
            reviewed=True)])
        self.assertEqual(added[0]["company"], "BYD")
        self.assertEqual(added[0]["published_name"], "BYD North America")

    def test_a_one_word_name_does_not_land_unreviewed(self):
        added, refused, _ = self.merge([self.hit(
            slug="ditto", company="DITTO", published_name="DITTO",
            ats="ashby", verified_count=17)])
        self.assertEqual(added, [])
        self.assertIn("human", refused[0]["refused_because"])

    def test_a_one_word_name_lands_once_somebody_has_looked(self):
        added, _refused, _ = self.merge([self.hit(
            slug="antares", company="Antares", published_name="Antares",
            ats="ashby", verified_count=43, reviewed=True)])
        self.assertEqual(added[0]["slug"], "antares")

    def test_an_employer_never_gets_two_boards(self):
        # Two boards for one employer would count it twice in every aggregate.
        added, refused, _ = self.merge(
            [self.hit(), self.hit(ats="lever", slug="cloudflare-inc")])
        self.assertEqual(len(added), 1)
        self.assertIn("already has a board", refused[0]["refused_because"])

    def test_an_already_watched_slug_is_left_alone(self):
        added, refused, payload = self.merge(
            [self.hit()],
            boards=[{"ats": "greenhouse", "slug": "Cloudflare",
                     "company": "Cloudflare, Inc.", "verified_count": 40}])
        self.assertEqual(added, [])
        self.assertEqual(len(payload["boards"]), 1)
        self.assertIn(refused[0]["refused_because"],
                      ("already watched", "this employer already has a board"))

    def test_the_boards_name_is_taken_only_when_it_keys_the_same(self):
        # 'Recursion' keys as a different employer from 'Recursion
        # Pharmaceuticals', so the board's own spelling is refused and ours is
        # kept — otherwise the volume panel hangs on an empty second profile.
        added, _refused, _ = self.merge([self.hit(
            slug="recursionpharmaceuticals", company="Recursion Pharmaceuticals",
            published_name="Recursion")])
        self.assertEqual(added[0]["company"], "Recursion Pharmaceuticals")

    def test_the_bigger_board_wins_a_tie_between_two_slugs(self):
        added, _refused, _ = self.merge([
            self.hit(slug="small", verified_count=11),
            self.hit(ats="ashby", slug="big", verified_count=300)])
        self.assertEqual([a["slug"] for a in added], ["big"])

    def test_the_file_stays_sorted_so_the_diff_stays_readable(self):
        _added, _refused, payload = self.merge([
            self.hit(ats="lever", slug="zzz", company="Zzz Ltd",
                     published_name="Zzz", reviewed=True),
            self.hit(ats="ashby", slug="aaa", company="Aaa Ltd",
                     published_name="Aaa Ltd", reviewed=True),
            self.hit()])
        self.assertEqual([(b["ats"], b["slug"]) for b in payload["boards"]],
                         [("ashby", "aaa"), ("greenhouse", "cloudflare"),
                          ("lever", "zzz")])


class VerifyBoard(_Probing):
    """A watched board is evidence about the day it was added and no other. A
    slug gets renamed, a board comes down, an account changes hands — and none
    of that is distinguishable from a quiet week to a collector reading an API
    that answers 200 with an empty list."""

    results = {
        "greenhouse:cloudflare": (41, "Cloudflare, Inc."),
        "greenhouse:shrunk": (3, "Shrunk Ltd"),
        "greenhouse:sold": (60, "Somebody Else Entirely"),
    }

    def verify(self, entry, min_count=10):
        return resolver.verify_board(entry, pace=self.pace, min_count=min_count)

    def test_a_healthy_board_verifies(self):
        out = self.verify({"ats": "greenhouse", "slug": "cloudflare",
                           "company": "Cloudflare, Inc."})
        self.assertEqual(out["state"], "ok")
        self.assertEqual(out["verified_count"], 41)

    def test_a_board_that_is_gone_says_so(self):
        out = self.verify({"ats": "greenhouse", "slug": "vanished",
                           "company": "Vanished Inc"})
        self.assertEqual(out["state"], "gone")

    def test_a_board_that_shrank_under_the_bar_says_so(self):
        out = self.verify({"ats": "greenhouse", "slug": "shrunk",
                           "company": "Shrunk Ltd"})
        self.assertEqual(out["state"], "small")

    def test_a_board_that_changed_hands_is_the_loudest_case(self):
        out = self.verify({"ats": "greenhouse", "slug": "sold",
                           "company": "Cloudflare, Inc."})
        self.assertEqual(out["state"], "wrong_company")
        self.assertIn("Somebody Else Entirely", out["detail"])

    def test_an_ats_with_no_probe_is_not_reported_as_broken(self):
        out = self.verify({"ats": "smartrecruiters", "slug": "Wise",
                           "company": "Wise"})
        self.assertEqual(out["state"], "unsupported")


class DumpWatchlist(unittest.TestCase):
    """The file is written one board per line on purpose. Six boards over six
    lines each is a diff nobody reads, and this is the file where an unread
    diff means watching somebody else's board."""

    payload = {
        "_comment": ["why this file exists"],
        "boards": [
            {"ats": "ashby", "slug": "abridge", "company": "Abridge",
             "verified_count": 44},
            {"ats": "greenhouse", "slug": "asana", "company": "Asana",
             "verified_count": 147},
            {"ats": "greenhouse", "slug": "block", "company": "Block, Inc.",
             "verified_count": 213},
        ],
        "withdrawn": [{"ats": "smartrecruiters", "slug": "Wise",
                       "company": "Wise", "verified_count": 401,
                       "reason": "robots"}],
    }

    def test_it_reparses_to_what_went_in(self):
        import json as _json
        self.assertEqual(_json.loads(resolver.dump_watchlist(self.payload)),
                         self.payload)

    def test_one_board_is_one_line(self):
        text = resolver.dump_watchlist(self.payload)
        self.assertIn('    {"ats": "greenhouse", "slug": "asana", '
                      '"company": "Asana", "verified_count": 147},', text)

    def test_a_blank_line_where_the_ats_changes(self):
        lines = resolver.dump_watchlist(self.payload).splitlines()
        asana = next(i for i, l in enumerate(lines) if "asana" in l)
        self.assertEqual(lines[asana - 1], "")

    def test_a_name_with_an_accent_stays_readable(self):
        payload = dict(self.payload, boards=[
            {"ats": "lever", "slug": "voodoo", "company": "Vöô Ltd",
             "verified_count": 12}])
        self.assertIn("Vöô Ltd", resolver.dump_watchlist(payload))


class SlugCandidates(unittest.TestCase):
    def test_both_the_stripped_and_the_whole_name_are_tried(self):
        slugs = resolver.slug_candidates("Match Group, Inc.")
        self.assertIn("match", slugs)
        self.assertTrue(any("matchgroup" in s for s in slugs))

    def test_never_more_than_the_cap(self):
        slugs = resolver.slug_candidates("A Very Long Legal Name Holdings Limited")
        self.assertLessEqual(len(slugs), resolver.MAX_SLUGS)

    def test_a_name_too_short_to_guess_is_not_guessed(self):
        self.assertEqual(resolver.slug_candidates("X"), [])


class TheShippedWatchlist(unittest.TestCase):
    """Invariants of the file itself, so a hand edit cannot quietly break what
    the merge rule guarantees."""

    def setUp(self):
        import json

        from collectors import ats_boards
        self.payload = json.loads(ats_boards.WATCHLIST_PATH.read_text())
        self.boards = self.payload["boards"]

    def test_no_employer_has_two_boards(self):
        from pipeline import vocab
        keys = [vocab.company_key(b["company"]) for b in self.boards]
        doubled = {k for k in keys if keys.count(k) > 1}
        self.assertEqual(doubled, set(),
                         "two boards for one employer double-count it")

    def test_no_slug_appears_twice_on_one_ats(self):
        ids = [f"{b['ats']}:{b['slug'].lower()}" for b in self.boards]
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_near_misses_are_named_the_way_our_filings_name_them(self):
        """A board keyed one word away from our filings is worse than a board
        with no filings at all: greenhouse:coinbase filed as "Coinbase" keys as
        `coinbase` while the 8-Ks are under `coinbase global`, so one company
        gets two profiles and neither of them is complete."""
        from pipeline import vocab
        names = {b["company"] for b in self.boards}
        self.assertIn("Coinbase Global, Inc.", names)
        self.assertIn("Robinhood Markets, Inc.", names)
        self.assertEqual(vocab.company_key("Coinbase Global, Inc."),
                         "coinbase global")

    def test_every_board_was_recorded_above_the_bar(self):
        for board in self.boards:
            self.assertGreaterEqual(board.get("verified_count", 0),
                                    resolver.MIN_COUNT, board)


if __name__ == "__main__":
    unittest.main()
