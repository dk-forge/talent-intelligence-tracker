"""The historical press walker: what it reads, what it refuses, what it costs.

Entirely offline. Every fixture under `tests/fixtures/press_*` is RECORDED from
a live publisher on 2026-07-30 and trimmed, never hand-written, because the
whole difficulty of this collector is that real sitemaps do not behave the way
the specification implies and a hand-written fixture would agree with the
specification.

The three findings these tests pin, each of which was a wrong assumption before
it was measured:

  1. A sitemap index's `<lastmod>` does not locate a month. SmartCompany stamps
     page one of a chronologically paginated set with the whole site's newest
     date while it holds posts from 2006, and its tag sitemaps carry today's
     date while holding no articles at all.
  2. A sitemap has no headline, and the URL slug is not a substitute: it is
     empty for PR TIMES (`/tv/detail/3164`) and opaque for CTech
     (`0,7340,L-3742319,00.html`). So the slug ORDERS and never filters.
  3. The Wayback CDX date range is a CAPTURE date, not a publication date.
     Asking for 2026-07-01..20 returned 2013 and 2014 articles from FINSMES and
     2012 articles from Wamda, because a crawler visiting in July 2026
     re-captures a decade of pages.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import requests

import backfill_press_2026 as walker
from collectors import press_archive
from collectors.national_press import Feed

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _clean_robots_cache():
    """`national_press._ROBOTS_CACHE` is module-global and keyed on origin, so
    one test's recorded robots.txt would otherwise decide the next test's
    answer — and the one that matters most (Disallow) would pass for the wrong
    reason."""
    from collectors import national_press
    national_press._ROBOTS_CACHE.clear()
    yield
    national_press._ROBOTS_CACHE.clear()

SMART = Feed(name="SmartCompany", rss="https://www.smartcompany.com.au/feed/",
             country="Australia", city="Melbourne", coverage="National",
             language="English", source_type="News Organization",
             site="https://www.smartcompany.com.au")


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# --- parsing a real sitemap ------------------------------------------------

def test_a_urlset_parses_into_dated_entries():
    is_index, entries = press_archive.parse_sitemap(
        fixture("press_sitemap_urlset.xml"))
    assert is_index is False
    assert entries, "the recorded urlset produced no entries at all"
    assert all(e.url.startswith("https://") for e in entries)
    assert all(e.day for e in entries), (
        "every entry in this recording carries a <lastmod>; if none parsed, the "
        "date reader has stopped reading W3C datetimes")


def test_an_index_is_recognised_as_an_index_and_not_read_as_articles():
    is_index, children = press_archive.parse_sitemap(
        fixture("press_sitemap_index.xml"))
    assert is_index is True
    assert any("post-sitemap" in c.url for c in children)


def test_the_news_namespace_supplies_titles_for_free():
    """17 of 72 publishers carry <news:title>, and where it is there the article
    head never needs fetching."""
    _, entries = press_archive.parse_sitemap(fixture("press_sitemap_news.xml"))
    titled = [e for e in entries if e.title]
    assert titled, "the recorded news sitemap produced no titles"
    assert all(e.day for e in titled), (
        "a news sitemap states publication_date; losing it would leave these "
        "entries undated and therefore dropped")


def test_a_malformed_sitemap_is_an_empty_one_and_never_an_exception():
    """A publisher's XML being invalid is not a reason to lose the publisher —
    the same reasoning as national_press's regex fallback."""
    assert press_archive.parse_sitemap(b"") == (False, [])
    assert press_archive.parse_sitemap(b"<html>not a sitemap</html>") == (False, [])
    assert press_archive.parse_sitemap(b"<urlset><url><loc>oops") == (False, [])


def test_an_undated_entry_is_dropped_rather_than_stamped_with_today():
    """A row stamped with the collection time files last March's article as
    today's news and corrupts every period column on the dashboard."""
    body = (b"<urlset><url><loc>https://x.test/a-story</loc></url>"
            b"<url><loc>https://x.test/b</loc><lastmod>2026-03-04</lastmod>"
            b"</url></urlset>")
    _, entries = press_archive.parse_sitemap(body)
    assert [e.day for e in entries] == ["", "2026-03-04"]
    kept = press_archive._in_window(entries, "2026-03-01", "2026-03-31")
    assert [e.url for e in kept] == ["https://x.test/b"]


