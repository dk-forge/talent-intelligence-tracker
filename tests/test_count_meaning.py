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

    def test_mine_reopening_headcount_named_only_in_summary(self):
        # Real 2026-09-02 row: the headline names no year and no marker word
        # ("Breaking: Tasmania's Mount Lyell copper mine to reopen in boost
        # for west coast"). The "by 2029" and "plans to" that make this a
        # projection appear only in `summary`/`talent_readthrough`, which a
        # digest that read the headline alone would miss -- and a digest that
        # skipped this module entirely (bypassing it, not misreading it) is
        # exactly how "300 jobs" reached an inbox as a current opening when
        # the mine does not reopen until 2029.
        m = cm.classify(row(
            headcount=300, headcount_scope="new_roles", signal_direction="hiring",
            headline="Breaking: Tasmania's Mount Lyell copper mine to reopen "
                     "in boost for west coast",
            summary="Sibanye-Stillwater plans to reopen the Mt Lyell copper "
                    "mine near Queenstown by 2029, creating 300 jobs.",
            talent_readthrough="Sibanye-Stillwater's reopening of the Mount "
                    "Lyell copper mine near Queenstown, Tasmania creates 300 "
                    "operations and manufacturing jobs onsite when it "
                    "restarts in 2029."))
        self.assertEqual(m.type, cm.PLANNED_JOBS)
        self.assertTrue(m.projected)
        self.assertFalse(m.counts_as_roles)
        self.assertIsNone(m.roles)

    def test_training_cohort_ahead_of_future_plant_launch(self):
        # Real 2026-09-02 row: 100 trainees for a plant that has not opened.
        # The 100 is a training-programme headcount, not "100 jobs" today;
        # the real jobs figure in the source (1,500) appears "upon
        # completion" and is never stored as this row's headcount at all.
        m = cm.classify(row(
            headcount=100, headcount_scope="new_roles", signal_direction="hiring",
            headline="Yicheng Plans China Training for 100 Cameroonians "
                     "Ahead of Pharma Plant Launch",
            summary="Yicheng Pharmaceutical Group is set to train 100 young "
                    "Cameroonians in China for its upcoming pharmaceutical "
                    "and medical-device complex in Meyo, Yaounde IV, aiming "
                    "to create 1,500 direct jobs upon completion."))
        self.assertEqual(m.type, cm.PLANNED_JOBS)
        self.assertTrue(m.projected)
        self.assertFalse(m.counts_as_roles)


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
            # A board-scan delta is an observation, not new roles opened:
            # the headline describes active postings listed, not a hiring act.
            headline="Acme's job board listed 6 more active postings "
                     "than our previous scan (job board: 20 to 26)"))
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
