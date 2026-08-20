"""The link-rot defences, offline.

No network anywhere in this file. Everything that touches HTTP is either a pure
classifier (link_check.classify, archive_sources.parse_*) or is driven through
an injected fake session, so the whole thing runs in a suite with no egress.

The property these tests exist to defend is not "the checker works". It is that
a checker CANNOT damage the record: the one thing worse than a dead source link
is a checker that reacts to one by editing the row it was meant to protect.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import archive_sources
import link_check
from pipeline import schema, source_links, validate

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "links.db")
    yield connection
    connection.close()


def _signal(url: str, name: str = "The Irish Times", company: str = "Stripe"):
    return validate.build_signal(
        {
            "company": company,
            "pillar": "company_development",
            "signal_direction": "hiring",
            "city": "Dublin",
            "country": "Ireland",
            "confidence": "reported",
            "headline": f"{company} to create 300 new jobs in Dublin",
            "summary": f"{company} will add 300 roles in Dublin.",
            "talent_readthrough": "300 engineering roles entering the Dublin market.",
        },
        {
            "raw_text": f"{company} to create 300 new jobs in Dublin",
            "source_url": url,
            "source_name": name,
            "published_date": "2026-07-20",
        },
        "national_press",
    )


@pytest.fixture
def stocked(conn):
    from pipeline import store as store_module

    for i, url in enumerate([
        "https://www.irishtimes.com/business/one/",
        "https://www.botswanaguardian.co.bw/news/two/",
        "https://www.ctech.co.il/three/",
    ]):
        store_module.store(conn, _signal(url, company=f"Company{i}"))
    conn.commit()
    return conn


# --- the drift guard, which is the whole reason this is not a status check ---

def test_a_hijacked_domain_answers_200_and_is_still_caught():
    """botswanaguardian.co.bw redirects to a betting site whose feed verifies
    perfectly green. Status codes cannot see this and neither can freshness: the
    only signal is that the bytes came from a domain other than the one we
    stored. A cited article that quietly becomes a casino is worse than a 404,
    because a 404 announces itself.
    """
    state, detail = link_check.classify(
        200,
        "https://luckystar-bets.example/welcome",
        "https://www.botswanaguardian.co.bw/news/two/")
    assert state == "drifted"
    assert "luckystar-bets.example" in detail


def test_drift_is_judged_on_the_registrable_domain_not_the_host():
    """A publisher moving www -> m, or adding a subdomain, has not been taken
    over. Reusing the collector's own registrable_domain() is what keeps that
    true, and keeps `co.bw` from making any two Botswana sites compare equal."""
    assert link_check.classify(
        200, "https://m.irishtimes.com/business/one/",
        "https://www.irishtimes.com/business/one/")[0] == "live"


def test_a_consent_gate_is_not_a_takeover():
    """Found on the first real sweep: hln.be bounces to a DPG Media consent
    page on another domain, carrying the article URL back with it in a callback
    parameter. A squatter has no reason to name the document it replaced, so
    that is the tell. Without it, `drifted` degrades into a list of European
    cookie banners and the one signal that matters gets ignored."""
    state, detail = link_check.classify(
        200,
        "https://myprivacy.dpgmedia.be/consent?siteKey=U&callbackUrl="
        "https%3A%2F%2Fwww.hln.be%2Fautobedrijven%2Ftesla~a1217115%2F",
        "https://www.hln.be/autobedrijven/tesla~a1217115/")
    assert state == "walled"
    assert "consent" in detail


def test_a_takeover_that_mentions_nothing_of_ours_is_still_drift():
    assert link_check.classify(
        200, "https://luckystar-bets.example/promo?ref=affiliate",
        "https://www.botswanaguardian.co.bw/news/two/")[0] == "drifted"


def test_the_drift_guard_is_the_collectors_one():
    """One guard, not two. A second copy would drift from the original and the
    divergence would surface as a phantom rot spike."""
    from collectors import national_press

    assert link_check.registrable_domain is national_press.registrable_domain
    assert link_check.robots_allows is national_press.robots_allows


# --- classification --------------------------------------------------------

@pytest.mark.parametrize("code,expected", [
    (200, "live"), (204, "live"), (301, "live"),
    (401, "walled"), (403, "walled"), (429, "walled"),
    (404, "dead"), (410, "dead"),
    (500, "error"), (503, "error"),
    (0, "unreachable"),
])
def test_status_codes_map_to_states(code, expected):
    url = "https://www.irishtimes.com/business/one/"
    final = url if code else ""
    assert link_check.classify(code, final, url)[0] == expected


def test_a_bot_wall_is_not_rot():
    """Counting 403s as rot would report every paywalled publisher in the
    catalogue as broken and bury the two states that actually matter."""
    assert "walled" not in source_links.ROT_STATES
    assert "walled" in source_links.REACHABLE_STATES


def test_only_dead_and_drifted_count_as_rot():
    assert source_links.ROT_STATES == {"dead", "drifted"}


# --- the rule that matters most: a dead link never edits a row -------------

def test_recording_a_dead_link_changes_no_signal(stocked):
    """Deciding what to do about a dead link is a human step, on purpose. An
    automatic reaction to an HTTP code would let a publisher's bad afternoon
    silently delete evidence."""
    before = stocked.execute(
        "SELECT row_id, headline, source_url, is_current, revision, content_hash "
        "  FROM signals ORDER BY row_id").fetchall()

    for url in [r["source_url"] for r in before]:
        source_links.record_check(stocked, url, state="dead", http_status=404,
                                  final_url=url, final_domain="irishtimes.com",
                                  detail="gone", host="www.irishtimes.com")
    stocked.commit()

    after = stocked.execute(
        "SELECT row_id, headline, source_url, is_current, revision, content_hash "
        "  FROM signals ORDER BY row_id").fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    assert stocked.execute(
        "SELECT COUNT(*) FROM signals WHERE is_current = 1").fetchone()[0] == 3


def test_a_drifted_link_does_not_retract_anything(stocked):
    source_links.record_check(
        stocked, "https://www.botswanaguardian.co.bw/news/two/",
        state="drifted", http_status=200,
        final_url="https://luckystar-bets.example/", final_domain="luckystar-bets.example",
        detail="taken over")
    stocked.commit()
    assert stocked.execute(
        "SELECT COUNT(*) FROM signals WHERE is_current = 0").fetchone()[0] == 0


def test_an_unknown_state_is_refused(conn):
    with pytest.raises(ValueError):
        source_links.record_check(conn, "https://example.com/", state="broken-ish")


# --- the ledger ------------------------------------------------------------

def test_the_ledger_is_keyed_on_the_url_not_the_row(stocked):
    """Thousands of SEC rows share a handful of filing index pages. One check
    and one snapshot have to serve every row citing the same document."""
    from pipeline import store as store_module

    shared = "https://www.sec.gov/Archives/edgar/data/1/index.htm"
    for i in range(3):
        store_module.store(stocked, _signal(shared, "SEC EDGAR", f"Filer{i}"))
    stocked.commit()

    urls = source_links.distinct_source_urls(stocked)
    assert len([u for u in urls if u["source_url"] == shared]) == 1
    assert next(u for u in urls if u["source_url"] == shared)["rows_citing"] == 3


def test_checked_urls_leave_the_queue_until_they_are_due_again(stocked):
    first = source_links.check_candidates(stocked, limit=10)
    assert len(first) == 3
    for row in first:
        source_links.record_check(stocked, row["source_url"], state="live",
                                  http_status=200, final_url=row["source_url"])
    stocked.commit()
    assert source_links.check_candidates(stocked, limit=10) == []
    # ...but a URL checked long enough ago comes back round.
    assert len(source_links.check_candidates(stocked, limit=10,
                                             recheck_after_days=0)) == 3


def test_a_run_can_be_pointed_at_several_collectors_at_once(stocked):
    """The interesting population is "the publisher collectors", not any single
    one. Measured 2026-07-29: 29% of publisher URLs were already in Wayback
    against 3% of the SEC and GOV.UK ones, and it is the publisher tail that
    rots while EDGAR keeps its filings indefinitely."""
    from pipeline import store as store_module

    other = _signal("https://news.example/x", "Example News", "Elsewhere")
    stocked.execute("UPDATE signals SET collector = 'google_news' WHERE row_id = 1")
    store_module.store(stocked, other)
    stocked.execute("UPDATE signals SET collector = 'sec_edgar' "
                    " WHERE source_url = 'https://news.example/x'")
    stocked.commit()

    both = source_links.distinct_source_urls(
        stocked, collector="national_press,google_news")
    assert len(both) == 3
    assert "https://news.example/x" not in [r["source_url"] for r in both]


def test_a_url_known_dead_is_still_rechecked(stocked):
    """Outlets restore articles. A checker that stops looking at everything it
    once called dead can only ever report a rot rate that climbs."""
    url = "https://www.ctech.co.il/three/"
    source_links.record_check(stocked, url, state="dead", http_status=404)
    stocked.commit()
    due = source_links.check_candidates(stocked, limit=10, recheck_after_days=0)
    assert url in [r["source_url"] for r in due]


# --- archiving -------------------------------------------------------------

def test_availability_rejects_a_snapshot_of_a_dead_page():
    """A stored 404 is a receipt that the page was already gone when the crawler
    arrived. Accepting one would give a row a fallback link to a photograph of
    nothing."""
    assert archive_sources.parse_availability({"archived_snapshots": {"closest": {
        "available": True, "status": "404",
        "url": "http://web.archive.org/web/2020/https://x.example/"}}}) is None


def test_availability_returns_an_https_permalink():
    got = archive_sources.parse_availability({"archived_snapshots": {"closest": {
        "available": True, "status": "200",
        "url": "http://web.archive.org/web/2020/https://x.example/"}}})
    assert got == "https://web.archive.org/web/2020/https://x.example/"


def test_availability_of_nothing_is_none():
    assert archive_sources.parse_availability({"archived_snapshots": {}}) is None
    assert archive_sources.parse_availability("not a dict") is None


def test_a_429_is_reported_as_throttling_not_as_failure():
    """Wayback throttles anonymous callers constantly. Treating a 429 as a
    permanent failure would burn the attempt budget on URLs that were never
    tried, and mark capturable pages 'unavailable'."""
    assert archive_sources.parse_save_response(429, {}, "") is archive_sources.RATE_LIMITED


def test_a_capture_permalink_is_read_from_content_location():
    assert archive_sources.parse_save_response(
        200, {"Content-Location": "/web/2026/https://x.example/"}, ""
    ) == "https://web.archive.org/web/2026/https://x.example/"


def test_attempts_are_bounded_so_the_queue_can_drain():
    """Some pages Wayback genuinely cannot capture. A queue that never drains
    hides the ones it could."""
    assert source_links.classify_archive_outcome(None, None, 1)[0] == "pending"
    assert source_links.classify_archive_outcome(
        None, None, source_links.MAX_ARCHIVE_ATTEMPTS)[0] == "unavailable"
    state, url = source_links.classify_archive_outcome(
        "https://web.archive.org/web/1/x", None, 99)
    assert (state, url) == ("archived", "https://web.archive.org/web/1/x")


def test_a_later_failure_never_blanks_a_permalink_we_hold(conn):
    url = "https://x.example/a"
    source_links.record_archive(conn, url, state="archived",
                                archive_url="https://web.archive.org/web/1/x")
    source_links.record_archive(conn, url, state="pending", attempts=2)
    assert conn.execute(
        "SELECT archive_url FROM source_links WHERE source_url = ?",
        (url,)).fetchone()[0] == "https://web.archive.org/web/1/x"


def test_archived_and_unavailable_urls_leave_the_queue(stocked):
    gap = source_links.archive_candidates(stocked, limit=10)
    assert len(gap) == 3
    source_links.record_archive(stocked, gap[0]["source_url"], state="archived",
                                archive_url="https://web.archive.org/web/1/x")
    source_links.record_archive(stocked, gap[1]["source_url"], state="unavailable",
                                attempts=source_links.MAX_ARCHIVE_ATTEMPTS)
    source_links.record_archive(stocked, gap[2]["source_url"], state="pending",
                                attempts=1)
    stocked.commit()
    still = [r["source_url"] for r in source_links.archive_candidates(stocked, limit=10)]
    assert still == [gap[2]["source_url"]]


def test_the_archive_projection_adds_a_fallback_and_touches_nothing_else(stocked):
    before = stocked.execute(
        "SELECT row_id, headline, summary, source_url, source_name, confidence, "
        "       country, content_hash, is_current FROM signals ORDER BY row_id"
    ).fetchall()

    url = "https://www.irishtimes.com/business/one/"
    source_links.record_archive(stocked, url, state="archived",
                                archive_url="https://web.archive.org/web/1/one")
    assert source_links.project_archive_urls(stocked) == 1
    stocked.commit()

    after = stocked.execute(
        "SELECT row_id, headline, summary, source_url, source_name, confidence, "
        "       country, content_hash, is_current FROM signals ORDER BY row_id"
    ).fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    assert stocked.execute(
        "SELECT archive_url FROM signals WHERE source_url = ?", (url,)
    ).fetchone()[0] == "https://web.archive.org/web/1/one"
    # Idempotent: a second pass changes nothing.
    assert source_links.project_archive_urls(stocked) == 0


# --- the run, driven by a fake session (still no network) ------------------

class _Response:
    def __init__(self, status, url):
        self.status_code = status
        self.url = url

    def close(self):
        pass


class _Session:
    """Answers robots.txt permissively and everything else from a table."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def get(self, url, **kwargs):
        if url.endswith("/robots.txt"):
            return _Response(404, url)
        self.asked.append(url)
        status, final = self.answers[url]
        if status == 0:
            raise OSError("connection reset")
        return _Response(status, final)