def test_a_date_in_the_path_is_used_only_when_it_is_a_real_calendar_date():
    _, entries = press_archive.parse_sitemap(
        b"<urlset>"
        b"<url><loc>https://x.test/2026/07/15/acme-raises</loc></url>"
        b"<url><loc>https://x.test/12345/67890/thing</loc></url>"
        b"<url><loc>https://x.test/2026/13/01/nope</loc></url>"
        b"</urlset>")
    assert [e.day for e in entries] == ["2026-07-15", "", ""]


# --- locating the window inside an index -----------------------------------

def _child(url, day=""):
    return press_archive.Entry(url=url, day=day)


def test_a_named_period_is_used_without_fetching_anything():
    children = [_child("https://x.test/sitemap-2024-11.xml"),
                _child("https://x.test/sitemap-2026-03.xml"),
                _child("https://x.test/sitemap-2026-07.xml")]

    def never(loc):
        raise AssertionError(f"fetched {loc} when the name already said the period")

    picked = press_archive.locate_children(
        children, "2026-03-01", "2026-03-31", never)
    assert picked == ["https://x.test/sitemap-2026-03.xml"]


def test_a_chronological_family_is_bisected_and_not_scanned():
    """The measurement this exists for: SmartCompany's index lists 105 children
    and July 2026 lives in exactly one of them. A scan is 105 fetches of a
    quarter of a megabyte each; a bisection is about seven."""
    pages = [f"https://x.test/post-sitemap{'' if i == 1 else i}.xml"
             for i in range(1, 90)]
    children = [_child(u, "2026-07-29") for u in pages]

    def contents(loc):
        index = pages.index(loc)
        day = date(2010, 1, 1).toordinal() + index * 60
        lo = date.fromordinal(day)
        hi = date.fromordinal(day + 59)
        return [_child(loc + "#a", lo.isoformat()),
                _child(loc + "#b", hi.isoformat())]

    fetched: list[str] = []

    def read(loc):
        fetched.append(loc)
        return contents(loc)

    target = contents(pages[-1])[0].day
    picked = press_archive.locate_children(children, target, target, read)
    assert len(fetched) <= press_archive.MAX_PROBES, (
        f"located the window in {len(fetched)} fetches; a bisection over 89 "
        f"pages must not exceed {press_archive.MAX_PROBES}")
    assert pages[-1] in picked, "the bisection did not find the page it was aimed at"


def test_a_family_that_is_not_chronological_is_abandoned_rather_than_trusted():
    """An unordered bisection is a wrong answer delivered quietly, so the
    strategy checks its own precondition before using it."""
    pages = [f"https://x.test/mix-sitemap{i}.xml" for i in range(1, 9)]
    children = [_child(u) for u in pages]
    days = {1: "2026-07-01", 2: "2020-01-01", 3: "2026-02-01", 4: "2011-05-05",
            5: "2026-05-01", 6: "2013-01-01", 7: "2026-06-01", 8: "2009-01-01"}

    def read(loc):
        n = int(loc.rsplit("sitemap", 1)[1].split(".")[0])
        return [_child(loc + "#a", days[n])]

    picked = press_archive.locate_children(children, "2026-03-01", "2026-03-31",
                                           read)
    # It falls back to reading children rather than trusting the ordering, and
    # it still stays inside the child budget.
    assert picked
    assert len(picked) <= press_archive.MAX_CHILDREN


def test_the_child_budget_is_never_exceeded():
    children = [_child(f"https://x.test/s{i}.xml", "2026-07-01")
                for i in range(200)]
    picked = press_archive.locate_children(
        children, "2026-07-01", "2026-07-26", lambda loc: [], max_children=8)
    assert len(picked) <= 8
    assert len(set(picked)) == len(picked), "a child was queued twice"


# --- the slug: an ordering signal, never a filter ---------------------------

