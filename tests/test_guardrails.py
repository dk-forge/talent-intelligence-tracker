"""The guardrails that should have caught the $86bn Form D overstatement.

Every case here is written from something that actually happened, and the
names in the vehicle tests are real issuer names off the 998 rows the
correction retracted. Nothing stubs a real module into sys.modules.
"""

from datetime import date, datetime, timezone

import pytest

from pipeline import guardrails, publish, schema

TODAY = date(2026, 7, 29)


def _row(conn, **kw):
    fields = {
        "signal_id": kw.get("signal_id", kw["content_hash"]),
        "headline": "h", "summary": "s", "talent_readthrough": "t",
        "company": kw.get("company", "Acme"),
        "company_key": kw.get("company_key", "acme"),
        "pillar": kw.get("pillar", "company_development"),
        "signal_direction": kw.get("signal_direction", "neutral"),
        "confidence": "reported",
        "source_url": kw.get("source_url", "https://example.com/x"),
        "source_name": "Example",
        "captured_at": kw.get("captured_at", "2026-07-01"),
        "as_of": "2026-07-01",
        "content_hash": kw["content_hash"],
        "collector": kw.get("collector", "sec_form_d_bulk"),
        "published_date": kw.get("published_date", "2026-07-01"),
        "funding_amount_usd": kw.get("funding_amount_usd"),
        "materiality": kw.get("materiality"),
        "is_current": kw.get("is_current", 1),
    }
    cols = ", ".join(fields)
    conn.execute(f"INSERT INTO signals ({cols}) VALUES ({', '.join('?' * len(fields))})",
                 tuple(fields.values()))


@pytest.fixture
def conn(tmp_path):
    c = schema.connect(tmp_path / "g.db")
    yield c
    c.close()


def _fill_lognormal(conn, n=400, base=5_000_000):
    """A plausible funding distribution: n rows spread over three decades of
    dollars, so the fit has something real to measure."""
    for i in range(n):
        _row(conn, content_hash=f"n{i}",
             funding_amount_usd=int(base * (1.02 ** (i % 200)) * (1 + (i % 7) / 10)))
    conn.commit()


# --------------------------------------------------------------------------
# 1. The threshold is derived, never typed
# --------------------------------------------------------------------------

def test_threshold_comes_from_the_distribution_and_not_from_a_constant():
    small = [10 ** 6 + i for i in range(500)]
    large = [10 ** 9 + i for i in range(500)]
    assert (guardrails.derive_amount_threshold(large)["threshold"]
            > guardrails.derive_amount_threshold(small)["threshold"] * 100)


def test_a_thin_corpus_says_so_instead_of_fitting_noise():
    stats = guardrails.derive_amount_threshold([1_000_000, 2_000_000])
    assert stats["derived"] is False
    assert stats["threshold"] == guardrails.FALLBACK_THRESHOLD
    assert "fallback" in stats["reason"]


def test_the_threshold_tightens_under_the_real_contamination_instead_of_relaxing():
    """Replays the shape of the actual defect.

    The 998 retracted Form D vehicles were NOT distinguishable as a population:
    log10 median 6.641 against the clean corpus's 6.737. They arrive as more of
    the same distribution with a heavier extreme tail. Under that, a median and
    MAD threshold moves DOWN ($1.80bn to $1.35bn measured) while a mean and
    standard deviation one moves UP ($2.32bn to $2.42bn) - a ceiling that
    relaxes precisely as the bad rows land.
    """
    import math
    import random
    import statistics

    rng = random.Random(11)
    clean = [int(10 ** rng.gauss(6.74, 0.63)) for _ in range(3000)]
    vehicles = ([int(10 ** rng.gauss(6.64, 0.63)) for _ in range(990)]
                + [10_159_286_124, 9_903_446_852, 8_579_340_479,
                   7_443_571_084, 6_123_908_697, 4_211_924_061,
                   3_872_613_703, 3_033_110_297])

    def mean_sd(values):
        logs = [math.log10(v) for v in values]
        z = statistics.NormalDist().inv_cdf(
            1 - guardrails.EXPECTED_ROWS_ABOVE / len(logs))
        return 10 ** (statistics.mean(logs) + z * statistics.pstdev(logs))

    robust_clean = guardrails.derive_amount_threshold(clean)["threshold"]
    robust_dirty = guardrails.derive_amount_threshold(clean + vehicles)["threshold"]

    assert robust_dirty < robust_clean, "the robust threshold must tighten, not relax"
    assert mean_sd(clean + vehicles) > mean_sd(clean), (
        "the mean-based alternative relaxes; that is why it was not used")
    assert sum(1 for v in vehicles if v > robust_dirty) >= 8, (
        "the extreme vehicles must still be caught after contamination")


