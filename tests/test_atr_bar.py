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
CTA = PLUGIN / "includes" / "atr_signup_cta.php"
STUBS = ("define('ABSPATH', '/');\n"
         "function esc_url($s) { return htmlspecialchars((string) $s, ENT_QUOTES); }\n"
         "function esc_attr($s) { return htmlspecialchars((string) $s, ENT_QUOTES); }\n"
         "function add_action() {}\n")
needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")


def _php(expr: str) -> str:
    src = "".join(m.read_text().replace("<?php", "", 1) for m in (CTA, MOD))
    p = subprocess.run(["php", "-r", STUBS + src + "\n" + expr],
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
    # one source per tracker: the same helper as the page's sign-up line
    assert q["utm_source"] == ["talent_tracker"] and q["utm_medium"] == ["cta"]
    assert q["utm_campaign"] == ["sticky_bar"]
    assert q["utm_content"] == ["sticky_bar"]
    # the dashboard's phone jump bar (Filters / Updates) is lifted above it
    assert "body.tit-atr-bar-on .tit-jump{bottom:calc(74px" in html
    assert 'id="tit-atr-bar-close"' in html and 'aria-label="Close"' in html
    assert " hidden>" in html and "@media (min-width:601px){.tit-atr-bar{display:none}" in html
    assert "min-height:44px" in html  # WCAG 2.2 target size


def test_script_is_a_file_and_storage_is_guarded():
    js = (PLUGIN / "assets" / "atr-bar.js").read_text()
    assert js.count("try{") == 2
    assert "tit_require('includes/atr_bar.php');" in (PLUGIN / "talent-intelligence-tracker.php").read_text()


def test_top_of_page_line_on_company_and_places_pages():
    """todo 1853 item 4: a tracked line inside the first screen, with its own
    landmark name, without moving the footer line (no upload-order gap)."""
    inc = PLUGIN / "includes"
    company = (inc / "company.php").read_text()
    i = company.index("tit_atr_cta_line_html('company_top', 'Resume check')")
    assert i < company.index("At a glance")
    places = (inc / "places.php").read_text()
    j = places.index("tit_atr_cta_line_html('place_top', 'Resume check')")
    assert places.index("tit_freshness_line($facts['span']['hi']") < j < places.index("tit_render_footer('place')")
    k = places.index("tit_atr_cta_line_html('places_top', 'Resume check')")
    assert places.index("tit_page_faq_intro('places')") < k < places.index("tit_render_footer('places')")
    main = (PLUGIN / "talent-intelligence-tracker.php").read_text()
    assert "$related !== '' && $related !== 'company' && function_exists('tit_atr_cta_line_html')" in main


@needs_php
def test_second_line_on_a_page_has_its_own_landmark_name():
    top = _php("echo tit_atr_cta_line_html('place_top', 'Resume check');")
    foot = _php("echo tit_atr_cta_line_html('place');")
    assert 'aria-label="Resume check"' in top and 'aria-label="Resume help"' in foot