def test_the_slug_is_empty_for_the_publishers_it_cannot_read():
    """PR TIMES publishes /tv/detail/3164 and CTech publishes
    0,7340,L-3742319,00.html. A slug PREFILTER returns zero for Japan and Israel
    while looking perfectly healthy in English, which is the same silent-zero
    failure test_locale_rotation.py exists to prevent one layer up."""
    assert press_archive.slug_words("https://prtimes.jp/tv/detail/3164") == ""
    assert press_archive.slug_words(
        "https://www.calcalistech.com/ctech/articles/0,7340,L-3723664,00.html") == ""
    assert press_archive.slug_words(
        "https://www.smartcompany.com.au/technology/whatsapp-username-reservations/"
    ) == "whatsapp username reservations"


def test_an_unreadable_slug_still_produces_a_candidate():
    """The property that makes the slug an ordering signal rather than a gate:
    an entry the slug cannot read still becomes a raw dict."""
    entry = press_archive.Entry(url="https://prtimes.jp/tv/detail/3164",
                                day="2026-07-15", title="A社が新CEOを任命")
    raw = press_archive.to_raw(SMART, entry, entry.title, "")
    assert raw["raw_text"].startswith("A社が新CEOを任命")


# --- the raw dict contract -------------------------------------------------

def test_every_raw_dict_sets_raw_text():
    """A collector that forgets raw_text posts zero records and says nothing
    about it. That bug cost the sibling weeks."""
    entry = press_archive.Entry(url="https://www.smartcompany.com.au/x/y/",
                                day="2026-07-15")
    raw = press_archive.to_raw(SMART, entry, "Acme names a new chief executive",
                               "The Melbourne firm said so on Tuesday.")
    assert raw["raw_text"]
    assert "Acme names a new chief executive" in raw["raw_text"]
    assert "The Melbourne firm said so on Tuesday." in raw["raw_text"]


def test_the_publishers_country_is_a_dateline_and_never_a_stored_country():
    """validate.py reads `country` as a sourced value, so writing the outlet's
    home country there would file a US round under Australia."""
    entry = press_archive.Entry(url="https://www.smartcompany.com.au/x/y/",
                                day="2026-07-15")
    raw = press_archive.to_raw(SMART, entry, "Headline", "Teaser")
    assert "country" not in raw
    assert raw["source_country"] == "Australia"
    assert "Melbourne, Australia" in raw["raw_text"]


def test_the_source_url_is_the_publishers_own_article_url():
    entry = press_archive.Entry(url="https://www.smartcompany.com.au/x/y/",
                                day="2026-07-15")
    raw = press_archive.to_raw(SMART, entry, "Headline", "Teaser")
    assert raw["source_url"] == "https://www.smartcompany.com.au/x/y/"
    assert raw["discovery_url"] == raw["source_url"]
    assert raw["published_date"] == "2026-07-15"
    assert raw["collector"] == "press_archive"


def test_the_published_date_comes_from_the_sitemap_and_not_from_the_clock():
    entry = press_archive.Entry(url="https://x.test/a", day="2026-01-04")
    raw = press_archive.to_raw(SMART, entry, "H", "T")
    assert raw["published_date"] == "2026-01-04"
    assert raw["published_date"] != date.today().isoformat()


# --- reading the article's own sharing metadata ----------------------------

def test_metadata_reads_the_two_fields_a_teaser_is_built_from():
    title, description = press_archive.metadata(
        fixture("press_article_head.html"))
    assert title.startswith("81% of employees confess")
    assert "HP and Microsoft" in description


def test_metadata_falls_back_to_the_title_element():
    body = b"<html><head><title>Acme hires a CFO</title></head>"
    title, description = press_archive.metadata(body)
    assert title == "Acme hires a CFO"
    assert description == ""


def test_metadata_never_raises_on_junk():
    assert press_archive.metadata(b"") == ("", "")
    assert press_archive.metadata(b"\x00\xff\xfe garbage") == ("", "")


# --- the guards -----------------------------------------------------------

class _Resp:
    def __init__(self, body=b"", status=200, url="", headers=None):
        self.content = body
        self.status_code = status
        self.url = url
        self.headers = headers or {}
        self.text = body.decode("utf8", "replace")

    def json(self):
        import json
        return json.loads(self.text)


