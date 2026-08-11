"""The funnel that decides "filter problem or source problem".

Every case here is synthetic and offline. The audit's job is to place a gold
miss in exactly one bucket, and each bucket sends the owner to a different piece
of work — backfill, plumbing, or sourcing — so a miscategorised item is a
misdirected week. The ordering between buckets is itself a decision (a document
we FETCHED is a filter finding even if the publisher is unwired), so it is
pinned.
"""

from datetime import date

import pytest

from analysis.recall import rejection_audit as audit

CATALOGUE = {
    "swept": {"swept-outlet.com": "Swept Outlet"},
    "known": {"swept-outlet.com": "Swept Outlet",
              "researched.co.il": "Researched Daily"},
    "backstop_countries": {"FJ"},
    "rows": 2,
}
# national_press started on the 29th, google_news on the 27th with a 7-day
# window, so the widest reach is the 20th.
FIRST_RUN = {"google_news": date(2026, 7, 27), "national_press": date(2026, 7, 29)}


def place(url, event_date, *, seen=None, cited=None, fetched=0, country="US"):
    return audit.classify_miss(
        {"id": "x", "country": country, "signal_type": "funding",
         "event_date": event_date, "source_name": "Outlet", "source_url": url},
        seen=seen or {}, cited=cited or {}, catalogue=CATALOGUE,
        by_domain={url and audit.domain_of(url): fetched},
        first_run=FIRST_RUN, feed_backlog_days=3)


# --- the buckets -------------------------------------------------------------

def test_a_document_we_fetched_and_dropped_is_a_filter_finding():
    got = place("https://swept-outlet.com/a", "2026-07-25",
                seen={"https://swept-outlet.com/a": ("national_press", "rejected")})

    assert got["stage"] == "fetched_then_dropped"
    assert got["outcome"] == "rejected" and got["dropped_by"] == "national_press"
    assert audit.ANSWER[got["stage"]] == "filter"


def test_a_row_we_already_hold_is_a_matching_defect_and_not_a_gap():
    """If this ever fires, the gold set is scoring a MISS on a document that is
    on the live page. That is the recall matcher's problem, not coverage's."""
    got = place("https://swept-outlet.com/a", "2026-07-25",
                cited={"https://swept-outlet.com/a": 1})

    assert got["stage"] == "stored_unmatched"
    assert "matching" in audit.ANSWER[got["stage"]]


def test_a_superseded_row_is_not_counted_as_a_gap_either():
    got = place("https://swept-outlet.com/a", "2026-07-25",
                cited={"https://swept-outlet.com/a": 0})
    assert got["stage"] == "stored_not_current"


def test_an_event_older_than_every_route_is_a_history_problem():
    """The 1st of July is nineteen days before the widest route reaches."""
    got = place("https://swept-outlet.com/a", "2026-07-01")

    assert got["stage"] == "outside_our_history"
    assert got["earliest_reachable"] == "2026-07-20"
    assert got["widest_route"] == "google_news"
    assert "backfill" in audit.ANSWER[got["stage"]]


def test_a_swept_publisher_inside_the_window_is_a_plumbing_problem():
    got = place("https://swept-outlet.com/a", "2026-07-25", fetched=6)

    assert got["stage"] == "feed_read_item_missed"
    assert got["domain_is_swept_feed"] is True
    assert got["urls_fetched_from_this_domain"] == 6
    # On the 25th the publisher's own feed was NOT yet being read — national
    # press first ran on the 29th and an RSS backlog is three days — so the only
    # live route was the Google News query. Naming it is the point: "our feed
    # dropped it" and "a search query never matched it" are different fixes.
    assert got["live_routes"] == ["google_news"]
    assert place("https://swept-outlet.com/a", "2026-07-27",
                 fetched=6)["live_routes"] == ["google_news", "national_press"]


def test_a_researched_publisher_with_no_feed_is_the_cheap_half_of_sourcing():
    got = place("https://researched.co.il/a", "2026-07-25")

    assert got["stage"] == "publisher_not_wired"
    assert got["domain_in_catalogue"] and not got["domain_is_swept_feed"]
    assert audit.ANSWER[got["stage"]].startswith("source")


def test_a_publisher_we_have_never_heard_of_is_the_source_answer():
    got = place("https://nobody-knows-this.example/a", "2026-07-25")

    assert got["stage"] == "publisher_unknown"
    assert audit.ANSWER[got["stage"]] == "source (not researched)"


def test_a_backstop_country_gets_the_twenty_one_day_search_window():
    """The backstop searches 21 days back, so it reaches further than the feeds
    do — but only for the countries that have no publisher feed at all."""
    got = place("https://nobody-knows-this.example/a", "2026-07-10", country="FJ")
    assert got["stage"] == "publisher_unknown"        # inside the 21d reach

    same_date_elsewhere = place("https://nobody-knows-this.example/a",
                                "2026-07-10", country="US")
    assert same_date_elsewhere["stage"] == "outside_our_history"


# --- the ordering ------------------------------------------------------------

def test_fetching_beats_everything_that_could_be_said_about_the_publisher():
    """An unknown publisher whose article we DID fetch is a filter finding.
    Ordering the other way would hide every drop behind a sourcing excuse."""
    url = "https://nobody-knows-this.example/a"
    got = place(url, "2026-07-01", seen={url: ("google_news", "rejected")})

    assert got["stage"] == "fetched_then_dropped"


# --- the domain rule ---------------------------------------------------------

@pytest.mark.parametrize("host, expected", [
    ("en.globes.co.il", "globes.co.il"),
    ("www.calcalistech.com", "calcalistech.com"),
    ("feeds.example.com", "example.com"),
    ("smartcompany.com.au", "smartcompany.com.au"),
    ("tech.eu", "tech.eu"),
])
def test_a_feed_host_and_an_article_host_resolve_to_one_publisher(host, expected):
    """The feed lives on one host and the articles on another often enough that
    matching on the exact host would report a swept publisher as unsourced."""
    assert audit.registrable_domain(host) == expected


# --- the verdict -------------------------------------------------------------

def test_the_verdict_names_its_own_confidence():
    from collections import Counter

    sentence = audit.verdict(Counter({"outside_our_history": 51,
                                      "feed_read_item_missed": 7,
                                      "publisher_not_wired": 12,
                                      "publisher_unknown": 11}), 81)

    assert "HISTORY" in sentence
    assert "HIGH confidence" in sentence and "MEDIUM" in sentence


def test_the_audit_reads_no_answers_out_of_the_gold_set():
    """The whole measurement is worthless if the feed list is derived from the
    misses. `load_catalogue` takes one argument and it is the catalogue path."""
    import inspect

    assert list(inspect.signature(audit.load_catalogue).parameters) == ["path"]
    source = inspect.getsource(audit.classify_miss)
    for leak in ("verified", "amount_usd", "matched_row", "goldset"):
        assert leak not in source
