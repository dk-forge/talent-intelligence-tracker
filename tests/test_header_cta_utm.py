"""todo#1881 item 2: one UTM scheme on the hiring tracker, header CTA included."""
import re
import subprocess
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "wordpress-plugin" / "talent-intelligence-tracker"
SRC_FILES = list((PLUGIN / "includes").glob("*.php")) + list((PLUGIN / "assets").glob("*.js"))


def test_every_cta_uses_one_source():
    sources = set()
    for f in SRC_FILES:
        if f.name == "header_cta_utm.php":  # names "blog" only as the source it replaces
            continue
        for m in re.finditer(r"utm_source(?:'\s*=>\s*'|=)([a-z_\-]+)", f.read_text()):
            sources.add(m.group(1))

    assert sources == {"talent_tracker"}, sources


def test_no_tracker_cta_points_at_quick_draft_or_the_homepage():
    for f in SRC_FILES:
        t = f.read_text()
        assert "asktherecruiter.com/quick-draft" not in t, f.name
        assert "asktherecruiter.com/?utm_" not in t, f.name


def test_header_cta_is_retagged_on_tracker_pages():
    php = f'''
define("ABSPATH", 1); function add_filter() {{}}
require "{PLUGIN / 'includes' / 'header_cta_utm.php'}";
echo tit_header_cta_retag('<a href="https://asktherecruiter.com/resume-score?utm_source=blog&#038;utm_medium=nav&#038;utm_campaign=header-cta">'), "\\n";
echo tit_header_cta_retag('<a href="https://example.com/?utm_source=blog&amp;utm_medium=nav">');
'''
    out = subprocess.run(["php", "-r", php], capture_output=True, text=True, check=True).stdout.splitlines()
    assert "utm_source=talent_tracker&#038;utm_medium=nav&#038;utm_campaign=header-cta" in out[0]
    assert "utm_source=blog" in out[1]  # not ours: untouched


def test_header_cta_filter_only_runs_on_our_pages():
    src = (PLUGIN / "includes" / "header_cta_utm.php").read_text()
    assert "if (!function_exists('tit_is_our_page') || !tit_is_our_page()) return $block_content;" in src
    main = (PLUGIN / "talent-intelligence-tracker.php").read_text()
    assert "tit_require('includes/header_cta_utm.php');" in main


def test_template_parts_render_before_wp_head():
    main = (PLUGIN / "talent-intelligence-tracker.php").read_text()
    i = main.index("function tit_render_header()")
    body = main[i:main.index("\n}\n", i)]
    assert body.index("block_template_part('header')") < body.index("wp_head();")
    assert body.index("block_template_part('footer')") < body.index("wp_head();")
    assert "echo $tit_header_html;" in body
    assert "echo $GLOBALS['tit_prerendered_footer'];" in main
