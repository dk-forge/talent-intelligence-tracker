"""The archive queue may never retire a document it was never told about.

`unavailable` is the one TERMINAL state in the link ledger: `archive_candidates`
drops the URL and only a hand-written UPDATE puts it back. This file pins the
rule that makes reaching it safe — a URL goes terminal only from EVIDENCE, being
at least one round in which archive.org answered and said it holds no snapshot.

The same mistake has shipped twice in this family, both times green:

  1. an availability-API 429 read as "no snapshot exists" (fixed 2026-07-30,
     covered by tests/test_link_hygiene_throttling.py);
  2. a Save Page Now 429 that still SPENT one of the five attempts, so five
     throttled nights — an ordinary fortnight for an anonymous caller — retired
     a capturable document without one refusal on record.

No network here: every response is injected, so these are properties of the
decision rather than of archive.org's mood.
"""

from __future__ import annotations

import pytest

import archive_sources
from pipeline import schema, source_links, store, validate

URLS = [
    "https://www.calcalistech.com/one/",
    "https://www.globes.co.il/two/",
    "https://betakit.com/three/",
]


@pytest.fixture
def stocked(tmp_path):
    conn = schema.connect(tmp_path / "links.db")
    for i, url in enumerate(URLS):
        store.store(conn, validate.build_signal(
            {"company": f"Company{i}", "pillar": "company_development",
             "signal_direction": "hiring", "city": "Dublin", "country": "Ireland",
             "confidence": "reported",
             "headline": f"Company{i} to create 300 new jobs in Dublin",
             "summary": f"Company{i} will add 300 roles in Dublin.",
             "talent_readthrough": "300 engineering roles in Dublin."},
            {"raw_text": f"Company{i} to create 300 new jobs in Dublin",
             "source_url": url, "source_name": "The Irish Times",
             "published_date": "2026-07-20"},
            "national_press"))
    conn.commit()
    yield conn
    conn.close()