class _Session:
    """A recorded conversation. Any URL not in the map is an error, so a test
    can never silently reach the network."""

    def __init__(self, routes):
        self.routes = routes
        self.asked: list[str] = []

    def get(self, url, **kwargs):
        self.asked.append(url)
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unrecorded request to {url}")


def test_a_sitemap_answering_from_another_domain_is_refused():
    """The dangerous case is not a 404 but a cited URL that answers 200 from
    somebody else's site. botswanaguardian.co.bw became a betting site whose
    feed verified perfectly green."""
    session = _Session({
        "https://www.smartcompany.com.au/robots.txt": _Resp(b"", 404),
        "https://www.smartcompany.com.au/sitemap.xml": _Resp(
            b"<urlset><url><loc>https://bet.example/x</loc></url></urlset>",
            200, url="https://casino.example/sitemap.xml"),
    })
    with pytest.raises(press_archive.DomainDrift):
        press_archive.find_sitemap(SMART, session=session)


def test_a_sitemap_listing_somebody_elses_domain_is_dropped(monkeypatch):
    """A sitemap can list anything, including a syndication partner. The
    receipt has to be the publisher's own document."""
    monkeypatch.setattr(press_archive, "entries_in_window",
                        lambda *a, **k: [
                            press_archive.Entry("https://www.smartcompany.com.au/a/",
                                                "2026-07-02", "Ours"),
                            press_archive.Entry("https://elsewhere.example/b/",
                                                "2026-07-02", "Theirs")])
    items, record = press_archive.read_publisher(
        SMART, "2026-07-01", "2026-07-26", head_pause=0, pause=0)
    assert [i["source_url"] for i in items] == ["https://www.smartcompany.com.au/a/"]
    assert press_archive.STATS["off_domain"] == 1


def test_robots_disallow_stops_the_sitemap_being_fetched():
    session = _Session({
        "https://www.smartcompany.com.au/robots.txt":
            _Resp(b"User-agent: *\nDisallow: /\n"),
    })
    assert press_archive.find_sitemap(SMART, session=session) is None
    assert not any(u.endswith("sitemap.xml") for u in session.asked), (
        "robots.txt disallowed everything and a sitemap was fetched anyway")


def test_robots_txt_is_read_for_the_sitemap_it_declares():
    session = _Session({
        "https://www.smartcompany.com.au/robots.txt": _Resp(
            b"User-agent: *\nAllow: /\nSitemap: https://www.smartcompany.com.au/x.xml\n"),
    })
    assert press_archive.declared_sitemaps(
        "https://www.smartcompany.com.au", session=session) == [
        "https://www.smartcompany.com.au/x.xml"]


def test_a_raw_dict_from_here_survives_the_free_verdict_on_the_write_path():
    """The dict shape is the contract, so it is checked against the real
    `validate.precheck` rather than against a description of it. This is the
    same call `run_collect` makes before any money is spent."""
    from pipeline import validate

    entry = press_archive.Entry(
        url="https://www.smartcompany.com.au/people/acme-names-a-new-cfo/",
        day="2026-07-15")
    raw = press_archive.to_raw(SMART, entry, "Acme names a new CFO",
                               "The Melbourne firm said so on Tuesday.")
    validate.precheck(raw)   # raises Rejected if the shape is wrong


def test_a_sitemap_entry_that_is_only_a_homepage_is_rejected_like_any_other():
    """A homepage is not a receipt. The rule that took six hours to learn holds
    for this collector too, and it holds because the same validator says so."""
    from pipeline import validate

    entry = press_archive.Entry(url="https://www.smartcompany.com.au/",
                                day="2026-07-15")
    raw = press_archive.to_raw(SMART, entry, "SmartCompany", "Business news")
    with pytest.raises(validate.Rejected):
        validate.precheck(raw)


def test_the_collector_writes_no_row_itself():
    """classify -> validate -> store, like everything else. A collector that
    imports the store is a collector that can bypass the pipeline."""
    source = (Path(press_archive.__file__)).read_text()
    assert "from pipeline import store" not in source
    assert "store.store(" not in source


# --- Route B: the archive, and the rule about not answering -----------------

