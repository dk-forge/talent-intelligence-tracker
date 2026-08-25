"""Publishing must never lose a row.

SQLite is the system of record and WordPress is a rendering surface, so a
failed push has to be resumable: rows stay unpublished until the server has
actually accepted them.

Note: nothing here stubs a real module into sys.modules. Only the module's own
_post_batch is monkeypatched, which pytest undoes per test.
"""

import re

import pytest

from pipeline import publish, schema


@pytest.fixture
def conn(tmp_path):
    c = schema.connect(tmp_path / "p.db")
    for i in range(3):
        c.execute(
            "INSERT INTO signals (signal_id, headline, summary, talent_readthrough,"
            " company, company_key, pillar, signal_direction, confidence, source_url,"
            " source_name, captured_at, as_of, content_hash, collector)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"sig{i}", "h", "s", "t", "Acme", "acme", "company_development",
             "hiring", "reported", f"https://example.com/{i}", "Example",
             "2026-01-01", "2026-01-01", f"hash{i}", "google_news"),
        )
    c.commit()
    yield c
    c.close()


def test_unpublished_returns_everything_at_first(conn):
    assert len(publish.unpublished(conn)) == 3


def test_published_rows_are_not_resent(conn):
    conn.execute("UPDATE signals SET published_at = '2026-01-02' WHERE signal_id = 'sig0'")
    conn.commit()
    ids = [r["signal_id"] for r in publish.unpublished(conn)]
    assert ids == ["sig1", "sig2"]


def test_a_successful_push_marks_rows_published(conn, monkeypatch):
    monkeypatch.setattr(publish, "_post_batch",
                        lambda *a, **k: {"stored": 3, "duplicate": 0, "errors": []})
    monkeypatch.setenv("WP_SITE_URL", "https://asktherecruiter.com/blog")
    monkeypatch.setenv("WP_API_KEY", "k" * 40)

    result = publish.publish(conn)
    assert result["stored"] == 3
    assert publish.unpublished(conn) == []


def test_a_failed_row_stays_unpublished_and_is_retried(conn, monkeypatch):
    """The row that errored must come back next run, not vanish."""
    monkeypatch.setattr(publish, "_post_batch", lambda *a, **k: {
        "stored": 2, "duplicate": 0,
        "errors": [{"index": 1, "error": "source_url is required"}],
    })
    monkeypatch.setenv("WP_SITE_URL", "https://asktherecruiter.com/blog")
    monkeypatch.setenv("WP_API_KEY", "k" * 40)

    result = publish.publish(conn)
    assert len(result["errors"]) == 1
    remaining = [r["signal_id"] for r in publish.unpublished(conn)]
    assert remaining == ["sig1"]


def test_a_duplicate_is_success_not_an_error(conn, monkeypatch):
    monkeypatch.setattr(publish, "_post_batch",
                        lambda *a, **k: {"stored": 0, "duplicate": 3, "errors": []})
    monkeypatch.setenv("WP_SITE_URL", "https://asktherecruiter.com/blog")
    monkeypatch.setenv("WP_API_KEY", "k" * 40)

    result = publish.publish(conn)
    assert result["duplicate"] == 3
    assert publish.unpublished(conn) == []


def test_dry_run_sends_nothing_and_marks_nothing(conn):
    result = publish.publish(conn, dry_run=True)
    assert result["would_send"] == 3
    assert len(publish.unpublished(conn)) == 3


def test_bare_domain_is_rejected(monkeypatch):
    """The root domain is a different application entirely."""
    monkeypatch.setenv("WP_SITE_URL", "https://asktherecruiter.com")
    monkeypatch.setenv("WP_API_KEY", "k" * 40)
    with pytest.raises(publish.PublishError, match="/blog"):
        publish._config()


def test_missing_key_is_rejected(monkeypatch):
    monkeypatch.setenv("WP_SITE_URL", "https://asktherecruiter.com/blog")
    monkeypatch.delenv("WP_API_KEY", raising=False)
    with pytest.raises(publish.PublishError, match="WP_API_KEY"):
        publish._config()


def test_the_request_looks_like_a_browser():
    """ModSecurity on this host blocks python-requests outright."""
    assert "python-requests" not in publish.USER_AGENT
    assert publish.USER_AGENT.startswith("TalentIntel/")


# --- the enrich allowlist, which exists in two places -----------------------

from pathlib import Path  # noqa: E402

_API_PHP = (Path(__file__).parent.parent / "wordpress-plugin"
            / "talent-intelligence-tracker" / "includes" / "api.php").read_text()
