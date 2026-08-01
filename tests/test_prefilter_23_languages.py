# -*- coding: utf-8 -*-
"""The 23 catalogue languages the free gate could not read until 2026-08-01.

WHAT WAS MEASURED
-----------------
117 of the 662 wired feeds publish in 23 languages with no phrase pack. All 117
were fetched live on 2026-08-01 (116 answered) for 2,119 items, and the gate
kept 27 of them: 1.3%. A control sample of 95 feeds whose language DOES have a
pack (six per covered language, 1,775 items) kept 9.7%.

The packs move the uncovered corpus 1.3% -> 3.3%, and NOT to 9.7%, because most
of that gap was never a language problem. A language-NEUTRAL control says so:
untranslated Latin-script tokens every one of these newsrooms writes anyway
(CEO/CFO/CTO, startup, "Series A", seed, VC, unicorn, IPO) appear in 6.5% of
the control corpus and 1.2% of the uncovered one. No regex in prefilter.py
touches that ratio. The uncovered-language feeds are national general dailies —
politics, crime, weather, sport — and the covered-language sample is
disproportionately technology and business press.

Every string below is VERBATIM from those feeds on that day. Several carry a
sentence of teaser as well as the headline, because that is what the gate is
actually handed: national_press builds `raw_text` as title plus excerpt, and
seven of these place the employment word in the excerpt rather than the title.
Trimming them to the headline would have made this file pass while the live
gate did something else.

Invented examples would have hidden every collision this file pins, because
each of them is a word doing an ordinary job in an ordinary story.
"""

from __future__ import annotations

import pytest

from pipeline import prefilter


# --- real signals, one per language that produced one --------------------

SIGNALS = [
    # Albanian — 1,300 new jobs. The employment word ("punësimin") is in the
    # excerpt, not the headline, which is the ordinary case rather than a
    # special one.
    "Durmishi: 5.4 milionë euro për mbi 1.300 vende të reja pune\n\n"
    "Ministri i Ekonomisë ka theksuar se Qeveria po realizon masa konkrete "
    "për punësimin, ekonominë dhe shëndetësinë.",
    # Bosnian — a hypermarket opening, the site event the English _SITE block
    # reads and no other language could
    "Bingo otvorio hipermarket u Vitezu i uručio donaciju Domu zdravlja",
    "Investicija vrijedna više od 10 miliona KM: Vitez dobija prvi Bingo "
    "hipermarket\n\nInvesticija je donijela i nova zaposlenja, pa je posao "
    "dobilo oko 80 ljudi.",
    # Bulgarian — new rules for part-time workers
    "Новите правила за почасовите работници: изсветляване или загуба на права",
    # Croatian — a site expansion with a number on it
    "Amazon ulaže 300 milijuna eura u proširenje logističkog centra u "
    "Njemačkoj\n\nLogistički centar koristit će stotine transportnih robota "
    "koji će zaposlenicima dopremati pokretne police.",
    "Svaki treći radnik u Hrvatskoj izgara na poslu",
    # Estonian — DHL's new Tallinn terminal
    "DHLi tippjuht panustab Eestile. Tallinna uus terminal tõi Saksa "
    "logistikahiiule kõva kasvu\n\n“Balti riikides on hea võimalus veelgi "
    "suurema äri tegemiseks,” ütles DHLi Expressi Euroopa haru tegevjuht "
    "Mike Parra.",
    # Hungarian — a 25% pay rise, and a leader standing down
    "25 százalékos béremelés jöhet a HUN-REN kutatóhálózatnál",
    "Lemondott a Magyar Külügyi Intézet amerikai vezetője",
    # Latvian — foreign workers needed across sectors
    "Ārvalstu darbinieki daudzās nozarēs ir nepieciešamība",
    # Macedonian — 43 new hires, and 300 more
    "АЛКАЛОИД со раст на инвестициите од 69%, консолидирани продажби од 168 "
    "милиони евра и 43 нови вработувања во земјата",
    "Триста нови вработувања на млади специјализанти",
    # Montenegrin — an appointment at a state road company
    "Stefan Vešović novi direktor podgoričkih Puteva",
    # Romanian — the labour market losing its young
    "Rata şomajului rămâne stabilă, dar piaţa muncii îi pierde pe tineri: "
    "aproape trei din zece nu au un loc de muncă",
    # Russian — Kazakhstan's labour market
    "Прощай, офис с 9:00-18:00: как казахстанцы меняют правила рынка труда"
    "\n\nОт офиса к ноутбуку – рынок труда меняет правила игры",
    # Serbian — minimum wage talks, the employment count, the hiring market
    "Pregovori o minimalnoj ceni rada počinju 10. avgusta",
    "Manje zaposlenih u drugom kvartalu godine u odnosu na 2025.",
    "Infostud istraživanje: Oporavlja se tržište zapošljavanja, posebno za mlade",
    # Slovenian — gross pay for May, and a working-temperature rule
    "Pri registriranih fizičnih osebah bruto plača za maj 1.698,57 EUR, neto "
    "1.113,71 EUR\n\nPovprečna bruto plača zaposlenih pri registriranih "
    "fizičnih osebah je za maj znašala 1.698,57 EUR.",
    "Temperatura v delovnih prostorih ne sme presegati 28 °C, delo na prostem "
    "le pod posebnimi pogoji\n\nDelodajalci morajo poskrbeti za ustrezne "
    "ukrepe za zaščito delavcev, poudarja Inšpektorat za delo.",
    # Nepali — a national survey of youth employment readiness
    "युवाको डिजिटल क्षमता र रोजगारी तत्परता बुझ्न राष्ट्रव्यापी सर्वेक्षण",
    # Thai — no spaces, so this one is only reachable through the CJK block
    "HBR ชี้ทำไมพนักงานตัวท็อป ถึงยื่นใบลาออกเป็นคนแรกเสมอ?",
    "ซีอีโอ Reddit บอก AI Overviews ส่งผลกระทบมากต่อทราฟิกจาก Google Search",
]


