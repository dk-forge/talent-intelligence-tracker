"""The bulk Form D path must not publish funds, and must not go quiet.

Every assertion here is a failure this source could plausibly ship: a hedge
fund on a hiring page, a link to the dataset zip instead of the filing, an
Oracle date string reaching the database, or a quarter that parsed to nothing
and exited green.
"""

from __future__ import annotations

import io
import re
import zipfile

import pytest

from collectors import sec_form_d, sec_form_d_bulk as bulk
from pipeline import validate

SUBMISSION_COLS = "ACCESSIONNUMBER\tFILE_NUM\tFILING_DATE\tSIC_CODE\tSCHEMAVERSION\tSUBMISSIONTYPE\tTESTORLIVE"
ISSUER_COLS = ("ACCESSIONNUMBER\tIS_PRIMARYISSUER_FLAG\tISSUER_SEQ_KEY\tCIK\tENTITYNAME\t"
               "STREET1\tCITY\tSTATEORCOUNTRY\tSTATEORCOUNTRYDESCRIPTION")
OFFERING_COLS = ("ACCESSIONNUMBER\tINDUSTRYGROUPTYPE\tINVESTMENTFUNDTYPE\t"
                 "ISPOOLEDINVESTMENTFUNDTYPE\tTOTALAMOUNTSOLD")


