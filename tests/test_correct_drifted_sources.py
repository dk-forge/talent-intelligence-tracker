"""A drifted citation moves only to an address the owner ruled is the same
document, only through the committed re-source ledger, and as a revision."""

from __future__ import annotations

import json

import pytest

import correct_drifted_sources as cds
from pipeline import re_source, schema, validate

OLD = "https://m.example-old.com/story/123"
NEW = "https://www.example-new.com/story/123"
HEADLINE = "Acme names a new chief executive"
HASH = validate.content_hash("acme", "leadership", "2026-09-11", HEADLINE, "Example Times")


@pytest.fixture
def conn(tmp_path):
    c = schema.connect(tmp_path / "rows.db")
    fields = {"signal_id": HASH, "headline": HEADLINE, "summary": "s", "talent_readthrough": "t",
              "company": "Acme", "company_key": "acme", "pillar": "leadership",
              "signal_direction": "neutral", "confidence": "reported", "source_url": OLD,
              "source_name": "Example Times", "captured_at": "2026-09-11", "as_of": "2026-09-11",
              "content_hash": HASH, "collector": "national_press", "published_date": "2026-09-11",
              "country": "US", "region": "North America", "published_at": "2026-09-12 01:00:00",
              "is_current": 1}
    c.execute(f"INSERT INTO signals ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
              tuple(fields.values()))
    c.commit()
    yield c
    c.close()


SPEC = {OLD: {"new_url": NEW, "why": "same article id at the outlet's new domain"}}


def test_apply_refuses_what_the_ledger_does_not_hold(conn, tmp_path):
    with pytest.raises(re_source.NotInLedger):
        cds.run(conn, SPEC, mode="apply", ledger_path=tmp_path / "l.json",
                push=re_source.push)
    assert conn.execute("SELECT source_url FROM signals WHERE is_current = 1").fetchone()[0] == OLD


def test_record_then_apply_revises_and_keeps_the_original(conn, tmp_path):
    ledger = tmp_path / "l.json"
    cds.run(conn, SPEC, mode="record", ledger_path=ledger)
    assert json.loads(ledger.read_text())[HASH]["source_url"] == NEW
    sent = []

    def push(row, **kw):
        re_source.entry_for(kw["ledger"], row["content_hash"], source_url=kw["source_url"],
                            source_name=kw["source_name"], headline=kw["headline"])
        sent.append(kw["source_url"])
        return {}

    cds.run(conn, SPEC, mode="apply", ledger_path=ledger, push=push)
    assert sent == [NEW]
    rows = conn.execute("SELECT source_url, is_current, content_hash, published_at FROM signals "
                        "ORDER BY is_current").fetchall()
    assert [(r[0], r[1]) for r in rows] == [(OLD, 0), (NEW, 1)]
    assert rows[1][2] == HASH and rows[1][3], "same fingerprint, still published"


def test_a_spec_that_stays_on_the_same_host_is_refused(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({OLD: {"new_url": "https://m.example-old.com/other", "why": "x"}}))
    with pytest.raises(ValueError):
        cds.load_spec(p)


def test_the_committed_spec_loads():
    assert cds.load_spec()
