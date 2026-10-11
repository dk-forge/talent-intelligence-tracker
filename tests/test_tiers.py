"""Public trust tiers (pipeline/tiers.py): table-driven, offline."""

import pytest

from pipeline import count_meaning as cm
from pipeline.tiers import TIERS, Tier, tier_for, tier_of_row

SEC = "https://www.sec.gov/Archives/edgar/data/1/0001.htm"
NEWS = "https://www.example-news.com/story"
BOARD = "https://boards.greenhouse.io/acme"

CASES = [
    # (confidence, url, collector, source_name) -> (tier, rumored)
    (("verified", SEC, "sec_edgar", "SEC EDGAR"), ("verified", False)),
    (("reported", SEC, "sec_edgar", "SEC EDGAR"), ("reported", False)),
    (("reported", NEWS, "national_press", "Example News"), ("reported", False)),
    (("rumored", NEWS, "national_press", "Example News"), ("reported", True)),
    # News-only can never be verified, whatever the stored label says.
    (("verified", NEWS, "national_press", "Example News"), ("reported", False)),
    (("VERIFIED", "not a url", None, None), ("reported", False)),
    # Job-board readings are measured, whatever the confidence.
    (("reported", BOARD, "ats_boards", "Greenhouse job board"), ("measured", False)),
    (("verified", SEC, "ats_boards", "Lever job board"), ("measured", False)),
    (("rumored", BOARD, "other", "Ashby job board"), ("measured", False)),
    ((None, None, None, None), ("reported", False)),
]


@pytest.mark.parametrize("args,expected", CASES)
def test_tier_table(args, expected):
    assert tier_for(*args) == Tier(*expected)


def test_every_result_is_a_public_tier():
    assert {tier_for(*a).tier for a, _ in CASES} == set(TIERS)


def test_tier_of_row_tolerates_missing_keys():
    assert tier_of_row({"confidence": "rumored"}) == Tier("reported", True)
    assert tier_of_row({}) == Tier("reported", False)


def test_measured_item_is_never_an_announced_hiring_event():
    row = {"headcount": 6, "confidence": "reported", "collector": "ats_boards",
           "source_name": "Greenhouse job board", "signal_direction": "hiring",
           "pillar": "hiring", "source_url": BOARD, "headline": "Acme board grew"}
    assert tier_of_row(row).tier == "measured"
    assert cm.classify(row).type != cm.CONFIRMED_HIRES


def test_php_twin_names_the_same_measured_rule_and_is_emitted():
    from pathlib import Path
    from pipeline.tiers import MEASURED_COLLECTORS
    plugin = Path(__file__).resolve().parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"
    php = (plugin / "includes" / "tiers.php").read_text()
    for c in MEASURED_COLLECTORS:
        assert f"'{c}'" in php
    assert r"\bjob board\b" in php
    assert "'tier'" in (plugin / "includes" / "export.php").read_text()
    assert "['tier']" in (plugin / "includes" / "api.php").read_text()
    assert "includes/tiers.php" in (plugin / "talent-intelligence-tracker.php").read_text()
