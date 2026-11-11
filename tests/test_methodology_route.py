"""/talent-intelligence-tracker/methodology/ must land on THIS tracker's method page.

Found live 2026-11-11: the path had no route, so WordPress's 404 guesser
301-redirected it to the sibling's /ai-layoff-tracker/methodology/. A journalist
typing the obvious URL read the other product's method. This product's method
page is /sources/ ("Where this data comes from"), so the path redirects there.
"""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "wordpress-plugin" / "talent-intelligence-tracker"
       / "includes" / "sources.php").read_text(encoding="utf-8")


def test_methodology_path_has_its_own_rewrite():
    assert "TIT_METHODOLOGY_PATH = 'talent-intelligence-tracker/methodology'" in SRC
    assert "add_rewrite_rule('^' . TIT_METHODOLOGY_PATH . '/?$', 'index.php?tit_methodology=1', 'top')" in SRC
    assert "$vars[] = 'tit_methodology';" in SRC


def test_methodology_redirects_permanently_to_sources():
    body = SRC[SRC.index("function tit_methodology_template("):]
    body = body[:body.index("\n}\n")]
    assert "wp_safe_redirect(tit_sources_url(), 301)" in body
    assert "exit;" in body
    assert "add_action('template_redirect', 'tit_methodology_template'" in SRC