def test_a_throttle_is_unknown_and_never_nothing_there():
    """429 was a live bug in this repo on 2026-07-28: a throttle read as an
    empty result turns an outage into a coverage claim nobody re-checks."""
    session = _Session({press_archive.CDX_URL: _Resp(
        b"", 429, headers={"Retry-After": "120"})})
    with pytest.raises(press_archive.ArchiveUnknown) as exc:
        press_archive.wayback_urls("x.test", "2026-07-01", "2026-07-20",
                                   session=session)
    assert "429" in str(exc.value)
    assert "Retry-After 120" in str(exc.value)


def test_a_gateway_timeout_is_unknown_too_and_that_is_the_one_measured():
    """Measured 2026-07-30: 504 after exactly 60 seconds on globes.co.il and
    tech.eu, and once inside a six-query burst on finsmes.com. No 429 was ever
    observed. The rule covers both because the failure mode is 'did not
    answer', not a particular number."""
    session = _Session({press_archive.CDX_URL: _Resp(b"<html>504</html>", 504)})
    with pytest.raises(press_archive.ArchiveUnknown):
        press_archive.wayback_urls("x.test", "2026-07-01", "2026-07-20",
                                   session=session)


def test_a_network_error_is_unknown_as_well():
    session = _Session({press_archive.CDX_URL: requests.Timeout("slow")})
    with pytest.raises(press_archive.ArchiveUnknown):
        press_archive.wayback_urls("x.test", "2026-07-01", "2026-07-20",
                                   session=session)


def test_an_empty_archive_answer_is_an_empty_list_and_not_an_error():
    """The distinction has to work in both directions or it is not a
    distinction: a 200 with no rows genuinely is nothing archived."""
    session = _Session({press_archive.CDX_URL: _Resp(b"[]", 200)})
    assert press_archive.wayback_urls("x.test", "2026-07-01", "2026-07-20",
                                      session=session) == []


def test_the_cdx_query_does_not_use_the_wildcard_form_that_504s():
    """`url=<domain>/*` with a date range answered 504 after 60 seconds on every
    domain tried; `matchType=prefix` answered 200."""
    captured = {}

    class Recorder:
        def get(self, url, params=None, **kwargs):
            captured.update(params or {})
            return _Resp(b"[]", 200)

    press_archive.wayback_urls("x.test", "2026-07-01", "2026-07-20",
                               session=Recorder())
    assert captured["matchType"] == "prefix"
    assert not captured["url"].endswith("*")


def test_the_archive_date_is_a_capture_date_and_the_module_says_so():
    """The finding that keeps Route B out of the walk. Asking CDX for
    2026-07-01..20 returned 2013 and 2014 FINSMES articles and 2012 Wamda
    articles, because a crawler visiting in July 2026 re-captures a decade of
    pages. A capture window is not a publication window, so the rows it returns
    cannot be filtered to a historical month by the API at all."""
    source = Path(press_archive.__file__).read_text()
    assert "capture" in source.lower(), (
        "nothing in the module warns that a CDX date range is a capture date. "
        "Somebody will use it as a publication filter and quietly collect 2013.")


# --- the cost model --------------------------------------------------------

def test_the_cost_of_a_slice_is_derived_from_measured_prices():
    cheap = walker.window_cost(gated=100, reads=5)
    dear = walker.window_cost(gated=100, reads=40)
    assert dear["usd"] > cheap["usd"] > 0
    # A read is about forty times a gate call, which is why the gate is what is
    # rationed and the read is only backstopped.
    assert walker.READ_USD_PER_ITEM > walker.GATE_USD_PER_ITEM * 30


def test_the_ration_is_derived_from_the_budget_and_not_typed():
    """A ceiling that only spend.py can stop is a ceiling that reads as a plan."""
    one = walker.pass_projection()
    assert one["usd_per_slice"] * 30 <= walker.MONTHLY_WALKER_BUDGET_USD * 1.05, (
        f"one slice a day projects ${one['usd_per_slice'] * 30:.2f} a month "
        f"against a ${walker.MONTHLY_WALKER_BUDGET_USD:.2f} allowance")


