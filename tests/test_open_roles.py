"""Open roles now (owner rulings 2026-11-12 and 2026-11-13).

The rules under test, each one a ruling:
  * a role links STRAIGHT to the employer's own posting: https, never our host;
  * only sources cleared in docs/SOURCE-TERMS-open-roles.md reach a reader;
  * a closed role (404/410 on re-check) is dropped before publishing;
  * no schema.org JobPosting markup anywhere in the plugin;
  * the company block is hidden when there are no roles or the copy is stale;
  * the daily email shows at most 10 roles, the weekly a fuller set with trends,
    and both end with the UTM-tagged "See all matches for your CV" button.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import open_roles
from collectors import ats_boards

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
MOD = PLUGIN / "includes" / "open_roles.php"
TERMS = ROOT / "docs" / "SOURCE-TERMS-open-roles.md"


def _role(i, *, company="Acme", country="US", city="Austin", family="engineering",
          url=None, posted="2026-11-10"):
    return {"company": company, "company_key": company.lower(), "ats": "greenhouse",
            "title": f"Role {i}", "url": url or f"https://boards.greenhouse.io/{company.lower()}/jobs/{i}",
            "city": city, "state": "TX" if country == "US" else "", "country": country,
            "remote": False, "family": family, "posted": posted}


# --- Collector fields --------------------------------------------------------

@pytest.mark.parametrize("ats,slug,fixture", [
    ("greenhouse", "stripe", "ats_greenhouse_stripe.json"),
    ("lever", "matchgroup", "ats_lever_matchgroup.json"),
    ("ashby", "havocai", "ats_ashby_havocai.json"),
])
def test_fetch_postings_keeps_title_and_employer_link(monkeypatch, ats, slug, fixture):
    payload = json.loads((FIX / fixture).read_text())
    monkeypatch.setattr(ats_boards, "_get", lambda url, **kw: payload)
    postings = ats_boards.fetch_postings({"ats": ats, "slug": slug})
    assert postings
    assert all(p["title"] for p in postings)
    assert all(p["url"].startswith("https://") for p in postings)
    if ats == "greenhouse":
        # The board URL, never absolute_url (which can be a third-party site).
        assert all(p["url"].startswith("https://job-boards.greenhouse.io/stripe/jobs/") for p in postings)
    roles = open_roles.roles_from_board({"ats": ats, "slug": slug},
                                        {"company": slug, "company_key": slug}, postings)
    assert roles and all(open_roles.link_ok(r["url"]) for r in roles)
    # The counts are untouched by the extra fields.
    assert ats_boards.snapshot(postings)["total"] == len(postings)


def test_only_cleared_sources_reach_a_reader():
    posting = {"title": "Engineer", "url": "https://apply.workable.com/x/j/1", "place": "city:London"}
    assert open_roles.roles_from_board({"ats": "workable", "slug": "x"}, {}, [posting]) == []
    assert open_roles.roles_from_board({"ats": "smartrecruiters", "slug": "x"}, {}, [posting]) == []


def test_every_cleared_source_has_a_terms_record():
    text = TERMS.read_text().lower()
    for ats in open_roles.ALLOWED_ATS:
        assert ats in text, f"{ats} is shown to readers with no terms check recorded"
    assert "jooble" in text and "off" in text


@pytest.mark.parametrize("url,ok", [
    ("https://boards.greenhouse.io/acme/jobs/1", True),
    ("https://jobs.lever.co/acme/abc", True),
    ("http://jobs.lever.co/acme/abc", False),
    ("https://asktherecruiter.com/jobs/1", False),
    ("https://www.asktherecruiter.com/blog/x", False),
    ("", False),
    ("javascript:alert(1)", False),
])
def test_link_ok(url, ok):
    assert open_roles.link_ok(url) is ok


def test_place_reads_us_state_from_city_or_text():
    city, state, country, _ = open_roles.place_of({"place": "city:San Francisco"})
    assert (city, country) == ("San Francisco", "US") and state == "CA"
    _, state, country, _ = open_roles.place_of({"place": "country:US",
                                                "location_text": "Columbus, OH"})
    assert (state, country) == ("OH", "US")


# --- Selection, re-check and payload ---------------------------------------

def test_email_pool_caps_any_one_employer():
    roles = [_role(i, company="Big") for i in range(30)] + [_role(i, company="Small") for i in range(3)]
    pool = open_roles.email_pool(roles)
    # Each cell admits two per employer, so Big cannot crowd Small out.
    assert {r["company"] for r in pool} == {"Big", "Small"}
    assert sum(1 for r in pool if r["company"] == "Big") <= 2 * 4


def test_company_block_is_five_newest():
    roles = [_role(i, posted=f"2026-11-{i + 1:02d}") for i in range(9)]
    block = open_roles.company_blocks(roles)["acme"]
    assert block["count"] == 9 and len(block["roles"]) == 5
    assert block["roles"][0]["title"] == "Role 8"


def test_a_closed_link_is_dropped_before_publishing():
    roles = [_role(1), _role(2)]
    snap = {"checked_on": "2026-11-13", "as_of": "2026-11-13T22:40:00Z", "roles": roles}
    links = {roles[0]["url"]: 404, roles[1]["url"]: None}   # None: could not tell, kept
    payload = open_roles.build(snap, {}, links=links)
    assert payload["totals"]["roles"] == 1
    assert payload["link_check"] == {"checked": 1, "unchecked": 1, "dropped": 1}
    assert [r["title"] for r in payload["pool"]] == ["Role 2"]


def test_check_links_reports_each_url():
    seen = open_roles.check_links(["https://a.example/1", "https://a.example/2"],
                                  status=lambda u, t: 410 if u.endswith("1") else 200)
    assert seen == {"https://a.example/1": 410, "https://a.example/2": 200}


def test_trend_is_counts_only_and_bounded():
    store = {}
    for d in range(1, 10):
        open_roles.record_trend(store, [_role(1)], f"2026-11-{d:02d}")
    day = store["open_roles_trend"]["2026-11-09"]
    assert day["total"] == 1 and "roles" not in day and "pool" not in day
    assert open_roles.trend_vs(store, "2026-11-09", 7)["date"] == "2026-11-02"
    assert open_roles.trend_vs({}, "2026-11-09", 7) is None


def test_role_file_is_never_committed():
    assert "data/open_roles_today.json" in (ROOT / ".gitignore").read_text()


# --- Plugin -------------------------------------------------------------------

def test_no_jobposting_markup_in_the_plugin():
    import re
    markup = re.compile(r"['\"]JobPosting['\"]|schema\.org/JobPosting|itemtype=[^>]*JobPosting")
    for f in list(PLUGIN.rglob("*.php")) + list(PLUGIN.rglob("*.js")):
        assert not markup.search(f.read_text(errors="ignore")), f
    # The guard bites: the markup it exists to stop is caught.
    assert markup.search("'@type' => 'JobPosting'")


needs_php = pytest.mark.skipif(not shutil.which("php"), reason="UNKNOWN, NOT RUN: php not installed")

STUBS = r"""
define('ABSPATH', '/');
$GLOBALS['opts'] = array();
function get_option($k) { return $GLOBALS['opts'][$k] ?? false; }
function update_option($k, $v, $a = null) { $GLOBALS['opts'][$k] = $v; return true; }
function esc_html($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s) { return esc_html($s); }
function esc_url($s) { return esc_html($s); }
function number_format_i18n($n) { return number_format($n); }
function wp_parse_url($u, $c = -1) { return parse_url($u, $c); }
function add_query_arg($a, $u) { return $u . '?' . http_build_query($a); }
function add_action() {}
function sanitize_text_field($s) { return (string) $s; }
function tit_country_name($c) { return array('US' => 'United States', 'GB' => 'United Kingdom')[$c] ?? $c; }
"""


def _php(setup: dict, expr: str) -> str:
    src = MOD.read_text().replace("<?php", "", 1)
    data = "$GLOBALS['opts']['tit_open_roles'] = json_decode(" + json.dumps(json.dumps(setup)) + ", true);\n"
    p = subprocess.run(["php", "-r", STUBS + src + "\n" + data + expr],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


def _payload(**over):
    roles = [_role(i, posted=f"2026-11-{i + 1:02d}") for i in range(12)]
    roles += [_role(i, company=f"Co{i}", country="GB", city="London", family="sales") for i in range(12)]
    snap = {"checked_on": "2026-11-13", "as_of": "2026-11-13T22:40:00Z", "roles": roles}
    store = {"open_roles_trend": {"2026-11-06": {"total": 20, "employers": 10,
                                                 "country": {"US": 8, "GB": 12},
                                                 "state": {}, "family": {"engineering": 8, "sales": 12}}}}
    payload = open_roles.build(snap, store)
    payload.update(over)
    return payload


NOW = "strtotime('2026-11-14T06:00:00Z')"


@needs_php
def test_company_block_lists_up_to_five_outbound_links():
    html = _php(_payload(), f"echo tit_open_roles_company_block('acme', {NOW});")
    assert "Open roles now: 12" in html
    assert html.count("<li>") == 5
    assert "Last checked 13 Nov 2026" in html
    assert 'href="https://boards.greenhouse.io/acme/jobs/' in html
    assert "asktherecruiter.com" not in html and "JobPosting" not in html


@needs_php
def test_company_block_hides_when_none_or_stale():
    assert _php(_payload(), f"echo tit_open_roles_company_block('nobody', {NOW});") == ""
    stale = "strtotime('2026-11-15T06:00:00Z')"   # 31h after as_of
    assert _php(_payload(), f"echo tit_open_roles_company_block('acme', {stale});") == ""


@needs_php
def test_daily_email_section():
    out = json.loads(_php(_payload(), f"echo json_encode(tit_open_roles_email_section('daily', array('now' => {NOW})));"))
    html = out["html"]
    assert html.count("<h2") == 1 and ">Open roles now</h2>" in html
    assert html.count("<li ") <= 10
    assert "By country" in html and "By role family" in html
    assert "utm_source=talent-tracker" in html and "utm_campaign=open-roles-daily" in html
    assert "See all matches for your CV" in html and "#start-intake" in html
    assert "—" not in html + out["text"]   # no em-dashes (owner style)


@needs_php
def test_email_section_follows_reader_choice():
    out = json.loads(_php(_payload(), "echo json_encode(tit_open_roles_email_section('daily', "
                                      f"array('country' => 'GB', 'family' => 'sales', 'now' => {NOW})));"))
    assert out["count"] == 12
    assert "Austin" not in out["html"] and "London" in out["html"]


@needs_php
def test_weekly_email_has_trend_and_fuller_roundup():
    out = json.loads(_php(_payload(), f"echo json_encode(tit_open_roles_email_section('weekly', array('now' => {NOW})));"))
    assert "This week:" in out["html"] and "on last week" in out["html"]
    assert "utm_campaign=open-roles-weekly" in out["html"]
    assert out["html"].count("<li ") > 10


@needs_php
def test_role_links_are_never_wrapped_in_tracking():
    expr = (f"echo json_encode(tit_open_roles_email_section('daily', array('now' => {NOW}, "
            "'link' => function ($u) { return 'https://asktherecruiter.com/blog/x?u=' . urlencode($u); })));")
    html = json.loads(_php(_payload(), expr))["html"]
    assert 'href="https://boards.greenhouse.io/acme/jobs/' in html
    # Only the CV button goes through the composer's tracker.
    assert html.count("asktherecruiter.com/blog/x?u=") == 1


@needs_php
def test_choices_list_current_countries_and_families():
    out = json.loads(_php(_payload(), f"echo json_encode(tit_open_roles_choices({NOW}));"))
    assert out["countries"] == {"GB": "United Kingdom", "US": "United States"}
    assert out["families"] == {"engineering": "Engineering", "sales": "Sales"}
    stale = "strtotime('2026-11-16T00:00:00Z')"
    assert json.loads(_php(_payload(), f"echo json_encode(tit_open_roles_choices({stale}));")) == {"countries": [], "families": []}


@needs_php
def test_email_section_is_null_when_stale():
    stale = "strtotime('2026-11-16T00:00:00Z')"
    assert _php(_payload(), f"var_export(tit_open_roles_email_section('daily', array('now' => {stale})));") == "NULL"


@needs_php
def test_ingest_refuses_a_link_to_our_own_site():
    bad = _payload()
    bad["pool"][0]["url"] = "https://asktherecruiter.com/jobs/1"
    expr = ("class WP_Error { public $c; function __construct($c, $m, $d) { $this->c = $c; } }\n"
            "class R { public $b; function __construct($b) { $this->b = $b; } function get_json_params() { return $this->b; } }\n"
            "function rest_ensure_response($x) { return $x; }\n"
            "$r = tit_api_open_roles(new R($GLOBALS['opts']['tit_open_roles']));\n"
            "echo $r instanceof WP_Error ? $r->c : 'stored';")
    # tit_api_open_roles type-hints WP_REST_Request; alias the stub to it.
    expr = expr.replace("class R ", "class WP_REST_Request ").replace("new R(", "new WP_REST_Request(")
    assert _php(bad, expr) == "tit_open_roles_link"
    assert _php(_payload(), expr) == "stored"
