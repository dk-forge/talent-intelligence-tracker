"""The free filter that runs before any paid classification.

Every rejection case here is a real headline from the first live run, which
fetched 25 candidates and contained zero talent signals.
"""

import pytest

from pipeline import prefilter

# Verbatim from the first live dry run.
REAL_NOISE = [
    "MLB expansion franchise to pour $72bn into local area after building new stadium",
    "WoW Factor: World of Warcraft's The War Within - great expansion or grind?",
    "New Medicaid Expansion Changes Hurt People with Disabilities",
    "U.S. Cattle Inventory -- Herd Expansion or Continued Herd Contraction?",
    "How NBA expansion to Seattle, Vegas would have a seismic impact",
    "Growing international concerns about the expansion of US nuclear weapons",
    "War expansion or imminent ceasefire? Conflicting reports as Israel escalates",
    "Injuries have slowed the Tempo, but the WNBA expansion team has shown promise",
    "The Daily Grind: Where is the line between an expansion and a DLC?",
]

# Verbatim from the SECOND live run, where the first version of this filter
# dropped every one of them. A capability centre opening is a hiring event even
# when the headline never says "jobs", and these are exactly what the
# standalone euphemism queries were written to surface.
SITE_OPENINGS = [
    "US telecom giant T-Mobile sets up global tech centre in Hyderabad",
    "Heineken launches global capability centre in Hyderabad",
    "US Biotech Regeneron to launch Global Capability Centre in Hyderabad",
    "TruBridge Launches Chennai Global Capability Center (GCC)",
    "Lonza To Establish Global Capability Centre In Hyderabad",
    "BMS opens Mumbai capability centre",
    "GI Outsourcing opens Global Capability Centre in Hyderabad",
]

REAL_SIGNALS = [
    "Stripe to create 300 new jobs at expanded Dublin engineering hub",
    "Workday appoints new chief people officer as it expands in London",
    "SAP raises minimum salary across German sites in retention push",
    "Intel opens new facility in Leixlip, hiring 500 staff",
    "Revolut CEO steps down after six years",
    "Shopify scraps its return to office policy for engineering employees",
]


@pytest.mark.parametrize("headline", REAL_NOISE)
def test_real_noise_is_filtered_before_the_llm(headline):
    keep, reason = prefilter.passes(headline)
    assert not keep, f"would have paid to classify: {headline}"
    assert reason


@pytest.mark.parametrize("headline", REAL_SIGNALS)
def test_real_signals_survive(headline):
    keep, reason = prefilter.passes(headline)
    assert keep, f"dropped a genuine signal ({reason}): {headline}"


@pytest.mark.parametrize("headline", SITE_OPENINGS)
def test_site_openings_survive_without_the_word_jobs(headline):
    keep, reason = prefilter.passes(headline)
    assert keep, f"dropped a site-opening signal ({reason}): {headline}"


def test_structural_leadership_news_survives():
    """Verbatim from a live run. Names no person and no headcount, but is
    squarely the leadership pillar."""
    keep, reason = prefilter.passes(
        "Woolworths reshapes leadership structure to accelerate growth"
    )
    assert keep, reason


@pytest.mark.parametrize("headline", [
    "Why leadership capability building is the most underrated AI skill",
    "A new era of shared leadership - South Jersey Media",
    "India vies for strategic leadership as GCC host",
])
def test_bare_leadership_think_pieces_stay_filtered(headline):
    """The term is deliberately narrow — 'leadership' alone is endless noise."""
    keep, _ = prefilter.passes(headline)
    assert not keep


def test_gulf_cooperation_council_is_not_a_capability_centre():
    """'GCC' is deliberately not a site term — it is also a trade bloc."""
    keep, _ = prefilter.passes("GCC leaders meet in Riyadh to discuss trade")
    assert not keep


