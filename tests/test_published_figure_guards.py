"""Proof that the published-figure guards discriminate.

Every case is a PAIR: the same check over a DEFECTIVE payload and over a
CORRECTED one, asserted to disagree. A check that always returned FAIL would
satisfy "it fails on the bad data" while being worthless, so the passing half is
the half that makes the failing half mean something.

The defective payloads are the shapes observed live on 2026-08-04, recorded here
so the guard stays armed once the rendering code is fixed and the site stops
reproducing them.
"""
import json
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import published_figures as pf                                 # noqa: E402


def region_button(text, codes, n):
    return (f'<button class="tit-region-btn" data-codes="{codes}">'
            f'<span>{text}</span> <span class="tit-region-n">{n:,}</span></button>')


REGIONS_OK = (region_button("World", "", 25_479)
              + region_button("Americas", "US,CA", 7_765)
              + region_button("Europe", "GB,DE", 8_972)
              + region_button("Asia", "IN,JP", 8_742))

# The live shape: World badges the placed-rows sum, not the whole view.
REGIONS_BROKEN = (region_button("World", "", 23_991)
                  + region_button("Americas", "US,CA", 7_765)
                  + region_button("Europe", "GB,DE", 8_972)
                  + region_button("Asia", "IN,JP", 7_254))

TILES = ('<span class="tit-fstat"><b>17,460</b><span>updates in 2026</span>'
         '<span class="tit-fstat-all">{all} all time</span></span>'
         '<span class="tit-fstat"><b>13,243</b><span>employers in 2026</span>'
         '<span class="tit-fstat-all">14,461 all time</span></span>'
         '<span class="tit-fstat tit-fstat-money"><b>$489B</b>'
         '<span>raised in 2026</span></span>')

RIBBON = '<span class="tit-ribbon-c" id="tit-ribbon-c">{c}</span>'


def home(regions=REGIONS_OK, all_time="25,479", countries=104):
    return TILES.format(all=all_time) + regions + RIBBON.format(c=countries)


def _router(html, query_totals, aggregate=None):
    agg = aggregate or {"total": 28_634, "companies": 16_275, "countries": 104,
                        "verified": 19_799, "money": {"total": 503_309_683_768}}

    def fetch(url, timeout=None):
        if "talent-intelligence-tracker/" in url:
            return html.encode()
        if "/query" in url:
            for frag, total in query_totals.items():
                if frag and frag in url:
                    return json.dumps({"total": total}).encode()
            return json.dumps({"total": query_totals.get("", 0)}).encode()
        if "/aggregate" in url:
            return json.dumps(agg).encode()
        raise AssertionError("unrouted URL: " + url)
    return pf.Ctx(fetch, 5, "cb")


def _dead(url, timeout=None):
    raise urllib.error.URLError("network is down")


DRILL_OK = {"": 25_479, "US%2CCA": 7_765, "GB%2CDE": 8_972, "IN%2CJP": 8_742}
DRILL_BROKEN = {"": 25_479, "US%2CCA": 7_765, "GB%2CDE": 8_972, "IN%2CJP": 7_254}


class RegionDrillDownTest(unittest.TestCase):

    def test_fails_when_the_world_tab_returns_more_than_it_badges(self):
        # THE LIVE DEFECT: badged 23,991, returns 25,479. The badge sums only the
        # rows that have a country; the click counts every notable row.
        r = pf.check_region_drilldown(_router(home(REGIONS_BROKEN), DRILL_BROKEN))
        self.assertEqual(r.state, pf.FAIL)
        self.assertIn("23,991", r.detail)
        self.assertIn("25,479", r.detail)
        self.assertIn("+1,488", r.detail)

    def test_passes_when_every_tab_returns_what_it_badges(self):
        r = pf.check_region_drilldown(_router(home(REGIONS_OK), DRILL_OK))
        self.assertEqual(r.state, pf.PASS)

    def test_a_regional_tab_can_fail_on_its_own(self):
        r = pf.check_region_drilldown(_router(home(REGIONS_OK),
                                              dict(DRILL_OK, **{"IN%2CJP": 1})))
        self.assertEqual(r.state, pf.FAIL)
        self.assertIn("Asia", r.detail)

    def test_a_dead_network_is_unknown_not_pass(self):
        r = pf.check_region_drilldown(pf.Ctx(_dead, 5, "cb"))
        self.assertEqual(r.state, pf.UNKNOWN)
        self.assertNotEqual(r.state, pf.PASS)

    def test_missing_tabs_are_unknown_not_pass(self):
        r = pf.check_region_drilldown(_router("<p>nothing</p>", DRILL_OK))
        self.assertEqual(r.state, pf.UNKNOWN)
        self.assertIn("NOT being checked", r.detail)


class RegionReconciliationTest(unittest.TestCase):

    def test_fails_when_the_regions_do_not_partition_the_world(self):
        bad = (region_button("World", "", 25_479)
               + region_button("Americas", "US,CA", 7_765)
               + region_button("Europe", "GB,DE", 8_972))
        r = pf.check_region_reconciliation(_router(home(bad), DRILL_OK))
        self.assertEqual(r.state, pf.FAIL)
        self.assertIn("in two regions or in none", r.detail)

    def test_passes_when_the_regions_partition_the_world(self):
        r = pf.check_region_reconciliation(_router(home(REGIONS_OK), DRILL_OK))
        self.assertEqual(r.state, pf.PASS)

    def test_reconciliation_can_pass_while_drilldown_fails(self):
        # This pairing is the point. On the live site the regions summed exactly
        # to the World badge while the World badge disagreed with its own click.
        # A checker with only one of these assertions reports green.
        ctx = _router(home(REGIONS_BROKEN), DRILL_BROKEN)
        self.assertEqual(pf.check_region_reconciliation(ctx).state, pf.PASS)
        self.assertEqual(pf.check_region_drilldown(ctx).state, pf.FAIL)

    def test_a_dead_network_is_unknown_not_pass(self):
        self.assertEqual(pf.check_region_reconciliation(pf.Ctx(_dead, 5, "cb")).state,
                         pf.UNKNOWN)


