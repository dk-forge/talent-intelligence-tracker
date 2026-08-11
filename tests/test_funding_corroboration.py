"""The amount guardrail was quarantining correct answers, and nobody read the queue.

WHAT THIS FILE IS WRITTEN FROM, measured against the committed database on
2026-08-04 and not from a hypothetical.

The single-row amount ceiling is derived from the corpus's own distribution,
which is the right way to derive it. In 2026 that puts it at $6.55bn over 3,928
stored amounts, and $6.55bn is BELOW every real AI mega-round of the year. So
the check flagged Anthropic's $30bn round and Arch's $539bn of assets under
management with equal confidence, and the queue that was supposed to tell them
apart held fifteen rows worth $874.2bn, every one `state='open'` with
`reviewed_at` NULL, one of them re-seen 229 times over five days, while the
live site published $212.5bn. Four fifths of the money we held had never
reached a reader, and the thing holding it was a human review step with no
human on it.

Raising the threshold is the fix that does not work, and the ledger proves it:
sorted by size the queue interleaves real rounds with wrong ones, so any
ceiling that admits Anthropic's $30bn also admits Turkish Airlines' $100bn,
which is a 100bn LIRA capex programme in a $2.3bn story. Size is not what is
wrong with them.

Two conditions are. Whether a FIGURE is right is answered by independent
outlets: Anthropic's $30bn had reuters.com and w.media arrive within three
hours of the row storing, and Anthropic's own newsroom three days later, and
every one of them was discarded by dedup as "duplicate" while the row it
corroborated sat quarantined for wanting exactly that. Whether it is a COMPANY
RAISE is a different question that outlet count cannot answer, and this corpus
proves that too: Kingswood's $4bn fund close was reported by two independent
outlets stating the same $4bn, so corroboration alone would have published a
private equity fund close as a company round.

Both conditions, or the row waits. And the waiting is now visible: see the
deadline tests at the bottom, which are the durable half. A queue nobody reads
is a delete with a nicer name, and that is what these were.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

import health_digest
import ops_status
from pipeline import guardrails, publish, schema, store

TODAY = date(2026, 7, 29)


def _row(conn, **kw):
    """One signals row. Everything the guardrail reads is settable."""
    fields = {
        "signal_id": kw.get("signal_id", kw["content_hash"]),
        "headline": kw.get("headline", "h"),
        "summary": kw.get("summary", "s"),
        "talent_readthrough": "t",
        "company": kw.get("company", "Acme"),
        "company_key": kw.get("company_key", "acme"),
        "pillar": "company_development",
        "signal_direction": "neutral",
        "confidence": "reported",
        "source_url": kw.get("source_url", "https://example.com/x"),
        "source_name": kw.get("source_name", "Example"),
        "captured_at": "2026-07-01",
        "as_of": "2026-07-01",
        "content_hash": kw["content_hash"],
        "collector": "google_news",
        "published_date": "2026-07-01",
        "funding_amount_usd": kw.get("funding_amount_usd"),
        "published_at": kw.get("published_at"),
        "is_current": kw.get("is_current", 1),
    }
    cols = ", ".join(fields)
    conn.execute(
        f"INSERT INTO signals ({cols}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()))


@pytest.fixture
def conn(tmp_path):
    c = schema.connect(tmp_path / "c.db")
    yield c
    c.close()


def _fill_lognormal(conn, n=400, base=5_000_000):
    """A plausible funding distribution, so the ceiling is derived and not the
    stated fallback. Same helper and same shape as tests/test_guardrails.py."""
    for i in range(n):
        _row(conn, content_hash=f"n{i}",
             funding_amount_usd=int(base * (1.02 ** (i % 200)) * (1 + (i % 7) / 10)))
    conn.commit()


def _mega(conn, **kw):
    """One figure above any derived ceiling. $30bn, the Anthropic round."""
    kw.setdefault("content_hash", "mega")
    kw.setdefault("company", "Anthropic")
    kw.setdefault("company_key", "anthropic")
    kw.setdefault("funding_amount_usd", 30_000_000_000)
    kw.setdefault("headline", "Anthropic raises $30bn led by GIC, Coatue")
    kw.setdefault("summary", "Anthropic has raised $30bn.")
    kw.setdefault("source_url", "https://www.siliconrepublic.com/business/anthropic-30bn")
    _row(conn, **kw)


def _flagged(conn):
    """The content hashes the guardrail would hold back, off the REAL path.

    Goes through publish.publish rather than calling check_amounts, because
    what has to be true is that the row reaches the batch - and the batch is
    assembled in publish, from `guard["quarantined"]`. A test that asserted on
    check_amounts alone would pass while the row was still being dropped one
    layer later.
    """
    result = publish.publish(conn, dry_run=True)
    return result["guardrails"]["quarantined"], result


# --------------------------------------------------------------------------
# The rule: two independent outlets, same figure, and a clean class
# --------------------------------------------------------------------------

def test_two_independent_outlets_publish_a_figure_the_ceiling_cannot_explain(conn):
    """THE REGRESSION. Before this rule existed the row below was quarantined,
    and stayed quarantined for five days across 171 evaluations because the
    only way out was a human who never came.

    Two outlets, one company_key, one amount. It publishes itself."""
    _fill_lognormal(conn)
    _mega(conn)
    # The second outlet, arriving through dedup, which is where corroboration
    # actually reaches us and where it was being thrown away.
    store.record_corroboration(
        conn, "mega", source_url="https://www.reuters.com/technology/anthropic-380",
        source_name="Reuters", amount_usd=30_000_000_000, collector="google_news")
    conn.commit()

    quarantined, result = _flagged(conn)
    assert "mega" not in quarantined
    assert result["quarantined"] == 0
    # And it says WHY, rather than silently letting a mega-round through.
    corroborated = result["guardrails"]["amount"]["corroborated"]
    assert [c["content_hash"] for c in corroborated] == ["mega"]
    assert "reuters.com" in corroborated[0]["outlets"]


def test_one_outlet_is_not_corroboration(conn):
    """The state the ledger was actually in. One source, no second opinion, so
    the figure waits for a person - which is the check doing its job."""
    _fill_lognormal(conn)
    _mega(conn)
    conn.commit()

    quarantined, _ = _flagged(conn)
    assert "mega" in quarantined


def test_one_publisher_on_two_subdomains_is_still_one_outlet(conn):
    """Independence is the whole content of the rule. A count that treated
    `finance.example.com` and `www.example.com` as two outlets would inflate
    itself on syndication, which is the one way this could publish a wrong
    figure."""
    _fill_lognormal(conn)
    _mega(conn, source_url="https://www.siliconrepublic.com/a")
    store.record_corroboration(
        conn, "mega", source_url="https://amp.siliconrepublic.com/a",
        source_name="Silicon Republic", amount_usd=30_000_000_000)
    conn.commit()

    quarantined, _ = _flagged(conn)
    assert "mega" in quarantined


def test_an_outlet_reporting_a_different_figure_is_not_corroboration(conn):
    """Dedup matched the ROUND. Whether it matched the NUMBER is a separate
    question, and this is where it gets asked."""
    _fill_lognormal(conn)
    _mega(conn)
    store.record_corroboration(
        conn, "mega", source_url="https://www.reuters.com/x",
        source_name="Reuters", amount_usd=13_000_000_000)
    conn.commit()

    quarantined, _ = _flagged(conn)
    assert "mega" in quarantined


def test_a_rounding_difference_is_the_same_figure(conn):
    """"$29.9bn" and "$30bn" are one round rounded two ways. Read with
    dedupe.AMOUNT_TOLERANCE, the same reader that produced the agreement."""
    _fill_lognormal(conn)
    _mega(conn)
    store.record_corroboration(
        conn, "mega", source_url="https://www.reuters.com/x",
        source_name="Reuters", amount_usd=29_900_000_000)
    conn.commit()

    quarantined, _ = _flagged(conn)
    assert "mega" not in quarantined


def test_a_second_stored_row_counts_as_the_second_outlet(conn):
    """The other channel corroboration reaches us by: a false split leaves two
    rows for one round, and two rows from two publishers at one figure is
    evidence whatever produced it."""
    _fill_lognormal(conn)
    _mega(conn)
    _mega(conn, content_hash="mega2", signal_id="mega2",
          source_url="https://www.reuters.com/technology/anthropic-380")
    conn.commit()

    quarantined, _ = _flagged(conn)
    assert quarantined == set()


# --------------------------------------------------------------------------
# The second condition: outlets cannot vouch for the CLASS
# --------------------------------------------------------------------------

def test_a_fund_close_stays_held_however_many_outlets_repeat_it(conn):
    """Kingswood, measured: businesswire.com and citybiz.co both reported the
    same $4bn raised "Across Two Oversubscribed Middle-Market Funds". An
    investor's own fund close is not a company round, and no amount of
    agreement about the number makes it one. This is why corroboration is one
    of two conditions and not the rule."""
    _fill_lognormal(conn)
    _row(conn, content_hash="fund", company="Kingswood Capital",
         company_key="kingswood capital", funding_amount_usd=9_000_000_000,
         headline="Kingswood Capital Raises $9 Billion Across Two Funds",
         summary="Kingswood has raised $9 billion across two funds.",
         source_url="https://www.businesswire.com/x")
    store.record_corroboration(
        conn, "fund", source_url="https://www.citybiz.co/y",
        source_name="citybiz", amount_usd=9_000_000_000)
    conn.commit()

    quarantined, _ = _flagged(conn)
    assert "fund" in quarantined


def test_assets_under_management_stays_held(conn):
    """Arch: "Surpasses $539 Billion In Private Market Assets" was read as a
    raise. Assets are not a raise, at any size and from any number of
    outlets."""
    _fill_lognormal(conn)
    _row(conn, content_hash="aum", company="Arch", company_key="arch",
         funding_amount_usd=539_000_000_000,
         headline="Arch Surpasses $539 Billion In Private Market Assets",
         summary="Arch has surpassed $539 billion in private market assets.",
         source_url="https://pulse2.com/x")
    store.record_corroboration(conn, "aum", source_url="https://www.reuters.com/x",
                               amount_usd=539_000_000_000)
    conn.commit()

    quarantined, _ = _flagged(conn)
    assert "aum" in quarantined


def test_capex_is_not_a_raise(conn):
    """ASE "lifts 2026 capex to record US$10.5 billion" and Turkish Airlines
    "injects $2bn": money SPENT, arriving through a funding parser."""
    _fill_lognormal(conn)
    for h, headline in (
            ("capex", "ASE lifts 2026 capex to record US$10.5 billion"),
            ("spend", "Turkish Airlines injects $2bn to create 36,000 jobs")):
        _row(conn, content_hash=h, company="X", company_key=h,
             funding_amount_usd=10_500_000_000, headline=headline, summary=headline,
             source_url=f"https://{h}.example.com/x")
        store.record_corroboration(conn, h, source_url=f"https://other-{h}.com/y",
                                   amount_usd=10_500_000_000)
    conn.commit()

    quarantined, _ = _flagged(conn)
    assert {"capex", "spend"} <= quarantined


def test_the_word_funding_is_not_the_word_fund(conn):
    """The veto has to leave the real rounds alone, and every real one says
    "funding": xAI's "Series E funding round", Databricks' "in latest
    funding". A stem match would have vetoed the whole corpus."""
    assert guardrails.not_a_company_round(
        "xAI raises $20 billion in Series E funding round") is None
    assert guardrails.not_a_company_round(
        "Databricks raises $5 billion in latest funding") is None
    assert guardrails.not_a_company_round(
        "A16z Raises $15B In New Funds") == "Funds"


