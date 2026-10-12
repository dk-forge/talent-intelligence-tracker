"""The stored language of a row's headline (owner-approved 2026-10-12).

What is pinned: a free local detector (no model, no network), an ISO 639-1
code plus a confidence, NULL code whenever the detector is not sure, and the
route from ingest to the site: the column exists, store() fills it, connect()
backfills it on old rows exactly once, and publish/enrich carry it to a site
whose schema, row mapping and /query all know it.
"""

import re
import sqlite3
from pathlib import Path

import pytest

from pipeline import language, publish, schema

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
API = (PLUGIN / "includes" / "api.php").read_text(encoding="utf-8")
DB = (PLUGIN / "includes" / "db.php").read_text(encoding="utf-8")
MAIN = (PLUGIN / "talent-intelligence-tracker.php").read_text(encoding="utf-8")

COLUMNS = ("language", "language_confidence", "summary_language")


@pytest.mark.parametrize("headline, code", [
    # Real shapes from the committed database.
    ("Formosa Ha Tinh contratará a 300 trabajadores el 20 de agosto.", "es"),
    ("Carraro Logística tem novas vagas para motoristas em São Paulo e Paraná", "pt"),
    ("Dr. Marc Kirchhoff startet offiziell als neuer Vorstandsvorsitzender der Sparkasse", "de"),
    ("Lionel Guérin est nommé directeur général d’Air Tahiti Nui", "fr"),
    ("Alla Asl Roma 4 via libera dalla regione per oltre 200 nuove assunzioni", "it"),
    ("Epiroc utökar produktionen: 40 nya jobb", "sv"),
    ("Kitopi raises $50 million in growth capital led by EvolutionX", "en"),
    ("Act Security raises $60M to take action against agentic access sprawl at the infrastructure layer", "en"),
    ("삼성전자, 반도체 인력 1000명 채용", "ko"),
    ("トヨタ、新卒採用を拡大", "ja"),
    ("Нова компанія відкриває офіс у Києві", "uk"),
])
def test_detects_the_language_of_real_headlines(headline, code):
    got, confidence = language.detect(headline)
    assert got == code
    assert confidence >= language.THRESHOLD


@pytest.mark.parametrize("headline", [
    "Nexio secures Texas campus",          # too short to be sure of anything
    "Rafael Oliveira CEO",                 # names, no function words
    "Švedų milžinė perka beveik 300 darbuotojų turinčią Vievio gamybos įmonę",  # Lithuanian: no list, must not become Polish
    "Новата компания назначи директор",    # Cyrillic with no letter telling ru/bg apart
])
def test_never_guesses_when_unsure(headline):
    code, confidence = language.detect(headline)
    assert code is None
    assert confidence is not None, "examined text must say so (confidence set)"


def test_nothing_to_examine_is_not_examined():
    assert language.detect("") == (None, None)
    assert language.detect(None) == (None, None)
    assert language.columns("")["language_confidence"] is None


def test_confidence_is_a_fraction():
    _, c = language.detect("Epiroc utökar produktionen: 40 nya jobb")
    assert 0.0 <= c <= 1.0


def test_detector_imports_no_network_or_model_client():
    src = (ROOT / "pipeline" / "language.py").read_text(encoding="utf-8")
    imports = set(re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, re.M))
    assert imports <= {"__future__", "re", "unicodedata"}, imports


def _signal_row(conn, **over):
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(signals)")}
    row = {}
    for name, r in cols.items():
        if r[3] and r[4] is None and not r[5]:  # NOT NULL, no default, not pk
            row[name] = 0 if "INT" in (r[2] or "").upper() else "x"
    row.update(signal_id="s1", content_hash="h1", is_current=1, revision=1,
               headline="Atac, nel 2026 previste nuove assunzioni: oltre 700 ingressi tra autisti e profili tecnici",
               summary="Atac plans to hire more than 700 drivers and technical staff in 2026.")
    row.update(over)
    names = ", ".join(row)
    conn.execute(f"INSERT INTO signals ({names}) VALUES ({', '.join('?' for _ in row)})",
                 tuple(row.values()))


def test_columns_are_migrated_onto_an_old_database(tmp_path):
    db = tmp_path / "old.db"
    raw = sqlite3.connect(db)
    old = schema.TABLES
    for col in COLUMNS:
        old = re.sub(rf"\n\s*{col}\s+[A-Z]+,", "", old)
    raw.executescript(old)
    raw.close()
    conn = schema.connect(db)
    have = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    assert set(COLUMNS) <= have
    for col in COLUMNS:
        assert any(m[0] == "signals" and m[1] == col for m in schema.MIGRATIONS)


def test_connect_backfills_old_rows_once_and_without_a_model(tmp_path):
    db = tmp_path / "t.db"
    conn = schema.connect(db)
    _signal_row(conn)
    conn.commit()
    conn.close()
    conn = schema.connect(db)
    row = conn.execute("SELECT language, language_confidence, summary_language FROM signals").fetchone()
    assert row["language"] == "it"
    assert row["language_confidence"] >= language.THRESHOLD
    assert row["summary_language"] == "en"
    # An undecidable row keeps a NULL code but is marked examined, so the next
    # connect() does not re-read it forever.
    _signal_row(conn, signal_id="s2", content_hash="h2", headline="Rafael Oliveira CEO",
                summary="x")
    conn.commit()
    assert schema.backfill_language(conn) == 1
    assert schema.backfill_language(conn) == 0
    r2 = conn.execute("SELECT language, language_confidence FROM signals WHERE signal_id='s2'").fetchone()
    assert r2["language"] is None and r2["language_confidence"] is not None


def test_columns_travel_to_the_site():
    for col in COLUMNS:
        assert col in publish.FIELDS
        assert col in publish.ENRICHABLE, "the only route onto rows already published"


def test_site_schema_mapping_and_query_know_the_columns():
    for col in COLUMNS:
        assert re.search(rf"^\s*{col} [A-Z]", DB, re.M), f"db.php table lacks {col}"
        assert f"'{col}'" in DB.split("function tit_row_to_db", 1)[-1] or f"'{col}'" in DB
        assert col in API.split("$rows_sql =", 1)[1].split("FROM", 1)[0], f"/query omits {col}"
        assert f"'{col}'" in MAIN.split("function tit_required_columns", 1)[1].split("}", 1)[0]


def test_site_refuses_a_malformed_language_value():
    assert "function tit_language_value" in API
    assert "tit_language_value(" in API.split("function tit_api_enrich", 1)[1]


def test_backup_check_excuses_only_columns_connect_rederives():
    import backup_check
    have = [f for f in publish.FIELDS if f not in COLUMNS]
    assert backup_check.publishable(have)[0] == backup_check.PASS
    assert backup_check.publishable(have[1:])[0] == backup_check.FAIL
    assert set(schema.DERIVED_ON_CONNECT) == set(COLUMNS)
