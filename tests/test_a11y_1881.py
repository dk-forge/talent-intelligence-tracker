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
    i = css.index(".tit-wrap .tit-watch-star { min-width:24px; min-height:24px; }")
    m = css.rfind("@media (min-width:900px) {", 0, i)
    assert m != -1 and "}" not in css[m + len("@media (min-width:900px) {"):i].replace("*/", "")


def test_phone_star_keeps_44px():
    """Codex P2 on #367: the desktop rule must not shrink the phone target."""
    css = (PLUGIN / "assets" / "dashboard.css").read_text()
    assert ".tit-wrap .tit-watch-star { min-width:44px; min-height:44px; padding:0 8px; }" in css
    # Every unscoped later rule for the star must not set a smaller min size.
    tail = css[css.index(".tit-wrap .tit-watch-star { min-width:44px"):]
    for line in tail.splitlines()[1:]:
        if line.startswith(".tit-wrap .tit-watch-star {") and "min-" in line:
            raise AssertionError("unscoped star size rule after the phone rule: " + line)
    j = css.index(".tit-wrap .tit-card-employer a { display:inline-block; min-height:24px; }")
    assert css.rfind("@media (min-width:900px) {", 0, j) > css.rfind("\n}", 0, j)  # desktop-scoped
