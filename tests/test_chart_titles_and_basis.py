"""Two properties a live UX audit found broken on the dashboard, pinned.

Both are about a chart SAYING something the data underneath it does not
support, which is a class of defect no rendering test catches: the markup was
valid, the numbers were correctly computed, and the page was wrong anyway.

ONE. EVERY CHART TITLE NAMES ITS UNIT. The geography card shipped titled
"Where the Jobs Are" over a ranking of record counts, so a bar reading
"United Kingdom 7,955" told a reader there were 7,955 jobs there when the
figure was 7,955 updates, most of them one filing. A title is the part of a
chart that travels: it is what a share link, a screenshot and a headline all
carry, and a title naming the wrong quantity is a wrong number written in
words.

TWO. THE TREND'S BASIS CHECK IS BUCKETED BY INGEST DATE. That chart plots our
own collection rate, and the sentence beside it tells a reader how much of a
movement is us reading more. It used to count collector names out of the
trend's own scan, which groups by COALESCE(published_date, DATE(captured_at)):
a collector switched on last week that ingests back-dated articles was
therefore counted as having fed the START of the window. The one confound the
measurement existed to detect was the one shape of it the measurement could
not see, and it then CERTIFIED the rise with "so the movement here is not a
change in how many sources we read".

These are source-text assertions because the suite cannot execute the plugin.
That is weaker than running it and it is not nothing: each one fails loudly if
somebody restores the shape it was written to keep out.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHORTCODES_PHP = (
    ROOT / "wordpress-plugin" / "talent-intelligence-tracker" / "includes" / "shortcodes.php"
)


def read():
    return SHORTCODES_PHP.read_text(encoding="utf-8")


def strip_comments(src):
    """Comments out, so an assertion reads the CODE and not the prose about it.

    Written the first time this file ran: the note explaining why the trend scan
    no longer carries collector names quotes the SQL it removed, and the test
    looking for that SQL found the quotation and failed. A guard that a correct
    tree cannot pass teaches whoever reads CI to discount a red.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def chart_titles(src):
    """Every tit_chart_head() title, and every money card's."""
    titles = re.findall(r"tit_chart_head\(\s*'([^']+)'", src)
    titles += re.findall(r"tit_money_chart\(\s*(?:/\*.*?\*/\s*)?'[a-z]+',\s*'([^']+)'",
                         src, re.S)
    return titles


# The three units this page deals in. "Updates" is a count of rows we hold,
# "Money" is summed US dollars, and the trend is a rate of updates per day.
# Nothing on this page counts jobs or companies, so nothing may be titled as
# though it did.
UNIT_WORDS = ("Updates", "Money")


def test_every_chart_title_names_its_unit():
    titles = chart_titles(read())
    # Eight cards since 2026-08-05: the collection-rate card moved to the
    # sources page and the direction ranking merged into the market trend as
    # its split. If this number moves, a card was added or removed and this
    # test should be read rather than renumbered.
    assert len(titles) == 8, f"expected 8 chart titles, found {len(titles)}: {titles}"
    for t in titles:
        assert any(w in t for w in UNIT_WORDS), (
            f"chart title {t!r} names no unit. Every title on this page says "
            f"whether it counts updates or sums money, because 'Where the Jobs "
            f"Are' over a count of records is how this page last shipped a "
            f"wrong number in words."
        )


def test_no_chart_title_claims_to_count_jobs_or_people():
    """The specific wrong quantities this page has actually claimed."""
    banned = ("Jobs", "Headcount Is Going", "People")
    for t in chart_titles(read()):
        for word in banned:
            assert word not in t, (
                f"chart title {t!r} contains {word!r}. No card on this page "
                f"holds a number of jobs or of people; the headcount card "
                f"counts UPDATES grouped by the direction each one states."
            )


def test_the_trend_basis_is_measured_by_ingest_date():
    src = strip_comments(read())
    assert "function tit_trend_ingest_breadth(" in src, (
        "the trend's basis measurement is gone. If it was deliberately removed, "
        "the sentence it feeds must be gone too: see the note in this file."
    )
    body = src[src.index("function tit_trend_ingest_breadth("):]
    body = body[: body.index("\n}\n")]
    assert "DATE(captured_at)" in body, (
        "the basis measurement must bucket by captured_at, which is when we "
        "wrote the row down."
    )
    assert "published_date" not in body, (
        "the basis measurement must NOT read published_date. Bucketing by "
        "publication is precisely the defect: a collector that arrives late and "
        "back-fills older articles then counts as having fed the start of the "
        "window, which is the confound this measurement exists to detect."
    )


def test_the_trend_scan_no_longer_carries_collector_names():
    """The old, unsound source of the same figure, kept out.

    tit_signal_trend()'s own scan groups by publication date. Anything counting
    collectors out of THAT is measuring the wrong thing, however the sentence
    downstream is worded.
    """
    src = strip_comments(read())
    body = src[src.index("function tit_signal_trend("):]
    body = body[: body.index("\n}\n")]
    assert "GROUP_CONCAT(DISTINCT collector) AS cols" not in body, (
        "the publication-date scan is carrying collector names again. That is "
        "the measurement the audit rejected."
    )


