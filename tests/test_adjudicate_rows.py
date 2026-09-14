"""A row no check caught can be put to the two referees, and only they change it.

Offline throughout: the model is a stub through `call`, the source through
`fetch`, the site through `push`, the database a tmp_path.
"""

from __future__ import annotations

import json

import pytest

import adjudicate_guardrail as adj
import correct_city_country
from pipeline import classify, guardrails, schema, validate

HEADLINE = "Luego del conflicto, Vicuna avanza con su ruta de acceso por San Juan"
HASH = validate.content_hash("construmin", "company_development", "2026-09-11", HEADLINE, "Clarin")
MONEY_HEADLINE = "Acme's $59 million investment in its new logistics centre"
MONEY_HASH = validate.content_hash("acme", "company_development", "2026-09-11", MONEY_HEADLINE, "Gestion")
A, B = adj.REFEREES
ARTICLE_AR = ("El proyecto de cobre Vicuna, en la provincia de San Juan, Argentina, adjudico "
              "la construccion del camino a Construmin, empresa sanjuanina. ") * 14
ARTICLE_MONEY = ("Keiko Fujimori destaco la inversion de US$59 millones de PepsiCo en su nuevo "
                 "centro logistico en Callao. Acme invests $59 million in its own facility. ") * 12
assert len(ARTICLE_AR) >= adj.EVIDENCE_MIN_CHARS and len(ARTICLE_MONEY) >= adj.EVIDENCE_MIN_CHARS


def _row(conn, **kw):
    fields = {
        "signal_id": kw["content_hash"], "headline": kw.get("headline", HEADLINE),
        "summary": "s", "talent_readthrough": "t",
        "company": kw.get("company", "Construmin"), "company_key": kw.get("company_key", "construmin"),
        "pillar": "company_development", "signal_direction": "neutral", "confidence": "reported",
        "source_url": "https://example.com/x", "source_name": kw.get("source_name", "Clarin"),
        "captured_at": "2026-09-11", "as_of": "2026-09-11", "content_hash": kw["content_hash"],
        "collector": "national_press", "published_date": "2026-09-11",
        "city": kw.get("city", "San Juan"), "region": kw.get("region", "North America"),
        "country": kw.get("country", "US"), "state": kw.get("state"),
        "funding_amount": kw.get("funding_amount"), "funding_amount_usd": kw.get("funding_amount_usd"),
        "money_basis": kw.get("money_basis"), "published_at": kw.get("published_at", "2026-09-12 01:00:00"),
        "is_current": 1,
    }
    cols = ", ".join(fields)
    conn.execute(f"INSERT INTO signals ({cols}) VALUES ({', '.join('?' * len(fields))})",
                 tuple(fields.values()))
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = schema.connect(tmp_path / "rows.db")
    _row(c, content_hash=HASH)
    _row(c, content_hash=MONEY_HASH, company="Acme", company_key="acme", headline=MONEY_HEADLINE,
         source_name="Gestion", city=None, region="Latin America", country="PE",
         funding_amount="US$59 millones", funding_amount_usd=59_000_000, money_basis="company_raise")
    yield c
    c.close()


@pytest.fixture
def stats():
    saved = dict(classify.STATS)
    yield classify.STATS
    classify.STATS.clear()
    classify.STATS.update(saved)


def _referee(answers, cost=0.004, stats=None):
    calls = []

    def call(model, system, user, *, timeout, max_tokens=None, json_mode=True):
        calls.append((model, user))
        if stats is not None:
            stats["usd"] = float(stats.get("usd", 0.0)) + cost
        answer = answers[model]
        return answer if isinstance(answer, str) else json.dumps(answer)

    call.calls = calls
    return call


def _place(recommended="edit", city="San Juan", country="AR", confidence=90):
    return {"location_stated": True, "recommended": recommended, "corrected_city": city,
            "corrected_country": country, "confidence": confidence,
            "reasoning": "'en la provincia de San Juan, Argentina'"}


def _money(recommended="edit", basis="outbound_investment", amount=59_000_000):
    return {"amount_stated_for_this_event": True, "money_basis": basis, "recommended": recommended,
            "corrected_amount": amount, "corrected_basis": basis, "confidence": 90,
            "reasoning": "'inversion de US$59 millones de PepsiCo'"}


