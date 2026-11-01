"""Rio Tinto is one employer, whatever accent the outlet put on it.

THE DEFECT (found 2026-11-01). Clarin's Spanish-language coverage writes the
miner "Río Tinto"; every other source writes "Rio Tinto". company_key keeps
diacritics (see the note above EMPLOYER_KEY_ALIASES for why that is not a
rule-shaped fix), so the corpus held two keys, 'rio tinto' and 'río tinto',
both claiming /company/rio-tinto/. ops_status [1c] listed the slug as claimed
by two keys and undecided, and every surface that groups by company_key (the
employer counts, the dedup layers via content_hash) treated one company as two.

The rows were read one by one: the 'río tinto' row is Clarin's report of Rio
Tinto's third Argentine lithium plant, the same Argentina lithium programme as
the 'rio tinto' rows from Clarin and Mining Weekly, under the same London HQ.
One employer, two spellings, so it is an alias. The survivor is the ASCII form,
following the map's own rule.

What is NOT merged, and why, so nobody "finishes the job":
  * 'rio tinto london' (RIO TINTO LONDON LIMITED, a Companies House entity) is
    a separate registered subsidiary with its own officers.
  * 'rio tinto simfer' is the Simandou joint venture in Guinea, a separate
    employer with its own Director General.
  * "Rio Tinto plc" and "Rio Tinto Ltd" already share the key 'rio tinto'
    through the legal-suffix strip; asserted below so it stays that way.
"""
from __future__ import annotations

import ops_status
from pipeline import vocab


def test_the_accented_spelling_shares_the_employer_key():
    assert vocab.company_key("Río Tinto") == vocab.company_key("Rio Tinto") == "rio tinto"


def test_both_listed_parents_are_one_key():
    for name in ("Rio Tinto plc", "Rio Tinto Ltd", "Rio Tinto Limited", "RIO TINTO PLC"):
        assert vocab.company_key(name) == "rio tinto", name


def test_the_survivor_owns_the_profile_slug():
    assert ops_status._profile_slug("rio tinto") == "rio-tinto"
    assert ops_status._profile_slug(vocab.company_key("Río Tinto")) == "rio-tinto"


def test_subsidiaries_stay_separate_employers():
    assert vocab.company_key("RIO TINTO LONDON LIMITED") == "rio tinto london"
    assert vocab.company_key("Rio Tinto SimFer") == "rio tinto simfer"
