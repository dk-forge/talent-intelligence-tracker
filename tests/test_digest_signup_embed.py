"""The dashboard prints the SHARED email-digest signup, safely.

The subscriber store, the double-opt-in flow and the sender live in the sibling
plugin (AI Layoff Tracker, includes/subscribe.php): both plugins run on one
WordPress install, and one person gets ONE consent record, not two. This side
only renders the form, through `alt_digest_subscribe_form()`.

What is pinned here is the ISOLATION promise from the top of
talent-intelligence-tracker.php: no shared code, no require across plugins. The
embed is a `function_exists()`-guarded CALL, so a missing sibling (deactivated,
or an FTP deploy caught mid-upload) renders nothing and fatals nothing.

TWO THINGS THIS FILE DOES DELIBERATELY.

First, every textual assertion runs on the PHP with its comments STRIPPED. The
block around this embed is fifty words of prose naming the function it calls,
so a test that greps the raw file cannot tell the call apart from the paragraph
explaining it. Delete the call, leave the comment, and the naive version of
this test still passes.

Second, the missing-dependency path is EXECUTED rather than described. The
guarded block is lifted out of shortcodes.php and run under the real `php`
binary twice: once with the sibling absent, where it must exit clean and print
NOTHING, and once with a stub defined, where it must print what the stub
returned. A guard nobody ran is a guard nobody has checked.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
SHORTCODES = PLUGIN / "includes" / "shortcodes.php"

#: The one function this plugin borrows from the sibling.
RENDERER = "alt_digest_subscribe_form"


def strip_comments(src: str) -> str:
    """Prose about the call is not the call.

    `/* ... */` (which is what the embed's own explanation uses) and `//` line
    comments. Deliberately not string-aware: no string literal involved in
    these assertions contains a comment opener, and a stripper that silently
    kept one would be the defect it exists to catch.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("//")
    )


@pytest.fixture(scope="module")
def php_src() -> str:
    return SHORTCODES.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code(php_src: str) -> str:
    return strip_comments(php_src)


# --------------------------------------------------------------------------
# it is a call, and it is on the dashboard
# --------------------------------------------------------------------------

def test_the_dashboard_calls_the_shared_signup_renderer(code: str):
    """A CALL, not a mention: `echo alt_digest_subscribe_form(...)`."""
    assert re.search(rf"echo\s+{RENDERER}\s*\(", code), (
        "the talent dashboard must render the shared digest signup by calling "
        f"{RENDERER}(); no call to it survives comment stripping")


def test_the_call_is_inside_the_dashboard_renderer(code: str):
    """On the DASHBOARD specifically, not merely somewhere in the file: this
    file also builds company pages and place pages."""
    start = code.index("function tit_dashboard_html()")
    nxt = code.find("\nfunction ", start + 1)
    body = code[start:nxt if nxt != -1 else len(code)]
    assert re.search(rf"echo\s+{RENDERER}\s*\(", body), (
        f"{RENDERER}() is called in shortcodes.php but not from "
        "tit_dashboard_html(), so it does not appear on the dashboard")


def test_it_sits_near_the_foot_and_not_above_the_data(code: str):
    """A signup form that pushes the numbers below the fold is a worse page.
    It belongs after the results and the trust panel, before the citation."""
    body = code[code.index("function tit_dashboard_html()"):]
    call = body.index(RENDERER)
    assert body.index("tit_trust_panel_html") < call, (
        "the signup form renders before the trust panel, which puts it above "
        "the page's own content")
    assert call < body.index("tit-cite"), (
        "the signup form renders after the citation footer")


def test_the_context_argument_says_which_page_it_is(code: str):
    """The shared renderer takes a context string. Passing it is how the
    sibling tells the two pages apart without either side forking the copy."""
    assert re.search(rf"echo\s+{RENDERER}\s*\(\s*'talent'\s*\)", code)


# --------------------------------------------------------------------------
# the isolation promise
# --------------------------------------------------------------------------

def test_every_call_is_function_exists_guarded(code: str):
    """A bare call fatals the whole plugin whenever the sibling is absent or an
    FTP deploy is caught mid-upload."""
    for m in re.finditer(rf"{RENDERER}\s*\(", code):
        before = code[max(0, m.start() - 300):m.start()]
        if f"function_exists('{RENDERER}')" in before:
            continue
        if before.rstrip().endswith("function_exists("):
            continue  # the guard's own condition names the function too
        pytest.fail(f"{RENDERER} is called without a function_exists() guard, "
                    f"near: {code[max(0, m.start() - 120):m.start() + 60]!r}")


