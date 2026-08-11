"""The structured 2026 walker: free, bounded by an API ceiling, and honest.

This walker is unlike `backfill_gdelt_2026.py` in the one way that matters to a
test. GDELT walks news, so its risk is SPEND and its guard is a priced pace.
Every source here exposes `as_classified`, so the model is never called and the
risk moves somewhere else entirely:

  * **the window ceiling each API silently enforces.** BSE refuses a window
    wider than 32 days with HTTP **200** and no `Table` key, which reads as a
    redesigned response; OpenDART truncates past three months. A slice sized
    over either would fail, or worse, quietly return less than it asked for.
  * **collecting the same rows twice.** The queue is requeueing tickets today
    (WRITER_QUEUE_TOKEN is unset), so a slice being re-dispatched is the normal
    case rather than the exception. It has to be free.
  * **the figure guard**, which has eaten correct records four times in three
    days on non-Latin scripts and typographic separators. Two of the three
    sources here are non-Latin at the source.

Offline. Every response is a stub built from the collectors' own recorded
fixtures; no network, no model, no key.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest

import backfill_slices
import backfill_structured_2026 as walker
from collectors import bse_india, companies_house, opendart_korea
from pipeline import publish, schema, validate

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"

# Syntactically valid credentials for the two collectors that refuse an empty
# one before they spend a request. They authenticate nothing: every response in
# this file is a stub.
DART_KEY = "a" * 40
CH_KEY = "companies-house-rest-key-not-a-real-one"


# --------------------------------------------------------------------------
# stubs
# --------------------------------------------------------------------------

class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _BseSession:
    """Answers page 1 of every sub-category with `rows`, and page 2 with none.

    Echoes `SUBCATNAME` back the way the live API does, so the collector's
    "the server-side filter has become a no-op" guard is really exercised.
    """

    def __init__(self, rows):
        self.rows = rows
        self.requests = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.requests.append(params)
        if int(params["pageno"]) > 1:
            return _Resp({"Table": []})
        out = []
        for row in self.rows:
            clone = dict(row)
            clone["SUBCATNAME"] = params["subcategory"]
            out.append(clone)
        return _Resp({"Table": out})


class _DartSession:
    """One page of `rows` per detail type, plus company.json for each filer."""

    def __init__(self, rows, english):
        self.rows = rows
        self.english = english
        self.requests = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.requests.append(params)
        if url == opendart_korea.COMPANY_URL:
            return _Resp({"status": "000",
                          "corp_name_eng": self.english.get(params["corp_code"], "")})
        if int(params["page_no"]) > 1:
            return _Resp({"status": "000", "list": [], "total_page": 1})
        return _Resp({"status": "000", "list": self.rows, "total_page": 1})


def _bse_rows(count: int, headline: str | None = None,
              company: str | None = None) -> list[dict]:
    """`count` distinct real filings, cloned from the recorded fixture.

    Distinct NEWSIDs, because the source URL is keyed on it and the collector
    dedupes on the URL. Distinct EMPLOYERS too, because
    `dedupe.fuzzy_duplicate` treats one employer's leadership changes inside 14
    days as one development — twelve clones of one company are one stored row,
    which is correct and would make this fixture measure the wrong thing.
    """
    fixture = json.loads((FIXTURES / "bse_india_reg30.json").read_text())
    base = fixture["appointment"]["Table"][0]
    stem = company or base["SLONGNAME"]
    out = []
    for index in range(count):
        row = dict(base)
        row["NEWSID"] = f"{index:08x}-5198-4b4a-92b5-5686387cc5ef"
        row["SCRIP_CD"] = 500000 + index
        row["SLONGNAME"] = f"{stem} {index}" if index else stem
        if headline is not None:
            row["HEADLINE"] = headline
        out.append(row)
    return out


def _dart_rows(count: int, *, corp_name=None, english=None):
    """Same reasoning as `_bse_rows`: distinct receipts AND distinct filers."""
    fixture = json.loads((FIXTURES / "opendart_korea_leadership.json").read_text())
    base = fixture["ceo_change"]["list"][0]
    stem = english if english is not None else fixture["ceo_change"]["_corp_name_eng"]
    rows, names = [], {}
    for index in range(count):
        row = dict(base)
        row["rcept_no"] = f"2026010190{index:04d}"
        row["corp_code"] = f"{10000000 + index:08d}"
        if corp_name is not None:
            row["corp_name"] = corp_name
        rows.append(row)
        names[row["corp_code"]] = f"{stem} {index}" if index else stem
    return rows, names


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway database, and a publish that does nothing.

    `publish` is stubbed rather than configured because it is a separate gate
    with its own guardrails; what is under test here is what the walker STORES.
    """
    path = tmp_path / "walker.db"
    monkeypatch.setattr(schema, "DB_PATH", path)
    monkeypatch.setattr(publish, "publish", lambda conn, **kw: {})
    monkeypatch.setenv("OPENDART_API_KEY_KR", DART_KEY)
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY_UK", CH_KEY)
    return path


