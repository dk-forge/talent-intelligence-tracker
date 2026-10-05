"""DOL OFLC H-1B/LCA reduction: offline, against a synthetic file in the real
header layout (tests/fixtures/h1b_lca). The fixture deliberately carries the
personal-data columns the real file has (contact/attorney names, emails,
phones, case numbers) so the test can prove none of them survives."""
import gzip
import json
from pathlib import Path

import pytest

import h1b_lca
import reference_freshness
import reference_store

FIX = Path(__file__).parent / "fixtures" / "h1b_lca"
XLSX = FIX / "LCA_Disclosure_Data_FY2026_Q3_sample.xlsx"


def _table():
    return h1b_lca.reduce(h1b_lca.iter_rows(XLSX))


def test_discover_picks_the_newest_lca_file_not_perm():
    d = h1b_lca.discover((FIX / "performance_page_snippet.html").read_text())
    assert d["file"] == "LCA_Disclosure_Data_FY2026_Q3.xlsx"
    assert d["url"].startswith("https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs/")
    assert d["as_of"] == "2026-06-30"


def test_quarter_end_follows_the_federal_fiscal_year():
    assert h1b_lca.quarter_end(2026, 1) == "2025-12-31"
    assert h1b_lca.quarter_end(2026, 4) == "2026-09-30"


def test_reader_returns_only_the_aggregate_columns():
    rows = list(h1b_lca.iter_rows(XLSX))
    assert len(rows) == 7
    for r in rows:
        assert set(r) <= set(h1b_lca.READ_COLUMNS)


def test_reduce_counts_h1b_only_and_groups_by_employer_worksite_family():
    table, stats = _table()
    assert stats["rows_read"] == 7 and stats["h1b_cases"] == 6   # the E-3 row is out
    assert stats["employers"] == 2
    acme = [r for r in table if r["employer_key"] == "acme widgets"
            and r["worksite_county"] == "SANGAMON" and r["soc_major"] == "15"][0]
    assert acme["cases"] == 3 and acme["certified"] == 2 and acme["positions"] == 3
    assert acme["job_family"] == "Computer and Mathematical"
    assert acme["naics"] == "541511" and acme["worksite_state"] == "IL"
    # 120,000/yr and 60/h * 2080 = 124,800 -> quartiles over the two certified wages
    assert acme["wage_n"] == 2 and acme["wage_median"] == 122400
    # spelling variants fold into one key; the display name is the commonest raw form
    assert acme["employer"] == "Acme Widgets Corp"


def test_certified_withdrawn_counts_as_certified_and_month_wages_annualise():
    table, _ = _table()
    cook = [r for r in table if r["worksite_county"] == "COOK"][0]
    assert cook["certified"] == 1 and cook["wage_median"] == 120000


def test_implausible_wages_are_dropped_not_averaged():
    assert h1b_lca.annual_wage("5", "Hour") is None          # $10,400 is under the full-time minimum-wage floor
    assert h1b_lca.annual_wage("2000", "Week") == 104000
    assert h1b_lca.annual_wage("abc", "Year") is None
    assert h1b_lca.annual_wage("100000", "Fortnight") is None


def test_no_personal_data_reaches_the_stored_table(tmp_path):
    table, stats = _table()
    h1b_lca.store(table, stats, {"file": XLSX.name, "fiscal_year": 2026, "quarter": 3,
                                 "as_of": "2026-06-30"}, store_dir=tmp_path)
    blob = gzip.decompress((tmp_path / "h1b_lca" / h1b_lca.TABLE).read_bytes()).decode()
    assert blob.splitlines()[0].split(",") == list(h1b_lca.FIELDS)
    for leak in ("example.com", "555-01", "Testperson", "Pat", "Counsel",
                 "I-200-26001", "Example Street", "94000"):
        assert leak not in blob, leak
    m = json.loads((tmp_path / "h1b_lca" / "MANIFEST.json").read_text())
    assert m["as_of"] == "2026-06-30" and "Public domain" in m["licence"]
    assert "Office of Foreign Labor Certification" in m["attribution"]
    assert h1b_lca.stored_employer_keys(tmp_path) == {"acme widgets", "example health"}