def test_equal_basis_does_not_certify_the_trend_as_market_movement():
    # Whitespace collapsed FIRST. The sentence this keeps out was wrapped across
    # two source lines, so the literal did not match it and this test passed
    # against the very tree it was written to reject. Caught by running it on
    # the pre-fix tree, which is the only way that class of mistake shows up.
    src = re.sub(r"\s+", " ", strip_comments(read()))
    assert "not a change in how many sources we read" not in src, (
        "the certifying claim is back. Nothing this page measures can rule out "
        "a collection change: the same collectors can double what they return, "
        "and a query can widen inside a collector that never changes its name."
    )


def test_the_basis_compares_sets_and_not_two_counts():
    """Three collectors at each end is not necessarily the same three."""
    src = strip_comments(read())
    assert "'sources_same'" in src, (
        "sources_same is gone, so the panel is back to comparing two counts. "
        "One collector stopping while another starts leaves the count untouched "
        "and is still a change in what we read."
    )
    assert "$set_first === $set_last" in src, (
        "sources_same must be a set comparison of the two sorted collector "
        "lists, not a comparison of their sizes."
    )


def test_the_dashboard_no_longer_titles_a_collection_rate_chart():
    """The ops chart left the dashboard for the sources page, 2026-08-05.

    Its title reappearing in shortcodes.php means somebody put the
    collection-rate card back on the dashboard, which the owner removed on the
    judgement that readers do not need an ops metric there. The chart itself
    lives on: sources.php renders it, and the guard for that is in
    tests/php/render_sources.php.
    """
    for title in chart_titles(read()):
        assert "Collected" not in title, (
            f"chart title {title!r} looks like the collection-rate card back on "
            f"the dashboard. It renders on the sources page now; the dashboard "
            f"slot carries the fixed-panel market trend."
        )


SOURCES_PHP = (
    ROOT / "wordpress-plugin" / "talent-intelligence-tracker" / "includes" / "sources.php"
)


def test_the_collection_rate_chart_moved_and_was_not_deleted():
    src = SOURCES_PHP.read_text(encoding="utf-8")
    assert "Updates Collected a Day" in src, (
        "the collection-rate chart is on neither page. It was MOVED to the "
        "sources page, not deleted; if it is being retired on purpose, retire "
        "this test in the same commit and say so in the TECHLOG."
    )
    assert "tit_signal_trend(" in src and "tit_signal_trend_html(" in src, (
        "the sources page must render the same measured series the dashboard "
        "used (tit_signal_trend), not a copy that can drift."
    )


def test_the_market_counts_variant_is_panel_restricted():
    """Raw all-collector counts are never drawn as a market claim.

    The counts variant must narrow its weekly scan to the fixed panel, and the
    panel must be decided by ingest dates (captured_at), never publication
    dates: a collector that arrives late and backfills looks, by publication,
    like it was always there. Same confound, same reason, as
    tit_trend_ingest_breadth() above.
    """
    src = strip_comments(read())
    assert "function tit_market_trend(" in src, (
        "the market trend function is gone. If the chart was deliberately "
        "removed, this file and the render harnesses must change with it."
    )
    body = src[src.index("function tit_market_trend("):]
    body = body[: body.index("\n}\n")]
    flat = re.sub(r"\s+", " ", body)
    assert "MIN(DATE(captured_at))" in flat and "MAX(DATE(captured_at))" in flat, (
        "panel liveness must be read from first-seen and last-seen ingest days"
    )
    assert "'counts'" in flat and "collector IN (" in flat, (
        "the counts variant must restrict its weekly scan to the panel "
        "collectors; a count over every collector is the confound this chart "
        "exists to keep out."
    )
    assert "'share'" in flat, (
        "the composition fallback is gone: with a thin panel the chart must "
        "fall back to shares, never to raw all-collector counts."
    )


def test_the_market_caveat_is_visible_prose_not_note_html():
    """Same defect class as the place caveat: a basis nobody sees is not one."""
    src = strip_comments(read())
    head = src[src.index('<div class="tit-chart tit-chart-nodl" id="chart-market">'):]
    head = head[: head.index('<div class="tit-market-box"')]
    assert 'id="tit-market-caveat"' in head, "the market caveat left the card"
    caveat_at = head.index('id="tit-market-caveat"')
    head_call_at = head.index("tit_chart_head(")
    assert caveat_at > head_call_at, (
        "the market caveat is being passed into tit_chart_head(), which puts "
        "it inside the (i) panel that dashboard.js closes on load."
    )
    assert 'class="tit-chart-caveat"' in head, (
        "and it must carry the visible caveat class, not sit inside "
        ".tit-chart-note."
    )


def test_the_place_caveat_is_not_inside_the_collapsible_note():
    """A retraction nobody has ever seen is not a retraction.

    The one-collector caveat used to be passed into tit_chart_head() as its
    note_html, which puts it inside .tit-chart-note. dashboard.js closes every
    one of those panels on load, so the element computed display:none and
    measured 0x0 on every browser that ran the script.
    """
    src = strip_comments(read())
    head = src[src.index('<div class="tit-chart" id="chart-place">'):]
    head = head[: head.index('<div class="tit-rank"')]
    assert 'id="tit-place-caveat"' in head, (
        "the place caveat left the card entirely."
    )
    # It must be printed as its own element on the card, AFTER the head call,
    # rather than handed to the head as note markup.
    caveat_at = head.index('id="tit-place-caveat"')
    head_call_at = head.index("tit_chart_head(")
    assert caveat_at > head_call_at, (
        "the place caveat is being passed into tit_chart_head() again, which "
        "puts it inside the (i) panel that dashboard.js closes on load."
    )
