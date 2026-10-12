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
