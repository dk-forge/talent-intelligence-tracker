"""A retracted record must stay retracted.

A story we withdrew resurfaced on a later run. Dedup only looked at current
rows, so it did not recognise the hash — and the unique index, which spans every
revision, raised IntegrityError and killed the run mid-batch. Had the insert
succeeded it would have been worse: the retraction would have been silently
undone and the bad record republished.
"""

import pytest

from pipeline import dedupe, schema, store, validate


def make(headline="Acme to create 300 jobs in Dublin"):
    return validate.build_signal(
        {"company": "Acme", "pillar": "company_development",
         "signal_direction": "hiring", "city": "Dublin", "country": "Ireland",
         "confidence": "reported", "headline": headline,
         "summary": "Acme will add 300 roles.",
         "talent_readthrough": "300 roles entering the Dublin market."},
        {"raw_text": "Acme to create 300 jobs in Dublin",
         "source_url": "https://www.irishtimes.com/business/acme-dublin/",
         "source_name": "The Irish Times", "published_date": "2026-07-20"},
        "google_news",
    )


@pytest.fixture
def conn(tmp_path):
    c = schema.connect(tmp_path / "r.db")
    yield c
    c.close()


def test_a_retracted_record_is_not_re_stored(conn):
    s = make()
    assert store.store(conn, s) == "stored"

    conn.execute("UPDATE signals SET is_current = 0, notes = 'retracted: bad source' "
                 "WHERE content_hash = ?", (s.content_hash,))
    conn.commit()

    assert store.store(conn, make()) == "retracted"


def test_re_storing_a_retracted_record_does_not_crash(conn):
    """The original failure was an IntegrityError that killed the whole run."""
    s = make()
    store.store(conn, s)
    conn.execute("UPDATE signals SET is_current = 0 WHERE content_hash = ?",
                 (s.content_hash,))
    conn.commit()
    store.store(conn, make())  # must not raise


def test_the_retraction_survives(conn):
    s = make()
    store.store(conn, s)
    conn.execute("UPDATE signals SET is_current = 0 WHERE content_hash = ?",
                 (s.content_hash,))
    conn.commit()
    store.store(conn, make())

    current = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE content_hash = ? AND is_current = 1",
        (s.content_hash,)).fetchone()[0]
    assert current == 0, "the record must stay withdrawn"


def test_a_live_duplicate_still_reports_as_duplicate(conn):
    s = make()
    store.store(conn, s)
    assert store.store(conn, make()) == "duplicate"


def test_dedupe_reports_which_kind(conn):
    s = make()
    store.store(conn, s)
    assert dedupe.exact_duplicate(conn, s.content_hash) == "duplicate"
    conn.execute("UPDATE signals SET is_current = 0 WHERE content_hash = ?",
                 (s.content_hash,))
    conn.commit()
    assert dedupe.exact_duplicate(conn, s.content_hash) == "retracted"


# --- a busy host is not a final answer -------------------------------------
#
# Measured 2026-07-29: a scope correction withdrew three rows and lost four to
# `504` from the gateway, one request at a time, with nothing wrong with the
# requests. A withdrawal that fails leaves a record live on a page that
# promises it is not there, so this is the one place where "the host was busy"
# has to be retried rather than reported.

class _Resp:
    def __init__(self, status, body='{"retracted": 1}'):
        self.status_code = status
        self.text = body

    def json(self):
        import json as _json
        return _json.loads(self.text)


def test_a_504_is_retried_until_the_host_answers(monkeypatch):
    import retract
    from pipeline import publish

    seen = []

    def flaky(url, **kwargs):
        seen.append(url)
        return _Resp(504, "<!DOCTYPE html>") if len(seen) < 3 else _Resp(200)

    monkeypatch.setattr(retract.publish, "_config",
                        lambda: ("https://example.com/blog", "k"))
    monkeypatch.setattr(retract.requests, "post", flaky)
    monkeypatch.setattr(retract.time, "sleep", lambda _s: None)

    assert retract.retract_remote("sig", "why") == {"retracted": 1}
    assert len(seen) == 3
    assert publish  # imported for the error type below


