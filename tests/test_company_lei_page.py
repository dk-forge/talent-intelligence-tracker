"""The LEI on a company profile: visible line, schema.org fields, absence when unasserted."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

MOD = (Path(__file__).resolve().parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"
       / "includes" / "company_lei.php")
STUBS = r"""
define('ABSPATH', '/'); define('TIT_PATH', '/nonexistent/');
function esc_html($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
"""
needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")
LEI = "549300ACMEGB0000AA01"
MAP = f"$m = array('acme corp' => '{LEI}', 'bad' => 'NOT-AN-LEI');"


def _php(expr: str) -> str:
    src = MOD.read_text().replace("<?php", "", 1)
    p = subprocess.run(["php", "-r", STUBS + src + "\n" + expr], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


@needs_php
def test_lookup_validates_the_code_and_misses_are_empty():
    assert _php(MAP + "echo tit_company_lei('acme corp', $m);") == LEI
    assert _php(MAP + "echo tit_company_lei('bad', $m);") == ""
    assert _php(MAP + "echo tit_company_lei('nobody', $m);") == ""


@needs_php
def test_missing_file_means_no_lei_not_a_fatal():
    assert _php("echo json_encode(tit_company_lei_map());") == "[]"


@needs_php
def test_visible_line_links_to_gleif_and_attributes():
    html = _php(f"echo tit_company_lei_html('{LEI}');")
    assert f'href="https://search.gleif.org/#/record/{LEI}"' in html
    assert f"LEI: <a" in html and "GLEIF Golden Copy" in html and "CC0" in html
    assert _php("echo tit_company_lei_html('');") == ""


@needs_php
def test_jsonld_fields():
    ld = json.loads(_php(f"echo json_encode(tit_company_lei_jsonld('{LEI}'));"))
    assert ld["leiCode"] == LEI
    assert ld["identifier"] == {"@type": "PropertyValue", "propertyID": "LEI", "value": LEI,
                                "url": f"https://search.gleif.org/#/record/{LEI}"}
    assert _php("echo json_encode(tit_company_lei_jsonld(''));") == "[]"