def test_this_walker_takes_the_smallest_share_of_the_product_budget():
    """Three walkers share one ~$5/month budget and the daily collector has to
    come first. GDELT holds $1.50 and the Google News walker $1.00."""
    import backfill_gdelt_2026
    import backfill_gnews_2026
    assert walker.MONTHLY_WALKER_BUDGET_USD <= \
        backfill_gnews_2026.MONTHLY_WALKER_BUDGET_USD
    assert (walker.MONTHLY_WALKER_BUDGET_USD
            + backfill_gnews_2026.MONTHLY_WALKER_BUDGET_USD
            + backfill_gdelt_2026.MONTHLY_WALKER_BUDGET_USD) <= 3.10, (
        "the three backfill walkers together now claim more than three fifths "
        "of the product's ~$5 monthly budget")


def test_a_full_depth_sweep_is_more_expensive_than_the_walkers_already_built():
    """The refusal, as arithmetic. If this ever stops being true the header's
    argument has expired and should be re-read rather than trusted."""
    month = walker.candidates_per_month()
    year = walker.window_cost(gated=int(month * 12))["usd"]
    assert year > 4.51, (
        "a full-depth year is now cheaper than GDELT's whole year, so the "
        "refusal in the module header no longer follows from the numbers")


def test_plan_cost_prints_the_refusal_and_calls_nothing(capsys, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("--plan-cost made a request")

    monkeypatch.setattr(requests, "get", forbidden)
    walker.print_cost_plan()
    out = capsys.readouterr().out
    assert "THE REFUSAL, WITH ITS NUMBERS" in out
    assert "NOT armed" in out
    assert "504" in out, "the Route B measurement is missing from the table"


# --- the roster partition --------------------------------------------------

def _feeds(n):
    return [Feed(name=f"pub{i:03d}", rss=f"https://p{i}.test/feed",
                 country="X", city="", coverage="National", language="English",
                 source_type="News Organization", site=f"https://p{i}.test")
            for i in range(n)]


def test_the_roster_order_does_not_depend_on_catalogue_row_order():
    """The cursor is an index into this list. A list ordered by CSV row would
    re-partition the moment somebody inserts a catalogue row in the middle —
    a hole in one slice and a double-collection in another, silently."""
    population = _feeds(30)
    shuffled = list(reversed(population))
    assert [f.name for f in walker.roster(population)] == \
           [f.name for f in walker.roster(shuffled)]


def test_every_publisher_is_visited_exactly_once_across_a_whole_pass():
    population = walker.roster(_feeds(137))
    size = walker.PUBLISHERS_PER_SLICE
    end = walker.last_index(population, size)
    seen: list[str] = []
    for index in range(end + 1):
        seen += [f.name for f in walker.partition(population, index, index, size)]
    assert len(seen) == len(population)
    assert len(set(seen)) == len(seen), "a publisher was walked twice"
    assert set(seen) == {f.name for f in population}


def _cursor_after(publishers_done: int, batch: int, lo: int, hi: int,
                  per_slice: int):
    """The cursor arithmetic the walker runs after its loop, in isolation."""
    if publishers_done >= batch:
        return hi
    if publishers_done >= per_slice:
        return lo + (publishers_done // per_slice) - 1
    return None


def test_a_budget_stop_part_way_through_a_slice_finishes_no_roster_index():
    """The hole this closes. A run that read 5 of 40 publishers and hit its
    budget has finished nothing; advancing the cursor would leave 35 publishers
    unvisited with the run count looking perfect — the same silent hole a date
    cursor produces, in a different unit.

    `backfill_slices.record` turns an unmoved cursor into a `stalled` job and a
    red run, which is the loud outcome that is wanted.
    """
    assert _cursor_after(5, 40, 3, 3, 40) is None
    assert _cursor_after(40, 40, 3, 3, 40) == 3
    # Two roster indices in one run: finishing the first advances by one only.
    assert _cursor_after(40, 80, 3, 4, 40) == 3
    assert _cursor_after(80, 80, 3, 4, 40) == 4
    # A short final slice still completes on its own length.
    assert _cursor_after(13, 13, 16, 16, 40) == 16


def test_the_slice_count_matches_the_roster_size():
    assert walker.roster_slices(653, 40) == 17
    assert walker.last_index(_feeds(653), 40) == 16
    # The last slice is the short one, and it must still be reachable.
    assert len(walker.partition(_feeds(653), 16, 16, 40)) == 13
