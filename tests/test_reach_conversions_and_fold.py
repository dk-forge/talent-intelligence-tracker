"""Reach fixes from the 2026-11-10 audit (owner-approved).

1. GA4 conversion events through the site-wide Google tag: digest signup,
   CSV/JSON download, RSS click, watchlist star, outbound source click. No
   personal data in any event: names and params come from a closed list and
   no value carries an email, a path or a querystring.
2. The dashboard leads with updates that state headcount (a number or a
   direction), and folds "Headcount Not Stated" behind a count and a button.
3. The dashboard HTML stops shipping the country and state name maps twice.

The node cases SKIP without node on PATH: UNKNOWN, not a pass.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
ANALYTICS = PLUGIN / "assets" / "analytics.js"
DASH_JS = PLUGIN / "assets" / "dashboard.js"
SHORTCODES = PLUGIN / "includes" / "shortcodes.php"
API = PLUGIN / "includes" / "api.php"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not on PATH: UNKNOWN, not a pass")

# --- 1. conversion events ---------------------------------------------------

NODE_HARNESS = r"""
var listeners = {};
global.window = global;
global.document = { addEventListener: function (t, f, c) { listeners[t] = {f: f, capture: c}; } };
var sent = [];
%(gtag)s
require(%(path)s);
function el(spec) {
  // spec: {matches: {selector: element-spec}, id, attrs, href}
  var e = { id: spec.id || '', href: spec.href || '',
            getAttribute: function (k) { return (spec.attrs || {})[k] == null ? null : spec.attrs[k]; },
            classList: { contains: function (c) { return (spec.classes || []).indexOf(c) !== -1; } } };
  e.closest = function (sel) {
    var hit = (spec.closest || {})[sel];
    return hit ? (hit === 'self' ? e : el(hit)) : null;
  };
  return e;
}
var cases = %(cases)s;
var out = cases.map(function (c) {
  return c.form ? window.TITAnalytics.formEvent(el(c.form)) : window.TITAnalytics.eventFor(el(c.target));
});
// Fire one click through the real listener to prove it reaches gtag.
listeners.click.f({ target: el(cases[0].target) });
listeners.submit.f({ target: el(cases[cases.length - 1].form) });
console.log(JSON.stringify({out: out, sent: sent, capture: [listeners.click.capture, listeners.submit.capture]}));
"""

SRC_HREF = "https://job-boards.greenhouse.io/agilysys?gh_jid=123&email=a@b.c"
CASES = [
    {"target": {"closest": {"a.tit-export-link": {"id": "tit-export-csv"}}}},
    {"target": {"closest": {"a.tit-export-link": {"id": "tit-export-json"}}}},
    {"target": {"closest": {"a.tit-export-link": {"id": "tit-export-hubspot"}}}},
    {"target": {"closest": {"a.tit-export-link": {"id": "tit-export-rss"}}}},
    {"target": {"closest": {".tit-watch-star": {"attrs": {"aria-pressed": "false"}}}}},
    {"target": {"closest": {".tit-watch-star": {"attrs": {"aria-pressed": "true"}}}}},
    {"target": {"closest": {".tit-card-src a": {"href": SRC_HREF}}}},
    {"target": {}},
    {"form": {"classes": ["alt-digest-form"], "attrs": {"data-alt-context": "talent"}}},
]


def run_node(gtag="window.gtag = function (k, n, p) { sent.push([k, n, p]); };"):
    code = NODE_HARNESS % {"gtag": gtag, "path": json.dumps(str(ANALYTICS)),
                           "cases": json.dumps(CASES)}
    proc = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@needs_node
def test_each_conversion_maps_to_one_named_event():
    out = run_node()["out"]
    names = [o and o["name"] for o in out]
    assert names == ["file_download", "file_download", "file_download", "rss_click",
                     "watchlist_star", "watchlist_star", "outbound_source_click",
                     None, "digest_signup"]
    assert out[0]["params"]["file_extension"] == "csv"
    assert out[1]["params"]["file_extension"] == "json"
    # The star's aria-pressed is read BEFORE the dashboard flips it.
    assert out[4]["params"]["action"] == "add"
    assert out[5]["params"]["action"] == "remove"
    assert out[6]["params"]["link_domain"] == "job-boards.greenhouse.io"
    assert out[8]["params"]["form_context"] == "talent"


@needs_node
def test_no_event_carries_personal_data_or_a_url():
    for o in run_node()["out"]:
        if not o:
            continue
        for k, v in o["params"].items():
            assert isinstance(v, str) and len(v) <= 64, (k, v)
            assert "@" not in v and "/" not in v and "?" not in v and "=" not in v, (k, v)
        assert not set(o["params"]) & {"email", "page_location", "link_url", "company"}


@needs_node
def test_listeners_capture_and_reach_gtag():
    res = run_node()
    assert res["capture"] == [True, True]
    assert [s[1] for s in res["sent"]] == ["file_download", "digest_signup"]
    assert all(s[0] == "event" for s in res["sent"])


@needs_node
def test_no_google_tag_is_a_silent_no_op():
    res = run_node(gtag="")
    assert res["sent"] == []


def test_analytics_script_is_enqueued_with_the_dashboard_and_loads_no_new_tag():
    php = SHORTCODES.read_text(encoding="utf-8")
    assert "assets/analytics.js" in php
    js = ANALYTICS.read_text(encoding="utf-8")
    # The site-wide tag (Site Kit) is the only tag. This file never loads one.
    assert "googletagmanager" not in js and "gtag/js" not in js
    assert "localStorage" not in js and "document.cookie" not in js


# --- 2. headcount leads, not-stated folds -----------------------------------

FOLD_HARNESS = r"""
%(fn)s
console.log(JSON.stringify([
  planFold(['hiring', 'neutral', 'displacement', 'neutral', 'comp_shift']),
  planFold(['neutral', 'neutral']),
  planFold(['hiring', 'comp_shift']),
  planFold([])
]));
"""


def js_function(src, name):
    start = src.index("function %s(" % name)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(name)


@needs_node
def test_fold_hides_only_not_stated_and_never_the_whole_list():
    fn = js_function(DASH_JS.read_text(encoding="utf-8"), "planFold")
    proc = subprocess.run([NODE, "-e", FOLD_HARNESS % {"fn": fn}],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    a, b, c, d = json.loads(proc.stdout)
    assert a == {"shown": [0, 2, 4], "folded": [1, 3]}
    # All not-stated: folding would leave an empty-looking page. Show them.
    assert b == {"shown": [0, 1], "folded": []}
    assert c == {"shown": [0, 1], "folded": []}
    assert d == {"shown": [], "folded": []}


def test_fold_control_is_a_real_toggle_with_a_count():
    js = DASH_JS.read_text(encoding="utf-8")
    body = js_function(js, "foldUnstated")
    assert "aria-expanded" in body
    assert "Headcount Not Stated" in body
    assert "afterRowsPaint" in js and "foldUnstated(" in js_function(js, "afterRowsPaint")


def test_first_paint_and_query_both_lead_with_stated_headcount():
    api = API.read_text(encoding="utf-8")
    php = SHORTCODES.read_text(encoding="utf-8")
    assert "function tit_headcount_lead_sql(" in api
    lead = re.search(r"function tit_headcount_lead_sql\(\)\s*\{(.*?)\n\}", api, re.S).group(1)
    assert "headcount" in lead and "'hiring'" in lead and "'displacement'" in lead
    # /query's default sort and the server-rendered rows use the same key.
    assert re.search(r"'notable'\s*=>\s*tit_headcount_lead_sql\(\)", api)
    assert "tit_headcount_lead_sql()" in php


# --- 3. HTML weight ---------------------------------------------------------

def test_name_maps_ship_once_not_twice():
    php = SHORTCODES.read_text(encoding="utf-8")
    loc = php[php.index("wp_localize_script('tit-dashboard'"):]
    loc = loc[:loc.index(");")]
    assert "tit_country_names" not in loc and "tit_state_names" not in loc
    # The element still carries them, and the script reads them from there.
    assert 'data-countries="' in php and 'data-states="' in php
    js = DASH_JS.read_text(encoding="utf-8")
    assert "getAttribute('data-countries')" in js
