"""The filer name a reader sees comes off EDGAR's `display_names`, and for
months it came off wrong in two opposite directions.

EFTS renders one rigid shape:

    {conformed name}  ({ticker}[, {ticker}...])  (CIK {digits})

joined by exactly TWO spaces — 771 of 771 separators in the committed sample.
Every one of the 502 names ends in a `(CIK ...)` group. The old rule read none
of that structure. It searched for a single parenthesised ticker-shaped token
anywhere in the string, which:

1. missed every multi-ticker block, because `(BBBY, BBBY-WT)` has a comma in
   it, leaving the block in the name and a doubled space behind it;
2. ate a legitimate parenthetical, because `Jerash Holdings (US), Inc.` has a
   ticker-shaped token in the middle of the company's actual name.

Both are in the fixtures below as the real strings EDGAR serves.

The fixture is real data, pulled from EDGAR full-text search and from
`company_tickers.json`, never invented: a rule tested only against names
somebody made up is a rule tested against its own assumptions. The expected
value for each hard case is the filer's own conformed name as EDGAR publishes
it, not this parser's opinion of it.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from collectors import sec_edgar

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "sec_edgar_display_names.json"
NAMES: list[str] = json.loads(FIXTURE.read_text())


def parse(display_name: str):
    return sec_edgar._company_and_cik({"_source": {"display_names": [display_name]}})


# Every shape EDGAR actually serves, with the filer's own conformed name as the
# expected result. Grouped by the property each one is here to defend.
CASES = [
    # --- the two live defects ------------------------------------------------
    ('BED BATH & BEYOND, INC.  (BBBY, BBBY-WT)  (CIK 0001130713)',
     'BED BATH & BEYOND, INC.', '1130713'),
    ('Jerash Holdings (US), Inc.  (JRSH)  (CIK 0001696558)',
     'Jerash Holdings (US), Inc.', '1696558'),

    # --- a list of tickers, of every length seen -----------------------------
    ('REGIONS FINANCIAL CORP  (RF, RF-PC, RF-PE, RF-PF)  (CIK 0001281761)',
     'REGIONS FINANCIAL CORP', '1281761'),
    ('Core Scientific, Inc./tx  (CORZ, CORZR, CORZW, CORZZ)  (CIK 0001839341)',
     'Core Scientific, Inc./tx', '1839341'),
    ('M&T BANK CORP  (MTB, MTB-PH, MTB-PJ, MTB-PK)  (CIK 0000036270)',
     'M&T BANK CORP', '36270'),

    # --- a ticker carrying a suffix ------------------------------------------
    ('BROWN FORMAN CORP  (BF-A, BF-B)  (CIK 0000014693)',
     'BROWN FORMAN CORP', '14693'),
    ('EQV Ventures Acquisition Corp. II  (EVAC, EVAC-UN, EVAC-WT)  (CIK 0002042902)',
     'EQV Ventures Acquisition Corp. II', '2042902'),

    # --- a legitimate parenthetical, anywhere in the name --------------------
    ('HUTCHMED (China) Ltd  (HCM, HMDCF)  (CIK 0001648257)',
     'HUTCHMED (China) Ltd', '1648257'),
    ('Super Group (SGHC) Ltd  (SGHC)  (CIK 0001878057)',
     'Super Group (SGHC) Ltd', '1878057'),
    ('ZTO Express (Cayman) Inc.  (ZTO, ZTOEF)  (CIK 0001677250)',
     'ZTO Express (Cayman) Inc.', '1677250'),
    ('ARYZTA AG (fka IAWS GROUP PLC)  (ARZTD)  (CIK 0001423210)',
     'ARYZTA AG (fka IAWS GROUP PLC)', '1423210'),
    ('Eco Wave Power Global AB (publ)  (WAVE)  (CIK 0001846715)',
     'Eco Wave Power Global AB (publ)', '1846715'),

    # A parenthetical that is INDISTINGUISHABLE from a ticker by its own
    # spelling. Only the two-space delimiter and the position before the CIK
    # group tell them apart, which is why the rule reads the shape of the whole
    # string rather than the shape of one token.
    ('ACUITY INC. (DE)  (AYI)  (CIK 0001144215)', 'ACUITY INC. (DE)', '1144215'),
    ('Western Asset Diversified Income Fund (WDI)  (WDI)  (CIK 0001819559)',
     'Western Asset Diversified Income Fund (WDI)', '1819559'),
    # ... and the same filer with no ticker block at all, where a rule that
    # simply dropped "the group before the CIK" would delete part of the name.
    ('Western Asset Diversified Income Fund (WDI)  (CIK 0001819559)',
     'Western Asset Diversified Income Fund (WDI)', '1819559'),
    ('Siddhi Acquisition Corp (Cayman Islands)  (CIK 0002034037)',
     'Siddhi Acquisition Corp (Cayman Islands)', '2034037'),

    # --- no ticker at all (funds, LPs, private filers) -----------------------
    ('Blackstone Private Credit Fund  (CIK 0001803498)',
     'Blackstone Private Credit Fund', '1803498'),
    ('Federal Home Loan Bank of Boston  (CIK 0001331463)',
     'Federal Home Loan Bank of Boston', '1331463'),
    ('CINEMARK USA INC /TX  (CIK 0000885975)', 'CINEMARK USA INC /TX', '885975'),

    # --- ampersands, trailing commas, embedded punctuation -------------------
    ('JOHNSON & JOHNSON  (JNJ)  (CIK 0000200406)', 'JOHNSON & JOHNSON', '200406'),
    ('BECTON DICKINSON & CO  (BDX)  (CIK 0000010795)', 'BECTON DICKINSON & CO', '10795'),
    ('ALPHA MODUS HOLDINGS, INC.  (AMOD, AMODW)  (CIK 0001862463)',
     'ALPHA MODUS HOLDINGS, INC.', '1862463'),
    # An ampersand AND a comma AND a parenthetical with no space before it.
    ('STRATS(SM) Trust for Procter & Gamble Securities, Series 2006-1  (GJR)  (CIK 0001353226)',
     'STRATS(SM) Trust for Procter & Gamble Securities, Series 2006-1', '1353226'),
    ("Bloomin' Brands, Inc.  (BLMN)  (CIK 0001546417)",
     "Bloomin' Brands, Inc.", '1546417'),

    # --- state and registry suffixes the conformed name legitimately carries -
    ('8X8 INC /DE/  (EGHT)  (CIK 0001023731)', '8X8 INC /DE/', '1023731'),
    ('US BANCORP \\DE\\  (USB, USB-PA, USB-PH, USB-PP, USB-PQ, USB-PR, USB-PS)  '
     '(CIK 0000036104)', 'US BANCORP \\DE\\', '36104'),
    ('Victory Giant Technology (HuiZhou) Co., Ltd./ADR  (VGTHY)  (CIK 0002131322)',
     'Victory Giant Technology (HuiZhou) Co., Ltd./ADR', '2131322'),
]


@pytest.mark.parametrize("display_name,expected_company,expected_cik", CASES)
def test_real_filer_names_parse_to_the_filers_own_name(
        display_name, expected_company, expected_cik):
    company, cik = parse(display_name)
    assert company == expected_company, (
        f"{display_name!r} parsed to {company!r}, not the filer's own name "
        f"{expected_company!r}")
    assert cik == expected_cik


def test_the_whole_committed_sample_leaves_no_ticker_block_behind():
    """Not one of the 502 real names may keep a ticker block or a CIK group."""
    leftovers = []
    for name in NAMES:
        company, _cik = parse(name)
        if re.search(r"\(CIK", company) or re.search(r"\s{2,}\(", company):
            leftovers.append((name, company))
    assert not leftovers, (
        f"{len(leftovers)} of {len(NAMES)} real filer names kept a ticker or CIK "
        f"block: {leftovers[:5]}")


def test_the_whole_committed_sample_keeps_a_single_space_run():
    """A doubled space in the output is the ticker block's footprint."""
    doubled = [(n, parse(n)[0]) for n in NAMES if "  " in parse(n)[0]]
    assert not doubled, (
        f"{len(doubled)} of {len(NAMES)} real filer names parsed with a doubled "
        f"space: {doubled[:5]}")


