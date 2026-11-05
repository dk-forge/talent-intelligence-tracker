"""Newsletter quality checks, from owner feedback 2026-11-04.

Six defects a reader met in a delivered edition, each pinned here:

  1. A WRONG EMPLOYER. "Cedra: 100 roles" credited the roles to Cedra, whose
     facilities were being reopened. The employer is Florian Grupa. The credited
     employer is now checked against the source text before an item goes out,
     and an item is rewritten or held back on a mismatch.
  2. Every employer links to its source or careers page.
  3. Every item shows its announcement date; stale or republished plans are
     filtered out.
  4. Every item has a country and an industry. A missing country is backfilled
     from the article text; an item that cannot be placed says "location not
     stated" and is kept out of the regional ranking.
  5. Counts say listings are not vacancies, and keep reports apart from unique
     employers.
  6. "Applications open", the roles and the location are shown where the
     source states them, with an Ask The Recruiter "tailor your CV" link.

Offline: fixtures only, no database, no network, no model.
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import daily_digest as D  # noqa: E402
from pipeline import digest_quality as Q  # noqa: E402

SINCE = dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc)
UNTIL = dt.datetime(2026, 11, 4, tzinfo=dt.timezone.utc)


def row(**kw):
    base = dict(headline="", summary="", talent_readthrough="", company="",
                headcount=None, headcount_scope=None, signal_direction=None,
                pillar=None, confidence="reported", collector="google_news",
                source_name="Example Wire", source_url="https://example.com/a",
                country=None, city=None, industry=None,
                published_date="2026-11-03", effective_date=None,
                funding_amount=None, funding_amount_usd=None,
                funding_stage=None, deal_type=None, money_basis=None)
    base.update(kw)
    return base


#: THE KNOWN BAD CASE. The delivered edition printed "Cedra: 100 roles".
CEDRA = row(company="Cedra",
            headline="Cedra plant to reopen with 100 jobs",
            summary="Florian Grupa is reopening Cedra facilities in Hungary, "
                    "creating 100 roles.",
            headcount=100, headcount_scope="new_roles",
            signal_direction="hiring", industry="manufacturing")

GOOD = row(company="Elnusa Petrofin",
           headline="Elnusa Petrofin adds fleet and personnel",
           summary="Elnusa Petrofin is adding 500 staff for fuel distribution "
                   "in Jakarta. Applications are now open. Roles include "
                   "drivers, mechanics and dispatchers.",
           headcount=500, headcount_scope="new_roles",
           signal_direction="hiring", country="ID", city="Jakarta",
           industry="energy_utilities")


def edition(rows):
    return D.build_edition(rows, SINCE, UNTIL, UNTIL, 1000)


class EmployerAttribution(unittest.TestCase):

    def test_cedra_is_rewritten_to_the_real_employer(self):
        v = Q.check_attribution(CEDRA)
        self.assertEqual(v.status, Q.REWRITTEN)
        self.assertEqual(v.employer,
                         "Florian Grupa (reopening Cedra facilities)")

    def test_cedra_never_reaches_the_reader_as_the_employer(self):
        text = D.render(edition([CEDRA]))
        self.assertIn("Florian Grupa (reopening Cedra facilities)", text)
        self.assertNotIn("100 roles - Cedra ", text)

    def test_an_employer_the_source_never_names_is_held_back(self):
        bad = row(company="Globex", headline="Initech to hire 200",
                  summary="Initech will hire 200 engineers.", headcount=200,
                  headcount_scope="new_roles", signal_direction="hiring")
        self.assertEqual(Q.check_attribution(bad).status, Q.HELD)
        ed = edition([bad, GOOD])
        self.assertNotIn("Globex", [f.company for f in ed.featured])
        self.assertEqual(1, len(ed.held))
        self.assertIn("Held back", D.render(ed))

    def test_a_correct_employer_passes_unchanged(self):
        v = Q.check_attribution(GOOD)
        self.assertEqual((v.status, v.employer), (Q.OK, "Elnusa Petrofin"))


class EveryEmployerLinks(unittest.TestCase):

    def test_the_source_link_is_printed_with_the_employer(self):
        text = D.render(edition([GOOD]))
        self.assertIn("https://example.com/a", text)

    def test_a_careers_page_is_preferred_when_stored(self):
        r = dict(GOOD, careers_url="https://elnusa.example/careers")
        self.assertIn("https://elnusa.example/careers", D.render(edition([r])))

    def test_an_item_with_no_link_is_held_back(self):
        ed = edition([dict(GOOD, source_url="")])
        self.assertEqual([], ed.featured)
        self.assertEqual(1, len(ed.held))


class AnnouncementDate(unittest.TestCase):

    def test_the_date_is_shown_on_each_item(self):
        self.assertIn("Announced 2026-11-03", D.render(edition([GOOD])))

    def test_a_stale_plan_is_filtered_out(self):
        old = dict(GOOD, published_date="2026-06-01")
        ed = edition([old])
        self.assertEqual([], ed.featured)
        self.assertEqual(1, ed.stale_count)

    def test_a_republished_plan_is_filtered_out(self):
        again = dict(GOOD, summary="Elnusa Petrofin plans, first announced "
                                   "in 2024, to add 500 staff.")
        self.assertTrue(Q.is_republished(again, UNTIL))
        self.assertEqual([], edition([again]).featured)

    def test_a_missing_date_says_so(self):
        text = D.render(edition([dict(GOOD, published_date=None)]))
        self.assertIn("Announcement date not stated", text)


class CountryAndIndustry(unittest.TestCase):

    def test_a_missing_country_is_backfilled_from_the_text(self):
        self.assertEqual("HU", Q.place(CEDRA))

    def test_an_unplaceable_item_is_labelled_and_kept_out_of_the_ranking(self):
        nowhere = dict(GOOD, country=None, city=None,
                       summary="Elnusa Petrofin is adding 500 staff.")
        ed = edition([nowhere, CEDRA])
        text = D.render(ed)
        self.assertIn("location not stated", text)
        ranking = D.regional_ranking(ed)
        self.assertEqual([("Hungary", 1)], ranking)

    def test_every_item_has_an_industry(self):
        r = dict(GOOD, industry=None, summary="A software company adds 500.")
        self.assertEqual("technology", Q.industry(dict(r, company="x")))
        self.assertEqual("industry not stated",
                         Q.industry(dict(GOOD, industry=None,
                                         summary="adds 500", headline="x")))


class ClearCounts(unittest.TestCase):

    def test_listings_are_not_vacancies_and_reports_are_not_employers(self):
        twice = dict(GOOD, source_url="https://example.com/b")
        text = D.render(edition([GOOD, twice, CEDRA]))
        self.assertIn("3 reports from 2 unique employers", text)
        self.assertIn("listings are not vacancies", text.lower())


class ReaderActions(unittest.TestCase):

    def test_applications_roles_and_location_are_shown_when_stated(self):
        text = D.render(edition([GOOD]))
        self.assertIn("Applications open", text)
        self.assertIn("Roles: drivers, mechanics and dispatchers", text)
        self.assertIn("Location: Jakarta, Indonesia", text)
        self.assertIn("https://asktherecruiter.com", text)
        self.assertIn("tailor your cv", text.lower())

    def test_nothing_is_claimed_the_source_does_not_state(self):
        text = D.render(edition([CEDRA]))
        self.assertNotIn("Applications open", text)
        self.assertNotIn("Roles:", text)


if __name__ == "__main__":
    unittest.main()
