"""ONE ANNOUNCEMENT MUST GET ONE ANSWER, AND ONE NOTE MUST DECIDE ONE THING.

Both halves here are written from rows that are in the live ledger right now.

THE DUPLICATE. A guardrail decision attaches to `content_hash`, which is
company_key|pillar|published_date|normalised-headline. So a second outlet
covering the SAME announcement with a different headline arrives as a brand new
finding carrying no memory of the answer already given. In the ledger today:

    rejected   Alibaba Group Holding $10,200,000,000   (SCMP, 2026-08-23)
    open       Alibaba $10,000,000,000                 (Taipei Times, 2026-08-25)

One event -- HK$80bn of newly issued shares -- reported twice, rounded twice,
keyed twice (`alibaba group holding` against `alibaba`, which is also why
neither dedup layer saw them, both requiring key EQUALITY). It is the fourth
occurrence: two DayOne rows, two Kingswood rows and two Intel rows went the
same way. `siblings_of` is what puts the earlier decision in front of the
reviewer instead of leaving them to answer it a second time from scratch.

THE NOTE. On 2026-08-22/23 an agent answering the amount queue pasted one note
across three unrelated findings, twice over:

    "... timesofoman: 'Micron ... announced a $10 billion investment ...'"
         -> Micron $10bn, Alibaba Group Holding $10.2bn, Lovable $13.3bn
    "... digitimes: 'Nitto Denko will invest JPY28 billion ...'"
         -> Nitto Denko $28bn, Nvidia $150bn, Broadcom $60bn

Six rows, $271.5bn, rejected for good; four of them on reasoning about a
company they have nothing to do with. `review()` accepted any string, so there
was nothing to catch it, and a rejection leaves no trace anywhere a reader
looks. The note IS the evidence for a permanent withholding.

PROVEN BY MUTATION, and the two guards are independent:

  * make `same_event` return False and
    test_the_rejected_alibaba_sibling_is_surfaced fails (0 siblings found);
  * drop the `_shared_note_clash` call in `review()` and
    test_a_note_that_already_decided_another_event_is_refused fails, while
    every sibling test still passes.
"""

from datetime import datetime, timezone

import pytest

from pipeline import guardrails, schema


def _signal(conn, content_hash, company, published_date, amount):
    conn.execute(
        "INSERT INTO signals (signal_id, headline, summary, talent_readthrough,"
        " company, company_key, pillar, signal_direction, confidence,"
        " source_url, source_name, captured_at, as_of, content_hash, collector,"
        " published_date, funding_amount_usd, is_current)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
        (content_hash, "h", "s", "t", company, company.lower(),
         "company_development", "neutral", "reported", "https://example.com/x",
         "Example", "2026-08-01", "2026-08-01", content_hash, "national_press",
         published_date, amount))


def _finding(conn, subject, label, value, state="open", note=None, by=None):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO publish_guardrails (check_name, subject, label, detail,"
        " value, state, first_seen, last_seen, seen, reviewed_at, reviewed_by,"
        " review_note) VALUES ('amount',?,?,'d',?,?,?,?,1,?,?,?)",
        (subject, label, value, state, now, now,
         now if note else None, by, note))


@pytest.fixture
def conn(tmp_path):
    c = schema.connect(tmp_path / "g.db")
    # The real pair, as the ledger holds it.
    _signal(c, "9b9198", "Alibaba Group Holding", "2026-08-23", 10_200_000_000)
    _signal(c, "85a1b6", "Alibaba", "2026-08-25", 10_000_000_000)
    _finding(c, "9b9198", "Alibaba Group Holding $10,200,000,000",
             10_200_000_000, "rejected", note="not a company raise", by="agent")
    _finding(c, "85a1b6", "Alibaba $10,000,000,000", 10_000_000_000, "open")
    # An unrelated finding that shares nothing but a rough order of magnitude.
    _signal(c, "26c3c9", "Lovable", "2026-08-15", 13_300_000_000)
    _finding(c, "26c3c9", "Lovable $13,300,000,000", 13_300_000_000,
             "rejected", note="not a company raise", by="agent")
    c.commit()
    yield c
    c.close()


# --- the duplicate --------------------------------------------------------

