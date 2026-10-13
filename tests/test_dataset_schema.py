"""SEO build 2026-10-13, slice 1: schema.org Dataset on the hub, plus the
"X% of records link a primary source" figure, both computed from the data.

The share's numerator is `confidence = 'verified'`, which validate.py only
allows on a PRIMARY_SOURCE_DOMAINS host, so it is a floor and the copy says
"at least". Nothing here is typed: every number comes from the facts bundle.
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
MOD = PLUGIN / "includes" / "dataset_schema.php"

STUBS = (
    "define('ABSPATH', '/');\n"
    "function home_url($p = '') { return 'https://example.org/blog' . $p; }\n"
    "function admin_url($p = '') { return 'https://example.org/blog/wp-admin/' . $p; }\n"
    "function esc_html($s) { return htmlspecialchars((string) $s, ENT_QUOTES); }\n"
    "function esc_attr($s) { return htmlspecialchars((string) $s, ENT_QUOTES); }\n"
    "function number_format_i18n($n) { return number_format($n); }\n"
    "function wp_json_encode($v, $f = 0) { return json_encode($v, $f); }\n"
    "function add_action() {}\n"
)
needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")

FACTS = {"notable": 4000, "verified": 1234, "companies": 900,
         "span_lo": "2025-01-02", "span_hi": "2026-10-12",
         "newest_run": "2026-10-12 22:10:00", "countries": 57}


def _php(expr: str) -> str:
    src = MOD.read_text().replace("<?php", "", 1)
    p = subprocess.run(["php", "-r", STUBS + src + "\n" + expr],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


def _facts() -> str:
    return "json_decode(%s, true)" % json.dumps(json.dumps(FACTS))


@needs_php
@pytest.mark.parametrize("v,t,want", [(1234, 4000, 30), (0, 10, 0), (5, 0, None),
                                      (999, 1000, 99), (1000, 1000, 100)])
def test_share_is_floored_and_never_rounds_up(v, t, want):
    out = _php("var_export(tit_primary_source_share(%d, %d));" % (v, t))
    assert out == ("NULL" if want is None else str(want))


@needs_php
def test_dataset_node_carries_required_fields_from_data():
    node = json.loads(_php("echo json_encode(tit_dataset_node(%s));" % _facts()))
    assert node["@type"] == "Dataset"
    assert node["license"] == "https://creativecommons.org/licenses/by/4.0/"
    assert node["creator"]["name"] == "Ask The Recruiter"
    assert node["temporalCoverage"] == "2025-01-02/2026-10-12"
    assert node["dateModified"] == "2026-10-12"
    assert node["spatialCoverage"]
    dist = node["distribution"][0]
    assert dist["@type"] == "DataDownload" and dist["encodingFormat"] == "text/csv"
    assert "tit_export_csv" in dist["contentUrl"]
    # The share appears in the description, computed: 1234/4000 -> 30%.
    assert "30%" in node["description"]
    assert "4,000" in node["description"]


@needs_php
def test_no_share_claim_on_an_empty_corpus():
    f = dict(FACTS, notable=0, verified=0)
    node = json.loads(_php("echo json_encode(tit_dataset_node(json_decode(%s, true)));"
                           % json.dumps(json.dumps(f))))
    assert "%" not in node["description"]
    assert _php("echo tit_primary_source_line_html(json_decode(%s, true));"
                % json.dumps(json.dumps(f))) == ""


@needs_php
def test_visible_line_states_the_floor_and_its_denominator():
    html = _php("echo tit_primary_source_line_html(%s);" % _facts())
    assert 'data-testid="tit-primary-share"' in html
    assert "At least 30%" in html and "4,000" in html


@needs_php
def test_jsonld_script_escapes_closing_tags():
    f = dict(FACTS, span_lo="</script><x>")
    out = _php("echo tit_dataset_jsonld_html(json_decode(%s, true));" % json.dumps(json.dumps(f)))
    assert out.startswith('<script type="application/ld+json">')
    assert out.count("</script>") == 1


def test_hub_renders_both():
    src = (PLUGIN / "includes" / "shortcodes.php").read_text()
    assert "tit_dataset_jsonld_html(" in src and "tit_primary_source_line_html(" in src
    main = (PLUGIN / "talent-intelligence-tracker.php").read_text()
    assert re.search(r"tit_require\('includes/dataset_schema\.php'\)", main)