def test_nothing_outside_the_ticker_and_cik_groups_is_deleted():
    """The parser may only remove the two trailing groups EFTS appends.

    Everything before the ticker block is the filer's own conformed name, so
    the output must be a literal prefix of the input. This is the assertion the
    Jerash defect fails: 'Jerash Holdings , Inc.' is not a prefix of
    'Jerash Holdings (US), Inc.  (JRSH)  (CIK 0001696558)'.
    """
    mangled = []
    for name in NAMES:
        company, _cik = parse(name)
        if company and not name.startswith(company):
            mangled.append((name, company))
    assert not mangled, (
        f"{len(mangled)} of {len(NAMES)} real filer names lost characters from "
        f"inside the name: {mangled[:5]}")


def test_every_name_in_the_sample_yields_a_cik():
    """document_url() returns None without one, so the filing is dropped."""
    missing = [n for n in NAMES if not parse(n)[1]]
    assert not missing, f"{len(missing)} real names yielded no CIK: {missing[:5]}"


def test_the_headline_a_reader_sees_carries_the_clean_name():
    """The published headline is built from this value; see collect()."""
    company, _cik = parse('BED BATH & BEYOND, INC.  (BBBY, BBBY-WT)  (CIK 0001130713)')
    headline = f"{company} 8-K filing (Item 5.02): officer or director change"
    assert headline == (
        "BED BATH & BEYOND, INC. 8-K filing (Item 5.02): officer or director change")


def test_an_empty_or_odd_hit_still_returns_a_pair():
    assert sec_edgar._company_and_cik({}) == ("", None)
    assert sec_edgar._company_and_cik({"_source": {"display_names": []}}) == ("", None)
    assert sec_edgar._company_and_cik(
        {"_source": {"display_names": ["Some Filer With No Suffix"]}}
    ) == ("Some Filer With No Suffix", None)