def test_the_reasoning_travels_with_the_number():
    stats = guardrails.derive_amount_threshold([int(10 ** (6 + (i % 40) / 20))
                                                for i in range(1000)])
    assert "median" in stats["reason"] and "sigma" in stats["reason"]


# --------------------------------------------------------------------------
# 1b. Implausible single rows are flagged, and reviewable
# --------------------------------------------------------------------------

def test_an_implausible_row_is_flagged_before_it_can_move_a_total(conn):
    _fill_lognormal(conn)
    _row(conn, content_hash="huge", company="KKR Infrastructure Vehicle",
         funding_amount_usd=50_000_000_000)
    conn.commit()

    findings, stats = guardrails.check_amounts(conn)
    assert [f.subject for f in findings] == ["huge"]
    assert f"${stats['threshold']:,}" in findings[0].detail, (
        "a finding has to carry the threshold it was judged against")


def test_an_ordinary_row_is_not_flagged(conn):
    _fill_lognormal(conn)
    _row(conn, content_hash="ordinary", funding_amount_usd=20_000_000)
    conn.commit()
    findings, _ = guardrails.check_amounts(conn)
    assert "ordinary" not in [f.subject for f in findings]


def test_a_flagged_row_is_still_in_the_table(conn):
    """Flag, never drop. The row survives so a human can judge it."""
    _fill_lognormal(conn)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()
    guardrails.check_amounts(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM signals WHERE content_hash = 'huge' AND is_current = 1"
    ).fetchone()[0] == 1


def test_a_genuine_mega_raise_survives_review_and_is_released(conn):
    """ChangXin Memory raised $8.6bn and the row is right.

    It must be reviewable rather than auto-binned, the answer must stick, and
    accepting it must RELEASE the row: it was never marked published, so the
    next run simply sends it. Re-accepting the same row every night is a review
    process nobody runs.
    """
    _fill_lognormal(conn)
    _row(conn, content_hash="changxin", company="ChangXin Memory Technologies",
         collector="national_press", funding_amount_usd=8_600_000_000)
    conn.commit()

    assert "changxin" in guardrails.quarantine(conn, today=TODAY)["quarantined"]

    guardrails.review(conn, "amount/changxin", "accepted", "IPO, figure is real")
    assert "changxin" not in guardrails.quarantine(conn, today=TODAY)["quarantined"]

    guardrails.quarantine(conn, today=TODAY)  # and stays accepted on the next run
    states = [r["state"] for r in conn.execute(
        "SELECT state FROM publish_guardrails WHERE subject = 'changxin'")]
    assert states == ["accepted"]


# --------------------------------------------------------------------------
# 2. Period totals must reconcile
# --------------------------------------------------------------------------

def test_consistent_periods_produce_no_finding(conn):
    for i in range(5):
        _row(conn, content_hash=f"a{i}", published_date="2026-07-28")
    for i in range(5):
        _row(conn, content_hash=f"b{i}", published_date="2026-02-01")
    conn.commit()
    assert guardrails.check_period_totals(conn, TODAY) == []


def test_a_longer_period_holding_less_than_a_shorter_one_is_caught(conn, monkeypatch):
    """The page carried "this quarter 268" against "2026 so far 6,018" beside a
    headline of 14,019. Three numbers that could not all be right."""
    _row(conn, content_hash="a", published_date="2026-07-28")
    conn.commit()

    real = guardrails.glance_matrix

    def broken(c, today=None):
        matrix = real(c, today)
        matrix["cells"]["total"] = [268, 268, 268, 100]   # YTD below the quarter
        return matrix

    monkeypatch.setattr(guardrails, "glance_matrix", broken)
    findings = guardrails.check_period_totals(conn, TODAY)
    assert any(f.subject.startswith("order/total/") for f in findings)


def test_year_to_date_can_never_exceed_all_time(conn, monkeypatch):
    _row(conn, content_hash="a", published_date="2026-07-28")
    conn.commit()
    real = guardrails.glance_matrix

    def broken(c, today=None):
        matrix = real(c, today)
        matrix["cells"]["total"] = [1, 1, 1, 6018]
        matrix["all_time"]["total"] = 5000
        return matrix

    monkeypatch.setattr(guardrails, "glance_matrix", broken)
    assert any(f.subject == "ytd/total"
               for f in guardrails.check_period_totals(conn, TODAY))


def test_a_subset_can_never_exceed_all_updates(conn, monkeypatch):
    """The shape of the original defect: 998 vehicles counted as funding rows
    under a clause scoped differently from the one counting updates."""
    _row(conn, content_hash="a", published_date="2026-07-28")
    conn.commit()
    real = guardrails.glance_matrix

    def broken(c, today=None):
        matrix = real(c, today)
        matrix["cells"]["funded"] = [999, 999, 999, 999]
        matrix["cells"]["total"] = [1, 1, 1, 1]
        return matrix

    monkeypatch.setattr(guardrails, "glance_matrix", broken)
    assert any(f.subject.startswith("subset/funded/")
               for f in guardrails.check_period_totals(conn, TODAY))


