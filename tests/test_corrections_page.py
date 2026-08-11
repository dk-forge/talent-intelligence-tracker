"""The corrections log.

A page that discloses errors is only worth having if it is reachable, framed,
and specific. All three are asserted here, because all three are the kind of
thing that quietly rots: a route that stops being linked, a framing paragraph
someone trims, an entry that says "data was corrected" and nothing else.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN = Path(__file__).parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"
CORRECTIONS = (PLUGIN / "includes" / "corrections.php").read_text()
SOURCES = (PLUGIN / "includes" / "sources.php").read_text()
SHORTCODES = (PLUGIN / "includes" / "shortcodes.php").read_text()
BOOTSTRAP = (PLUGIN / "talent-intelligence-tracker.php").read_text()
PAGE = (PLUGIN / "includes" / "page.php").read_text()
CSS = (PLUGIN / "assets" / "dashboard.css").read_text()

URL = "/talent-intelligence-tracker/corrections/"


def test_the_page_is_routed_the_same_way_the_sources_page_is():
    for fragment in ("add_rewrite_rule", "query_vars", "template_redirect",
                     "pre_get_document_title"):
        assert fragment in CORRECTIONS, fragment
    assert "talent-intelligence-tracker/corrections" in CORRECTIONS


def test_the_include_is_actually_loaded():
    """A file nobody requires is a file that does not exist."""
    assert "tit_require('includes/corrections.php');" in BOOTSTRAP


def test_a_new_route_needs_a_version_bump_to_flush_rewrites():
    """Rewrite rules live in the database and an FTP deploy runs no activation
    hook; tit_company_maybe_flush() is gated on TIT_VERSION. Shipping the page
    without a bump gives a 404 that looks like a broken deploy."""
    version = re.search(r"define\('TIT_VERSION', '([^']+)'\)", BOOTSTRAP).group(1)
    assert version != "1.34.1", "bump TIT_VERSION or the corrections route 404s"
    assert f"Version: {version}" in BOOTSTRAP, "header and constant disagree"


def test_it_is_linked_from_the_footer_and_from_the_sources_page():
    """Unlinked, it is not a disclosure, it is a page that happens to exist."""
    assert URL in SHORTCODES, "not linked from the dashboard footer"
    assert URL in SOURCES, "not linked from the sources page"


def test_the_table_of_contents_plugin_is_kept_off_it():
    """Easy Table of Contents injects itself into anything with headings, and
    this page is all headings."""
    assert "tit_corrections" in PAGE


# The prose here wraps, and a test that breaks on rewrapping teaches you to
# stop editing the copy. Match on normalised whitespace, never on the wrap.
FLAT = " ".join(CORRECTIONS.split())
FLAT_SOURCES = " ".join(SOURCES.split())


def test_the_page_explains_why_it_exists_before_listing_failures():
    """A list of corrections with no framing reads as a list of failures. The
    framing is the point of the page, so it is asserted, not assumed."""
    assert "more trustworthy than one that hides them" in FLAT
    assert "nothing is ever silently deleted" in FLAT


ENTRIES = CORRECTIONS[CORRECTIONS.index("function tit_corrections_entries"):]
ENTRIES = ENTRIES[:ENTRIES.index("\nfunction tit_corrections_outstanding")]
# Comments carry the future past-tense wording on purpose ("// TENSE: ..."), and
# a reader never sees them. The tense rule is about published prose only.
FLAT_ENTRIES = " ".join(
    re.sub(r"//[^\n]*", "", ENTRIES).split())
ANYTHING_SCHEDULED = "'status' => 'scheduled'" in ENTRIES


def _blocks():
    """The entries one at a time, so a tense rule can be applied to the entry it
    is about.

    Both tense tests used to read the whole list as one string, which was
    harmless only while every entry shared a status. The first time a PENDING
    entry joined two applied ones, "The badge is now" — a true, past-tense
    sentence in an entry that ran on 29 July — failed the pending-entry test and
    would have been rewritten into something false to make the suite green. The
    property is per entry and always was.
    """
    parts = re.split(r"(?=\n\s*array\(\s*\n\s*'date')", ENTRIES)
    return [p for p in parts if "'status'" in p]


def _flat(block):
    return " ".join(re.sub(r"//[^\n]*", "", block).split())


SCHEDULED = [b for b in _blocks() if "'status' => 'scheduled'" in b]
APPLIED = [b for b in _blocks() if "'status' => 'applied'" in b]


def test_every_entry_is_either_scheduled_or_applied():
    """A third value would silently fall out of both tense tests, which is the
    only way this page can disagree with the data without anything failing."""
    assert len(SCHEDULED) + len(APPLIED) == len(_blocks()) == ENTRIES.count("'status'")


def test_every_entry_carries_a_date_a_count_and_the_fields_touched():
    for key in ("'date'", "'title'", "'rows'", "'fields'", "'body'", "'status'"):
        assert ENTRIES.count(key) >= 2, f"{key} missing from an entry"
    # Both entries are dated the day the defect was found.
    assert ENTRIES.count("2026-07-28") == 2


def test_an_unapplied_correction_is_never_written_in_the_past_tense():
    """The one failure this page cannot have. It shipped for ~40 minutes saying
    "The badge is now Headcount not stated" while the correction had not run —
    exactly the plausible-but-false claim the tracker exists not to make. A
    defect is disclosed before it is fixed. It is not backdated.

    Scoped to the entry that is pending. An applied entry beside it is entitled
    to the past tense, and reading them as one string made the true sentence in
    the applied one fail for the pending one's rule."""
    for block in SCHEDULED:
        flat = _flat(block)
        for claim in ("The badge is now", "have been withdrawn", "records have been",
                      "fell from roughly", "the new one does not",
                      "were withdrawn on", "was corrected on"):
            assert claim not in flat, f"past tense on an unapplied correction: {claim!r}"


