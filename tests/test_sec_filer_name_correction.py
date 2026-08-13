"""Re-issuing the published 8-K rows whose headline carries a mangled filer name.

Three properties matter. A corrected row must say exactly what a freshly
collected one would say, now that collectors/sec_edgar parses the name
correctly. It must move only the headline and the values DERIVED from the
headline, because anything else would be a republication wearing a correction's
name. And it must never INVENT a name: the eaten-parenthetical rows lost
characters, and a script that fills them in from a guess is a worse defect than
the one it closes.
"""

from __future__ import annotations

import pytest

import correct_sec_filer_name as correct
from pipeline import store, schema, validate

FILING_BODY = (
    "Item 5.02 Departure of Directors or Certain Officers; Election of Directors. "
    "On June 29, 2026 the Board appointed a new Chief Financial Officer."
)

# The two live shapes, as they are actually stored.
TICKER_BLOCK = ("BED BATH & BEYOND, INC.  (BBBY, BBBY-WT)"
                " 8-K filing (Item 5.02): officer or director change")
EATEN = "Jerash Holdings , Inc. 8-K filing (Item 5.02): officer or director change"


def stored(headline, company, **over):
    raw = {
        "raw_text": FILING_BODY,
        "headline": headline,
        "source_url": "https://www.sec.gov/Archives/edgar/data/1/2/d8k.htm",
        "source_name": "SEC EDGAR",
        "published_date": "2026-06-29",
        "country": "United States",
    }
    raw.update(over.pop("raw", {}))
    read = {
        "company": company,
        "pillar": "leadership_change",
        "signal_direction": "neutral",
        "confidence": "verified",
        "headline": headline,
        "summary": f"{company} appointed a new Chief Financial Officer.",
        "talent_readthrough": "A finance leadership seat has changed hands.",
    }
    read.update(over)
    signal = validate.build_signal(read, raw, correct.COLLECTOR)
    return signal


@pytest.fixture
def conn(tmp_path):
    connection = schema.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def publish_row(conn, signal):
    assert store.store(conn, signal) == "stored"
    conn.execute("UPDATE signals SET published_at = '2026-07-01' WHERE signal_id = ?",
                 (signal.signal_id,))
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id = ?", (signal.signal_id,)).fetchone())


# --- what the corrected name is, and where it refuses to guess -------------

def test_a_ticker_list_is_cut_off_the_stored_headline():
    row = {"headline": TICKER_BLOCK, "company": "Bed Bath & Beyond, Inc."}
    assert correct.corrected_name(row) == "BED BATH & BEYOND, INC."


def test_a_ticker_list_whose_delimiter_the_model_collapsed_is_still_cut():
    """Four live rows read '... Inc. (CCLD, CCLDO) 8-K filing ...' with ONE
    space, because build_signal takes the model's echo of the headline over the
    collector's. A ticker LIST is unambiguous at any spacing."""
    row = {"headline": "CareCloud, Inc. (CCLD, CCLDO)" + correct.SUFFIX,
           "company": "CareCloud, Inc."}
    assert correct.is_mangled(row)
    assert correct.corrected_name(row) == "CareCloud, Inc."


def test_a_single_collapsed_token_is_not_assumed_to_be_a_ticker():
    """One space and one token is the ACUITY INC. (DE) shape, and guessing
    there is the defect this change exists to undo."""
    for name in ("ACUITY INC. (DE)", "Western Asset Diversified Income Fund (WDI)",
                 "Grayscale Litecoin Trust (LTC)"):
        row = {"headline": name + correct.SUFFIX, "company": name}
        assert not correct.is_mangled(row), name


def test_an_eaten_parenthetical_is_restored_only_when_the_company_proves_it():
    row = {"headline": EATEN, "company": "Jerash Holdings (US), Inc."}
    assert correct.corrected_name(row) == "Jerash Holdings (US), Inc."


def test_an_eaten_parenthetical_is_never_guessed():
    """The stored company must reproduce the mangled name under the OLD rule.

    "Jerash Holdings Limited" is a plausible name and not this filer's, so the
    script must refuse rather than write it onto a published row.
    """
    row = {"headline": EATEN, "company": "Jerash Holdings Limited"}
    with pytest.raises(correct.Unprovable):
        correct.corrected_name(row)


def test_a_headline_the_model_rewrote_is_not_this_pass_s_business():
    """605 live rows carry the model's own headline. It read the document and
    found something specific in it; that is not a parser artefact."""
    row = {"headline": "Masimo to be Acquired by Danaher", "company": "Masimo"}
    assert correct.filer_name(row["headline"]) is None
    assert not correct.is_mangled(row)
    with pytest.raises(correct.Unprovable):
        correct.corrected_name(row)


def test_a_clean_row_is_not_a_target():
    row = {"headline": "ACME CORP 8-K filing (Item 5.02): officer or director change",
           "company": "Acme Corp"}
    assert not correct.is_mangled(row)


def test_a_legitimate_parenthetical_in_a_clean_headline_is_left_alone():
    """The fixed collector now writes these, and they must not look mangled."""
    for name in ("Jerash Holdings (US), Inc.", "ACUITY INC. (DE)",
                 "Super Group (SGHC) Ltd", "HUTCHMED (China) Ltd",
                 "Western Asset Diversified Income Fund (WDI)"):
        row = {"headline": name + correct.SUFFIX, "company": name}
        assert not correct.is_mangled(row), name