_PHP_ALLOWLIST = _API_PHP[_API_PHP.index("function tit_enrichable_columns"):]
_PHP_ALLOWLIST = _PHP_ALLOWLIST[:_PHP_ALLOWLIST.index("\n}")]
_PHP_COLUMNS = set(re.findall(r"'([a-z_]+)'", _PHP_ALLOWLIST))


def test_both_ends_of_enrich_allow_the_same_columns():
    """The allowlist is written twice, once per language, and a column present
    on one side only is silently dropped rather than rejected.

    That is not hypothetical: hq_city and hq_country were filled locally by the
    identity backfill and missing from both lists, so published rows stayed
    invisible to every geographic filter while we already held the answer. It
    took a recall measurement to notice. This is the check that would have.
    """
    assert set(publish.ENRICHABLE) == _PHP_COLUMNS, (
        "pipeline/publish.py ENRICHABLE and tit_enrichable_columns() disagree: "
        f"only in Python {set(publish.ENRICHABLE) - _PHP_COLUMNS}, "
        f"only in PHP {_PHP_COLUMNS - set(publish.ENRICHABLE)}"
    )


def test_enrich_can_never_write_what_a_source_stated():
    """/enrich carries values we COMPUTED or LOOKED UP. `country` is the job
    location and comes only from the source text, so a looked-up headquarters
    must never be written into it: that would turn "where the source says this
    happened" into "where the company is from", with no way back. The site
    unions the two at query time instead, which is reversible."""
    for stated in ("country", "city", "region", "headline", "summary", "company",
                   "source_url", "published_date", "confidence", "signal_direction"):
        assert stated not in publish.ENRICHABLE, stated
        assert f"'{stated}'" not in _PHP_ALLOWLIST, stated


def test_the_headquarters_columns_can_reach_the_site():
    for column in ("hq_city", "hq_country"):
        assert column in publish.ENRICHABLE
        assert column in _PHP_COLUMNS


# --- the enrich loop stops itself before the platform's timeout does --------

def test_the_enrich_run_budget_reports_the_rest_rather_than_being_killed(
        tmp_path, monkeypatch):
    """A run killed by the platform mid-loop gives back nothing: no batch
    count, no error, no clue whether it was hung or just slow. Run
    32835391093 (2026-08-25) did exactly that -- 30 minutes of zero output
    before GitHub cancelled the job. So the loop stops ITSELF first, the same
    way retract.py's RUN_BUDGET_SECONDS does, and says what it did not get
    to."""
    conn = schema.connect(tmp_path / "e.db")
    n = 60  # BATCH_SIZE is 25, so this is 3 batches
    for i in range(n):
        conn.execute(
            "INSERT INTO signals (signal_id, headline, summary, talent_readthrough,"
            " company, company_key, pillar, signal_direction, confidence, source_url,"
            " source_name, captured_at, as_of, content_hash, collector,"
            " published_date, published_at, funding_amount_usd)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"sig{i}", "h", "s", "t", "Acme", "acme", "company_development",
             "hiring", "reported", f"https://example.com/{i}", "Example",
             "2026-01-01", "2026-01-01", f"hash{i}", "google_news",
             "2026-01-01", "2026-01-02", 1_000_000 + i),
        )
    conn.commit()

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"updated": len(sent_batches[-1])}

    sent_batches = []

    class FakeSession:
        def post(self, url, json=None, **kw):
            sent_batches.append(json["rows"])
            return FakeResponse()

    monkeypatch.setattr(publish.requests, "Session", lambda: FakeSession())
    monkeypatch.setenv("WP_SITE_URL", "https://asktherecruiter.com/blog")
    monkeypatch.setenv("WP_API_KEY", "k" * 40)

    # started() consumes the first tick (0). The budget check ahead of batch
    # 0 sees elapsed=400 (<= 600, proceeds); the check ahead of batch 1 sees
    # elapsed=800 (> 600, stops), so only the first batch goes out.
    clock = iter(range(0, 100_000, 400))
    monkeypatch.setattr(publish.time, "monotonic", lambda: next(clock))

    result = publish.enrich_published(conn, budget=600)
    assert result["not_attempted"] == 35, (
        "35 of 60 rows (batches 2 and 3) must be reported as not attempted, "
        f"got {result['not_attempted']}")
    assert result["sent"] == 25, "the batch already in flight when the budget "\
        "tripped must still complete rather than being abandoned mid-send"
    assert len(sent_batches) == 1
