"""The bulk Form D path must not publish funds, and must not go quiet.

Every assertion here is a failure this source could plausibly ship: a hedge
fund on a hiring page, a link to the dataset zip instead of the filing, an
Oracle date string reaching the database, or a quarter that parsed to nothing
and exited green.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from collectors import sec_form_d_bulk as bulk
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
