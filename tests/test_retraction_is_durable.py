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
