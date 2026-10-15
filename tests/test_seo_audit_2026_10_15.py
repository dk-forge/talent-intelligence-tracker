"""Defects found by the indexation audit of 2026-10-15 (docs/SEO_AUDIT_2026-10-15.md).

Each test pins a behaviour measured wrong on the live site:
  * the dashboard printed TWO FAQPage JSON-LD blocks;
  * Rank Math's empty-url "#webpage" node sat beside our page node on every
    company, place and role page;
  * those pages had no og:title / og:description / og:url;
  * nothing told a search engine a page changed (IndexNow).
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
INC = PLUGIN / "includes"

STUBS = r"""
define('ABSPATH', '/');
define('DAY_IN_SECONDS', 86400); define('MINUTE_IN_SECONDS', 60);
function esc_html($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function wp_json_encode($v, $f = 0){ return json_encode($v, $f); }
function home_url($p = '/'){ return 'https://example.org/blog' . $p; }
function add_action(){}
function add_filter(){}
"""

needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")


def _php(files: list[str], expr: str) -> str:
    src = "\n".join((INC / f).read_text(encoding="utf-8").replace("<?php", "", 1) for f in files)
    p = subprocess.run(["php", "-r", STUBS + src + "\n" + expr], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


@needs_php
def test_faq_section_can_be_rendered_without_a_second_faqpage():
    c = "$c = array('h2'=>'Q', 'faq'=>array(array('Why?', 'Because.')));"
    with_ld = _php(["page_faq.php"], c + "echo tit_page_faq_section_html($c);")
    without = _php(["page_faq.php"], c + "echo tit_page_faq_section_html($c, false);")
    assert with_ld.count('"FAQPage"') == 1
    assert '"FAQPage"' not in without
    assert "Why?" in without and "Because." in without  # still visible


def test_dashboard_wrap_suppresses_its_faqpage():
    # The dashboard shortcode prints its own FAQPage; the wrap must not add one.
    src = (INC / "page_faq.php").read_text(encoding="utf-8")
    body = re.search(r"function tit_page_faq_wrap_dashboard\(.*?\n\}", src, re.S).group(0)
    assert "tit_page_faq_section_html($c, false)" in body
    assert "FAQPage" in (INC / "shortcodes.php").read_text(encoding="utf-8")


@needs_php
def test_rank_math_empty_page_node_is_dropped_only_where_we_print_ours():
    graph = json.dumps([
        {"@type": "WebSite", "url": "https://example.org/blog/"},
        {"@type": "CollectionPage", "@id": "#webpage", "url": ""},
        {"@type": "WebPage", "url": "https://example.org/blog/x/"},
        {"@type": "BreadcrumbList"},
    ])
    out = json.loads(_php(["breadcrumbs.php"],
        f"echo json_encode(array_values(tit_breadcrumbs_strip_rank_math(json_decode('{graph}', true), true)));"))
    assert [e["@type"] for e in out] == ["WebSite", "WebPage"]
    untouched = json.loads(_php(["breadcrumbs.php"],
        f"echo json_encode(array_values(tit_breadcrumbs_strip_rank_math(json_decode('{graph}', true), false)));"))
    assert len(untouched) == 4


@needs_php
def test_og_tags_carry_title_description_url_and_escape():
    out = _php(["seo_meta.php"],
        "echo tit_seo_og_html('Who&#039;s hiring in \"London\"', 'Desc <b>', 'https://example.org/blog/city/london/');")
    assert '<meta property="og:title" content="Who&#039;s hiring in &quot;London&quot;" />' in out
    assert 'og:description" content="Desc &lt;b&gt;"' in out
    assert 'og:url" content="https://example.org/blog/city/london/"' in out
    assert "twitter:title" in out and "twitter:description" in out
    assert _php(["seo_meta.php"], "echo tit_seo_og_html('', '', '');") == ""


def test_og_tags_are_wired_into_every_entity_head():
    for f in ("company.php", "places.php", "roles.php"):
        assert "tit_seo_og_echo(" in (INC / f).read_text(encoding="utf-8"), f


@needs_php
def test_indexnow_pending_is_deduped_and_capped():
    out = json.loads(_php(["indexnow.php"],
        "echo json_encode(tit_indexnow_merge_pending(array('a','b'), array('b','', 'c','d'), 3));"))
    assert out == ["a", "b", "c"]


@needs_php
def test_indexnow_submits_only_publishable_company_pages():
    out = json.loads(_php(["indexnow.php"],
        "echo json_encode(tit_indexnow_url_list(array('https://h/t/'), array('ok','thin','ok'),"
        " array('ok'=>true), function($k){ return 'https://h/t/company/' . $k . '/'; }));"))
    assert out == ["https://h/t/", "https://h/t/company/ok/"]


def test_indexnow_reuses_the_sibling_key_and_never_mints_one():
    src = (INC / "indexnow.php").read_text(encoding="utf-8")
    assert "alt_indexnow_submit_urls" in src
    assert "random_bytes" not in src and "bin2hex" not in src
    # A write never waits on the network: it only queues and schedules.
    body = re.search(r"function tit_indexnow_after_write\(.*?\n\}", src, re.S).group(0)
    assert "wp_remote" not in body and "alt_indexnow_submit_urls" not in body
    # The daily hub throttle must survive tit_flush_caches(), which deletes tit_ transients.
    assert "set_transient('tit_indexnow" not in src


def test_write_routes_queue_indexnow_and_the_file_is_loaded():
    api = (INC / "api.php").read_text(encoding="utf-8")
    assert api.count("tit_indexnow_after_write(") == 2
    assert "tit_require('includes/indexnow.php');" in (PLUGIN / "talent-intelligence-tracker.php").read_text()