def test_a_dropped_connection_is_retried_too(monkeypatch):
    import retract

    seen = []

    def flaky(url, **kwargs):
        seen.append(url)
        if len(seen) < 2:
            raise retract.requests.RequestException("connection reset")
        return _Resp(200)

    monkeypatch.setattr(retract.publish, "_config",
                        lambda: ("https://example.com/blog", "k"))
    monkeypatch.setattr(retract.requests, "post", flaky)
    monkeypatch.setattr(retract.time, "sleep", lambda _s: None)

    assert retract.retract_remote("sig", "why") == {"retracted": 1}


def test_a_4xx_is_not_retried_because_it_is_our_fault(monkeypatch):
    """A bad key or an unknown signal_id is the same wrong question however
    many times it is asked."""
    import retract
    from pipeline import publish

    seen = []

    def refused(url, **kwargs):
        seen.append(url)
        return _Resp(403, "forbidden")

    monkeypatch.setattr(retract.publish, "_config",
                        lambda: ("https://example.com/blog", "k"))
    monkeypatch.setattr(retract.requests, "post", refused)
    monkeypatch.setattr(retract.time, "sleep", lambda _s: None)

    with pytest.raises(publish.PublishError):
        retract.retract_remote("sig", "why")
    assert len(seen) == 1


def test_a_host_that_never_recovers_still_fails_loudly(monkeypatch):
    import retract
    from pipeline import publish

    monkeypatch.setattr(retract.publish, "_config",
                        lambda: ("https://example.com/blog", "k"))
    monkeypatch.setattr(retract.requests, "post",
                        lambda url, **k: _Resp(504, "<!DOCTYPE html>"))
    monkeypatch.setattr(retract.time, "sleep", lambda _s: None)

    with pytest.raises(publish.PublishError) as caught:
        retract.retract_remote("sig", "why")
    assert "attempts" in str(caught.value), (
        "a row still live on the site must say so, and say how hard we tried")


# --- a correction is a LIST, and one bad row must not eat the other 26 -------
#
# `retract.py` and retract.yml took exactly one signal_id per run. A correction
# landing 27 rows could not use them: retract.yml's concurrency group keeps only
# ONE pending run, so dispatching 27 silently drops most of them, and 27
# sequential dispatches would be 27 rebase-and-push commits against a 72MB
# binary database — 27 chances at the binary conflict the workflow's own
# comments already worry about.
#
# So one run withdraws a list. The properties that matter are not "it loops":
# they are that a partial failure stays LOUD (non-zero) and LEGIBLE (per row,
# with a re-runnable list of just the failures), that the per-row retry ladder
# stays per row, and that a re-run of a partly-applied list neither claims a
# success it did not achieve nor fails on a row that was already done.

def _insert(conn, signal_id, *, is_current=1):
    conn.execute(
        "INSERT INTO signals (signal_id, is_current, headline, summary,"
        " talent_readthrough, company, company_key, pillar, signal_direction,"
        " confidence, source_url, source_name, captured_at, as_of,"
        " content_hash, collector) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (signal_id, is_current, "h", "s", "t", "Acme", "acme",
         "company_development", "hiring", "reported",
         "https://example.com/a", "Example", "2026-08-13", "2026-08-13",
         signal_id, "google_news"),
    )
    conn.commit()


def test_a_comma_separated_list_becomes_many_ids():
    import retract
    assert retract.parse_ids("a, b ,c") == ["a", "b", "c"]
    assert retract.parse_ids(" solo ") == ["solo"]


def test_an_empty_entry_is_an_error_not_a_skip():
    """`a,,b` is a list someone built wrong, not a list of two."""
    import retract
    for raw in ("a,,b", "a,", ",a", "a, ,b"):
        with pytest.raises(ValueError) as caught:
            retract.parse_ids(raw)
        assert "empty" in str(caught.value).lower(), (
            f"{raw!r} must name the empty entry, not silently drop it")