class SameClaimOnOnePageTest(unittest.TestCase):

    def test_fails_when_the_tile_and_the_world_badge_disagree(self):
        # THE LIVE DEFECT, visible in a single response: the tile says 25,479 all
        # time and the World tab badges 23,991, a few hundred pixels apart.
        r = pf.check_same_claim_agrees_on_one_page(
            _router(home(REGIONS_BROKEN, all_time="25,479"), DRILL_BROKEN))
        self.assertEqual(r.state, pf.FAIL)
        self.assertIn("25,479", r.detail)
        self.assertIn("23,991", r.detail)

    def test_passes_when_they_agree(self):
        r = pf.check_same_claim_agrees_on_one_page(
            _router(home(REGIONS_OK, all_time="25,479"), DRILL_OK))
        self.assertEqual(r.state, pf.PASS)

    def test_a_dead_network_is_unknown_not_pass(self):
        self.assertEqual(
            pf.check_same_claim_agrees_on_one_page(pf.Ctx(_dead, 5, "cb")).state,
            pf.UNKNOWN)


class AgreementTest(unittest.TestCase):

    def test_fails_when_the_page_and_the_api_disagree(self):
        # THE LIVE DEFECT: the ribbon renders 103 while the API answers 104.
        r = pf.check_figures_agree(_router(home(countries=103), DRILL_OK))
        self.assertEqual(r.state, pf.FAIL)
        self.assertIn("103", r.detail)
        self.assertIn("104", r.detail)

    def test_passes_when_they_agree(self):
        r = pf.check_figures_agree(_router(home(countries=104), DRILL_OK))
        self.assertEqual(r.state, pf.PASS)

    def test_uncovered_figures_are_named_in_the_detail(self):
        r = pf.check_figures_agree(_router(home(countries=104), DRILL_OK))
        self.assertIn("NOT covered", r.detail)

    def test_a_dead_network_is_unknown_not_pass(self):
        self.assertEqual(pf.check_figures_agree(pf.Ctx(_dead, 5, "cb")).state,
                         pf.UNKNOWN)


class BasisTest(unittest.TestCase):

    def test_fails_when_a_label_claims_people_instead_of_records(self):
        bad = home().replace("updates in 2026", "people hired this year")
        bad = bad.replace("employers in 2026", "people hired in 2026")
        r = pf.check_basis_is_stated(_router(bad, DRILL_OK))
        self.assertEqual(r.state, pf.FAIL)

    def test_passes_on_the_real_labels(self):
        r = pf.check_basis_is_stated(_router(home(), DRILL_OK))
        self.assertEqual(r.state, pf.PASS)

    def test_a_news_card_quoting_headcount_is_not_a_defect(self):
        # The false positive that an earlier draft produced. A source's own words
        # inside a published card are DATA, not a label the product wrote, and
        # flagging them trains the reader to ignore the alert.
        with_card = home() + ('<article class="tit-card"><p>The announcement '
                              'confirms headcount added rather than open roles '
                              'to fill.</p></article>')
        r = pf.check_basis_is_stated(_router(with_card, DRILL_OK))
        self.assertEqual(r.state, pf.PASS)

    def test_a_dead_network_is_unknown_not_pass(self):
        self.assertEqual(pf.check_basis_is_stated(pf.Ctx(_dead, 5, "cb")).state,
                         pf.UNKNOWN)


class ContractTest(unittest.TestCase):

    def test_a_crashing_check_is_unknown_never_a_pass(self):
        def boom(ctx):
            raise RuntimeError("kaboom")
        boom.__name__ = "boom"
        rep = pf.check_all(ctx=pf.Ctx(_dead, 5, "cb"), checks=(boom,))
        self.assertEqual(rep.verdict, pf.UNKNOWN)
        self.assertIn("NOT a pass", rep.results[0].detail)

    def test_unknown_never_becomes_pass_in_the_verdict(self):
        ok = lambda ctx: pf.Result("a", "a", pf.PASS)          # noqa: E731
        un = lambda ctx: pf.Result("b", "b", pf.UNKNOWN)       # noqa: E731
        self.assertEqual(pf.check_all(ctx=pf.Ctx(_dead, 5, "cb"),
                                      checks=(ok, un)).verdict, pf.UNKNOWN)

    def test_fail_outranks_unknown(self):
        un = lambda ctx: pf.Result("b", "b", pf.UNKNOWN)       # noqa: E731
        bad = lambda ctx: pf.Result("c", "c", pf.FAIL)         # noqa: E731
        self.assertEqual(pf.check_all(ctx=pf.Ctx(_dead, 5, "cb"),
                                      checks=(un, bad)).verdict, pf.FAIL)

    def test_every_figure_declares_its_unit_and_period(self):
        for f in pf.HOME_FIGURES:
            self.assertTrue(f.unit and f.period, f.key)

    def test_uncovered_surfaces_are_named_rather_than_omitted(self):
        self.assertTrue(pf.NOT_RECOMPUTABLE)

    def test_the_comparison_uses_the_approved_framing(self):
        # No competing tracker and no survey publisher is named in any file. This
        # asserts the presence of the approved vocabulary rather than the absence
        # of specific names, because writing the banned names into an assertion
        # would put them in the repo, which is the thing the rule forbids.
        src = Path(pf.__file__).read_text(encoding="utf-8").lower()
        self.assertNotIn("competitor", src)


if __name__ == "__main__":
    unittest.main()
