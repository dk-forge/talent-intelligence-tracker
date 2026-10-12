"""Company pages: open roles in the description, and both tracker sitemaps
listed in the site's sitemap index (owner-approved 2026-10-12, item 3).

Measured live 2026-10-12: /blog/sitemap_index.xml (the SEO plugin's index, the
one /blog/robots.txt names) lists the sibling's sitemaps and NEITHER of ours,
so the 1,371 company URLs and 103 place URLs were reachable by a crawler only
by guessing. The page-level furniture (title, description, canonical, one
robots tag, JSON-LD) was already correct and is pinned by test_company_page.py
and test_place_pages.py.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN = Path(__file__).parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"
COMPANY = (PLUGIN / "includes" / "company.php").read_text()
PLACES = (PLUGIN / "includes" / "places.php").read_text()
PANEL = (PLUGIN / "includes" / "board_series.php").read_text()


def _fn(src: str, name: str) -> str:
    return re.search(rf"function {name}\(.*?\n}}", src, re.S).group(0)


def test_the_company_description_carries_current_open_roles():
    head = _fn(COMPANY, "tit_company_head")
    assert "function_exists('tit_board_series_open_roles')" in head
    assert "open roles on its job board" in head


def test_the_open_roles_helper_counts_current_boards_only():
    fn = _fn(PANEL, "tit_board_series_open_roles")
    assert "array_key_exists('current', $board)" in fn
    assert "return null" in fn, "unknown is omitted, never a zero"


INDEX = (PLUGIN / "includes" / "sitemap_index.php").read_text()
BOOTSTRAP = (PLUGIN / "talent-intelligence-tracker.php").read_text()


def test_both_sitemaps_are_in_the_seo_plugins_index():
    assert "tit_require('includes/sitemap_index.php');" in BOOTSTRAP
    assert "add_filter('wpseo_sitemap_index', 'tit_sitemap_index_entries');" in INDEX
    assert "add_filter('rank_math/sitemap/index', 'tit_sitemap_index_entries');" in INDEX
    body = _fn(INDEX, "tit_sitemap_index_entries")
    assert "tit_company_sitemap_url()" in body
    assert "tit_places_sitemap_url()" in body
    assert "<sitemap><loc>" in body and "esc_url(" in body
    # Appends, never replaces what the SEO plugin built, and never twice.
    assert "return $xml . $add;" in body
    assert "strpos((string) $xml, $url) !== false" in body


def test_the_plugin_hooks_stay_out_of_the_page_files():
    """company.php's robots fix must not name an SEO plugin; the index file is
    the one deliberate exception."""
    for src in (COMPANY, PLACES):
        assert "wpseo_" not in src and "rank_math" not in src