def test_a_week_reaching_over_a_month_boundary_is_not_an_error(conn):
    """"This week" starts six days back, so early in a month it holds rows
    "this month" does not. Asserting week-inside-month would fire falsely on
    roughly half the days of the year."""
    early = date(2026, 7, 3)
    _row(conn, content_hash="a", published_date="2026-06-29")
    _row(conn, content_hash="b", published_date="2026-07-02")
    conn.commit()
    assert guardrails.check_period_totals(conn, early) == []


# --------------------------------------------------------------------------
# 3. The printed date span must match the data
# --------------------------------------------------------------------------

def test_a_healthy_span_produces_no_finding(conn):
    _row(conn, content_hash="a", published_date="2026-06-28")
    _row(conn, content_hash="b", published_date="2026-07-28")
    conn.commit()
    assert guardrails.check_date_span(conn, TODAY) == []


def test_the_day_count_must_come_from_the_bounds_it_is_printed_beside(conn, monkeypatch):
    """"Everything here spans 3,318 days, 28 Jun to 28 Jul 2026" - nine years of
    days against thirty days of dates, because the count was measured over the
    whole table and the bounds over the recent window."""
    _row(conn, content_hash="a", published_date="2026-06-28")
    _row(conn, content_hash="b", published_date="2026-07-28")
    conn.commit()

    real = guardrails.span_scopes

    def swapped(c):
        scopes = real(c)
        scopes["view"]["days"] = 3318      # measured on a different row set
        return scopes

    monkeypatch.setattr(guardrails, "span_scopes", swapped)
    findings = guardrails.check_date_span(conn, TODAY)
    assert any(f.subject == "days/view" for f in findings)


def test_the_shown_view_cannot_reach_outside_the_full_range(conn, monkeypatch):
    _row(conn, content_hash="a", published_date="2026-07-28")
    conn.commit()
    real = guardrails.span_scopes

    def wider(c):
        scopes = real(c)
        scopes["view"] = {"lo": "2009-01-01", "hi": "2026-07-28", "days": 6418, "n": 1}
        return scopes

    monkeypatch.setattr(guardrails, "span_scopes", wider)
    assert any(f.subject == "containment"
               for f in guardrails.check_date_span(conn, TODAY))


def test_a_tile_counting_rows_the_printed_range_excludes_is_caught(conn, monkeypatch):
    _row(conn, content_hash="a", published_date="2026-07-28")
    conn.commit()
    real = guardrails.span_scopes

    def truncated(c):
        scopes = real(c)
        scopes["view"] = {"lo": "2025-01-01", "hi": "2025-12-31", "days": 365, "n": 1}
        scopes["all"] = {"lo": "2025-01-01", "hi": "2025-12-31", "days": 365, "n": 1}
        return scopes

    monkeypatch.setattr(guardrails, "span_scopes", truncated)
    findings = guardrails.check_date_span(conn, TODAY)
    assert any(f.subject.startswith("coverage/") for f in findings)


def test_a_live_span_matching_neither_scope_is_caught(conn):
    _row(conn, content_hash="a", published_date="2026-07-28")
    conn.commit()
    findings = guardrails.check_date_span(
        conn, TODAY, live_span={"lo": "2017-06-28", "hi": "2026-07-29"})
    assert any(f.subject == "live/bounds" for f in findings)


def test_a_live_span_matching_a_real_scope_is_accepted(conn):
    _row(conn, content_hash="a", published_date="2026-07-28")
    conn.commit()
    assert guardrails.check_date_span(
        conn, TODAY, live_span={"lo": "2026-07-28", "hi": "2026-07-28"}) == []


# --------------------------------------------------------------------------
# 4. Vehicle and SPV names
# --------------------------------------------------------------------------

# Real issuer names off the 998 rows the Form D correction retracted.
RETRACTED_VEHICLES = [
    "NATIONWIDE PPVUL SEPARATE ACCOUNT 6",
    "Nationwide PPVUL Separate Account - AC1",
    "KKR Private Equity Conglomerate LLC",
    "KKR Infrastructure Conglomerate LLC",
    "PRUCO LIFE INSURANCE CO",
    "NEW YORK LIFE INSURANCE & ANNUITY CORP",
    "AMERICAN GENERAL LIFE INSURANCE CO",
    "100 Villas Drive LLC",
    "1316 6TH STREET MB LLC",
    "259 East Broadway Owner LLC",
    "102-106 S. Washington Street Bubble LLC",
    "Green Courte Real Estate Partners VI, LLC",
    "EQT Private Equity Co LLC",
]

