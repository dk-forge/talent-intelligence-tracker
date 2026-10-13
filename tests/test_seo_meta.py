"""SEO audit 2026-10-12, TIT's share (items 2, 3, 5, 10).

- #3: ALL-CAPS legal names are title-cased with the legal suffix dropped in
  titles, and company titles are at most 60 characters. (Thin pages are
  already noindex and out of the sitemap: the gate needs >= 3 documents.)
- #10: generated meta descriptions on company and place pages <= 155 chars.
- #5: news items carry datePublished + dateModified; the page node is
  published by the Organization "Ask The Recruiter".
- #2: the places directory's lastmod is its newest child, not "today".
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
MOD = PLUGIN / "includes" / "seo_meta.php"
COMPANY = PLUGIN / "includes" / "company.php"
PLACES = PLUGIN / "includes" / "places.php"

STUBS = "define('ABSPATH', '/');\n"
needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")


def _fn(path: Path, name: str) -> str:
    m = re.search(r"\nfunction " + name + r"\s*\(.*?\n\}", path.read_text(), re.S)
    assert m, name + " is missing"
    return m.group(0)


def _php(expr: str, extra: str = "") -> str:
    src = MOD.read_text().replace("<?php", "", 1)
    p = subprocess.run(["php", "-r", STUBS + src + extra + "\n" + expr],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


@needs_php
@pytest.mark.parametrize("raw,want", [
    ("2 SISTERS FOOD GROUP LIMITED", "2 Sisters Food Group"),
    ("ACME HOLDINGS PLC", "Acme Holdings"),
    ("IBM UK LTD", "IBM UK"),
    ("BANK OF THE WEST, INC.", "Bank of the West"),
    ("eBay", "eBay"),
    ("Stripe, Inc.", "Stripe"),
    ("OpenAI", "OpenAI"),
])
def test_display_name(raw, want):
    assert _php("echo tit_company_display_name(%s);" % json.dumps(raw)) == want


@needs_php
def test_company_title_capped_at_60():
    for name, n in (("2 SISTERS FOOD GROUP LIMITED", 14),
                    ("THE VERY LONG NAME OF A REGISTERED INTERNATIONAL HOLDING COMPANY GROUP LIMITED", 3),
                    ("Acme", 1)):
        t = _php("echo tit_company_seo_title(%s, %d);" % (json.dumps(name), n))
        assert len(t) <= 60, t
        assert "LIMITED" not in t
    assert _php("echo tit_company_seo_title('Acme', 1);") == \
        "Acme: 1 tracked update on hiring, funding and leadership"


def test_title_filter_uses_capped_title():
    assert "tit_company_seo_title(" in _fn(COMPANY, "tit_company_title")


@needs_php
def test_description_cap():
    long = "x " * 200
    out = _php("echo tit_seo_cap_description(%s);" % json.dumps(long))
    assert len(out) <= 155 and out.endswith("…")
    assert _php("echo tit_seo_cap_description('Short.');") == "Short."


def test_head_descriptions_capped():
    for path, fn in ((COMPANY, "tit_company_head"), (PLACES, "tit_place_head")):
        body = _fn(path, fn)
        assert "tit_seo_cap_description(" in body
        assert "> 300" not in body


@needs_php
def test_company_jsonld_dates_and_publisher():
    extra = _fn(COMPANY, "tit_company_jsonld")
    rows = [{"headline": "A", "published_date": "2026-09-01", "source_url": "https://n.example/a"},
            {"headline": "B", "published_date": "", "captured_at": "2026-10-02 10:00:00",
             "source_url": "https://n.example/b"}]
    out = json.loads(_php("echo json_encode(tit_company_jsonld('ACME LIMITED', 'https://e.org/c/acme/', "
                          "json_decode(%s, true)));" % json.dumps(json.dumps(rows)), extra))
    org, page = out["@graph"]
    assert [i["datePublished"] for i in org["subjectOf"]] == ["2026-09-01", "2026-10-02"]
    assert all(i["dateModified"] == i["datePublished"] for i in org["subjectOf"])
    assert all("publisher" not in i for i in org["subjectOf"])
    assert page["publisher"]["@type"] == "Organization"
    assert page["publisher"]["name"] == "Ask The Recruiter"
    assert page["dateModified"] == "2026-10-02"


@needs_php
def test_place_page_dates():
    out = json.loads(_php("echo json_encode(tit_place_page_dates(array(array('published_date'=>'2026-01-02'),"
                          "array('published_date'=>'2026-03-04'))));", _fn(PLACES, "tit_place_page_dates")))
    assert out == {"datePublished": "2026-01-02", "dateModified": "2026-03-04"}
    assert "tit_publisher_org()" in PLACES.read_text()


def test_places_hub_lastmod_is_newest_child():
    body = _fn(PLACES, "tit_places_sitemap_entries")
    assert "$entries[0]['lastmod'] = $newest" in body