def test_the_class_veto_never_creates_a_finding_of_its_own(conn):
    """It withholds the shortcut and nothing else. A fund close BELOW the
    ceiling was always published and still is: adding a new quarantine class
    would add a second queue with nobody on it, which is the defect this whole
    change exists to remove."""
    _fill_lognormal(conn)
    _row(conn, content_hash="small", company="Smallco", company_key="small",
         funding_amount_usd=200_000_000,
         headline="Small raises $200M across two funds",
         summary="Small raises $200M across two funds.")
    conn.commit()

    quarantined, _ = _flagged(conn)
    assert "small" not in quarantined


# --------------------------------------------------------------------------
# The durable half: the queue is now impossible to ignore
# --------------------------------------------------------------------------

def _age(conn, subject, *, hours):
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
        timespec="seconds")
    conn.execute("UPDATE publish_guardrails SET first_seen = ? WHERE subject = ?",
                 (stamp, subject))
    conn.commit()


def test_ops_status_goes_red_on_an_amount_nobody_has_answered(conn, capsys):
    """The durable fix. A finding open past the deadline makes the tool every
    session runs first exit non-zero, and names the row and the dollars.

    Before this, fifteen findings worth $874.2bn sat open for days inside a
    192-hour grace window while ops_status printed "held row(s) are inside
    their grace window; runs stay green until then" and returned no problems.
    """
    _fill_lognormal(conn)
    _mega(conn)
    conn.commit()
    guardrails.quarantine(conn, write=True)
    _age(conn, "mega", hours=guardrails.AMOUNT_REVIEW_DEADLINE_HOURS + 1)

    problems = ops_status._report_guardrails(conn)
    out = capsys.readouterr().out

    assert problems, "an unanswered funding figure must make ops_status exit 2"
    assert "30.00bn" in out           # the dollar figure, named
    assert "Anthropic" in out         # the row, named
    assert "amount/mega" in out       # the key to answer it with