# Also retracted, and deliberately NOT expected here. Their names say nothing:
# "Cottonwood Communities, Inc." was excluded by its Form D industry group and
# "Makena Club LLC" by the offering's own description ("Non-Equity Golf
# Memberships"). A name check that reached them would have to match "club" and
# "communities" outright, which would take real employers with it. Recorded so
# the measured recall below is read as a ceiling and not as a shortfall.
RETRACTED_BUT_NOT_BY_NAME = ["Cottonwood Communities, Inc.", "Makena Club LLC"]

# Real operating employers currently in the corpus. Every one of these raises
# is genuine and must not be flagged, or the queue stops being read.
REAL_EMPLOYERS = [
    "Baseten Labs, Inc.",
    "Saronic Technologies, Inc.",
    "Shield AI Inc",
    "Ramp Business Corp",
    "Impulse Space, Inc.",
    "MatX Inc.",
    "ChangXin Memory Technologies",
    "Sierra Space Corp",
    "groundcover",
    "HawkEye 360, Inc.",
]


@pytest.mark.parametrize("name", RETRACTED_VEHICLES)
def test_a_retracted_vehicle_is_recognised(name):
    assert guardrails.vehicle_match(name), name


@pytest.mark.parametrize("name", REAL_EMPLOYERS)
def test_a_real_employer_is_not(name):
    assert guardrails.vehicle_match(name) is None, name


@pytest.mark.parametrize("name", RETRACTED_BUT_NOT_BY_NAME)
def test_the_names_a_name_check_honestly_cannot_reach(name):
    """Pinned so nobody widens the patterns to "fix" them.

    Both were caught by the Form D industry group and the offering description
    instead. Matching them here would mean matching "communities" and "club",
    which takes operating employers with it. The measured recall of the name
    check on the real retracted set is 229 of 998 rows - but $71.3bn of the
    $85.6bn, because the vehicles are exactly the large ones.
    """
    assert guardrails.vehicle_match(name) is None, name


@pytest.mark.parametrize("name", [
    "Synthetic GIC Program LLC",
    "Acme GICs Trust",
    "BOLI Funding Vehicle LLC",
    "COLI Series Account",
    "Guaranteed Investment Contract Co",
    "AGL Institutional Life",
])
def test_the_abbreviations_the_first_fix_missed(name):
    """The lesson, kept as a test so it cannot be un-learned.

    The first exclusion was written from the spelled-out phrase "guaranteed
    investment contract" and therefore missed the trade's own abbreviation,
    leaving four GIC / BOLI / COLI rows worth $12.4bn as the largest amounts
    remaining. An exclusion written from a spelled-out phrase will miss the
    abbreviation the industry actually uses.
    """
    assert guardrails.vehicle_match(name), name


def test_a_vehicle_named_funding_row_is_flagged_whatever_collected_it(conn):
    """Not a Form D rule wearing a different hat: the collector's filter governs
    what Form D collects, this governs what reaches a headline figure."""
    _row(conn, content_hash="spv", company="404 Riverside Drive LLC",
         collector="google_news", funding_amount_usd=40_000_000)
    _row(conn, content_hash="real", company="Baseten Labs, Inc.",
         collector="google_news", funding_amount_usd=40_000_000)
    conn.commit()
    assert [f.subject for f in guardrails.check_vehicle_names(conn)] == ["spv"]


def test_a_vehicle_name_without_money_is_not_a_funding_row(conn):
    _row(conn, content_hash="x", company="100 Villas Drive LLC")
    conn.commit()
    assert guardrails.check_vehicle_names(conn) == []


# --------------------------------------------------------------------------
# Quarantine: the flagged row is held, the batch is not
# --------------------------------------------------------------------------

def _wp(monkeypatch):
    monkeypatch.setenv("WP_SITE_URL", "https://asktherecruiter.com/blog")
    monkeypatch.setenv("WP_API_KEY", "k" * 40)


def _age_finding(conn, subject, *, hours):
    from datetime import timedelta
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
        timespec="seconds")
    conn.execute("UPDATE publish_guardrails SET first_seen = ? WHERE subject = ?",
                 (stamp, subject))
    conn.commit()


def test_the_clean_rows_publish_and_only_the_flagged_one_is_held(conn, monkeypatch):
    """The change the first two production runs forced.

    Both failed on eight findings while carrying dozens of good records, and one
    of the eight (X.AI, $16.6bn) is a real raise. A guard that halts the product
    every time a genuine mega-round lands is a guard that gets removed.
    """
    _fill_lognormal(conn, n=400)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    _row(conn, content_hash="spv", company="100 Villas Drive LLC",
         funding_amount_usd=40_000_000)
    conn.commit()

    seen: list[str] = []

    def capture(session, site, key, rows):
        seen.extend(r["content_hash"] for r in rows)
        return {"stored": len(rows), "duplicate": 0, "errors": []}

    monkeypatch.setattr(publish, "_post_batch", capture)
    _wp(monkeypatch)

    result = publish.publish(conn)

    assert result["quarantined"] == 2
    assert "huge" not in seen and "spv" not in seen
    assert len(seen) == 400, "every clean row must still go out"
    assert result["stored"] == 400


