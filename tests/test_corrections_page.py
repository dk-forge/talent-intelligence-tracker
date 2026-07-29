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


def test_every_entry_carries_a_date_a_count_and_the_fields_touched():
    for key in ("'date'", "'title'", "'rows'", "'fields'", "'body'", "'status'"):
        assert ENTRIES.count(key) >= 2, f"{key} missing from an entry"
    # Both entries are dated the day the defect was found.
    assert ENTRIES.count("2026-07-28") == 2


def test_an_unapplied_correction_is_never_written_in_the_past_tense():
    """The one failure this page cannot have. It shipped for ~40 minutes saying
    "The badge is now Headcount not stated" while the correction had not run —
    exactly the plausible-but-false claim the tracker exists not to make. A
    defect is disclosed before it is fixed. It is not backdated."""
    if "'status' => 'scheduled'" not in ENTRIES:
        return  # all applied, and then the past tense is the honest tense
    for claim in ("The badge is now", "have been withdrawn", "records have been",
                  "fell from roughly", "the new one does not"):
        assert claim not in FLAT_ENTRIES, f"past tense on an unapplied correction: {claim!r}"


def test_a_scheduled_entry_says_the_live_figures_still_include_it():
    """Without this a reader takes the disclosure as already reflected in the
    numbers, which is worse than not disclosing at all."""
    assert "scheduled to be corrected" in FLAT
    assert "scheduled for withdrawal" in FLAT
    assert "still includes them" in FLAT
    assert "currently overstated" in FLAT


def test_the_projection_is_labelled_as_a_projection():
    """A projection rendered as a measurement is a fabricated figure."""
    assert "'projection'" in CORRECTIONS
    assert "Projected effect, not yet applied" in CORRECTIONS
    for figure in ("4,024", "3,026", "$199.7bn", "$114.1bn",
                   "$59.04bn", "$8.44bn", "$13.16bn", "$1.00bn"):
        assert figure in CORRECTIONS, figure


def test_outstanding_work_is_flagged_at_the_top_of_the_page():
    """Buried in entry two, a reader checking a headline number misses it."""
    assert "tit_corrections_outstanding" in CORRECTIONS
    assert "Some of these are not fixed yet" in FLAT
    assert "still to be applied" in CORRECTIONS


def test_flipping_an_entry_to_applied_is_a_small_edit():
    """The badge, the notice, the extra stat and the table heading all derive
    from one field, so landing the run is a status change plus prose rather
    than a template rewrite."""
    assert "$e['status']" in CORRECTIONS
    assert "'scheduled'" in CORRECTIONS and "'applied'" in CORRECTIONS
    # And the sentences that must change are marked where they sit.
    assert CORRECTIONS.count("// TENSE:") >= 3


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


def test_the_money_distortion_is_quantified_rather_than_left_to_be_noticed():
    """Someone quoting the headline number deserves to know it is wrong, and
    by how much, without having to work it out from a row count."""
    assert "overstated by roughly $86bn" in FLAT


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
