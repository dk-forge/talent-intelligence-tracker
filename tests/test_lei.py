"""GLEIF LEI matching: a code is attached only on a UNIQUE name AND country match.

Offline. The fixtures are synthetic rows in the GLEIF golden-copy CSV shape
(only the columns the matcher reads), never a download.
"""
import csv
import io
import json
import sqlite3
import zipfile

import gleif_lei
from pipeline import lei


def test_normalise_strips_case_punctuation_accents_and_legal_suffixes():
    assert lei.normalise_name("Hook Media, Inc.") == "hook media"
    assert lei.normalise_name("HÖÖK media AB") == "hook media"
    assert lei.normalise_name("The Acme Widget Co., Ltd") == "acme widget"
    assert lei.normalise_name("Smith & Sons GmbH & Co. KG") == "smith and sons"
    assert lei.normalise_name("  Société Générale S.A. ") == "societe generale"


def test_a_name_that_is_only_a_suffix_does_not_normalise_to_empty_match():
    # "Holding Inc" must not collapse to "" and match every other empty name.
    assert lei.normalise_name("Inc.") == ""
    idx = lei.LeiIndex({("", "US")})
    idx.add("", "US", "LEI00000000000000001")
    assert lei.match_company(idx, ["Inc."], {"US"}) is None


def _index(*records):
    wanted = {(lei.normalise_name("Hook Media, Inc."), "US"),
              (lei.normalise_name("Acme Corp"), "GB"),
              (lei.normalise_name("Twin Ltd"), "GB")}
    idx = lei.LeiIndex(wanted)
    for code, name, cc in records:
        idx.add(name, cc, code)
    return idx


def test_country_is_required_the_hook_media_false_match():
    # The probe's real false match: a US employer and a Swedish LEI holder
    # whose names normalise identically.
    idx = _index(("549300SWEDEN0000HOOK", "HÖÖK media AB", "SE"))
    assert lei.match_company(idx, ["Hook Media, Inc."], {"US"}) is None


def test_unique_name_and_country_match_assigns_the_lei():
    idx = _index(("549300ACMEGB0000AA01", "ACME CORP LIMITED", "GB"))
    assert lei.match_company(idx, ["Acme Corp"], {"GB"}) == "549300ACMEGB0000AA01"


def test_two_entities_with_the_same_name_in_the_country_is_ambiguous():
    idx = _index(("549300TWIN00000000A1", "Twin Limited", "GB"),
                 ("549300TWIN00000000B2", "TWIN LTD.", "GB"))
    assert lei.match_company(idx, ["Twin Ltd"], {"GB"}) is None


def test_one_entity_reached_by_two_names_is_still_unique():
    idx = _index(("549300ACMEGB0000AA01", "Acme Corp Ltd", "GB"),
                 ("549300ACMEGB0000AA01", "ACME CORP", "GB"))
    assert lei.match_company(idx, ["Acme Corp"], {"GB"}) == "549300ACMEGB0000AA01"


def test_a_company_seen_in_two_countries_gets_no_lei():
    idx = _index(("549300ACMEGB0000AA01", "Acme Corp", "GB"))
    assert lei.match_company(idx, ["Acme Corp"], {"GB", "US"}) is None
    assert lei.match_company(idx, ["Acme Corp"], set()) is None


def test_two_of_our_spellings_pointing_at_two_leis_is_ambiguous():
    wanted = {("alpha", "DE"), ("alpha beta", "DE")}
    idx = lei.LeiIndex(wanted)
    idx.add("Alpha GmbH", "DE", "LEIALPHA000000000001")
    idx.add("Alpha Beta AG", "DE", "LEIALPHABETA00000002")
    assert lei.match_company(idx, ["Alpha", "Alpha Beta"], {"DE"}) is None


def test_index_only_keeps_names_it_was_asked_for():
    idx = lei.LeiIndex({("acme corp", "GB")})
    idx.add("Unrelated Plc", "GB", "LEIUNRELATED00000001")
    assert len(idx) == 0


# ---- the job ---------------------------------------------------------------

HEADER = ["LEI", "Entity.LegalName", "Entity.OtherEntityNames.OtherEntityName.1",
          "Entity.TransliteratedOtherEntityNames.TransliteratedOtherEntityName.1",
          "Entity.LegalAddress.Country", "Entity.EntityStatus",
          "Registration.RegistrationStatus"]


def _golden_zip(tmp_path, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADER)
    w.writerows(rows)
    path = tmp_path / "gc.csv.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("golden.csv", buf.getvalue())
    return path


def _db(tmp_path, rows):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE signals (company TEXT, company_key TEXT, country TEXT,"
                 " hq_country TEXT, is_current INTEGER)")
    conn.executemany("INSERT INTO signals VALUES (?,?,?,?,1)", rows)
    conn.commit()
    conn.close()
    return path


def test_job_end_to_end_is_unique_country_matched_and_skips_inactive(tmp_path):
    golden = _golden_zip(tmp_path, [
        ["549300ACMEGB0000AA01", "ACME CORP LIMITED", "", "", "GB", "ACTIVE", "ISSUED"],
        ["549300SWEDEN0000HOOK", "HÖÖK media AB", "", "", "SE", "ACTIVE", "ISSUED"],
        ["549300DEAD0000000001", "Gone Ltd", "", "", "GB", "INACTIVE", "ISSUED"],
        ["549300RETIRED0000001", "Old Co", "", "", "US", "ACTIVE", "RETIRED"],
        ["549300ZETA0000000001", "Zeta Holdings PLC", "Zeta", "", "GB", "ACTIVE", "LAPSED"],
    ])
    db = _db(tmp_path, [
        ("Acme Corp", "acme corp", "GB", "GB"),
        ("Hook Media, Inc.", "hook media", "US", "US"),
        ("Gone Ltd", "gone", "GB", None),
        ("Old Co", "old co", "US", None),
        ("Zeta", "zeta", None, "GB"),
    ])
    companies = gleif_lei.load_companies(sqlite3.connect(db))
    result = gleif_lei.match_all(companies, gleif_lei.iter_golden_copy(golden))
    assert result == {"acme corp": "549300ACMEGB0000AA01",
                      "zeta": "549300ZETA0000000001"}


def test_hq_country_wins_over_event_country(tmp_path):
    db = _db(tmp_path, [("Acme Corp", "acme corp", "FR", "GB"),
                        ("Acme Corp", "acme corp", "DE", "GB")])
    companies = gleif_lei.load_companies(sqlite3.connect(db))
    assert companies["acme corp"][1] == {"GB"}


def test_output_document_is_stable_and_attributed(tmp_path):
    out = tmp_path / "company_lei.json"
    gleif_lei.write_output(out, {"b": "LEIB", "a": "LEIA"}, "2026-10-13 08:00:00")
    doc = json.loads(out.read_text())
    assert list(doc["companies"]) == ["a", "b"]
    assert doc["source"]["licence"] == "CC0 1.0"
    assert doc["source"]["golden_copy"] == "2026-10-13 08:00:00"


def test_the_job_never_names_a_paid_or_model_provider():
    src = open(gleif_lei.__file__, encoding="utf-8").read().lower()
    for name in ("openrouter", "anthropic", "openai", "api_key"):
        assert name not in src
