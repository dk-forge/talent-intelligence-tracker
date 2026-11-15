"""The update log: /talent-intelligence-tracker/update-log/.

Generated from git by generate_update_log.py, rendered by includes/update_log.php.
The standing rule is that a release cannot ship without its line, which is what
test_the_newest_entry_is_the_shipped_version pins: bump TIT_VERSION and this goes
red until `python3 generate_update_log.py --pending "..."` has been run.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
LOG = PLUGIN / "data" / "update-log.json"
PHP = (PLUGIN / "includes" / "update_log.php").read_text(encoding="utf-8")
BOOT = (PLUGIN / "talent-intelligence-tracker.php").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT))
import generate_update_log as gen  # noqa: E402


def _entries():
    return json.loads(LOG.read_text(encoding="utf-8"))["entries"]


def _key(v):
    return tuple(int(p) for p in v.split("."))


def test_the_newest_entry_is_the_shipped_version():
    shipped = gen.parse_version(BOOT)
    assert shipped, "TIT_VERSION not found"
    assert _entries()[0]["version"] == shipped, (
        "TIT_VERSION moved without an update-log entry: run "
        "`python3 generate_update_log.py --pending '<what changed>'`")
    assert f"Version: {shipped}" in BOOT


def test_entries_are_well_formed_unique_and_newest_first():
    entries = _entries()
    assert len(entries) >= 100, "the log shrank; was it regenerated from a shallow clone?"
    versions = [e["version"] for e in entries]
    assert len(versions) == len(set(versions))
    assert versions == sorted(versions, key=_key, reverse=True)
    for e in entries:
        assert re.fullmatch(r"\d+\.\d+\.\d+", e["version"])
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["date"])
        assert e["tag"] in ("Change", "Fix")
        assert e["text"].strip() and e["text"].endswith((".", "?", "!"))
        assert not re.match(r"^(plugin\s|v\d|\d+\.\d+\.\d+)", e["text"], re.I), e


def test_clean_subject_strips_release_prefixes_and_keeps_the_pr_number():
    tag, text, pr = gen.clean_subject("plugin 1.99.1: keep the owner's item first (#249)")
    assert (tag, pr) == ("Change", 249)
    assert text == "Keep the owner's item first."
    tag, text, pr = gen.clean_subject("vNEXT — fix(security): js/xss-through-dom (#286)")
    assert (tag, pr) == ("Fix", 286) and text == "Js/xss-through-dom."
    tag, text, _ = gen.clean_subject("feat(design): modern company profile page (1.112.0) (#295)")
    assert text == "Modern company profile page."


def test_one_entry_per_version_the_earliest_commit_wins():
    rows = [  # git log order: newest first
        {"version": "1.2.0", "date": "2026-02-02", "tag": "Change", "text": "Later touch."},
        {"version": "1.2.0", "date": "2026-02-01", "tag": "Change", "text": "The release."},
        {"version": "1.10.0", "date": "2026-03-01", "tag": "Change", "text": "Ten."},
    ]
    built = gen.build(rows)
    assert [e["version"] for e in built] == ["1.10.0", "1.2.0"], "10 sorts after 2, not before"
    assert built[1]["text"] == "The release."


def test_the_page_is_routed_loaded_and_linked_beside_corrections():
    for fragment in ("add_rewrite_rule", "query_vars", "template_redirect", "pre_get_document_title"):
        assert fragment in PHP, fragment
    assert "TIT_UPDATE_LOG_PATH = 'talent-intelligence-tracker/update-log'" in PHP
    assert "tit_require('includes/update_log.php');" in BOOT
    shortcodes = (PLUGIN / "includes" / "shortcodes.php").read_text(encoding="utf-8")
    page = (PLUGIN / "includes" / "page.php").read_text(encoding="utf-8")
    for url in ("/talent-intelligence-tracker/update-log/", "/talent-intelligence-tracker/methodology/"):
        assert shortcodes.count(url) >= 2, f"{url} not linked from both the ribbon and the footer"
    assert "tit_update_log" in page and "tit_methodology" in page


def test_the_page_reads_a_file_and_never_git():
    assert "data/update-log.json" in PHP
    assert "git " not in PHP.replace("a repository", "").lower().split("<?php", 1)[1].split("*/", 1)[1]