# --- what a corrected ROW says ---------------------------------------------

def test_the_corrected_row_moves_the_headline_and_only_what_derives_from_it(conn):
    row = publish_row(conn, stored(TICKER_BLOCK, "Bed Bath & Beyond, Inc."))
    fixed = correct.corrected_signal(row)

    assert fixed.headline == ("BED BATH & BEYOND, INC. 8-K filing (Item 5.02): "
                              "officer or director change")
    assert fixed.content_hash != row["content_hash"], (
        "headline is an input to content_hash; a corrected row that kept the old "
        "fingerprint would be republished as a second copy on the next collection")
    assert fixed.content_hash == validate.content_hash(
        fixed.company_key, fixed.pillar, fixed.published_date,
        fixed.headline, fixed.source_name)

    # Identity is untouched, which is the whole claim of this correction.
    for field in ("company", "company_key", "cik", "ticker", "summary",
                  "talent_readthrough", "source_url", "published_date",
                  "confidence", "signal_direction", "country"):
        assert getattr(fixed, field) == row[field], field


def test_the_corrected_headline_still_carries_the_item_code(conn):
    """validate.forced_pillar fires only while the headline names the item.

    Cutting the ticker block must not cut the row out of the leadership pillar.
    """
    row = publish_row(conn, stored(TICKER_BLOCK, "Bed Bath & Beyond, Inc."))
    fixed = correct.corrected_signal(row)
    assert validate.forced_pillar(correct.COLLECTOR, fixed.headline) == "leadership_change"
    assert fixed.pillar == "leadership_change"


def test_the_worklist_is_only_the_mangled_rows(conn):
    publish_row(conn, stored(TICKER_BLOCK, "Bed Bath & Beyond, Inc."))
    publish_row(conn, stored(EATEN, "Jerash Holdings (US), Inc."))
    publish_row(conn, stored(
        "ACME CORP 8-K filing (Item 5.02): officer or director change", "Acme Corp",
        raw={"source_url": "https://www.sec.gov/Archives/edgar/data/9/9/a8k.htm"},
        summary="Acme Corp appointed a new Chief Financial Officer."))

    rows = correct.current_rows(conn)
    to_move, unprovable = correct.targets(rows)
    assert len(rows) == 3
    assert len(to_move) == 2
    assert unprovable == []


def test_an_unprovable_row_is_listed_and_not_moved(conn):
    publish_row(conn, stored(EATEN, "Jerash Holdings Limited"))
    to_move, unprovable = correct.targets(correct.current_rows(conn))
    assert to_move == []
    assert len(unprovable) == 1
    assert "not proven" in unprovable[0][1]


def test_a_runaway_worklist_refuses_rather_than_re_issuing_the_source(conn):
    rows = [{"headline": TICKER_BLOCK, "company": "X", "row_id": i}
            for i in range(correct.MIN_ROWS + 1)]
    with pytest.raises(correct.Unsafe):
        correct.targets(rows)
    moved, _ = correct.targets(rows, force=True)
    assert len(moved) == len(rows)


# --- resumability ----------------------------------------------------------

def test_a_row_already_re_issued_is_not_a_target_again(conn):
    signal = stored(TICKER_BLOCK, "Bed Bath & Beyond, Inc.")
    row = publish_row(conn, signal)
    calls = []
    correct.reissue(conn, row, withdraw=lambda sid, reason: calls.append(sid))
    assert calls == [signal.signal_id]

    to_move, unprovable = correct.targets(correct.current_rows(conn))
    assert to_move == [] and unprovable == []

    live = dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id = ? AND is_current = 1",
        (signal.signal_id,)).fetchone())
    assert live["revision"] == 2
    assert live["published_at"] is None
    assert live["headline"].startswith("BED BATH & BEYOND, INC. 8-K")

    old = dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id = ? AND is_current = 0",
        (signal.signal_id,)).fetchone())
    assert old["headline"] == TICKER_BLOCK, "the original must survive"


def test_a_correction_converging_onto_a_live_row_withdraws_it_instead(conn):
    """The corrected headline can land on a fingerprint another live row holds.

    The site's unique key is (content_hash, revision) and every insert it makes
    is revision 1, so the second row would not error, it would silently never
    land. It has to be withdrawn with a reason instead.

    The clean row is inserted straight into the table on purpose. store.store()
    would refuse it as a near-identical headline TODAY, which is precisely why
    the pair cannot arise through the front door and has to be constructed: the
    rows this guard exists for were stored before their headlines converged.
    """
    mangled = stored(TICKER_BLOCK, "Bed Bath & Beyond, Inc.")
    row = publish_row(conn, mangled)

    clean = correct.corrected_signal(row)
    columns = [c for c in row if c not in
               ("row_id", "signal_id", "revision", "is_current", "supersedes_row_id")]
    values = {c: getattr(clean, c, row[c]) for c in columns}
    values["signal_id"] = "a" * 32
    values["revision"] = 1
    values["is_current"] = 1
    conn.execute(
        f"INSERT INTO signals ({', '.join(values)}) "
        f"VALUES ({', '.join('?' for _ in values)})", tuple(values.values()))
    conn.commit()

    to_move, _ = correct.targets(correct.current_rows(conn))
    assert [r["signal_id"] for r in to_move] == [mangled.signal_id]
    reissue, dupes = correct.split_duplicates(conn, to_move)
    assert reissue == []
    assert [r["signal_id"] for r in dupes] == [mangled.signal_id]