def test_a_run_records_every_state_and_leaves_the_signals_alone(stocked, monkeypatch):
    monkeypatch.setattr(link_check, "robots_allows", lambda url, session=None: True)
    answers = {
        "https://www.irishtimes.com/business/one/":
            (200, "https://www.irishtimes.com/business/one/"),
        "https://www.botswanaguardian.co.bw/news/two/":
            (200, "https://luckystar-bets.example/"),
        "https://www.ctech.co.il/three/": (404, "https://www.ctech.co.il/three/"),
    }
    result = link_check.run(stocked, limit=10, collector=None, dry_run=False,
                            recheck_days=30, shuffle=False, pause=0,
                            session=_Session(answers), sleep=lambda _s: None)
    stocked.commit()

    assert result["checked"] == 3
    assert result["states"] == {"live": 1, "drifted": 1, "dead": 1}
    assert result["rot_pct"] == pytest.approx(66.7)
    assert stocked.execute("SELECT COUNT(*) FROM signals WHERE is_current = 1"
                           ).fetchone()[0] == 3
    assert stocked.execute("SELECT COUNT(*) FROM source_links WHERE state IS NOT NULL"
                           ).fetchone()[0] == 3


def test_a_dry_run_records_nothing(stocked, monkeypatch):
    monkeypatch.setattr(link_check, "robots_allows", lambda url, session=None: True)
    answers = {r["source_url"]: (404, r["source_url"])
               for r in source_links.distinct_source_urls(stocked)}
    link_check.run(stocked, limit=10, collector=None, dry_run=True,
                   recheck_days=30, shuffle=False, pause=0,
                   session=_Session(answers), sleep=lambda _s: None)
    assert stocked.execute("SELECT COUNT(*) FROM source_links").fetchone()[0] == 0


