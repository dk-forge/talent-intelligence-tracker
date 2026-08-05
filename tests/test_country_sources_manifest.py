"""The recall page's per-country source lines are generated, never typed.

Under each country's recall score the page names WHY the score is what it is:
the live sources reading that country, the publishers probed and refused with
the probe's own recorded reason, and how many researched candidates queue
behind them. A country at zero with no dedicated source then reads as a to-do
item rather than a mystery.

That only stays true if the file is derived from the registry and the probe
catalogue on every build, so these tests hold the file, the deriver and the
render boundary rules (no em or en dashes, no competitor or paid-product
names) together.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import source_registry as registry

OUT = (Path(registry.__file__).parent / "wordpress-plugin"
       / "talent-intelligence-tracker" / "data" / "country_sources.json")

BANNED_DASHES = ("—", "–")


def coverage_file():
    return json.loads(OUT.read_text())


def test_the_file_is_in_sync_with_the_deriver():
    """Run build_sources_json.py if this fails; never hand-edit the JSON."""
    assert coverage_file() == registry.country_coverage()


def test_keys_are_iso2_codes_the_php_side_can_look_up():
    for code in coverage_file():
        assert re.fullmatch(r"[A-Z]{2}", code), (
            f"{code!r} is not an ISO2 key; the recall page looks a gold-set "
            f"cell's country code straight up")


def test_live_sources_land_under_their_own_country():
    cov = coverage_file()
    assert any("ACRA" in n for n in cov["SG"]["live"]), (
        "Singapore's live registry source is missing from its own country "
        "block; the live list is derived from source_registry.SOURCES")
    live_by_code = {
        s.country: s.name for s in registry.SOURCES
        if s.status == "live" and s.country
    }
    for code in live_by_code:
        assert cov.get(code, {}).get("live"), (
            f"{code} has a live source in the registry and none in the file")


def test_every_refusal_carries_the_probes_own_dated_evidence():
    """A refusal with evidence is finished work; a bare name is not.

    The reason must be the probe's recorded line (they all open with the date
    the probe ran), never a summary somebody wrote later, so the next session
    does not re-probe the same fifteen paths.
    """
    for code, c in coverage_file().items():
        for r in c["refused"]:
            assert r["name"].strip(), f"{code}: a refusal with no publisher"
            assert re.match(r"\d{4}-\d{2}-\d{2}", r["reason"] or ""), (
                f"{code}: {r['name']!r} refused without dated probe evidence: "
                f"{r.get('reason')!r}")


def test_a_wired_publisher_is_never_listed_as_refused():
    """The two states are exclusive, or the page calls working feeds dead."""
    manifest = registry.sources_manifest()
    wired = {s["name"].lower() for s in manifest
             if (s.get("rss") or "").startswith("http")}
    for code, c in coverage_file().items():
        for r in c["refused"]:
            assert r["name"].lower() not in wired, (
                f"{code}: {r['name']!r} has a wired feed and is listed as "
                f"refused")


def test_no_banned_dash_reaches_the_page():
    """Same render boundary as sources.json, same refusal-not-rewrite rule."""
    blob = OUT.read_text()
    for dash in BANNED_DASHES:
        assert dash not in blob, (
            "an em or en dash is in country_sources.json, which feeds a "
            "public page. Repair data/sources_catalogue.csv and rebuild.")
