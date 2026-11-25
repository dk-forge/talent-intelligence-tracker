"""todo#1853 AI search: the hiring tracker's own llms.txt, quotable figures with
an "as of" date and a source page, every number from the facts bundle."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
MODS = [PLUGIN / "includes" / "dataset_schema.php", PLUGIN / "includes" / "atr_signup_cta.php",
        PLUGIN / "includes" / "llms.php"]
STUBS = (
    "define('ABSPATH', '/');\n"
    "function home_url($p = '') { return 'https://example.org/blog' . $p; }\n"
    "function admin_url($p = '') { return 'https://example.org/blog/wp-admin/' . $p; }\n"
    "function esc_url($s) { return (string) $s; }\n"
         "function esc_attr($s) { return htmlspecialchars((string) $s, ENT_QUOTES); }\n"
    "function esc_html($s) { return htmlspecialchars((string) $s, ENT_QUOTES); }\n"
    "function number_format_i18n($n) { return number_format($n); }\n"
    "function wp_json_encode($v, $f = 0) { return json_encode($v, $f); }\n"
    "function add_action() {}\n"
)
needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")
FACTS = {"notable": 4000, "verified": 1234, "companies": 900, "countries": 57,
         "span_lo": "2024-03-01", "span_hi": "2026-10-12",
         "view_lo": "2025-01-02", "view_hi": "2026-10-11", "newest_run": "2026-10-12 22:10:00"}


def _php(expr: str) -> str:
    src = "\n".join(m.read_text().replace("<?php", "", 1) for m in MODS)
    p = subprocess.run(["php", "-r", STUBS + src + "\n" + expr], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


def _txt(facts) -> str:
    return _php("echo tit_llms_txt_content(json_decode(%s, true));" % json.dumps(json.dumps(facts)))


@needs_php
def test_quotable_sentences_carry_date_number_and_source():
    out = _txt(FACTS)
    assert "## Quotable figures (as of 2026-10-12)" in out
    assert ("As of 2026-10-12, the Talent Intelligence Tracker by AskTheRecruiter.com holds 4,000 "
            "sourced employer signals dated 2025-01-02 to 2026-10-11. "
            "Source: https://example.org/blog/talent-intelligence-tracker/") in out
    # the hub's wording: records FROM countries, never "covering" them
    assert "900 employers, with records from 57 countries; measured coverage varies by country" in out
    assert "cover 57" not in out and "covering" not in out.lower()
    # the notable set's bounds (as the hub prints them), never the all-rows span
    assert "2024-03-01" not in out
    # the same floored share the hub prints (1234/4000 = 30.85 -> 30)
    assert "at least 30% of them link a primary source" in out
    share = next(l for l in out.splitlines() if "primary source" in l and l.startswith("- As of"))
    # the hub prints the share; /sources/ does not
    assert share.endswith("Source: https://example.org/blog/talent-intelligence-tracker/")
    assert "/talent-intelligence-tracker/methodology/" in out and "/talent-intelligence-tracker/recall/" in out
    assert "resume-score?utm_source=talent_tracker&utm_medium=cta&utm_campaign=llms_txt&utm_content=llms_txt" in out
    for line in out.splitlines():
        if line.startswith("- As of"):
            assert "Source: https://" in line


@needs_php
def test_no_corpus_no_figures():
    out = _txt({})
    assert "Quotable figures" not in out and "## Key pages" in out


@needs_php
@pytest.mark.parametrize("uri,want", [
    ("/blog/talent-intelligence-tracker/llms.txt", True),
    ("/blog/talent-intelligence-tracker/llms.txt?x=1", True),
    ("/blog/llms.txt", False),
    ("/blog/ai-layoff-tracker/llms.txt", False),
])
def test_only_this_trackers_path_is_claimed(uri, want):
    assert _php("var_export(tit_llms_is_request(%s));" % json.dumps(uri)) == ("true" if want else "false")


def test_handler_runs_before_the_layoff_trackers_catch_all():
    src = (PLUGIN / "includes" / "llms.php").read_text()
    assert "add_action('init', 'tit_serve_llms_txt', -1);" in src
    assert "tit_require('includes/llms.php');" in (PLUGIN / "talent-intelligence-tracker.php").read_text()


# todo#1881 item 10: the hub's "Key numbers" block uses the SAME sentences.
@needs_php
def test_hub_key_numbers_block_matches_llms_txt_and_is_dated():
    html = _php("echo tit_key_numbers_html(json_decode(%s, true));" % json.dumps(json.dumps(FACTS)))
    txt = _txt(FACTS)
    assert 'Key numbers (updated <time datetime="2026-10-12">October 12, 2026</time>)' in html
    lines = _php("echo json_encode(tit_key_number_lines(json_decode(%s, true)));" % json.dumps(json.dumps(FACTS)))
    for line in json.loads(lines):
        assert line in txt
        assert line.replace("%", "%") in html.replace("&#039;", "'")
    assert 'aria-labelledby="tit-key-numbers-h"' in html


@needs_php
def test_hub_key_numbers_block_is_empty_without_a_corpus():
    assert _php("echo tit_key_numbers_html(array('notable' => 0));") == ""


def test_hub_renders_the_block_under_the_dashboard():
    sc = (PLUGIN / "includes" / "shortcodes.php").read_text()
    assert "$html .= tit_key_numbers_html(tit_dashboard_facts(tit_table_name()));" in sc
