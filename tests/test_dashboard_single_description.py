"""The dashboard prints exactly one meta description.

2026-10-01, found by the owner's SEO pass: /blog/talent-intelligence-tracker/
carried two `meta[name=description]` and two `og:description` tags, Rank
Math's and the plugin's live-count line, so share cards picked one at random.
The dashboard is a real WordPress page that Rank Math describes, so when an
SEO plugin is active the plugin stays silent there. Without one, the live-count
fallback still prints, so the page is never left with no description.

Runs the real page.php under php with WordPress stubbed. Without php on PATH
this SKIPS, which is UNKNOWN and not a pass.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = (Path(__file__).resolve().parents[1]
        / "wordpress-plugin" / "talent-intelligence-tracker" / "includes" / "page.php")
PHP = shutil.which("php")

HARNESS = r"""
define('ABSPATH', '/');
define('TIT_VERSION', 'test');
%(seo)s
function add_action(...$a) {}
function add_filter(...$a) {}
function is_page($s = null) { return true; }
function tit_table_name() { return 't'; }
function tit_dashboard_facts($t) { return array('notable' => 35049, 'companies' => 900, 'countries' => 40); }
function number_format_i18n($n) { return number_format($n); }
function esc_attr($s) { return htmlspecialchars($s, ENT_QUOTES); }
require '%(page)s';
tit_dashboard_head();
"""


def render(seo_define: str) -> str:
    code = HARNESS % {"seo": seo_define, "page": PAGE}
    out = subprocess.run([PHP, "-r", code], capture_output=True, text=True, check=True)
    return out.stdout


@pytest.mark.skipif(PHP is None, reason="php not on PATH: UNKNOWN, not a pass")
@pytest.mark.parametrize("define", [
    "define('RANK_MATH_VERSION', '1.0');",
    "define('WPSEO_VERSION', '1.0');",
])
def test_silent_when_an_seo_plugin_describes_the_page(define):
    html = render(define)
    assert 'name="description"' not in html
    assert "og:description" not in html


@pytest.mark.skipif(PHP is None, reason="php not on PATH: UNKNOWN, not a pass")
def test_live_count_fallback_without_an_seo_plugin():
    html = render("")
    assert html.count('name="description"') == 1
    assert html.count("og:description") == 1
    assert "35,049" in html