def test_a_quarantined_row_stays_unpublished_so_it_reaches_no_figure(conn, monkeypatch):
    """The half that must never weaken. It is also why no requeue is needed: the
    row is simply never marked published, so it is re-offered every run."""
    _fill_lognormal(conn, n=400)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()
    monkeypatch.setattr(publish, "_post_batch", lambda *a, **k: {
        "stored": 400, "duplicate": 0, "errors": []})
    _wp(monkeypatch)

    publish.publish(conn)

    assert conn.execute(
        "SELECT published_at FROM signals WHERE content_hash = 'huge'"
    ).fetchone()[0] is None
    assert "huge" in [r["content_hash"] for r in publish.unpublished(conn)]


def test_accepting_a_finding_releases_the_row_on_the_next_run(conn, monkeypatch):
    _fill_lognormal(conn, n=400)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()
    seen: list[str] = []

    def capture(session, site, key, rows):
        seen.extend(r["content_hash"] for r in rows)
        return {"stored": len(rows), "duplicate": 0, "errors": []}

    monkeypatch.setattr(publish, "_post_batch", capture)
    _wp(monkeypatch)

    publish.publish(conn)
    assert "huge" not in seen

    guardrails.review(conn, "amount/huge", "accepted", "read the filing, real")
    publish.publish(conn)
    assert "huge" in seen, "an accepted row publishes itself, with no replay path"


def test_a_quarantine_alone_does_not_fail_the_run(conn, monkeypatch):
    """Exit 0 is the success case: the guard worked, the suspect row is out of
    every figure, and red at that moment would only mean the machine noticed."""
    _fill_lognormal(conn, n=400)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()
    monkeypatch.setattr(publish, "_post_batch", lambda *a, **k: {
        "stored": 400, "duplicate": 0, "errors": []})
    _wp(monkeypatch)

    result = publish.publish(conn)  # must not raise
    assert result["quarantined"] == 1


def test_a_neglected_finding_goes_red_but_only_after_the_clean_rows_are_sent(
        conn, monkeypatch):
    """The escalation must not cost a clean row. "One suspect row does not take
    the batch down with it" has to hold on the day the run goes red too."""
    _fill_lognormal(conn, n=400)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()
    seen: list[str] = []

    def capture(session, site, key, rows):
        seen.extend(r["content_hash"] for r in rows)
        return {"stored": len(rows), "duplicate": 0, "errors": []}

    monkeypatch.setattr(publish, "_post_batch", capture)
    _wp(monkeypatch)

    guardrails.quarantine(conn, today=TODAY)
    _age_finding(conn, "huge", hours=guardrails.HELD_FINDING_GRACE_HOURS + 1)

    with pytest.raises(publish.PublishError, match="grace window"):
        publish.publish(conn)

    assert len(seen) == 400, "the clean rows must be sent before the run goes red"
    assert conn.execute(
        "SELECT COUNT(*) FROM signals WHERE published_at IS NOT NULL"
    ).fetchone()[0] == 400


def test_an_already_live_row_gets_the_shorter_window(conn):
    """A held row is the guard working and nothing is wrong in public. A row
    already on the site is a wrong figure on the page that quarantine cannot
    pull back, so it runs on a much shorter clock."""
    _fill_lognormal(conn, n=400)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.execute("UPDATE signals SET published_at = '2026-07-01' "
                 " WHERE content_hash = 'huge'")
    conn.commit()

    report = guardrails.quarantine(conn, today=TODAY)
    assert [r["subject"] for r in report["live"]] == ["huge"]
    assert report["held"] == []
    assert report["live"][0]["grace_hours"] == guardrails.LIVE_FINDING_GRACE_HOURS

    _age_finding(conn, "huge", hours=guardrails.LIVE_FINDING_GRACE_HOURS + 1)
    assert guardrails.quarantine(conn, today=TODAY)["overdue"]


def test_a_held_row_does_not_inherit_the_live_window(conn):
    _fill_lognormal(conn, n=400)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()

    report = guardrails.quarantine(conn, today=TODAY)
    assert [r["subject"] for r in report["held"]] == ["huge"]
    assert report["overdue"] == []

    _age_finding(conn, "huge", hours=guardrails.LIVE_FINDING_GRACE_HOURS + 1)
    assert guardrails.quarantine(conn, today=TODAY)["overdue"] == [], (
        "a row that never reached the site must not inherit the live window")

    _age_finding(conn, "huge", hours=guardrails.HELD_FINDING_GRACE_HOURS + 1)
    assert guardrails.quarantine(conn, today=TODAY)["overdue"]


