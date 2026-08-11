"""The RSS feed and the CRM export presets, validated from OUTSIDE PHP.

The PHP harness (tests/php/feed_and_crm.php) already asserts the feed against
DOMDocument; this module re-parses the SAME output with Python's strict
ElementTree parser and email.utils' RFC 822 date reader, so the document has
to satisfy two independent parsers before it ships. A feed only one lenient
parser accepts is a feed half the readers cannot read.

Where php is not installed these tests read the SOURCE instead of running it,
so the suite stays green offline while CI (which has php) runs the harness
proper.
"""

from __future__ import annotations

import csv
import email.utils
import io
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
FEED = (PLUGIN / "includes" / "feed.php").read_text()
CRM = (PLUGIN / "includes" / "export_crm.php").read_text()
JS = (PLUGIN / "assets" / "dashboard.js").read_text()
SHORTCODES = (PLUGIN / "includes" / "shortcodes.php").read_text()
HARNESS = ROOT / "tests" / "php" / "feed_and_crm.php"

PHP = shutil.which("php")


def _run(*args: str) -> str:
    out = subprocess.run(
        [PHP, str(HARNESS), *args], capture_output=True, text=False, cwd=ROOT
    )
    assert out.returncode == 0, out.stderr.decode(errors="replace")
    return out.stdout.decode("utf-8")


# --- the feed document, under a second strict parser -----------------------

@pytest.mark.skipif(PHP is None, reason="php not installed; CI runs the harness")
def test_feed_survives_a_strict_xml_parse_and_rfc822_dates():
    xml = _run("--dump-xml")
    root = ET.fromstring(xml)  # raises on anything malformed
    assert root.tag == "rss" and root.get("version") == "2.0"
    channel = root.find("channel")
    assert channel is not None
    for need in ("title", "link", "description"):
        el = channel.find(need)
        assert el is not None and (el.text or "").strip(), need
    self_link = channel.find("{http://www.w3.org/2005/Atom}link")
    assert self_link is not None and self_link.get("rel") == "self"

    items = channel.findall("item")
    assert len(items) == 3
    for item in items:
        assert (item.findtext("title") or "").strip()
        assert (item.findtext("link") or "").startswith("https://")
        guid = item.find("guid")
        assert guid is not None and guid.get("isPermaLink") == "false"
        when = email.utils.parsedate_to_datetime(item.findtext("pubDate"))
        assert when.utcoffset().total_seconds() == 0
        assert (item.findtext("category") or "").strip()


@pytest.mark.skipif(PHP is None, reason="php not installed; CI runs the harness")
def test_crm_presets_round_trip_through_a_python_csv_reader():
    for preset, first, domain_col in (
        ("hubspot", "Company name", "Company domain name"),
        ("salesforce", "Account Name", "Website"),
    ):
        raw = _run(f"--dump-csv={preset}")
        assert raw.startswith("﻿"), "UTF-8 BOM for Excel"
        rows = list(csv.reader(io.StringIO(raw.lstrip("﻿"))))
        head, data = rows[0], rows[1:]
        assert head[0] == first
        assert domain_col in head
        assert len(data) == 3
        di = head.index(domain_col)
        for row in data:
            assert len(row) == len(head)
            assert row[di] == "", "no invented websites"


# --- source-level guarantees (no php needed) --------------------------------

def test_feed_route_reuses_querys_own_where_and_cache():
    assert "tit_build_where" in FEED, "the feed must share /query's WHERE builder"
    assert "tit_cache_key" in FEED, "same transient discipline as /query"
    assert "TIT_CACHE_TTL" in FEED
    assert 'isPermaLink="false"' in FEED
    assert "rest_pre_serve_request" in FEED


def test_feed_has_a_rate_cap_that_spares_cached_hits():
    assert "tit_feed_over_cap" in FEED
    # The cap must be checked only on a cache MISS: the miss branch is the one
    # that queries, and cached polls must never 429.
    miss = FEED[FEED.index("if ($xml === false)"):FEED.index("set_transient($cache_key")]
    assert "tit_feed_over_cap" in miss


def test_crm_headers_cite_both_vendors_docs():
    assert "knowledge.hubspot.com" in CRM
    assert "help.salesforce.com" in CRM
    for header in ("Company name", "Company domain name", "Country/Region",
                   "Account Name", "Website", "Billing Country"):
        assert f"'{header}'" in CRM, header


def test_crm_exports_reuse_the_export_walker_not_their_own_query():
    assert "tit_export_walk" in CRM
    assert "tit_csv_guard" in CRM
    assert "tit_export_throttle" in CRM
    assert "$wpdb" not in CRM, "presets must not query on their own"


def test_the_harness_exists_and_ci_runs_it():
    assert HARNESS.exists()
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
    assert "php tests/php/feed_and_crm.php" in workflow, (
        "the harness exists but nothing runs it, which is worse than not having it"
    )


def test_export_links_carry_the_active_filters_for_all_four_plus_rss():
    """One updater rewrites every download link, RSS included; a preset that
    ignored the filters would hand over a different set than the page shows."""
    for id_ in ("tit-export-csv", "tit-export-json",
                "tit-export-hubspot", "tit-export-salesforce", "tit-export-rss"):
        assert id_ in JS, id_
        assert id_ in SHORTCODES, id_