def test_a_duplicate_id_is_withdrawn_once():
    import retract
    assert retract.parse_ids("a,b,a") == ["a", "b"]


def test_a_long_list_can_arrive_as_a_file(tmp_path):
    """Twenty-seven ids on a command line is the wrong container: it cannot be
    reviewed in a diff, it lands in shell history, and it is built by hand.
    `--ids-file` is for the human; the comma list is for the workflow input."""
    import retract
    f = tmp_path / "correction.txt"
    f.write_text("# a scope correction\nsig01\nsig02, sig03\n\nsig01\n")
    assert retract.read_ids_file(f) == ["sig01", "sig02", "sig03"]


def test_an_empty_entry_inside_a_file_line_is_still_an_error(tmp_path):
    """Blank LINES are not entries and are dropped. `a,,b` on a real line is
    still a list built wrong, wherever it was written."""
    import retract
    f = tmp_path / "correction.txt"
    f.write_text("sig01\nsig02,,sig03\n")
    with pytest.raises(ValueError) as caught:
        retract.read_ids_file(f)
    assert "empty" in str(caught.value).lower()


def _stub_host(monkeypatch, respond):
    """Point retract at a fake host. `respond(signal_id, n)` returns a _Resp."""
    import retract
    seen = []

    def post(url, **kwargs):
        sid = kwargs["json"]["signal_id"]
        seen.append((sid, kwargs["json"]["reason"]))
        return respond(sid, sum(1 for s, _ in seen if s == sid))

    monkeypatch.setattr(retract.publish, "_config",
                        lambda: ("https://example.com/blog", "k"))
    monkeypatch.setattr(retract.requests, "post", post)
    monkeypatch.setattr(retract.time, "sleep", lambda _s: None)
    return seen


def test_row_14_of_27_failing_does_not_stop_the_other_26(conn, monkeypatch):
    import retract
    ids = [f"sig{i:02d}" for i in range(1, 28)]
    for sid in ids:
        _insert(conn, sid)

    seen = _stub_host(monkeypatch, lambda sid, n:
                      _Resp(403, "forbidden") if sid == "sig14" else _Resp(200))

    failures, results = retract.retract_many(conn, ids, "a scope correction")

    assert failures == 1, "one row failed, so exactly one failure"
    assert [sid for sid, _ in seen].count("sig27") == 1, (
        "row 27 must still have been attempted after row 14 failed")
    assert len(results) == 27
    live = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE is_current = 1").fetchone()[0]
    assert live == 1, "every row but the failed one must be withdrawn locally"


def test_any_failure_makes_the_run_red(conn, monkeypatch):
    import retract
    for sid in ("a", "b"):
        _insert(conn, sid)
    _stub_host(monkeypatch, lambda sid, n:
               _Resp(403, "no") if sid == "b" else _Resp(200))
    assert retract.retract_many(conn, ["a", "b"], "why")[0] == 1


def test_the_failures_come_back_as_a_rerunnable_list(conn, monkeypatch, capsys):
    """26 lines of output are useless if the human has to grep them to retry."""
    import retract
    ids = [f"sig{i:02d}" for i in range(1, 28)]
    for sid in ids:
        _insert(conn, sid)
    _stub_host(monkeypatch, lambda sid, n:
               _Resp(403, "forbidden") if sid in ("sig14", "sig22")
               else _Resp(200))

    failures, results = retract.retract_many(conn, ids, "a scope correction")
    retract.report(results, "a scope correction")
    out = capsys.readouterr().out

    assert "sig14,sig22" in out, (
        "the failures must come back as one paste-ready list, not 27 lines to grep")
    assert "sig13,sig14" not in out, "a succeeded row must not be in the retry list"