def test_an_aggregate_finding_still_halts_because_it_has_no_clean_subset(
        conn, monkeypatch):
    """A row finding names a row to hold back. An aggregate finding says the
    arithmetic of the whole set is wrong, and no subset of a wrong total is
    right, so this one keeps the halting behaviour on purpose."""
    _fill_lognormal(conn, n=400)
    conn.commit()

    real = guardrails.glance_matrix

    def broken(c, today=None):
        matrix = real(c, today)
        matrix["cells"]["total"] = [1, 1, 1, 6018]
        matrix["all_time"]["total"] = 5000
        return matrix

    monkeypatch.setattr(guardrails, "glance_matrix", broken)
    sent = []
    monkeypatch.setattr(publish, "_post_batch", lambda *a, **k: sent.append(1) or {
        "stored": 1, "duplicate": 0, "errors": []})
    _wp(monkeypatch)

    with pytest.raises(publish.PublishError, match="does not add up"):
        publish.publish(conn)
    assert sent == []


def test_the_enrich_path_quarantines_too(conn, monkeypatch):
    """enrich pushes funding_amount_usd onto rows the site already holds, so a
    flagged amount could reach the money total by the back door while publish()
    was carefully not sending it by the front."""
    _fill_lognormal(conn, n=400)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.execute("UPDATE signals SET published_at = '2026-07-01'")
    conn.commit()

    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"updated": 1}

    class FakeSession:
        def post(self, url, json=None, **kw):
            captured.setdefault("hashes", []).extend(
                r["content_hash"] for r in json["rows"])
            return FakeResponse()

    monkeypatch.setattr(publish.requests, "Session", lambda: FakeSession())
    _wp(monkeypatch)

    result = publish.enrich_published(conn)
    assert result["quarantined"] == 1
    assert "huge" not in captured["hashes"]


def test_a_dry_run_records_nothing_and_still_reports(conn, capsys):
    _fill_lognormal(conn)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()
    result = publish.publish(conn, dry_run=True)
    assert result["quarantined"] == 1
    assert conn.execute("SELECT COUNT(*) FROM publish_guardrails").fetchone()[0] == 0
    assert "would quarantine" in capsys.readouterr().out


def test_a_quarantine_is_announced_where_it_cannot_be_missed(conn, capsys):
    _fill_lognormal(conn)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()
    publish.publish(conn, dry_run=True)
    out = capsys.readouterr().out
    assert "::warning::" in out, (
        "a quarantine must annotate the Actions run, not just sit in a log")
    assert "guardrails.py" in out


def test_a_finding_that_stops_firing_is_resolved_not_deleted(conn):
    _fill_lognormal(conn)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()
    guardrails.quarantine(conn, today=TODAY)

    conn.execute("UPDATE signals SET is_current = 0 WHERE content_hash = 'huge'")
    conn.commit()
    guardrails.quarantine(conn, today=TODAY)

    row = conn.execute("SELECT state FROM publish_guardrails "
                       " WHERE subject = 'huge'").fetchone()
    assert row["state"] == "resolved"


def test_a_resolved_finding_that_returns_is_open_again(conn):
    _fill_lognormal(conn)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()
    guardrails.quarantine(conn, today=TODAY)
    conn.execute("UPDATE signals SET is_current = 0 WHERE content_hash = 'huge'")
    conn.commit()
    guardrails.quarantine(conn, today=TODAY)
    conn.execute("UPDATE signals SET is_current = 1 WHERE content_hash = 'huge'")
    conn.commit()
    assert "huge" in guardrails.quarantine(conn, today=TODAY)["quarantined"]
    assert conn.execute("SELECT state FROM publish_guardrails "
                        " WHERE subject = 'huge'").fetchone()["state"] == "open"


# --------------------------------------------------------------------------
# Rejected is a third state, and the day it was a synonym for accepted
# --------------------------------------------------------------------------
#
# For as long as the ledger existed, `quarantine` read `state IN ('accepted',
# 'rejected')` as one thing: answered, therefore released. Accepted means "the
# figure is real, send it" and rejected means "this is not a raise" - opposite
# verdicts with one effect. On an UNPUBLISHED row that inverted the guardrail:
# rejecting was the only verdict that GUARANTEED publication. Measured on the
# committed database 2026-08-13, the next publish run would have sent Nvidia's
# $709bn (an infrastructure financing arrangement, rejected by hand that
# morning) and Oracle's $25bn (a corporate bond issue, likewise) into a live
# corpus totalling $521.65bn.

