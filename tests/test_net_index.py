"""The monthly Net Talent Index page (owner ruling 2026-10-15).

Pinned: the formula, the floors (a thin month or city prints no figure), the
month in progress is never published, month-on-month change only between two
published values, one URL per month in its own sitemap listed in the index,
Dataset markup per page, and no em or en dash in the reader copy.
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
MOD = PLUGIN / "includes" / "net_index.php"

STUBS = r"""
define('ABSPATH', '/'); define('HOUR_IN_SECONDS', 3600);
function home_url($p = '/'){ return 'https://example.org/blog' . $p; }
function add_action(){} function add_filter(){}
"""

needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")


def _php(expr: str) -> str:
    src = MOD.read_text(encoding="utf-8").replace("<?php", "", 1)
    p = subprocess.run(["php", "-r", STUBS + src + "\n" + expr], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


@needs_php
def test_formula_and_floor():
    assert _php("echo json_encode(tit_net_index_value(60, 2, 400));") == "14.5"
    assert _php("echo json_encode(tit_net_index_value(60, 2, 199));") == "null"
    assert _php("echo json_encode(tit_net_index_value(10, 0, 30, 30));") == "33.3"
    assert _php("echo json_encode(tit_net_index_value(10, 0, 29, 30));") == "null"


@needs_php
def test_months_drop_current_and_early_and_chain_change_only_between_values():
    rows = [
        {"m": "2025-12", "active": 900, "adding": 1, "cutting": 0},
        {"m": "2026-02", "active": 1000, "adding": 120, "cutting": 0},
        {"m": "2026-01", "active": 1000, "adding": 100, "cutting": 0},
        {"m": "2026-03", "active": 100, "adding": 50, "cutting": 0},   # below the floor
        {"m": "2026-04", "active": 1000, "adding": 90, "cutting": 10},
        {"m": "2026-10", "active": 2000, "adding": 400, "cutting": 0},  # in progress
        {"m": "garbage", "active": 5000, "adding": 1, "cutting": 0},
    ]
    out = json.loads(_php(f"echo json_encode(tit_net_index_months(json_decode('{json.dumps(rows)}', true), '2026-10'));"))
    assert list(out) == ["2026-01", "2026-02", "2026-03", "2026-04"]
    assert out["2026-01"]["value"] == 10 and out["2026-01"]["change"] is None
    assert out["2026-02"]["value"] == 12 and out["2026-02"]["change"] == 2
    assert out["2026-03"]["value"] is None and out["2026-03"]["change"] is None
    assert out["2026-04"]["value"] == 8 and out["2026-04"]["change"] is None  # previous had no value


@needs_php
def test_metros_need_the_floor_and_rank_by_value():
    rows = [{"city": "A", "active": 40, "adding": 4, "cutting": 0},
            {"city": "B", "active": 50, "adding": 20, "cutting": 1},
            {"city": "C", "active": 10, "adding": 9, "cutting": 0}]
    out = json.loads(_php(f"echo json_encode(tit_net_index_metros(json_decode('{json.dumps(rows)}', true)));"))
    assert [m["city"] for m in out] == ["B", "A"]


@needs_php
def test_dataset_node_per_month_and_for_the_hub():
    months = {"2026-08": {}, "2026-09": {}}
    one = json.loads(_php(f"echo json_encode(tit_net_index_dataset_node(json_decode('{json.dumps(months)}', true), '2026-09', 'u'));"))
    assert one["@type"] == "Dataset" and one["temporalCoverage"] == "2026-09"
    assert one["license"].startswith("https://creativecommons.org/licenses/by/4.0")
    assert "September 2026" in one["name"] and one["variableMeasured"]
    hub = json.loads(_php(f"echo json_encode(tit_net_index_dataset_node(json_decode('{json.dumps(months)}', true), '', 'u'));"))
    assert hub["temporalCoverage"] == "2026-08/2026-09"


@needs_php
def test_headline_sentence_is_quotable_and_signed():
    row = {"m": "2026-09", "value": 11.2, "adding": 544, "active": 4850, "cutting": 2, "change": -1.4}
    s = _php(f"echo tit_net_index_headline_sentence(json_decode('{json.dumps(row)}', true));")
    assert s.startswith("The Net Talent Index for September 2026 is 11.2: 544 of 4,850 employers")
    assert "down 1.4 points" in s


@needs_php
def test_month_urls_and_validation():
    assert _php("echo tit_net_index_url('2026-09');") == "https://example.org/blog/talent-intelligence-tracker/net-talent-index/2026-09/"
    assert _php("echo tit_net_index_clean_month('2026-13') . '|' . tit_net_index_clean_month('2026-09x');") == "|"


def test_routes_sitemap_and_wiring():
    src = MOD.read_text(encoding="utf-8")
    assert "(\\d{4}-\\d{2})" in src and "net-index-sitemap.xml" in src
    assert "set_404()" in src  # an unpublished month is a real 404, not a soft 200
    assert "tit_net_index_sitemap_url" in (PLUGIN / "includes" / "sitemap_index.php").read_text()
    assert "tit_require('includes/net_index.php');" in (PLUGIN / "talent-intelligence-tracker.php").read_text()
    for f in ("press.php", "places.php"):
        assert "tit_net_index_url()" in (PLUGIN / "includes" / f).read_text(), f


def test_no_em_or_en_dash_in_reader_copy():
    src = MOD.read_text(encoding="utf-8")
    assert "—" not in src and "–" not in src