class _Resp:
    def __init__(self, status, payload=None, headers=None, url=""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.url = url

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Session:
    """Availability always answers a definitive miss; SPN answers from a queue."""

    def __init__(self, save_statuses):
        self.save_statuses = list(save_statuses)
        self.availability_calls = 0
        self.save_calls = 0

    def get(self, url, **kwargs):
        if url.startswith(archive_sources.AVAILABILITY):
            self.availability_calls += 1
            return _Resp(200, {"archived_snapshots": {}})
        self.save_calls += 1
        status = (self.save_statuses.pop(0) if self.save_statuses else 429)
        return _Resp(status, headers={}, url="")


class _BlindSession:
    """Availability never answers. Save Page Now would save if it were reached."""

    def __init__(self):
        self.availability_calls = 0
        self.save_calls = 0

    def get(self, url, **kwargs):
        if url.startswith(archive_sources.AVAILABILITY):
            self.availability_calls += 1
            return _Resp(429)
        self.save_calls += 1
        return _Resp(200, headers={"Content-Location": "/web/2026/x"},
                     url="https://web.archive.org/web/2026/x")


# --- the decision, in isolation --------------------------------------------

def test_terminal_needs_a_definitive_negative_not_just_spent_attempts():
    """The whole of the rule, as a table. The middle row is the bug."""
    spent = source_links.MAX_ARCHIVE_ATTEMPTS
    cases = [
        # (availability, save, attempts, probes, expected state)
        (None, None, 0, 0, "pending"),          # fresh
        (None, None, spent, 0, "pending"),      # attempts spent, NOTHING learned
        (None, None, spent, 1, "unavailable"),  # spent AND told there is nothing
        (None, "https://web.archive.org/web/1", spent, 0, "archived"),
        ("https://web.archive.org/web/2", None, spent, 0, "archived"),
    ]
    for availability, save, attempts, probes, expected in cases:
        state, _ = source_links.classify_archive_outcome(
            availability, save, attempts, probes=probes)
        assert state == expected, (availability, save, attempts, probes)


def test_a_throttled_capture_spends_no_attempt(stocked):
    """Five 429s from Save Page Now must leave the URL exactly where it was.

    Before this, each one incremented `archive_attempts`, so the fifth recorded
    'unavailable' — a document retired without one refusal to capture it on
    record, out of five green runs.
    """
    for _ in range(source_links.MAX_ARCHIVE_ATTEMPTS + 2):
        archive_sources.run(stocked, limit=10, collector=None, dry_run=False,
                            spn_max=10, spn_gap=0, deadline=999, avail_gap=0,
                            session=_Session([429] * 10), sleep=lambda _s: None)
    rows = {r["source_url"]: dict(r) for r in stocked.execute(
        "SELECT source_url, archive_state, archive_attempts, archive_probes, "
        "       archive_blind_rounds FROM source_links")}
    assert rows, "the run recorded nothing at all"
    for url, row in rows.items():
        assert row["archive_state"] == "pending", url
        assert row["archive_attempts"] == 0, url
        # It DID learn something on the free pass each round: archive.org
        # answered and said it holds nothing. That is a probe, not a blind round.
        assert row["archive_probes"] > 0, url
        assert row["archive_blind_rounds"] > 0, url


def test_a_real_refusal_to_capture_does_reach_terminal(stocked):
    """The other half: a queue that can never drain is also a defect.

    Save Page Now answering 200 with no permalink is a real "cannot capture
    this", so the attempts do get spent and the URL is eventually reported
    rather than retried forever.
    """
    for _ in range(source_links.MAX_ARCHIVE_ATTEMPTS):
        archive_sources.run(stocked, limit=10, collector=None, dry_run=False,
                            spn_max=10, spn_gap=0, deadline=999, avail_gap=0,
                            session=_Session([200] * 10), sleep=lambda _s: None)
    states = {r[0] for r in stocked.execute(
        "SELECT archive_state FROM source_links")}
    assert states == {"unavailable"}, states


# --- pacing ----------------------------------------------------------------

def test_a_blinded_free_pass_stops_instead_of_walking_the_whole_queue(stocked):
    """A closed door is not worked through at two requests a second.

    The old behaviour walked every candidate at the flat gap, learned nothing
    about any of them, and spent the deadline proving archive.org was still
    refusing. Now the gap backs off and the pass ends on a long enough streak.
    """
    slept: list[float] = []
    session = _BlindSession()
    result = archive_sources.run(
        stocked, limit=10, collector=None, dry_run=False, spn_max=10,
        spn_gap=0, deadline=999, avail_gap=0.5,
        session=session, sleep=slept.append)

    assert session.save_calls == 0, "a non-answer must never reach pass 2"
    assert result["unknown"] == session.availability_calls
    assert result["archived"] == 0
    # Backoff: each successive wait is longer, and capped.
    waits = [s for s in slept if s]
    assert waits == sorted(waits), waits
    assert max(waits) <= archive_sources.AVAIL_BACKOFF_MAX
    # And nothing was recorded on the strength of it.
    for row in stocked.execute("SELECT * FROM source_links"):
        assert row["archive_state"] == "pending"
        assert row["archive_attempts"] == 0
        assert (row["archive_probes"] or 0) == 0
        assert row["archive_blind_rounds"] >= 1


def test_the_free_pass_gives_up_on_a_long_unbroken_blind_streak(tmp_path):
    """With more candidates than the streak ceiling, the pass ends early."""
    conn = schema.connect(tmp_path / "many.db")
    for i in range(archive_sources.AVAIL_BLIND_STREAK_MAX + 6):
        store.store(conn, validate.build_signal(
            {"company": f"Co{i}", "pillar": "company_development",
             "signal_direction": "hiring", "city": "Dublin", "country": "Ireland",
             "confidence": "reported",
             "headline": f"Co{i} to create 300 new jobs in Dublin",
             "summary": f"Co{i} will add 300 roles in Dublin.",
             "talent_readthrough": "300 roles in Dublin."},
            {"raw_text": f"Co{i} to create 300 new jobs in Dublin",
             "source_url": f"https://example{i}.com/story/",
             "source_name": "The Irish Times", "published_date": "2026-07-20"},
            "national_press"))
    conn.commit()
    session = _BlindSession()
    result = archive_sources.run(conn, limit=500, collector=None, dry_run=False,
                                spn_max=10, spn_gap=0, deadline=999,
                                avail_gap=0, session=session,
                                sleep=lambda _s: None)
    assert session.availability_calls == archive_sources.AVAIL_BLIND_STREAK_MAX
    assert result["unexamined"] == 6
    conn.close()


# --- the recheck pass ------------------------------------------------------

def test_reset_puts_back_only_the_urls_that_went_terminal_while_blind(stocked):
    ledger = [
        # url, state, probes, archive_url  -> should it come back?
        (URLS[0], "unavailable", 0, "", True),    # retired while blind
        (URLS[1], "unavailable", 3, "", False),   # retired on real negatives
        (URLS[2], "archived", 0, "https://web.archive.org/web/1/x", False),
    ]
    for url, state, probes, permalink, _ in ledger:
        source_links.record_archive(stocked, url, state=state,
                                    archive_url=permalink,
                                    attempts=source_links.MAX_ARCHIVE_ATTEMPTS,
                                    probes=probes)
    stocked.commit()

    moved = source_links.reset_blinded_terminal(stocked)
    assert moved == [URLS[0]]

    rows = {r["source_url"]: dict(r) for r in stocked.execute(
        "SELECT source_url, archive_state, archive_attempts FROM source_links")}
    assert rows[URLS[0]]["archive_state"] == "pending"
    assert rows[URLS[0]]["archive_attempts"] == 0
    assert rows[URLS[1]]["archive_state"] == "unavailable"
    assert rows[URLS[2]]["archive_state"] == "archived"

    # Idempotent: a second pass finds nothing, so it is safe in a tool.
    assert source_links.reset_blinded_terminal(stocked) == []


def test_a_reset_url_re_enters_the_candidate_list(stocked):
    source_links.record_archive(stocked, URLS[0], state="unavailable",
                                attempts=source_links.MAX_ARCHIVE_ATTEMPTS,
                                probes=0)
    stocked.commit()
    before = {r["source_url"] for r in
              source_links.archive_candidates(stocked, limit=50)}
    assert URLS[0] not in before, "terminal means terminal until it is reset"

    source_links.reset_blinded_terminal(stocked)
    after = {r["source_url"] for r in
             source_links.archive_candidates(stocked, limit=50)}
    assert URLS[0] in after


def test_a_never_answered_url_outranks_one_already_confirmed_absent(stocked):
    """The ordering is what stops the tail starving when `limit` binds.

    `pending` was always re-examined; what it was not is REACHED. A URL nobody
    has ever had an answer about sorts ahead of one archive.org has explicitly
    said it does not hold, because the second is a known gap awaiting a capture
    and the first is a gap in what we know.
    """
    # URLS[0] is the NEWEST capture, so a pure newest-first order puts it first.
    source_links.record_archive(stocked, URLS[0], state="pending", probes=4)
    stocked.commit()
    order = [r["source_url"] for r in
             source_links.archive_candidates(stocked, limit=50)]
    assert order[-1] == URLS[0], order
    assert set(order[:2]) == {URLS[1], URLS[2]}


def test_the_gap_is_reported_split_never_as_one_percentage(stocked):
    source_links.record_archive(stocked, URLS[0], state="pending", probes=2)
    source_links.record_archive(stocked, URLS[1], state="pending", probes=0,
                                blind_rounds=3)
    stocked.commit()
    split = source_links.archive_gap(stocked)
    assert split["probed_absent"] == 1
    # URLS[1] (blind) and URLS[2] (no ledger row at all).
    assert split["never_probed"] == 2
    assert split["blind_recently"] == 1
    assert split["terminal_blind"] == 0


def test_ops_status_goes_red_on_a_wrongly_terminal_url(stocked, capsys):
    """A terminal-while-blind row must be loud, not a line in a table.

    It cannot happen again through the code, so if one appears it is either
    pre-fix history or a third route into the same bug, and both need a human.
    """
    import ops_status

    source_links.record_archive(stocked, URLS[0], state="unavailable",
                                attempts=source_links.MAX_ARCHIVE_ATTEMPTS,
                                probes=0)
    stocked.commit()
    problems = ops_status._report_link_rot(stocked)
    capsys.readouterr()
    assert any("TERMINAL" in p and "--recheck-terminal" in p for p in problems), \
        problems


def test_the_probed_tier_is_walked_oldest_round_first_so_a_limit_reaches_everyone(stocked):
    """Tiering fixed WHICH tier starves; this fixes starvation INSIDE the tier.

    Within `pending`, newest-capture order under a bound `limit` re-examines
    the same recent window every pass and never reaches the older rows, which
    is how 1,841 in-scope URLs sat past the 7-day promise while every archive
    run reported success (2026-09-04). The probed tier is a round robin on the
    ledger's `updated_at`: the URL whose last round is oldest is next.
    """
    source_links.record_archive(stocked, URLS[0], state="pending", probes=1)
    source_links.record_archive(stocked, URLS[1], state="pending", probes=1)
    stocked.execute(
        "UPDATE source_links SET updated_at = '2026-01-01T00:00:00+00:00' "
        " WHERE source_url = ?", (URLS[1],))
    stocked.commit()
    order = [r["source_url"] for r in
             source_links.archive_candidates(stocked, limit=50)]
    # URLS[2] was never probed and still leads; then the OLDEST round, then
    # the newest capture, which pure newest-first would have put second.
    assert order == [URLS[2], URLS[1], URLS[0]], order
