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
    """'Expansion' ALONE is never enough. That was the whole problem, and the
    nine headlines in REAL_NOISE are still the proof of it.

    Anchored to a place of work it is a different word. "expansion of its
    Dublin facility" is a site getting bigger, which is one of the five site
    events and a geographic hiring signal months ahead of the job adverts, so
    it now survives the free gate and the model is asked about it. That claims
    nothing about headcount: the stored row still says the source stated none.
    """
    keep, _ = prefilter.passes("Acme announces expansion in the region")
    assert not keep

    keep, _ = prefilter.passes("Acme announces expansion of its Dublin facility")
    assert keep


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


# --- Site events -----------------------------------------------------------
#
# Every headline below is verbatim from a sweep of the wired publisher feeds in
# data/sources_catalogue.csv on 2026-07-29: 9,872 items from 555 live feeds.
# Of 28 hand-labelled corporate site events in that sweep the previous phrase
# list caught 7. These are the misses, and they are not exotic — an ordinary
# noun, an ordinary verb form, a normal number of words in between, or a
# language nobody wrote the phrase in.

REAL_SITE_EVENTS = [
    # The noun was missing: "factory", "warehouse", "branch", "distribution".
    "Siemens opens electrification, automation factory in Egypt to boost local manufacturing",
    "Schnucks to shutter sole company-owned warehouse",
    "Java opens 110th branch in Kiambu’s Thindigua",
    "American Eagle Outfitters to open $41M North Carolina distribution center",
    # The verb form was missing: "opening", "build", "set up", "add".
    "Why is Amazon opening a disaster relief hub near Edmonton?",
    "Electra to build $850M manufacturing plant in Ohio",
    "AstraZeneca India to set up genomic solutions centre in Amaravati",
    "Amazon to add supply chain facilities in New York, Texas",
    # Words in between: money, adjectives, a place name.
    "Meta to build $13 billion data centre in Alberta, largest outside the U.S.",
    "Rocket Lab to open Alaska launch site under $266 million Space Force deal",
    "Bryden pi to relocate to new Chaguanas distribution hub",
    # The language was missing entirely.
    "Clínica Bíblica inicia una nueva etapa en Guanacaste con la apertura de su sede en Liberia",
    "Marshalls inaugurará nueva tienda en Arecibo y proyecta crear 60 empleos",
    "Touba : vers un nouveau site industriel de 180 hectares, selon le DG de l’APROSI",
    "Solar-Start-up: Enpal schließt Standort in Hamburg",
]


@pytest.mark.parametrize("headline", REAL_SITE_EVENTS)
def test_a_real_site_event_survives_the_free_gate(headline):
    assert prefilter.site_event_term(headline), headline
    assert prefilter.passes(headline)[0], headline


# Also verbatim from that sweep, and all of them were hits before the false
# friends existed. The same words are ordinary in property copy, in finance
# copy and in war reporting.
SITE_FALSE_FRIENDS = [
    "Singapore-based investors now the top non-local buyers of Hong Kong office assets",
    "Metro Manila IT ecozone ban lift to expand PEZA office supply—Colliers",
    "IFM Investors snaps up $300 million Melbourne shopping centre portfolio developed by Oreana",
    "Saint Lucia to benefit from new Caribbean infrastructure facility",
    "Saudi Arabia: Iraq-launched drones targeted oil facilities",
]


@pytest.mark.parametrize("headline", SITE_FALSE_FRIENDS)
def test_the_same_words_about_square_metres_and_loans_are_not_a_site_event(headline):
    assert prefilter.site_event_term(headline) is None, headline


def test_a_bare_data_centre_is_not_a_site_event():
    """It was, and it was 23 of 67 hits on its own: power demand, green loans,
    industry surveys, a zoning vote. A data centre somebody is BUILDING still
    matches, through the verb."""
    for noise in [
        "Data center demand is soaring, and off-grid gas won't fix the problem",
        "AI Data Centres To Consume 26.3 GW Power By FY32: MoS Power",
        "Mississauga to vote this week on one-year freeze on AI data centres",
    ]:
        assert prefilter.site_event_term(noise) is None, noise

    assert prefilter.site_event_term(
        "Meta to build $13 billion data centre in Alberta")


def test_site_verbs_are_anchored_on_word_boundaries():
    """Unanchored, the Turkish "taşı" (moves) matched inside the Indonesian
    "Investasi" and filed a Batam electricity-supply story as a relocation.
    Same class of bug as "RIF" inside "tariff"."""
    assert prefilter.site_event_term(
        "Batam Siapkan Listrik dan Air demi Tarik Investasi Data Center") is None


def test_a_closure_that_states_job_losses_is_still_the_siblings():
    """The boundary has to survive site events, or adding them quietly reopens
    the thing the page promises it does not publish. Both headlines are live
    German feed items about the same closure; only one of them says what
    happened to the people.

    This is why the site OPENING verbs went into _IN_SCOPE_SUBJECT and the
    CLOSURE verbs did not: an in-scope subject appearing before the cut is what
    keeps a story here, and "schließt Standort" appears before "entlassen".
    """
    with_cut = "Enpal schließt Standort in Hamburg – rund 85 Mitarbeiter entlassen"
    assert prefilter.workforce_reduction_term(with_cut)
    assert not prefilter.passes(with_cut)[0]

    without_cut = "Solar-Start-up: Enpal schließt Standort in Hamburg"
    assert prefilter.workforce_reduction_term(without_cut) is None
    assert prefilter.passes(without_cut)[0]


