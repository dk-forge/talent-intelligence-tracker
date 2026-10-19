"""By location table, Top rounds and shareable URL state (1.109.0).

The aggregation is proven in tests/php/by_location.php against SQLite through
the real /aggregate?include=geo route. This file runs it and pins the parts
that live in markup, script and stylesheet.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
JS = (PLUGIN / "assets" / "dashboard.js").read_text()
CSS = (PLUGIN / "assets" / "dashboard.css").read_text()
SC = (PLUGIN / "includes" / "shortcodes.php").read_text()
BL = (PLUGIN / "includes" / "by_location.php").read_text()

needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")


@needs_php
def test_aggregation_harness_passes():
    p = subprocess.run(["php", str(ROOT / "tests" / "php" / "by_location.php")],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr


def test_money_is_company_raise_by_name():
    # Both the table sum and the top-rounds filter go through tit_money_where().
    assert BL.count("tit_money_where()") >= 2
    assert "NOT IN" not in BL


def test_measures_share_the_boards_predicates():
    assert "tit_signal_defs()" in BL and "tit_region_defs()" in BL
    assert "tit_glance_periods()" in BL


def test_switch_is_hidden_until_script_runs_and_time_is_default():
    assert 'id="tit-geo-bar" hidden' in SC
    assert 'id="tit-top-rounds" hidden' in SC
    assert 'Copy link</button>' in JS and 'class="tit-geo-view"' in JS
    assert "var geoView = 'time'" in JS


def test_url_state_is_read_client_side_with_prefixed_keys():
    for key in ("geo_view", "geo_period", "geo_sort"):
        assert f"q.get('{key}')" in JS and f"q.set('{key}'" in JS
    # The page URL carries it; the API call does not receive geo_view/geo_sort.
    assert "geoQuery()" in JS and "readGeoUrl();" in JS
    assert "navigator.clipboard.writeText(url)" in JS


def test_config_does_not_depend_on_the_inline_object():
    # Autoptimize gotcha #10: the API base comes from TIT, which the dashboard
    # already backs with the root element's data-api attribute.
    assert "fetch(TIT.api + 'aggregate?' + params.toString())" in JS
    assert re.search(r'data-api="<\?php echo esc_attr\(rest_url\(', SC)


def test_row_tap_reuses_the_existing_filter_controls():
    body = JS[JS.index("function applyGeoPlace("):]
    body = body[: body.index("\n  }\n")]
    assert "setRegion(" in body and "ensureOption(inputs.country" in body
    assert "ensureOption(inputs.city" in body and "refresh();" in body


def test_top_25_then_show_all():
    assert "var GEO_TOP = 25;" in JS and "'Show all '" in JS


def test_mobile_scrolls_inside_the_table_not_the_page():
    assert re.search(r"\.tit-geo-scroll \{[^}]*overflow-x:auto", CSS)
    assert re.search(r"\.tit-geo-table th:first-child[^{]*\{[^}]*position:sticky", CSS)
    # Column order puts the two key numbers next to the place.
    cols = re.findall(r"\['(\w+)', '[^']+', '(?:text|count|money)'\]", JS)
    assert cols[:3] == ["label", "rounds", "money"]


def test_new_css_uses_plugin_tokens_only():
    block = CSS[CSS.index("BY LOCATION + TOP ROUNDS"):]
    assert not re.search(r"color:\s*#", block), "hard-coded colour breaks dark mode"