def test_a_rejected_finding_permanently_withholds_an_unpublished_row(
        conn, monkeypatch):
    """THE LOAD-BEARING ONE. A human said no, so the row never goes out.

    Rejecting a never-published row is the cheapest correction there is: the
    figure has never been in public and simply never leaves. It used to be the
    one verdict that published it.
    """
    _fill_lognormal(conn, n=400)
    _row(conn, content_hash="bond", company="Oracle",
         funding_amount_usd=25_000_000_000)
    _row(conn, content_hash="real", company="Anthropic",
         funding_amount_usd=30_000_000_000)
    conn.commit()

    guardrails.quarantine(conn, today=TODAY)
    guardrails.review(conn, "amount/bond", "rejected", "corporate bond issue")
    guardrails.review(conn, "amount/real", "accepted", "real round, read it")

    seen: list[str] = []

    def capture(session, site, key, rows):
        seen.extend(r["content_hash"] for r in rows)
        return {"stored": len(rows), "duplicate": 0, "errors": []}

    monkeypatch.setattr(publish, "_post_batch", capture)
    _wp(monkeypatch)
    publish.publish(conn)

    assert "bond" not in seen, (
        "a rejected row must never be sent; rejection is the cheapest possible "
        "correction on a row that has never published")
    assert "real" in seen, (
        "accepting must still release the row, or this becomes 'nothing "
        "publishes'")
    assert conn.execute(
        "SELECT published_at FROM signals WHERE content_hash = 'bond'"
    ).fetchone()[0] is None


def test_the_whole_accepted_backlog_ships_in_one_batch(conn, monkeypatch):
    """The shape of the real database on 2026-08-14, in miniature.

    Eight rows accepted by hand across a fortnight, two rejected, one still
    open. publish() takes no limit and is scoped to every unpublished row and
    not to the ones this run collected, so all eight leave together on the next
    run whatever run first flagged them - and exactly the three stay behind.
    """
    _fill_lognormal(conn, n=400)
    accepted = [f"ok{i}" for i in range(8)]
    for i, chash in enumerate(accepted):
        _row(conn, content_hash=chash, company=f"Accepted {i}",
             funding_amount_usd=(i + 5) * 1_000_000_000)
    for chash in ("nvda", "orcl"):
        _row(conn, content_hash=chash, company=chash.upper(),
             funding_amount_usd=709_000_000_000)
    _row(conn, content_hash="open", company="Climate Fund Managers II",
         funding_amount_usd=182_000_000)
    conn.commit()

    guardrails.quarantine(conn, today=TODAY)
    # A note PER ROW, because that is what answering eleven findings by hand
    # looks like and because review() now refuses a note already used to decide
    # a different event. Eight companies sharing one sentence is the shape that
    # withheld $271.5bn on 2026-08-22/23. See tests/test_guardrail_siblings.py.
    for chash in accepted:
        guardrails.review(conn, f"amount/{chash}", "accepted",
                          f"read the filing for {chash}")
    for chash in ("nvda", "orcl"):
        guardrails.review(conn, f"amount/{chash}", "rejected",
                          f"not a round: {chash}")

    seen: list[str] = []

    def capture(session, site, key, rows):
        seen.extend(r["content_hash"] for r in rows)
        return {"stored": len(rows), "duplicate": 0, "errors": []}

    monkeypatch.setattr(publish, "_post_batch", capture)
    _wp(monkeypatch)
    result = publish.publish(conn)

    assert set(accepted) <= set(seen), (
        "an accepted row from an earlier run is still unpublished, so it "
        "belongs in the next batch; nothing scopes the batch to this run")
    assert {"nvda", "orcl"} & set(seen) == set(), "a rejection withholds"
    assert "open" not in seen, "an unanswered finding holds its row back"
    assert result["quarantined"] == 3

    still_out = {r[0] for r in conn.execute(
        "SELECT content_hash FROM signals "
        " WHERE is_current = 1 AND published_at IS NULL")}
    assert still_out == {"nvda", "orcl", "open"}


def test_a_rejected_row_is_quarantined_and_stays_quarantined(conn):
    _fill_lognormal(conn)
    _row(conn, content_hash="spv", company="100 Villas Drive LLC",
         funding_amount_usd=40_000_000)
    conn.commit()
    assert "spv" in guardrails.quarantine(conn, today=TODAY)["quarantined"]
    guardrails.review(conn, "vehicle_name/spv", "rejected", "SPV, not an employer")

    report = guardrails.quarantine(conn, today=TODAY)
    assert "spv" in report["quarantined"]
    assert [r["subject"] for r in report["withheld"]] == ["spv"]
    assert report["held"] == [], "a decided row is not waiting on anybody"