@pytest.mark.parametrize("headline", SIGNALS)
def test_a_real_signal_in_these_languages_survives(headline):
    keep, reason = prefilter.passes(headline)
    assert keep, f"dropped a genuine signal ({reason}): {headline}"


# --- the four collisions the live read actually found --------------------
#
# Each of these is why a term is anchored rather than bare. A vocabulary that
# keeps everything is worth as little as one that keeps nothing.

COLLISIONS = [
    # LATVIAN "algas" (wages) IS ESTONIAN "algas" (began). This tuple compiles
    # into ONE regex over all 23 languages at once, so a Latvian pay term has
    # to survive Estonian text. Bare `algas` held two festival listings.
    "Tartus algas väntorelifestival",
    "Haapsalus algas taas Augustibluus",
    # RUSSIAN bare nouns. "сотрудник" is any member of staff, "возглавил"
    # tops a league table, "зарплата" is a footballer's wage. All three were
    # live false positives on the Central Asian feeds.
    "Полицейский в отпуске спас пятерых детей из горящей квартиры в Караганде",
    "«Шахтер» обошел «Кайрат-Жастар» и возглавил таблицу Первой лиги",
    "Сколько зарабатывает самый дорогой футболист «Челси»",
    "Открытую форму туберкулеза выявили у школьника в Петропавловске",
    # A BARE RESIGNATION VERB IS A POLITICS FEED. Slovenian "odstopil" held
    # Boy George and two FIFA governance rows; Montenegrin "imenovana" filed
    # three seats on a fiscal council in a single run.
    "Boy George zaradi skladbe v podporo Izraelu ob vlogo v muzikalu "
    "Jesus Christ Superstar",
    "Čobi i Obradović izabrani za predsjednike odbora, imenovana tri člana "
    "Fiskalnog savjeta",
    # ICELANDIC lost every bare noun it had: "starfsmaður", "forstjóri" and
    # "störf" kept six crime and sport stories out of seven.
    "„Ég er ras­isti og ég gerði þetta viljandi“",
    "Eru of fáar konur að starfa fyrir karlalið í knatt­spyrnu?",
    # GREEK "στελέχη" (executives) held a FIFA governance row.
    "«Δεν ήθελα διχασμό»: Ο Ινφαντίνο κάνει πίσω και αποσύρει το σχέδιο για "
    "την «πώληση» του Μουντιάλ",
    # BULGARIAN: a new ambassador is an appointment, not an employer signal.
    "Нов българския посланик пристигна в Киев",
]


@pytest.mark.parametrize("headline", COLLISIONS)
def test_the_collisions_the_live_read_found_stay_filtered(headline):
    keep, _reason = prefilter.passes(headline)
    assert not keep, f"would have paid to look at: {headline}"


def test_thai_is_in_the_no_boundary_block_and_not_the_boundary_one():
    """Thai writes without spaces, so `\\b` can never fire inside a Thai
    string. A Thai term placed in _EMPLOYMENT_TERMS_INTL compiles into an
    alternative that matches nothing, silently and forever — the same shape as
    the Danish magnitude-word bug, one script further along."""
    intl = "|".join(prefilter._EMPLOYMENT_TERMS_INTL)
    assert "พนักงาน" not in intl, (
        "a Thai term is in the boundary-wrapped tuple, where it can never match")
    assert "พนักงาน" in prefilter._EMPLOYMENT_TERMS_CJK


def test_every_new_term_still_compiles_under_the_shared_boundary():
    """The tuple is wrapped in `\\b(?:...)\\b`, so an alternative that ends in a
    non-word character can never match. That is not a hypothetical: it is the
    Danish "million" bug recorded above this block in prefilter.py."""
    for term in prefilter._EMPLOYMENT_TERMS_INTL:
        assert not term.endswith(("|", "(", "[")), term
        # A trailing literal space would put the closing boundary between two
        # spaces and the alternative would be dead.
        assert not term.endswith(" "), term