def test_a_disallowed_path_is_neither_fetched_nor_called_broken(stocked, monkeypatch):
    """The publisher told us their terms. Routing around robots.txt is how a
    product whose only asset is credibility loses it, and calling a document
    broken because we did not ask for it properly is the same error inverted."""
    monkeypatch.setattr(link_check, "robots_allows", lambda url, session=None: False)
    session = _Session({})
    result = link_check.run(stocked, limit=10, collector=None, dry_run=False,
                            recheck_days=30, shuffle=False, pause=0,
                            session=session, sleep=lambda _s: None)
    stocked.commit()
    assert session.asked == []
    assert result["states"] == {"robots": 3}
    assert "robots" not in source_links.ROT_STATES


def test_requests_to_one_host_are_spaced_out(stocked, monkeypatch):
    """Politeness: a publisher carrying forty of our citations must not be hit
    forty times back to back."""
    from pipeline import store as store_module

    for i in range(3):
        store_module.store(stocked, _signal(
            f"https://www.irishtimes.com/business/extra{i}/", company=f"Extra{i}"))
    stocked.commit()
    monkeypatch.setattr(link_check, "robots_allows", lambda url, session=None: True)
    answers = {r["source_url"]: (200, r["source_url"])
               for r in source_links.distinct_source_urls(stocked)}

    slept = []
    link_check.run(stocked, limit=20, collector=None, dry_run=True,
                   recheck_days=30, shuffle=False, pause=2.0,
                   session=_Session(answers), sleep=slept.append)
    # Four URLs on irishtimes.com means three waits; the two single-URL hosts
    # cost none. A per-request sleep would have slept six times.
    assert slept == [2.0, 2.0, 2.0]


