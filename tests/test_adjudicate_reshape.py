"""An owner-ruled spec can rename an employer, change a headcount, rewrite the
amount wording, or split one row into several. Nothing else can.

Offline throughout: the site is two stubs (`withdraw`, `send`) handed in, the
referees are a stub `call`, the database is a tmp_path. The row is the shape of
the one that needed this: two unaffiliated manufacturers merged into one
employer under one article.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

import pytest

import adjudicate_guardrail as adj
import merge_db
from pipeline import classify, publish, schema, store, validate, vocab

HEADLINE = "Suppliers plan to invest $40M, create 240-plus jobs in Cedar Park"
MERGED = "Elite Metal Finishing and Machine Sciences"
DATE = "2026-09-11"
URL = "https://example.com/cedar-park"
HASH = validate.content_hash(vocab.company_key(MERGED), "how_we_work", DATE, HEADLINE, "Example")
KEY = f"amount/{HASH}"
A, B = adj.REFEREES
ARTICLE = "Two suppliers signed separate incentive agreements with Cedar Park. " * 20
_FIELDS = [f.name for f in dataclasses.fields(validate.Signal)]

ROWS = [
    {"corrected_company": "Elite Metal Finishing", "corrected_amount_text": "$10 million",
     "corrected_amount": 10_000_000, "corrected_headcount": 170},
    {"corrected_company": "Machine Sciences Corporation",
     "corrected_amount_text": "about $32 million", "corrected_amount": 32_000_000,
     "corrected_headcount": 70},
]


def _seed(conn, *, published_at="2026-09-12 01:45:52"):
    fields = {
        "signal_id": HASH, "headline": HEADLINE, "summary": "s", "talent_readthrough": "t",
        "company": MERGED, "company_key": vocab.company_key(MERGED),
        "pillar": "how_we_work", "signal_direction": "hiring", "country": "US",
        "materiality": "high", "confidence": "reported", "source_url": URL,
        "source_name": "Example", "captured_at": DATE, "as_of": DATE,
        "content_hash": HASH, "collector": "national_press", "published_date": DATE,
        "headcount": 240, "funding_amount": "$40 million", "funding_amount_usd": 40_000_000,
        "deal_type": "outbound_investment", "money_basis": "outbound_investment",
        "site_event": "opened", "published_at": published_at, "is_current": 1,
    }
    conn.execute(f"INSERT INTO signals ({', '.join(fields)}) "
                 f"VALUES ({', '.join('?' * len(fields))})", tuple(fields.values()))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO publish_guardrails (check_name, subject, label, detail, value, "
        " state, first_seen, last_seen, seen) VALUES ('amount', ?, ?, ?, ?, 'open', ?, ?, 1)",
        (HASH, f"{MERGED} $40,000,000", "raised by a session: merged employers", 40e6, now, now))
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = schema.connect(tmp_path / "reshape.db")
    _seed(c)
    yield c
    c.close()


def _spec(tmp_path, **over):
    body = {"key": KEY, "label": "x", "status": "owner-ruled", "action": "split",
            "rows": ROWS, "ruled_by": "two reviewers, 2026-09-21",
            "ruling": "the row merges two unaffiliated companies"}
    body.update(over)
    path = tmp_path / f"spec-{len(list(tmp_path.glob('spec-*.json')))}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


class Site:
    """The two doors to the live site, recording instead of posting."""

    def __init__(self):
        self.withdrawn, self.sent = [], []

    def withdraw(self, signal_id, reason):
        self.withdrawn.append(signal_id)
        return {"retracted": 1}

    def send(self, conn):
        rows = publish.unpublished(conn)
        self.sent.extend(rows)
        conn.executemany("UPDATE signals SET published_at = datetime('now') WHERE row_id = ?",
                         [(r["row_id"],) for r in rows])
        conn.commit()
        return {"sent": len(rows), "stored": len(rows), "duplicate": 0, "errors": []}


def _apply(conn, path, site, apply=True):
    return adj.apply_from_spec(conn, path, apply=apply, withdraw=site.withdraw, send=site.send)


def _current(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM signals WHERE is_current = 1 ORDER BY row_id")]


def _snapshot(conn):
    return [tuple(r) for r in conn.execute("SELECT * FROM signals ORDER BY row_id")]


# --- the split ---------------------------------------------------------------

def test_a_split_supersedes_the_original_and_inserts_the_second_row(conn, tmp_path):
    site = Site()
    assert _apply(conn, _spec(tmp_path), site) == 0
    first, second = _current(conn)
    assert (first["company"], first["funding_amount"], first["funding_amount_usd"],
            first["headcount"]) == ("Elite Metal Finishing", "$10 million", 10_000_000, 170)
    assert (second["company"], second["funding_amount"], second["funding_amount_usd"],
            second["headcount"]) == ("Machine Sciences Corporation", "about $32 million",
                                     32_000_000, 70)
    # Revision N+1 of the ORIGINAL signal, superseding it.
    old = dict(conn.execute("SELECT * FROM signals WHERE content_hash = ?", (HASH,)).fetchone())
    assert old["is_current"] == 0
    assert (first["signal_id"], first["revision"], first["supersedes_row_id"]) == (
        HASH, 2, old["row_id"])
    # A NEW signal, named the way build_signal names one.
    assert (second["revision"], second["supersedes_row_id"]) == (1, None)
    assert second["signal_id"] == second["content_hash"] != HASH
    for row in (first, second):
        assert row["money_basis"] == row["deal_type"] == "outbound_investment"
        assert (row["site_event"], row["country"], row["source_url"],
                row["published_date"]) == ("opened", "US", URL, DATE)


def test_every_new_hash_comes_from_the_repos_own_hashing_function(conn, tmp_path):
    _apply(conn, _spec(tmp_path), Site())
    for row in _current(conn):
        assert row["company_key"] == vocab.company_key(row["company"])
        assert row["content_hash"] == validate.content_hash(
            row["company_key"], row["pillar"], row["published_date"],
            row["headline"], row["source_name"])


def test_each_row_carries_a_provenance_note_pointing_at_the_spec(conn, tmp_path):
    _apply(conn, _spec(tmp_path), Site())
    first, second = _current(conn)
    assert first["notes"].startswith(f"{adj.RESHAPE_MARK} {KEY} part 1/2")
    assert second["notes"].startswith(f"{adj.RESHAPE_MARK} {KEY} part 2/2")
    assert "two reviewers, 2026-09-21" in second["notes"]


def test_the_live_row_is_withdrawn_before_the_replacements_are_sent(conn, tmp_path):
    order = []
    site = Site()
    withdraw, send = site.withdraw, site.send
    site.withdraw = lambda *a: (order.append("withdraw"), withdraw(*a))[1]
    site.send = lambda c: (order.append("send"), send(c))[1]
    _apply(conn, _spec(tmp_path), site)
    assert order == ["withdraw", "send"]
    assert site.withdrawn == [HASH], "the site retracts by signal_id, once"
    assert sorted(r["company"] for r in site.sent) == [
        "Elite Metal Finishing", "Machine Sciences Corporation"]
    assert HASH not in {r["content_hash"] for r in site.sent}, \
        "the merged row must be replaced on the site, never re-sent beside the two"
    assert all(r["published_at"] for r in _current(conn))


def test_a_never_published_row_is_split_without_touching_the_site(tmp_path):
    c = schema.connect(tmp_path / "held.db")
    _seed(c, published_at=None)
    site = Site()
    assert _apply(c, _spec(tmp_path), site) == 0
    assert site.withdrawn == []
    assert len(_current(c)) == 2


def test_the_finding_is_accepted_under_the_rulers_name(conn, tmp_path):
    _apply(conn, _spec(tmp_path), Site())
    row = conn.execute("SELECT state, reviewed_by FROM publish_guardrails").fetchone()
    assert row["state"] == "accepted"
    assert row["reviewed_by"] == "owner ruling (two reviewers, 2026-09-21)"
    assert "referee" not in row["reviewed_by"], "no referee was asked this question"


def test_a_dry_run_changes_nothing_anywhere(conn, tmp_path):
    before, site = _snapshot(conn), Site()
    assert _apply(conn, _spec(tmp_path), site, apply=False) == 0
    assert _snapshot(conn) == before and not site.withdrawn and not site.sent
    assert conn.execute("SELECT state FROM publish_guardrails").fetchone()["state"] == "open"


# --- idempotency ---------------------------------------------------------------

def test_reapplying_the_same_spec_changes_nothing(conn, tmp_path):
    site, path = Site(), _spec(tmp_path)
    assert _apply(conn, path, site) == 0
    before, reviewed = _snapshot(conn), conn.execute(
        "SELECT reviewed_at FROM publish_guardrails").fetchone()[0]
    again = Site()
    assert _apply(conn, path, again) == 0
    assert _snapshot(conn) == before, "a second application must write no row"
    assert again.withdrawn == [] and again.sent == []
    assert conn.execute("SELECT reviewed_at FROM publish_guardrails").fetchone()[0] == reviewed


def test_a_run_killed_after_the_local_write_finishes_the_ledger_and_the_send(conn, tmp_path):
    path = _spec(tmp_path)

    def dies(_conn):
        raise publish.PublishError("504 from the host")

    site = Site()
    assert adj.apply_from_spec(conn, path, apply=True, withdraw=site.withdraw, send=dies) == 1
    assert len(_current(conn)) == 2 and not any(r["published_at"] for r in _current(conn))
    before = [(r["row_id"], r["content_hash"]) for r in _current(conn)]
    retry = Site()
    assert _apply(conn, path, retry) == 0
    assert [(r["row_id"], r["content_hash"]) for r in _current(conn)] == before
    assert retry.withdrawn == [], "already withdrawn; the retry must not withdraw again"
    assert len(retry.sent) == 2


# --- only an owner ruling may do this ----------------------------------------

@pytest.mark.parametrize("status", ["agree-dry-run", "applied"])
def test_a_two_referee_spec_can_never_split(conn, tmp_path, status):
    before, site = _snapshot(conn), Site()
    assert _apply(conn, _spec(tmp_path, status=status, who=adj.WHO), site) == 3
    assert _snapshot(conn) == before and not site.withdrawn


@pytest.mark.parametrize("field,value", [
    ("corrected_company", "Elite Metal Finishing"), ("corrected_headcount", 170),
    ("corrected_amount_text", "$10 million")])
def test_a_two_referee_spec_can_never_rename_or_recount(conn, tmp_path, field, value):
    before = _snapshot(conn)
    path = _spec(tmp_path, status="agree-dry-run", action="edit", rows=None,
                 correction={field: value})
    assert _apply(conn, path, Site()) == 3
    assert _snapshot(conn) == before


def test_the_referee_door_refuses_a_split_even_when_handed_one(conn):
    item = adj.load_finding(conn, KEY)
    for apply in (False, True):
        with pytest.raises(adj.OwnerOnly):
            adj.apply_decision(conn, item, "split", None, "n", apply=apply)
        with pytest.raises(adj.OwnerOnly):
            adj.apply_decision(conn, item, "edit", {"corrected_company": "X"}, "n", apply=apply)
        with pytest.raises(adj.OwnerOnly):
            adj.apply_place(conn, item, "edit", {"corrected_company": "X",
                                                 "corrected_country": "US"}, "n", apply=apply)


def test_apply_reshape_itself_refuses_anything_but_an_owner_ruling(conn):
    with pytest.raises(adj.OwnerOnly):
        adj.apply_reshape(conn, {"key": KEY, "status": "applied", "action": "split",
                                 "rows": ROWS}, who=adj.WHO, apply=True)


def _referees(answer):
    def call(model, system, user, *, timeout, max_tokens=None, json_mode=True):
        return json.dumps(answer)
    return call


def test_two_referees_answering_split_is_no_verdict_and_applies_nothing(conn, tmp_path):
    saved = dict(classify.STATS)
    try:
        before = _snapshot(conn)
        answer = {"recommended": "split", "confidence": 99, "rows": ROWS, "reasoning": "two"}
        rc = adj.adjudicate(conn, KEY, apply=True, start_usd=0.0, call=_referees(answer),
                            fetch=lambda url: ARTICLE, wayback=lambda url: None,
                            spec_dir=tmp_path / "specs")
        assert rc == 3
        assert _snapshot(conn) == before
        spec = json.loads(next((tmp_path / "specs").glob("*.json")).read_text())
        assert spec["status"] == "unknown" and spec["action"] == "keep"
    finally:
        classify.STATS.clear()
        classify.STATS.update(saved)


def test_two_referees_smuggling_a_rename_into_an_edit_rename_nothing(conn, tmp_path):
    saved = dict(classify.STATS)
    try:
        answer = {"recommended": "edit", "confidence": 99, "corrected_amount": 10_000_000,
                  "corrected_basis": "outbound_investment", "reasoning": "r",
                  "corrected_company": "Elite Metal Finishing", "corrected_headcount": 170,
                  "rows": ROWS}
        rc = adj.adjudicate(conn, KEY, apply=True, start_usd=0.0, call=_referees(answer),
                            fetch=lambda url: ARTICLE, wayback=lambda url: None,
                            spec_dir=tmp_path / "specs", push=lambda row, amount: {})
        assert rc == 0
        (row,) = _current(conn)
        assert (row["company"], row["headcount"], row["content_hash"]) == (MERGED, 240, HASH)
        spec = json.loads(next((tmp_path / "specs").glob("*.json")).read_text())
        assert set(spec["correction"]) == {"corrected_amount", "corrected_basis"}
    finally:
        classify.STATS.clear()
        classify.STATS.update(saved)


def test_split_is_not_in_the_vocabulary_the_automatic_run_reads():
    assert adj.SPLIT not in adj.ACTIONS
    assert adj.parse_verdict(json.dumps({"recommended": "split", "confidence": 99})) is None


# --- refusals happen before the site is touched ------------------------------------

def _refused(conn, tmp_path, **over):
    before, site = _snapshot(conn), Site()
    assert _apply(conn, _spec(tmp_path, **over), site) == 3
    assert _snapshot(conn) == before and site.withdrawn == [] and site.sent == []


def test_two_entries_for_one_employer_are_refused(conn, tmp_path):
    _refused(conn, tmp_path, rows=[ROWS[0], dict(ROWS[0], corrected_headcount=70)])


def test_a_split_into_one_row_is_refused(conn, tmp_path):
    _refused(conn, tmp_path, rows=[ROWS[0]])


def test_wording_and_integer_that_disagree_are_refused(conn, tmp_path):
    _refused(conn, tmp_path, rows=[dict(ROWS[0], corrected_amount=40_000_000), ROWS[1]])


def test_a_hash_this_database_already_holds_is_refused(conn, tmp_path):
    taken = adj.reshaped_signal(dict(conn.execute("SELECT * FROM signals").fetchone()), ROWS[1])
    taken.signal_id = taken.content_hash
    assert store.store(conn, taken) == "stored"
    conn.commit()
    _refused(conn, tmp_path)


def test_a_live_row_whose_hash_would_not_move_is_refused(conn, tmp_path):
    _refused(conn, tmp_path, action="edit", rows=None, correction={"corrected_headcount": 170})


def test_a_held_row_can_have_its_headcount_edited_in_place(tmp_path):
    c = schema.connect(tmp_path / "held.db")
    _seed(c, published_at=None)
    site, path = Site(), _spec(tmp_path, action="edit", rows=None,
                               correction={"corrected_headcount": 170})
    assert _apply(c, path, site) == 0
    (row,) = _current(c)
    assert (row["headcount"], row["revision"], row["content_hash"]) == (170, 2, HASH)
    before = _snapshot(c)
    assert _apply(c, path, Site()) == 0
    assert _snapshot(c) == before, "an edit that keeps the hash must be idempotent too"


def test_an_owner_ruled_rename_is_a_one_row_reshape(conn, tmp_path):
    site = Site()
    path = _spec(tmp_path, action="edit", rows=None,
                 correction={"corrected_company": "Elite Metal Finishing"})
    assert _apply(conn, path, site) == 0
    (row,) = _current(conn)
    assert (row["company"], row["company_key"], row["revision"]) == (
        "Elite Metal Finishing", "elite metal finishing", 2)
    assert row["content_hash"] != HASH and site.withdrawn == [HASH]


# --- the rest of the pipeline ------------------------------------------------------

def _as_signal(row):
    return validate.Signal(**{k: row[k] for k in _FIELDS})


def test_the_next_collect_neither_collapses_the_pair_nor_restores_the_merged_row(conn, tmp_path):
    original = _as_signal(dict(conn.execute("SELECT * FROM signals").fetchone()))
    _apply(conn, _spec(tmp_path), Site())
    # The same article again, extracted the same merged way: judged and withdrawn.
    assert store.store(conn, original) == "retracted"
    # Each half again, from this outlet and from a second one: already held.
    for row in _current(conn):
        again = _as_signal(row)
        assert store.store(conn, again) == "duplicate"
        again.headline, again.source_name = "Cedar Park slated for 2 new manufacturers", "Other"
        again.content_hash = validate.content_hash(
            again.company_key, again.pillar, again.published_date,
            again.headline, again.source_name)
        assert store.store(conn, again) == "duplicate"
    assert [r["company"] for r in _current(conn)] == [
        "Elite Metal Finishing", "Machine Sciences Corporation"]


def test_the_shared_source_url_does_not_make_the_two_rows_one(conn, tmp_path):
    _apply(conn, _spec(tmp_path), Site())
    first, second = _current(conn)
    assert first["source_url"] == second["source_url"]
    assert first["content_hash"] != second["content_hash"]
    assert first["company_key"] != second["company_key"]


def test_the_funding_backfill_leaves_both_figures_alone(conn, tmp_path):
    _apply(conn, _spec(tmp_path), Site())
    assert schema.backfill_funding_usd(conn) == 0
    assert [r["funding_amount_usd"] for r in _current(conn)] == [10_000_000, 32_000_000]
    assert conn.execute("SELECT funding_amount_usd FROM signals WHERE content_hash = ?",
                        (HASH,)).fetchone()[0] == 40_000_000, "history is not rewritten"


def test_neither_row_reaches_the_raise_total(conn, tmp_path):
    _apply(conn, _spec(tmp_path), Site())
    total = conn.execute("SELECT COALESCE(SUM(funding_amount_usd), 0) FROM signals "
                         " WHERE is_current = 1 AND money_basis = 'company_raise'").fetchone()[0]
    assert total == 0


def test_merge_db_carries_the_split_onto_a_main_that_never_saw_it(conn, tmp_path):
    main = schema.connect(tmp_path / "main.db")
    _seed(main)
    _apply(conn, _spec(tmp_path), Site())
    merge_db._merge_signals(conn, main)
    merge_db._merge_guardrails(conn, main)
    main.commit()
    rows = _current(main)
    assert sorted(r["company"] for r in rows) == [
        "Elite Metal Finishing", "Machine Sciences Corporation"]
    old = main.execute("SELECT row_id, is_current FROM signals WHERE content_hash = ?",
                       (HASH,)).fetchone()
    assert old["is_current"] == 0
    revised = next(r for r in rows if r["signal_id"] == HASH)
    assert revised["supersedes_row_id"] == old["row_id"], "remapped onto main's numbering"
    assert all(r["published_at"] for r in rows)
    assert main.execute("SELECT state FROM publish_guardrails").fetchone()["state"] == "accepted"
    # And the merge is as idempotent as the apply.
    before = _snapshot(main)
    merge_db._merge_signals(conn, main)
    assert _snapshot(main) == before
    main.close()


def test_the_committed_elite_metal_spec_is_well_formed():
    path = adj.SPEC_DIR / "2026-09-21-amount-23344cfec1156a226e3bc355bae49768.json"
    spec = json.loads(path.read_text(encoding="utf-8"))
    assert (spec["status"], spec["action"]) == ("owner-ruled", "split")
    assert spec["ruled_by"] and "communityimpact.com" in spec["ruling"] \
        and "connectcre.com" in spec["ruling"]
    assert [(r["corrected_company"], r["corrected_amount"], r["corrected_headcount"])
            for r in spec["rows"]] == [("Elite Metal Finishing", 10_000_000, 170),
                                       ("Machine Sciences Corporation", 32_000_000, 70)]
    for entry in spec["rows"]:
        assert vocab.parse_funding_usd(entry["corrected_amount_text"]) == entry["corrected_amount"]
    assert chr(0x2014) not in path.read_text(encoding="utf-8")
