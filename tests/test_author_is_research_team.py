"""Owner ruling 2026-11-11: the tracker's author reads "Ask The Recruiter
research team", and no personal name appears anywhere on asktherecruiter.com.
Every author in the plugin's structured data is that Organization, never a
Person."""

import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "wordpress-plugin"
AUTHOR = "Ask The Recruiter research team"


def _php():
    return [(p, p.read_text(encoding="utf-8")) for p in PLUGIN.rglob("*.php")]


def test_every_schema_author_is_the_research_team():
    found = 0
    for path, text in _php():
        for m in re.finditer(r"'author'\s*=>\s*array\(([^)]*)\)", text):
            found += 1
            assert "'Organization'" in m.group(1), path
            assert f"'{AUTHOR}'" in m.group(1), f"{path}: {m.group(0)}"
    assert found, "no author schema found; the scan is looking in the wrong place"


def test_no_person_is_ever_named_as_author():
    for path, text in _php():
        assert not re.search(r"'@type'\s*=>\s*'Person'", text), path
