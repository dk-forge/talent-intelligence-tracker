"""The guardrails that should have caught the $86bn Form D overstatement.

Every case here is written from something that actually happened, and the
names in the vehicle tests are real issuer names off the 998 rows the
correction retracted. Nothing stubs a real module into sys.modules.
"""

from datetime import date

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


def test_a_genuine_mega_raise_survives_review_and_stops_blocking(conn):
    """ChangXin Memory raised $8.6bn and the row is right.

    It must be reviewable rather than auto-binned, and the answer must stick:
    re-accepting the same row every night is a review process nobody runs.
    """
    _fill_lognormal(conn)
    _row(conn, content_hash="changxin", company="ChangXin Memory Technologies",
         collector="national_press", funding_amount_usd=8_600_000_000)
    conn.commit()

    with pytest.raises(guardrails.GuardrailTripped):
        guardrails.enforce(conn, today=TODAY)

    guardrails.review(conn, "amount/changxin", "accepted", "IPO, figure is real")
    guardrails.enforce(conn, today=TODAY)  # no longer blocks

    guardrails.enforce(conn, today=TODAY)  # and stays accepted on the next run
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
# The ledger, and the refusal to publish
# --------------------------------------------------------------------------

def test_publishing_is_blocked_and_nothing_is_sent(conn, monkeypatch):
    _fill_lognormal(conn)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()

    sent = []
    monkeypatch.setattr(publish, "_post_batch", lambda *a, **k: sent.append(1) or
                        {"stored": 1, "duplicate": 0, "errors": []})
    monkeypatch.setenv("WP_SITE_URL", "https://asktherecruiter.com/blog")
    monkeypatch.setenv("WP_API_KEY", "k" * 40)

    with pytest.raises(publish.PublishError):
        publish.publish(conn)
    assert sent == [], "a tripped guardrail must stop the send, not annotate it"


def test_the_enrich_path_is_guarded_too(conn, monkeypatch):
    """enrich pushes funding_amount_usd onto rows the site already holds, so it
    can move the money total on its own. It once took the charts from $3.2M to
    $20.79bn."""
    _fill_lognormal(conn)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()
    monkeypatch.setenv("WP_SITE_URL", "https://asktherecruiter.com/blog")
    monkeypatch.setenv("WP_API_KEY", "k" * 40)
    with pytest.raises(publish.PublishError):
        publish.enrich_published(conn)


def test_a_dry_run_records_nothing(conn):
    _fill_lognormal(conn)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()
    with pytest.raises(publish.PublishError):
        publish.publish(conn, dry_run=True)
    assert conn.execute("SELECT COUNT(*) FROM publish_guardrails").fetchone()[0] == 0


def test_a_finding_that_stops_firing_is_resolved_not_deleted(conn):
    _fill_lognormal(conn)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()
    with pytest.raises(guardrails.GuardrailTripped):
        guardrails.enforce(conn, today=TODAY)

    conn.execute("UPDATE signals SET is_current = 0 WHERE content_hash = 'huge'")
    conn.commit()
    guardrails.enforce(conn, today=TODAY)

    row = conn.execute("SELECT state FROM publish_guardrails "
                       " WHERE subject = 'huge'").fetchone()
    assert row["state"] == "resolved"


def test_a_resolved_finding_that_returns_is_open_again(conn):
    _fill_lognormal(conn)
    _row(conn, content_hash="huge", funding_amount_usd=50_000_000_000)
    conn.commit()
    with pytest.raises(guardrails.GuardrailTripped):
        guardrails.enforce(conn, today=TODAY)
    conn.execute("UPDATE signals SET is_current = 0 WHERE content_hash = 'huge'")
    conn.commit()
    guardrails.enforce(conn, today=TODAY)
    conn.execute("UPDATE signals SET is_current = 1 WHERE content_hash = 'huge'")
    conn.commit()
    with pytest.raises(guardrails.GuardrailTripped):
        guardrails.enforce(conn, today=TODAY)
    assert conn.execute("SELECT state FROM publish_guardrails "
                        " WHERE subject = 'huge'").fetchone()["state"] == "open"


def test_a_rejected_finding_also_stops_blocking(conn):
    """Rejecting records the judgement; retract.py is what removes the row, so
    the correction stays visible on the site instead of happening silently."""
    _fill_lognormal(conn)
    _row(conn, content_hash="spv", company="100 Villas Drive LLC",
         funding_amount_usd=40_000_000)
    conn.commit()
    with pytest.raises(guardrails.GuardrailTripped):
        guardrails.enforce(conn, today=TODAY)
    guardrails.review(conn, "vehicle_name/spv", "rejected", "SPV, retracting")
    guardrails.enforce(conn, today=TODAY)


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
