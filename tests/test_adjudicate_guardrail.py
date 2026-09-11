"""Two referees, one ledger write, and the owner hears only about disagreements.

Every test here is offline: the model is a stub handed in through `call`, the
evidence is a stub handed in through `fetch`, and the database is a tmp_path.
Nothing stubs a real module into sys.modules (CLAUDE.md, "Test gotcha").
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import adjudicate_guardrail as adj
from pipeline import classify, guardrails, schema, validate

HEADLINE = "Acme valued at $30 billion after new funding"
# The real fingerprint, because corrected_signal() asserts the hash is unmoved.
HASH = validate.content_hash("acme", "company_development", "2026-09-01", HEADLINE, "Example")
KEY = f"amount/{HASH}"
A, B = adj.REFEREES
# Long enough to clear EVIDENCE_MIN_CHARS: a real article, not a stub.
ARTICLE = ("Acme raised $30 billion in a Series F led by Example Capital, the company "
           "said on Monday, in a round that values it at $90 billion. ") * 12
assert len(ARTICLE) >= adj.EVIDENCE_MIN_CHARS


def _row(conn, **kw):
    fields = {
        "signal_id": kw.get("signal_id", kw["content_hash"]),
        "headline": kw.get("headline", HEADLINE),
        "summary": "s", "talent_readthrough": "t",
        "company": kw.get("company", "Acme"),
        "company_key": kw.get("company_key", "acme"),
        "pillar": "company_development",
        "signal_direction": "neutral",
        "confidence": "reported",
        "source_url": kw.get("source_url", "https://example.com/x"),
        "source_name": "Example",
        "captured_at": "2026-09-01", "as_of": "2026-09-01",
        "content_hash": kw["content_hash"],
        "collector": "google_news",
        "published_date": "2026-09-01",
        "funding_amount": kw.get("funding_amount", "$30 billion"),
        "funding_amount_usd": kw.get("funding_amount_usd", 30_000_000_000),
        "money_basis": "company_raise",
        "published_at": kw.get("published_at"),
        "is_current": 1,
    }
    cols = ", ".join(fields)
    conn.execute(f"INSERT INTO signals ({cols}) VALUES ({', '.join('?' * len(fields))})",
                 tuple(fields.values()))


def _finding(conn, key=KEY, label="Acme $30,000,000,000", value=30e9):
    check, _, subject = key.partition("/")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO publish_guardrails (check_name, subject, label, detail, value, "
        " state, first_seen, last_seen, seen) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, 1)",
        (check, subject, label, "above the ceiling", value, now, now))
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = schema.connect(tmp_path / "adj.db")
    _row(c, content_hash=HASH)
    _finding(c)
    yield c
    c.close()


@pytest.fixture
def stats():
    saved = dict(classify.STATS)
    yield classify.STATS
    classify.STATS.clear()
    classify.STATS.update(saved)


def _verdict(recommended="accept", amount=None, basis=None, reasoning="'raised $30 billion'"):
    return {"amount_stated_for_this_event": recommended != "reject",
            "money_basis": basis or "company_raise", "recommended": recommended,
            "corrected_amount": amount, "corrected_basis": basis,
            "confidence": 90, "reasoning": reasoning}


def _referee(answers: dict, cost=0.004, stats=None):
    """A `call` that answers per model and meters like the real door does."""
    calls = []

    def call(model, system, user, *, timeout, max_tokens=None, json_mode=True):
        calls.append(model)
        if stats is not None:
            stats["usd"] = float(stats.get("usd", 0.0)) + cost
        answer = answers[model]
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)

    call.calls = calls
    return call


def _state(conn, key=KEY):
    check, _, subject = key.partition("/")
    row = conn.execute("SELECT state, reviewed_by, review_note FROM publish_guardrails "
                       " WHERE check_name = ? AND subject = ?", (check, subject)).fetchone()
    return dict(row)


def _run(conn, tmp_path, call, *, apply=True, fetch=None, stats=None, push=None):
    return adj.adjudicate(
        conn, KEY, apply=apply, start_usd=float((stats or classify.STATS).get("usd", 0.0)),
        call=call, fetch=fetch or (lambda url: ARTICLE),
        wayback=lambda url: None, spec_dir=tmp_path / "specs", push=push)


# --- agreement applies -------------------------------------------------------

def test_two_agreeing_accepts_release_the_row_through_review(conn, tmp_path, stats):
    call = _referee({A: _verdict("accept"), B: _verdict("accept")}, stats=stats)
    rc = _run(conn, tmp_path, call, stats=stats)
    assert rc == 0
    state = _state(conn)
    assert state["state"] == "accepted"
    assert state["reviewed_by"] == adj.WHO
    assert A in state["reviewed_by"] and B in state["reviewed_by"]
    assert "raised $30 billion" in state["review_note"]
    assert call.calls == [A, B], "each referee is asked exactly once on a clean answer"
    spec = json.loads(next((tmp_path / "specs").glob("*.json")).read_text())
    assert spec["status"] == "applied"
    assert set(spec["verdicts"]) == {A, B}
    assert spec["cost_usd"] == pytest.approx(0.008)
    assert spec["action"] == "accept"


def test_two_agreeing_rejects_withhold_the_row(conn, tmp_path, stats):
    call = _referee({A: _verdict("reject"), B: _verdict("reject")}, stats=stats)
    assert _run(conn, tmp_path, call, stats=stats) == 0
    assert _state(conn)["state"] == "rejected"


def test_an_agreed_edit_revises_the_row_then_accepts(conn, tmp_path, stats):
    fix = dict(amount=1_380_000_000, basis="company_raise",
               reasoning="'raised $1.38 billion at a $30 billion valuation'")
    call = _referee({A: _verdict("edit", **fix), B: _verdict("edit", **fix)}, stats=stats)
    assert _run(conn, tmp_path, call, stats=stats) == 0
    assert _state(conn)["state"] == "accepted"
    current = conn.execute("SELECT funding_amount_usd, revision, is_current, notes FROM signals "
                           " WHERE signal_id = ? AND is_current = 1", (HASH,)).fetchone()
    assert current["funding_amount_usd"] == 1_380_000_000
    assert current["revision"] == 2
    assert adj.WHO in current["notes"]
    old = conn.execute("SELECT funding_amount_usd FROM signals WHERE signal_id = ? "
                       " AND is_current = 0", (HASH,)).fetchone()
    assert old["funding_amount_usd"] == 30_000_000_000, "the original survives as history"


def test_an_agreed_basis_edit_keeps_the_figure_out_of_the_sum(conn, tmp_path, stats):
    fix = dict(amount=30_000_000_000, basis="project_finance", reasoning="'a $30 billion loan'")
    call = _referee({A: _verdict("edit", **fix), B: _verdict("edit", **fix)}, stats=stats)
    assert _run(conn, tmp_path, call, stats=stats) == 0
    current = conn.execute("SELECT money_basis, deal_type, funding_amount_usd FROM signals "
                           " WHERE signal_id = ? AND is_current = 1", (HASH,)).fetchone()
    assert current["money_basis"] == "project_finance"
    assert current["deal_type"] == "project_finance"
    assert current["funding_amount_usd"] == 30_000_000_000


def test_an_edit_on_a_live_row_pushes_the_site_first(conn, tmp_path, stats):
    conn.execute("UPDATE signals SET published_at = '2026-09-02' WHERE signal_id = ?", (HASH,))
    conn.commit()
    pushed = []
    fix = dict(amount=1_380_000_000, basis="company_raise")
    call = _referee({A: _verdict("edit", **fix), B: _verdict("edit", **fix)}, stats=stats)
    rc = _run(conn, tmp_path, call, stats=stats,
              push=lambda row, parsed: pushed.append((row["content_hash"], parsed)) or {})
    assert rc == 0
    assert pushed == [(HASH, 1_380_000_000)]
    current = conn.execute("SELECT published_at FROM signals WHERE signal_id = ? "
                           " AND is_current = 1", (HASH,)).fetchone()
    assert current["published_at"] == "2026-09-02", "the revision is already on the site"


def test_a_dry_run_writes_the_spec_and_nothing_else(conn, tmp_path, stats):
    call = _referee({A: _verdict("accept"), B: _verdict("accept")}, stats=stats)
    assert _run(conn, tmp_path, call, apply=False, stats=stats) == 0
    assert _state(conn)["state"] == "open"
    spec = json.loads(next((tmp_path / "specs").glob("*.json")).read_text())
    assert spec["status"] == "agree-dry-run"


# --- disagreement and unknown apply nothing ----------------------------------

def test_a_disagreement_writes_both_verdicts_and_exits_3(conn, tmp_path, stats):
    call = _referee({A: _verdict("accept"), B: _verdict("reject")}, stats=stats)
    assert _run(conn, tmp_path, call, stats=stats) == 3
    assert _state(conn)["state"] == "open", "nothing applied"
    spec = json.loads(next((tmp_path / "specs").glob("*.json")).read_text())
    assert spec["status"] == "disagree"
    assert spec["verdicts"][A]["recommended"] == "accept"
    assert spec["verdicts"][B]["recommended"] == "reject"
    assert spec["cost_usd"] == pytest.approx(0.008)


def test_two_edits_with_different_corrections_are_a_disagreement(conn, tmp_path, stats):
    call = _referee({A: _verdict("edit", amount=1_000_000_000, basis="company_raise"),
                     B: _verdict("edit", amount=2_000_000_000, basis="company_raise")},
                    stats=stats)
    assert _run(conn, tmp_path, call, stats=stats) == 3
    assert _state(conn)["state"] == "open"


def test_unreadable_evidence_is_unknown_and_asks_no_referee(conn, tmp_path, stats):
    call = _referee({A: _verdict("accept"), B: _verdict("accept")}, stats=stats)
    rc = _run(conn, tmp_path, call, stats=stats, fetch=lambda url: "")
    assert rc == 3
    assert call.calls == [], "no evidence, no paid call"
    assert stats["usd"] == 0.0
    assert _state(conn)["state"] == "open"
    spec = json.loads(next((tmp_path / "specs").glob("*.json")).read_text())
    assert spec["status"] == "unknown"
    assert spec["verdicts"] == {}


def test_a_non_json_answer_is_a_referee_failure_not_a_verdict(conn, tmp_path, stats):
    call = _referee({A: "I think this is fine, accept it.", B: _verdict("accept")}, stats=stats)
    assert _run(conn, tmp_path, call, stats=stats) == 3
    assert _state(conn)["state"] == "open"
    spec = json.loads(next((tmp_path / "specs").glob("*.json")).read_text())
    assert spec["status"] == "unknown"
    assert spec["verdicts"][A] is None
    assert spec["verdicts"][B]["recommended"] == "accept"


def test_a_thin_read_tries_the_next_copy_and_no_copy_is_unknown(conn, tmp_path, stats):
    """The Mistral case: a Wayback interstitial of 185 characters is not the article."""
    seen = []

    def fetch(url):
        seen.append(url)
        return "OpenAI rival Mistral valued at $24 billion as Samsung leads funding"

    call = _referee({A: _verdict("reject"), B: _verdict("reject")}, stats=stats)
    rc = adj.adjudicate(conn, KEY, apply=True, start_usd=0.0, call=call, fetch=fetch,
                        wayback=lambda url: "https://web.archive.org/web/1/x",
                        spec_dir=tmp_path / "specs")
    assert rc == 3
    assert seen == ["https://example.com/x", "https://web.archive.org/web/1/x"], (
        "the live page, then the Wayback copy, each judged against the floor")
    assert call.calls == [], "nothing was spent on evidence nobody could read"
    assert _state(conn)["state"] == "open"


def test_a_long_enough_read_wins_over_a_thin_earlier_copy(conn, tmp_path, stats):
    article = "Mistral raised 3 billion euros ($3.5 billion) at a 21 billion euro valuation. " * 20
    fetch = lambda url: ("stub" if "example.com" in url else article)  # noqa: E731
    call = _referee({A: _verdict("accept"), B: _verdict("accept")}, stats=stats)
    rc = adj.adjudicate(conn, KEY, apply=True, start_usd=0.0, call=call, fetch=fetch,
                        wayback=lambda url: "https://web.archive.org/web/1/x",
                        spec_dir=tmp_path / "specs")
    assert rc == 0
    spec = json.loads(next((tmp_path / "specs").glob("*.json")).read_text())
    assert spec["evidence_url"] == "https://web.archive.org/web/1/x"


def test_two_confidence_zero_rejects_are_blindness_not_agreement(conn, tmp_path, stats):
    blind = dict(_verdict("reject"), confidence=0, reasoning="the source text does not state it")
    call = _referee({A: blind, B: dict(blind, confidence=30)}, stats=stats)
    assert _run(conn, tmp_path, call, stats=stats) == 3
    assert _state(conn)["state"] == "open"
    spec = json.loads(next((tmp_path / "specs").glob("*.json")).read_text())
    assert spec["status"] == "unknown"
    assert spec["verdicts"] == {A: None, B: None}


def test_the_raw_cap_is_applied_after_stripping_not_before():
    """CNBC carries ~600k of CSS before the body; a cap on the raw text cut the
    page off at 'Skip Navigation' and left an unclosed tag as the evidence."""
    page = "<html><head><style>" + ("x" * 700_000) + "</style></head><body>" \
           "<a>Skip Navigation</a><header class='Nav'>menu</header><p>" \
           + ("Mistral raised 3 billion euros. " * 40) + "</p></body></html>"
    text = adj.strip_html(page[:adj.RAW_CAP])
    assert "Mistral raised 3 billion euros" in text
    assert "<" not in text
    assert adj.RAW_CAP > 700_000


def test_a_verdict_outside_the_vocabulary_is_not_a_verdict():
    assert adj.parse_verdict(json.dumps({"recommended": "maybe"})) is None
    assert adj.parse_verdict("") is None
    assert adj.parse_verdict(json.dumps({"recommended": "accept"})) is None, (
        "no confidence at all reads as confidence 0")
    ok = adj.parse_verdict(json.dumps({"recommended": "edit", "corrected_basis": "loan",
                                       "corrected_amount": "1.5e9", "confidence": 90}))
    assert ok["corrected_basis"] is None, "a basis outside pipeline.vocab is dropped"
    assert ok["corrected_amount"] == 1_500_000_000


# --- the meter -----------------------------------------------------------------

def test_a_throttled_first_attempt_is_retried_outside_the_call(conn, tmp_path, stats):
    answers = {A: [classify.Throttled("busy"), _verdict("accept")], B: [_verdict("accept")]}
    calls = []

    def call(model, system, user, *, timeout, max_tokens=None, json_mode=True):
        calls.append(model)
        stats["usd"] = float(stats.get("usd", 0.0)) + 0.004
        answer = answers[model].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer)

    assert _run(conn, tmp_path, call, stats=stats) == 0
    assert calls == [A, A, B]
    assert _state(conn)["state"] == "accepted"


def test_the_run_ceiling_stops_the_second_referee_and_decides_nothing(conn, tmp_path, stats):
    call = _referee({A: _verdict("accept"), B: _verdict("accept")},
                    cost=adj.RUN_CEILING_USD, stats=stats)
    rc = _run(conn, tmp_path, call, stats=stats)
    assert rc == 3
    assert call.calls == [A], "the ceiling is read before every request"
    assert _state(conn)["state"] == "open"
    spec = json.loads(next((tmp_path / "specs").glob("*.json")).read_text())
    assert spec["status"] == "unknown"


def test_the_months_switch_is_read_before_any_request(conn, tmp_path, stats, monkeypatch):
    monkeypatch.setenv("TIT_PAID_READS", "off")
    call = _referee({A: _verdict("accept"), B: _verdict("accept")}, stats=stats)
    assert _run(conn, tmp_path, call, stats=stats) == 3
    assert call.calls == []


def test_the_retry_never_lives_inside_the_paid_callable():
    """CLAUDE.md iron rule, held on this module's own source: every request
    the tool makes goes through one call of classify._call per attempt, and
    the attempts loop is the only loop around it."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(adj))
    ask = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "ask_referee")
    loops = [n for n in ast.walk(ask) if isinstance(n, (ast.For, ast.While))]
    assert len(loops) == 1, "exactly one attempts loop"
    gate_calls = [n for n in ast.walk(loops[0]) if isinstance(n, ast.Call)
                  and getattr(n.func, "id", "") == "_gate"]
    assert gate_calls, "the gate is read inside the loop, before every attempt"
    posts = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)
             and n.attr in ("post",) and getattr(n.value, "id", "") == "requests"]
    assert not posts, "no second door to OpenRouter"


