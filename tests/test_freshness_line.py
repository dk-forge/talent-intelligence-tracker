"""Visible "Last updated <date> · <N> sources" on company and place pages.

Owner-approved SEO work 2026-10-13. dateModified was already in the structured
data; a reader (and a rater) could not see it. The line is computed from the
same rows the page prints, never written down, and a future-dated row (a fiscal
year that has not ended) never becomes a future "last updated" date, matching
the sitemap's lastmod rule.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
MOD = PLUGIN / "includes" / "freshness.php"

STUBS = r"""
define('ABSPATH', '/');
function esc_html($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function number_format_i18n($n, $d = 0){ return number_format((float)$n, (int)$d); }
function date_i18n($f, $t = null){ return gmdate($f, $t === null ? time() : $t); }
function _n($a, $b, $n, $d = ''){ return $n == 1 ? $a : $b; }
"""

needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")


def _php(expr: str) -> str:
    src = MOD.read_text().replace("<?php", "", 1)
    p = subprocess.run(["php", "-r", STUBS + src + "\n" + expr],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


@needs_php
def test_line_states_date_and_source_count():
    html = _php("echo tit_freshness_line('2026-10-01', 7, '2026-10-13');")
    assert "Last updated" in html
    assert '<time datetime="2026-10-01">1 Oct 2026</time>' in html
    assert "7 sources" in html


@needs_php
def test_one_source_is_singular():
    assert "1 source<" in _php("echo tit_freshness_line('2026-10-01', 1, '2026-10-13');")


@needs_php
def test_a_future_date_is_never_printed():
    html = _php("echo tit_freshness_line('2027-03-31', 3, '2026-10-13');")
    assert "2027" not in html
    assert 'datetime="2026-10-13"' in html


@needs_php
def test_nothing_held_prints_nothing():
    assert _php("echo tit_freshness_line('', 0, '2026-10-13');") == ""


@needs_php
def test_a_date_without_sources_still_prints_the_date_only():
    html = _php("echo tit_freshness_line('2026-09-01', 0, '2026-10-13');")
    assert "Last updated" in html and "source" not in html


def test_both_page_types_render_the_line():
    company = (PLUGIN / "includes" / "company.php").read_text()
    places = (PLUGIN / "includes" / "places.php").read_text()
    assert "tit_freshness_line(" in company
    assert "tit_freshness_line(" in places
    boot = (PLUGIN / "talent-intelligence-tracker.php").read_text()
    assert "tit_require('includes/freshness.php')" in boot


def test_company_profile_carries_its_newest_date():
    company = (PLUGIN / "includes" / "company.php").read_text()
    assert "'last_updated'" in company
