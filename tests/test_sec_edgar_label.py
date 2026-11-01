"""Every reader-facing name for a source read off SEC EDGAR says "SEC EDGAR".

THE DEFECT (2026-11-01). One publisher reached a reader under four names:

    stored rows, every surface that cites a row    "SEC EDGAR", "SEC EDGAR (Form D)",
                                                    "SEC EDGAR (pay-versus-performance)"
    the sources page                                "SEC EDGAR 8-K (Item 5.02)",
                                                    "SEC EDGAR Form D", and
                                                    "SEC executive compensation disclosures"
    the dashboard's concentration caveat            "SEC 8-K filings", "SEC Form D filings",
                                                    "SEC executive pay filings"

So a reader who met 4,029 rows cited "SEC EDGAR (pay-versus-performance)" looked
for that source on the sources page and found no entry naming EDGAR, and the
country caveat under the US bar named "SEC 8-K filings" where every card beside
it said "SEC EDGAR". The rule now pinned: the publisher is always called
"SEC EDGAR", and what follows says which filing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import source_registry as registry
from tests.phpsource import balanced_block

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
LABEL = "SEC EDGAR"


def _sec_collectors() -> set[str]:
    return {c for c in registry.COLLECTOR_BY_SOURCE_NAME.values() if c.startswith("sec_")}


def test_there_are_sec_collectors_to_check():
    # Positive control: an empty set would make every test below a vacuous pass.
    assert {"sec_edgar", "sec_form_d", "sec_execcomp"} <= _sec_collectors()


def test_every_live_sec_source_on_the_sources_page_is_named_sec_edgar():
    for name, collector in registry.COLLECTOR_BY_SOURCE_NAME.items():
        if collector.startswith("sec_"):
            assert name.startswith(LABEL), (collector, name)
    for s in registry.SOURCES:
        if "sec.gov/edgar" in s.url:
            assert s.name.startswith(LABEL), s.name


def test_the_generated_sources_json_agrees():
    data = json.loads((PLUGIN / "data" / "sources.json").read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else data.get("sources", [])
    named = [r["name"] for r in rows if str(r.get("collector", "")).startswith("sec_")]
    assert named, "no SEC source in sources.json; this test is reading the wrong file"
    for n in named:
        assert n.startswith(LABEL), n


def test_every_sec_collector_stores_sec_edgar_as_the_source_name():
    found = {}
    for path in sorted((ROOT / "collectors").glob("sec_*.py")):
        for m in re.finditer(r'"source_name":\s*"([^"]+)"', path.read_text(encoding="utf-8")):
            found.setdefault(path.stem, []).append(m.group(1))
    assert {"sec_edgar", "sec_form_d", "sec_execcomp"} <= set(found)
    for stem, names in found.items():
        for n in names:
            assert n.startswith(LABEL), (stem, n)


def test_the_dashboard_collector_label_names_sec_edgar():
    src = (PLUGIN / "includes" / "shortcodes.php").read_text(encoding="utf-8")
    body = balanced_block(src, "function tit_collector_label($key) {")
    table = balanced_block(body, "$map = array(", what="tit_collector_label's map")
    labels = dict(re.findall(r"'([a-z_]+)'\s*=>\s*'([^']*)'", table))
    for collector in _sec_collectors():
        assert collector in labels, f"{collector} would fall back to its raw key"
        assert labels[collector].startswith(LABEL), (collector, labels[collector])
