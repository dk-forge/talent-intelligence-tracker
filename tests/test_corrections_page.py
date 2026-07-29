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


def test_every_entry_carries_a_date_a_count_and_the_fields_touched():
    entries = CORRECTIONS[CORRECTIONS.index("function tit_corrections_entries"):]
    entries = entries[:entries.index("\nfunction ")]
    for key in ("'date'", "'title'", "'rows'", "'fields'", "'body'"):
        assert entries.count(key) >= 2, f"{key} missing from an entry"
    # Both first entries are dated the day the fix shipped.
    assert entries.count("2026-07-28") == 2


def test_the_entries_name_what_was_wrong_in_words_not_in_jargon():
    """"data quality issue" would satisfy a schema and tell a reader nothing."""
    assert "states an amount and it states nothing at all about headcount" in FLAT
    assert "within the following two to six quarters" in FLAT
    assert "premium collected from policyholders" in FLAT


def test_the_money_drop_is_explained_rather_than_left_to_be_noticed():
    """Someone who quoted the old figure deserves to know why it moved."""
    assert "$200bn" in FLAT and "$120bn" in FLAT
    assert "the correction working" in FLAT


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
