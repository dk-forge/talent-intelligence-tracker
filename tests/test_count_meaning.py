"""The count-meaning classifier: what a headcount MEANS and whether it is hiring.

The editorial review of 2026-08-24 found workforce events, projections and
funding/leadership rows counted as hiring jobs. Each case below is one of those,
plus the present-tense openings that must STILL count.

unittest, not pytest: the machine that runs these has no pytest, so a
pytest-only file never runs (mirrors tests/test_scope_and_materiality.py).
"""

import unittest

from pipeline import count_meaning as cm


def row(**over):
    base = {
        "headcount": None, "headcount_scope": None, "signal_direction": "hiring",
        "pillar": "company_development", "confidence": "reported",
        "collector": "google_news", "source_name": "Some Outlet",
        "funding_amount": None, "headline": "", "summary": "",
        "talent_readthrough": "", "effective_date": None,
    }
    base.update(over)
    return base


class WorkforceEventsDoNotCountAsHiring(unittest.TestCase):
    def test_gm_labor_agreement_over_existing_employees(self):
        # A tentative labour agreement covering 4,600 EXISTING employees.
        m = cm.classify(row(
            headcount=4600, headcount_scope="affected", signal_direction="neutral",
            headline="GM reaches tentative agreement covering 4,600 workers"))
        self.assertEqual(m.type, cm.WORKFORCE_EVENT)
        self.assertFalse(m.counts_as_roles)
        self.assertIsNone(m.roles)
        self.assertFalse(m.hiring_intent)

    def test_once_total_workforce_is_not_openings(self):
        # "80.000 profesionales" -- people already on staff, stored hiring.
        m = cm.classify(row(
            headcount=80000, headcount_scope="total_workforce",
            signal_direction="hiring",
            headline="La ONCE bate records con 80.000 profesionales"))
        self.assertEqual(m.type, cm.WORKFORCE_EVENT)
        self.assertFalse(m.counts_as_roles)
        self.assertIsNone(m.roles)

    def test_scope_beats_direction(self):
        # total_workforce wins even when the model said direction=hiring.
        m = cm.classify(row(headcount=12000, headcount_scope="total_workforce",
                            signal_direction="hiring"))
        self.assertEqual(m.type, cm.WORKFORCE_EVENT)

    def test_non_hiring_direction_with_a_number(self):
        m = cm.classify(row(headcount=300, headcount_scope="new_roles",
                            signal_direction="displacement"))
        self.assertEqual(m.type, cm.WORKFORCE_EVENT)
        self.assertFalse(m.counts_as_roles)


class ProjectionsAreLabelledNotCounted(unittest.TestCase):
    def test_costa_rica_500_by_2030(self):
        m = cm.classify(row(
            headcount=500, headcount_scope="new_roles",
            headline="Company to add 500 jobs in Costa Rica by 2030"))
        self.assertEqual(m.type, cm.PLANNED_JOBS)
        self.assertTrue(m.projected)
        self.assertFalse(m.counts_as_roles)
        self.assertIsNone(m.roles)
        self.assertTrue(m.hiring_intent)  # it IS a hiring intention, just future

    def test_plans_to_hire(self):
        m = cm.classify(row(headcount=50000, headcount_scope="new_roles",
                            headline="Deloitte to hire 50,000 employees in India"))
        self.assertEqual(m.type, cm.PLANNED_JOBS)

    def test_future_effective_date_marks_projection(self):
        m = cm.classify(row(headcount=200, headcount_scope="new_roles",
                            headline="Firm opens hub", effective_date="2027-01-01"))
        self.assertTrue(m.projected)


class PresentOpeningsStillCount(unittest.TestCase):
    def test_confirmed_present_tense_hiring(self):
        m = cm.classify(row(
            headcount=7500, headcount_scope="new_roles",
            headline="Poste Italiane is hiring 7,500 positions"))
        self.assertEqual(m.type, cm.CONFIRMED_HIRES)
        self.assertTrue(m.counts_as_roles)
        self.assertEqual(m.roles, 7500)

    def test_job_board_delta_is_open_vacancies_first_party(self):
        m = cm.classify(row(
            headcount=6, headcount_scope="new_roles", collector="ats_boards",
            source_name="Greenhouse job board",
            headline="Acme opened 6 more roles (job board: 20 to 26)"))
        self.assertEqual(m.type, cm.OPEN_VACANCIES)
        self.assertTrue(m.counts_as_roles)
        self.assertEqual(m.roles, 6)
        self.assertTrue(m.first_party)


class FundingAndLeadershipHaveNoHiringCount(unittest.TestCase):
    def test_funding_round(self):
        m = cm.classify(row(headcount=None, funding_amount="$30B",
                            headline="Anthropic raises $30B"))
        self.assertEqual(m.type, cm.FUNDING_OR_LEADERSHIP)
        self.assertFalse(m.counts_as_roles)
        self.assertIsNone(m.roles)

    def test_leadership_move(self):
        m = cm.classify(row(headcount=None, pillar="leadership_change",
                            headline="Acme CEO steps down"))
        self.assertEqual(m.type, cm.FUNDING_OR_LEADERSHIP)
        self.assertFalse(m.counts_as_roles)


class ProvenanceFlags(unittest.TestCase):
    def test_verified_is_primary(self):
        m = cm.classify(row(headcount=100, headcount_scope="new_roles",
                            confidence="verified", headline="Files: 100 hires"))
        self.assertTrue(m.primary)
        self.assertTrue(m.first_party)

    def test_reported_news_is_not_primary_not_first_party(self):
        m = cm.classify(row(headcount=100, headcount_scope="new_roles",
                            confidence="reported", headline="Outlet: 100 hires"))
        self.assertFalse(m.primary)
        self.assertFalse(m.first_party)


if __name__ == "__main__":
    unittest.main()