def test_an_opening_still_wins_a_race_against_an_old_cut():
    """"Klarna hires 1,000 after AI-driven job cuts" was already ours. A site
    opening is the same shape of story and had no subject term of its own."""
    story = "Acme opens a Dublin hub two years after its redundancy programme"
    assert prefilter.workforce_reduction_term(story) is None
    assert prefilter.passes(story)[0]


def test_a_work_policy_change_survives_in_more_than_english():
    """Measured honestly: the 9,872-item sweep held THREE work-policy headlines
    and the English phrases already caught two. This block is insurance against
    a silent zero in the other 42 languages, which is exactly what the German
    and Danish blocks are, and not a claim that it filled the pillar."""
    for headline in [
        "Dell orders staff back to the office five days a week",
        "SAP kündigt Rückkehr ins Büro für alle Standorte an",
        "Telefónica anuncia la vuelta a la oficina tres días por semana",
        "Ferrovial confirme le retour au bureau pour ses équipes",
        "Novo Nordisk indfører hjemmearbejdspolitik for hele koncernen",
        "Seznam ruší hybridní režim, zaměstnanci se vrací",
    ]:
        assert prefilter.passes(headline)[0], headline


# --- The Spanish, Polish and Greek widening (2026-08-03) --------------------
#
# Measured on the wired feeds for the four thinnest wired markets, fetched
# live through the collector's own parse: Argentina 137 items, Mexico 130,
# Poland 85, Greece 100, with Germany (265) as the covered-language control.
# Pass rates moved 11.7% -> 12.4% (AR), 9.2% -> 10.0% (MX), 7.1% -> 12.9%
# (PL); the control did not move at all (8.3% before and after), and every
# newly passing item was hand-read — all seven were genuine signals or a
# funding teaser. The LIVE items are below verbatim; the historical rounds
# are real events, spelled the way those newsrooms spelled them, standing in
# for the funding vocabulary today's corpus happened not to exercise.
LATAM_AND_POLISH_SIGNALS = [
    # live, Expansión (MX) — leadership, rejected by the old pack
    "Fabiano Hideto Ikejiri asume la dirección de MSD Salud Animal en México",
    # live, iProUP (AR) — a fund putting $10m into startups
    "Un fondo argentino apuesta u$s10 millones en las startups con mayor potencial en IA",
    # real rounds, LatAm register: financiamiento / levantar / recaudar / cerrar
    "Kavak levanta 700 millones de dólares en nueva ronda de inversión",
    "La startup mexicana Stori recauda 50 millones de dólares",
    "Clara cierra una ronda de financiamiento por 80 millones de dólares",
    "Ualá levantó una ronda serie E de 300 millones de dólares",
    # live, ITwiz — a market entry
    "TBD Solutions rozpoczyna działalność w Polsce",
    # live, MamStartup — a VC investment stated as a portfolio join
    "ForActive dołącza do portfolio Simpact Ventures",
    # real rounds, Polish register: pozyskać / zebrać, and the inflected round
    "Booksy pozyskało 70 mln dolarów finansowania",
    "ICEYE zebrał 93 mln dolarów w rundzie finansowania",
    "Nowy prezes Allegro. Rada nadzorcza powołała go na stanowisko w poniedziałek",
]

GREEK_SIGNALS = [
    "Η ελληνική startup άντλησε 5 εκατ. ευρώ για την επέκτασή της",
    "Ανακοινώθηκε η πρόσληψη νέου γενικού διευθυντή",
]


@pytest.mark.parametrize("headline", LATAM_AND_POLISH_SIGNALS + GREEK_SIGNALS)
def test_latam_polish_and_greek_signals_survive(headline):
    keep, reason = prefilter.passes(headline)
    assert keep, f"dropped a genuine signal ({reason}): {headline}"


@pytest.mark.parametrize("headline", [
    # live, Bankier.pl — price rises, not pay rises. "podwyżk" is anchored to
    # pay precisely because of this front page.
    "Inflacja w Polsce wzrosła, ale handel nie przewiduje podwyżek cen",
    # a crowd gathering is "zebrać" too; the verb is anchored to an amount
    "Przed Sejmem zebrały się tysiące protestujących",
    # government infrastructure money is not a funding round
    "El Gobierno anunció una inversión de 500 millones en carreteras",
    # drawing water is "αντλώ" as well; the verb is anchored to an amount
    "Η πυροσβεστική άντλησε νερό από τη λίμνη",
])
def test_the_anchors_on_the_new_verbs_hold(headline):
    keep, reason = prefilter.passes(headline)
    assert not keep, f"would have paid to classify: {headline}"


def test_a_polish_factory_closure_with_job_losses_is_still_the_siblings():
    """The new Polish site vocabulary must not leak a cut past the boundary:
    a closure that states job losses is the sibling's record, exactly like the
    Enpal case above."""
    with_cut = "Zamknęli fabrykę w Poznaniu. 200 pracowników zwolnionych"
    assert prefilter.workforce_reduction_term(with_cut)
    assert not prefilter.passes(with_cut)[0]

    # live, Bankier.pl — the closure alone is a site event and stays ours
    without_cut = "Zamknęli fabrykę, wybudują bloki. Zaskakujący plan firmy meblarskiej"
    assert prefilter.workforce_reduction_term(without_cut) is None
    assert prefilter.passes(without_cut)[0]
