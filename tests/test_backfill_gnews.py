"""The Google News historical walker: its window, its ration, its cadence.

Offline. No network, no model, no clock dependence. Every stub here is a
monkeypatched ATTRIBUTE of a real module, never an entry in `sys.modules` — a
fake in `sys.modules` persists and shadows the real module for every test loaded
afterwards, so those tests pass alone and fail in the suite.

Three families of property live here:

  * **The window.** `after:`/`before:` are what make the archive reachable and
    `when:Nd` is what makes it unreachable. Mixing them is a recency filter
    intersected with a historical window, which is an empty set for every day
    older than the recency figure — silently, with no error and no zero to
    notice.
  * **The ration, and the reason it is not a stop.** `backfill_gdelt_2026.py`
    stops the run when its read ceiling binds and lets the cursor resume at the
    unfinished window. That shape cannot work here: a day of Google News across
    52 editions puts ~395 candidates at the gate, a budget-derived ceiling is in
    the tens, so the ceiling would bind inside window one, the run would finish
    no window, and `backfill_slices.record` would correctly refuse to requeue a
    cursor that never moved. The chain would stall on its first slice, for ever,
    at a green exit code. So the budget buys a RATION per day and a rationed
    window is FINISHED. That is the property asserted below.
  * **The cadence.** Same pairing `tests/test_backfill_pace.py` exists for: the
    cursor advances on the RUN, never on a date.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import backfill_slices
import backfill_gnews_2026 as walker

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

FIXED = datetime(2026, 3, 10, 4, 0, 0, tzinfo=timezone.utc)


# --- the window: after:/before:, one day, and the 100-item cap -------------

def test_a_historical_query_carries_after_and_before_and_never_when():
    """`when:Nd` is the daily collector's recency filter. In a historical query
    it intersects with the window and empties it, silently."""
    queries = walker.historical_queries("en", date(2026, 1, 5), date(2026, 1, 6))
    assert queries, "the English phrase pack produced no queries"
    for query in queries:
        assert "after:2026-01-05" in query
        assert "before:2026-01-06" in query
        assert "when:" not in query, (
            "a historical query carries the daily collector's recency filter. "
            "Intersected with an `after:`/`before:` window it returns nothing "
            "for every day older than the recency figure, and returns it "
            "quietly — no error, no exception, just a walk over an empty "
            "archive that reads as a quiet year.")


def test_every_edition_asks_in_its_own_language():
    """The measured trap: the same English phrases returned 23 items from the US
    edition, 2 from Germany and 0 from Brazil, while German phrasing returned 20
    from that same German edition."""
    for lang in ("de", "ja", "he"):
        english = set(walker.historical_queries("en", date(2026, 1, 5), date(2026, 1, 6)))
        native = set(walker.historical_queries(lang, date(2026, 1, 5), date(2026, 1, 6)))
        assert not (english & native), f"{lang} is being asked in English"


def test_a_locale_without_a_phrase_pack_is_refused_rather_than_defaulted():
    """Falling back to English is what makes a silent near-zero look like
    coverage. `tests/test_locale_rotation.py` refuses it for the daily rotation;
    the walker takes locales from a CLI input, so it refuses it here too."""
    assert walker.parse_locales("he:IL,ja:jp") == [("he", "IL"), ("ja", "JP")]
    with pytest.raises(SystemExit):
        walker.parse_locales("xx:ZZ")


def test_the_window_is_one_day_because_a_wider_one_is_truncated():
    """Measured 2026-07-30 on January 2026, en:US, one leadership query:
    a 31-day query returned 100 (the cap) and 31 one-day queries returned 170,
    of which the month's 100 were a strict subset. Nothing is lost by slicing
    and 70% more is found."""
    assert walker.WINDOW_DAYS == 1
    assert walker.RESULT_CAP == 100
    windows = list(walker.iter_windows(date(2026, 1, 1), date(2026, 1, 4)))
    assert [lo.isoformat() for lo, _ in windows] == [
        "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    # Half-open and contiguous: no day is walked twice and none is skipped.
    for (lo, hi), (next_lo, _) in zip(windows, windows[1:]):
        assert hi == next_lo


def test_a_query_that_comes_back_at_the_cap_is_counted_as_truncated(monkeypatch):
    """Google News offers no pagination, so a query at the cap has silently lost
    the rest of its window. The window has to get smaller, never the query
    broader — which is only actionable if the run says it happened."""
    from collections import Counter

    def fake_fetch(query, *, lang="en", country="US", timeout=30):
        n = walker.RESULT_CAP if country == "US" else 3
        return [{"discovery_url": f"https://news.google.com/{country}/{query[:6]}/{i}",
                 "raw_text": "x", "headline": "x"} for i in range(n)]

    monkeypatch.setattr(walker.google_news, "fetch", fake_fetch)
    monkeypatch.setattr(walker.time, "sleep", lambda _s: None)
    stats: Counter = Counter()
    walker.fetch_day(date(2026, 1, 1), date(2026, 1, 2),
                     [("en", "US"), ("en", "GB")], pause=0, stats=stats)
    # Derived from the phrase pack, never typed. It used to be a literal 3, and
    # widening the English funding vocabulary on 2026-07-30 turned that into a
    # failing test with nothing wrong behind it — the same shape of defect as
    # the sources page asserting a hardcoded set of five collector names.
    per_edition = len(walker.registry.GOOGLE_NEWS_VOCAB["en"])
    assert stats["truncated"] == per_edition, "every US query came back at the cap"
    assert stats["queries"] == per_edition * 2


# --- the cost model: the ration is derived, and it is a ration ------------

def test_the_ration_is_derived_from_the_budget_and_not_typed():
    """A ceiling only spend.py can stop is a ceiling that reads as a plan."""
    per_slice = walker.window_cost(
        gated=walker.DAILY_GATE_RATION * walker.SLICE_DAYS)["usd"]
    assert per_slice * 30 <= walker.MONTHLY_WALKER_BUDGET_USD * 1.05, (
        f"one slice a day at the default ration projects ${per_slice * 30:.2f} a "
        f"month against a ${walker.MONTHLY_WALKER_BUDGET_USD:.2f} allowance")
    # And it must move when the budget moves, or the two can disagree.
    doubled = max(1, int(walker.MONTHLY_WALKER_BUDGET_USD * 2 / 30
                         / walker.SLICE_DAYS / walker.USD_PER_GATED_CANDIDATE))
    assert doubled > walker.DAILY_GATE_RATION


def test_the_price_of_a_gate_call_includes_the_read_it_buys():
    """Pricing the gate alone is what makes a stated ceiling quietly untrue.

    Under a read-only ceiling the gate cost is unbounded: a year of Google News
    across 52 editions is $4.34 of gate calls before a single article is read,
    which is the whole of GDELT's year.
    """
    assert walker.USD_PER_GATED_CANDIDATE > walker.GATE_USD_PER_ITEM
    assert walker.USD_PER_GATED_CANDIDATE == pytest.approx(
        walker.GATE_USD_PER_ITEM + walker.GATE_SURVIVAL * walker.READ_USD_PER_ITEM)
    cheap = walker.window_cost(gated=100)
    dear = walker.window_cost(gated=400)
    assert dear["usd"] > cheap["usd"] > 0
    assert cheap["read_usd"] > cheap["gate_usd"], "reads still dominate a gate call"


def test_the_year_projection_says_what_the_ration_leaves_behind():
    year = walker.year_projection()
    assert year["slices"] == pytest.approx(366 / walker.SLICE_DAYS, rel=0.05)
    assert 0 < year["read_depth"] < 1, (
        "the ration must be a stated FRACTION of a day. A walker that claims to "
        "read a day it samples 9% of is a coverage claim, not a budget.")
    assert year["usd_total"] < walker.MONTHLY_WALKER_BUDGET_USD * 12


def test_plan_cost_prints_the_refusal_and_touches_nothing(capsys, monkeypatch):
    """--plan-cost answers a question about pace. It must fetch nothing, call
    nothing and write nothing, so it can be run before any decision."""
    def explode(*a, **k):  # pragma: no cover - the point is that it never runs
        raise AssertionError("--plan-cost made a network call")

    monkeypatch.setattr(walker.google_news, "fetch", explode)
    monkeypatch.setattr(walker.schema, "connect", explode)
    walker.print_cost_plan()
    out = capsys.readouterr().out
    assert "REFUSAL" in out
    assert "$  32" in out or "32.0" in out, "the full-sweep price is not printed"
    assert "NOT armed" in out


# --- the ration is a ration, not a stop ----------------------------------

def _item(day: str, n: int) -> dict:
    return {
        "raw_text": f"Northwind Systems appoints a chief executive and plans to "
                    f"hire staff for a new office. Story {day}-{n}.",
        "headline": f"Northwind Systems appoints chief executive ({day}-{n})",
        "discovery_url": f"https://news.google.com/rss/articles/CB{day}{n}",
        "source_url": f"https://news.google.com/rss/articles/CB{day}{n}",
        "source_name": "Example Wire",
        "published_date": day,
        "collector": walker.COLLECTOR,
    }


@pytest.fixture
def offline_walker(monkeypatch, tmp_path):
    """Everything past the fetch, stubbed at the module attribute."""
    db = tmp_path / "t.db"
    conn = walker.schema.connect(db)
    monkeypatch.setattr(walker.schema, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(walker.publish, "publish", lambda *a, **k: None)
    monkeypatch.setattr(walker.time, "sleep", lambda _s: None)

    def fake_fetch_day(lo, hi, locales, *, pause=0.4, stats=None):
        return [_item(lo.isoformat(), i) for i in range(100)]

    def fake_resolve(item, *, timeout=20, session=None):
        # A real resolution turns the aggregator redirect into the publisher's
        # own deep URL. Without that, precheck rejects every item as a bare
        # domain and the ration is never reached.
        aid = item["discovery_url"].rsplit("/", 1)[-1]
        item["source_url"] = f"https://example-wire.test/business/{aid}"
        return item

    monkeypatch.setattr(walker, "fetch_day", fake_fetch_day)
    monkeypatch.setattr(walker.google_news, "resolve_source_url", fake_resolve)
    # Every candidate is judged not-a-signal, so the run completes its windows
    # and stores nothing: this test is about the CURSOR, not about rows.
    monkeypatch.setattr(walker.cheap_extract, "extract", lambda item: None)
    monkeypatch.setattr(walker.classify, "classify", lambda raw, **k: None)
    return conn


def _run(monkeypatch, argv):
    monkeypatch.setattr(walker.sys, "argv", ["backfill_gnews_2026.py"] + argv)
    return walker.main()


def test_a_window_that_spends_its_whole_ration_is_still_finished(
        offline_walker, monkeypatch, tmp_path, capsys):
    """THE property, and the one that differs from the GDELT walker.

    100 eligible candidates a day against a ration of 5. If the ration behaved
    like `backfill_gdelt_2026.py`'s read ceiling — stop the run, leave the
    window unfinished — `done_through` would still be None after four days, the
    ticket's `next_cursor` would equal the cursor it started from, and
    `backfill_slices.record` would mark the job `stalled` and refuse to requeue
    it. The chain would end on its first slice with a green exit code.
    """
    state = tmp_path / "state.json"
    ticket = tmp_path / "slice.json"
    rc = _run(monkeypatch, [
        "--start", "2026-01-01", "--end", "2026-01-31", "--slice",
        "--slice-days", "4", "--ration", "5",
        "--state", str(state), "--emit-next", str(ticket)])
    assert rc == 0
    out = capsys.readouterr().out

    import json
    emitted = json.loads(ticket.read_text())
    assert emitted["slice"] == "2026-01-01..2026-01-04"
    assert emitted["next_cursor"] == "2026-01-05", (
        "the slice finished four windows but the cursor did not move past them, "
        "so the ration behaved like a stop and the chain will stall")
    assert emitted["totals"]["windows"] == 4
    assert emitted["totals"]["left_for_later"] == 4 * 95, (
        "the candidates the ration did not reach must be COUNTED and left "
        "unmarked — that count is what makes a second walk of the same range "
        "worth dispatching")

    # And exactly the ration was gated on each day, not more and not fewer.
    gated = [int(m) for m in re.findall(r"(\d+) gated", out)]
    assert gated == [5, 5, 5, 5], gated


def test_the_slice_ticket_advances_the_cursor_once_per_run(
        offline_walker, monkeypatch, tmp_path):
    """The date-ordinal trap, asserted on THIS walker's own ticket.

    Two runs recorded inside one clock second must advance twice. A date-keyed
    cursor returns the same window to both and re-gates it at full price; that
    pairing cost the sibling ~$3.80 a day for six days behind runs that were all
    green.
    """
    import json

    state = tmp_path / "state.json"
    ticket = tmp_path / "slice.json"
    queue = tmp_path / "queue.json"
    seen = []
    for _ in range(2):
        _run(monkeypatch, [
            "--start", "2026-01-01", "--end", "2026-01-31", "--slice",
            "--slice-days", "4", "--ration", "3",
            "--state", str(state), "--emit-next", str(ticket)])
        emitted = json.loads(ticket.read_text())
        seen.append(emitted["slice"])
        # Apply it the way the workflow does, at one frozen instant.
        loaded = backfill_slices.load(state)
        result = backfill_slices.record(loaded, emitted, now=FIXED)
        backfill_slices.save(loaded, state)
        assert result["advanced"], result.get("problem")

    assert seen == ["2026-01-01..2026-01-04", "2026-01-05..2026-01-08"], (
        f"the second run repeated the first run's window: {seen}")
    assert queue.exists() is False  # nothing here queues; the CLI does


def test_a_retried_run_after_a_requeue_resumes_and_does_not_redo(
        offline_walker, monkeypatch, tmp_path):
    """The writer queue is currently blocked, so tickets requeue and runs get
    retried. A retry must start where the cursor is, not where the INPUT says.

    The dispatch inputs still carry start/end so a run is readable in the
    Actions list, but the committed cursor is the authority: a ticket can sit in
    the queue for hours behind other work, and an input that decided the window
    would be a second, staler source of truth.
    """
    import json

    state = tmp_path / "state.json"
    ticket = tmp_path / "slice.json"
    _run(monkeypatch, ["--start", "2026-01-01", "--end", "2026-01-31", "--slice",
                       "--slice-days", "4", "--ration", "3",
                       "--state", str(state), "--emit-next", str(ticket)])
    loaded = backfill_slices.load(state)
    backfill_slices.record(loaded, json.loads(ticket.read_text()), now=FIXED)
    backfill_slices.save(loaded, state)

    # Same dispatch inputs, second run. It must NOT walk 01-01 again.
    _run(monkeypatch, ["--start", "2026-01-01", "--end", "2026-01-31", "--slice",
                       "--slice-days", "4", "--ration", "3",
                       "--state", str(state), "--emit-next", str(ticket)])
    assert json.loads(ticket.read_text())["slice"] == "2026-01-05..2026-01-08"


def test_a_finished_job_re_dispatched_does_nothing_rather_than_starting_over(
        offline_walker, monkeypatch, tmp_path, capsys):
    state = tmp_path / "state.json"
    ticket = tmp_path / "slice.json"
    _run(monkeypatch, ["--start", "2026-01-01", "--end", "2026-01-02", "--slice",
                       "--slice-days", "4", "--ration", "3",
                       "--state", str(state), "--emit-next", str(ticket)])
    import json
    loaded = backfill_slices.load(state)
    result = backfill_slices.record(loaded, json.loads(ticket.read_text()), now=FIXED)
    backfill_slices.save(loaded, state)
    assert result["complete"]

    ticket.unlink()
    rc = _run(monkeypatch, ["--start", "2026-01-01", "--end", "2026-01-02",
                            "--slice", "--slice-days", "4", "--ration", "3",
                            "--state", str(state), "--emit-next", str(ticket)])
    assert rc == 0
    assert "already complete" in capsys.readouterr().out
    assert not ticket.exists()


# --- a fetch-only run is as inert as a dry run ---------------------------

def test_fetch_only_calls_no_model_and_writes_nothing(
        offline_walker, monkeypatch, tmp_path):
    """The free reducers mark rejections seen, which is a database write. A run
    that promises to spend nothing must also promise to change nothing, or
    "prove the collector first" quietly consumes the very candidates it was
    rehearsing."""
    def explode(*a, **k):  # pragma: no cover
        raise AssertionError("a fetch-only run called the model")

    monkeypatch.setattr(walker.classify, "classify", explode)
    marked = []
    monkeypatch.setattr(walker.store, "mark_seen",
                        lambda *a, **k: marked.append(a))
    rc = _run(monkeypatch, ["--start", "2026-01-01", "--end", "2026-01-02",
                            "--fetch-only"])
    assert rc == 0
    assert marked == [], f"a fetch-only run wrote {len(marked)} seen-rows"


def test_a_dry_run_neither_advances_the_cursor_nor_emits_a_ticket(
        offline_walker, monkeypatch, tmp_path):
    """A chain of dry runs would otherwise advance the cursor over days it never
    collected, and the walk would report itself complete having stored nothing.

    The cursor cannot move without a ticket: the walker never writes the state
    file itself — `backfill_slices.py record` does, after the reset — so the
    absence of a ticket IS the absence of progress.
    """
    state = tmp_path / "state.json"
    ticket = tmp_path / "slice.json"
    _run(monkeypatch, ["--start", "2026-01-01", "--end", "2026-01-31", "--slice",
                       "--slice-days", "4", "--ration", "3", "--dry-run",
                       "--state", str(state), "--emit-next", str(ticket)])
    assert not ticket.exists(), "a dry run emitted a ticket, so the chain would "\
                                "advance over days it never collected"
    assert not state.exists(), "the walker wrote the cursor itself; only "\
                               "`backfill_slices record`, after the reset, may"


# --- cadence ------------------------------------------------------------

def test_the_google_news_walker_is_not_armed():
    """Dispatch-only until the owner has chosen a pace and a ration.

    The ration IS the budget, and a cron multiplies it by the runs per day. The
    numbers to decide with are printed by
    `backfill_gnews_2026.py --plan-cost`, and the decision belongs in TECHLOG
    beside the chosen pace and the projected month.
    """
    path = WORKFLOWS / "backfill-gnews-2026.yml"
    assert path.exists()
    crons = [line.strip() for line in path.read_text().splitlines()
             if line.strip().startswith("- cron:")]
    assert crons == [], f"the Google News walker has grown a schedule: {crons}"


def test_the_walker_holds_the_writer_lock_like_every_other_writer():
    import yaml

    parsed = yaml.safe_load((WORKFLOWS / "backfill-gnews-2026.yml").read_text())
    assert parsed["concurrency"]["group"] == "talent-collect"
    assert parsed["concurrency"]["cancel-in-progress"] is False
    for job in parsed["jobs"].values():
        assert job["timeout-minutes"] <= backfill_slices.SLICE_TIMEOUT_MINUTES
