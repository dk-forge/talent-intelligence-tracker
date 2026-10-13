"""Embeddable hub chart (owner-approved SEO work, 2026-10-13).

The embed page is a bare document (no theme) with the market-trend chart, a
visible backlink and the CC BY 4.0 attribution. The snippet on the press page
carries its own backlink outside the iframe, so attribution survives a blocked
frame. The press-page check runs in tests/php/render_press.php.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
MOD = PLUGIN / "includes" / "embed.php"

STUBS = r"""
define('ABSPATH', '/');
define('TIT_URL', 'https://example.org/blog/wp-content/plugins/tit/');
define('TIT_VERSION', '9.9.9');
function esc_html($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function home_url($p = '/'){ return 'https://example.org/blog' . $p; }
function add_action(){}
function add_filter(){}
function add_rewrite_rule(){}
"""

needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")


def _php(expr: str) -> str:
    src = MOD.read_text().replace("<?php", "", 1)
    p = subprocess.run(["php", "-r", STUBS + src + "\n" + expr],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


HUB = "https://example.org/blog/talent-intelligence-tracker/"


@needs_php
def test_document_has_chart_backlink_and_licence():
    doc = _php("echo tit_embed_document('<svg id=\"x\"></svg>', 'A caveat.');")
    assert doc.startswith("<!doctype html>")
    assert '<svg id="x"></svg>' in doc
    assert f'href="{HUB}"' in doc and 'target="_blank"' in doc
    assert "Talent Intelligence Tracker" in doc and "CC BY 4.0" in doc
    assert "noindex" in doc
    assert f'rel="canonical" href="{HUB}"' in doc


@needs_php
def test_snippet_is_an_iframe_with_a_backlink_outside_it():
    snip = _php("echo tit_embed_snippet();")
    assert snip.startswith("<iframe") and "/talent-intelligence-tracker/embed/market-trend/" in snip
    tail = snip.split("</iframe>", 1)[1]
    assert f'<a href="{HUB}">' in tail and "CC BY 4.0" in tail


def test_wired_into_bootstrap_routes_and_press():
    boot = (PLUGIN / "talent-intelligence-tracker.php").read_text()
    assert "tit_require('includes/embed.php')" in boot
    assert "'tit_embed_rewrites'" in boot
    assert "tit_embed_press_section(" in (PLUGIN / "includes" / "press.php").read_text()


def test_the_chart_is_the_hubs_own_and_not_a_copy():
    src = MOD.read_text()
    assert "tit_market_trend(" in src and "tit_market_trend_html(" in src
    assert "tit_notable_where()" in src