def test_a_changed_header_fails_loudly(tmp_path):
    import zipfile
    bad = tmp_path / "bad.xlsx"
    with zipfile.ZipFile(XLSX) as src, zipfile.ZipFile(bad, "w") as dst:
        for n in src.namelist():
            body = src.read(n)
            if n == "xl/sharedStrings.xml":
                body = body.replace(b"<t>EMPLOYER_NAME</t>", b"<t>EMPLOYER_LEGAL_NAME</t>")
            dst.writestr(n, body)
    with pytest.raises(h1b_lca.SourceError, match="EMPLOYER_NAME"):
        list(h1b_lca.iter_rows(bad))


def test_compact_site_copy_has_attribution_and_top_employers():
    table, stats = _table()
    c = h1b_lca.compact(table, stats, {"file": XLSX.name, "as_of": "2026-06-30"},
                        {"tracker_companies": 10, "matched": 1, "match_pct": 10.0})
    assert c["top_employers"][0]["employer"] == "Acme Widgets Corp"
    assert c["top_employers"][0]["cases"] == 4
    assert c["licence"] and c["attribution"] and c["source_url"]


def test_touch_keeps_a_monthly_check_of_a_quarterly_source_fresh(tmp_path):
    from datetime import datetime, timezone
    table, stats = _table()
    h1b_lca.store(table, stats, {"file": XLSX.name, "fiscal_year": 2026, "quarter": 3,
                                 "as_of": "2026-06-30"}, store_dir=tmp_path)
    later = datetime(2026, 12, 1, tzinfo=timezone.utc)
    spec = {"h1b_lca": reference_freshness.SPECS["h1b_lca"]}
    assert any("collector" in p for p in reference_freshness.check(tmp_path, later, spec)[0]["problems"])
    reference_store.touch("h1b_lca", tmp_path, now=later)
    assert not reference_freshness.check(tmp_path, later, spec)[0]["problems"]


def test_compact_carries_matched_company_detail_keyed_by_tracker_name():
    """Company pages look up an EXACT tracker company name; the site never
    re-implements the normaliser, so the name->key map is built here."""
    table, stats = _table()
    key = table[0]["employer_key"]
    match = {"tracker_companies": 2, "matched": 1, "match_pct": 50.0,
             "pairs": {key: key}, "names": {"Tracker Name Inc": key}}
    c = h1b_lca.compact(table, stats, {"file": XLSX.name, "as_of": "2026-06-30",
                                       "fiscal_year": 2026, "quarter": 3}, match)
    assert c["fiscal_year"] == 2026 and c["quarter"] == 3
    assert c["names"] == {"Tracker Name Inc": key}
    d = c["companies"][key]
    rows = [r for r in table if r["employer_key"] == key]
    assert d["certified"] == sum(r["certified"] for r in rows)
    assert d["cases"] == sum(r["cases"] for r in rows)
    assert d["roles"] and d["states"]
    assert set(c["companies"]) == {key}          # unmatched employers never travel


def test_top_employers_carry_role_and_state_breakdowns_for_the_filters():
    table, stats = _table()
    top = h1b_lca.top_employers(table)
    assert top[0]["roles"] and top[0]["states"]
    assert sum(top[0]["states"].values()) <= top[0]["certified"] or top[0]["certified"] == 0


def test_weighted_median_combines_group_medians_by_wage_count():
    assert h1b_lca.weighted_median([(100000, 1), (200000, 3)]) == 200000
    assert h1b_lca.weighted_median([]) is None


def test_republish_rebuilds_from_the_committed_table(tmp_path):
    table, stats = _table()
    meta = {"file": XLSX.name, "as_of": "2026-06-30", "fiscal_year": 2026, "quarter": 3}
    h1b_lca.store(table, stats, meta, store_dir=tmp_path)
    back = h1b_lca.read_stored_table(tmp_path)
    assert back == table
