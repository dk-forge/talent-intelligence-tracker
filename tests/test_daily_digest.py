"""The daily digest, rebuilt honestly.

Regressions for the editorial review's findings: the GM-class workforce event is
not a hiring figure, a projection is labelled projected, the confirmed vs
unconfirmed split renders, a non-English headline gets an English summary with
the original beneath, and the section is renamed off "biggest".

unittest, not pytest (see test_count_meaning).
"""

import unittest
from datetime import datetime, timezone

import daily_digest as dd
from pipeline import count_meaning as cm


def row(**over):
    base = {
        "company": "Acme", "headline": "Acme is hiring 100 people",
        "summary": "Acme is hiring 100 people.", "talent_readthrough": "",
        "headcount": 100, "headcount_scope": "new_roles",
        "signal_direction": "hiring", "pillar": "company_development",
        "confidence": "reported", "collector": "google_news",
        "source_name": "Outlet", "source_url": "https://x/1",
        "funding_amount": None, "country": "US", "published_date": "2026-08-23",
        "effective_date": None,
    }
    base.update(over)
    return base


SINCE = datetime(2026, 8, 23, tzinfo=timezone.utc)
UNTIL = datetime(2026, 8, 24, tzinfo=timezone.utc)
ASOF = datetime(2026, 8, 24, 6, 0, tzinfo=timezone.utc)


def edition(rows, ytd=1000, prev=None):
    return dd.build_edition(rows, SINCE, UNTIL, ASOF, ytd, prev_ytd=prev)


class GmDoesNotAppearAsHiring(unittest.TestCase):
    def test_labor_agreement_not_in_roles_section(self):
        gm = row(company="GM", headcount=4600, headcount_scope="affected",
                 signal_direction="neutral",
                 headline="GM tentative agreement covering 4,600 workers",
                 summary="GM reached a tentative labour agreement covering 4,600 "
                         "existing employees.")
        ed = edition([gm, row()])
        roles_companies = [f.company for f in ed.naming_roles]
        self.assertNotIn("GM", roles_companies)
        self.assertIn("GM", [f.company for f in ed.workforce])
        # And its 4,600 never reaches the roles total.
        self.assertEqual(ed.current_roles_total, 100)

    def test_render_has_no_gm_4600_as_hiring(self):
        gm = row(company="GM", headcount=4600, headcount_scope="affected",
                 signal_direction="neutral",
                 headline="GM tentative agreement covering 4,600 workers",
                 summary="A labour agreement covering 4,600 existing employees.")
        text = dd.render(edition([gm]))
        # It appears only under workforce events, never in the roles ranking.
        self.assertIn("WORKFORCE EVENTS", text)
        roles_block = text.split("PLANNED")[0].split("SIGNALS NAMING THE MOST ROLES")[1]
        self.assertNotIn("4,600 roles", roles_block)


class ProjectionLabelled(unittest.TestCase):
    def test_projection_in_planned_section_not_roles(self):
        proj = row(company="CR", headcount=500, headcount_scope="new_roles",
                   headline="Firm to add 500 jobs in Costa Rica by 2030",
                   summary="Firm plans 500 jobs by 2030.")
        ed = edition([proj])
        self.assertEqual([f.company for f in ed.planned], ["CR"])
        self.assertEqual(ed.naming_roles, [])
        self.assertIn("PLANNED / PROJECTED", dd.render(ed))


class ConfirmedUnconfirmedSplit(unittest.TestCase):
    def test_split_counts_and_renders(self):
        rows = [
            row(confidence="verified", source_url="https://x/a"),
            row(confidence="reported", source_url="https://x/b"),
            row(confidence="reported", source_url="https://x/c"),
        ]
        ed = edition(rows)
        self.assertEqual(ed.confirmed_count, 1)
        self.assertEqual(ed.early_count, 2)
        self.assertIn("1 confirmed via primary source", dd.render(ed))
        self.assertIn("2 early indications", dd.render(ed))


class NonEnglishGetsEnglishSummary(unittest.TestCase):
    def test_spanish_headline_leads_with_summary(self):
        f = dd._featured(row(
            headline="Mercado Libre busca generar más de 14.000 nuevos empleos",
            summary="Mercado Libre plans to create over 14,000 new jobs.",
            headcount=14000, headcount_scope="new_roles"))
        self.assertTrue(f.non_english)
        self.assertEqual(f.lead, "Mercado Libre plans to create over 14,000 new jobs.")
        self.assertEqual(f.original_note,
                         "Mercado Libre busca generar más de 14.000 nuevos empleos")

    def test_non_latin_script_flagged(self):
        f = dd._featured(row(headline="مؤسسة زاكورة: توظيف 541 مربية",
                             summary="A Moroccan foundation is hiring 541 educators."))
        self.assertTrue(f.non_english)
        self.assertEqual(f.lead, "A Moroccan foundation is hiring 541 educators.")

    def test_english_headline_leads_with_headline(self):
        f = dd._featured(row(headline="Acme is hiring 100 people",
                             summary="Acme is hiring 100 people."))
        self.assertFalse(f.non_english)
        self.assertIsNone(f.original_note)


class HeadingRenamed(unittest.TestCase):
    def test_no_biggest_hiring_signals(self):
        text = dd.render(edition([row()]))
        self.assertIn("SIGNALS NAMING THE MOST ROLES", text)
        self.assertNotIn("Biggest hiring", text)
        self.assertNotIn("BIGGEST HIRING", text)


class WindowAndBackfill(unittest.TestCase):
    def test_windows_tile_without_overlap(self):
        from datetime import date
        s1, u1 = dd.default_window(date(2026, 8, 24))
        s2, u2 = dd.default_window(date(2026, 8, 25))
        self.assertEqual(u1, s2)  # half-open: adjacent, no overlap
        self.assertEqual((u1 - s1).days, 1)

    def test_backfill_note_when_ytd_outgrows_edition(self):
        ed = edition([row(), row(source_url="https://x/2")], ytd=1000, prev=900)
        # ytd grew 100, edition added 2 -> backfill.
        self.assertTrue(ed.backfilled)
        self.assertIn("backfill", dd.render(ed))

    def test_no_backfill_note_without_prev(self):
        ed = edition([row()], ytd=1000, prev=None)
        self.assertFalse(ed.backfilled)


class OpenVacanciesProvenance(unittest.TestCase):
    def test_job_board_shows_first_party(self):
        f = dd._featured(row(company="Braze", headcount=17,
                             headcount_scope="new_roles", collector="ats_boards",
                             source_name="Greenhouse job board",
                             headline="Braze opened 17 more roles (job board: 252 to 269)"))
        self.assertEqual(f.meaning.type, cm.OPEN_VACANCIES)
        self.assertEqual(f.provenance(), "First-party employer board")


if __name__ == "__main__":
    unittest.main()
