"""Re-pointing a row that cites an aggregator at the outlet that reported it.

The 2026-09-16 finding: 79 published rows cited a commercial data provider's
"news note" pages, all surfaced by Google News, all stored because the store's
blocklist had never heard of the host. The provider serves those pages behind a
bot wall, so the canonical route finds nothing, and the event is chased through
the news index instead. Five properties matter, and each is a way this pass
could do damage rather than a correction:

  the worklist is DERIVED from the write path's own predicate;
  a candidate is accepted only on the WHOLE rule (outlet not an aggregator,
    employer in the title, the headline figure in the title, inside the date
    window), and anything short of it is UNKNOWN and applies nothing;
  the content_hash does NOT move, because the masthead suffix comes off with
    the masthead and the hash was taken over the stripped form;
  the site is corrected BEFORE the revision is appended, and the original
    survives at is_current = 0;
  a deployed plugin that drops the fields (today's plugin does, by a standing
    decision) makes the whole pass refuse before writing anything.

Provider hosts are read from the loader's encoded list and never typed here.
"""

from __future__ import annotations

import sqlite3

import pytest

import correct_aggregator_sources as correct
from collectors import national_press
from pipeline import schema, store, validate


def provider_host() -> str:
    hosts = sorted(h for h in national_press._AGGREGATOR_HOSTS if h.startswith("app."))
    assert hosts
    return hosts[0]


def provider_label() -> str:
    """The masthead as the index wrote it on the end of every headline."""
    return provider_host().split(".")[1].capitalize()


ARTICLE = ("Buzz Solutions has raised $20M in Series A funding to scale its grid "
           "inspection AI, the company said on Tuesday.")


def read(**over):
    base = {
        "company": "Buzz Solutions",
        "pillar": "company_development",
        "signal_direction": "hiring",
        "confidence": "reported",
        "headline": f"Buzz Solutions raises $20M Series A to scale grid inspection AI - {provider_label()}",
        "summary": "Buzz Solutions has raised $20M in Series A funding.",
        "talent_readthrough": "A funded AI company will hire.",
        "funding_amount": "$20M",
        "funding_stage": "Series A",
    }
    base.update(over)
    return base


def raw(**over):
    base = {
        "raw_text": ARTICLE,
        "headline": read()["headline"],
        "source_url": "https://www.publisher-example.com/2026/09/buzz-solutions-20m/",
        "source_name": "Publisher Example",
        "published_date": "2026-09-02",
    }
    base.update(over)
    return base


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


def planted(conn, *, published=True, **over):
    """A row as history left it: built through build_signal against a publisher
    URL (the guard now refuses the aggregator at precheck), then the citation
    the July collector actually stored is written back."""
    signal = validate.build_signal(read(**over), raw(**over), "google_news")
    assert store.store(conn, signal) == "stored"
    conn.execute(
        "UPDATE signals SET source_url = ?, source_name = ?, headline = ?, published_at = ? "
        "WHERE signal_id = ?",
        (f"https://{provider_host()}/news/note/buzz-solutions-raises-20m-series-a",
         provider_label(), read(**over)["headline"],
         "2026-09-03 22:10:00" if published else None, signal.signal_id))
    conn.commit()
    return signal


def live(conn, signal_id) -> dict:
    return dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id = ? AND is_current = 1",
        (signal_id,)).fetchone())


def item(title, outlet="https://techcrunch.com", *, name="TechCrunch",
         when="Tue, 02 Sep 2026 14:00:00 GMT", discovery="https://news.google.com/rss/articles/CBMiX"):
    return {"headline": title, "source_url": outlet, "source_name": name,
            "published_date": when, "discovery_url": discovery}


class Recorder:
    """Stands in for the site. Never a stubbed module."""

    def __init__(self, response=None, status=200):
        self.calls: list[dict] = []
        self._response = response if response is not None else {
            "corrected": 1, "unchanged_or_missing": 0, "skipped_no_fields": 0, "errors": []}
        self._status = status

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        recorder = self

        class Resp:
            status_code = recorder._status
            text = "recorded"

            def json(self_inner):
                return recorder._response

        return Resp()


class RefusingSession:
    """The provider's bot wall: every GET is a 403 with no canonical."""

    def __init__(self):
        self.urls: list[str] = []

    def get(self, url, **kw):
        self.urls.append(url)

        class Resp:
            status_code = 403
            content = b"<html>Just a moment...</html>"
            text = "Just a moment..."
        return Resp()


@pytest.fixture(autouse=True)
def wp_config(monkeypatch):
    monkeypatch.setenv("WP_SITE_URL", "https://example.test/blog")
    monkeypatch.setenv("WP_API_KEY", "test-key")


