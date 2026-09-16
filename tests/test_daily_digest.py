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
                             # Observation, not new roles opened: the board
                             # listed 17 more active postings than the last scan.
                             headline="Braze's job board listed 17 more active "
                                      "postings than our previous scan "
                                      "(job board: 252 to 269)"))
        self.assertEqual(f.meaning.type, cm.OPEN_VACANCIES)
        self.assertEqual(f.provenance(), "First-party employer board")


if __name__ == "__main__":
    unittest.main()


class NoEmDashInARenderedEdition(unittest.TestCase):
    """Zero em-dashes in anything a reader might see is the house rule, and
    this renderer carried four typed ones (a section heading and the three
    row heads) until 2026-09-04. It is offline and wired to no sender, which
    is exactly why nothing caught it: the tracker's own copy scan covers the
    plugin, and a rendered edition is reader-facing whoever forwards it.

    Every branch that typed one is driven here: a current-roles row, a
    projected row, a workforce row (which also prints the heading), and a
    funding/leadership row. Reintroducing any of the four reddens this.
    """

    def test_every_section_renders_without_an_em_dash(self):
        rows = [
            row(),
            row(company="Planner", headcount=300, headcount_scope="new_roles",
                headline="Planner plans to hire 300 by 2028",
                summary="Planner plans to add 300 roles by 2028."),
            row(company="GM", headcount=4600, headcount_scope="affected",
                signal_direction="neutral",
                headline="GM tentative agreement covering 4,600 workers",
                summary="A labour agreement covering 4,600 existing employees."),
            row(company="Fundco", headcount=None, headcount_scope=None,
                signal_direction="neutral", pillar="company_development",
                headline="Fundco raises $40M Series B",
                summary="Fundco raised a $40M Series B.",
                funding_amount="40000000"),
        ]
        out = dd.render(edition(rows))
        self.assertNotIn("\u2014", out)
        # And the sections the rows drive really rendered, so an empty edition
        # cannot pass this by saying nothing.
        self.assertIn("SIGNALS NAMING THE MOST ROLES", out)
        self.assertIn("WORKFORCE EVENTS", out)


class FundingNeverReachesTheRolesSection(unittest.TestCase):
    """A funding round is not a hiring count, and the reader newsletter said
    it was.

    THE LIVE INSTANCE. An edition's roles section listed funding rounds that
    carry no job count at all. `count_meaning` already refuses them a `roles`
    figure and `render` already gives them their own heading, so the CONTENT
    was right in the committed code -- and nothing asserted it, which is how a
    renderer change could put them back without a single test going red. The
    neighbouring workforce and projection cases each have a regression here;
    funding did not.

    Driven from fixtures rather than from the database: the assertion is about
    what a reader is shown, so it must not depend on what happens to be stored.
    """

    def _edition(self):
        return edition([
            row(),                                   # a real 100-role opening
            row(company="Fundco", headcount=None, headcount_scope=None,
                signal_direction="neutral", pillar="company_development",
                headline="Fundco raises $40M Series B",
                summary="Fundco raised a $40M Series B.",
                source_url="https://x/fund",
                funding_amount="40000000"),
        ])

    def test_a_funding_row_is_not_classified_as_naming_roles(self):
        ed = self._edition()
        named = {f.company for f in ed.naming_roles}
        self.assertIn("Acme", named)
        self.assertNotIn("Fundco", named)

    def test_a_funding_row_sits_under_its_own_heading(self):
        ed = self._edition()
        self.assertIn("Fundco", {f.company for f in ed.funding_leadership})

    def test_the_funding_heading_says_it_carries_no_hiring_count(self):
        out = dd.render(self._edition())
        self.assertIn("FUNDING & LEADERSHIP (no hiring count)", out)

    def test_the_funding_amount_never_becomes_a_roles_total(self):
        """40,000,000 dollars must never be read as 40,000,000 roles."""
        ed = self._edition()
        self.assertEqual(ed.current_roles_total, 100)
        out = dd.render(ed)
        self.assertNotIn("40,000,000 roles", out)

    def test_a_funding_only_edition_says_none_rather_than_listing_it(self):
        ed = edition([
            row(company="Fundco", headcount=None, headcount_scope=None,
                signal_direction="neutral", pillar="company_development",
                headline="Fundco raises $40M Series B",
                summary="Fundco raised a $40M Series B.",
                funding_amount="40000000"),
        ])
        out = dd.render(ed)
        roles_block = out.split("SIGNALS NAMING THE MOST ROLES", 1)[1]
        roles_block = roles_block.split("FUNDING", 1)[0]
        self.assertIn("None this window.", roles_block)
        self.assertNotIn("Fundco", roles_block)


