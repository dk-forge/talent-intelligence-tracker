"""todo#2731: a click on a talent_tracker sign-up CTA sends resume_prompt_click.

Same event name as the layoff tracker so the GA4 funnel (visit -> CTA click ->
match check -> sign-up) has one CTA-click step. Only the campaign slug is sent.
The node cases SKIP without node on PATH: UNKNOWN, not a pass.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ANALYTICS = (Path(__file__).resolve().parents[1] / "wordpress-plugin"
             / "talent-intelligence-tracker" / "assets" / "analytics.js")
NODE = shutil.which("node")

HARNESS = r"""
global.window = global;
global.document = { addEventListener: function () {} };
require(%s);
function link(href) {
  var a = { href: href, id: '' };
  return { closest: function (sel) {
    return sel === 'a[href*="utm_source=talent_tracker"]' && href.indexOf('utm_source=talent_tracker') !== -1 ? a : null;
  } };
}
console.log(JSON.stringify(%s.map(function (h) { return window.TITAnalytics.eventFor(link(h)); })));
"""


@pytest.mark.skipif(NODE is None, reason="node not on PATH: UNKNOWN, not a pass")
def test_cta_click_sends_campaign_only():
    hrefs = [
        "https://asktherecruiter.com/resume-score?utm_source=talent_tracker&utm_medium=cta&utm_campaign=role-page",
        "https://asktherecruiter.com/?utm_source=talent_tracker&utm_campaign=company-is-hiring#start-intake",
        "https://asktherecruiter.com/?utm_source=talent_tracker&utm_campaign=bad%40x.com",
        "https://example.com/?utm_source=other&utm_campaign=x",
    ]
    out = subprocess.run([NODE, "-e", HARNESS % (json.dumps(str(ANALYTICS)), json.dumps(hrefs))],
                         capture_output=True, text=True, check=True).stdout
    got = json.loads(out)
    assert got[0] == {"name": "resume_prompt_click", "params": {"campaign": "role-page"}}
    assert got[1] == {"name": "resume_prompt_click", "params": {"campaign": "company-is-hiring"}}
    assert got[2] == {"name": "resume_prompt_click", "params": {"campaign": "unknown"}}
    assert got[3] is None