def resolve_to(url):
    def resolve(it):
        it = dict(it)
        it["source_url"] = url
        return it
    return resolve


# --- the worklist ------------------------------------------------------------

def test_the_planted_row_is_a_target_and_a_publisher_row_is_not(conn):
    signal = planted(conn)
    validate.build_signal(read(company="Other Co"), raw(
        source_url="https://www.publisher-example.com/2026/09/other/"), "google_news")
    found = correct.targets(conn)
    assert [r["signal_id"] for r in found] == [signal.signal_id]


def test_the_guard_refuses_the_same_row_at_ingest_now():
    """The forward half: the shape the 79 rows carry no longer reaches a model."""
    with pytest.raises(validate.Rejected, match="aggregator stored as source"):
        validate.precheck(raw(
            source_url=f"https://{provider_host()}/news/note/buzz-solutions-raises-20m"))


# --- the rule ----------------------------------------------------------------

def test_the_figure_is_normalised_across_spellings():
    assert correct.money_tokens("Acme raises $20M") == ["20m"]
    assert correct.money_tokens("Acme raises US$20 million") == ["20m"]
    assert correct.money_tokens("Acme raises €23M Series B") == ["23m"]
    assert correct.money_tokens("Acme raises SEK 55M") == ["55m"]
    assert correct.money_tokens("Acme raises ₹10 crore") == ["10crore"]
    assert correct.money_tokens("Acme raises $1.05B at $13B valuation") == ["1.05b", "13b"]
    # A year is not a figure.
    assert correct.money_tokens("Acme plans 2026 listing") == []


def _row(conn, **over):
    signal = planted(conn, **over)
    return live(conn, signal.signal_id)


def test_the_whole_rule_accepts_and_the_earliest_outlet_is_credited(conn):
    row = _row(conn)
    items = [
        item("Buzz Solutions raises $20M Series A", "https://www.axios.com", name="Axios",
             when="Wed, 03 Sep 2026 09:00:00 GMT"),
        item("Buzz Solutions lands $20 million Series A led by X", "https://techcrunch.com",
             when="Tue, 02 Sep 2026 14:00:00 GMT"),
    ]
    matches, why = correct.judge_candidates(row, items)
    assert why == "ok"
    assert [m["source_name"] for m in matches] == ["TechCrunch", "Axios"]


@pytest.mark.parametrize("bad, reason", [
    (item("Buzz Solutions raises $20M Series A", f"https://{provider_host()}", name="X"),
     "outlet is an aggregator"),
    (item("Grid inspection startup raises $20M Series A"), "employer not in title"),
    (item("Buzz Solutions raises $25M Series A"), "figure not in title"),
    (item("Buzz Solutions raises $20M Series A", when="Sat, 01 Nov 2026 09:00:00 GMT"),
     "outside the date window"),
])
def test_anything_short_of_the_whole_rule_is_unknown(conn, bad, reason):
    row = _row(conn)
    matches, why = correct.judge_candidates(row, [bad])
    assert matches == []
    assert reason in why


def test_a_headline_with_no_figure_needs_a_referee_not_a_guess(conn):
    row = _row(conn, headline=f"Medicall raises seed round for dental AI - {provider_label()}",
               company="Medicall", funding_amount=None)
    matches, why = correct.judge_candidates(row, [item("Medicall raises seed round for dental AI",
                                                      when="Tue, 02 Sep 2026 14:00:00 GMT")])
    assert matches == []
    assert "needs a referee" in why


def test_an_unrecoverable_publisher_url_is_unknown(conn):
    row = _row(conn)
    best, why, matches = correct.chase_publisher(
        row, fetch=lambda q: [item("Buzz Solutions raises $20M Series A")],
        resolve=lambda it: it)   # the redirect did not resolve: outlet homepage only
    assert best is None
    assert "could not be recovered" in why
    assert len(matches) == 1


def test_the_index_is_queried_with_the_masthead_stripped(conn):
    row = _row(conn)
    queries = []

    def fetch(q):
        queries.append(q)
        return []
    correct.chase_publisher(row, fetch=fetch, resolve=lambda it: it)
    assert queries == ["Buzz Solutions raises $20M Series A to scale grid inspection AI"]


# --- the correction ------------------------------------------------------------

def test_the_hash_holds_and_the_masthead_comes_off(conn):
    row = _row(conn)
    signal = correct.corrected_signal(
        row, "https://techcrunch.com/2026/09/02/buzz-solutions/", "TechCrunch")
    assert signal.headline == "Buzz Solutions raises $20M Series A to scale grid inspection AI"
    assert signal.source_name == "TechCrunch"
    assert validate.content_hash(signal.company_key, signal.pillar, signal.published_date,
                                 signal.headline, signal.source_name) == row["content_hash"]


