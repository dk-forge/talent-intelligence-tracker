"""A CLOSED WALLET IS NOT AN UNREAD PUBLISHER, and the press chain died on it.

THE INCIDENT, 2026-08-05, run 30982514410.

PR #10 put the backfills under `spend.py --degrade` so a discretionary job could
not spend the scheduled collectors' allowance. Its promise, in its own words,
was that `--degrade` "always exits 0" and "can never fail a backfill step": the
free fetch, the prefilter and every `cheap_extract` close keep running, and a
candidate that cannot be paid for is left UNMARKED for a later pass.

That is not what happened. With the month's allowance spent, `classify.classify`
raised `BudgetExhausted` on the FIRST candidate it was handed, and
`backfill_press_2026` broke out of the candidate loop and then out of the
PUBLISHER loop. The run walked 2 of the 40 publishers in roster index 0 and
stopped. An index that is 2/40 walked is not finished, so `roster_progress` left
`done_through` at None, the emitted ticket carried a `next_cursor` equal to the
cursor the run started from, and `backfill_slices.record` did exactly the right
thing: it refused to requeue a chain that had made no progress, and went red.

So the degrade reddened the run AND stopped the backfill, and it would have done
so on every future run for as long as the allowance stayed spent: each one stops
at the same first paid candidate. A backfill that looks like it ran and did 1/N
of the work, forever.

WHAT IS PINNED HERE is the distinction the loop was missing. Fetching,
prefiltering and free extraction cost nothing and still run, so the publishers
ARE read and the cursor may honestly pass them. What is lost is DEPTH, and depth
already had a name in this walker: `rationed_off`, the candidates past the cut,
left unmarked so a later pass reads them. A budget-deferred candidate is the
same thing for the same reason, and the fix is to treat it that way rather than
as a wall.

None of the guards move. The runaway guard in `record` still refuses a cursor
that did not move. `roster_progress` still refuses to pass an index whose every
publisher failed at the transport layer. What changes is only that a shut wallet
stops being mistaken for either of those.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import backfill_press_2026 as walker
import backfill_slices
from collectors import national_press

FIXED = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)

#: Small enough to run in a test, large enough that "it stopped after the first
#: publisher" and "it walked the index" are different numbers.
PER_SLICE = 4


def _feed(i: int):
    return national_press.Feed(
        name=f"pub{i:03d}", rss=f"https://p{i}.test/feed", country="X",
        city="", coverage="National", language="English",
        source_type="News Organization", site=f"https://p{i}.test")


def _item(pub: str, n: int) -> dict:
    return {
        "raw_text": f"Northwind Systems appoints a chief executive and plans "
                    f"to hire staff for a new office. Story {pub}-{n}.",
        "headline": f"Northwind Systems appoints a chief executive ({pub}-{n})",
        "source_url": f"https://{pub}.test/business/story-{n}",
        "source_name": pub,
        "published_date": "2026-03-02",
        "collector": walker.COLLECTOR,
    }


class _Wallet:
    """The paid path, and a count of how often it was actually attempted."""

    def __init__(self, exc: type[BaseException] | None):
        self.exc = exc
        self.calls = 0

    def __call__(self, item, **kw):
        self.calls += 1
        if self.exc is not None:
            raise self.exc("the month's allowance is spent "
                           "(TIT_PAID_READS=off, set by spend.py --degrade)")
        return None


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """Everything past the control flow, stubbed at the module attribute.

    The filters are stubbed deliberately: what is under test is the LOOP, not
    `prefilter` or `validate`, both of which have their own tests. Every
    candidate therefore reaches the gate, which is the only place the wallet is
    consulted.
    """
    monkeypatch.setenv("TIT_GATE_LEDGER", "off")
    conn = walker.schema.connect(tmp_path / "t.db")
    monkeypatch.setattr(walker.schema, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(walker.publish, "publish", lambda *a, **k: None)
    monkeypatch.setattr(walker.prefilter, "passes", lambda _t: (True, ""))
    monkeypatch.setattr(walker.validate, "precheck", lambda _i: None)
    monkeypatch.setattr(walker.store, "already_seen", lambda *a, **k: False)
    monkeypatch.setattr(walker.cheap_extract, "parse_funding", lambda _i: None)
    monkeypatch.setattr(walker.candidate_rank, "rank", lambda items, _ctx: items)
    monkeypatch.setattr(walker.candidate_rank.Context, "for_conn",
                        staticmethod(lambda _c: None))

    # Two candidates per publisher, and every publisher answers. There is no
    # transport failure anywhere in this fixture, so `roster_progress` has no
    # reason of its own to hold the cursor back: anything that holds it back is
    # the thing under test.
    def read_publisher(feed, lo, hi, **kw):
        record = {"name": feed.name, "country": feed.country,
                  "site": feed.site, "status": "ok", "urls": 9,
                  "heads": 2, "items": 2, "detail": ""}
        return [_item(feed.name, 0), _item(feed.name, 1)], record

    monkeypatch.setattr(walker.press_archive, "read_publisher", read_publisher)
    monkeypatch.setattr(walker.press_archive, "_report", lambda *a, **k: None)
    monkeypatch.setattr(walker.press_archive, "PUBLISHER_HEALTH", [])
    monkeypatch.setattr(walker, "load_feeds",
                        lambda *a, **k: [_feed(i) for i in range(PER_SLICE * 3)])
    return conn


def _run(monkeypatch, argv):
    monkeypatch.setattr(walker.sys, "argv", ["backfill_press_2026.py"] + argv)
    return walker.main()


def _slice(monkeypatch, tmp_path, state, ticket, extra=()):
    return _run(monkeypatch, [
        "--start", "2026-01-01", "--end", "2026-06-30", "--slice",
        "--publishers-per-slice", str(PER_SLICE), "--ration", "100",
        "--state", str(state), "--emit-next", str(ticket), *extra])


# --------------------------------------------------------------------------
# the defect
# --------------------------------------------------------------------------

@pytest.mark.parametrize("wall", ["budget", "credits"])
def test_a_shut_wallet_still_finishes_the_roster_index_and_advances(
        offline, monkeypatch, tmp_path, capsys, wall):
    """THE property. A degraded slice must walk its whole index and requeue.

    Before the fix this walked 1 publisher of PER_SLICE and emitted a cursor
    that had not moved, which `record` refuses to requeue: the chain ended on
    its first slice, red, and every retry reproduced it exactly.
    """
    exc = (walker.classify.BudgetExhausted if wall == "budget"
           else walker.classify.CreditsExhausted)
    wallet = _Wallet(exc)
    monkeypatch.setattr(walker.classify, "classify", wallet)
    monkeypatch.setattr(walker.cheap_extract, "extract", lambda _i: None)

    state, ticket = tmp_path / "state.json", tmp_path / "slice.json"
    assert _slice(monkeypatch, tmp_path, state, ticket) == 0, (
        "a spend degrade must still exit 0")

    emitted = json.loads(ticket.read_text())
    assert emitted["slice"] == "0..0"
    assert emitted["next_cursor"] == "1", (
        "the wallet shut and the walker treated it as a wall: the cursor did "
        "not move past roster index 0, so `record` will refuse to requeue and "
        "the backfill stops after one slice")
    assert emitted["totals"]["publishers"] == PER_SLICE, (
        f"only {emitted['totals']['publishers']} of {PER_SLICE} publishers were "
        f"walked; fetching and free extraction cost nothing and must continue")
    assert emitted["totals"]["left_unread"] == PER_SLICE * 2, (
        "every candidate the shut wallet could not pay for must be COUNTED")

    # And `record` accepts it, which is the whole point of the exercise.
    loaded = backfill_slices.load(state)
    result = backfill_slices.record(loaded, emitted, now=FIXED)
    assert result["advanced"], result.get("problem")
    assert result["job"]["state"] == "running"


def test_the_paid_path_is_attempted_once_and_then_latched(
        offline, monkeypatch, tmp_path):
    """Continuing the walk must not mean re-asking a wallet that is shut.

    A retry per candidate would be a 402 storm against OpenRouter for the rest
    of the month, which is a worse failure than the one being fixed.
    """
    wallet = _Wallet(walker.classify.BudgetExhausted)
    monkeypatch.setattr(walker.classify, "classify", wallet)
    monkeypatch.setattr(walker.cheap_extract, "extract", lambda _i: None)

    state, ticket = tmp_path / "state.json", tmp_path / "slice.json"
    _slice(monkeypatch, tmp_path, state, ticket)
    assert wallet.calls == 1, (
        f"the gate was called {wallet.calls} times after the budget closed it; "
        f"it must be latched on the first refusal")
    # ...and latched is not the same as STOPPED, which is the whole defect: the
    # pre-fix walker also called the gate exactly once, by ending the run. This
    # assertion is what makes the one above mean anything.
    emitted = json.loads(ticket.read_text())
    assert emitted["totals"]["publishers"] == PER_SLICE, (
        "the gate was called once because the walk ended, not because the "
        "wallet was latched")


def test_a_deferred_candidate_is_left_unmarked_so_a_later_pass_reads_it(
        offline, monkeypatch, tmp_path):
    """The coverage half of the promise. Marking it seen would make the shut
    wallet permanent: `store.already_seen` skips it for free forever after."""
    monkeypatch.setattr(walker.classify, "classify",
                        _Wallet(walker.classify.BudgetExhausted))
    monkeypatch.setattr(walker.cheap_extract, "extract", lambda _i: None)
    marked = []
    monkeypatch.setattr(walker.store, "mark_seen",
                        lambda conn, url, c, o: marked.append((url, o)))

    state, ticket = tmp_path / "state.json", tmp_path / "slice.json"
    _slice(monkeypatch, tmp_path, state, ticket)
    assert marked == [], (
        f"{len(marked)} deferred candidate(s) were marked seen, so no later "
        f"pass will ever read them: {marked[:3]}")
    # Measured over the WHOLE index, not over the one publisher a stopped walk
    # reached. Unmarked-because-we-stopped is not the property being claimed.
    emitted = json.loads(ticket.read_text())
    assert emitted["totals"]["left_unread"] == PER_SLICE * 2, (
        "the unmarked candidates must be the whole index's worth, and counted")


def test_free_extraction_keeps_storing_after_the_wallet_shuts(
        offline, monkeypatch, tmp_path):
    """`--degrade` loses DEPTH, not coverage. A record whose headline states
    every field closes for $0 and must still land."""
    wallet = _Wallet(walker.classify.BudgetExhausted)
    monkeypatch.setattr(walker.classify, "classify", wallet)

    # The first candidate cannot be closed for free, so it shuts the wallet.
    # Everything after it can, and must still be stored.
    seen: list[str] = []

    def cheap(item):
        seen.append(item["source_url"])
        return None if len(seen) == 1 else {"free": True}

    stored: list[str] = []
    monkeypatch.setattr(walker.cheap_extract, "extract", cheap)
    monkeypatch.setattr(walker.validate, "build_signal",
                        lambda *a, **k: _Signal())
    monkeypatch.setattr(walker.store, "mark_seen", lambda *a, **k: None)
    monkeypatch.setattr(walker.store, "store",
                        lambda conn, sig: stored.append(sig.headline) or "stored")

    state, ticket = tmp_path / "state.json", tmp_path / "slice.json"
    _slice(monkeypatch, tmp_path, state, ticket)

    assert wallet.calls == 1
    assert len(stored) == PER_SLICE * 2 - 1, (
        f"only {len(stored)} free closes landed; a shut wallet must not stop "
        f"the extraction that costs nothing")
    emitted = json.loads(ticket.read_text())
    assert emitted["next_cursor"] == "1"


class _Signal:
    headline = "Northwind Systems appoints a chief executive"
    country = "X"
    notes = ""


# --------------------------------------------------------------------------
# the failure mode that remains, and must stay loud
# --------------------------------------------------------------------------

def test_a_run_that_really_finishes_no_index_still_refuses_to_requeue(
        offline, monkeypatch, tmp_path, capsys):
    """The guard is not weakened. A slice stopped by the WALL CLOCK part way
    through an index still emits a cursor that has not moved, and `record`
    still refuses it. Only the wallet stopped counting as that."""
    monkeypatch.setattr(walker.classify, "classify", _Wallet(None))
    monkeypatch.setattr(walker.cheap_extract, "extract", lambda _i: None)

    # A budget that is already spent the first time it is consulted.
    monkeypatch.setattr(backfill_slices.Budget, "expired", lambda self: True)

    state, ticket = tmp_path / "state.json", tmp_path / "slice.json"
    _slice(monkeypatch, tmp_path, state, ticket)
    emitted = json.loads(ticket.read_text())
    assert emitted["next_cursor"] == "0", "the cursor passed an unwalked index"

    loaded = backfill_slices.load(state)
    result = backfill_slices.record(loaded, emitted, now=FIXED)
    assert not result["advanced"]
    assert result["job"]["state"] == "stalled"
    # And it is loud about it. A guard that holds silently is how this stopped
    # being noticed for a day.
    assert "STOPS here" in result["problem"]


def test_the_run_that_cannot_requeue_says_so_itself_and_names_the_index(
        offline, monkeypatch, tmp_path, capsys):
    """VISIBILITY. On 2026-08-05 the only explanation anywhere was `record`, one
    step later, saying "the cursor is still 0" without saying why. The run that
    caused it exited 0 and printed nothing about it."""
    monkeypatch.setattr(walker.classify, "classify", _Wallet(None))
    monkeypatch.setattr(walker.cheap_extract, "extract", lambda _i: None)
    monkeypatch.setattr(backfill_slices.Budget, "expired", lambda self: True)

    state, ticket = tmp_path / "state.json", tmp_path / "slice.json"
    _slice(monkeypatch, tmp_path, state, ticket)
    err = capsys.readouterr().err

    assert "NOT REQUEUEING" in err
    assert "roster index 0" in err
    assert f"of {PER_SLICE} publishers" in err, (
        "the message must say how far it actually got, which is the number "
        "that distinguishes a shut wallet from a blocked runner")
    assert "STOPS here" in err


def test_the_record_step_names_the_reason_instead_of_pointing_at_a_log():
    """`record` holds the ticket, and the ticket holds the reason. Printing
    "the cursor is still 0" and stopping there is what made the incident take a
    log dive to explain."""
    state = backfill_slices.empty_state()
    job = backfill_slices.open_job(
        state, workflow="backfill-press-2026.yml", unit="slices",
        start="0", end="16", slice_size=1, label="2026-01-01..2026-06-30")
    ticket = backfill_slices.slice_ticket(
        job, "0", "0", next_cursor=job["cursor"],
        stopped_early="read-through cap: the month's allowance is spent")

    result = backfill_slices.record(state, ticket, now=FIXED)
    assert not result["advanced"]
    problem = result["problem"]
    assert "read-through cap: the month's allowance is spent" in problem, (
        "the reason the run recorded on its own ticket is the one thing a "
        "human needs here, and it was being dropped")
    assert "0..0" in problem, "the problem must name the slice that stalled"


def test_a_ticket_with_no_recorded_reason_says_that_is_the_problem():
    """Three states, not two. "No reason" must never read like "no problem"."""
    state = backfill_slices.empty_state()
    job = backfill_slices.open_job(
        state, workflow="backfill-press-2026.yml", unit="slices",
        start="0", end="16", slice_size=1)
    ticket = backfill_slices.slice_ticket(job, "0", "0",
                                          next_cursor=job["cursor"])
    result = backfill_slices.record(state, ticket, now=FIXED)
    assert "recorded no reason" in result["problem"]
