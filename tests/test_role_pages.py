""""Who's hiring" role pages from the `functions` field (owner-approved SEO, 2026-10-13).

The behaviour (gate, 301/302/404, sitemap, CTA) runs against real SQL in
tests/php/render_role_pages.php; this file runs that harness and asserts the
properties that are textual.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
ROLES = PLUGIN / "includes" / "roles.php"


@pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")
def test_the_role_harness_passes():
    p = subprocess.run(["php", str(ROOT / "tests" / "php" / "render_role_pages.php")],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr + p.stdout


def test_loaded_after_places_and_routes_are_verified():
    boot = (PLUGIN / "talent-intelligence-tracker.php").read_text()
    assert boot.index("tit_require('includes/roles.php')") > boot.index("tit_require('includes/places.php')")
    assert "'tit_roles_rewrites'" in boot


def test_the_gate_is_the_place_gate_not_a_copy():
    src = ROLES.read_text()
    assert "tit_place_meets_threshold(" in src
    assert "const TIT_ROLE_MIN" not in src


def test_the_sitemap_is_in_the_index():
    idx = (PLUGIN / "includes" / "sitemap_index.php").read_text()
    assert "tit_roles_sitemap_url()" in idx


def test_role_vocabulary_matches_the_api():
    src = ROLES.read_text()
    assert "tit_allowed_functions()" in src


def test_the_harness_runs_in_ci():
    wf = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
    assert "php tests/php/render_role_pages.php" in wf


def test_no_em_dashes_in_role_page_copy():
    import re
    src = re.sub(r"/\*.*?\*/", "", ROLES.read_text(), flags=re.S)
    src = re.sub(r"^[ \t]*//.*$", "", src, flags=re.M)
    assert "—" not in src