def test_an_applied_correction_does_not_still_say_it_is_pending():
    """The inverse of the tense bug and exactly as false: for several hours the
    page told readers our figures were inflated after they had been fixed. Both
    directions are the same failure, the page disagreeing with the data.

    Asserted against the ENTRY PROSE, not the template: the template keeps its
    pending wording for the next defect, gated on status.

    Scoped per entry for the same reason its opposite is: a genuinely pending
    entry saying it is pending must not make an applied one exempt."""
    for block in APPLIED:
        flat = _flat(block)
        for stale in ("scheduled for withdrawal", "scheduled to be corrected",
                      "are scheduled to be", "currently overstated",
                      "will show afterwards", "Until that runs", "Until this runs"):
            assert stale not in flat, f"pending language on an applied correction: {stale!r}"


def test_the_pending_notice_is_gated_and_not_merely_deleted():
    """The banner must disappear because nothing is outstanding, not because
    someone removed it — otherwise the next defect ships with no warning."""
    assert "if ($pending)" in CORRECTIONS
    assert "$pending = tit_corrections_outstanding($entries);" in CORRECTIONS


def test_the_pending_machinery_survives_for_the_next_defect():
    """Deleting it rather than gating it would mean the next defect is
    published only once fixed, which is the posture this page rejects."""
    for fragment in ("not yet applied", "Some of these are not fixed yet",
                     "tit_corrections_outstanding", "Projected effect, not yet applied"):
        assert fragment in CORRECTIONS, fragment


def test_a_projection_that_missed_is_kept_beside_the_result():
    """We published $114.1bn and it landed at $124.0bn. Both stay on the page:
    a corrections log that quietly revises its own numbers is doing the thing
    it exists to prevent."""
    assert "'measured'" in CORRECTIONS
    assert "We projected" in CORRECTIONS
    assert "What we projected, and what actually happened" in CORRECTIONS
    for figure in ("4,024", "3,026", "3,064", "$199.7bn", "$114.1bn", "$124.0bn"):
        assert figure in CORRECTIONS, figure


def test_the_gap_between_projection_and_result_is_explained():
    """"About $10bn higher" with no cause reads as the correction failing. It
    did not: the money arrived from a new collector after the projection."""
    assert "$9.25bn" in FLAT and "$0.9bn" in FLAT
    assert "national-press collector" in FLAT
    assert "not the correction falling short" in FLAT