# --- apply from an agreed spec, no model ---------------------------------------

def test_an_agreed_spec_can_be_reapplied_without_a_model(conn, tmp_path, stats):
    call = _referee({A: _verdict("reject"), B: _verdict("reject")}, stats=stats)
    assert _run(conn, tmp_path, call, apply=False, stats=stats) == 0
    spec = next((tmp_path / "specs").glob("*.json"))
    assert _state(conn)["state"] == "open"
    assert adj.apply_from_spec(conn, spec, apply=True) == 0
    assert _state(conn)["state"] == "rejected"
    assert adj.apply_from_spec(conn, spec, apply=True) == 0, "idempotent: already decided"


def test_a_disagreeing_spec_cannot_be_applied(conn, tmp_path, stats):
    call = _referee({A: _verdict("accept"), B: _verdict("reject")}, stats=stats)
    assert _run(conn, tmp_path, call, stats=stats) == 3
    spec = next((tmp_path / "specs").glob("*.json"))
    assert adj.apply_from_spec(conn, spec, apply=True) == 3
    assert _state(conn)["state"] == "open"


# --- the CLI ----------------------------------------------------------------------

def test_the_cli_refuses_to_run_without_a_key(monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(schema, "connect", lambda *a, **k: None)
    assert adj.main(["--key", KEY]) == 1
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err