def _state(conn, key):
    check, _, subject = key.partition("/")
    row = conn.execute("SELECT state, reviewed_by, review_note, detail FROM publish_guardrails "
                       " WHERE check_name = ? AND subject = ?", (check, subject)).fetchone()
    return dict(row) if row else None


def _current(conn, content_hash):
    return dict(conn.execute("SELECT * FROM signals WHERE content_hash = ? AND is_current = 1",
                             (content_hash,)).fetchone())


# --- opening a session finding ----------------------------------------------

def test_a_session_finding_is_opened_through_record_and_resolves_nothing_else(conn):
    # An unrelated open amount finding must survive: record() with the full
    # check list would mark it resolved because it "did not fire this pass".
    other = guardrails.Finding(guardrails.AMOUNT, "0" * 32, "Other $1", "above the ceiling", 1.0)
    guardrails.record(conn, [other], checks=())
    state = adj.open_session_finding(conn, adj.PLACE, HASH, "headline and outlet look Argentine")
    assert state == {"key": f"place/{HASH}", "opened": True, "state": "open"}
    assert _state(conn, f"place/{HASH}")["detail"].startswith(adj.SESSION_LABEL + ": ")
    assert _state(conn, "amount/" + "0" * 32)["state"] == "open"


def test_an_existing_finding_is_not_reopened(conn):
    adj.open_session_finding(conn, guardrails.AMOUNT, MONEY_HASH, "why")
    guardrails.review(conn, f"amount/{MONEY_HASH}", "rejected", "a person said no", "owner")
    again = adj.open_session_finding(conn, guardrails.AMOUNT, MONEY_HASH, "why again")
    assert again == {"key": f"amount/{MONEY_HASH}", "opened": False, "state": "rejected"}


def test_only_amount_and_place_may_be_raised(conn):
    with pytest.raises(ValueError):
        adj.open_session_finding(conn, "period_totals", HASH, "why")
    with pytest.raises(ValueError, match="no current row"):
        adj.open_session_finding(conn, adj.PLACE, "f" * 32, "why")


def test_a_place_finding_never_holds_a_row_back_or_goes_overdue(conn):
    adj.open_session_finding(conn, adj.PLACE, HASH, "why")
    assert adj.PLACE not in guardrails.ROW_CHECKS and adj.PLACE not in guardrails.CHECKS
    report = guardrails.quarantine(conn, write=False)
    assert HASH not in report["quarantined"]
    assert not [r for r in report["live"] + report["held"] if r["subject"] == HASH]


def test_drop_only_removes_an_open_session_finding(conn):
    adj.open_session_finding(conn, adj.PLACE, HASH, "why")
    guardrails.review(conn, f"place/{HASH}", "accepted", "decided", "owner")
    assert adj.drop_session_finding(conn, f"place/{HASH}") == 0
    other = guardrails.Finding(guardrails.AMOUNT, MONEY_HASH, "Acme", "above the ceiling", 1.0)
    guardrails.record(conn, [other], checks=())
    assert adj.drop_session_finding(conn, f"amount/{MONEY_HASH}") == 0, "a check's finding is not ours to drop"


# --- the place verdict --------------------------------------------------------

def test_two_agreeing_place_edits_correct_the_site_first_then_revise(conn, tmp_path, stats):
    adj.open_session_finding(conn, adj.PLACE, HASH, "why")
    pushed = []
    call = _referee({A: _place(), B: _place(city="san juan")}, stats=stats)
    rc = adj.adjudicate(conn, f"place/{HASH}", apply=True, start_usd=0.0, call=call,
                        fetch=lambda url: ARTICLE_AR, wayback=lambda url: None,
                        spec_dir=tmp_path / "specs",
                        push=lambda row, fixed: pushed.append(fixed) or {})
    assert rc == 0
    assert pushed and pushed[0]["country"] == "AR"
    row = _current(conn, HASH)
    assert (row["country"], row["city"], row["state"]) == ("AR", "San Juan", None)
    assert row["region"] == validate._region_for_country("AR")
    assert row["revision"] == 2 and row["published_at"], "a revision, not an overwrite, and still live"
    assert _state(conn, f"place/{HASH}")["state"] == "accepted"
    assert adj.WHO in _state(conn, f"place/{HASH}")["reviewed_by"]
    assert "corrected_country=AR" in _state(conn, f"place/{HASH}")["review_note"]


