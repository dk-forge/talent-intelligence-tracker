"""Every card says when this tracker first listed the row (card contract 1.1.0).

The card's own date (card-when) is the date the SOURCE published. A reporter
citing a row also needs when WE first carried it. That is captured_at: written
once on INSERT (tit_store_signal) and never on update, until now visible only
in the CSV export. Both renderers show it as "First listed <date>" in a
<time class="tit-card-listed">, from its UTC date.

When captured_at is missing, empty or not a date the card says nothing about
it: never a guess, never the source date standing in. Both renderers are
EXECUTED here (node for dashboard.js, php for shortcodes.php), not grepped.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
DASHBOARD_JS = PLUGIN / "assets" / "dashboard.js"
SHORTCODES_PHP = PLUGIN / "includes" / "shortcodes.php"
NODE = shutil.which("node")
PHP = shutil.which("php")

CASES = [
    ("2026-11-03 14:05:00", "2026-11-03", "3 Nov 2026"),
    ("2026-01-09T23:59:59Z", "2026-01-09", "9 Jan 2026"),
]
EMPTY = [None, "", "garbage", "0000-00-00 00:00:00"]


def _js_fn(src, name):
    start = src.index("function %s(" % name)
    end = src.index("\n  }\n", start) + 4
    return src[start:end]


def _listed_js(captured_at, published_date=None):
    if not NODE:
        pytest.skip("node is not installed")
    src = DASHBOARD_JS.read_text(encoding="utf-8")
    script = (
        "var MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];\n"
        "function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')"
        ".replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
        + _js_fn(src, "listedCell")
        + "\nconsole.log(JSON.stringify(listedCell(%s)));\n"
        % json.dumps({"captured_at": captured_at, "published_date": published_date})
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _listed_php(captured_at):
    if not PHP:
        pytest.skip("php is not installed")
    src = SHORTCODES_PHP.read_text(encoding="utf-8")
    m = re.search(r"\nfunction tit_card_listed_date\(.*?\n}\n", src, re.S)
    assert m, "shortcodes.php defines no tit_card_listed_date()"
    code = m.group(0) + "\necho json_encode(tit_card_listed_date(%s));" % (
        "null" if captured_at is None else var_export(captured_at)
    )
    out = subprocess.run([PHP, "-r", code], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def var_export(s):
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


@pytest.mark.parametrize("captured_at,iso,label", CASES)
def test_js_card_says_first_listed(captured_at, iso, label):
    assert _listed_js(captured_at) == (
        '<time class="tit-card-listed" datetime="%s">First listed %s</time>' % (iso, label)
    )


@pytest.mark.parametrize("captured_at", EMPTY)
def test_js_card_says_nothing_without_a_first_listed_time(captured_at):
    assert _listed_js(captured_at, published_date="2026-01-02") == ""


@pytest.mark.parametrize("captured_at,iso,label", CASES)
def test_php_reads_the_utc_date(captured_at, iso, label):
    assert _listed_php(captured_at) == iso


@pytest.mark.parametrize("captured_at", EMPTY)
def test_php_says_nothing_without_a_first_listed_time(captured_at):
    assert _listed_php(captured_at) == ""


def test_php_card_prints_first_listed_only_when_there_is_a_date():
    src = SHORTCODES_PHP.read_text(encoding="utf-8")
    body = src[src.index("function tit_card_html($r) {"):]
    body = body[: body.index("\n    return ob_get_clean();")]
    assert "tit_card_listed_date($r['captured_at'] ?? '')" in body
    assert re.search(r"if \(\$listed !== ''\)", body)
    assert 'First listed %s' in body


def test_first_paint_selects_captured_at():
    """A column missing from the server's row SELECT renders a card that grows
    the line only on the first repaint, which is the drift the contract stops."""
    src = SHORTCODES_PHP.read_text(encoding="utf-8")
    m = re.search(r'"SELECT signal_id, headline, talent_readthrough.*?FROM', src, re.S)
    assert m and "captured_at" in m.group(0)
