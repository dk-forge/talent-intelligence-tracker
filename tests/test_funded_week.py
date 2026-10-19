"""Funded this week, now hiring: the weekly roundup page (1.111.0).

The page logic is proven in tests/php/funded_week.php on SQLite with a pinned
clock. This file runs it and pins what lives outside PHP: the dashboard link,
the tap floor, and the two refusals (no model, no reader mail, no writer).
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
FW = (PLUGIN / "includes" / "funded_week.php").read_text()
JS = (PLUGIN / "assets" / "dashboard.js").read_text()
CSS = (PLUGIN / "assets" / "dashboard.css").read_text()
BOOT = (PLUGIN / "talent-intelligence-tracker.php").read_text()

needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")


@needs_php
def test_roundup_harness_passes():
    p = subprocess.run(["php", str(ROOT / "tests" / "php" / "funded_week.php")],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr


def test_loaded_and_its_routes_are_verified():
    assert "tit_require('includes/funded_week.php');" in BOOT
    assert "'tit_funded_week_rewrites'" in BOOT


def test_reuses_the_cards_query_and_links_not_a_copy():
    assert "tit_raises_between(" in FW and "SELECT company, company_key" not in FW
    assert "tit_money_where()" in FW and "NOT IN" not in FW


def test_no_model_call_and_no_reader_mail():
    for banned in ("openrouter", "wp_mail", "opsmail", "/alert", "brevo"):
        assert banned not in FW.lower(), banned


def test_no_scheduled_writer_was_added():
    # Rendered live: the week rolls at 00:00 UTC Monday by the calendar alone.
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        assert "funded_week" not in wf.read_text() and "funded-this-week" not in wf.read_text(), wf.name


def test_dashboard_card_links_the_roundup_without_markup_bytes():
    assert "talent-intelligence-tracker/funded-this-week/" in JS
    assert "This week\\'s roundup" in JS
    assert re.search(r"var TR_WEEK = escUrl\(", JS)


def test_new_links_clear_the_44px_tap_floor_on_phones():
    phone = CSS[CSS.rindex("@media (max-width: 767px)"):]
    assert re.search(r"\.tit-tr-week \{[^}]*min-height:44px", phone)
    assert re.search(r"\.tit-fweek-archive a \{[^}]*min-height:44px", phone)