def test_the_place_prompt_carries_the_location_rules_not_the_money_rules(conn, tmp_path, stats):
    adj.open_session_finding(conn, adj.PLACE, HASH, "why")
    call = _referee({A: _place("accept"), B: _place("accept")}, stats=stats)
    adj.adjudicate(conn, f"place/{HASH}", apply=False, start_usd=0.0, call=call,
                   fetch=lambda url: ARTICLE_AR, wayback=lambda url: None, spec_dir=tmp_path / "s")
    prompt = call.calls[0][1]
    assert "ISO 3166-1 alpha-2" in prompt
    assert 'The public "money raised" total' not in prompt, "the money rules leaked into a place question"


def test_two_place_edits_naming_different_countries_disagree(conn, tmp_path, stats):
    adj.open_session_finding(conn, adj.PLACE, HASH, "why")
    call = _referee({A: _place(country="AR"), B: _place(country="PR")}, stats=stats)
    rc = adj.adjudicate(conn, f"place/{HASH}", apply=True, start_usd=0.0, call=call,
                        fetch=lambda url: ARTICLE_AR, wayback=lambda url: None,
                        spec_dir=tmp_path / "specs", push=lambda row, fixed: {})
    assert rc == 3
    assert _current(conn, HASH)["country"] == "US"
    assert _state(conn, f"place/{HASH}")["state"] == "open"
    spec = json.loads(next((tmp_path / "specs").glob("*.json")).read_text())
    assert spec["status"] == "disagree" and spec["action"] == "keep"


def test_a_country_that_is_not_two_letters_is_not_a_verdict():
    v = adj.parse_place_verdict(json.dumps(_place(country="Argentina")))
    assert v["corrected_country"] is None
    assert adj.decide_place({A: v, B: v}) == ("disagree", "keep", None)
    assert adj.parse_place_verdict(json.dumps(_place(confidence=10))) is None


def test_a_place_reject_closes_the_finding_and_moves_nothing(conn, tmp_path, stats):
    adj.open_session_finding(conn, adj.PLACE, HASH, "why")
    call = _referee({A: _place("reject"), B: _place("reject")}, stats=stats)
    rc = adj.adjudicate(conn, f"place/{HASH}", apply=True, start_usd=0.0, call=call,
                        fetch=lambda url: ARTICLE_AR, wayback=lambda url: None,
                        spec_dir=tmp_path / "specs", push=lambda row, fixed: {})
    assert rc == 0
    assert _current(conn, HASH)["country"] == "US" and _current(conn, HASH)["revision"] == 1
    assert _state(conn, f"place/{HASH}")["state"] == "rejected"


# --- the money verdict on a session-raised row --------------------------------

def test_a_session_raised_money_row_takes_the_guardrail_path(conn, tmp_path, stats):
    adj.open_session_finding(conn, guardrails.AMOUNT, MONEY_HASH, "an investment shown as raised")
    pushed = []
    call = _referee({A: _money(), B: _money()}, stats=stats)
    rc = adj.adjudicate(conn, f"amount/{MONEY_HASH}", apply=True, start_usd=0.0, call=call,
                        fetch=lambda url: ARTICLE_MONEY, wayback=lambda url: None,
                        spec_dir=tmp_path / "specs",
                        push=lambda row, usd: pushed.append(usd) or {})
    assert rc == 0
    row = _current(conn, MONEY_HASH)
    assert row["money_basis"] == "outbound_investment" and row["funding_amount_usd"] == 59_000_000
    assert row["revision"] == 2
    assert _state(conn, f"amount/{MONEY_HASH}")["state"] == "accepted"


def test_a_rejected_live_money_row_is_flagged_for_retraction(conn, tmp_path, stats):
    adj.open_session_finding(conn, guardrails.AMOUNT, MONEY_HASH, "why")
    call = _referee({A: _money("reject"), B: _money("reject")}, stats=stats)
    rc = adj.adjudicate(conn, f"amount/{MONEY_HASH}", apply=True, start_usd=0.0, call=call,
                        fetch=lambda url: ARTICLE_MONEY, wayback=lambda url: None,
                        spec_dir=tmp_path / "specs")
    assert rc == 0
    spec = json.loads(next((tmp_path / "specs").glob("*.json")).read_text())
    assert spec["live_row_needs_retraction"] is True