# --- reporting -------------------------------------------------------------

def test_rot_is_reported_per_publisher(stocked):
    for i in range(4):
        source_links.record_check(stocked, f"https://a.example/{i}",
                                  state="dead" if i < 3 else "live",
                                  http_status=404, host="a.example")
    for i in range(4):
        source_links.record_check(stocked, f"https://b.example/{i}",
                                  state="live", http_status=200, host="b.example")
    stocked.commit()
    worst = source_links.rot_by_publisher(stocked)
    assert worst[0]["host"] == "a.example"
    assert worst[0]["rot_pct"] == 75.0
    assert [w["host"] for w in worst] == ["a.example"]


def test_the_rot_rate_is_over_checked_urls_not_the_whole_corpus(stocked):
    """A rate over everything would fall simply because the checker got slower,
    which is the sort of metric that improves while the thing it measures does
    not."""
    source_links.record_check(stocked, "https://www.ctech.co.il/three/",
                              state="dead", http_status=404)
    stocked.commit()
    summary = source_links.rot_summary(stocked)
    assert summary["distinct_source_urls"] == 3
    assert summary["checked"] == 1
    assert summary["rot_pct"] == 100.0


# --- wiring ----------------------------------------------------------------

def test_the_ledger_merges_instead_of_being_overwritten(tmp_path, stocked):
    """A writer that loses its push resets to origin/main and merges. Without an
    entry in merge_db every observation this job made would be discarded there,
    silently, which is the failure that cost 9,572 rows in July."""
    import merge_db

    theirs = schema.connect(tmp_path / "theirs.db")
    source_links.record_check(theirs, "https://theirs.example/x", state="live",
                              http_status=200)
    theirs.commit()
    theirs.close()

    source_links.record_check(stocked, "https://ours.example/y", state="dead",
                              http_status=404)
    stocked.commit()
    ours_path = Path(stocked.execute("PRAGMA database_list").fetchone()[2])
    stocked.close()

    report = merge_db.merge(ours_path, tmp_path / "theirs.db")
    assert report["source_links_added"] == 1
    merged = schema.connect(tmp_path / "theirs.db")
    urls = {r[0] for r in merged.execute("SELECT source_url FROM source_links")}
    merged.close()
    assert urls == {"https://theirs.example/x", "https://ours.example/y"}


