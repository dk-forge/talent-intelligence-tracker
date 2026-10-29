"""Every rendered item carries its language tag (plugin 1.106.0).

pipeline/language.py stores an ISO 639-1 code per row (NULL when unsure) and
/query has returned it since 1.97.0, but nothing rendered it, so a Spanish
headline was read by screen readers in an English voice and filed by crawlers
as English. The tag is an HTML lang attribute on the headline and hreflang on
the source link. NULL renders nothing: "not sure" is never "English".

The PHP routes are exercised for real in tests/php/render_role_pages.php; this
file runs the JavaScript renderer under node and pins every PHP headline site.
"""
import json
import re
import subprocess
from pathlib import Path
from shutil import which

import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"
JS = (PLUGIN / "assets" / "dashboard.js").read_text()

# Every server-rendered item headline, by file.
PHP_HEADLINE_SITES = {
    "company.php": "<h3 class=\"tit-h\"",
    "places.php": "<h3 class=\"tit-h\"",
    "roles.php": "<h3 class=\"tit-h\"",
    "shortcodes.php": "<span class=\"tit-card-h tit-h\"",
}


def _js_fn(name):
    start = JS.index("  function " + name + "(")
    end = JS.index("\n  }\n", start) + 4
    return JS[start:end]


def _run_node(body):
    if not which("node"):
        pytest.skip("node not installed")
    script = "\n".join(_js_fn(n) for n in ("esc", "escUrl", "langCode", "langAttr", "srcAnchor")) + body
    proc = subprocess.run([which("node"), "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_js_renderer_tags_a_known_language_and_nothing_else():
    out = _run_node("""
const rows = [{language: 'es'}, {language: 'ES '}, {language: null}, {},
              {language: 'e"s'}, {language: 'spa'}, {language: ''}];
console.log(JSON.stringify(rows.map(langAttr)));
""")
    assert out == [' lang="es"', ' lang="es"', "", "", "", "", ""]


def test_the_js_source_link_carries_hreflang_only_when_known():
    out = _run_node("""
console.log(JSON.stringify([
  srcAnchor({source_url: 'https://pub.example/a', source_name: 'Pub', language: 'de'}),
  srcAnchor({source_url: 'https://pub.example/a', source_name: 'Pub', language: null})
]));
""")
    assert ' hreflang="de" ' in out[0]
    assert "hreflang" not in out[1]


def test_both_js_headline_renderers_carry_the_tag():
    assert "'<span class=\"tit-card-h tit-h\"' + langAttr(r) + '>'" in JS
    assert "'<td class=\"tit-ut-signal\"' + langAttr(r) + '>'" in JS
    # No headline is rendered anywhere in the script without it.
    total = JS.count("esc(r.headline)")
    tagged = JS.count("langAttr(r) + '>' + esc(r.headline)")
    assert total == tagged == 2, "a headline rendered without langAttr(r)"


@pytest.mark.parametrize("fname,opener", sorted(PHP_HEADLINE_SITES.items()))
def test_every_php_headline_carries_the_tag(fname, opener):
    src = (PLUGIN / "includes" / fname).read_text()
    sites = [m.start() for m in re.finditer(re.escape(opener), src)
             if "$r['headline']" in src[m.start():m.start() + 200]]
    assert sites, f"{fname}: no headline site found"
    for at in sites:
        assert src[at:at + 200].startswith(opener + "<?php echo tit_lang_attr($r); ?>>"), \
            f"{fname}: a headline is rendered without tit_lang_attr()"
    # And the row it reads must have been selected WITH the column, or the
    # attribute is silently never there.
    assert re.search(r"(SELECT|\$columns\s*=)[^;]*\blanguage\b", src, re.S), \
        f"{fname}: no SELECT feeding the headline reads language"
    assert "tit_hreflang_attr($r)" in src, f"{fname}: source link carries no hreflang"
