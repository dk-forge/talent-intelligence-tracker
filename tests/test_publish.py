"""Publishing must never lose a row.

SQLite is the system of record and WordPress is a rendering surface, so a
failed push has to be resumable: rows stay unpublished until the server has
actually accepted them.

Note: nothing here stubs a real module into sys.modules. Only the module's own
_post_batch is monkeypatched, which pytest undoes per test.
"""

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