def test_no_cross_plugin_require(code: str):
    """Rendering the sibling's form must never become LOADING the sibling's
    code. One WordPress install loads both plugins, or neither."""
    assert not re.search(r"(require|include)[^\n]*ai-layoff-tracker", code), (
        "no require/include may cross the plugin boundary")


def test_the_talent_plugin_defines_no_subscriber_store_of_its_own():
    """One subscriber table, one consent record per person. If this side grew
    its own store or endpoints, a reader would be on two lists and a single
    unsubscribe would only half work."""
    forbidden = ("alt_subscribers_table", "alt_digest_signup",
                 "alt_digest_confirm", "alt_digest_unsubscribe",
                 "alt_digest_send")
    for path in sorted(PLUGIN.rglob("*.php")):
        body = strip_comments(path.read_text(encoding="utf-8"))
        for name in forbidden:
            assert f"function {name}" not in body, (
                f"{path.relative_to(ROOT)} defines {name}(); the store, the "
                "consent flow and the sender belong to the sibling plugin only")


def test_no_em_or_en_dashes_in_the_embed_block(php_src: str):
    idx = php_src.index(RENDERER)
    block = php_src[max(0, idx - 900):idx + 300]
    for ch in ("—", "–"):
        assert ch not in block


# --------------------------------------------------------------------------
# the missing-dependency path, EXECUTED
# --------------------------------------------------------------------------

def _guarded_block(src: str) -> str:
    """The `if (function_exists(...)) { ... }` statement, lifted verbatim.

    Brace-matched rather than regexed to the next `}`, so a nested block inside
    the guard could not silently truncate what actually gets executed.
    """
    start = src.index(f"if (function_exists('{RENDERER}'))")
    open_at = src.index("{", start)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("the function_exists guard has unbalanced braces")


def _run_php(body: str, tmp_path: Path) -> subprocess.CompletedProcess:
    script = tmp_path / "probe.php"
    script.write_text("<?php\n" + body + "\n", encoding="utf-8")
    return subprocess.run(
        [shutil.which("php"), "-d", "error_reporting=E_ALL",
         "-d", "display_errors=1", str(script)],
        capture_output=True, text=True, timeout=60)


php_required = pytest.mark.skipif(
    shutil.which("php") is None,
    reason="the php CLI is needed to EXECUTE the degrade path")


@php_required
def test_the_page_degrades_silently_when_the_sibling_is_absent(
        php_src: str, tmp_path: Path):
    """THE missing-dependency path. Sibling deactivated, or an FTP deploy
    caught between two files: the block must print NOTHING and fatal on
    nothing."""
    result = _run_php(_guarded_block(php_src), tmp_path)
    assert result.returncode == 0, (
        f"the embed fatals when the sibling plugin is absent:\n"
        f"{result.stdout}\n{result.stderr}")
    assert result.stdout.strip() == "", (
        f"the embed printed something with no sibling to render it, so a "
        f"broken or empty form reaches the reader: {result.stdout!r}")
    noise = (result.stdout + result.stderr).lower()
    for bad in ("fatal error", "call to undefined function", "warning:",
                "deprecated:"):
        assert bad not in noise, f"{bad!r} in: {result.stdout}{result.stderr}"


@php_required
def test_the_form_is_printed_when_the_sibling_is_present(
        php_src: str, tmp_path: Path):
    """The other half, without which the test above would pass on a page that
    renders the form under no circumstances whatsoever."""
    stub = (f"function {RENDERER}($context = '') {{\n"
            f"    return '<section class=\"alt-digest\">' . $context "
            f". '</section>';\n"
            f"}}\n")
    result = _run_php(stub + _guarded_block(php_src), tmp_path)
    assert result.returncode == 0, result.stderr
    assert '<section class="alt-digest">talent</section>' in result.stdout, (
        f"the guard did not print the renderer's output: {result.stdout!r}")


@php_required
def test_the_guard_is_what_makes_it_safe_and_not_luck(tmp_path: Path):
    """Guard the guard. If removing `function_exists` did NOT fail, then the
    silent-degrade test above proves nothing about the guard."""
    result = _run_php(f"echo {RENDERER}('talent');", tmp_path)
    noise = (result.stdout + result.stderr).lower()
    assert result.returncode != 0 or "undefined function" in noise, (
        "an UNGUARDED call to the absent renderer did not fail, so this php "
        "build is not enforcing the thing the guard protects against and the "
        "degrade test cannot be trusted")