def test_a_hash_that_would_move_is_unsafe(conn):
    # The stored fingerprint disagrees with the re-hash, whatever the cause:
    # the row can never be matched on the site again, so nothing is written.
    row = _row(conn)
    conn.execute("UPDATE signals SET content_hash = 'deadbeef' WHERE row_id = ?", (row["row_id"],))
    conn.commit()
    row = live(conn, row["signal_id"])
    with pytest.raises(correct.Unsafe):
        correct.corrected_signal(row, "https://techcrunch.com/2026/09/02/x/", "TechCrunch")


def test_the_site_is_corrected_first_then_the_revision_is_appended(conn):
    row = _row(conn)
    site = Recorder()
    session = RefusingSession()
    rc = correct.run(str(conn.execute("PRAGMA database_list").fetchone()["file"]),
                     apply=True, session=session, pause=0,
                     fetch=lambda q: [item("Buzz Solutions raises $20M Series A")],
                     resolve=resolve_to("https://techcrunch.com/2026/09/02/buzz-solutions/"),
                     push=lambda r, s: correct.push_citation(r, s, session=site))
    assert rc == 0
    assert len(site.calls) == 1
    sent = site.calls[0]["json"]["rows"][0]
    assert sent["content_hash"] == row["content_hash"]
    assert sent["source_url"] == "https://techcrunch.com/2026/09/02/buzz-solutions/"
    assert sent["source_name"] == "TechCrunch"
    assert provider_label().lower() not in sent["headline"].lower()

    now = live(conn, row["signal_id"])
    assert now["revision"] == 2
    assert now["source_url"] == "https://techcrunch.com/2026/09/02/buzz-solutions/"
    assert now["published_at"] == row["published_at"]
    assert now["content_hash"] == row["content_hash"]
    old = conn.execute("SELECT * FROM signals WHERE row_id = ?", (row["row_id"],)).fetchone()
    assert old["is_current"] == 0
    assert provider_host() in old["source_url"]
    # No page on the provider was requested after its first refusal.
    assert len(session.urls) == 1


def test_an_unpublished_row_never_touches_the_site(conn):
    signal = planted(conn, published=False)
    site = Recorder()
    correct.run(str(conn.execute("PRAGMA database_list").fetchone()["file"]),
                apply=True, session=RefusingSession(), pause=0,
                fetch=lambda q: [item("Buzz Solutions raises $20M Series A")],
                resolve=resolve_to("https://techcrunch.com/2026/09/02/buzz-solutions/"),
                push=lambda r, s: correct.push_citation(r, s, session=site))
    assert site.calls == []
    assert live(conn, signal.signal_id)["revision"] == 2


def test_a_dry_run_writes_nothing_and_posts_nothing(conn):
    row = _row(conn)
    site = Recorder()
    correct.run(str(conn.execute("PRAGMA database_list").fetchone()["file"]),
                apply=False, session=RefusingSession(), pause=0,
                fetch=lambda q: [item("Buzz Solutions raises $20M Series A")],
                resolve=resolve_to("https://techcrunch.com/2026/09/02/buzz-solutions/"),
                push=lambda r, s: correct.push_citation(r, s, session=site))
    assert site.calls == []
    assert live(conn, row["signal_id"])["revision"] == 1


def test_an_unknown_row_is_left_alone_and_the_run_is_red(conn, capsys):
    row = _row(conn)
    rc = correct.run(str(conn.execute("PRAGMA database_list").fetchone()["file"]),
                     apply=True, session=RefusingSession(), pause=0,
                     fetch=lambda q: [], resolve=lambda it: it,
                     push=lambda r, s: pytest.fail("nothing may reach the site"))
    assert rc == 1
    assert live(conn, row["signal_id"])["revision"] == 1
    out = capsys.readouterr().out
    assert "UNKNOWN" in out
    # The run log is public: the provider's name is redacted in every line.
    assert provider_label().lower() not in out.lower()


def test_a_plugin_that_drops_the_fields_refuses_the_whole_pass(conn):
    row = _row(conn)
    old_plugin = Recorder(response={"corrected": 0, "unchanged_or_missing": 0,
                                    "skipped_no_fields": 1, "errors": []})
    signal = correct.corrected_signal(row, "https://techcrunch.com/2026/09/02/x/", "TechCrunch")
    with pytest.raises(correct.PluginTooOld):
        correct.push_citation(row, signal, session=old_plugin)
    assert live(conn, row["signal_id"])["revision"] == 1
