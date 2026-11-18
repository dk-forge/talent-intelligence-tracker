"""Hub page sitemap lastmod follows the newest data run (2026-11-18).

Search Console: hub "Crawled - currently not indexed", last crawl 2026-09-06,
while page-sitemap.xml still said lastmod 2026-07-29.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SRC = (Path(__file__).parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"
       / "includes" / "sitemap_index.php").read_text()
needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")
FN = re.search(r"\nfunction tit_hub_sitemap_entry\s*\(.*?\n\}", SRC, re.S).group(0)
HUB = "https://x.test/blog/talent-intelligence-tracker/"


def _run(entry: str, newest: str) -> str:
    code = FN + "\n$r = tit_hub_sitemap_entry(%s, '%s', '%s', '2026-11-18');" \
                "echo is_array($r) ? ($r['mod'] ?? '-') : 'x';" % (entry, HUB, newest)
    p = subprocess.run(["php", "-r", code], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return p.stdout


@needs_php
def test_hub_gets_newest_run_date():
    assert _run("array('loc' => '%s', 'mod' => '2026-07-29T21:57:26+00:00')" % HUB,
                "2026-11-17 06:00:00") == "2026-11-17T00:00:00+00:00"


@needs_php
def test_future_run_is_clamped_to_today():
    assert _run("array('loc' => '%s', 'mod' => '')" % HUB, "2027-01-01") == "2026-11-18T00:00:00+00:00"


@needs_php
def test_other_pages_and_bad_dates_untouched():
    assert _run("array('loc' => 'https://x.test/blog/other/', 'mod' => 'M')", "2026-11-17") == "M"
    assert _run("array('loc' => '%s', 'mod' => 'M')" % HUB, "") == "M"


def test_registered_on_both_seo_plugins():
    assert "add_filter('rank_math/sitemap/entry', 'tit_hub_sitemap_entry_filter'" in SRC
    assert "add_filter('wpseo_sitemap_entry', 'tit_hub_sitemap_entry_filter'" in SRC