def test_neither_job_schedules_itself():
    """Both are armed since 2026-07-30 — but from schedule-link-hygiene.yml,
    which writes a ticket, never from a cron in these files.

    They write the database, so they hold the single `talent-collect` lock, and
    GitHub keeps exactly ONE pending run per lock. A cron here would be a direct
    dispatch on a timer: it evicts the pending run, or it is evicted and ends
    `cancelled` with zero jobs and inputs GitHub will not disclose, so it cannot
    be replayed. The full argument, and the scheduler's own shape, are in
    tests/test_link_hygiene_schedule.py.
    """
    import yaml

    for name in ("link-check.yml", "archive-sources.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text()
        parsed = yaml.safe_load(text)
        triggers = parsed.get("on") or parsed.get(True)
        assert "schedule" not in triggers, (
            f"{name} schedules itself into the writer lock — see "
            "tests/test_link_hygiene_schedule.py for why that loses runs")
        assert "workflow_dispatch" in triggers
        assert parsed["concurrency"]["group"] == "talent-collect", name
        assert parsed["concurrency"]["cancel-in-progress"] is False, name


def test_the_digest_gives_the_owner_a_link_specific_instruction():
    """A drifted link is neither a parser bug nor decay, so the generic "fix the
    collector" line would send the owner to the wrong file entirely."""
    import health_digest

    buckets = {"ok": [], "stale": [], "unknown_age": [],
               "degraded": [("link_check", "degraded", "2 urls drifted")]}
    _subject, body = health_digest.build_email(buckets, False, 1.0, None, "local")
    assert "ops_status.py" in body and "[2c]" in body
    assert "Do not delete any row" in body


def test_no_model_is_called_by_either_job():
    """The whole defence has to cost nothing: the owner's ceiling is about $5 a
    month for the entire product, and it is all spent on classification."""
    for path in (ROOT / "link_check.py", ROOT / "archive_sources.py",
                 ROOT / "pipeline" / "source_links.py"):
        body = path.read_text()
        for forbidden in ("openrouter", "OPENROUTER_API_KEY", "classify_signal"):
            assert forbidden not in body, f"{path.name} reaches for a model"


# --- coverage, scoped the way the schedule actually runs -------------------

def test_the_scheduled_scope_is_read_from_the_workflow_not_guessed():
    """Two tools now report archive coverage and both must scope it the same.

    ops_status.py used to carry its own copy of this reader. That is the shape
    the staleness leashes were in when the dashboard and the digest disagreed
    about every collector off the 2x/day cron, and a session reading "0.5%
    archived" here and "11% archived" in the weekly email would have no way to
    tell which one was lying.
    """
    scope = source_links.scheduled_archive_scope(ROOT)
    text = (ROOT / ".github" / "workflows" / "archive-sources.yml").read_text()
    assert scope, "no collector scope could be read from the workflow"
    for name in scope:
        assert name in text
    assert "ops_status.py" not in text or "_archive_scope" not in \
        (ROOT / "ops_status.py").read_text(), (
            "ops_status.py has grown a second copy of the scope reader")


def test_coverage_is_measured_over_the_scope_the_schedule_can_reach(stocked):
    """The corpus percentage has a ceiling near 4% and cannot move much.

    ~96% of what we cite is SEC and GOV.UK filings the schedule deliberately
    skips, so a corpus-wide percentage reads a healthy archiver as a stalled one.
    This ratio has a ceiling of 100% and moves when the job does.
    """
    cover = source_links.archive_coverage(stocked, ["national_press"])
    assert cover["in_scope"] == 3
    assert cover["archived"] == 0
    assert cover["never_probed"] == 3, (
        "a URL nothing has ever asked about is not a gap in Wayback, it is a "
        "gap in what we know, and the two size a capture budget differently")

    source_links.record_archive(
        stocked, "https://www.irishtimes.com/business/one/", state="archived",
        archive_url="https://web.archive.org/web/2026/x", attempts=1, probes=1)
    stocked.commit()
    cover = source_links.archive_coverage(stocked, ["national_press"])
    assert cover["archived"] == 1
    assert cover["pct"] == 33.3
    assert cover["newest_snapshot"]


def test_a_collector_outside_the_scope_is_not_counted_against_it(stocked):
    """Widening the scope is an edit to the workflow, never an accident here."""
    assert source_links.archive_coverage(stocked, ["sec_edgar"])["in_scope"] == 0
