"""todo#1853 CTA: the phone sign-up bar on every hiring-tracker page."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
MOD = PLUGIN / "includes" / "atr_bar.php"
STUBS = ("define('ABSPATH', '/');\n"
         "function esc_url($s) { return htmlspecialchars((string) $s, ENT_QUOTES); }\n"
         "function add_action() {}\n")
needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")


def _php(expr: str) -> str:
    p = subprocess.run(["php", "-r", STUBS + MOD.read_text().replace("<?php", "", 1) + "\n" + expr],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


@needs_php
@pytest.mark.parametrize("uri,want", [
    ("/blog/talent-intelligence-tracker/", True),
    ("/blog/talent-intelligence-tracker/company/acme/", True),
    ("/blog/talent-intelligence-tracker/places/", True),
    ("/blog/talent-intelligence-tracker/city/berlin/?x=1", True),
    ("/blog/talent-intelligence-tracker/llms.txt", False),
    ("/blog/ai-layoff-tracker/", False),
    ("/blog/some-post/", False),
])
def test_bar_shows_on_every_tracker_route_only(uri, want):
    assert _php("var_export(tit_atr_bar_is_route(%s));" % json.dumps(uri)) == ("true" if want else "false")


@needs_php
def test_bar_is_tagged_closable_hidden_until_js_and_phone_only():
    html = _php("echo tit_atr_bar_html();")
    href = html.split('<a href="', 1)[1].split('"', 1)[0].replace("&amp;", "&")
    u = urlparse(href)
    assert u.netloc == "asktherecruiter.com" and u.path == "/resume-score"
    q = parse_qs(u.query)
    assert q["utm_source"] == ["talent-tracker"] and q["utm_campaign"] == ["sticky_bar"]
    assert 'id="tit-atr-bar-close"' in html and 'aria-label="Close"' in html
    assert " hidden>" in html and "@media (min-width:601px){.tit-atr-bar{display:none}" in html
    assert "min-height:44px" in html  # WCAG 2.2 target size


def test_script_is_a_file_and_storage_is_guarded():
    js = (PLUGIN / "assets" / "atr-bar.js").read_text()
    assert js.count("try{") == 2
    assert "tit_require('includes/atr_bar.php');" in (PLUGIN / "talent-intelligence-tracker.php").read_text()