def test_the_rejected_alibaba_sibling_is_surfaced(conn):
    kin = guardrails.siblings_of(conn, "85a1b6", "amount")
    labels = [k["label"] for k in kin]
    assert any("Alibaba Group Holding" in x for x in labels), (
        "the OPEN Alibaba $10bn finding did not surface the $10.2bn row that "
        "was already rejected two days earlier. They are one announcement -- "
        "HK$80bn of new shares -- and a reviewer who cannot see the first "
        "answer is being asked to invent a second one. Got: %r" % labels)


def test_a_different_company_at_a_similar_amount_is_not_a_sibling(conn):
    kin = guardrails.siblings_of(conn, "85a1b6", "amount")
    assert not any("Lovable" in (k["label"] or "") for k in kin), (
        "Lovable $13.3bn was offered as the same event as Alibaba $10bn. A "
        "fingerprint that groups on amount alone would put half the mega-round "
        "queue in one bucket and teach a reviewer to ignore the section.")


def test_the_amount_tolerance_does_not_stretch_to_a_different_round(conn):
    _signal(conn, "aaaa11", "Alibaba", "2026-08-26", 30_000_000_000)
    _finding(conn, "aaaa11", "Alibaba $30,000,000,000", 30_000_000_000)
    conn.commit()
    kin = guardrails.siblings_of(conn, "aaaa11", "amount")
    assert kin == [], (
        "a $30bn Alibaba row was called the same event as the $10bn share "
        "sale. Same employer and same fortnight is not one announcement; the "
        "amount has to agree too, or every future Alibaba raise inherits this "
        "one's verdict.")


def test_the_same_employer_a_quarter_later_is_a_different_event(conn):
    _signal(conn, "bbbb22", "Alibaba", "2026-12-25", 10_000_000_000)
    _finding(conn, "bbbb22", "Alibaba $10,000,000,000", 10_000_000_000)
    conn.commit()
    assert guardrails.siblings_of(conn, "bbbb22", "amount") == [], (
        "an identical amount four months later was treated as the same "
        "announcement, which would let one decision withhold a real later "
        "raise for ever.")


def test_a_retracted_sibling_still_counts_as_a_decision(conn):
    """Both Kingswood rows are is_current = 0 and they are the best precedent."""
    conn.execute("UPDATE signals SET is_current = 0 WHERE content_hash = '9b9198'")
    conn.commit()
    kin = guardrails.siblings_of(conn, "85a1b6", "amount")
    assert any("Alibaba Group Holding" in (k["label"] or "") for k in kin), (
        "a sibling whose row was retracted or revised stopped being shown. "
        "The decision still happened and is still the thing a reviewer needs.")


# --- the note -------------------------------------------------------------

def test_a_note_that_already_decided_another_event_is_refused(conn):
    with pytest.raises(guardrails.SharedNoteRefused):
        guardrails.review(conn, "amount/85a1b6", "rejected",
                          "not a company raise", "somebody")
    state = conn.execute(
        "SELECT state FROM publish_guardrails WHERE subject = '85a1b6'"
    ).fetchone()[0]
    assert state == "open", (
        "the refusal did not prevent the write. A guard that raises after "
        "changing the row is not a guard.")


def test_a_note_may_be_reused_across_siblings_of_one_event(conn):
    """Answering a duplicate pair the same way is correct, not a mistake."""
    n = guardrails.review(conn, "amount/85a1b6", "rejected",
                          "not a company raise", "somebody",
                          allow_shared_note=True)
    assert n == 1
    conn.execute("UPDATE publish_guardrails SET review_note = NULL,"
                 " state = 'open' WHERE subject = '85a1b6'")
    conn.execute("DELETE FROM publish_guardrails WHERE subject = '26c3c9'")
    conn.commit()
    # Now the only other holder of that note IS the sibling, so it is allowed
    # without the escape hatch.
    assert guardrails.review(conn, "amount/85a1b6", "rejected",
                             "not a company raise", "somebody") == 1


def test_a_fresh_note_is_never_refused(conn):
    assert guardrails.review(
        conn, "amount/85a1b6", "accepted",
        "Read the Alibaba announcement: HK$80bn from 710m newly issued "
        "shares, primary proceeds to the company.", "somebody") == 1


def test_the_escape_hatch_is_explicit(conn):
    """A shared note must be a decision somebody made, not a default."""
    assert guardrails.review(conn, "amount/85a1b6", "rejected",
                             "not a company raise", "somebody",
                             allow_shared_note=True) == 1
