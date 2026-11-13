"""Per-company and per-city follow alerts (owner ruling 2026-10-15).

The email system lives in the sibling plugin. Pinned: nothing renders and no
filter is registered unless BOTH the sibling's contract constant and the
owner's option are set; the form posts to the sibling's own handler; rows are
handed over in the sibling's section shape with no job count invented; and this
plugin never sends mail or stores an address itself.
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
MOD = PLUGIN / "includes" / "follows.php"

STUBS = r"""
define('ABSPATH', '/');
function esc_html($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function add_action(){} function add_filter(){}
"""

needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")


def _php(expr: str) -> str:
    src = MOD.read_text(encoding="utf-8").replace("<?php", "", 1)
    p = subprocess.run(["php", "-r", STUBS + src + "\n" + expr], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


@needs_php
def test_both_gates_must_hold():
    assert _php("var_export(tit_follows_gate(true, '1'));") == "true"
    assert _php("var_export(tit_follows_gate(false, '1'));") == "false"
    assert _php("var_export(tit_follows_gate(true, '0'));") == "false"
    assert _php("var_export(tit_follows_gate(true, ''));") == "false"


@needs_php
def test_form_posts_to_the_sibling_handler_and_escapes():
    html = _php("echo tit_follow_form_html('talent_city', 'London', 'London <b>', 'https://e/wp-admin/admin-post.php');")
    assert 'name="action" value="alt_follow"' in html
    assert 'name="alt_follow_kind" value="talent_city"' in html
    assert "London &lt;b&gt;" in html and "<b>" not in html
    assert 'name="alt_hp"' in html  # the sibling's honeypot
    assert "confirmation link" in html


@needs_php
def test_form_refuses_unknown_kind_or_empty_value():
    assert _php("echo tit_follow_form_html('state', 'CA', 'California', 'u');") == ""
    assert _php("echo tit_follow_form_html('talent_company', '', 'X', 'u');") == ""


@needs_php
def test_sent_state_shows_status_not_a_form():
    html = _php("echo tit_follow_form_html('talent_company', 'openai', 'OpenAI', 'u', true);")
    assert 'role="status"' in html and "<form" not in html


@needs_php
def test_rows_take_the_sibling_section_shape_without_a_job_count():
    rows = [{"company": "Acme", "d": "2026-10-01", "city": "London"}]
    out = json.loads(_php(f"echo json_encode(tit_follow_section_rows(json_decode('{json.dumps(rows)}', true), 'London', 'https://e/city/london/'));"))
    assert out == [{"follow": "London", "company": "Acme", "jobs": 0, "date": "2026-10-01",
                    "place": "London", "url": "https://e/city/london/"}]


def test_this_plugin_never_sends_mail_or_stores_an_address():
    src = MOD.read_text(encoding="utf-8")
    for banned in ("wp_mail", "wp_remote_post", "INSERT", "update_option('tit_follow"):
        assert banned not in src, banned
    # Registration is behind the gate, and the pages only ever call the gated helper.
    body = re.search(r"function tit_follows_register\(.*?\n\}", src, re.S).group(0)
    assert "if (!tit_follows_enabled()) return;" in body
    assert "tit_follow_form('talent_company'" in (PLUGIN / "includes" / "company.php").read_text()
    assert "tit_follow_form('talent_city'" in (PLUGIN / "includes" / "places.php").read_text()
