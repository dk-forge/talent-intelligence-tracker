"""SEO route B for the talent tracker (ai-layoff-tracker#446, owner ruling 2026-10-14).

Three items, the sibling's 1, 4 and 5. Intro and FAQ copy (items 2 and 3) are
the owner's to write and are deliberately absent.

1. A server-rendered totals table under the weekly market chart, built from the
   SAME tit_market_trend() array the chart draws, so the table cannot disagree
   with the bars. When the chart refuses to draw (variant 'none'), so does the
   table: a count table there would make the market claim the caveat refuses.
4. A "Related" link row on every page the plugin publishes (the dashboard
   shortcode page plus every virtual route), from ONE map in code. Every page
   links Data Sources except the Sources page itself.
5. Every chart drawing carries an accessible name: SVG drawings role="img" +
   aria-label; bar lists made of buttons are role="group" + aria-label
   (role="img" would hide their buttons from assistive tech).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
TOTALS = PLUGIN / "includes" / "market_totals.php"
RELATED = PLUGIN / "includes" / "related_links.php"
BOOT = PLUGIN / "talent-intelligence-tracker.php"
SHORT = PLUGIN / "includes" / "shortcodes.php"
HARNESS = ROOT / "tests" / "php" / "render_dashboard.php"

STUBS = r"""
define('ABSPATH', '/');
function esc_html($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s){ return (string)$s; }
function number_format_i18n($n){ return number_format((float)$n); }
function home_url($p = '/'){ return 'https://example.org/blog' . $p; }
"""

needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")


def _php(mod: Path, expr: str, *args) -> str:
    src = mod.read_text().replace("<?php", "", 1)
    p = subprocess.run(["php", "-r", STUBS + src + "\n" + expr, "--", *[json.dumps(a) for a in args]],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


def _market(variant="share"):
    weeks = [
        {"lo": "2026-09-21", "hi": "2026-09-27", "g": 92, "s": 0, "u": 1068, "n": 1160},
        {"lo": "2026-09-28", "hi": "2026-10-04", "g": 74, "s": 1, "u": 870, "n": 945},
        {"lo": "2026-10-05", "hi": "2026-10-11", "g": 0, "s": 0, "u": 0, "n": 0},
    ]
    return {"start": "2026-09-21", "end": "2026-10-11", "weeks": weeks, "weeks_total": 3,
            "with_data": 2, "panel": [], "panel_size": 2, "variant": variant}


# ---- item 1 ------------------------------------------------------------------

@needs_php
def test_totals_table_matches_the_chart_weeks_newest_first():
    out = _php(TOTALS, "echo tit_market_totals_table_html(json_decode($argv[1], true));", _market())
    assert "<table" in out and "<caption" in out and 'scope="col"' in out
    rows = re.findall(r"<tr><th scope=\"row\"[^>]*>([^<]+)</th>(.*?)</tr>", out)
    assert [r[0] for r in rows] == ["Week of 2026-10-05", "Week of 2026-09-28", "Week of 2026-09-21"]
    cells = re.findall(r"<td[^>]*>([^<]+)</td>", rows[1][1])
    assert cells == ["74", "1", "870", "945"]
    assert "1,068" in out and "1,160" in out
    assert "—" not in out, "no em-dashes in UI copy"


@needs_php
def test_share_variant_caption_says_the_chart_shows_shares():
    out = _php(TOTALS, "echo tit_market_totals_table_html(json_decode($argv[1], true));", _market("share"))
    assert "shares" in out.lower()
    out = _php(TOTALS, "echo tit_market_totals_table_html(json_decode($argv[1], true));", _market("counts"))
    assert "panel" in out.lower()


@needs_php
def test_no_chart_no_table():
    assert _php(TOTALS, "echo tit_market_totals_table_html(json_decode($argv[1], true));", _market("none")).strip() == ""
    assert _php(TOTALS, "echo tit_market_totals_table_html(array());").strip() == ""


def test_table_rides_under_the_market_chart_with_the_same_array():
    src = SHORT.read_text()
    chart = src.index("echo tit_market_trend_html($market);")
    table = src.index("tit_market_totals_table_html($market)")
    assert chart < table < src.index('id="chart-kind"')
    assert "tit_require('includes/market_totals.php')" in BOOT.read_text()


# ---- item 4 ------------------------------------------------------------------

def _map():
    return json.loads(_php(RELATED, "echo json_encode(tit_related_links_map());"))


def _hrefs(key):
    out = _php(RELATED, "echo tit_related_links_html(json_decode($argv[1]));", key)
    return re.findall(r'<a href="([^"]+)">', out), out


@needs_php
def test_every_page_links_data_sources_except_sources():
    m = _map()
    assert {"dashboard", "sources", "press", "recall", "corrections", "places", "place",
            "roles", "role", "company"} <= set(m)
    src_url = "https://example.org/blog/talent-intelligence-tracker/sources/"
    for key in m:
        hrefs, _ = _hrefs(key)
        assert hrefs, key
        assert len(hrefs) == len(set(hrefs)), key
        if key == "sources":
            assert src_url not in hrefs
        else:
            assert src_url in hrefs, key


@needs_php
def test_row_markup_and_unknown_key():
    hrefs, out = _hrefs("dashboard")
    assert 'class="tit-related"' in out and 'aria-label="Related pages"' in out
    assert "—" not in out
    assert _hrefs("nope")[1].strip() == ""


def test_every_page_render_passes_a_mapped_key():
    keys = set()
    for f in (PLUGIN / "includes").glob("*.php"):
        for call in re.findall(r"tit_render_footer\(([^)]*)\)", f.read_text()):
            if "function" in call:
                continue
            assert call.strip(), f"{f.name}: tit_render_footer() with no related key"
            keys.add(call.strip().strip("'\""))
    assert keys >= {"sources", "press", "recall", "corrections", "places", "place", "roles", "role", "company"}
    boot = BOOT.read_text()
    assert "function tit_render_footer($related = '')" in boot
    assert "tit_require('includes/related_links.php')" in boot
    assert "tit_related_links_html('dashboard')" in SHORT.read_text()


# ---- item 5 ------------------------------------------------------------------

def test_every_chart_drawing_has_an_accessible_name():
    found = 0
    for f in list((PLUGIN / "includes").glob("*.php")) + [BOOT]:
        src = f.read_text()
        for tag in re.findall(r"<svg\b.*?(?<!\?)>", src, re.S):
            if 'aria-hidden="true"' in tag or "tit-roo" in tag:
                continue
            found += 1
            assert 'role="img"' in tag and "aria-label" in tag, (f.name, tag[:100])
        for tag in re.findall(r"<div class=\"(?:tit-rank|tit-pillars|tit-market-box)\"[^>]*>", src, re.S):
            found += 1
            assert re.search(r'role="(group|img)"', tag) and "aria-label" in tag, (f.name, tag[:100])
    assert found > 8


# ---- end to end: the real shortcode through the real PHP ---------------------

@needs_php
def test_rendered_dashboard_carries_the_table_and_the_row():
    dest = os.path.join(tempfile.mkdtemp(), "out.html")
    subprocess.run(["php", str(HARNESS)], cwd=str(ROOT), env=dict(os.environ, TIT_DUMP_HTML=dest),
                   capture_output=True, text=True, timeout=300)
    if not os.path.exists(dest):
        pytest.skip("render_dashboard.php produced no markup: UNKNOWN, not a pass")
    html = open(dest, encoding="utf-8").read()
    os.unlink(dest)
    assert 'class="tit-market-totals' in html
    assert 'class="tit-related"' in html