def test_ops_status_stays_green_inside_the_deadline(conn):
    """Red has to mean neglect, not "the machine noticed". A finding that is
    hours old is the guardrail working and says so."""
    _fill_lognormal(conn)
    _mega(conn)
    conn.commit()
    guardrails.quarantine(conn, write=True)
    _age(conn, "mega", hours=guardrails.AMOUNT_REVIEW_DEADLINE_HOURS - 1)

    assert ops_status._report_guardrails(conn) == []


def test_an_answered_finding_does_not_go_red_however_old(conn):
    """Accepting or rejecting is the answer. Once given, age stops mattering -
    otherwise the deadline would nag forever about decisions already made."""
    _fill_lognormal(conn)
    _mega(conn)
    conn.commit()
    guardrails.quarantine(conn, write=True)
    _age(conn, "mega", hours=guardrails.AMOUNT_REVIEW_DEADLINE_HOURS * 10)
    guardrails.review(conn, "amount/mega", "accepted", "real round", "test")

    assert ops_status._report_guardrails(conn) == []


def test_the_digest_mails_every_unanswered_figure_and_never_truncates(conn):
    """"... and 9 more" is not a fact anybody acts on. The summary above the
    list may truncate; this list may not, because it is the list of figures
    being withheld from the product."""
    rows = [
        {"check_name": "amount", "subject": f"h{i}", "label": f"Company{i} ${i}bn",
         "detail": "", "value": float(i) * 1e9, "already_live": False,
         "age_hours": guardrails.AMOUNT_REVIEW_DEADLINE_HOURS + 10,
         "grace_hours": guardrails.HELD_FINDING_GRACE_HOURS}
        for i in range(1, 13)
    ]
    subject, body = health_digest.build_email(
        {"ok": [], "stale": [], "degraded": [], "zero": [], "unknown_age": []},
        False, 2.0, None, "local", rows)

    for i in range(1, 13):
        assert f"Company{i}" in body, f"Company{i} was dropped from the email"
        assert f"amount/h{i}" in body
    assert "$" in subject and "unanswered" in subject


def test_the_deadline_ignores_a_finding_whose_age_is_unknown(conn):
    """Absence of a timestamp is not evidence of freshness. Counting an
    ageless row would be the one shape that makes this always fire; skipping
    it is the one that makes it always pass. It is skipped, and the row is
    still quarantined by every other path - so nothing reaches a reader."""
    rows = [{"check_name": "amount", "subject": "x", "value": 1e10,
             "age_hours": None, "grace_hours": 192, "already_live": False}]
    assert guardrails.unreviewed_amounts(rows) == []


def test_only_the_money_queue_carries_the_short_deadline(conn):
    """The vehicle-name queue is a handful of insurance separate accounts
    nobody is waiting on. Giving every check a 48-hour deadline is how an
    escalation becomes background noise."""
    rows = [{"check_name": "vehicle_name", "subject": "v", "value": 1e8,
             "age_hours": 1000.0, "grace_hours": 192, "already_live": False}]
    assert guardrails.unreviewed_amounts(rows) == []
