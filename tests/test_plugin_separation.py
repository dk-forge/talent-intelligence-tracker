"""The two plugins must never touch.

The AI Layoff Tracker is live on the same WordPress install. WordPress fatals
an entire plugin on one bad require, so any shared file, table or route means a
single mistake takes down a running product.

These tests make the separation a build failure rather than a code review note.
"""

import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"
PHP_FILES = sorted(PLUGIN.rglob("*.php"))
DEPLOY = Path(__file__).parent.parent / ".github" / "workflows" / "deploy-plugin.yml"


def test_the_plugin_exists():
    assert PHP_FILES, "no PHP files found"


@pytest.mark.parametrize("path", PHP_FILES, ids=lambda p: p.name)
def test_no_sibling_function_prefix(path):
    """alt_ belongs to the layoff tracker. Two plugins defining the same
    function name is a fatal redeclare."""
    hits = re.findall(r"\bfunction\s+(alt_\w+)", path.read_text())
    assert not hits, f"{path.name} defines sibling-prefixed functions: {hits}"


@pytest.mark.parametrize("path", PHP_FILES, ids=lambda p: p.name)
def test_never_references_the_sibling_table(path):
    text = path.read_text()
    assert "alt_layoffs" not in text, f"{path.name} references the sibling's table"


@pytest.mark.parametrize("path", PHP_FILES, ids=lambda p: p.name)
def test_never_registers_in_the_sibling_namespace(path):
    text = path.read_text()
    assert "layoffs/v1" not in text or "ai-layoff-tracker" in text, (
        f"{path.name} touches the sibling REST namespace"
    )


@pytest.mark.parametrize("path", PHP_FILES, ids=lambda p: p.name)
def test_every_file_blocks_direct_access(path):
    assert "if (!defined('ABSPATH')) exit;" in path.read_text(), (
        f"{path.name} is directly loadable"
    )


def test_requires_are_guarded_against_a_partial_upload():
    """FTP lands files one at a time. A hard require of a file that has not
    arrived yet fatals the plugin on every request until the upload finishes."""
    bootstrap = (PLUGIN / "talent-intelligence-tracker.php").read_text()
    assert "is_readable" in bootstrap
    for include in ("includes/db.php", "includes/api.php", "includes/shortcodes.php"):
        assert f"tit_require('{include}')" in bootstrap
        assert f"require_once {include}" not in bootstrap


def test_version_header_matches_the_constant():
    """FTP bypasses activation hooks, so the version bump is the only trigger
    for migrations and the cache flush."""
    text = (PLUGIN / "talent-intelligence-tracker.php").read_text()
    header = re.search(r"^ \* Version:\s*([\d.]+)", text, re.M).group(1)
    const = re.search(r"define\('TIT_VERSION',\s*'([\d.]+)'\)", text).group(1)
    assert header == const


def test_the_api_key_check_fails_closed():
    """An empty stored key must never match an empty header."""
    text = (PLUGIN / "talent-intelligence-tracker.php").read_text()
    assert "hash_equals" in text
    assert "503" in text, "no key configured must reject, not allow"


def test_deploy_guard_blocks_the_sibling_directory():
    yml = DEPLOY.read_text()
    assert "*ai-layoff-tracker*" in yml, "deploy does not refuse the sibling's directory"
    assert "*/talent-intelligence-tracker" in yml, "deploy does not pin its own directory"
    assert "wp-content/plugins" in yml


def test_deploy_is_dormant_until_armed():
    """It must not fire on push before a human has read the guard output."""
    yml = DEPLOY.read_text()
    active_push = re.search(r"^on:\n(?:\s*#.*\n)*\s{2}push:", yml, re.M)
    assert active_push is None, "deploy-plugin.yml is armed; that should be deliberate"


def test_assets_are_versioned_by_content_not_just_by_constant():
    """Autoptimize serves a rewritten copy keyed on whatever version string we
    hand it, and an FTP deploy can ship a CSS-only fix without moving the
    constant. Same string, same stale copy, and the deploy looks like it never
    landed. The mtime is what makes the bust real.
    """
    from pathlib import Path

    php = (Path(__file__).parent.parent / "wordpress-plugin"
           / "talent-intelligence-tracker" / "includes" / "shortcodes.php").read_text()
    assert "tit_asset_version('assets/dashboard.css')" in php
    assert "tit_asset_version('assets/dashboard.js')" in php

    boot = (Path(__file__).parent.parent / "wordpress-plugin"
            / "talent-intelligence-tracker" / "talent-intelligence-tracker.php").read_text()
    assert "function tit_asset_version" in boot
    assert "filemtime" in boot
    # An FTP deploy has a window where the file is not on disk yet, and an
    # unguarded filemtime() warns into the response body.
    assert "is_readable($file)" in boot


def test_the_stylesheet_the_page_asks_for_actually_exists():
    from pathlib import Path

    css = (Path(__file__).parent.parent / "wordpress-plugin"
           / "talent-intelligence-tracker" / "assets" / "dashboard.css")
    assert css.is_file() and css.stat().st_size > 5000
    body = css.read_text()
    # Keep this list in step with what the page actually renders. The old
    # period-tile strip (.tit-glance-cell) was replaced by the signal-by-period
    # matrix on 2026-07-28, so this guard was failing on a class nothing emits
    # any more. The point is that the stylesheet really covers the live markup,
    # not that a particular historical class survives forever.
    for selector in (".tit-hero", ".tit-matrix", ".tit-cell", ".tit-region", ".tit-table"):
        assert selector in body, selector
