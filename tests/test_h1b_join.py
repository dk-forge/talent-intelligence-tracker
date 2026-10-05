"""Company-name join: tracker companies -> H-1B (DOL OFLC LCA) employers.

Written before h1b_join.py. Names are synthetic or public company names.
"""
import h1b_join


def test_normalise_strips_case_punctuation_and_legal_suffixes():
    n = h1b_join.normalise_employer
    assert n("Kontoor Brands, Inc.") == "kontoor brands"
    assert n("KONTOOR BRANDS INC") == "kontoor brands"
    assert n("The Example Company, L.L.C.") == "example"
    assert n("Acme Widgets Corp") == n("ACME WIDGETS CORPORATION")
    assert n("Smith & Jones LLP") == "smith and jones"
    assert n("Acme GmbH") == "acme"


def test_normalise_never_reduces_a_name_to_nothing():
    # "The Company Inc" is all suffix; an empty key would match every other empty key.
    assert h1b_join.normalise_employer("The Company, Inc.") != ""
    assert h1b_join.normalise_employer("") == ""
    assert h1b_join.normalise_employer(None) == ""


def test_dba_clause_is_dropped():
    assert h1b_join.normalise_employer("Acme Holdings LLC d/b/a Acme Cloud") == "acme"
    assert h1b_join.normalise_employer("ACME HOLDINGS LLC DBA ACME CLOUD") == "acme"


def test_match_reports_rate_and_pairs():
    employers = {"kontoor brands", "acme widgets", "example"}
    tracker = ["Kontoor Brands, Inc.", "Acme Widgets Corporation", "Unrelated Startup Inc", "Kontoor Brands Inc"]
    r = h1b_join.match(tracker, employers)
    assert r["tracker_companies"] == 3          # deduplicated by normalised key
    assert r["matched"] == 2
    assert r["match_pct"] == round(100 * 2 / 3, 1)
    assert r["pairs"]["kontoor brands"] == "kontoor brands"
    assert "unrelated startup" in r["unmatched_sample"]


def test_match_on_empty_input_is_zero_not_a_division_error():
    r = h1b_join.match([], {"x"})
    assert r["tracker_companies"] == 0 and r["match_pct"] == 0.0


def test_tracker_companies_reads_current_us_rows_read_only(tmp_path):
    import sqlite3
    db = tmp_path / "t.db"
    c = sqlite3.connect(db)
    c.execute("create table signals (company text, country text, is_current int)")
    c.executemany("insert into signals values (?,?,?)", [
        ("Acme Widgets Corp", "US", 1), ("Acme Widgets Corp", "US", 1),
        ("Old Name Inc", "US", 0), ("Euro GmbH", "DE", 1), (None, "US", 1)])
    c.commit(); c.close()
    assert h1b_join.tracker_companies(db) == ["Acme Widgets Corp"]


def test_match_maps_every_tracker_spelling_to_its_key():
    r = h1b_join.match(["Acme, Inc.", "ACME Inc", "Other Co"], {"acme"})
    assert r["names"] == {"Acme, Inc.": "acme", "ACME Inc": "acme"}
