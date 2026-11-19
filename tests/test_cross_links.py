"""todo#1881 item 5: links to the same company/country/sector in the layoff tracker."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "wordpress-plugin" / "talent-intelligence-tracker"
DATA = json.loads((PLUGIN / "data" / "cross-links.json").read_text())


def test_ambiguous_slugs_left_out_and_known_ones_present():
    for slug in ("one", "coop", "bull", "nec", "pandora", "toast"):
        assert slug not in DATA["companies"]
    assert "amazon" in DATA["companies"] and "germany" in DATA["countries"]
    assert [p[0] for p in DATA["industries"]["healthcare"]] == ["healthcare-pharma"]


def test_wired_guarded_and_no_city_links():
    comp = (PLUGIN / "includes" / "company.php").read_text()
    places = (PLUGIN / "includes" / "places.php").read_text()
    assert "function_exists('tit_cross_links_html')" in comp
    assert "function_exists('tit_cross_links_html') && $kind !== 'city'" in places
    assert "tit_require('includes/cross_links.php');" in (PLUGIN / "talent-intelligence-tracker.php").read_text()


def test_link_text_carries_no_number():
    src = (PLUGIN / "includes" / "cross_links.php").read_text()
    fn = src[src.index("function tit_cross_links_html"):]
    assert "number_format" not in fn and "jobs" not in fn


@pytest.mark.skipif(not shutil.which("php"), reason="php CLI not installed: UNKNOWN, NOT RUN")
def test_renders_only_existing_targets():
    php = r'''
define("ABSPATH", 1); define("TIT_PATH", "%s/");
function esc_html($s){return htmlspecialchars($s);} function esc_url($s){return $s;}
function sanitize_title($s){return trim(preg_replace('/[^a-z0-9]+/','-',strtolower($s)),'-');}
function home_url($p=''){return 'https://asktherecruiter.com/blog'.$p;}
require "%s/includes/cross_links.php";
echo tit_cross_links_html('company','amazon','Amazon'),"\n";
echo tit_cross_links_html('company','one','One'),"|\n";
echo tit_cross_links_html('industry','pharma-and-biotech','Pharma and biotech'),"\n";
''' % (PLUGIN, PLUGIN)
    out = subprocess.run(["php", "-r", php], capture_output=True, text=True, check=True).stdout.splitlines()
    assert 'href="https://asktherecruiter.com/blog/company-layoffs/amazon/"' in out[0]
    assert out[1] == "|"
    assert "industry-layoffs/healthcare-pharma/" in out[2] and "Healthcare and pharma in the layoff tracker" in out[2]
