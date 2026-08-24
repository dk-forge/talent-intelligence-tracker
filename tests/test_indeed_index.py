"""The Indeed Hiring Lab macro backdrop: collector logic and the display panel.

This panel shows SOMEBODY ELSE'S number, licensed CC BY 4.0, next to the
tracker's own signals. The whole risk is that a reader mistakes it for one of
our counts, or that we quietly modify it and forget to say so. So these
assertions are about honesty as much as plumbing: the value is Indeed's and only
the comparisons are ours, the section says it is external and not counted in our
figures, the source is linked with its licence, and a fetch that fails or a
schema that changed raises rather than publishing a stale or empty panel.

Two halves: the Python build_indeed_index.py logic runs against tiny inline CSV
fixtures; the PHP panel is read as text (the suite cannot execute PHP).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import build_indeed_index as bii

PLUGIN = Path(__file__).parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"
PANEL = (PLUGIN / "includes" / "indeed_index.php").read_text()
BOOTSTRAP = (PLUGIN / "talent-intelligence-tracker.php").read_text()
SHORTCODES = (PLUGIN / "includes" / "shortcodes.php").read_text()
CSS = (PLUGIN / "assets" / "dashboard.css").read_text()
SEED = PLUGIN / "data" / "indeed_index.json"


# A minimal national CSV: two variables, two countries, a short SA series so the
# month-ago pick and the baseline delta are both exercised.
NATIONAL_CSV = (
    "date,jobcountry,indeed_job_postings_index_SA,indeed_job_postings_index_NSA,variable\n"
    "2020-02-01,US,100,100,total postings\n"
    "2026-06-14,US,100.50,104.0,total postings\n"
    "2026-07-14,US,101.45,105.0,total postings\n"
    "2026-08-14,US,101.79,105.86,total postings\n"
    "2026-08-14,CA,88.0,90.0,total postings\n"        # other country, ignored
    "2026-08-14,US,95.0,99.0,new postings\n"          # other variable, ignored
)

AI_CSV = (
    "date,jobcountry,AI_share_postings\n"
    "2026-07-01,US,5.95\n"
    "2026-07-31,US,6.28\n"
    "2026-07-31,GB,9.40\n"                            # other country, ignored
)


# --- Collector logic -------------------------------------------------------

def test_the_national_block_reads_the_seasonally_adjusted_us_total():
    block = bii.parse_national(NATIONAL_CSV)
    assert block["as_of"] == "2026-08-14"
    assert block["index"] == 101.79
    assert block["seasonally_adjusted"] is True


def test_the_value_is_indeeds_and_the_comparisons_are_ours():
    """vs_baseline and the month-ago delta are the only computed numbers, and
    they are arithmetic on the published series, not a re-indexing."""
    block = bii.parse_national(NATIONAL_CSV)
    assert block["vs_baseline"] == pytest.approx(1.79)          # 101.79 - 100
    assert block["month_ago"]["date"] == "2026-07-14"
    assert block["month_ago"]["delta"] == pytest.approx(0.34)   # 101.79 - 101.45


def test_it_ignores_other_countries_and_the_new_postings_variable():
    block = bii.parse_national(NATIONAL_CSV)
    for date, value in block["series"]:
        # Every retained point is a US 'total postings' SA value; the CA row
        # (88.0) and the 'new postings' row (95.0) must never appear.
        assert value not in (88.0, 95.0)


def test_the_ai_block_reads_the_us_share_and_its_own_date():
    block = bii.parse_ai(AI_CSV)
    assert block["as_of"] == "2026-07-31"
    assert block["share_pct"] == 6.28
    assert block["month_ago"]["delta"] == pytest.approx(0.33)   # 6.28 - 5.95


def test_the_payload_states_that_values_are_as_published():
    payload = bii.build(NATIONAL_CSV, AI_CSV)
    assert payload["values_as_published"] is True
    assert payload["license"] == "CC BY 4.0"
    assert payload["source"] == "Indeed Hiring Lab"
    # The headline as_of is the index's date, not the older AI date.
    assert payload["as_of"] == "2026-08-14"
    assert "computed by us" in payload["rule"]
    assert "never added into the tracker" in payload["rule"]


def test_a_changed_schema_raises_rather_than_publishing_a_wrong_number():
    """The one thing worse than no backdrop is a confidently wrong one."""
    with pytest.raises(bii.SchemaError):
        bii.parse_national("date,jobcountry,something_else,variable\n"
                           "2026-08-14,US,101.8,total postings\n")
    with pytest.raises(bii.SchemaError):
        bii.parse_ai("date,jobcountry,not_the_share\n2026-07-31,US,6.28\n")


def test_no_us_rows_is_a_failure_not_an_empty_panel():
    with pytest.raises(bii.SchemaError):
        bii.parse_national(
            "date,jobcountry,indeed_job_postings_index_SA,variable\n"
            "2026-08-14,CA,88.0,total postings\n")


# --- The shipped seed ------------------------------------------------------

def test_the_shipped_seed_is_well_formed_and_grounded():
    """The seed is what a fresh install renders. It must satisfy the same shape
    the endpoint enforces, and its comparisons must be internally consistent
    with the published value they sit beside."""
    data = json.loads(SEED.read_text())
    assert data["as_of"] and data["rule"] and data["values_as_published"] is True
    assert data["license"] == "CC BY 4.0"

    nat = data["national"]
    assert nat["source_url"].startswith("https://")
    assert isinstance(nat["index"], (int, float))
    # The computed comparison is exactly index - 100, to the stored precision.
    assert nat["vs_baseline"] == pytest.approx(round(nat["index"] - 100.0, 2))
    assert nat["series"] and nat["series"][0][0] <= nat["series"][-1][0]
    if "month_ago" in nat:
        assert nat["month_ago"]["delta"] == pytest.approx(
            round(nat["index"] - nat["month_ago"]["index"], 2))

    ai = data["ai"]
    assert ai["source_url"].startswith("https://")
    assert isinstance(ai["share_pct"], (int, float))


# --- The PHP panel ---------------------------------------------------------

def test_the_include_is_loaded_and_the_dashboard_renders_it_guarded():
    assert "tit_require('includes/indeed_index.php');" in BOOTSTRAP
    # function_exists-guarded so a mid-upload FTP race renders nothing, not a fatal.
    assert "function_exists('tit_indeed_index_panel')" in SHORTCODES
    assert "echo tit_indeed_index_panel();" in SHORTCODES


def test_the_endpoint_is_keyed_like_every_other_write():
    assert "register_rest_route('talent/v1', '/indeed-index'" in PANEL
    assert "tit_api_permission" in PANEL
    assert "'__return_false'" in PANEL


def test_the_endpoint_refuses_a_number_with_no_source_or_date():
    assert "tit_indeed_unsourced" in PANEL
    assert "empty($national['source_url'])" in PANEL
    assert "empty($national['as_of'])" in PANEL


def test_the_panel_says_it_is_external_and_not_one_of_our_counts():
    """The load-bearing honesty: a reader must never read this as our figure."""
    assert "not the tracker's own records" in PANEL
    assert "not counted in" in PANEL


def test_the_panel_credits_the_source_and_states_the_licence():
    assert "Indeed Hiring Lab" in PANEL
    assert "CC" in PANEL and "BY" in PANEL and "4.0" in PANEL
    assert "esc_url($n_src" in PANEL
    assert 'rel="nofollow noopener"' in PANEL


def test_the_panel_shows_each_series_real_as_of_date():
    """Staleness has to be visible: the index and the AI share carry their own
    dates, because the AI series lags the index by weeks."""
    assert "index as of" in PANEL and "AI share as of" in PANEL


def test_the_sparkline_needs_no_script_and_cannot_bleed_sideways():
    assert "<svg" in PANEL and "polyline" in PANEL
    assert "<script" not in PANEL
    assert 'preserveAspectRatio="none"' in PANEL
    assert ".tit-macro" in CSS


def test_a_series_with_one_reading_draws_no_line():
    fn = re.search(r"function tit_indeed_sparkline.*?\n}", PANEL, re.S).group(0)
    assert "if ($count < 2) return '';" in fn


def test_the_panel_renders_nothing_when_we_hold_no_backdrop():
    """An empty shell would imply a market with no postings."""
    fn = re.search(r"function tit_indeed_index_panel.*?ob_start", PANEL, re.S).group(0)
    assert "return '';" in fn