def test_a_rejected_row_never_escalates_and_never_nags(conn):
    """Open and rejected are both held back, and only one of them is a queue.

    An open finding escalates on a grace clock because a person has not
    answered it. A rejected one has been answered, so it must never redden a
    run and never appear in the unreviewed money queue however old it gets.
    """
    _fill_lognormal(conn, n=400)
    _row(conn, content_hash="aum", company="Arch",
         funding_amount_usd=539_000_000_000)
    conn.commit()
    guardrails.quarantine(conn, today=TODAY)
    guardrails.review(conn, "amount/aum", "rejected", "assets under management")
    _age_finding(conn, "aum", hours=guardrails.HELD_FINDING_GRACE_HOURS * 10)

    report = guardrails.quarantine(conn, today=TODAY)
    assert report["overdue"] == []
    assert guardrails.unreviewed_amounts(
        report["held"] + report["live"] + report["withheld"]) == []


def test_a_rejected_row_that_is_already_live_still_needs_a_retraction(conn):
    """The other half, and the reason rejection is not just 'never send it'.

    A row already on the site cannot be withheld - the figure is in public and
    only `retract.py` can pull it back. So a rejected LIVE row keeps the live
    treatment: reported, on the shorter window, red until somebody retracts it.
    """
    _fill_lognormal(conn, n=400)
    _row(conn, content_hash="ipo", company="ChangXin Memory Technologies",
         funding_amount_usd=8_600_000_000)
    conn.execute("UPDATE signals SET published_at = '2026-07-01' "
                 " WHERE content_hash = 'ipo'")
    conn.commit()
    guardrails.quarantine(conn, today=TODAY)
    guardrails.review(conn, "amount/ipo", "rejected", "an IPO is not funding")

    report = guardrails.quarantine(conn, today=TODAY)
    assert [r["subject"] for r in report["live"]] == ["ipo"]
    assert report["withheld"] == []
    assert report["live"][0]["grace_hours"] == guardrails.LIVE_FINDING_GRACE_HOURS

    _age_finding(conn, "ipo", hours=guardrails.LIVE_FINDING_GRACE_HOURS + 1)
    assert [r["subject"] for r in
            guardrails.quarantine(conn, today=TODAY)["overdue"]] == ["ipo"], (
        "a rejected row that is live is a wrong number in public, and nothing "
        "but a retraction fixes it")

    # Retracting is what closes it, exactly as it does today.
    conn.execute("UPDATE signals SET is_current = 0 WHERE content_hash = 'ipo'")
    conn.commit()
    after = guardrails.quarantine(conn, today=TODAY)
    assert after["overdue"] == [] and after["live"] == []


def test_the_read_only_pass_withholds_exactly_what_the_write_path_does(conn):
    """ops_status and the digest read without writing. A rejection that only
    withholds on the write path is a rejection nobody can see coming."""
    _fill_lognormal(conn, n=400)
    _row(conn, content_hash="fundclose", company="Kingswood Capital",
         funding_amount_usd=50_000_000_000)
    conn.commit()
    guardrails.quarantine(conn, today=TODAY)
    guardrails.review(conn, "amount/fundclose", "rejected", "a fund close")

    written = guardrails.quarantine(conn, today=TODAY, write=True)
    read_only = guardrails.quarantine(conn, today=TODAY, write=False)
    assert written["quarantined"] == read_only["quarantined"] == {"fundclose"}
    assert ([r["subject"] for r in written["withheld"]]
            == [r["subject"] for r in read_only["withheld"]] == ["fundclose"])


def test_a_read_only_pass_agrees_with_a_recorded_one(conn):
    """ops_status and the digest both read without writing, and both must show a
    finding as overdue when it is. Computing the age from "now" would make every
    read-only caller report zero, which is exactly where an overdue finding most
    needs to be visible."""
    _fill_lognormal(conn)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()
    guardrails.quarantine(conn, today=TODAY)
    _age_finding(conn, "huge", hours=guardrails.HELD_FINDING_GRACE_HOURS + 1)

    written = guardrails.quarantine(conn, today=TODAY, write=True)
    read_only = guardrails.quarantine(conn, today=TODAY, write=False)
    assert [r["subject"] for r in written["overdue"]] == ["huge"]
    assert [r["subject"] for r in read_only["overdue"]] == ["huge"]


def test_review_refuses_a_state_it_does_not_understand(conn):
    with pytest.raises(ValueError):
        guardrails.review(conn, "amount/x", "probably fine", "")


def test_every_check_is_evaluated_on_one_pass(conn):
    _fill_lognormal(conn)
    result = guardrails.evaluate(conn, today=TODAY)
    assert set(result) >= {"findings", "amount"}
    assert result["amount"]["derived"] is True


def test_no_model_is_called_and_no_network_is_touched(conn, monkeypatch):
    """The whole product runs on $3-5 a month. A guardrail that costs anything
    per row is a guardrail that gets switched off."""
    import requests

    def forbidden(*a, **k):
        raise AssertionError("a guardrail must never make a request")

    monkeypatch.setattr(requests, "get", forbidden)
    monkeypatch.setattr(requests, "post", forbidden)
    _fill_lognormal(conn)
    guardrails.evaluate(conn, today=TODAY)