def test_the_page_states_what_it_cannot_promise():
    """The honest ceiling: a correction reaches the records the pipeline holds.
    A reader is told what a survivor would look like rather than being given a
    guarantee we cannot make."""
    assert "cannot promise" in FLAT
    assert "would like to be told" in FLAT


def test_measured_figures_are_dated_because_they_keep_moving():
    """A new collector is adding records daily. An undated total on a
    corrections page becomes wrong on its own."""
    assert "Measured on the live tracker" in CORRECTIONS
    assert "snapshot" in FLAT


def test_flipping_an_entry_to_applied_is_a_small_edit():
    """The badge, the notice, the extra stat and the table heading all derive
    from one field, so landing the run is a status change plus prose rather
    than a template rewrite."""
    assert "$e['status']" in CORRECTIONS
    assert "'scheduled'" in CORRECTIONS and "'applied'" in CORRECTIONS
    # While anything is pending, the sentences that must change are marked
    # where they sit, so the flip does not become a hunt through prose.
    if ANYTHING_SCHEDULED:
        assert CORRECTIONS.count("// TENSE:") >= 2
    # And the contract itself stays documented either way.
    assert "WHEN A CORRECTION RUNS" in CORRECTIONS
    assert "never overwritten" in CORRECTIONS


def test_the_synthetic_gic_finding_is_visible():
    """It shows the correction was reviewed rather than assumed complete, which
    is the part of this that is worth a reader's trust."""
    assert "BOLI/COLI" in FLAT and "$12.4bn" in FLAT
    assert "reading the money list after the fix instead of trusting it" in FLAT


def test_the_entries_name_what_was_wrong_in_words_not_in_jargon():
    """"data quality issue" would satisfy a schema and tell a reader nothing."""
    assert "states an amount and it states nothing at all about headcount" in FLAT
    assert "within the following two to six quarters" in FLAT
    assert "premium collected from policyholders" in FLAT


def test_the_money_movement_is_quantified_rather_than_left_to_be_noticed():
    """Someone quoting the headline number deserves to be told, in figures,
    either how wrong it currently is or how far it just moved."""
    if ANYTHING_SCHEDULED:
        assert "overstated by roughly" in FLAT_ENTRIES
    else:
        assert "$199.7bn" in FLAT_ENTRIES and "$124.0bn" in FLAT_ENTRIES


def test_the_real_estate_collateral_is_disclosed_in_both_places():
    """Accepted as the better of two errors, but never silently."""
    for flat, where in ((FLAT, "corrections log"), (FLAT_SOURCES, "sources page")):
        assert "single-asset vehicles" in flat, where
        assert "genuine real-estate employers" in flat, where
        assert "no field that separates the two" in flat, where


def test_the_badge_name_matches_what_the_page_actually_renders():
    """The entry quotes the new badge. If the label is ever renamed, the
    correction becomes a description of something a reader cannot find."""
    label = re.search(r"'neutral'\s*=>\s*'([^']+)'", SHORTCODES).group(1)
    assert f'"{label}"' in CORRECTIONS


def test_the_projection_table_is_not_wired_to_the_card_layout():
    """Below 860px `.tit-table` turns rows into cards and visually hides thead.
    These cells are numbers whose entire meaning is which column they sit in,
    so on a phone that would render as two unlabelled figures side by side."""
    assert 'class="tit-projection"' in CORRECTIONS
    assert 'class="tit-table tit-projection"' not in CORRECTIONS
    assert ".tit-projection th, .tit-projection td" in CSS, "no standalone cell styling"


def test_the_page_is_styled_and_constrained_like_the_others():
    assert ".tit-corrections" in CSS and ".tit-correction " in CSS
    layout = CSS[CSS.index(".tit-wrap.tit-sources"):]
    assert ".tit-wrap.tit-corrections" in layout[:400], "no max-width, so it runs edge to edge"


def test_output_is_escaped():
    """Hand-written constants today, but the loop should not be the reason a
    future entry with an apostrophe or a link breaks the page."""
    body = CORRECTIONS[CORRECTIONS.index("function tit_corrections_render"):]
    assert "echo $e[" not in body
    assert "esc_html" in body and "esc_url" in body