# --- the run ceiling is an argument, read at call time ------------------------

def test_the_ceiling_argument_stops_the_second_referee(conn, tmp_path, stats):
    adj.open_session_finding(conn, adj.PLACE, HASH, "why")
    call = _referee({A: _place(), B: _place()}, cost=0.06, stats=stats)
    rc = adj.adjudicate(conn, f"place/{HASH}", apply=True, start_usd=0.0, call=call,
                        fetch=lambda url: ARTICLE_AR, wayback=lambda url: None,
                        spec_dir=tmp_path / "specs", push=lambda row, fixed: {}, ceiling=0.05)
    assert rc == 3 and len(call.calls) == 1
    assert _current(conn, HASH)["country"] == "US"


# --- the CLI ------------------------------------------------------------------

def test_a_dry_run_from_the_cli_leaves_the_ledger_as_it_found_it(tmp_path, monkeypatch, stats):
    db = tmp_path / "cli.db"
    c = schema.connect(db)
    _row(c, content_hash=HASH)
    c.close()
    real_connect = schema.connect
    monkeypatch.setattr(schema, "connect", lambda *a, **k: real_connect(db))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(adj, "fetch_page", lambda url, timeout=0: ARTICLE_AR)
    monkeypatch.setattr(adj, "wayback_copy", lambda url: None)
    monkeypatch.setattr(classify, "_call", _referee({A: _place(), B: _place()}, stats=stats))
    monkeypatch.setattr(adj, "SPEC_DIR", tmp_path / "specs")
    rc = adj.main(["--place-row", HASH, "--reason", "why"])
    assert rc == 0
    check = real_connect(db)
    assert check.execute("SELECT COUNT(*) FROM publish_guardrails").fetchone()[0] == 0
    assert _current(check, HASH)["country"] == "US"


def test_the_cli_requires_a_reason_for_a_session_row(monkeypatch):
    with pytest.raises(SystemExit):
        adj.main(["--row", HASH])


def test_the_cli_files_a_priced_health_row_when_asked(tmp_path, monkeypatch, stats):
    db = tmp_path / "cli.db"
    c = schema.connect(db)
    _row(c, content_hash=HASH)
    c.close()
    real_connect = schema.connect
    monkeypatch.setattr(schema, "connect", lambda *a, **k: real_connect(db))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(adj, "fetch_page", lambda url, timeout=0: ARTICLE_AR)
    monkeypatch.setattr(adj, "wayback_copy", lambda url: None)
    monkeypatch.setattr(classify, "_call", _referee({A: _place(), B: _place()}, stats=stats))
    monkeypatch.setattr(adj, "SPEC_DIR", tmp_path / "specs")
    monkeypatch.setattr(correct_city_country, "push_place", lambda row, fixed, session=None: {})
    rc = adj.main(["--place-row", HASH, "--reason", "why", "--apply", "--health"])
    assert rc == 0
    check = real_connect(db)
    health = check.execute("SELECT collector, status, cost_usd FROM source_health WHERE collector = ?",
                           (adj.HEALTH_NAME,)).fetchone()
    assert health is not None
    assert health["cost_usd"] == pytest.approx(0.008), "the run's spend must reach the ledger, not NULL"
    assert _current(check, HASH)["country"] == "AR"


# --- the workflow -------------------------------------------------------------

def test_the_workflow_is_a_writer_the_drainer_knows_and_red_on_a_disagreement():
    from pathlib import Path

    import yaml

    import writer_queue

    root = Path(__file__).resolve().parents[1]
    text = (root / ".github/workflows/adjudicate-rows.yml").read_text()
    parsed = yaml.safe_load(text)
    assert parsed["concurrency"]["group"] == "talent-collect"
    assert "adjudicate-rows.yml" in writer_queue.lock_group_workflows()
    triggers = (parsed.get("on") or parsed.get(True))
    assert "schedule" not in triggers, "a session-raised adjudication is never scheduled"
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["default"] is True
    assert "--apply" in text and 'inputs.dry_run }}" = "false"' in text
    assert "rc == '3'" in text and "exit 1" in text, "a disagreement must red the run once"
    assert "TIT_RUN_KIND: discretionary" in text
    assert "--health" in text, "the spend must reach the committed cost ledger"
