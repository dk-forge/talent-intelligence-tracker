"""Breadcrumb trails (owner request 2026-10-12).

One trail per page drives BOTH the visible nav and the BreadcrumbList JSON-LD,
so a reader and a crawler are never told different paths.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
MOD = PLUGIN / "includes" / "breadcrumbs.php"

STUBS = r"""
define('ABSPATH', '/');
function esc_html($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function wp_json_encode($v, $f = 0){ return json_encode($v, $f); }
function home_url($p = '/'){ return 'https://example.org/blog' . $p; }
function add_action(){}
function add_filter(){}
"""

needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")


def _php(expr: str) -> str:
    src = MOD.read_text().replace("<?php", "", 1)
    p = subprocess.run(["php", "-r", STUBS + src + "\n" + expr], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


CITY = ("$t = tit_breadcrumbs_place_trail('Cities', 'https://example.org/blog/talent-intelligence-tracker/places/',"
        " 'London', 'https://example.org/blog/talent-intelligence-tracker/city/london/');")


@needs_php
def test_visible_trail_matches_jsonld():
    html = _php(CITY + "echo tit_breadcrumbs_html($t);")
    ld = json.loads(re.search(r">(\{.*\})</script>", _php(CITY + "echo tit_breadcrumbs_jsonld($t);")).group(1))
    assert ld["@type"] == "BreadcrumbList"
    names = [i["name"] for i in ld["itemListElement"]]
    assert names == ["Talent Intelligence Tracker", "Cities", "London"]
    assert re.findall(r'<a href="([^"]+)"', html) == [i["item"] for i in ld["itemListElement"]][:-1]


@needs_php
def test_markup_is_accessible():
    html = _php(CITY + "echo tit_breadcrumbs_html($t);")
    assert '<nav class="tit-crumb" aria-label="Breadcrumb">' in html
    assert '<span aria-current="page">London</span>' in html
    assert "city/london" not in html  # the current page is not a link


@needs_php
def test_escapes_names():
    out = _php("echo tit_breadcrumbs_html(tit_breadcrumbs_company_trail('<b>A&B</b>', 'u'));")
    assert "&lt;b&gt;A&amp;B&lt;/b&gt;" in out


def test_pages_render_the_shared_trail():
    places = (PLUGIN / "includes" / "places.php").read_text()
    company = (PLUGIN / "includes" / "company.php").read_text()
    assert "tit_breadcrumbs_html(tit_breadcrumbs_place_trail(" in places
    assert "tit_breadcrumbs_html(tit_breadcrumbs_places_trail(" in places
    assert "tit_breadcrumbs_company_trail(" in company
    assert "tit_require('includes/breadcrumbs.php')" in (PLUGIN / "talent-intelligence-tracker.php").read_text()


def test_crumb_list_wraps_on_a_phone():
    css = (PLUGIN / "assets" / "dashboard.css").read_text()
    assert re.search(r"\.tit-crumb-list\s*\{[^}]*flex-wrap:\s*wrap", css)


# --- 2026-10-12 follow-up: every TIT route, and one BreadcrumbList only ------

SECTION_PAGES = {
    "press.php": "Press",
    "recall.php": "Measured recall",
    "sources.php": "Sources",
    "corrections.php": "Corrections",
}


def test_section_pages_use_the_shared_trail():
    """Press, recall, sources and corrections hand-built an unlabelled
    <nav class="tit-crumb"> with no list, no aria-label, no aria-current and no
    JSON-LD. They now print the shared trail like every other route."""
    for f in SECTION_PAGES:
        src = (PLUGIN / "includes" / f).read_text()
        assert '<nav class="tit-crumb">' not in src, f
        assert "tit_breadcrumbs_html(tit_breadcrumbs_section_trail(" in src, f


@needs_php
def test_section_trail_matches_jsonld():
    t = "$t = tit_breadcrumbs_section_trail('Press', 'https://example.org/blog/talent-intelligence-tracker/press/');"
    html = _php(t + "echo tit_breadcrumbs_html($t);")
    ld = json.loads(re.search(r">(\{.*\})</script>", _php(t + "echo tit_breadcrumbs_jsonld($t);")).group(1))
    assert [i["name"] for i in ld["itemListElement"]] == ["Talent Intelligence Tracker", "Press"]
    assert '<span aria-current="page">Press</span>' in html


@needs_php
def test_rank_math_one_item_list_is_dropped_on_our_routes():
    """Rank Math adds a one-item "Home" BreadcrumbList to every virtual route.
    Where we print our own, its list goes, and the WebPage reference to it."""
    data = ("$d = array('WebSite' => array('@type' => 'WebSite'),"
            " 'BreadcrumbList' => array('@type' => 'BreadcrumbList', '@id' => 'x#breadcrumb'),"
            " 'WebPage' => array('@type' => 'WebPage', 'breadcrumb' => array('@id' => 'x#breadcrumb')));")
    out = json.loads(_php(data + "echo json_encode(tit_breadcrumbs_strip_rank_math($d, true));"))
    assert "BreadcrumbList" not in out and "breadcrumb" not in out["WebPage"]
    assert out["WebSite"]["@type"] == "WebSite"
    kept = json.loads(_php(data + "echo json_encode(tit_breadcrumbs_strip_rank_math($d, false));"))
    assert "BreadcrumbList" in kept


def test_head_hook_covers_every_route():
    src = MOD.read_text()
    for qv in ("tit_press", "tit_recall", "tit_sources", "tit_corrections"):
        assert f"'{qv}'" in src, qv
    assert "rank_math/json_ld" in src