# Verbatim from the first run that actually reached the model. Two of these
# named countries the vocabulary did not know at the time; it now covers all
# 198, so the useful assertion is not "the helper misses them" but "the filter
# lets them through either way".
GEOGRAPHY_EDGE_CASES = [
    "Uzbekistan bets on digitalization to create jobs and compete globally",
    "Investing in Somalia's Climate Resilience Now to Create Jobs",
    "DeWine: Ohio approves 7 projects to create jobs and boost local economy",
    "Big Rapids mayor targets economic development to create jobs",
    "Revolut CEO steps down after eight years",
]


@pytest.mark.parametrize("headline", GEOGRAPHY_EDGE_CASES)
def test_geography_is_never_a_gate(headline):
    """Gating on geography was tried and reverted: it also dropped "Revolut CEO
    steps down" (no place in the headline), "Intel opens new facility in
    Leixlip" and "BMS opens Mumbai capability centre". Recall is worth more
    than the fraction of a cent it saves, and a record we cannot place is
    stored unplaced rather than discarded.
    """
    keep, why = prefilter.passes(headline)
    assert keep, why


def test_the_helper_now_knows_the_countries_it_used_to_miss():
    """Uzbekistan and Somalia were classified and then rejected, money spent to
    learn something the vocabulary should have known."""
    assert prefilter.has_covered_geography("Uzbekistan bets on digitalization")
    assert prefilter.has_covered_geography("Investing in Somalia's resilience")


@pytest.mark.parametrize("headline", [
    "Stripe to create 300 new jobs in Dublin",
    "SAP raises minimum salary across German sites",
    "US telecom giant T-Mobile sets up global tech centre in Hyderabad",
    "Intel to create 500 jobs in Ireland",
])
def test_covered_geography_survives(headline):
    keep, reason = prefilter.passes(headline)
    assert keep, reason


def test_lowercase_us_is_not_the_united_states():
    """'\\bus\\b' case-insensitively would let 'join us' through as the USA."""
    assert not prefilter.has_covered_geography("Come join us and create jobs")


def test_uppercase_US_is_the_united_states():
    assert prefilter.has_covered_geography("US firm to create 400 jobs")


def test_geography_terms_track_the_registry():
    """The gate is built from vocab, so a new market needs no edit here."""
    assert prefilter.has_covered_geography("hiring in Kraków")
    assert not prefilter.has_covered_geography("hiring in Ulaanbaatar")


def test_empty_text_is_filtered():
    assert prefilter.passes("") == (False, "empty text")


def test_employment_words_match_on_boundaries_not_substrings():
    """The sibling's equivalent loop went inert for a day because 'RIF' matched
    inside 'tariff'."""
    keep, _ = prefilter.passes("New tariffs on imported steel announced")
    assert not keep


def test_a_company_expansion_with_no_people_is_filtered():
    """'Expansion' alone is never enough — that was the whole problem."""
    keep, _ = prefilter.passes("Acme announces expansion of its Dublin facility")
    assert not keep


def test_the_same_expansion_with_a_headcount_survives():
    keep, _ = prefilter.passes("Acme announces expansion of its Dublin facility, 200 jobs")
    assert keep


# --- Hebrew, Czech and Danish ----------------------------------------------
#
# Three of the fourteen languages on the wired catalogue feeds had no
# vocabulary at all, which is the same silent-and-total failure the funding
# block above was written for: the gate cannot tell "nothing happened in
# Israel this week" from "we cannot read Hebrew". Geektime, Globes, Techtime,
# Ynet and Haaretz publish in Hebrew, and the four Israeli rounds this whole
# collector exists to catch (Glow, Plantopia, Harmony, Enigma) are the visible
# end of it.
#
# Every headline marked "live" below is verbatim from that publisher's own
# feed on 2026-07-28. The constructed ones are ordinary newsroom phrasing for
# an intent the live sample happened not to contain that day.

