"""Discovery credit has to travel inside the downloaded file.

The sources page names GDELT and links it, which is an adequate credit for the
page. It is not a credit for a CSV somebody downloaded and mailed on: that file
leaves the site, and until now every row in it said only "asktherecruiter.com".
GDELT asks to be cited when data found through it is redistributed, and the
GDELT-discovered share of the table is about to grow by an order of magnitude
through the 2026 backfill.

The rows already carry `collector`, so the fix is a lookup and these tests are
what stop it rotting: a new collector must make a deliberate decision about its
upstream credit rather than defaulting to silence.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
EXPORT = PLUGIN / "includes" / "export.php"
SOURCES = PLUGIN / "includes" / "sources.php"
LICENCE = ROOT / "LICENSE-DATA"

# Collectors that read a publisher directly — a government service, an
# employer's own applicant-tracking board. There is no index in between to
# credit, so their absence from the export map is correct, not an oversight.
# A new collector belongs here or in the map, and this file fails until it does.
NO_UPSTREAM = {"uk_paygap", "ats_boards"}


def php_map(text, func):
    """The collector => name array inside a named PHP function."""
    body = re.search(r"function " + func + r"\([^)]*\) \{(.*?)\n\}", text, re.S).group(1)
    return dict(re.findall(r"'([a-z_ ]+)'\s*=>\s*'([^']+)'", body))


def export_map():
    return php_map(EXPORT.read_text(), "tit_export_upstream")


def test_every_collector_decides_its_upstream_credit():
    """Adding a collector without answering "who led us to this row?" is the
    silent failure: the rows land, the export credits only us, and nothing
    anywhere says otherwise."""
    declared = {re.search(r'^COLLECTOR = "([^"]+)"', p.read_text(), re.M).group(1)
                for p in (ROOT / "collectors").glob("*.py")
                if re.search(r'^COLLECTOR = ', p.read_text(), re.M)}
    undecided = declared - set(export_map()) - NO_UPSTREAM
    assert not undecided, f"no upstream credit decision for: {sorted(undecided)}"


def test_gdelt_rows_name_gdelt():
    assert export_map()["gdelt"] == "GDELT"


def test_every_sec_collector_credits_edgar():
    m = export_map()
    for key in [k for k in m if k.startswith("sec_")]:
        assert m[key] == "SEC EDGAR", key


def test_the_csv_attribution_is_computed_per_row_not_a_constant():
    """It was one hardcoded string for every row, which is exactly the state
    this guards against returning to."""
    text = EXPORT.read_text()
    assert "tit_export_attribution($row->collector" in text
    body = re.search(r"function tit_export_csv\(\) \{(.*?)\n\}", text, re.S).group(1)
    assert "'Talent Intelligence Tracker - asktherecruiter.com - CC BY 4.0'," not in body


def test_the_json_download_carries_the_credit_too():
    """Header for the reader, per record for the machine. A JSON export that
    only credits us in the header cannot be attributed row by row."""
    text = EXPORT.read_text()
    assert '"upstream_sources":' in text
    assert "https://www.gdeltproject.org/" in text
    assert "'discovered_via'" in text


def test_the_export_and_the_sources_page_know_the_same_collectors():
    """Two maps keyed by collector, in two files. A collector added to one and
    forgotten in the other shows up as a source with no last-run row, or as an
    export with no credit."""
    page = set(php_map(SOURCES.read_text(), "tit_sources_health_map"))
    assert page <= set(export_map()), sorted(page - set(export_map()))


def test_the_data_licence_names_the_upstream_indexes():
    """The licence file is what a redistributor actually reads. Crediting only
    ourselves in it while shipping GDELT-discovered rows is the gap."""
    text = LICENCE.read_text()
    assert "## Upstream sources" in text
    assert "https://www.gdeltproject.org/" in text
    assert "https://www.sec.gov/edgar" in text
    assert "discovered_via" in text, "the licence must say where the credit is"
