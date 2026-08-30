"""The standing assertion has to be able to FAIL, and it could not.

`correct_money_basis.py --check` is the guard CLAUDE.md points at for "no live
row carries an unjudged figure". Until 2026-08-30 it asked the committed
database, which is where the pipeline keeps its own judgement and is not where a
number gets published. It answered "every live figure has been judged", exit 0,
while the site's /aggregate reported `money.coverage.unjudged = 2`.

A guard that cannot go red on the defect it names is not a guard, so these tests
are written as MUTATIONS: each one puts the defect somewhere real and asserts the
verdict moves. The two that matter most are the ones about ABSENCE -- a site that
cannot be reached, and a plugin too old to report the key -- because both are
states a `coverage.get("unjudged", 0)` would have rendered as a clean corpus.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import correct_money_basis as cmb
from pipeline import schema

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wordpress-plugin/talent-intelligence-tracker"


# --- Stubs -----------------------------------------------------------------

class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Session:
    """A site that answers whatever the test says it answers.

    The suite never reaches the network (see tests/conftest.py); every live read
    here is this object.
    """

    def __init__(self, payload, status=200, boom: Exception | None = None):
        self._payload, self._status, self._boom = payload, status, boom
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self._boom is not None:
            raise self._boom
        return _Response(self._payload, self._status)


def _site_says(unjudged, **extra):
    coverage = {"with": 4300, "all": 4828}
    if unjudged is not ...:
        coverage["unjudged"] = unjudged
    coverage.update(extra)
    return {"money": {"total": 1.0, "coverage": coverage}}


@pytest.fixture
def conn(tmp_path):
    """A real file, not ':memory:' -- schema.connect ATTACHes a second database
    derived from the path, so an in-memory connection writes a literal
    ':memory:_cache' file into the checkout."""
    return schema.connect(str(tmp_path / "t.db"))


def _row(conn, **over):
    """One live row, published, carrying a figure."""
    fields = {
        "signal_id": "s1", "company": "Acme", "company_key": "acme",
        "headline": "Acme raises $70M", "summary": "Acme has raised $70M.",
        "talent_readthrough": "Capital changes the hiring picture.",
        "pillar": "company_development", "signal_direction": "neutral",
        "confidence": "reported", "source_url": "https://example.com/s",
        "source_name": "Example", "captured_at": "2026-08-01T00:00:00Z",
        "as_of": "2026-08-01", "collector": "google_news",
        "is_current": 1, "funding_amount": "$70M",
        "funding_amount_usd": 70_000_000, "money_basis": "company_raise",
        "published_at": "2026-08-01T00:00:00Z", "content_hash": "h1",
    }
    fields.update(over)
    cols = ", ".join(fields)
    conn.execute(f"INSERT INTO signals ({cols}) VALUES "
                 f"({', '.join('?' * len(fields))})", tuple(fields.values()))
    conn.commit()


# --- THE MUTATION: an unjudged figure on the live site ----------------------

def test_it_goes_red_when_the_live_corpus_carries_an_unjudged_figure(conn):
    """THE DEFECT OF 2026-08-30, reproduced.

    The committed database is spotless and the site is not. This is the exact
    pair of readings the old --check scored as a pass.
    """
    _row(conn)
    verdict, lines = cmb.check(conn, session=_Session(_site_says(2)))
    assert verdict == cmb.FAIL
    assert cmb.EXIT[verdict] == 1
    body = "\n".join(lines)
    assert "PASS  pipeline" in body, (
        "the pipeline half must still read clean, or this test is passing for "
        "the wrong reason and proves nothing about the live half")
    assert "2 published row(s)" in body


def test_it_stays_green_when_the_site_agrees_with_the_pipeline(conn):
    """The other half of the mutation. A guard that is always red is also
    useless, and 'it went red' means nothing without this."""
    _row(conn)
    verdict, lines = cmb.check(conn, session=_Session(_site_says(0)))
    assert verdict == cmb.PASS
    assert cmb.EXIT[verdict] == 0
    assert "PASS  site" in "\n".join(lines)


def test_it_goes_red_when_the_committed_database_carries_one(conn):
    """The half that already worked, pinned so the rewrite did not lose it."""
    _row(conn, money_basis=None)
    verdict, lines = cmb.check(conn, session=_Session(_site_says(0)))
    assert verdict == cmb.FAIL
    assert "FAIL  pipeline" in "\n".join(lines)


# --- ABSENCE IS NOT A PASS --------------------------------------------------

def test_an_unreachable_site_is_unknown_and_never_a_pass(conn):
    _row(conn)
    verdict, lines = cmb.check(
        conn, session=_Session(None, boom=RuntimeError("tunnel refused")))
    assert verdict == cmb.UNKNOWN
    assert cmb.EXIT[verdict] == 3
    assert "NOT a pass" in "\n".join(lines)


def test_a_plugin_too_old_to_report_the_key_is_unknown_not_zero(conn):
    """THE SILENT PASS THIS PROJECT REFUSES.

    `coverage.get("unjudged", 0)` reads a site that has never heard of
    tit_money_unjudged_where() as a corpus with nothing wrong in it. The key is
    read BY NAME for the same reason tit_money_where() asks for `company_raise`
    by name: absence must not resolve to the reassuring value.
    """
    _row(conn)
    with pytest.raises(cmb.LiveUnavailable):
        cmb.live_unjudged(session=_Session(_site_says(...)))
    verdict, _ = cmb.check(conn, session=_Session(_site_says(...)))
    assert verdict == cmb.UNKNOWN


def test_a_response_with_no_money_block_is_unknown(conn):
    """tit_aggregate_money() returns null when tit_money_aggregate() is absent."""
    _row(conn)
    verdict, _ = cmb.check(conn, session=_Session({"money": None}))
    assert verdict == cmb.UNKNOWN


@pytest.mark.parametrize("value", [None, "", "many", True, [], {}])
def test_a_non_numeric_count_is_unknown(value):
    with pytest.raises(cmb.LiveUnavailable):
        cmb.live_unjudged(session=_Session(_site_says(value)))


def test_offline_does_not_grant_a_pass(conn):
    """`--offline` is a way to run the local half, not a way to get a green."""
    _row(conn)
    verdict, lines = cmb.check(conn, offline=True)
    assert verdict == cmb.UNKNOWN
    assert "NOT consulted" in "\n".join(lines)


def test_fail_wins_over_unknown(conn):
    """backup_check.py's rule. If something is definitely wrong, that is the
    answer, whatever else could not be read."""
    _row(conn, money_basis=None)
    verdict, _ = cmb.check(conn, session=_Session(None, boom=OSError("down")))
    assert verdict == cmb.FAIL


# --- The two corpora must keep asking the same question ---------------------

def test_the_two_corpora_share_one_predicate():
    """`unjudged()` here and `tit_money_unjudged_where()` there.

    Asserted on the SQL of both, because the whole value of the live read is
    that it counts the SAME rows this script would. If the PHP drifted to, say,
    `money_basis = ''`, the site would report zero forever and this guard would
    faithfully relay it.
    """
    php = (PLUGIN / "includes/api.php").read_text()
    assert "function tit_money_unjudged_where()" in php
    where = php.split("function tit_money_unjudged_where()", 1)[1].split("}", 1)[0]
    assert "funding_amount_usd IS NOT NULL" in where
    assert "money_basis IS NULL" in where

    import inspect
    sql = inspect.getsource(cmb.unjudged)
    assert "funding_amount_usd IS NOT NULL" in sql
    assert "money_basis IS NULL" in sql
    assert "is_current = 1" in sql


def test_the_check_reads_the_published_corpus_not_only_the_committed_one():
    """The regression that started all of this.

    A `check()` that never builds a request is the version that shipped, and it
    passed its own test suite because it had none. This asserts the network read
    HAPPENS, by counting it.
    """
    import inspect
    source = inspect.getsource(cmb.check)
    assert "live_unjudged" in source, (
        "check() no longer consults the live corpus -- this is the 2026-08-30 "
        "defect returning")


def test_the_live_read_targets_aggregate(conn):
    _row(conn)
    session = _Session(_site_says(0))
    cmb.check(conn, site="https://example.test/blog", session=session)
    assert session.calls == ["https://example.test/blog/wp-json/talent/v1/aggregate"]


def test_the_site_base_is_never_the_bare_domain(conn):
    """WP_SITE_URL is `.../blog`; the bare domain is a different application."""
    assert cmb.DEFAULT_SITE.endswith("/blog")


# --- Wiring: the guard has to be stood up by something ----------------------

WORKFLOWS = ROOT / ".github/workflows"


def test_a_workflow_actually_runs_the_check():
    """THE OTHER HALF OF THE 2026-08-30 FINDING.

    The script was referenced by no workflow and no test: `grep -rln
    correct_money_basis .github/workflows/ tests/` matched nothing. An assertion
    nothing stands up is a comment.
    """
    runners = [p for p in WORKFLOWS.glob("*.yml")
               if "correct_money_basis.py --check" in p.read_text()]
    assert runners, ("no workflow runs `correct_money_basis.py --check`; the "
                     "standing assertion is not standing")


def test_the_check_workflow_is_scheduled():
    import yaml
    parsed = yaml.safe_load((WORKFLOWS / "money-basis-check.yml").read_text())
    triggers = parsed.get("on") or parsed.get(True) or {}
    assert triggers.get("schedule"), (
        "a guard that only runs when someone remembers to press it is the "
        "state this replaced")
    assert any(e.get("cron") for e in triggers["schedule"])


def test_the_check_workflow_is_a_reader_and_holds_no_lock():
    """It writes nothing, so it must not enter `talent-collect`.

    A reader in the writers' group takes a slot in a queue that keeps exactly
    one pending run, which is how drain-writers.yml's evictions happen.
    """
    text = (WORKFLOWS / "money-basis-check.yml").read_text()
    assert "group: talent-collect" not in text
    assert "--enrich" not in text and "--apply" not in text
    assert "merge_db.py" not in text, "a reader must not commit a database"


def test_the_correction_is_a_writer_and_has_no_schedule_of_its_own():
    """Every database writer enters the lock ONLY through drain-writers.yml.

    A `schedule:` here would be an uncoordinated entry into the group, which is
    exactly what evicts the single pending run and produces the unreplayable
    orphans drain-writers exists to stop.
    """
    import yaml
    path = WORKFLOWS / "correct-money-basis.yml"
    assert "group: talent-collect" in path.read_text()
    parsed = yaml.safe_load(path.read_text())
    # PyYAML reads a bare `on:` key as the boolean True.
    triggers = parsed.get("on") or parsed.get(True) or {}
    assert "schedule" not in triggers, (
        "a writer with its own schedule enters the lock without the queue")
    assert "workflow_dispatch" in triggers


def test_the_drainer_watches_the_new_writer():
    """drain-writers' fast path is a list of workflow NAMES. A writer missing
    from it is drained only by the 15-minute cron, so the queue behind it stalls
    for up to a quarter of an hour with nothing saying why."""
    drainer = (WORKFLOWS / "drain-writers.yml").read_text()
    name = None
    for line in (WORKFLOWS / "correct-money-basis.yml").read_text().splitlines():
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
            break
    assert name, "the writer workflow has no name"
    assert f"- {name}" in drainer, (
        f"{name!r} is not in drain-writers.yml's workflow_run list")
