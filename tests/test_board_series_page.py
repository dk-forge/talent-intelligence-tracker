"""The job-posting volume panel on a company profile.

The panel publishes a NUMBER WE MEASURED rather than a document somebody filed,
which makes it the easiest thing in this product to overclaim with. So these
assertions are about honesty as much as plumbing: the board is linked, the rule
is printed next to the line, "we cannot tell" is a renderable answer, and a
falling board is never dressed up as a layoff.

The suite cannot execute PHP, so the source is read as text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PLUGIN = Path(__file__).parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"
PANEL = (PLUGIN / "includes" / "board_series.php").read_text()
COMPANY = (PLUGIN / "includes" / "company.php").read_text()
BOOTSTRAP = (PLUGIN / "talent-intelligence-tracker.php").read_text()
CSS = (PLUGIN / "assets" / "dashboard.css").read_text()
SEED = PLUGIN / "data" / "board_series.json"


def test_the_include_is_actually_loaded():
    """A file nobody requires is a file that does not exist."""
    assert "tit_require('includes/board_series.php');" in BOOTSTRAP


def test_the_profile_page_calls_it_and_survives_a_half_finished_deploy():
    """FTP lands files one at a time, so company.php can arrive first. A hard
    call would fatal every profile page for the seconds in between."""
    assert "function_exists('tit_board_series_panel')" in COMPANY
    assert "tit_board_series_panel($key)" in COMPANY


def test_the_endpoint_is_keyed_like_every_other_write():
    assert "register_rest_route('talent/v1', '/board-series'" in PANEL
    assert "tit_api_permission" in PANEL
    assert "'__return_false'" in PANEL


def test_the_endpoint_refuses_a_number_with_no_source():
    """The rule the whole tracker runs on: no source URL, no record. A series
    is a claim about an employer and needs the board it was counted from."""
    assert "tit_board_series_unsourced" in PANEL
    assert "empty($entry['source_url'])" in PANEL


def test_the_only_directions_the_endpoint_accepts_are_the_four_the_rule_emits():
    block = re.search(r"\$allowed = array\(([^)]*)\)", PANEL).group(1)
    assert {v.strip().strip("'") for v in block.split(",") if v.strip()} == {
        "rising", "falling", "flat", "unknown"}


def test_we_cannot_tell_is_a_rendered_answer_and_not_a_blank():
    """A board with three readings gets no direction. The panel has to say so
    in words, because an empty space reads as 'nothing is happening'."""
    assert "'unknown' => 'Not enough readings yet'" in PANEL


def test_a_falling_board_is_never_labelled_a_cut():
    labels = re.search(r"function tit_board_direction_label.*?\n}", PANEL, re.S).group(0)
    assert "'falling' => 'Job board shrinking'" in labels
    for forbidden in ("layoff", "Layoff", "cuts", "Cutting", "redundanc"):
        assert forbidden not in labels, forbidden


def test_the_rule_is_printed_where_the_line_is_read():
    """A direction whose reasoning lives in a footnote is a direction being
    asserted. Both the per-board basis and the overall rule render on the page."""
    assert "$trajectory['basis']" in PANEL
    assert "$data['rule']" in PANEL


def test_the_board_is_linked_so_a_reader_can_count_for_themselves():
    assert "esc_url($board['source_url']" in PANEL
    assert 'rel="nofollow noopener"' in PANEL


def test_the_sparkline_needs_no_script_and_cannot_bleed_sideways():
    """Shared hosting, full-page cache, and a phone. Inline SVG, no library —
    the same reason the recall chart is drawn this way."""
    assert "<svg" in PANEL and "polyline" in PANEL
    assert "<script" not in PANEL
    assert "preserveAspectRatio=\"none\"" in PANEL
    assert ".tit-board .tit-spark" in CSS and "max-width:100%" in CSS


def test_a_board_with_one_reading_draws_no_line_at_all():
    """Two dots are a line; one dot is not, and joining nothing to itself would
    be a trend drawn out of a single day."""
    fn = re.search(r"function tit_board_sparkline.*?\n}", PANEL, re.S).group(0)
    assert "if ($count < 2) return '';" in fn


def test_the_shipped_seed_matches_what_the_endpoint_would_accept():
    """The seed is what a fresh install renders, so it has to satisfy the same
    validation the live payload does."""
    data = json.loads(SEED.read_text())
    assert data["as_of"] and data["rule"] and data["boards"]
    for entries in data["boards"].values():
        for entry in entries:
            assert entry["source_url"].startswith("https://")
            assert entry["series"]
            assert entry["trajectory"]["direction"] in {
                "rising", "falling", "flat", "unknown"}


def test_the_seed_carries_the_caveat_a_falling_line_needs():
    data = json.loads(SEED.read_text())
    assert "not evidence of job cuts" in data["rule"]
    assert "we cannot tell" in data["rule"]
