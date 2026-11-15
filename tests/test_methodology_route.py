"""/talent-intelligence-tracker/methodology/ is THIS tracker's own method page.

History. Found live 2026-11-11: the path had no route, so WordPress's 404 guesser
301-redirected it to the sibling's /ai-layoff-tracker/methodology/. The first
fix redirected it to /sources/. That was the right stopgap and the wrong
destination (a list of sources is not an account of method), and the 2026-11-15
press-readiness audit asked for a real page. So the route now RENDERS, and the
two things that must never come back are asserted: the redirect, and any hand-off
to the sibling's page.
"""
import re
from pathlib import Path

PLUGIN = (Path(__file__).resolve().parents[1] / "wordpress-plugin" / "talent-intelligence-tracker")
METHOD = (PLUGIN / "includes" / "methodology.php").read_text(encoding="utf-8")
SOURCES = (PLUGIN / "includes" / "sources.php").read_text(encoding="utf-8")
BOOT = (PLUGIN / "talent-intelligence-tracker.php").read_text(encoding="utf-8")
# The prose is written as concatenated PHP string literals; join them back up so
# a phrase is matched as a reader sees it, not as the source wraps it.
SECTIONS_SRC = METHOD[METHOD.index("function tit_methodology_sections("):METHOD.index("function tit_methodology_para_html(")]
FLAT = " ".join(re.sub(r"'\s*\.\s*'", "", SECTIONS_SRC).split())


def _body(src, fn):
    start = src.index(f"function {fn}(")
    return src[start:src.index("\n}\n", start)]


def test_the_path_has_its_own_rewrite_and_query_var():
    assert "TIT_METHODOLOGY_PATH = 'talent-intelligence-tracker/methodology'" in METHOD
    assert "add_rewrite_rule('^' . TIT_METHODOLOGY_PATH . '/?$', 'index.php?tit_methodology=1', 'top')" in METHOD
    assert "$vars[] = 'tit_methodology';" in METHOD


def test_it_renders_a_page_and_does_not_redirect_anywhere():
    body = _body(METHOD, "tit_methodology_template")
    assert "tit_methodology_render()" in body and "exit;" in body
    assert "wp_safe_redirect" not in METHOD and "wp_redirect" not in METHOD
    assert "ai-layoff-tracker/methodology" not in METHOD


def test_the_old_redirect_is_gone_and_the_route_is_registered_once():
    assert "tit_methodology_template" not in SOURCES
    assert "TIT_METHODOLOGY_PATH" not in SOURCES
    assert "tit_require('includes/methodology.php');" in BOOT


def test_the_page_covers_what_the_audit_asked_for():
    ids = re.findall(r"array\('([a-z-]+)', '[^']+', array\(", METHOD)
    for needed in ("counts", "announced-vs-measured", "sources", "checking",
                   "dedupe", "recall", "limits"):
        assert needed in ids, needed


def test_the_three_labels_and_the_job_board_distinction_are_stated():
    for phrase in ("Verified:", "Reported:", "Measured:",
                   "never counted as an announced hiring event"):
        assert phrase in FLAT, phrase


def test_the_prose_carries_no_hard_coded_figures():
    """A number typed into method copy is the first thing to go stale. The only
    digits allowed are rules the code itself fixes: the 14-day same-event window
    (pipeline/dedupe.SAME_EVENT_DAYS), the 95% interval and the CC BY 4.0 licence."""
    literals = re.findall(r"'((?:[^'\\]|\\.)*)'", re.sub(r"'\s*\.\s*'", "", SECTIONS_SRC))
    prose = " ".join(l for l in literals if " " in l)
    digits = set(re.findall(r"\d+(?:\.\d+)?%?", prose))
    assert digits <= {"14", "95%", "4.0"}, digits
    assert "14 days" in prose, "the dedupe window the copy states must stay stated"


def test_the_stated_dedupe_window_is_the_code_s_window():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pipeline import dedupe
    assert dedupe.SAME_EVENT_DAYS == 14, "update the methodology page with the window"
