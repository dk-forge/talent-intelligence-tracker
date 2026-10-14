"""Intro + H2 + 3-question FAQ on the tracker page and every static route, and
FAQPage JSON-LD built from exactly that visible text (ai-layoff-tracker#446
items 2 and 3, ported).

Owner ruling 2026-10-14: Claude writes the wording. Pinned here:
  * figures and the year come from the render-time facts, never typed;
  * a figure that cannot be read (0 / empty) drops its sentence;
  * the JSON-LD strings are byte-identical to the visible ones and sit inside
    the same <section>, so the markup only exists where the FAQ is shown;
  * no em or en dash; an update is never called a job opening;
  * every page links Data Sources (except Sources itself) and Measured Recall
    (except Recall itself), this product's method pages.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
MOD = PLUGIN / "includes" / "page_faq.php"

STUBS = r"""
function esc_html($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s){ return (string)$s; }
function wp_json_encode($v, $f = 0){ return json_encode($v, $f); }
"""

U = {"dashboard": "https://e.org/blog/talent-intelligence-tracker/",
     "sources": "https://e.org/blog/talent-intelligence-tracker/sources/",
     "recall": "https://e.org/blog/talent-intelligence-tracker/recall/",
     "corrections": "https://e.org/blog/talent-intelligence-tracker/corrections/",
     "places": "https://e.org/blog/talent-intelligence-tracker/places/",
     "roles": "https://e.org/blog/talent-intelligence-tracker/roles/",
     "contact": "https://e.org/blog/contact/"}
FULL = {"y": 2031, "upd_y": 4321, "emp_y": 876, "ver_y": 1234, "all_n": 98765,
        "all_c": 5432, "countries": 47, "top_country": "Germany",
        "top_industry": "Software", "cadence": "three times a day"}
EMPTY = {"y": 2031, "upd_y": 0, "emp_y": 0, "ver_y": 0, "all_n": 0, "all_c": 0,
         "countries": 0, "top_country": "", "top_industry": "", "cadence": ""}
PAGES = ["dashboard", "sources", "press", "recall", "corrections", "places", "roles"]

needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")


def _php(expr: str, *args) -> str:
    src = MOD.read_text(encoding="utf-8")
    funcs = re.findall(r"\nfunction tit_page_faq_[a-z_]+\s*\(.*?\n\}", src, re.S)
    p = subprocess.run(["php", "-r", STUBS + "\n".join(funcs) + "\n" + expr, "--",
                        *[json.dumps(a) for a in args]],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


def content(key, facts):
    return json.loads(_php(
        "echo json_encode(tit_page_faq_content(json_decode($argv[1]), json_decode($argv[2], true), json_decode($argv[3], true)));",
        key, facts, U))


def text(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s))


@needs_php
@pytest.mark.parametrize("key", PAGES)
def test_intro_h2_three_questions(key):
    c = content(key, FULL)
    assert c["h2"].strip()
    small = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "vs", "with"}
    for i, w in enumerate(c["h2"].split()):
        bare = re.sub(r"^\W+|\W+$", "", w)
        assert (i > 0 and bare.lower() in small) or bare[:1].isupper(), "H2s are Title Case here: " + c["h2"]
    assert len(c["faq"]) == 3
    n = len(re.findall(r"[.!?](?:\s|$)", text(c["intro"])))
    assert 2 <= n <= 3, c["intro"]


@needs_php
def test_unknown_key_gets_nothing():
    assert content("embed", FULL) is None


@needs_php
def test_figures_and_year_come_from_facts():
    blob = " ".join(json.dumps(content(k, FULL)) for k in PAGES)
    for v in ("2031", "4,321", "876", "1,234", "98,765", "Germany", "Software", "three times a day"):
        assert v in blob, v
    src = re.sub(r"(?m)^\s*(\*|//).*$", "", MOD.read_text(encoding="utf-8"))
    assert not re.search(r"\b20[12]\d\b", src), "no year typed into the copy"


@needs_php
@pytest.mark.parametrize("key", PAGES)
def test_unreadable_figure_drops_its_sentence(key):
    c = content(key, EMPTY)
    blob = text(json.dumps(c))
    assert not re.search(r"(?<![\d,.])0 (updates|employers|countries|of them)", blob)
    assert "  " not in c["intro"] and c["intro"].strip()
    for q, a in c["faq"]:
        assert a.strip(), q
    assert " has the most" not in blob.replace("Germany has", "")


@needs_php
@pytest.mark.parametrize("key", PAGES)
def test_copy_rules(key):
    c = content(key, FULL)
    blob = text(json.dumps(c, ensure_ascii=False))
    assert "—" not in blob and "–" not in blob
    assert not re.search(r"(?i)\b(revolutionary|unprecedented|booming|skyrocket\w*)\b", blob)
    for s in re.split(r"(?<=[.!?])\s+", text(c["intro"]) + " " + " ".join(text(a) for _, a in c["faq"])):
        assert len(s.split()) <= 30, s
        if re.search(r"(?i)job (openings|ads|vacancies)|vacanc", s):
            assert re.search(r"(?i)\b(not|no)\b", s), "an update is never a job opening: " + s
    raw = json.dumps(c)
    if key != "sources":
        assert U["sources"] in raw
    if key != "recall":
        assert U["recall"] in raw


@needs_php
@pytest.mark.parametrize("key", PAGES)
def test_jsonld_is_the_visible_faq(key):
    c = content(key, FULL)
    out = _php("$c = tit_page_faq_content(json_decode($argv[1]), json_decode($argv[2], true), json_decode($argv[3], true));"
               "echo tit_page_faq_intro_html($c) . '|||' . tit_page_faq_section_html($c);", key, FULL, U)
    intro, section = out.split("|||")
    assert intro.startswith('<p class="tit-page-intro">')
    assert re.match(r'<section class="tit-page-faq"[^>]*><h2>', section)
    ld = re.findall(r'<script type="application/ld\+json">(.*?)</script>', section, re.S)
    assert len(ld) == 1
    node = json.loads(ld[0])
    assert node["@type"] == "FAQPage"
    assert [(e["name"], e["acceptedAnswer"]["text"]) for e in node["mainEntity"]] == [tuple(x) for x in c["faq"]]
    for q, a in c["faq"]:
        assert html.escape(q, quote=True) in section
        assert a in section


@needs_php
def test_empty_content_renders_nothing():
    assert _php("echo tit_page_faq_intro_html(null) . tit_page_faq_section_html(null);") == ""


@needs_php
def test_dashboard_intro_lands_inside_tit_wrap():
    # Every other route echoes tit_page_faq_intro() INSIDE its own <div
    # class="tit-wrap">; the dashboard is composed afterwards by
    # tit_page_faq_wrap_html(), which must put the intro in the same place.
    # dashboard.css only themes `.tit-wrap p` for dark mode (the site's own
    # `.entry-content p { color:#2a2a2a !important }` wins otherwise), so an
    # intro sitting ahead of the wrapper renders unreadable in dark mode even
    # though every other page's intro is fine.
    out = _php(
        "$html = '<div class=\"tit-wrap\" id=\"tit-dashboard\"><p>inner</p></div>';"
        "echo tit_page_faq_wrap_html($html, '<p class=\"tit-page-intro\">hi</p>', '');"
    )
    wrap_at = out.index('<div class="tit-wrap"')
    intro_at = out.index('<p class="tit-page-intro">')
    assert intro_at > wrap_at, "intro must be inside .tit-wrap, not ahead of it: " + out


def test_wired_into_every_route_and_the_dashboard():
    main = (PLUGIN / "talent-intelligence-tracker.php").read_text(encoding="utf-8")
    assert "includes/page_faq.php" in main
    for f, key in (("sources.php", "sources"), ("press.php", "press"), ("recall.php", "recall"),
                   ("corrections.php", "corrections"), ("places.php", "places"), ("roles.php", "roles")):
        src = (PLUGIN / "includes" / f).read_text(encoding="utf-8")
        assert f"tit_page_faq_intro('{key}')" in src, f
        assert f"tit_page_faq_section('{key}')" in src, f
    sc = (PLUGIN / "includes" / "shortcodes.php").read_text(encoding="utf-8")
    assert "tit_page_faq_wrap_dashboard" in sc