class EveryExplanatorySentenceAppearsOnce(unittest.TestCase):
    """One email said the same thing to the reader twice.

    The counting-basis sentence appeared twice in a shipped edition. A reader
    told the basis once is informed; a reader told it twice is being shown a
    renderer bug, and it costs the edition credibility on exactly the sentence
    that is meant to buy it.

    This is a SHAPE guard, not a string check of one sentence: it renders an
    edition that drives every section and asserts that no explanatory line
    repeats. A new duplicated caption reddens this without anybody having to
    think of it in advance.
    """

    def _full_edition(self):
        return edition([
            row(),
            row(company="Planner", headcount=300, headcount_scope="new_roles",
                headline="Planner plans to hire 300 by 2028",
                summary="Planner plans to add 300 roles by 2028.",
                source_url="https://x/plan"),
            row(company="GM", headcount=4600, headcount_scope="affected",
                signal_direction="neutral",
                headline="GM tentative agreement covering 4,600 workers",
                summary="A labour agreement covering 4,600 existing employees.",
                source_url="https://x/gm"),
            row(company="Fundco", headcount=None, headcount_scope=None,
                signal_direction="neutral", pillar="company_development",
                headline="Fundco raises $40M Series B",
                summary="Fundco raised a $40M Series B.",
                source_url="https://x/fund",
                funding_amount="40000000"),
        ], ytd=1000, prev=900)

    @staticmethod
    def _explanatory_lines(text):
        """Lines that explain the edition to the reader, rather than report it.

        A row's own lines (indented, or a bare url) are excluded: two rows may
        legitimately share a lead or a source name. What must not repeat is a
        caption, a heading or a basis sentence.
        """
        out = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or raw.startswith("  "):
                continue
            if line.startswith("http"):
                continue
            out.append(line)
        return out

    def test_no_explanatory_line_is_printed_twice(self):
        lines = self._explanatory_lines(dd.render(self._full_edition()))
        seen, repeated = set(), []
        for line in lines:
            if line in seen:
                repeated.append(line)
            seen.add(line)
        self.assertEqual(repeated, [],
                         f"an edition repeated an explanatory line: {repeated}")

    def test_the_counting_basis_is_stated_exactly_once(self):
        """The specific sentence the live defect duplicated."""
        out = dd.render(self._full_edition())
        self.assertEqual(out.count("current openings only"), 1)
        self.assertEqual(out.count("NOT counted here"), 1)

    def test_the_guard_drives_every_section(self):
        """An edition that renders nothing must not pass this quietly."""
        out = dd.render(self._full_edition())
        for heading in ("SIGNALS NAMING THE MOST ROLES",
                        "PLANNED / PROJECTED HIRING",
                        "WORKFORCE EVENTS",
                        "FUNDING & LEADERSHIP"):
            self.assertIn(heading, out)

    def test_it_catches_a_duplicated_caption(self):
        """Proved by MUTATION: a renderer that says the basis twice is red."""
        out = dd.render(self._full_edition())
        basis = next(l for l in out.splitlines()
                     if "current openings only" in l)
        mutated = out.replace(basis, basis + "\n" + basis, 1)
        lines = self._explanatory_lines(mutated)
        self.assertNotEqual(len(lines), len(set(lines)),
                            "the guard would not notice a duplicated caption")