def test_the_retry_ladder_still_applies_PER_ROW(conn, monkeypatch):
    """The transient-5xx retry is per request. A batch must not spend one
    ladder across 27 rows, nor give up on row 3 because row 2 wobbled."""
    import retract
    for sid in ("a", "b", "c"):
        _insert(conn, sid)

    seen = _stub_host(monkeypatch, lambda sid, n:
                      _Resp(504, "<!DOCTYPE html>") if sid == "b" and n < 3
                      else _Resp(200))

    failures, results = retract.retract_many(conn, ["a", "b", "c"], "why")
    assert failures == 0, "a host that wobbled and then answered is not a failure"
    assert [s for s, _ in seen].count("b") == 3, "row b got its own ladder"
    assert [s for s, _ in seen].count("c") == 1, "row c got a fresh one"


def test_one_reason_applies_to_the_whole_list(conn, monkeypatch):
    import retract
    for sid in ("a", "b"):
        _insert(conn, sid)
    seen = _stub_host(monkeypatch, lambda sid, n: _Resp(200))
    retract.retract_many(conn, ["a", "b"], "one reason")
    assert {r for _, r in seen} == {"one reason"}


# --- idempotence: a re-run of a partly-applied list --------------------------
#
# The route is tit_api_retract() in wordpress-plugin/.../includes/api.php. Its
# UPDATE is scoped `WHERE signal_id = %s AND is_current = 1`, and it returns
# HTTP 200 with `{"retracted": 0}` when that matches nothing. So the host
# answers the same way for "already withdrawn" and for "no such id" — it cannot
# tell them apart and neither can a caller reading only the response. The local
# database can: it knows every signal_id it ever stored.

def test_a_second_run_of_the_same_list_is_not_a_failure(conn, monkeypatch):
    import retract
    for sid in ("a", "b"):
        _insert(conn, sid, is_current=0)
    _stub_host(monkeypatch, lambda sid, n: _Resp(200, '{"retracted": 0}'))

    failures, results = retract.retract_many(conn, ["a", "b"], "why")
    assert failures == 0, "a row that was already withdrawn is done, not broken"
    assert {r.outcome for r in results} == {"already withdrawn"}


def test_a_row_already_done_is_not_counted_as_a_new_withdrawal(conn, monkeypatch):
    import retract
    _insert(conn, "fresh")
    _insert(conn, "done", is_current=0)
    _stub_host(monkeypatch, lambda sid, n:
               _Resp(200, '{"retracted": 0}') if sid == "done" else _Resp(200))

    _, results = retract.retract_many(conn, ["fresh", "done"], "why")
    by_id = {r.signal_id: r.outcome for r in results}
    assert by_id == {"fresh": "withdrawn", "done": "already withdrawn"}


def test_an_id_nobody_has_ever_seen_is_a_failure(conn, monkeypatch):
    """`{"retracted": 0}` for an id the database has never held is a typo in
    the correction, not a job well done. Reporting it green is how a row stays
    live under a page that promises it is not there."""
    import retract
    _stub_host(monkeypatch, lambda sid, n: _Resp(200, '{"retracted": 0}'))

    failures, results = retract.retract_many(conn, ["sig-typo"], "why")
    assert failures == 1
    assert "unknown" in results[0].detail.lower(), (
        f"an unknown signal_id must say so; got {results[0].detail!r}")


def test_the_run_budget_reports_the_rest_rather_than_being_killed(conn, monkeypatch):
    """A run killed by the platform mid-list is the worst outcome here: the
    commit step never runs and the database forgets withdrawals the site
    applied. So the script stops itself first and says what it did not do."""
    import retract
    ids = [f"sig{i:02d}" for i in range(1, 28)]
    for sid in ids:
        _insert(conn, sid)
    _stub_host(monkeypatch, lambda sid, n: _Resp(200))

    clock = iter(range(0, 100_000, 120))  # two minutes per read
    monkeypatch.setattr(retract.time, "monotonic", lambda: next(clock))

    failures, results = retract.retract_many(conn, ids, "why", budget=600)
    assert failures, "rows we never attempted are failures, not silence"
    skipped = [r for r in results if r.outcome == "not attempted"]
    assert skipped, "the unattempted rows must be reported by name"
    assert len(results) == 27, "every id is accounted for, attempted or not"
