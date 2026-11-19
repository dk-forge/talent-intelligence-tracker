"""todo#1881 item 9: the axe failures measured live on the TIT hub, 2026-11-19."""
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "wordpress-plugin" / "talent-intelligence-tracker"


def test_scrollers_are_keyboard_reachable():
    sc = (PLUGIN / "includes" / "shortcodes.php").read_text()
    mt = (PLUGIN / "includes" / "market_totals.php").read_text()
    assert 'class="tit-table-scroll tit-chart-scroll" tabindex="0" role="region" aria-label=' in sc
    assert 'class="tit-table-scroll tit-market-totals" tabindex="0" role="region" aria-label=' in mt


def test_card_targets_meet_24px():
    css = (PLUGIN / "assets" / "dashboard.css").read_text()
    assert ".tit-wrap .tit-watch-star { min-width:24px; min-height:24px; }" in css
    assert ".tit-wrap .tit-card-employer a { display:inline-block; min-height:24px; }" in css
