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
                 "ISPOOLEDINVESTMENTFUNDTYPE\tISTENANTINCOMMONTYPE\tISOTHERTYPE\t"
                 "DESCRIPTIONOFOTHERTYPE\tTOTALAMOUNTSOLD\t"
                 # The columns that decide whether the amount is money raised
                 # at all. Read past for as long as this collector existed.
                 "ISBUSINESSCOMBINATIONTRANS\tBUSCOMBCLARIFICATIONOFRESP\t"
                 "ISAMENDMENT\tTOTALOFFERINGAMOUNT\tMORETHANONEYEAR")


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
         fund_type="", live="LIVE", cik="1234567", tic="false", other_desc="",
         submission_type="D", biz_comb="false", biz_comb_text="",
         amendment="false", offering_amount="", more_than_one_year="false"):
    return (
        f"{acc}\t021-1\t31-MAR-2026\t\tX0708\t{submission_type}\t{live}",
        f"{acc}\tYES\t101\t{cik}\t{name}\t1 Main St\tSan Francisco\tCA\tCALIFORNIA",
        f"{acc}\t{industry}\t{fund_type}\t{fund_flag}\t{tic}\t"
        f"{'true' if other_desc else 'false'}\t{other_desc}\t{amount}\t"
        f"{biz_comb}\t{biz_comb_text}\t{amendment}\t{offering_amount}\t{more_than_one_year}",
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


# --- Not every Form D is a capital raise -----------------------------------
#
# The largest single distortion in the money views, and the one no name filter
# could reach: on 2025Q4 these were 3.5% of surviving rows and 41.6% of the
# dollars. The discriminator is on the FILING, not the issuer.

NOT_RAISES = [
    "Private Placement Variable Life Insurance Policies (PPVUL)",
    "Flexible Premium Variable Universal Life Insurance",
    "MetLife Separate Account Life Insurance Funding Account",
    "Group PPVL with Fixed Account",
    "Variable Insurance Policies",
    "IRC SECTION 529 PROGRAM ACCUMULATION SEGREGATED PORTFOLIO FUNDING AGREEMENT",
    "Health Savings Account Program Book Value Separate Account Funding Agreement",
    "Funding agreement issued by Empower Annuity Insurance Company",
    "Interests in a Share Incentive Plan and a Stock incentive Plan",
    "Participant interests in Issuers Deferred Compensation Program",
    "Non-Equity Golf Memberships",
    # The second tail, found by looking at the money list AFTER the first pass:
    # the same products written in the trade's abbreviations. Spelling out
    # "guaranteed investment contract" caught none of these, and they were then
    # the largest rows on the tracker.
    "Synthetic GICs issued to insurance carriers of BOLI/COLI policies.",
    "Synthetic GICs issued to IRC Section 529 plans",
    "Synthetic GICs issued to IRC Section 403(b)(9) church plans.",
    "AGL Institutional Life",
    "Interests in Universal Life Policy (BOLI 3)",
    "Allocated Units of Precious Metals",
]


@pytest.mark.parametrize("description", NOT_RAISES)
def test_an_offering_that_is_not_a_capital_raise_is_dropped(description):
    """Premium collected from policyholders is not money the company raised,
    and an employee share plan is not money it raised either."""
    assert bulk.parse_archive(_archive([
        _row("0003-26-000001", "METROPOLITAN LIFE INSURANCE CO", "4926890826",
             industry="Insurance", other_desc=description)])) == []


def test_the_discriminator_is_the_filing_not_the_issuer():
    """This is the whole point. Metropolitan Life is a REAL employer that files
    both annuity products and genuine corporate raises under one name and one
    CIK, so an entity-level filter cannot separate them and would have to drop
    the company outright. Same issuer, two filings, two outcomes."""
    product = _row("0003-26-000002", "METROPOLITAN LIFE INSURANCE CO", "3362355473",
                   industry="Insurance", other_desc="MetLife Separate Account Life Insurance")
    raise_ = _row("0003-26-000003", "METROPOLITAN LIFE INSURANCE CO", "3362355473",
                  industry="Insurance")
    kept = bulk.parse_archive(_archive([product, raise_]))
    assert [i["accession"] for i in kept] == ["0003-26-000003"]


def test_ordinary_llc_equity_is_still_a_raise():
    """"Membership Interests" is how an LLC describes its own equity. A rule
    that matched bare "membership" would delete real raises, which is why it
    matches "golf memberships" instead."""
    for description in ("Membership Interests", "LLC Membership units",
                        "Limited Liability Company Membership Interests",
                        "Series A-1 Common Units", "Preferred units and Common units"):
        items = bulk.parse_archive(_archive([
            _row("0003-26-000004", "Vurvey Labs, Inc.", "5000000", other_desc=description)]))
        assert len(items) == 1, description


def test_insurance_product_wrappers_are_dropped_by_name_too():
    """A separate account is a ring-fenced pool backing policies, with no staff.
    The filing-level check is the strong one; this is the fallback for a filing
    that leaves the description blank."""
    for name in ("DELAWARE LIFE VARIABLE ACCOUNT H", "NATIONWIDE PPVUL SEPARATE ACCOUNT 6",
                 "Keyport Life Ins Co Separate Account P", "DL Private Variable Account A"):
        assert bulk.parse_archive(
            _archive([_row("0003-26-000005", name, "7355355524", industry="Other")])) == [], name


def test_non_traded_credit_and_infrastructure_vehicles_are_dropped():
    for name in ("Apollo Asset Backed Credit Co LLC", "Apollo Infrastructure Co LLC"):
        assert bulk.parse_archive(
            _archive([_row("0003-26-000006", name, "1589086605", industry="Other")])) == [], name


def test_that_rule_does_not_eat_an_operating_company_ending_in_co_llc():
    """Which is why the strategy words are listed instead of matching "Co LLC"."""
    for name in ("Quarry Glue Holding Co LLC", "Municipal Apparel Co LLC",
                 "Fervo Energy Co", "NATIONAL FUEL GAS CO"):
        items = bulk.parse_archive(_archive([_row("0003-26-000007", name, "9000000")]))
        assert len(items) == 1, name


# --- Not every real number off a real filing is money raised ----------------
#
# Every filter above asks WHO filed. These three ask WHAT THE FIGURE IS, and an
# ordinary operating company clears every issuer check and still reports a
# number that is not a dated capital raise. Measured on 2026-07-29 they were
# 744 published rows and $23.55bn — 176 business combinations, 539 amendments,
# 29 continuous offerings, in that order of precedence.

BUSINESS_COMBINATIONS = [
    # Live rows. Every one of these is a real employer, correctly identified,
    # publishing the value of stock handed to somebody else's shareholders.
    ("Snowflake Inc.", 376_100_000,
     "In connection with the closing of the acquisition of Observe, Inc., "
     "Snowflake Inc. issued a total of 1,539,804 shares as partial consideration."),
    ("Marvell Technology, Inc.", 200_000_000,
     "Issuance of shares of common stock in connection with the acquisition of "
     "XConn Technologies Holdings, Ltd."),
    ("Roblox Corp", 44_600_000,
     "Issuance of shares of Class A Common Stock in connection with the "
     "acquisition of Morpheus AI, Inc"),
    ("AeroVironment Inc", 157_400_000,
     "Issuances of common stock as a portion of merger consideration payable to "
     "the stockholders of Empirical Systems Aerospace, Inc."),
    ("RADIAN GROUP INC", 21_300_000,
     "Radian Group Inc. acquired Inigo Limited on February 2, 2026"),
    ("Tencent Music Entertainment Group", 20_200_000,
     "On May 18, 2026, the Issuer completed its acquisition of Ximalaya Inc."),
    # DILLARD'S, INC. — $2.39bn, withdrawn by hand on 2026-07-29. It was the
    # largest row on the tracker and it was a merger with W.D. Company, Inc.
    ("DILLARD'S, INC.", 2_386_710_625,
     "Merger of W.D. Company, Inc., an Arkansas corporation, with and into "
     "Dillard's, Inc., with Dillard's, Inc. surviving the merger."),
]


@pytest.mark.parametrize("name,amount,clarification", BUSINESS_COMBINATIONS)
def test_merger_consideration_is_not_money_raised(name, amount, clarification):
    """The issuer ticked the box itself. No cash reached the company, so the
    figure is the value of the shares it issued, not a raise."""
    assert bulk.parse_archive(_archive([
        _row("0005-26-000001", name, str(amount), biz_comb="true",
             biz_comb_text=clarification)])) == []


def test_a_business_combination_is_dropped_with_the_box_empty():
    """115 of the 176 published business-combination rows leave the
    clarification blank, so a rule that read the text would decide a third of
    the class and let the rest through on silence."""
    assert bulk.parse_archive(_archive([
        _row("0005-26-000002", "Baldwin Insurance Group, Inc.", "552000000",
             biz_comb="true")])) == []


def test_a_cash_raise_that_funds_an_acquisition_is_dropped_too_on_purpose():
    """The known, paid cost of not text-gating rule 1: roughly fifteen rows and
    $0.6bn where the offering really was cash and the acquisition was what the
    cash bought. Asserted so the loss stays deliberate — if someone later adds
    a carve-out for these, this test is where they have to argue for it, and
    the mixed rows below are why it is hard."""
    assert bulk.parse_archive(_archive([
        _row("0005-26-000003", "INFINITY NATURAL RESOURCES, INC.", "350000000",
             biz_comb="true",
             biz_comb_text="This offering was made to partially fund the acquisition "
                           "whereby the Issuer purchased certain rights, title and "
                           "interests in oil and gas properties.")])) == []


MIXED = [
    # One figure covering both halves, and no column that splits it. Publishing
    # these keeps a raise that is overstated by an amount nothing states.
    ("Onebrief, Inc.", "A portion of the proceeds of the sale of securities to "
     "investors was used to acquire a target company shortly after the offering "
     "closed, and a portion of the securities was issued to the target company's "
     "stockholders in connection with the acquisition."),
    ("HawkEye 360, Inc.", "$25M of the shares covered by the Form D were issued to "
     "eligible holders as partial consideration for an acquisition"),
    ("ChartSpan Medical Technologies, Inc.", "Total includes shares issued pursuant "
     "to a merger as well as shares sold to investors."),
]


@pytest.mark.parametrize("name,clarification", MIXED)
def test_a_part_cash_part_consideration_figure_is_dropped(name, clarification):
    assert bulk.parse_archive(_archive([
        _row("0005-26-000004", name, "359300000", biz_comb="true",
             biz_comb_text=clarification)])) == []


def test_an_ordinary_raise_is_not_touched_by_the_business_combination_rule():
    """The box is answered "false" on nearly every filing. It has to stay a
    filter on the answer and not become a filter on the word "acquisition"."""
    items = bulk.parse_archive(_archive([
        _row("0005-26-000005", "Acquisition Robotics, Inc.", "9000000",
             biz_comb="false", other_desc="Shares issued in a merger of equals")]))
    assert len(items) == 1


def test_an_amendment_is_not_new_money_on_its_filing_date():
    """A D/A restates the CUMULATIVE amount sold since the offering's first
    sale. Fluidstack is the shape it took live: the original D reported $450M,
    and the D/A four months later reported $842M against the SAME first sale —
    the $450M inside it, published a second time under its own headline."""
    original = _row("0006-26-000001", "Fluidstack Ltd", "450000000")
    amendment = _row("0006-26-000002", "Fluidstack Ltd", "842478689",
                     submission_type="D/A", amendment="true")
    kept = bulk.parse_archive(_archive([original, amendment]))
    assert [i["accession"] for i in kept] == ["0006-26-000001"]


def test_a_later_separate_offering_by_the_same_issuer_survives():
    """The rule is about amendments, not about issuers. Fluidstack's June
    filing was a NEW $1.5bn offering with its own first sale, and it is as real
    a raise as January's."""
    kept = bulk.parse_archive(_archive([
        _row("0006-26-000003", "Fluidstack Ltd", "450000000"),
        _row("0006-26-000004", "Fluidstack Ltd", "729999546"),
    ]))
    assert len(kept) == 2


@pytest.mark.parametrize("submission_type,amendment", [
    ("D/A", "true"),        # how every one of the 553 published rows read
    ("D/A", "false"),       # the header says amendment, the flag disagrees
    ("D", "true"),          # and the other way round
])
def test_either_amendment_signal_alone_is_enough(submission_type, amendment):
    """They agreed on all 2,998 published rows. A rule this consequential
    should not rest on one column of a data set SEC has already moved once."""
    assert bulk.parse_archive(_archive([
        _row("0006-26-000005", "OPTCAPITAL LLC", "1769219217",
             submission_type=submission_type, amendment=amendment)])) == []


def test_a_continuous_offering_reports_a_running_total_not_a_raise():
    """No stated size AND intended to run more than a year: the amount sold has
    been accumulating over a window with no beginning in view, and it is
    re-reported larger at every annual amendment. OPTCAPITAL's $1.77bn had been
    accruing since 2012-07-22; it was withdrawn by hand on 2026-07-29."""
    assert bulk.parse_archive(_archive([
        _row("0007-26-000001", "OPTCAPITAL LLC", "1769219217",
             offering_amount="Indefinite", more_than_one_year="true")])) == []


def test_indefinite_alone_is_still_a_raise():
    """Both halves are required. On its own "Indefinite" usually means only
    that the filer declined to state a ceiling — 88 published rows and $1.21bn
    are Indefinite on a one-year offering with a recent first sale, and they
    are ordinary raises. Harvey AI's $200M first sold twelve days before the
    filing; a rule on the word alone would have taken it."""
    items = bulk.parse_archive(_archive([
        _row("0007-26-000002", "Harvey AI Corp", "200000000",
             offering_amount="Indefinite", more_than_one_year="false")]))
    assert len(items) == 1
    assert items[0]["amount_usd"] == 200_000_000


def test_a_long_offering_with_a_stated_size_is_still_a_raise():
    """The other half, on its own. A company can state a $50M offering and
    expect it to take eighteen months; the total sold is still bounded by a
    number the filing gives."""
    items = bulk.parse_archive(_archive([
        _row("0007-26-000003", "Tolerance Bio Inc.", "15000000",
             offering_amount="50000000", more_than_one_year="true")]))
    assert len(items) == 1


def test_the_two_form_d_paths_share_one_definition_of_money_raised():
    """Same reason as the vehicle and address definitions: the search path and
    the bulk path reach the same filings, and one route must not publish what
    the other drops. The bulk path reads TSV columns and the search path reads
    XML tags, so only the READING is local — the rule has one home."""
    assert bulk.sec_form_d.money_raised_exclusion is sec_form_d.money_raised_exclusion
    assert sec_form_d.money_raised_exclusion(
        business_combination=False, amendment=False,
        offering_amount="5000000", more_than_one_year=False) is None
    for kwargs in ({"business_combination": True}, {"amendment": True},
                   {"offering_amount": "Indefinite", "more_than_one_year": True}):
        args = {"business_combination": False, "amendment": False,
                "offering_amount": "5000000", "more_than_one_year": False, **kwargs}
        assert sec_form_d.money_raised_exclusion(**args)


def test_the_correction_path_can_say_which_rule_withdrew_a_row():
    """A withdrawal is published. "The current rules no longer produce this
    URL" is true and tells a reader nothing, so the archive names the box."""
    blob = _archive([
        _row("0008-26-000001", "Snowflake Inc.", "376100000", biz_comb="true"),
        _row("0008-26-000002", "Fluidstack Ltd", "842478689",
             submission_type="D/A", amendment="true"),
        _row("0008-26-000003", "OPTCAPITAL LLC", "1769219217",
             offering_amount="Indefinite", more_than_one_year="true"),
        _row("0008-26-000004", "Baseten Labs, Inc.", "75000000"),
    ])
    reasons = bulk.money_raised_exclusions(blob)
    kept = bulk.parse_archive(blob)[0]

    assert kept["source_url"] not in reasons          # a raise has no reason
    assert len(reasons) == 3
    assert sorted(reasons.values()) == sorted([
        sec_form_d.BUSINESS_COMBINATION, sec_form_d.AMENDMENT,
        sec_form_d.CONTINUOUS_OFFERING])
    # Keyed on the filing URL, which is what a published row stores.
    assert all(u.endswith("/primary_doc.xml") for u in reasons)


def test_a_tenant_in_common_offering_is_a_property_syndication():
    """The dataset says so itself, so this does not rely on the name."""
    assert bulk.parse_archive(_archive([
        _row("0003-26-000008", "Cedar Point Holdings, Inc.", "9000000", tic="true")])) == []


def test_edgar_name_bookkeeping_does_not_reach_the_company_column():
    """"Maverick Bancshares, Inc.\\TX" and "BAE SYSTEMS PLC /FI/" were rendering
    with EDGAR's own suffix attached."""
    items = bulk.parse_archive(_archive([
        _row("0003-26-000009", "Maverick Bancshares, Inc.\\TX", "9000000"),
        _row("0003-26-000010", "DataBahn, Inc. \\DE", "9000000"),
        _row("0003-26-000011", "BAE SYSTEMS PLC /FI/", "9000000"),
    ]))
    assert sorted(i["headline"].split(" raised ")[0] for i in items) == [
        "BAE SYSTEMS PLC", "DataBahn, Inc.", "Maverick Bancshares, Inc."]


def _foreign_row(acc, name, amount, code, description, city="Vancouver"):
    """A row whose issuer address is outside the US. STATEORCOUNTRY carries
    EDGAR's own two-character code (A1 = British Columbia), which is not a US
    state code, and STATEORCOUNTRYDESCRIPTION carries the readable place."""
    return (
        f"{acc}\t021-1\t31-MAR-2026\t\tX0708\tD\tLIVE",
        f"{acc}\tYES\t101\t1234567\t{name}\t1 Main St\t{city}\t{code}\t{description}",
        f"{acc}\tOther Technology\t\tfalse\tfalse\tfalse\t\t{amount}\tfalse\t\tfalse\t\tfalse",
    )


@pytest.mark.parametrize("description, expected", [
    ("BRITISH COLUMBIA, CANADA", "CANADA"),
    ("ONTARIO, CANADA", "CANADA"),
    ("NEW SOUTH WALES, AUSTRALIA", "AUSTRALIA"),
    ("ENGLAND, UNITED KINGDOM", "UNITED KINGDOM"),
    ("ISRAEL", "ISRAEL"),               # already one segment; must not change
    ("", ""),
])
def test_the_country_is_read_out_of_a_province_qualified_place(description, expected):
    assert bulk._country_name(description) == expected


def test_a_canadian_issuer_gets_canada_and_not_british_columbia_canada():
    """The filing states the country and we were throwing it away.

    STATEORCOUNTRYDESCRIPTION is written narrowest-first, so a foreign issuer's
    reads "BRITISH COLUMBIA, CANADA". Passing the whole string on as the
    country meant vocab.normalize_country saw a province and stored NULL, and
    100 Canadian issuers landed with no country in EITHER column — invisible to
    every geographic filter, with the answer printed in the filing.
    """
    items = bulk.parse_archive(_archive([
        _foreign_row("0004-26-000001", "Group Eleven Resources Corp.", "1500000",
                     "A1", "BRITISH COLUMBIA, CANADA"),
    ]))
    assert len(items) == 1
    assert items[0]["country"] == "Canada"   # title-cased, like every place here
    assert items[0]["state"] == ""      # a province is not the US state column
    # And the filing's own wording survives in the text the classifier reads.
    assert "British Columbia, Canada" in items[0]["raw_text"]

    signal = validate.build_signal(bulk.as_classified(items[0]), items[0], bulk.COLLECTOR)
    assert signal.country == "CA"


def test_a_us_issuer_is_unchanged_and_keeps_its_state():
    items = bulk.parse_archive(_archive([
        _row("0004-26-000002", "Acme Robotics Inc.", "4500000")]))
    assert items[0]["country"] == "United States"
    assert items[0]["state"] == "CA"
    signal = validate.build_signal(bulk.as_classified(items[0]), items[0], bulk.COLLECTOR)
    assert signal.country == "US"


def test_an_unrecognised_place_still_stores_nothing_rather_than_a_guess():
    """The fix reads a field, it does not invent one. A tail the vocabulary
    does not know must still come out NULL — a wrong country is worse than a
    missing one."""
    items = bulk.parse_archive(_archive([
        _foreign_row("0004-26-000003", "Nowhere Mining Ltd", "2000000",
                     "Z9", "SOMEWHERE, NOT A COUNTRY"),
    ]))
    signal = validate.build_signal(bulk.as_classified(items[0]), items[0], bulk.COLLECTOR)
    assert signal.country is None


def test_the_two_form_d_paths_share_one_definition_of_a_vehicle():
    """The search path and the bulk path reach the same filings. If they drift
    on what counts as an employer, one route publishes what the other drops."""
    assert bulk.EXCLUDED_INDUSTRIES is sec_form_d.EXCLUDED_INDUSTRIES
    assert bulk.EXCLUDED_NAME_PATTERNS is sec_form_d.EXCLUDED_NAME_PATTERNS
    assert "other real estate" in bulk.EXCLUDED_INDUSTRIES
    assert "pooled investment fund" in bulk.EXCLUDED_INDUSTRIES


def test_the_two_form_d_paths_share_one_definition_of_where_an_issuer_is():
    """Same reason, for the address rather than the vehicle. A filing that is
    Canadian down one route must not be American down the other."""
    assert bulk.US_STATE_CODES is sec_form_d.US_STATE_CODES
    assert bulk._country_name is sec_form_d._country_name
    assert "CA" in bulk.US_STATE_CODES        # a state code
    assert "A1" not in bulk.US_STATE_CODES    # an EDGAR country code


# ---------------------------------------------------------------------------
# The search path (sec_form_d.collect) reaches the same filings through EFTS
# and reads the XML itself. These drive it end to end with the network stubbed.

def _primary_doc(name, *, code, description, amount="1500000",
                 city="Vancouver", industry="Other Technology",
                 submission_type="D", biz_comb="false", amendment="false",
                 offering_amount="1500000", more_than_one_year="false") -> str:
    """A Form D primary_doc.xml, cut down to the tags the collector reads.

    The address block is the real shape: `stateOrCountry` is the two-character
    code and `stateOrCountryDescription` is the readable place beside it. The
    offering block likewise: `isBusinessCombinationTransaction` is nested under
    `businessCombinationTransaction`, `isAmendment` under `typeOfFiling`.
    """
    return (
        f"<?xml version='1.0'?><edgarSubmission><submissionType>{submission_type}"
        "</submissionType><primaryIssuer>"
        f"<entityName>{name}</entityName>"
        "<issuerAddress>"
        "<street1>1 Main St</street1>"
        f"<city>{city}</city>"
        f"<stateOrCountry>{code}</stateOrCountry>"
        f"<stateOrCountryDescription>{description}</stateOrCountryDescription>"
        "</issuerAddress>"
        "</primaryIssuer><offeringData>"
        f"<industryGroupType>{industry}</industryGroupType>"
        f"<typeOfFiling><newOrAmendment><isAmendment>{amendment}</isAmendment>"
        "</newOrAmendment></typeOfFiling>"
        "<businessCombinationTransaction>"
        f"<isBusinessCombinationTransaction>{biz_comb}"
        "</isBusinessCombinationTransaction>"
        "<clarificationOfResponse></clarificationOfResponse>"
        "</businessCombinationTransaction>"
        f"<durationOfOffering><moreThanOneYear>{more_than_one_year}</moreThanOneYear>"
        "</durationOfOffering>"
        f"<offeringSalesAmounts><totalOfferingAmount>{offering_amount}</totalOfferingAmount>"
        f"<totalAmountSold>{amount}</totalAmountSold></offeringSalesAmounts>"
        "</offeringData></edgarSubmission>"
    )


def _classified(item: dict) -> dict:
    """What the classifier returns for a Form D on this path — with the country
    left EMPTY, which is the case that matters here. `validate.build_signal`
    falls back to the raw record's country when the model does not state one,
    so the hardcoded "United States" was the value that reached the database.
    """
    company = item["headline"].split(" raised ")[0]
    return {
        "company": company,
        "pillar": "company_development",
        "signal_direction": "neutral",
        "headline": item["headline"],
        "summary": f"{company} reported a private placement in a Form D filing.",
        "talent_readthrough": "The filing records money only; it names no roles.",
        "country": "",
        "confidence": "verified",
    }


def _search_path(monkeypatch, xml: str) -> list[dict]:
    """One EFTS hit whose filing is `xml`. No network, no sleeping."""
    hit = {
        "_id": "0004-26-000009:primary_doc.xml",
        "_source": {"display_names": ["Group Eleven Resources Corp.  (CIK 0001234567)"],
                    "file_date": "2026-03-31"},
    }

    class Resp:
        text = xml

    monkeypatch.setattr(sec_form_d.time, "sleep", lambda *a: None)
    monkeypatch.setattr(sec_form_d, "search",
                        lambda **kw: hit and ([hit] if kw.get("page", 0) == 0 else []))
    monkeypatch.setattr(sec_form_d.requests, "get", lambda *a, **k: Resp())
    return sec_form_d.collect()


def test_a_canadian_issuer_through_the_search_path_gets_canada(monkeypatch):
    """The same bug the bulk path was fixed for, on the route that fetches the
    XML itself. It read `stateOrCountry` ("A1") into the US state column and
    hardcoded country "United States" on every record, so a Vancouver issuer
    was published as an American company in state "A1" — and a wrong country is
    worse than a missing one. The description was in the XML the whole time."""
    items = _search_path(monkeypatch, _primary_doc(
        "Group Eleven Resources Corp.", code="A1",
        description="BRITISH COLUMBIA, CANADA"))

    assert len(items) == 1
    assert items[0]["country"] == "Canada"
    assert items[0]["state"] == ""      # a province is not the US state column
    assert "British Columbia, Canada" in items[0]["raw_text"]
    assert "A1" not in items[0]["raw_text"]

    signal = validate.build_signal(_classified(items[0]), items[0], sec_form_d.COLLECTOR)
    assert signal.country == "CA"


def test_a_one_segment_foreign_place_through_the_search_path(monkeypatch):
    """"ISRAEL" has no province in front of it. It must survive whole."""
    items = _search_path(monkeypatch, _primary_doc(
        "Tel Aviv Robotics Ltd", code="L3", description="ISRAEL", city="Tel Aviv"))
    assert items[0]["country"] == "Israel"
    assert items[0]["state"] == ""
    signal = validate.build_signal(_classified(items[0]), items[0], sec_form_d.COLLECTOR)
    assert signal.country == "IL"


def test_a_us_issuer_through_the_search_path_keeps_its_state(monkeypatch):
    items = _search_path(monkeypatch, _primary_doc(
        "Acme Robotics Inc.", code="CA", description="CALIFORNIA",
        city="San Francisco", amount="4500000"))
    assert items[0]["country"] == "United States"
    assert items[0]["state"] == "CA"
    signal = validate.build_signal(_classified(items[0]), items[0], sec_form_d.COLLECTOR)
    assert signal.country == "US"


def test_an_unrecognised_place_through_the_search_path_stores_nothing(monkeypatch):
    """Reading a field, not inventing one: a tail the vocabulary does not know
    still comes out NULL rather than a guess."""
    items = _search_path(monkeypatch, _primary_doc(
        "Nowhere Mining Ltd", code="Z9", description="SOMEWHERE, NOT A COUNTRY",
        amount="2000000"))
    signal = validate.build_signal(_classified(items[0]), items[0], sec_form_d.COLLECTOR)
    assert signal.country is None


@pytest.mark.parametrize("kwargs", [
    {"biz_comb": "true"},                                       # merger consideration
    {"submission_type": "D/A", "amendment": "true"},            # cumulative total
    {"amendment": "true"},                                      # flag alone
    {"submission_type": "D/A"},                                 # header alone
    {"offering_amount": "Indefinite", "more_than_one_year": "true"},
])
def test_the_search_path_drops_what_is_not_money_raised(monkeypatch, kwargs):
    """The same three questions, on the route that fetches the XML itself. The
    answers were in the document the whole time and this path read past them —
    which is how the two routes would have drifted the moment the bulk path was
    fixed alone: one publishing what the other withdrew, from one filing."""
    assert _search_path(monkeypatch, _primary_doc(
        "Snowflake Inc.", code="CA", description="CALIFORNIA",
        city="San Francisco", amount="376100000", **kwargs)) == []


def test_the_search_path_still_collects_an_ordinary_raise(monkeypatch):
    """The guard against the above being right for the wrong reason."""
    items = _search_path(monkeypatch, _primary_doc(
        "Baseten Labs, Inc.", code="CA", description="CALIFORNIA",
        city="San Francisco", amount="75000000", offering_amount="100000000"))
    assert len(items) == 1
    assert items[0]["funding_amount"] == "$75M"


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