def _archive(rows: list[tuple[str, str, str]]) -> bytes:
    """rows: (submission line, issuer line, offering line)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("2026Q1_d/FORMDSUBMISSION.tsv",
                   SUBMISSION_COLS + "\n" + "\n".join(r[0] for r in rows) + "\n")
        z.writestr("2026Q1_d/ISSUERS.tsv",
                   ISSUER_COLS + "\n" + "\n".join(r[1] for r in rows) + "\n")
        z.writestr("2026Q1_d/OFFERING.tsv",
                   OFFERING_COLS + "\n" + "\n".join(r[2] for r in rows) + "\n")
    return buf.getvalue()


def _row(acc, name, amount, *, industry="Other Technology", fund_flag="false",
         fund_type="", live="LIVE", cik="1234567"):
    return (
        f"{acc}\t021-1\t31-MAR-2026\t\tX0708\tD\t{live}",
        f"{acc}\tYES\t101\t{cik}\t{name}\t1 Main St\tSan Francisco\tCA\tCALIFORNIA",
        f"{acc}\t{industry}\t{fund_type}\t{fund_flag}\t{amount}",
    )


def test_an_operating_company_raise_becomes_a_record():
    items = bulk.parse_archive(_archive([_row("0001-26-000001", "Acme Robotics Inc.", "4500000")]))
    assert len(items) == 1
    item = items[0]
    assert item["funding_amount"] == "$4,500,000"      # exact, not "$4.5M"
    assert item["published_date"] == "2026-03-31"      # not "31-MAR-2026"
    # The receipt is the filing, never the dataset archive.
    assert item["source_url"].startswith("https://www.sec.gov/Archives/edgar/data/")
    assert item["source_url"].endswith("/primary_doc.xml")
    assert ".zip" not in item["source_url"]

    signal = validate.build_signal(bulk.as_classified(item), item, bulk.COLLECTOR)
    assert signal.funding_amount_usd == 4_500_000
    assert signal.confidence == "verified"             # a filing is primary
    assert signal.country == "US" and signal.state == "CA"


@pytest.mark.parametrize("kwargs", [
    {"industry": "Pooled Investment Fund"},
    {"fund_flag": "true"},
    {"fund_type": "Hedge Fund"},
])
def test_investment_vehicles_are_dropped(kwargs):
    """A fund raising a fund employs nobody, and it is two thirds of Form D
    volume. The dataset says so three ways; all three are checked."""
    assert bulk.parse_archive(
        _archive([_row("0001-26-000002", "Blue Harbor Capital", "50000000", **kwargs)])) == []


def test_small_raises_and_test_filings_are_dropped():
    assert bulk.parse_archive(
        _archive([_row("0001-26-000003", "Tiny Startup Inc.", "250000")])) == []
    assert bulk.parse_archive(
        _archive([_row("0001-26-000004", "Acme Inc.", "9000000", live="TEST")])) == []


def test_the_same_filing_gets_the_same_url_as_the_search_collector():
    """Both routes must produce ONE record, and dedup is by URL, so the two
    URLs have to be byte-identical."""
    from collectors import sec_edgar
    item = bulk.parse_archive(
        _archive([_row("0001999999-26-000007", "Acme Inc.", "3000000", cik="0000320193")]))[0]
    assert item["source_url"] == (
        f"{sec_edgar.ARCHIVES}/320193/000199999926000007/primary_doc.xml")


def test_a_form_d_is_not_a_hiring_signal():
    """The filing states money. It states nothing about headcount, so the badge
    beside it must not say "Hiring up" — that was a claim no filing makes, and
    it is the same regression the classifier prompt was fixed for this morning.
    """
    item = bulk.parse_archive(
        _archive([_row("0001-26-000010", "Baseten Labs, Inc.", "75000000")]))[0]
    classified = bulk.as_classified(item)
    assert classified["signal_direction"] == "neutral"
    # Nothing else is given up: the pillar and the figure still carry the value.
    assert classified["pillar"] == "company_development"
    signal = validate.build_signal(classified, item, bulk.COLLECTOR)
    assert signal.signal_direction == "neutral"
    assert signal.funding_amount_usd == 75_000_000


PREDICTIONS = re.compile(
    r"\b(precursor|within the following|two to six|will|expects?|suggests?|may|"
    r"might|could|likely|indicates?|usually|typically|standard)\b", re.I)


def test_the_read_through_states_the_filing_and_names_the_gap():
    """It used to assert that capital is spent on headcount "within the
    following two to six quarters" — a generalisation in no filing, printed
    identically on thousands of rows as though it had been sourced."""
    item = bulk.parse_archive(
        _archive([_row("0001-26-000011", "Fluidstack Inc.", "23000000")]))[0]
    readthrough = bulk.as_classified(item)["talent_readthrough"]

    assert not PREDICTIONS.search(readthrough), readthrough
    # Only things the filing says: who, how much, when, from where.
    assert "Fluidstack Inc." in readthrough
    assert "$23.0M" in readthrough or "$23M" in readthrough
    assert "2026-03-31" in readthrough
    assert "San Francisco, CA" in readthrough
    # And the gap, named rather than filled in.
    assert "names no roles and no hiring plan" in readthrough


def test_the_read_through_is_not_one_sentence_repeated():
    """Two rows must not differ only by the number: that is a template, not a
    read-through, and it is what made the invented claim so visible."""
    rows = [_row("0001-26-000012", "SonoThera, Inc.", "60500000"),
            _row("0001-26-000013", "PowerPollen, Inc.", "12000000")]
    a, b = (bulk.as_classified(i)["talent_readthrough"]
            for i in bulk.parse_archive(_archive(rows)))
    assert a != b


# The live rows these were drawn from: an LLC per building or per deal, raising
# money for one asset, employing nobody.
VEHICLES = [
    ("MIMG CCLXV Rapid City 6 Master, LLC", "Commercial"),
    ("Melrose II S TIC, LLC", "Residential"),
    ("Whitefish 57 Commercial, LLC", "Commercial"),
    ("ROGERS NWA OFFICE, LLC", "Other Real Estate"),
    ("E22 Gore Partners LLC", "Construction"),
    ("Driftwood Golf Club Development, Inc.", "Other"),
    ("Indy Innovation Apartments, LLC", "Other"),
    ("Northfield V74 I, LLC", "Other"),
    ("KKR Private Equity Conglomerate LLC", "Other"),
    ("Courseview SPV II, LLC", "Business Services"),
    ("Synapse InvestCo, LLC", "Other Technology"),
]


@pytest.mark.parametrize("name,industry", VEHICLES)
def test_single_purpose_vehicles_are_not_employers(name, industry):
    """These were live. They also distorted every money view, because one
    building's raise is the size of a real company's Series B."""
    assert bulk.parse_archive(
        _archive([_row("0002-26-000001", name, "16325000", industry=industry)])) == []


# Real operating companies, each drawn from the live rows. If a filter change
# drops any of these it is too broad, whatever it caught.
OPERATING = ["Baseten Labs, Inc.", "Fluidstack", "Kioxia Holdings Corp", "SonoThera, Inc.",
             "PowerPollen, Inc.", "Events.com, Inc.", "Vurvey Labs, Inc.",
             # Named because a "properties"/"development" pattern nearly ate them.
             "Real Estate Business Analytics, Inc.", "Strobe Development, Inc."]


@pytest.mark.parametrize("name", OPERATING)
def test_operating_companies_survive_the_vehicle_filter(name):
    items = bulk.parse_archive(
        _archive([_row("0002-26-000002", name, "16325000")]))
    assert [i["headline"].split(" raised ")[0] for i in items] == [name]


def test_the_two_form_d_paths_share_one_definition_of_a_vehicle():
    """The search path and the bulk path reach the same filings. If they drift
    on what counts as an employer, one route publishes what the other drops."""
    assert bulk.EXCLUDED_INDUSTRIES is sec_form_d.EXCLUDED_INDUSTRIES
    assert bulk.EXCLUDED_NAME_PATTERNS is sec_form_d.EXCLUDED_NAME_PATTERNS
    assert "other real estate" in bulk.EXCLUDED_INDUSTRIES
    assert "pooled investment fund" in bulk.EXCLUDED_INDUSTRIES


def test_a_user_agent_with_a_contact_address():
    """A browser-shaped UA gets 'Request Rate Threshold Exceeded' from SEC."""
    assert "@" in bulk.USER_AGENT
    assert "Mozilla" not in bulk.USER_AGENT


def test_urls_are_scraped_not_constructed(monkeypatch):
    """SEC moved the dataset directory mid-2026. A pattern-built URL would
    404 on exactly the quarter we most wanted."""
    page = ('<a href="/files/datastandardsinnovation/data/form-d-data-sets/2026q2_d.zip">a</a>'
            '<a href="/files/structureddata/data/form-d-data-sets/2026q1_d.zip">b</a>')

    class Resp:
        status_code = 200
        text = page

        def raise_for_status(self):
            pass

    monkeypatch.setattr(bulk.requests, "get", lambda *a, **k: Resp())
    urls = bulk.dataset_urls()
    assert urls["2026q2"] == ["https://www.sec.gov/files/datastandardsinnovation/"
                              "data/form-d-data-sets/2026q2_d.zip"]
    assert urls["2026q1"][0].startswith("https://www.sec.gov/files/structureddata/")


def test_an_index_page_with_no_archives_raises(monkeypatch):
    """Silence is the failure mode this project keeps having: a blocked or
    restructured page must be loud, not an empty quarter."""
    class Resp:
        status_code = 200
        text = "<html>nothing here</html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(bulk.requests, "get", lambda *a, **k: Resp())
    with pytest.raises(bulk.DatasetError):
        bulk.dataset_urls()