HEBREW_SIGNALS = [
    # live, Geektime — a raise
    "פחות משנה אחרי החשיפה: Hush גייסה עוד 30 מיליון דולר",
    # live, Geektime — the SAME root used for hiring, not money
    "8 חודשים מהחשיפה: יוצאי Wiz ומיקרוסופט מגייסים שוב, הפעם ממיקרוסופט",
    # live, Geektime — a seed round
    '"התעשייה הרימה ידיים": סבב סיד חריג לסטארטאפ הישראלי Way',
    # live, Globes — headcount and pay in one line
    "הדיפנס-טק פורח: מספר המשרות זינק ב-20%, השכר קפץ מעל 50 אלף שקל",
    # live, Globes — an executive who has not started yet
    "עוד לא נכנסה לתפקיד וכבר קיבלה מצנח זהב של מיליונים",
    # constructed: an appointment, a resignation, a hiring drive
    'נעם בר מונה ליו"ר הדירקטוריון של החברה',
    "סמנכ״ל הכספים של החברה התפטר לאחר ארבע שנים",
    "החברה מגייסת עשרות עובדים למרכז הפיתוח בתל אביב",
    "החברה השלימה סבב א' בהיקף 20 מיליון דולר",
]

CZECH_SIGNALS = [
    "Startup získal investici 50 milionů korun od investorů",
    "Firma nabírá nové zaměstnance do pražské pobočky",
    "Novým ředitelem banky byl jmenován Jan Novák",
    "Zaměstnancům vzrostly mzdy o pět procent",
    "Startup uzavřel investiční kolo ve výši 100 milionů",
]

DANISH_SIGNALS = [
    # live, TechSavvy — the amount carries a Danish plural suffix, which is
    # exactly where the enclosing word boundary bit once
    "Visibuilt rejser 25 millioner kroner til at bringe biobaserede "
    "bindemidler tættere på markedet",
    # live, Bootstrapping.dk
    "Serpier rejser runde og vil lade AI-agent automatisere marketing hos webshops",
    "Dansk startup henter 40 mio. kr. i ny finansieringsrunde",
    "Novo Nordisk ansætter 500 nye medarbejdere i Kalundborg",
    "Maersk udnævner ny administrerende direktør",
    "Overenskomst giver lønstigning til 20.000 ansatte",
]


@pytest.mark.parametrize("headline", HEBREW_SIGNALS + CZECH_SIGNALS + DANISH_SIGNALS)
def test_hebrew_czech_and_danish_signals_survive(headline):
    keep, reason = prefilter.passes(headline)
    assert keep, f"dropped a genuine signal ({reason}): {headline}"


# Verbatim from the same feeds on the same day. A vocabulary that keeps
# everything is worth as little as one that keeps nothing, and the live
# measurement is the only thing that tells the two apart: with these blocks in
# place the three language groups kept 19%, 11% and 16% of what their feeds
# carried, which is the band the English gate already sits in.
HEBREW_NOISE = [
    "וואטסאפ ווב משתדרגת עם פיצ'רים חדשים: סוף סוף תאפשר לכם לעשות שיחות",
    "דירות להשכרה בתל אביב: המחירים ממשיכים לעלות",
    "העובדה שהחברה גדלה לא מעידה על רווחיות",
]
CZECH_NOISE = [
    "Po spoustě slabých filmů konečně jeden povedený. Spider-Man má slabinu",
    "Nová platforma pro streamování hudby míří do Česka",
]
DANISH_NOISE = [
    "Krigen blusser op igen: Iran angriber tre skibe",
    "Overblik: Her er dine rettigheder, hvis ferien bliver påvirket af naturbrande",
]


@pytest.mark.parametrize("headline", HEBREW_NOISE + CZECH_NOISE + DANISH_NOISE)
def test_hebrew_czech_and_danish_noise_is_filtered(headline):
    keep, reason = prefilter.passes(headline)
    assert not keep, f"would have paid to classify: {headline}"
    assert reason


@pytest.mark.parametrize("headline", [
    # live, Geektime
    '"ה-AI מאפשרת לקצר תהליכים": Papaya מפטרת 30 עובדים',
    "החברה הודיעה על פיטורים של 200 עובדים",
    # live, E15 — "will scrap another five thousand positions"
    "Porsche pokračuje v masivních škrtech. Do roku 2035 zruší dalších pět tisíc míst",
    "Firma oznámila hromadné propouštění stovek zaměstnanců",
    "Danske Bank fyrer 300 medarbejdere",
    "Vestas afskediger 500 ansatte i Danmark",
])
def test_a_cut_in_these_languages_is_recognised_as_the_siblings(headline):
    """The scope boundary has to hold in fourteen languages, not one. A rule
    that only reads English lets every non-English cut through, which is
    exactly how the Spanish Verizon row reached a page promising it collects
    none. Asserted through the helper because validate.py calls it directly as
    the backstop for a headline that hid the cut."""
    assert prefilter.workforce_reduction_term(headline)
    assert not prefilter.passes(headline)[0]