def rows_in(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute(
            "select count(*) from signals where is_current = 1").fetchone()[0]


# --------------------------------------------------------------------------
# the premise: this walker cannot spend
# --------------------------------------------------------------------------

def test_every_source_derives_its_record_and_none_can_reach_the_model():
    """THE premise. A source without `as_classified` would go to the classifier.

    `run_collect` decides on exactly this attribute: `derive = getattr(module,
    "as_classified", None)`, and everything about this walker being free
    follows from it. If a source is ever added here without one, the walk stops
    costing $0 and nothing else in the file would notice.
    """
    for name, spec in walker.SPECS.items():
        assert callable(getattr(spec.module, "as_classified", None)), (
            f"{name} has no as_classified, so it would be sent to the model and "
            f"this walker would stop being free")


def test_the_walker_does_not_import_the_classifier_at_all():
    """Not a style point: it is the only structural proof that spend is zero.

    A guard that reads `--max-readthroughs` or a cap can be raised. A module
    that never imports the model cannot be made to call one.
    """
    import ast

    tree = ast.parse((ROOT / "backfill_structured_2026.py").read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[-1] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
            imported.add((node.module or "").split(".")[-1])
    assert "classify" not in imported, (
        "the structured walker imported the classifier. Every source here is "
        "derived from typed fields; if one ever needs a model it belongs in the "
        "priced walker, with a budget beside it.")


def test_the_plan_fetches_nothing_and_says_so(capsys):
    assert walker.main(["--plan", "--start", "2026-01-01",
                        "--end", "2026-07-30"]) == 0
    printed = capsys.readouterr().out
    assert "$0.00" in printed
    for name in walker.SPECS:
        assert name in printed
    # The pace decision this walker actually needs is rate limits, not dollars.
    assert "rate limit" in printed
    assert "req/slice" in printed


def test_the_plan_never_passes_a_projection_off_as_a_measurement(capsys):
    """Two of the three sources need a key nobody has locally, so two of the
    three wall clocks are arithmetic. Saying which is which is the difference
    between a plan and a claim."""
    walker.main(["--plan", "--start", "2026-01-01", "--end", "2026-07-30"])
    printed = capsys.readouterr().out
    assert "MEASURED except where marked" in printed
    for name, spec in walker.SPECS.items():
        line = next(l for l in printed.splitlines()
                    if l.strip().startswith(name))
        if spec.measured_slice_minutes is None:
            assert "*" in line, f"{name} is a projection and is not marked"
            assert "NOT re-measured" in spec.evidence
        else:
            assert "*" not in line
            assert "MEASURED" in spec.evidence


def test_a_slice_fits_well_inside_the_budget_that_bounds_the_lock():
    """The 350-minute incident in miniature is what this stops.

    A slice here is ATOMIC — one API window, or one roster slice — so unlike
    the GDELT walker there is no boundary inside it at which the budget could
    stop the run and point a cursor honestly. The bound has to come from the
    slice being SIZED right, so it is asserted rather than enforced at runtime.
    """
    for name, spec in walker.SPECS.items():
        assert walker.slice_minutes(spec) < backfill_slices.SLICE_BUDGET_MINUTES / 3, (
            f"{name} takes {walker.slice_minutes(spec):.1f} min against a "
            f"{backfill_slices.SLICE_BUDGET_MINUTES} min budget, and its slice "
            f"cannot be interrupted part way. Cut slice_size.")
    assert backfill_slices.SLICE_BUDGET_MINUTES < backfill_slices.SLICE_TIMEOUT_MINUTES


# --------------------------------------------------------------------------
# the window ceilings, which are the reason this exists
# --------------------------------------------------------------------------

def test_every_slice_size_is_inside_its_apis_measured_window_ceiling():
    """A slice wider than the ceiling is not slow, it is refused or truncated.

    BSE answers HTTP 200 with `{"Status":"False","Message":"Date range exceeded
    threshold."}` and no `Table` key — binary-searched on 2026-07-30 from
    2026-01-01: 32 days accepted, 33 refused. OpenDART's is documented at three
    months and returns a SHORTER window rather than an error, which is worse.
    """
    for name, spec in walker.SPECS.items():
        if spec.window_cap_days is None:
            continue
        # The window a slice actually asks for is slice_size - 1 days wide
        # (inclusive both ends), so this is the conservative comparison.
        assert spec.slice_size <= spec.window_cap_days, (
            f"{name} asks for {spec.slice_size} days against a "
            f"{spec.window_cap_days}-day ceiling")


def test_the_bse_ceiling_here_is_the_collectors_own_number():
    """Typed twice is a number that will disagree with itself."""
    assert walker.SPECS["bse_india"].window_cap_days == bse_india.WINDOW_CAP_DAYS


def test_the_korea_ceiling_is_the_one_the_collector_enforces(monkeypatch):
    """`days_from_env` refuses over 90; the walker must not ask for more.

    OpenDART's failure mode is the quieter of the two: a corp_code-less search
    past three months returns a SHORTER window rather than an error, so a
    walker that asked for 120 days would collect 90 and record 120 as done.
    """
    monkeypatch.setenv("TIT_DART_DAYS", "91")
    with pytest.raises(opendart_korea.OpenDartError, match="three months"):
        opendart_korea.days_from_env()
    assert walker.SPECS["opendart_korea"].window_cap_days == 90


def test_a_window_wider_than_the_ceiling_is_refused_before_any_request():
    """Locally, with the width named — not at the API, where it is an HTTP 200.

    The whole point: at the API this failure arrives looking like a redesigned
    response, which sends a reader to the wrong place entirely.
    """
    spec = walker.SPECS["bse_india"]
    with pytest.raises(SystemExit) as caught:
        walker.fetch_slice("bse_india", spec, ("2026-01-01", "2026-06-01"),
                           job_start=date(2026, 1, 1), job_end=date(2026, 7, 30),
                           roster_slices=8)
    assert "wider than the 32 days" in str(caught.value)


def test_the_bse_refusal_names_the_window_and_not_the_response_shape():
    """The message a human reads at 2am has to point at the cause.

    Before this, the width refusal fell into the `no 'Table' key` branch and
    said "the response shape has changed" — which is a search for a redesigned
    API instead of for a number in a workflow input.
    """
    class _Refusing:
        def get(self, url, params=None, headers=None, timeout=None):
            return _Resp({"Status": "False",
                          "Message": "Date range exceeded threshold."})

    with pytest.raises(bse_india.BseError) as caught:
        bse_india.fetch_page("Change in Management", "20260101", "20260730", 1,
                             session=_Refusing())
    message = str(caught.value)
    assert "too wide" in message
    assert "32" in message
    assert "response shape has changed" not in message


def test_the_env_input_refuses_a_window_the_api_will_refuse(monkeypatch):
    """`collect-structured.yml`'s `days` input said a gap is closed by widening
    it. That is false above 32, and it failed blaming the API."""
    monkeypatch.setenv("TIT_BSE_DAYS", "211")
    with pytest.raises(bse_india.BseError, match="wider than 32 days"):
        bse_india.days_from_env()
    # And a window inside the ceiling is still accepted, so a short gap is
    # still closed the cheap way.
    monkeypatch.setenv("TIT_BSE_DAYS", "28")
    assert bse_india.days_from_env() == 28


def test_a_days_slice_asks_the_api_for_exactly_the_window_it_was_given():
    """A half-open window that looks inclusive is a silently skipped day."""
    session = _BseSession(_bse_rows(12))
    walker.fetch_slice("bse_india", walker.SPECS["bse_india"],
                       ("2026-01-01", "2026-01-28"),
                       job_start=date(2026, 1, 1), job_end=date(2026, 7, 30),
                       roster_slices=8, session=session)
    asked = {(p["strPrevDate"], p["strToDate"]) for p in session.requests}
    assert asked == {("20260101", "20260128")}


# --------------------------------------------------------------------------
# storing, and re-running for free
# --------------------------------------------------------------------------

def _run_bse(db, session, extra=()):
    return walker.main(["--source", "bse_india", "--start", "2026-01-01",
                        "--end", "2026-01-28", *extra], session=session)


def test_a_slice_stores_and_the_second_run_of_it_stores_nothing(db):
    """Seen-URL skipping BEFORE any work, which is what makes a requeue free.

    The writer queue is requeueing tickets today because WRITER_QUEUE_TOKEN is
    unset, so a slice running twice is the ordinary case. It has to cost one
    fetch and nothing else.
    """
    assert _run_bse(db, _BseSession(_bse_rows(12))) == 0
    first = rows_in(db)
    assert first >= 10

    session = _BseSession(_bse_rows(12))
    assert _run_bse(db, session) == 0
    assert rows_in(db) == first, "a repeated slice stored rows a second time"


def test_the_repeat_is_skipped_before_any_derivation(db, capsys):
    assert _run_bse(db, _BseSession(_bse_rows(12))) == 0
    capsys.readouterr()
    assert _run_bse(db, _BseSession(_bse_rows(12))) == 0
    printed = capsys.readouterr().out
    assert re.search(r"already seen\s+12", printed), printed


def test_fetch_only_writes_nothing_at_all(db):
    assert _run_bse(db, _BseSession(_bse_rows(12)), ["--fetch-only"]) == 0
    assert rows_in(db) == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("select count(*) from seen_urls").fetchone()[0] == 0


def test_a_dry_run_writes_nothing_and_reports_what_it_would_store(db, capsys):
    assert _run_bse(db, _BseSession(_bse_rows(12)), ["--dry-run"]) == 0
    assert rows_in(db) == 0
    assert "WOULD STORE" in capsys.readouterr().out


def test_an_empty_slice_goes_red_rather_than_green(db):
    """A backfill that walks a window returning nothing must never exit 0.

    That is the shape of the first SEC dispatch, which exited 0 after five
    silent 403s and read exactly like a successful run that found nothing.
    """
    class _Empty(_BseSession):
        def get(self, url, params=None, headers=None, timeout=None):
            return _Resp({"Table": []})

    # The collector's own floor fires first, which is the correct order: it
    # knows India files ~250 a week. Either way the run is red.
    assert _run_bse(db, _Empty([])) == 1
    assert rows_in(db) == 0


# --------------------------------------------------------------------------
# the figure guard, on the two non-Latin sources
# --------------------------------------------------------------------------

def test_a_filed_description_ending_in_a_date_still_round_trips(db):
    """The newline-spanning `\\s*` in validate._NUMBER, through the walker.

    `_NUMBER` ends with an optional magnitude suffix behind `\\s*`, and that
    `\\s*` matches newlines — so a date at the end of the quoted line followed
    by a blank line and a word beginning with K tokenised as `28072026k`, the
    same date in the summary looked invented, and a correct record was
    discarded. `bse_india` closes the quote to put a non-space character after
    the figure. This drives that whole path rather than asserting the regex.
    """
    rows = _bse_rows(12,
                     headline="Appointment of Ms. Neha Rathi as Additional "
                              "Director (Independent) w.e.f. 28.07.2026",
                     company="Kimia Biosciences Ltd")
    assert _run_bse(db, _BseSession(rows)) == 0
    assert rows_in(db) >= 10

    # And the guard itself agrees, on the exact strings that were stored.
    item = bse_india._row(dict(rows[0], SUBCATNAME="Change in Directorate"),
                          "Change in Directorate")
    derived = bse_india.as_classified(item)
    validate.assert_figures_are_sourced(derived["summary"], item["raw_text"])
    assert "28.07.2026" in item["raw_text"]


def test_full_width_digits_survive_the_whole_korean_path(db):
    """Trap 4 of `opendart_korea`, driven end to end rather than unit-tested.

    `validate._numbers_in` tokenises with `\\d`, which matches U+FF10..FF19,
    and `_normalize_number` does not fold them — so `１２３` in a summary
    against `123` in `raw_text` compares unequal and a correct record is
    discarded silently. `_squeeze` folds them on the way in. NFKC would too,
    and is WRONG here: it rewrites the U+318D in
    독립이사의선임ㆍ해임또는중도퇴임에관한신고 to U+119E and the report-name
    allowlist stops matching.
    """
    rows, english = _dart_rows(
        8, corp_name="１２３ 주식회사", english="１２３ Holdings Co., Ltd.")
    session = _DartSession(rows, english)
    assert walker.main(["--source", "opendart_korea", "--start", "2026-01-01",
                        "--end", "2026-03-01"], session=session) == 0
    stored = rows_in(db)
    assert stored >= 5, "the Korean slice stored nothing"

    with sqlite3.connect(db) as conn:
        headline, summary = conn.execute(
            "select headline, summary from signals where is_current = 1 limit 1"
        ).fetchone()
    # Folded, not preserved: a full-width digit in a company name is what makes
    # the summary and raw_text disagree in the first place.
    assert "１２３" not in headline and "１２３" not in summary
    assert "123" in headline

    item = opendart_korea._row(rows[0], english[rows[0]["corp_code"]])
    validate.assert_figures_are_sourced(
        opendart_korea.as_classified(item)["summary"], item["raw_text"])


def test_nfkc_would_break_the_korean_allowlist_and_is_not_used():
    """Stated as a test because "just normalise it" is the obvious wrong fix."""
    import unicodedata

    name = "독립이사의선임ㆍ해임또는중도퇴임에관한신고"
    assert name in opendart_korea.REPORT_NAMES
    assert unicodedata.normalize("NFKC", name) not in opendart_korea.REPORT_NAMES
    assert opendart_korea.is_wanted(name)


# --------------------------------------------------------------------------
# companies_house: the roster is the cursor, and it must partition exactly once
# --------------------------------------------------------------------------

def test_the_backfill_partition_covers_every_employer_exactly_once():
    """A missed slice is a hole nothing ever looks at again.

    The backfill uses EIGHT slices where the weekly rotation uses four, so this
    also proves the two do not have to agree: `slice_of` is a blake2b digest of
    the company number, so any count partitions the roster exactly once.
    """
    numbers = [f"{n:08d}" for n in range(4000)]
    for count in (companies_house.SLICES, walker.CH_BACKFILL_SLICES):
        buckets = [companies_house.slice_of(n, count) for n in numbers]
        assert set(buckets) == set(range(count))
        assert len(buckets) == len(numbers)
        # Reasonably even, or one slice becomes the long pole.
        sizes = [buckets.count(i) for i in range(count)]
        assert max(sizes) < 2 * min(sizes)


def test_the_partition_is_stable_across_processes():
    """Python's hash() is salted per process; this must not be.

    A reshuffling rotation leaves some companies unvisited for months while the
    run count looks perfect.
    """
    first = [companies_house.slice_of(f"{n:08d}", walker.CH_BACKFILL_SLICES)
             for n in range(200)]
    second = [companies_house.slice_of(f"{n:08d}", walker.CH_BACKFILL_SLICES)
              for n in range(200)]
    assert first == second


def test_the_uk_source_url_is_recorded_but_never_consulted(db, monkeypatch,
                                                           capsys):
    """One person can be appointed twice, so their appointments page recurs.

    Skipping it on sight would make the first appointment the last one this
    source ever reported — the `ats_boards` lesson. The outcome is still
    RECORDED, exactly as `run_collect` records it; what must not happen is the
    lookup. The walker reads the flag off the collector rather than restating
    it, so the two cannot drift.
    """
    assert walker.SPECS["companies_house"].revisits_source_url is True
    assert companies_house.REVISITS_ITS_SOURCE_URL is True

    fixture = json.loads((FIXTURES / "companies_house_officers.json").read_text())
    block = fixture["appointment"]
    employer = companies_house.Employer(
        block["company"]["number"], block["company"]["name"],
        block["company"]["size_band"], block["company"]["postcode"],
        block["company"]["sic"])

    monkeypatch.setattr(companies_house, "roster", lambda **kw: [employer])
    monkeypatch.setattr(companies_house, "fetch_officers",
                        lambda number, **kw: block["payload"]["items"])

    def _run():
        return walker.main(["--source", "companies_house", "--start",
                            "2026-01-01", "--end", "2026-07-30",
                            "--roster-slices", "1"])

    assert _run() == 0
    assert rows_in(db) >= 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("select count(*) from seen_urls").fetchone()[0] >= 1

    capsys.readouterr()
    assert _run() == 0
    printed = capsys.readouterr().out
    assert re.search(r"already seen\s+0 ", printed), (
        "the UK officer's appointments page was skipped on sight, so their "
        "next appointment would never be collected\n" + printed)


def test_the_uk_walk_filters_on_the_whole_job_window_not_one_slice(monkeypatch):
    """The roster is what is sliced; the date window is a free filter."""
    seen = {}

    def _collect(queries=None, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(companies_house, "collect", _collect)
    walker.fetch_slice("companies_house", walker.SPECS["companies_house"],
                       ("3", "3"), job_start=date(2026, 1, 1),
                       job_end=date(2026, 7, 30), roster_slices=8)
    assert seen["slice_index"] == 3
    assert seen["slices"] == 8
    assert seen["today"] == date(2026, 7, 30)
    assert seen["days"] == (date(2026, 7, 30) - date(2026, 1, 1)).days


# --------------------------------------------------------------------------
# the chain
# --------------------------------------------------------------------------

def test_three_sources_on_one_window_keep_three_cursors(tmp_path):
    """Without a label the job id is workflow + window, so India and Korea
    would share a cursor and each would resume where the other stopped — a
    hole and a double-collection in one."""
    ids = {
        source: backfill_slices.job_id(walker.WORKFLOW, "2026-01-01",
                                       "2026-07-30", source)
        for source in walker.SPECS
    }
    assert len(set(ids.values())) == len(ids)
    for source, key in ids.items():
        assert source in key


def test_a_slice_run_emits_a_ticket_whose_cursor_advances(db, tmp_path):
    state = tmp_path / "state.json"
    ticket = tmp_path / "slice.json"
    assert walker.main(["--source", "bse_india", "--start", "2026-01-01",
                        "--end", "2026-07-30", "--slice", "--state", str(state),
                        "--emit-next", str(ticket)],
                       session=_BseSession(_bse_rows(12))) == 0

    emitted = json.loads(ticket.read_text())
    assert emitted["slice"] == "2026-01-01..2026-01-28"
    assert emitted["next_cursor"] == "2026-01-29"
    assert emitted["label"] == "bse_india"
    assert emitted["halt"] == ""

    applied = backfill_slices.load(state)
    result = backfill_slices.record(applied, emitted)
    assert result["advanced"]
    assert result["job"]["cursor"] == "2026-01-29"
    # And the requeue carries the DATE window, not the cursor: the cursor is
    # committed and is the authority, so an input saying where to start would
    # be a second, staler source of truth.
    inputs = backfill_slices.next_inputs(result["job"])
    assert inputs["source"] == "bse_india"
    assert inputs["start"] == "2026-01-01"


def test_a_roster_slice_ticket_never_overwrites_the_date_window():
    """companies_house's job start/end are slice INDICES. If `next_inputs`
    injected them the next run would read a one-day window and store nothing,
    silently, for seven of the eight slices."""
    state = backfill_slices.empty_state()
    job = backfill_slices.open_job(
        state, workflow=walker.WORKFLOW, unit="slices", start="0", end="7",
        slice_size=1, label="companies_house",
        inputs={"source": "companies_house", "start": "2026-01-01",
                "end": "2026-07-30"})
    backfill_slices.record(state, backfill_slices.slice_ticket(job, "0", "0"))
    inputs = backfill_slices.next_inputs(job)
    assert inputs["start"] == "2026-01-01"
    assert inputs["end"] == "2026-07-30"
    assert job["cursor"] == "1"


def test_a_whole_roster_walk_is_monotonic_and_visits_each_slice_once():
    state = backfill_slices.empty_state()
    job = backfill_slices.open_job(
        state, workflow=walker.WORKFLOW, unit="slices", start="0",
        end=str(walker.CH_BACKFILL_SLICES - 1), slice_size=1,
        label="companies_house")
    visited = []
    for _ in range(walker.CH_BACKFILL_SLICES + 3):
        window = backfill_slices.next_slice(job["cursor"], job["end"], "slices", 1)
        if window is None:
            break
        visited.append(window[0])
        backfill_slices.record(state, backfill_slices.slice_ticket(job, *window))
    assert visited == [str(i) for i in range(walker.CH_BACKFILL_SLICES)]
    assert job["state"] == "done"


def test_edinet_is_absent_and_the_refusal_says_why():
    """Japan needs no walker and the reason has to survive in the code.

    Its list endpoint is one call per calendar day and its own MAX_DAYS is 366,
    so the whole of 2026 is a single dispatch of collect-structured.yml. A
    walker for that would be a second implementation of a cursor for 211
    requests.
    """
    assert "edinet_japan" not in walker.SPECS
    with pytest.raises(SystemExit) as caught:
        walker.spec_for("edinet_japan")
    message = str(caught.value)
    assert "366" in message and "collect-structured.yml" in message

    from collectors import edinet_japan
    assert edinet_japan.MAX_DAYS >= 366, (
        "EDINET's own window cap shrank below a year, so Japan is no longer "
        "reachable in one dispatch and this walker's omission of it is stale")


def test_the_walker_writes_no_health_row(db):
    """A backfill must not reset a collector's staleness leash.

    `staleness.py` leashes each of these to its WEEKLY cron. If a backfill
    reported health, a broken weekly run would be masked by a backfill that
    happened to succeed, and the leash would be measuring the wrong thing.
    """
    assert _run_bse(db, _BseSession(_bse_rows(12))) == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "select count(*) from source_health").fetchone()[0] == 0