@pytest.mark.parametrize("headline", [
    "Acme gjorde comeback: henter 40 mio. efter sidste års fyringer",
    "Startup získal investici 50 milionů po loňském propouštění",
    "החברה גייסה 30 מיליון דולר אחרי הפיטורים בשנה שעברה",
])
def test_a_raise_after_a_cut_stays_ours_in_these_languages_too(headline):
    """Same rule as "Klarna hires 1,000 after AI-driven job cuts": the subject
    leads. Without the funding verbs in the in-scope list the heuristic has
    nothing to find in these languages and hands the growth story away."""
    assert prefilter.workforce_reduction_term(headline) is None
    assert prefilter.passes(headline)[0]


# --- Hebrew is not just another Latin block --------------------------------

@pytest.mark.parametrize("prefixed", [
    "החברה גייסה 30 מיליון דולר",      # ha-  (the)
    "מיליון דולר בגיוס האחרון",        # be-  (in)
    "והעובדים קיבלו בונוס שנתי",       # ve- + ha- (and the), two clitics
    'המנכ״ל של החברה מונה אתמול',      # ha- in front of an acronym
])
def test_hebrew_clitics_do_not_hide_the_word(prefixed):
    """Hebrew writes "and", "the", "in", "to", "from" and "that" as single
    letters glued to the next word, and those letters are `\\w`. So `\\b` finds
    no boundary in front of the stem and a plain `\\bגיוס\\b` matches only the
    bare noun — which is the minority of real occurrences."""
    keep, reason = prefilter.passes(prefixed)
    assert keep, reason


@pytest.mark.parametrize("lookalike", [
    "דירות להשכרה בתל אביב",           # a RENTAL contains "salary"
    "העובדה שהחברה גדלה",              # "the FACT" contains "employee"
    "נפטר בגיל 80 מייסד הקבוצה",       # "died" contains the layoff root
])
def test_hebrew_stems_are_not_matched_as_bare_substrings(lookalike):
    """The other half of the same problem. Matching these as substrings — the
    way the CJK and Arabic block does, where it is safe — would fire on
    ordinary words, and every one of these is an ordinary word."""
    assert prefilter.workforce_reduction_term(lookalike) is None
    keep, _ = prefilter.passes(lookalike)
    assert not keep


def test_a_hebrew_first_name_is_not_a_layoff():
    """"Peter" is spelled פיטר, which is also the verb "laid off", so the bare
    verb is deliberately absent from the reduction vocabulary and only the
    inflected forms are listed. A reduction verdict is a hard drop rather than
    a cheaper classification, so this is the one gate where under-matching is
    the safe direction."""
    story = "פיטר תיל משקיע בקרן חדשה"
    assert prefilter.workforce_reduction_term(story) is None
    assert prefilter.passes(story)[0]


def test_every_hebrew_alternative_survives_the_enclosing_word_boundary():
    """`_hebrew()` output is compiled inside `\\b(?:...)\\b`, so an alternative
    that ends on punctuation (a geresh, say) can never match however correct it
    looks in isolation. Cheap to assert, and invisible otherwise: the pattern
    compiles, the tests that do not cover it pass, and the term is simply
    dead."""
    import re

    for alternative in prefilter._hebrew(r"גיוס", r"סבב (?:סיד|א)", r'מנכ["\'׳״]?ל'):
        probe = re.compile(r"\b(?:" + alternative + r")\b", re.I | re.UNICODE)
        assert probe.search("החברה השלימה גיוס וגם סבב סיד, אמר המנכ״ל שלה") or \
            probe.search("החברה השלימה סבב א' השבוע"), alternative
