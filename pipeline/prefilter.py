"""Deterministic gate, run before the LLM ever sees a candidate.

Free filtering beats paid filtering (spec 4 rule 3). The first live run proved
why: a query containing the bare word "expansion" returned MLB expansion, World
of Warcraft expansion, Medicaid expansion, cattle herd expansion and war
escalation. Every one of those would have cost a classification call to reject.

The rule is simple: a talent signal is about **people at an employer**. If the
text contains no employment noun at all, no model needs to look at it.
"""

from __future__ import annotations

import re

# At least one of these must appear. They are the words that make a story about
# employment rather than about stadiums, herds or health policy.
_EMPLOYMENT_TERMS = (
    r"jobs?", r"hiring", r"hire[sd]?", r"roles?", r"headcount", r"staff",
    r"employees?", r"workers?", r"workforce", r"recruit\w*", r"vacanc\w+",
    r"appoint\w*", r"names? (?:its |a |new )?(?:chief|ceo|cfo|cto|president)",
    r"steps? down", r"resign\w*", r"succeeds?", r"chief \w+ officer",
    # Structural leadership news names no individual and no headcount, but is
    # squarely the leadership pillar. Kept narrow on purpose: bare "leadership"
    # matches an endless supply of AI-strategy think pieces.
    r"leadership (?:structure|team|reshuffle|shake-?up|transition)",
    r"management team", r"executive team", r"board appoint\w*",
    # C-suite acronyms, sitting in the ENGLISH block because they are not
    # English -- they are untranslated in every language we query, which is
    # exactly why they belong here rather than repeated eleven times below.
    # Found 2026-07-30: "Governanca Brasil tem novo CRO" was rejected by the
    # gate because its only signal was the acronym, and the line above only
    # matches an acronym when an English verb ("names") precedes it. A Spanish
    # "Nombran nuevo CMO" or a Japanese headline fails identically.
    #
    # Safe to match bare because these tokens are rare outside a corporate
    # leadership context and a false positive costs $0.00003 at the gate, where
    # a false negative costs the record. CEO/CFO/CTO were already reachable via
    # the "names ... " pattern and are listed here so the set is complete and
    # no future reader has to work out which four were missing and why.
    # The (?!-\w) is load-bearing: without it "Cro-Magnon" matched, because a
    # hyphen is a word boundary. No acronym here is ever the first half of a
    # hyphenated word in the copy we read.
    #
    # KNOWN LIMIT, not fixed here: this whole term list is wrapped in \b...\b,
    # and \b does not fire between a Japanese or Chinese character and a Latin
    # one -- both are word characters -- so "新しいCEOが就任" does NOT match.
    # That is a property of the wrapper, affects every term rather than these,
    # and quietly weakens the gate for CJK markets. Fixing it means changing
    # how the whole expression is anchored, which is a bigger change than this
    # one and wants its own measurement against real Japanese and Korean feeds.
    r"c(?:e|f|o|t|r|m|i|s|d|p)o\b(?!-\w)", r"chro\b(?!-\w)", r"cxo\b(?!-\w)",
    r"salar\w+", r"pay(?:rise|\srise)?", r"wages?", r"bonus\w*", r"compensation",
    r"remote work", r"hybrid work\w*", r"return to office", r"four-day week",
)
# Funding, in every language we query. This block is why the gate exists at all
# in its current form: without it, "who is raising money" was dead on arrival.
#
# The failure was silent and total. A funding headline says "Enigma Raises $71M
# in Seed Funding" and contains no employment word, so the employment gate threw
# away every single one. A live dry run on 2026-07-27 fetched 140 candidates and
# filtered 96; 78 of those 96 were funding stories. One of the four pillars had
# never produced a news record and nothing reported an error, because a free
# filter rejecting everything looks exactly like a quiet news day.
#
# A raise is a hiring signal: it is the money that pays for the roles. That is
# why it is a pillar, and why the page headline says "who is raising money".
#
# --- THE BILLION-SCALE GAP, measured 2026-08-04 ------------------------------
#
# This pack was built from seed-and-Series-A copy and it reads that register
# fluently. It could not read the register the three largest private rounds of
# 2026 were reported in, and the loss was total for four of their real
# headlines:
#
#   'OpenAI Valued at $852 Billion After Completing $122 Billion Round'
#       -- Bloomberg. A bare "$X Billion Round" is not "funding round".
#   'Anthropic lève 65 milliards de dollars en série H'
#       -- L'Usine Digitale. The pack had the NOUN "levée de fonds" and not the
#          VERB "lève", which is what a French headline actually writes.
#   'Anthropic sammelt 65 Milliarden Dollar ein'
#       -- the separable verb in its present tense; the pack had only the
#          participle "eingesammelt".
#   'Anthropic、650億ドル調達で評価額9,650億ドルに到達'
#       -- _EMPLOYMENT_TERMS_CJK had 資金調達 and not 億ドル調達.
#
# and for the whole class of headline whose only money noun is a VALUATION:
# 'Anthropic hits $965 billion valuation', 'alcanza una valoración de 965.000
# millones', 'erreicht 840 Milliarden Dollar Bewertung'.
#
# Every addition below is anchored to a stated amount or to a funding noun, the
# discipline the Czech and Danish blocks already keep: bare "valuation" is every
# market-cap column ever written, and bare "lève" lifts a weight.
#
# THE SERIES-LETTER CEILING is the sharpest one and the cheapest to fix. Every
# Series pattern in this file stopped at E or F while pipeline/cheap_extract.py
# `_STAGE` has always read `series\s+[a-k]`, so the two halves of the pipeline
# disagreed about what a funding stage is, and the round that exposed it was a
# Series H. They are the same class now. Raising the ceiling is what lets the
# French "série H" through as well, since these alternatives compile into one
# expression over every language at once.
_FUNDING_TERMS = (
    r"rais(?:e[sd]?|ing)", r"series [a-k]\b", r"seed (?:funding|round)",
    r"pre-?seed", r"funding round", r"secures? (?:\$|€|£|₹|us\$)?[\d.,]+",
    r"private placement", r"venture round", r"led the round",
    r"(?:post|pre)-?money", r"tender offer", r"secondary (?:share )?sale",
    # An amount at scale, followed by the noun that makes it a raise. This is
    # the Bloomberg shape, and TWO details of it are load-bearing.
    #
    # It ends on a WORD, because an alternative ending in `[\d.,]+` can never
    # match "$71M" at all: the trailing \b this tuple is compiled inside lands
    # between the digit and the M and fails, and backtracking cannot save it.
    # (`secures? ...[\d.,]+` above has exactly that defect. Left alone here
    # because fixing it is a separate measurement.)
    #
    # And it STARTS on the digits with the currency symbol optional, because
    # the LEADING \b cannot fire in front of a '$' either -- a space and a '$'
    # are both non-word characters, so an alternative beginning `[\$€£₹]` is
    # unreachable in every string that writes the symbol after a space, which
    # is all of them.
    r"(?:[\$€£₹]\s?)?[\d.,]+\s*(?:billion|trillion|million|bn|tn|m)\s+"
    r"(?:round|raise|financing|funding)",
    # A valuation, in either order, and only ever beside a figure at scale.
    # "$122,000 valuation" has no scale word and does not reach this.
    r"valu(?:ed|ation)\s+(?:at|of)\s+(?:about |around |nearly |over |more than )?"
    r"[\$€£₹]?\s?[\d.,]+\s*(?:billion|trillion|million|bn|tn)",
    r"(?:[\$€£₹]\s?)?[\d.,]+\s*(?:billion|trillion|million|bn|tn)\s+valuation",
    # German, French, Spanish, Portuguese, Italian, Dutch
    r"finanzierungsrunde", r"eingesammelt", r"kapitalrunde",
    r"lev\w*e de fonds", r"tour de table",
    # French: the verb, anchored to what is being raised.
    r"l[èe]ve\s+(?:\S+\s+){0,3}?(?:millions?|milliards?)",
    r"valorisation de\s+(?:\S+\s+){0,3}?(?:millions?|milliards?)",
    # German: the separable verb in its present tense, and a valuation in
    # either order. "sammelt" alone collects donations; the scale word and the
    # trailing "ein" are what make it a round.
    r"sammelt\s+(?:\S+\s+){0,4}?(?:millionen|milliarden|mio\.?|mrd\.?)\w*"
    r"(?:\s+\S+){0,3}?\s+ein\b",
    r"bewertung von\s+(?:\S+\s+){0,3}?(?:millionen|milliarden|mio\.?|mrd\.?)",
    r"(?:millionen|milliarden|mio\.?|mrd\.?)\w*\s+(?:\S+\s+){0,2}?bewertung",
    # Spanish
    r"valoraci\w*n de\s+(?:\S+\s+){0,3}?(?:millon\w*|mil millones)",
    # `financia\w+` and not `financiaci\w*n`: Latin American business copy
    # writes "ronda de financiamiento" (El Economista, El CEO, iProUP) where
    # Spain writes "financiación", and the narrower stem silently dropped the
    # whole LatAm phrasing (measured on the wired AR/MX feeds, 2026-08-03).
    r"ronda de (?:financia\w+|inversi\w*n)", r"capta\w*",
    r"rodada de (?:investimento|financiamento)",
    r"round di finanziamento", r"raccoglie",
    r"financieringsronde",
)

# --- Hebrew word boundaries ------------------------------------------------
#
# Hebrew needs its own wrapper, and skipping it is a silent bug rather than a
# loud one. Two properties break the assumptions every other Latin block here
# rests on:
#
#   1. There are no capitals, so `re.I` buys nothing and hides nothing.
#   2. The clitics are GLUED to the next word. "and", "the", "in", "to",
#      "from" and "that" are single letters written with no space, so
#      "בגיוס" (in the raise) and "שהעובדים" (that the employees) are one
#      token each.
#
# Hebrew letters are `\w` under Python's Unicode rules, so `\b` sees the whole
# glued token as one word and finds no boundary in front of the stem: a plain
# `\bגיוס\b` matches the bare noun and misses every prefixed form, which is
# most of them. Writing the stems as bare substrings instead — the way the CJK
# and Arabic block below does — is the opposite failure: "שכר" (salary) is
# inside "השכרה" (a rental) and "עובד" (employee) is inside "העובדה" (the
# fact), and both of those are ordinary headline words.
#
# So the boundary is spelled out: nothing Hebrew immediately before, up to two
# clitic letters, the stem with its own inflections, nothing Hebrew
# immediately after. `נפטר` (died) cannot reach `פטר` because נ is not a
# clitic, which is exactly the kind of collision the explicit set buys.
_HEB = "א-ת"          # alef..tav, the five final forms included
_HEB_CLITICS = "והבלכמש"        # ve- ha- be- le- ke- mi- she-


def _hebrew(*stems: str) -> tuple[str, ...]:
    """Hebrew stems, each wrapped in the boundary `\\b` cannot express.

    Every stem states its own suffixes. Allowing a trailing `\\w*` instead
    would be shorter and would put "שכר" back inside "השכרה".
    """
    return tuple(
        rf"(?<![{_HEB}])[{_HEB_CLITICS}]{{0,2}}(?:{stem})(?![{_HEB}])"
        for stem in stems
    )


# The same gate in the languages Google News is queried in. Without these, a
# German or Portuguese headline fails the free filter and is dropped before it
# ever reaches the model, so querying those editions would have produced
# exactly zero records while looking like it was working.
#
# Grouped by language for review. Each group is the vocabulary of the three
# intents its queries ask about: who is leading, who is hiring, who raised.
_EMPLOYMENT_TERMS_INTL = (
    # German
    r"stellen", r"arbeitspl\w+", r"mitarbeiter\w*", r"besch\w*ftigt\w*",
    r"vorstandsvorsitzend\w+", r"gesch\w*ftsf\w*hrer\w*", r"personalchef\w*",
    r"einstell\w+", r"tritt zur\w*ck", r"geh\w*lt\w*", r"l\w*hne",
    r"finanzierungsrunde", r"eingesammelt",
    # French
    r"emplois?", r"salari\w+", r"recrut\w+", r"embauch\w+", r"effectifs?",
    r"directeur g\w*n\w*ral", r"pdg", r"d\w*mission\w*", r"salaires?",
    r"lev\w*e de fonds",
    # Spanish. Widened 2026-08-03 after a live read of the wired Argentine and
    # Mexican feeds (137 + 130 items): the pack knew Spain's register and not
    # Latin America's. "Fabiano Hideto Ikejiri asume la dirección de MSD Salud
    # Animal en México" is a leadership row this gate rejected, and every
    # funding verb LatAm newsrooms actually use (levantar, recaudar, cerrar
    # una ronda, "financiamiento") was absent while the one phrase present
    # ("ronda de financiación") is Spain-only. The money verbs are anchored to
    # an amount or a round the way the Czech and Danish blocks anchor theirs:
    # bare "levanta" is a crane and bare "recauda" is a tax office.
    r"empleos?", r"empleados?", r"contrata\w*", r"plantilla", r"puestos?",
    r"vacantes?", r"sueldos?",
    r"consejero delegado", r"director general", r"dimit\w+", r"salarios?",
    r"nombramiento\w*", r"asume (?:la |el )?(?:direcci\w*n|presidencia|gerencia)",
    r"asume como", r"nuev\w+ director\w*",
    r"designad\w+ (?:como|nuev\w+|director\w*)",
    r"renunci\w+ (?:a la |al |como )",
    r"ronda de financia\w+", r"ronda de inversi\w*n",
    r"ronda semilla", r"capital semilla",
    r"levant\w+\s+(?:\S+\s+){0,3}?(?:millon\w*|mdd|mdp|capital|una ronda)",
    r"recauda\w*\s+(?:\S+\s+){0,3}?millon\w*",
    r"cierra (?:una |su )?ronda", r"obtien\w+ financiamiento",
    r"millon\w+ en (?:las )?startups?",
    # Portuguese. The hiring side was fine; the FUNDING side had exactly one
    # phrase, "rodada de investimento", which is the formal register and not
    # what Brazilian business copy actually writes. Measured 2026-07-30 by
    # re-reading the 156 items the gate rejected in one Brazilian sweep: 2 were
    # genuine misses (~1.3%), "Governanca Brasil tem novo CRO" and "GS1
    # Ventures faz seu primeiro aporte". The verbs below are the ones those
    # newsrooms use -- captar, aportar, levantar -- plus the round names, which
    # appear in Portuguese copy untranslated.
    #
    # Deliberately NOT added: bare "rodada" (a round of talks or fixtures),
    # bare "levanta" (levantamento is a survey) and bare "capta" without a
    # tense ending (captura). Each would have widened the gate far past the
    # 1.3% it is meant to recover, and a gate that lets everything through is
    # the read budget spent on nothing.
    r"empregos?", r"funcion\w*rios?", r"contrat\w+", r"vagas?", r"quadro de pessoal",
    r"presidente-executivo", r"diretor-?geral", r"demiss\w+", r"sal\w*rios?",
    r"rodada de investimento", r"aportes?", r"aportou", r"capta\w*ão", r"captou",
    r"levantou", r"s\w*rie [a-k]\b", r"pr\w*-seed", r"investimento semente",
    # Italian
    r"posti di lavoro", r"dipendenti", r"assunzion\w+", r"assumer\w+", r"organico",
    r"amministratore delegato", r"dimission\w+", r"stipend\w+",
    r"round di finanziamento",
    # Dutch
    r"banen", r"medewerkers?", r"personeel", r"aannem\w+", r"vacatures?",
    r"topman", r"bestuursvoorzitter", r"stapt op", r"salaris\w*",
    r"financieringsronde",
    # Polish. Widened 2026-08-03 after a live read of the four wired Polish
    # feeds (85 items) with MamStartup — Poland's dedicated startup-funding
    # title — among them and one Polish-publisher row ever stored. Two kinds
    # of gap. First, inflection: "runda finansowania" is the nominative, and
    # a Polish headline as often writes "rundę/rundzie finansowania", so the
    # noun is now stemmed. Second, the verbs: "ForActive dołącza do portfolio
    # Simpact Ventures" and "TBD Solutions rozpoczyna działalność w Polsce"
    # are a funding row and a market entry this gate rejected. The money
    # verbs are anchored (pozyskał grant data too; zebrał a crowd), and
    # "podwyżk" is anchored to pay because bare it is every price-rise story
    # on Bankier's front page ("nie przewiduje podwyżek cen").
    r"prezes\w*", r"zatrudni\w*", r"miejsc pracy", r"pracownik\w+",
    r"rezygnuje", r"rezygnacj\w+", r"wynagrodzeni\w+",
    r"rund\w+ finansowania", r"rund\w+ inwestycyjn\w+",
    r"pozyska\w+\s+(?:\S+\s+){0,3}?(?:mln|milion\w*|finansowani\w*|inwestor\w*)",
    r"zebra\w+\s+(?:\S+\s+){0,3}?(?:mln|milion\w*)",
    r"od inwestorów", r"dołącza do portfolio",
    r"dyrektor\w* generaln\w*", r"dyrektor\w* zarządzając\w*",
    r"mianowan\w+",
    r"powoła\w+\s+(?:\S+\s+){0,2}?(?:na stanowisko|do zarządu|na prezesa)",
    r"obejmuje (?:stanowisko|funkcję|stery)",
    r"odchodzi z (?:firmy|funkcji|stanowiska|zarządu)",
    r"rekrutacj\w+", r"pensj\w+",
    r"podwyżk\w+ (?:płac|wynagrodze\w+|pensji)",
    r"rozpoczyna działalność",
    # Swedish
    r"\bvd\b", r"anställ\w+", r"jobb", r"medarbetare", r"personal",
    r"lämnar sin post", r"finansieringsrunda",
    # Turkish. The first pass had only eight terms and let 22% of real Turkish
    # hiring headlines through; the misses were all ordinary newsroom wording
    # ("personel alımı", "kadro", "atandı") rather than anything exotic.
    r"genel müdür\w*", r"istihdam", r"işe al\w+", r"çalışan\w*",
    r"görevinden ayrıl\w+", r"maaş\w*", r"yatırım turu", r"yatırım aldı",
    r"personel alım\w*", r"personel", r"kadro\w*", r"atand[ıi]", r"atama\w*",
    r"iş ilan\w*", r"eleman alım\w*", r"memur alım\w*", r"başkan\w* seçildi",
    r"yönetim kurulu", r"ceo['’]?su", r"görevine getirildi", r"istifa\w*", r"ceo['’]s[uü]",
    r"yeni ceo", r"ceo oldu", r"ceo olarak", r"ceo atand[ıi]",
    r"ücret\w*", r"zam", r"yeni ofis", r"fabrika açt\w+", r"tesis açt\w+",
    # Indonesian / Malay
    r"direktur utama", r"merekrut", r"lowongan", r"karyawan", r"pegawai",
    r"mengundurkan diri", r"pendanaan", r"putaran pendanaan",
    # Vietnamese
    r"tổng giám đốc", r"tuyển dụng", r"nhân viên", r"việc làm",
    r"từ chức", r"gọi vốn", r"huy động vốn",
    # Czech. Note what is NOT here: bare "investice", "investor", "investuje".
    # A live read of cc.cz, e15.cz and lupa.cz on 2026-07-28 kept 15 of 55
    # items and NINE of the fifteen were held by one of those three nouns —
    # an investment portfolio, a carmaker's share price, a startup failing to
    # repay creditors. English has the same trap and answers it the same way:
    # "investment" is not an employment term, "funding round" is.
    r"zaměstna\w+", r"pracovní\w* míst\w*", r"nábor\w*", r"nabír\w+",
    r"ředitel\w*", r"nov\w+ šéf\w*", r"šéfem se stal\w*", r"do čela",
    r"jmenova\w+", r"jmenuj\w+", r"vedení firmy",
    r"rezignova\w+", r"odstupuje", r"odchází z čela", r"představenstv\w+",
    r"mzd\w+", r"mezd", r"plat(?:y|u|ů|ech|ům)?", r"platov\w+", r"odměn\w+",
    r"získal\w*\s+(?:\S+\s+){0,4}?(?:investic\w+|milion\w+|miliard\w+|korun\w*)",
    r"od investorů", r"investic\w+\s+(?:ve výši|za|od)",
    r"vstoupil\w*\s+do\s+(?:firmy|startupu|společnosti)",
    r"kolo financování", r"investiční kolo", r"rizikový kapitál",
    r"seed(?:ov\w+)?\s+(?:kolo|investic\w+)", r"série [a-k]\b",
    # Danish. "rejser" and "henter" are the two verbs a Danish funding
    # headline actually uses, and both are ordinary words on their own
    # ("travels", "collects"), so each is anchored to what is being raised.
    r"ansæt\w+", r"ansatte?", r"medarbejder\w*", r"personale\w*",
    r"stilling(?:er|erne|en)?", r"arbejdsplads\w*", r"beskæftigelse\w*",
    r"rekrutter\w+", r"direktør\w*", r"topchef\w*", r"udnævn\w+",
    r"tiltræder", r"fratræder", r"bestyrelsesformand", r"ny chef",
    r"løn\w*", r"mindsteløn", r"lønstigning\w*",
    # The trailing `\w*` on the amount is load-bearing, not decoration: this
    # whole tuple is compiled inside `\b(?:...)\b`, so an alternative ending
    # at "million" matches nothing in "rejser 25 millioner kroner" — the
    # trailing boundary lands mid-word and fails. That is a live TechSavvy
    # funding headline (Visibuilt) and it read as a clean miss.
    r"rejse[rt]?\s+(?:en\s+|ny\s+)?(?:runde|kapital|finansiering|"
    r"[\d.,]+\s*(?:mio|mia|million\w*|milliard\w*))",
    r"henter\s+(?:[\d.,]+|million\w*|milliard\w*|kapital|investering\w*|"
    r"tocifret|trecifret)",
    r"finansieringsrunde", r"kapitalrunde", r"kapitalindsprøjtning",
    r"vækstkapital", r"investorpenge",
    # Same reasoning as the Czech block: bare "investering" held a data
    # breach story and a defence-spending analysis on the live Børsen and
    # Version2 feeds, so it is anchored to somebody receiving one.
    r"(?:henter|rejser|får|sikrer sig|lander)\s+(?:en\s+|ny\s+)?investering\w*",
    # Hebrew. Wired feeds: Geektime, Globes (two Hebrew nodes), Techtime,
    # Ynet, Haaretz. Until this block existed the whole set was invisible to
    # the gate, which is the recall loss the four missed Israeli rounds
    # (Glow, Plantopia, Harmony, Enigma) were the visible end of.
    *_hebrew(
        # Funding. ג.י.ס is one root for "raised money" and "recruited
        # people", so this group earns its place twice over: "Hush גייסה עוד
        # 30 מיליון דולר" (raised another $30m) and "יוצאי Wiz מגייסים שוב"
        # (Wiz alumni are hiring again) are both live Geektime headlines and
        # both are signals here.
        r"גיוס(?:ים)?", r"גייס(?:ה|ו|תי|ת)?", r"מגייס(?:ת|ים|ות)?", r"לגייס",
        # "סבב א'" ends in a geresh, which is punctuation rather than a
        # letter, so including it would put the trailing `\b` between two
        # non-word characters and the alternative would never match. The
        # stem stops at the letter and the geresh falls outside.
        r"סבב (?:סיד|גיוס|השקעה|הון|א)",
        # Anchored to "invests IN", for the reason the Czech and Danish
        # blocks are: the bare noun held "משקיע העל מגדיל את ההימור" (a
        # markets column about a short position) on the live Globes feed.
        r"השקע(?:ה|ות)", r"משקיע(?:ה|ים|ות)?\s+ב\S*", r"הון סיכון",
        # Hiring and employment. "העסקה" is deliberately absent: with the
        # definite article it is indistinguishable from "עסקה", a deal, and
        # it held two M&A stories. The construct form is unambiguous.
        r"עובד(?:ים|ות|ת)?", r"משר(?:ה|ות)", r"מועסק(?:ים|ות)?",
        r"העסקת", r"תנאי העסקה", r"מעסיק(?:ה|ים)?", r"תעסוקה", r"כוח אדם",
        r"דרושים", r"הייטקיסט(?:ים)?", r"טאלנט(?:ים)?", r"שוק העבודה",
        # Leadership. The acronyms are written with a gershayim, which is
        # punctuation rather than a letter and comes in an ASCII and a
        # Unicode flavour depending on the CMS.
        r"מנכ[\"'׳״]?ל(?:ית)?", r"סמנכ[\"'׳״]?ל(?:ית)?", r"יו[\"'׳״]?ר",
        r"מינו(?:י|יים)", r"מונ(?:ה|תה)", r"ימונה", r"נכנס(?:ה|ים)? לתפקיד",
        r"התפטר(?:ה|ו|ות)?", r"מתפטר(?:ת|ים)?", r"פרישה", r"דירקטוריון",
        # Pay
        r"שכר", r"משכור(?:ת|ות)", r"בונוס(?:ים)?", r"תגמול(?:ים)?",
        r"מצנח זהב",
    ),

    # ---------------------------------------------------------------------
    # THE OTHER 117 FEEDS: the languages the catalogue wires and this gate
    # could not read (added 2026-08-01).
    #
    # WHAT WAS MEASURED, AND WHY THE HEADLINE NUMBER IS NOT THE ONE TO QUOTE
    # ---------------------------------------------------------------------
    # 117 of the 662 wired feeds publish in 23 languages that had no phrase
    # pack, and those feeds passed the gate at 1.3% (27 of 2,119 items pulled
    # live from all 117 on 2026-08-01) against 9.7% for a control sample of
    # feeds whose language DOES have a pack (173 of 1,775, 95 feeds, six per
    # covered language). A 7x gap.
    #
    # Most of that gap is NOT this file's fault, and the packs below therefore
    # recover much less than the gap implies. A LANGUAGE-NEUTRAL control says
    # so: untranslated tokens that every one of these newsrooms writes in
    # Latin script anyway — CEO/CFO/CTO, "startup", "Series A", "seed", "VC",
    # "unicorn", "IPO" — appear in 6.5% of the CONTROL corpus and 1.2% of the
    # uncovered-language corpus. That ratio owes nothing to any regex here.
    # The uncovered-language feeds are national general dailies (Blic, MRT,
    # MIA, Nova.rs, Unimedia): politics, crime, weather and sport. The
    # covered-language sample is disproportionately technology and business
    # press. They are not the same population, and about five sixths of the
    # 7x is which feeds are wired rather than which languages are read.
    #
    # So the honest number is the measured one: these packs move the uncovered
    # corpus from 1.3% to 3.3%, not to 9.7%. 42 extra candidates per 2,119
    # items; a hand-read of all 42 puts ~24 in scope, which is a better
    # precision than the English gate's own. Cost is NOT zero and should not be
    # quoted as zero: 42 extra candidates a run is 42 x 2/day x 30 x $0.00003
    # ~= $0.08/month at the gate, and nothing at the read-through, which is
    # capped by classify.READTHROUGH_CAP and reallocated rather than raised.
    #
    # RULES THESE OBEY, EACH ONE PAID FOR BY A FALSE POSITIVE IN THE LIVE READ
    # -----------------------------------------------------------------------
    # * This whole tuple compiles into ONE regex over EVERY language at once,
    #   so a term has to survive the other 22 languages' text as well as its
    #   own. Latvian "algas" (wages) is Estonian "algas" (began) and held two
    #   festival listings; it is gone, and Latvian pay is anchored to
    #   "minimala/videja alga" instead. This is the same class of bug as the
    #   Hebrew clitic problem above, arriving from the opposite direction.
    # * A bare everyday noun is not a term. Russian "сотрудник" is any member
    #   of staff and held a policeman on holiday and a schoolboy with TB;
    #   "возглавил" topped a league table; "зарплата" was a footballer's wage.
    #   Every Russian and Ukrainian term below is anchored to an employer.
    # * A bare resignation verb is a politics feed. Slovenian "odstopil" held
    #   Boy George and two FIFA stories; Albanian "dorëheqje" is what a street
    #   protest chants at the prime minister; Montenegrin "imenovana" filed
    #   three council seats in one run. Each is anchored to a company office.
    # * Icelandic lost every bare noun it had: "starfsmaður", "forstjóri" and
    #   "störf" kept six crime and sport stories out of seven. What is left
    #   matches the EVENT ("ráðinn sem", "lætur af störfum") and keeps nothing
    #   in this sample, which is the correct answer for 50 items of Icelandic
    #   general news rather than a failure.
    # * Site events count. "Bingo otvorio hipermarket u Vitezu" and "Amazon
    #   ulaže 300 milijuna eura u proširenje logističkog centra" are the
    #   geographic hiring signal the _SITE block below reads in English only.
    #
    # Thai has no spaces and lives in _EMPLOYMENT_TERMS_CJK, not here: a \b
    # cannot fire inside a Thai string, so a term placed here would compile
    # into an alternative that can never match.
    # Serbian / Croatian / Bosnian / Montenegrin — 30 feeds, one
    # vocabulary under four catalogue labels, Latin script as these feeds write
    # it.
    r"zaposlen\w*", r"zapošljav\w+", r"zapošlj\w+", r"radnic\w+",
    r"radnik\w*", r"radn\w+ mjest\w+", r"radn\w+ mest\w+",
    r"generaln\w+ direktor\w*", r"izvršn\w+ direktor\w*",
    r"nov\w+ direktor\w*", r"direktor\w* kompanije",
    r"imenovan\w* (?:\S+\s+){0,2}?(?:direktor\w*|izvršn\w+|predsjednik\w* uprave|čelnik\w*)",
    r"podn\w+ ostavku", r"smjenj\w+", r"konkurs za posao",
    r"oglas\w* za posao", r"minimaln\w+ (?:plat|plać)\w*",
    r"prosječn\w+ plać\w*", r"prosečn\w+ plat\w*", r"plate zaposlen\w+",
    r"povećanje plat\w+", r"(?:cijen|cen)[aeiu] rada",
    r"rund[au] finansiranja", r"rund[au] financiranja",
    r"investicijsk\w+ rund\w+",
    r"prikupi\w+ (?:\S+\s+){0,3}?(?:milion\w*|milijun\w*)",
    r"nov[au] fabrik\w*", r"nov[aiu] tvornic\w*",
    r"otvor\w+ (?:\S+\s+){0,2}?(?:pogon|fabrik\w*|tvornic\w*|hipermarket|poslovnic\w+)",
    # Macedonian
    r"работни места", r"вработув\w+", r"вработен\w*", r"работници",
    r"генерален директор", r"извршен директор", r"нов директор",
    r"именуван\w* за", r"назначен\w* за", r"назначув\w+ на",
    r"поднесе оставка", r"огласи за работа", r"конкурс за работа",
    r"минимална плата", r"просечна плата", r"рунда финансирање",
    r"инвестициска рунда",
    r"отвор\w+ (?:\S+\s+){0,2}?(?:фабрик\w*|погон\w*)",
    # Bulgarian
    r"работни места", r"наема\w* (?:служител|работник)\w*", r"служител\w+",
    r"работници", r"заетост", r"пазара на труда", r"изпълнителен директор",
    r"главен изпълнителен",
    r"назначен\w* за (?:\S+\s+){0,2}?(?:директор|управител|шеф)\w*",
    r"подаде оставка", r"минимална заплата", r"средна заплата",
    r"кръг финансиране", r"инвестиционен кръг",
    # Slovenian
    r"delovn\w+ mest\w*", r"zaposl\w+", r"delavc\w+", r"zaposlovanj\w+",
    r"generaln\w+ direktor\w*", r"izvršn\w+ direktor\w*",
    r"nov\w+ direktor\w*",
    r"imenovan\w* (?:\S+\s+){0,2}?(?:direktor\w*|predsednik\w* uprave)",
    r"odstopil\w* (?:s|z) mesta",
    r"na čelo (?:\S+\s+){0,2}?(?:podjetj|družb|bank)\w*",
    r"minimaln\w+ plač\w*", r"povprečn\w+ plač\w*", r"plače zaposlen\w+",
    r"krog financiranja", r"naložben\w+ krog",
    r"zbral\w*\s+(?:\S+\s+){0,3}?milijon\w*",
    # Slovak
    r"pracovn\w+ miest\w*", r"zamestnanc\w+", r"zamestnáva\w*", r"nábor\w*",
    r"generáln\w+ riadit\w+", r"výkonn\w+ riadit\w+", r"nov\w+ riadit\w+",
    r"vymenova\w+ (?:\S+\s+){0,2}?(?:riadit|šéf)\w*", r"rezignova\w+",
    r"odstúpil\w* z (?:funkcie|čela)", r"minimáln\w+ mzd\w+",
    r"priemern\w+ mzd\w+", r"mzdy zamestnanc\w+", r"kolo financovania",
    r"investičn\w+ kolo",
    # Russian
    r"рабочих мест", r"нанима\w+ сотрудник\w+",
    r"наб(?:ор|ирает) (?:персонал|сотрудник)\w*", r"ваканси\w+",
    r"трудоустройств\w+", r"рынок труда", r"штат сотрудник\w+",
    r"генеральн\w+ директор\w*", r"гендиректор\w*",
    r"исполнительн\w+ директор\w*",
    r"назначен\w* (?:\S+\s+){0,2}?(?:директор\w*|руководител\w+)",
    r"возглав\w+ (?:\S+\s+){0,2}?(?:компани\w+|банк\w*|холдинг\w*|корпораци\w+)",
    r"поки(?:дает|нул) пост", r"уш[её]л с поста", r"подал в отставку",
    r"средн\w+ зарплат\w+", r"повышени\w+ зарплат\w*",
    r"индексаци\w+ зарплат\w*", r"раунд финансирования", r"инвестраунд\w*",
    r"посевн\w+ раунд", r"привлек\w*\s+(?:\S+\s+){0,3}?(?:миллион\w*|млн)",
    # Ukrainian
    r"робочих місць",
    r"наймає (?:\S+\s+){0,2}?(?:працівник|співробітник)\w*", r"вакансі\w+",
    r"працевлаштуванн\w+", r"ринок праці", r"генеральн\w+ директор\w*",
    r"гендиректор\w*", r"виконавч\w+ директор\w*",
    r"призначен\w* (?:\S+\s+){0,2}?(?:директор\w*|керівник\w*)",
    r"очолив (?:\S+\s+){0,2}?(?:компані\w+|банк\w*|холдинг\w*)",
    r"подав у відставку", r"залишає посаду", r"середн\w+ зарплат\w+",
    r"мінімальн\w+ зарплат\w+", r"раунд фінансуванн\w+",
    r"інвестиційн\w+ раунд",
    r"залучив\w*\s+(?:\S+\s+){0,3}?(?:мільйон\w*|млн)",
    # Romanian
    r"locuri de muncă", r"loc de muncă", r"angajat\w*", r"angajeaz\w+",
    r"angajăr\w+", r"recrut\w+", r"forța de muncă", r"forţa de muncă",
    r"director general", r"director executiv", r"nou director",
    r"numit în funcți\w+", r"numit în funcţi\w+", r"demision\w+",
    r"preia conducerea", r"salari[uți]\w*", r"salariul minim", r"salarii",
    r"rundă de finanțare", r"rundă de investiți\w+", r"finanțare seed",
    r"a atras\s+(?:\S+\s+){0,3}?(?:milioane|milion)",
    # Greek
    r"θέσε\w+ εργασίας", r"προσλήψ\w+", r"προσλαμβάν\w+", r"εργαζόμεν\w+",
    r"υπάλληλ\w+", r"απασχόληση", r"στελέχωση", r"νέα στελέχη",
    r"διευθύνων σύμβουλος", r"διευθύνοντα σύμβουλο", r"γενικός διευθυντής",
    r"διορίστηκε", r"διορισμ\w+", r"ανέλαβε καθήκοντα",
    r"αναλαμβάνει καθήκοντα", r"παραιτήθηκε από", r"μισθ[οόώ]\w*",
    r"κατώτατος μισθός", r"γύρο\w* χρηματοδότησης", r"χρηματοδότηση seed",
    # The singular has a different accented stem (πρόσληψη vs προσλήψεις), so
    # the plural-only stem above misses it; and "άντλησε 5 εκατ." is the verb
    # a Greek funding headline actually leads with, anchored to the amount
    # because bare it also draws water (added 2026-08-03).
    r"πρόσληψ\w+",
    r"άντλησ\w+\s+(?:\S+\s+){0,3}?(?:εκατ|δισ|κεφάλαι\w+|χρηματοδότησ\w+)",
    # Hungarian
    r"munkahely\w*", r"munkavállaló\w*", r"alkalmazott\w*",
    r"foglalkoztat\w+", r"toborz\w+", r"munkaerő\w*", r"vezérigazgató\w*",
    r"ügyvezető\w*", r"kinevez\w+", r"lemondott", r"élére áll",
    r"minimálbér\w*", r"béremelés\w*", r"finanszírozási kör\w*",
    r"befektetési kör\w*", r"tőkebevonás\w*",
    # Finnish
    r"työpaikk\w+", r"työntekij\w+", r"henkilöst\w+", r"rekrytoi\w*",
    r"rekrytoin\w+", r"toimitusjohtaj\w+", r"nimitettiin", r"nimitetty",
    r"jättää tehtävän", r"irtisanoutu\w+", r"palkka\w*", r"palkko\w+",
    r"palkankorotu\w+", r"rahoituskierro\w+", r"siemenrahoitu\w+",
    r"keräsi\s+(?:\S+\s+){0,3}?miljoona\w*",
    # Norwegian
    r"ansatt\w*", r"ansetter", r"arbeidsplass\w*", r"rekrutter\w+",
    r"administrerende direktør", r"konsernsjef\w*", r"utnevn\w+", r"ny sjef",
    r"går av som", r"trekker seg som", r"lønn\w*", r"lønnsøkning\w*",
    r"finansieringsrunde\w*", r"kapitalinnhenting\w*",
    r"henter\s+(?:\S+\s+){0,3}?(?:millioner|milliarder)",
    # Icelandic
    r"nýr forstjór\w*", r"nýr framkvæmdastjór\w*", r"ráðin\w* (?:sem|til)",
    r"ráðning\w* (?:nýs|forstjór|framkvæmdastjór)\w*", r"lætur af störfum",
    r"hættir sem (?:forstjór|framkvæmdastjór)\w*", r"störfum fjölgar",
    r"fjölga starfsm\w+", r"launahækkun\w*", r"kjarasamning\w*",
    r"fjármögnun\w*", r"hlutafjáraukning\w*",
    # Estonian
    r"töötaja\w*", r"töökoh\w+", r"värba\w+", r"tööle võt\w+",
    r"tegevjuh\w+", r"juhatuse esimees", r"juhatuse liige",
    r"nimetati ametisse", r"astus tagasi", r"lahkub amet\w+", r"palga\w*",
    r"palgad", r"palgatõus\w*", r"rahastusvoor\w*", r"investeeringuvoor\w*",
    r"kaasas\s+(?:\S+\s+){0,3}?miljon\w*",
    # Latvian
    r"darbinieku?\w*", r"darbiniek\w+", r"darbavie\w+", r"vakanc\w+",
    r"valdes priekšsēdētāj\w*", r"izpilddirektor\w*",
    r"iecelt\w* (?:\S+\s+){0,2}?(?:direktor|vadītāj|amat)\w*",
    r"atkāpjas no amata", r"atstāj amatu", r"minimāl\w+ alg\w+",
    r"alg[au] (?:pieaug|kāpum|palielin)\w*", r"vidēj\w+ alg\w+",
    r"finansējuma kārt\w+", r"investīciju kārt\w+",
    r"piesaistīj\w*\s+(?:\S+\s+){0,3}?miljon\w*",
    # Lithuanian
    r"darbuotoj\w+", r"darbo viet\w+", r"įdarbin\w+",
    r"generalinis direktori\w+", r"vadov\w+ tapo",
    r"paskirt\w* (?:\S+\s+){0,2}?(?:direktori|vadov)\w*",
    r"atsistatydin\w+ iš", r"traukiasi iš", r"atlyginim\w+",
    r"minimal\w+ alg\w+", r"finansavimo raund\w+", r"investicij\w+ raund\w+",
    r"pritraukė\s+(?:\S+\s+){0,3}?milijon\w*",
    # Albanian
    r"vende pune", r"vend pune", r"punonjës\w*", r"punësim\w*", r"punëson",
    r"rekrutim\w*", r"fuqinë punëtore", r"drejtor i përgjithshëm",
    r"drejtor ekzekutiv", r"emërohet (?:\S+\s+){0,2}?drejtor\w*",
    r"emëruar (?:\S+\s+){0,2}?drejtor\w*", r"largohet nga detyra",
    r"lë detyrën", r"paga minimale", r"paga mesatare", r"raund financimi",
    r"raund investimi",
    # Nepali
    r"कर्मचारी", r"रोजगार\w*", r"नियुक्त", r"नियुक्ति", r"राजीनामा", r"तलब",
    r"प्रमुख कार्यकारी", r"महाप्रबन्धक",
    # Swahili
    r"ajira", r"wafanyakazi", r"kuajiri", r"ameajiriwa", r"mkurugenzi mkuu",
    r"afisa mkuu mtendaji", r"kuteuliwa", r"ameteuliwa", r"kujiuzulu",
    r"mishahara", r"nafasi za kazi",
)

# CJK and Arabic have no spaces between words the way \b expects, so these are
# matched as substrings. That is safe here because each string is long enough to
# be unambiguous — the risk \b guards against ("RIF" inside "tariff") does not
# arise for 社長に就任 or تعيين رئيس تنفيذي.
_EMPLOYMENT_TERMS_CJK = (
    # Japanese. 億ドル調達 and 億円調達 added 2026-08-04: a Japanese headline
    # writes the scale word, the currency and the verb as one run
    # ("650億ドル調達"), so 資金調達 -- which is the noun, with 資金 in front --
    # cannot reach it. Both are long enough to be unambiguous, which is the
    # test this whole block is matched on.
    "社長", "就任", "退任", "採用", "求人", "従業員", "人員", "新拠点",
    "資金調達", "億ドル調達", "億円調達", "万ドル調達", "億ドルを調達",
    "シリーズA", "シードラウンド", "賃上げ", "給与",
    # Korean
    "대표이사", "선임", "사임", "채용", "인력", "직원", "사무소",
    "투자 유치", "억 달러 투자", "억달러 투자", "시리즈 A", "시드 투자", "임금",
    # Chinese
    "首席执行官", "总裁", "任命", "辞职", "招聘", "员工", "融资", "轮融资",
    "亿美元融资", "亿美元投资",
    # Arabic
    "الرئيس التنفيذي", "تعيين", "استقالة", "توظيف", "وظائف", "موظف",
    "جولة تمويل", "تمويل", "رواتب",
    # Thai (added 2026-08-01 with the 23-language pass above). It belongs HERE
    # and not in _EMPLOYMENT_TERMS_INTL for the reason this block exists: Thai
    # writes without spaces, so a \b can never fire inside one of these
    # strings and a term placed in the boundary-wrapped tuple would compile
    # into an alternative that matches nothing, silently. Same trap the Danish
    # magnitude-word note above describes, one script further along.
    "พนักงาน", "จ้างงาน", "รับสมัครงาน", "ตำแหน่งงาน", "อัตรากำลัง", "ซีอีโอ", "ประธานเจ้าหน้าที่บริหาร", "กรรมการผู้จัดการ", "แต่งตั้ง", "ลาออก", "เงินเดือน", "ค่าจ้าง", "ระดมทุน", "รอบการลงทุน",
)

_CJK = re.compile("|".join(re.escape(t) for t in _EMPLOYMENT_TERMS_CJK))

_EMPLOYMENT = re.compile(
    r"\b(?:" + "|".join(_EMPLOYMENT_TERMS + _EMPLOYMENT_TERMS_INTL + _FUNDING_TERMS) + r")\b",
    re.I | re.UNICODE,
)

# --- Site events: a company opening, closing or moving a place of work -----
#
# A company opening a site in a city is a geographic hiring signal that arrives
# months before the job adverts do, and a closure is the same signal inverted.
# Neither states a headcount, which is exactly why the first version of this
# block only recognised a handful of English phrases: it was written to rescue
# the euphemism queries ("capability centre"), not to see the event.
#
# Measured on 9,872 items pulled live from the wired publisher feeds in
# data/sources_catalogue.csv (2026-07-29): of 28 hand-labelled corporate site
# events in that sweep, the phrase list below this comment used to catch 7.
# The misses were not exotic. They were ordinary newsroom wording:
#
#   "Siemens opens electrification, automation factory in Egypt"   (noun)
#   "Why is Amazon opening a disaster relief hub near Edmonton?"   (verb form)
#   "AstraZeneca India to set up genomic solutions centre"          (word gap)
#   "Clínica Bíblica ... la apertura de su sede en Liberia"        (language)
#   "Schnucks to shutter sole company-owned warehouse"             (closures)
#
# So the shape is a VERB, up to five intervening words, and a SITE NOUN, in
# each language the feeds are read in. Anchoring to the noun is what keeps
# "opens the door to" and "launches a platform" out; the intervening words are
# what let the money and the adjectives sit where a sub-editor puts them.
#
# "GCC" is deliberately absent as a bare word: it is also the Gulf Cooperation
# Council. So is a bare "data centre": it matched the entire AI-infrastructure
# news cycle (power demand, loans, surveys, zoning votes) and was 23 of 67 hits
# on its own. A data centre somebody is BUILDING still matches through the verb.

_SITE_NOUN = (
    r"offices?|hubs?|campus(?:es)?|plants?|factor(?:y|ies)|facilit(?:y|ies)|"
    r"warehouses?|premises|branch(?:es)?|headquarters|hq|"
    r"laborator(?:y|ies)|cent(?:re|er)s?|sites?|depots?|"
    # Spanish, Portuguese
    r"oficinas?|sedes?|plantas?|f[áa]bricas?|escrit[óo]rios?|filia(?:l|is|les)|"
    r"sucursa(?:l|les)|centros?|almacenes?|"
    # French
    r"bureaux?|usines?|si[èe]ges?|entrep[ôo]ts?|"
    # German, Dutch, Nordic, Czech, Turkish
    r"standorte?n?|werke?s?|niederlassung(?:en)?|zentrale|"
    r"kantoren?|vestiging(?:en)?|fabriek(?:en)?|"
    r"kontore?r?|fabrikke?r?|"
    r"pobo[čc]k\w*|z[áa]vod\w*|"
    # Polish. `fabryk\w*` carries the inflections ("fabrykę", "fabryki");
    # bare "zakład" is NOT here because it is also a bet and a barbershop.
    r"fabryk\w*|siedzib\w*|"
    r"ofis\w*|fabrika\w*|tesis\w*|"
    # Italian
    r"uffici(?:o)?|stabiliment\w+"
)

# Every verb is `\b`-anchored where it is used. Unanchored, the Turkish "taşı"
# matched inside the Indonesian "Investasi" and filed a Batam electricity story
# as a site relocation.
_SITE_OPEN_VERB = (
    r"opens?|opened|opening|launch(?:es|ed|ing)?|"
    r"sets? up|setting up|establish(?:es|ed|ing)?|"
    r"builds?|building|built|inaugurat(?:es|ed|ing)?|"
    r"break(?:s|ing)? ground|invests?|investing|adds?|adding|"
    # Bare "expansion" is the original noise word (MLB, Medicaid, cattle herds,
    # World of Warcraft) and stays out of the employment gate. Anchored to a
    # site noun it is unambiguous, and an expansion IS one of the five site
    # events: "expansion of its Dublin facility" is a place getting bigger.
    r"expands?|expanding|expansions?|"
    r"abre|abrir[áa]?|abren|inaugura\w*|apertura|invierte|"
    r"ampl[íi]a|ampliaci[óo]n|erweitert|erweiterung|udvider|"
    r"roz[šs][íi][řr]\w+|b[üu]y[üu]t\w+|"
    r"ouvre|ouvrir|ouvertures?|implante|"
    r"er[öo]ffnet|er[öo]ffnung|errichtet|"
    r"apre|aprir[àe]|investe|"
    r"opent|åbner|otev[řr]\w+|a[çc][ıi]yor|a[çc]t[ıi]|kuruyor|"
    # Polish (2026-08-03): "Zamknęli fabrykę, wybudują bloki" was invisible.
    r"otwiera|otworzy\w*|uruchamia|wybuduje|zbuduje"
)
_SITE_CLOSE_VERB = (
    r"clos(?:es|ed|ing)|shut(?:s|ting)? down|shutters?|shuttering|"
    r"winds? down|winding down|mothball\w*|"
    r"cierra|cierre|clausura|"
    r"ferme|fermeture|"
    r"schlie[ßs]t|schlie[ßs]ung|"
    r"chiude|chiusura|sluit|lukker|zav[íi]r\w+|kapat\w+|zamyka|zamkn[ęe]\w+"
)
_SITE_MOVE_VERB = (
    r"relocat(?:es|ed|ing|ion)?|moves? (?:its|their)|"
    r"traslada|se muda|d[ée]m[ée]nage|verlagert|verhuist|flytter|st[ěe]huje|ta[şs][ıi]"
)

_SITE_GAP = r"(?:[\w$€£₹¥.,'’\-]+\s+){0,5}?"
# The noun-first "new <something> office" pattern has no verb to anchor it, so
# it gets a shorter reach. At five words it swallowed "New Jersey law will let
# data centers pay for home energy upgrades".
_SITE_NEW_GAP = r"(?:[\w$€£₹¥.,'’\-]+\s+){0,3}?"

_SITE_TERMS = (
    rf"\b(?:{_SITE_OPEN_VERB})\s+{_SITE_GAP}(?:{_SITE_NOUN})\b",
    rf"\b(?:{_SITE_CLOSE_VERB})\s+{_SITE_GAP}(?:{_SITE_NOUN})\b",
    rf"\b(?:{_SITE_MOVE_VERB})\s+{_SITE_GAP}(?:{_SITE_NOUN})\b",
    # Noun-first phrasings, which a headline uses with no verb at all.
    rf"\bnew\s+{_SITE_NEW_GAP}(?:{_SITE_NOUN})\b",
    r"\bnue[vn]\w*\s+(?:[\w'’\-]+\s+){0,2}?(?:oficina|sede|planta|f[áa]brica|centro|sucursal|tienda)\w*",
    r"\bnov[ao]\s+(?:[\w'’\-]+\s+){0,2}?(?:sede|f[áa]brica|escrit[óo]rio|filial|unidade)\w*",
    r"\bnouve(?:au|lle)\s+(?:[\w'’\-]+\s+){0,2}?(?:bureau|site|usine|si[èe]ge|centre)\w*",
    r"\bneue[rns]?\s+(?:[\w'’\-]+\s+){0,2}?(?:standort|werk|niederlassung|b[üu]ro|zentrum)\w*",
    r"\bnuov[ao]\s+(?:[\w'’\-]+\s+){0,2}?(?:sede|stabilimento|ufficio)\w*",
    r"\bnow[aey]\s+(?:[\w'’\-]+\s+){0,2}?(?:fabryk\w*|siedzib\w*|biur[oa])\b",
    # Site types that name the event on their own.
    r"\bcapability cent(?:re|er)s?\b", r"\bcent(?:re|er)s? of excellence\b",
    r"\bdelivery cent(?:re|er)s?\b", r"\bshared services\b",
    r"\btech(?:nology)? cent(?:re|er)s?\b", r"\bengineering cent(?:re|er)s?\b",
    r"\br&d cent(?:re|er)s?\b", r"\binnovation cent(?:re|er)s?\b",
    r"\bdevelopment cent(?:re|er)s?\b",
    r"\bdistribution cent(?:re|er)s?\b", r"\bfulfil?lment cent(?:re|er)s?\b",
    # Bare "GCC" stays out (Gulf Cooperation Council), but a verb in front of
    # it is unambiguous: in Indian business press "opens a new GCC" is a Global
    # Capability Centre, which is exactly the category we want.
    r"(?:new|opens?|open|launch(?:es|ing)?|sets? up|establish(?:es|ing)?)\s+(?:a\s+|its\s+|the\s+)?(?:new\s+)?gcc\b",
    *_hebrew(r"משרד(?:ים)? חדש\w*", r"מרכז פיתוח", r"מפעל חדש", r"פותח\w* משרד"),
)
_SITE = re.compile(r"(?:" + "|".join(_SITE_TERMS) + r")", re.I | re.UNICODE)

# The same words about square metres, loans and munitions rather than about an
# employer putting people somewhere. Checked in a narrow window around the hit,
# so an ordinary sentence elsewhere in the article cannot veto a real match.
_SITE_FALSE_FRIENDS = (
    r"(?:office|retail|industrial|commercial)\s+"
    r"(?:space|supply|market|assets?|rents?|rentals?|vacanc\w+|portfolios?|prices?|stock|demand|leasing)",
    r"shopping cent(?:re|er)", r"town cent(?:re|er)", r"city cent(?:re|er)",
    r"cent(?:re|er) of (?:attention|the (?:pitch|park|storm|debate|controversy|row))",
    r"opens? (?:the )?(?:door|doors|debate|way|possibility|window)",
    r"centro de (?:atenci[óo]n|la pol[ée]mica)",
    # In finance copy a "facility" is a loan far more often than a building.
    r"(?:credit|loan|lending|financing|funding|liquidity|swap|repo|infrastructure)\s+facilit",
    # A launched drone is not a launched site.
    r"\b(?:drones?|missiles?|airstrikes?|warheads?)\b",
)
_SITE_FALSE = re.compile(r"(?:" + "|".join(_SITE_FALSE_FRIENDS) + r")", re.I)


def site_event_term(text: str) -> str | None:
    """The phrase that makes `text` a site event, or None.

    Free, deterministic, and the only thing that decides whether the model is
    ever asked about a story whose headline says nothing about people.
    """
    if not text:
        return None
    hit = _SITE.search(text)
    if not hit:
        return None
    window = text[max(0, hit.start() - 30): hit.end() + 30]
    if _SITE_FALSE.search(window):
        return None
    return hit.group(0)


# --- How we work: the policy half of that pillar ---------------------------
#
# The four English phrases below already lived in _EMPLOYMENT_TERMS. What was
# missing was the other 42 languages the feeds are read in, and the reasoning
# is the German and Danish blocks' reasoning: a phrase that does not match is a
# silent zero, indistinguishable from a quiet news week.
#
# Be honest about the size of this: the same 9,872-item sweep held THREE
# work-policy headlines, and the existing English phrases already caught two of
# them. So this block is insurance against a silent zero, not a fix for the
# empty pillar — the empty pillar is a routing problem and is fixed in
# classify.py, where the model is finally told what how_we_work means.
#
# Bare "teletrabajo" and bare "télétravail" are deliberately absent, for the
# reason bare "investice" is absent from the Czech block: on the live feeds
# they held an energy-consumption feature and a cross-border-commuting column.
# The policy-shaped phrases below do not.
_WORK_POLICY_TERMS = (
    r"return to (?:the )?office", r"back[- ]to[- ]the[- ]office", r"\brto\b",
    r"office attendance", r"days? (?:a|per) week in the office",
    r"in[- ]office (?:requirement|mandate|polic\w+|days?)",
    r"remote[- ]work(?:ing)? polic\w+", r"remote[- ]first",
    r"work(?:ing)? from home", r"work[- ]from[- ]home", r"work from anywhere",
    r"hybrid work(?:ing)?", r"hybrid model", r"hybrid polic\w+",
    r"four[- ]day (?:working )?week", r"4[- ]day week",
    r"compressed hours", r"flexible working", r"hot[- ]desking",
    # Spanish, Portuguese
    r"pol[íi]tica de teletrabajo", r"trabajo h[íi]brido",
    r"vuelta a la oficina", r"regreso a la oficina", r"jornada de cuatro d[íi]as",
    r"pol[íi]tica de teletrabalho", r"trabalho h[íi]brido",
    r"volta ao escrit[óo]rio", r"semana de quatro dias",
    # French
    r"accord de t[ée]l[ée]travail", r"travail hybride", r"retour au bureau",
    r"semaine de quatre jours",
    # German, Dutch, Nordic
    r"mobiles arbeiten", r"hybrides arbeiten", r"homeoffice[- ]regel\w+",
    r"r[üu]ckkehr ins b[üu]ro", r"vier[- ]tage[- ]woche",
    r"thuiswerkbeleid", r"hybride werken", r"vierdaagse werkweek",
    r"hjemmearbejdspolitik", r"distansarbete", r"fyradagarsvecka",
    r"fire[- ]dages arbejdsuge",
    # Czech, Polish, Turkish, Italian
    r"pr[áa]ce z domova", r"hybridn[íi] re[žz]im",
    r"praca zdalna", r"praca hybrydowa",
    r"uzaktan [çc]al[ıi][şs]ma polit\w+", r"hibrit [çc]al[ıi][şs]",
    r"ofise d[öo]n[üu][şs]",
    r"lavoro agile", r"smart working", r"rientro in ufficio", r"settimana corta",
    *_hebrew(
        r"עבודה מרחוק", r"עבודה היברידית", r"חזרה למשרד", r"שבוע עבודה מקוצר",
        r"עבודה מהבית",
    ),
)
_WORK_POLICY = re.compile(
    r"(?:" + "|".join(_WORK_POLICY_TERMS) + r")", re.I | re.UNICODE
)

# Domains where "expansion", "hiring" and "roster" mean something else entirely.
# Cheap to check, and they were most of the noise in the first live run.
_OFF_TOPIC_TERMS = (
    r"nba", r"nfl", r"mlb", r"wnba", r"nhl", r"premier league", r"playoffs?",
    r"franchise", r"roster", r"draft pick", r"touchdown", r"season opener",
    r"medicaid", r"medicare", r"nuclear weapons?", r"ceasefire", r"airstrikes?",
    r"herd", r"cattle", r"livestock", r"acreage",
    r"world of warcraft", r"dlc", r"expansion pack", r"video game",
    # Government and civil-service exam notices. These are instructions to
    # applicants ("registration closes tomorrow", "admit card released"), not
    # intelligence about an employer's plans. A live run stored UPPSC PCS and
    # Indian Navy SSC notices before this existed.
    r"recruitment 20\d\d", r"admit card", r"answer key", r"exam date",
    r"registration closes", r"apply online", r"notification (?:out|released)",
    r"\d+\s+posts?\b", r"uppsc", r"upsc", r"ssc\s+(?:cgl|chsl|gd|mts|officer)",
    r"bharti", r"sarkari", r"vacanc(?:y|ies) notification",
    r"police constable", r"assistant teacher recruitment",
    # Football uses the hiring verb in several of these languages: a live test
    # of the Indonesian edition returned "Barcelona mencapai kesepakatan untuk
    # merekrut bintang Man City" as a hiring signal.
    r"barcelona", r"real madrid", r"man city", r"manchester united",
    r"liverpool", r"chelsea", r"arsenal", r"juventus", r"bayern",
    r"sepak bola", r"liga inggris", r"transfer pemain", r"bintang",
    r"bóng đá", r"câu lạc bộ", r"cầu thủ", r"chuyển nhượng",
    r"futbol", r"transfer sezonu", r"teknik direktör",
    r"fotboll", r"allsvenskan", r"piłkarz", r"transfer\w* piłkar\w*",
    # Naming clubs is whack-a-mole and it lost: "Barca va recruter le crack
    # ghaneen de 20 ans" and "Sparta Prague va recruter le cousin d'Erling
    # Haaland" both reached the live page tagged "Hiring up" because neither
    # club was on the list above (observed 2026-07-28). A signing IS a hire in
    # the literal sense, which is exactly why the employment gate passes it, so
    # the exclusion has to name the SPORT rather than the teams. Each term below
    # is football-specific in its own language and does not collide with
    # corporate hiring vocabulary.
    r"mercato", r"transfer window", r"footballer", r"football club",
    r"on loan from", r"free transfer", r"signs for",
    r"attaquant", r"milieu de terrain", r"gardien de but",
    r"delantero", r"centrocampista", r"portero", r"fichaje",
    r"st[üu]rmer", r"torwart", r"mittelfeldspieler",
    r"midfielder", r"goalkeeper", r"winger", r"centre-back",
    r"sparta prague", r"bar[çc]a\b", r"atl[ée]tico",
    # Public-sector recruitment notices, same reasoning as the Indian ones.
    r"tuyển dụng viên chức", r"tuyển dụng công chức", r"thi tuyển",
    r"penerimaan cpns", r"\bcpns\b", r"seleksi calon pegawai",
    r"memur alımı ilanı", r"kpss", r"bakanlığı personel alım\w*",
    r"bakanlık\w* personel", r"belediyesi personel", r"kamu personel alım\w*",
    r"i̇şkur", r"başvuru ekranı", r"başvuru şartları",
)
_OFF_TOPIC = re.compile(r"\b(?:" + "|".join(_OFF_TOPIC_TERMS) + r")\b", re.I)


# --- Scope boundary: displacement belongs to the sibling --------------------
#
# The page footer promises "Layoff and redundancy data is not collected here;
# see the AI Layoff Tracker", and it was not true: a Spanish-language Verizon
# story ("Verizon despedirá a 3,000 empleados") was live on the page. Two
# products, one boundary — the sibling owns workforce REDUCTION, this one owns
# everything else about the talent market.
#
# The vocabulary lives here rather than in validate.py because BOTH gates need
# exactly the same words. This one runs free, before any model is paid; the one
# in validate.py is the backstop for a story whose headline hid the cut.
#
# 49 Google News editions means the boundary has to hold in more than English.
# A rule that only recognises "layoffs" lets every non-English cut through,
# which is precisely how the Verizon row happened.
_REDUCTION_TERMS = (
    # English
    r"lay[ -]?offs?", r"laid off", r"lays? off", r"laying off",
    r"job cuts?", r"jobs? cut", r"job losses", r"role cuts?", r"staff cuts?",
    r"cut(?:s|ting)?\s+(?:up to\s+)?(?:about\s+)?(?:some\s+)?(?:[\d,.]+\s+)?"
    r"(?:more\s+)?(?:of its\s+)?(?:jobs|roles|positions|posts|staff|workers|"
    r"employees|workforce|headcount)",
    r"redundanc(?:y|ies)", r"made redundant",
    r"reduction in force", r"workforce reduction", r"headcount reduction",
    r"staff reductions?",
    r"reduc\w+\s+(?:its\s+)?(?:workforce|headcount|staff|staffing)",
    r"downsiz\w+", r"retrench\w+",
    r"shed(?:s|ding)?\s+(?:[\d,.]+\s+)?(?:jobs|roles|staff|workers|positions)",
    r"ax(?:e[sd]?|ing)\s+(?:[\d,.]+\s+)?(?:jobs|roles|staff|posts|positions)",
    r"slash(?:es|ing|ed)?\s+(?:[\d,.]+\s+)?(?:jobs|roles|staff|its workforce)",
    r"eliminat\w+\s+(?:[\d,.]+\s+)?(?:jobs|roles|positions)",
    r"mass firings?", r"pink slips?",
    r"terminat\w+\s+(?:[\d,.]+\s+)?(?:employees|workers|staff)",
    # Spanish
    r"desped\w+", r"despido\w*",
    r"recort(?:e|es|ar|a)\w*\s+(?:de\s+)?(?:empleos?|personal|plantilla|puestos)",
    r"reducci\w*n de (?:plantilla|personal|empleo)",
    r"suprim\w+\s+(?:[\d,.]+\s+)?(?:empleos|puestos)",
    # French. Narrowed to the noun and the verb forms: bare "licenci\\w+"
    # also matches the Spanish "licencia" (a permit) and the French
    # "licencié" (a degree holder, or a registered club player).
    r"licenciement\w*", r"licencie(?:r|nt|ra|ront|z)\b",
    r"suppressions? d[e'’]\s*(?:[\d\s.,]+\s*)?(?:postes|emplois)",
    r"supprime\w*\s+(?:[\d,.\s]+)?(?:postes|emplois)",
    r"plan social", r"plan de sauvegarde de l[e'’]emploi",
    r"r\w*duction d[e'’]effectifs",
    # German
    r"stellenabbau", r"arbeitsplatzabbau", r"personalabbau",
    r"stellenstreichung\w*", r"massenentlassung\w*", r"entlassung\w*",
    r"entl\w*sst", r"entlassen", r"streicht\s+[\d.]+\s+stellen",
    r"baut\s+[\d.]+\s+stellen ab",
    # Portuguese. Bare "demissão" is NOT here: in Portuguese business copy it is
    # as often one executive resigning as it is a workforce cut, and this
    # boundary must not swallow the leadership pillar.
    r"demiss(?:\w+)? em massa", r"demit(?:e|iu|ir)\w*\s+[\d.,]+",
    r"cort(?:a|am|ar|ou|es?)?\s+(?:de\s+)?(?:[\d.,]+\s+)?"
    r"(?:vagas|empregos?|pessoal|postos|funcion\w+)",
    r"redu\w+\s+(?:o\s+)?quadro de (?:pessoal|funcion\w+)",
    r"dispensa de funcion\w+", r"enxugamento",
    # Italian
    r"licenziament\w+", r"licenzia(?:re|no|ti|ta|to)\b", r"esuberi",
    r"tagli\w*\s+(?:ai posti|di posti|del personale|occupazionali|dipendenti)",
    r"riduzione (?:del personale|dell[e'’]organico)",
    # Dutch, Polish, Swedish, Turkish — cheap to add, same failure if absent.
    r"ontslag\w*", r"banenverlies", r"schrapt\s+[\d.]+\s+banen",
    # "zwolnieni\w+" reaches the noun ("zwolnienia grupowe") but not the
    # participle a Polish headline actually uses ("200 pracowników
    # zwolnionych"), and bare "zwolni\w+" is off the table: the same verb
    # slows down, releases and exempts ("zwolnił tempo", "zwolnienie
    # lekarskie"). So the participle is anchored to the people, both ways
    # round (2026-08-03, found when the Polish site vocabulary would have
    # passed a factory-closure-with-job-losses story to the gate).
    r"zwolnieni\w+", r"redukcj\w+ etat\w+",
    r"zwolni\w+\s+(?:\S+\s+){0,2}?(?:pracownik\w*|osób|etat\w*)",
    r"pracownik\w*\s+zwolnion\w+", r"zwalnia\w*\s+(?:\S+\s+){0,2}?pracownik\w*",
    r"varsl\w+", r"neddragning\w*",
    r"i̇?şten çıkar\w*", r"toplu i̇?şten",
    # Czech. The live E15 feed carried "Porsche ... zruší dalších pět tisíc
    # míst" while this was missing, which is a sibling story reaching a page
    # that promises it publishes none.
    r"propouš\w+", r"propust\w+", r"propušt\w+",
    r"(?:z)?ruší\s+(?:\S+\s+){0,3}?(?:pracovních\s+)?míst\w*",
    r"snižování stavu", r"snižuje stav\w*",
    # Danish
    r"fyring\w*", r"massefyring\w*", r"afskedig\w*", r"nedskæring\w*",
    r"personalereduktion\w*",
    r"fyrer\s+(?:[\d.,]+\s+)?(?:medarbejdere|ansatte|folk|procent)",
    r"nedlægger\s+(?:[\d.,]+\s+)?(?:stillinger|arbejdsplads\w*|job)",
    r"skærer\s+(?:[\d.,]+\s+)?(?:stillinger|arbejdsplads\w*|job)",
    # Hebrew. Bare "פיטר" is deliberately absent even though it is the verb:
    # it is also how "Peter" is spelled, so it would hand every Peter Thiel
    # funding story to the sibling. Under-matching is the safe direction for
    # THIS gate specifically — the inflected forms below are unambiguous.
    *_hebrew(
        r"פיטור(?:ים|ין)", r"פיטורי\w*", r"פיטר(?:ה|ו)", r"פיטר את",
        r"מפטר(?:ת|ים|ות)?", r"לפטר", r"יפטר(?:ו)?",
        r"צמצומ(?:ים)? (?:בכוח אדם|במצבת|בכוח האדם)",
        r"צמצום כוח אדם", r"קיצוצים במשרות",
    ),
)
_REDUCTION = re.compile(r"\b(?:" + "|".join(_REDUCTION_TERMS) + r")", re.I | re.UNICODE)

# "RIF" is only a reduction in force when it is written in capitals. Lowercase
# "rif" is a syllable in several of the languages above, and the sibling's own
# filter went inert for a day because "RIF" matched inside "tariff".
_RIF = re.compile(r"\bRIFs?\b")

# The events this product DOES own. A story is not a layoff story merely
# because it mentions a cut: "Klarna hires 1,000 after AI-driven job cuts" is a
# hiring story, and rejecting it would hand the sibling a story about growth.
_IN_SCOPE_SUBJECT_TERMS = (
    # Hiring and site growth
    r"hiring", r"hires?", r"to hire", r"hired", r"recruit\w*",
    r"creat\w+\s+(?:[\d,.]+\s+)?(?:new\s+)?(?:jobs|roles|posts|positions)",
    r"new jobs", r"add(?:s|ing)?\s+(?:[\d,.]+\s+)?(?:jobs|roles|staff)",
    r"opens? (?:a |its |new )", r"new (?:office|hub|campus|plant|facility|site)",
    r"capability cent(?:re|er)",
    # A site OPENING and a work-policy change are subjects this product owns,
    # so a story that leads with one keeps its story even when it goes on to
    # mention a past cut. Site CLOSURE verbs are deliberately NOT here: a
    # closure that states job losses is the sibling's record, and listing
    # "schließt Standort" as an in-scope subject would have kept "Enpal
    # schließt Standort in Hamburg - rund 85 Mitarbeiter entlassen" on a page
    # that promises it publishes no layoffs (live on the German feeds,
    # 2026-07-29). Closures still reach us; they just do not win this race.
    r"opens? (?:a |its |the |new )?(?:office|hub|campus|site|plant|factory|cent(?:re|er))",
    r"return to (?:the )?office", r"hybrid work(?:ing)?", r"four[- ]day week",
    # Leadership
    r"appoint\w*", r"names? (?:a |its |new )?(?:ceo|chief|cfo|cto|coo|president)",
    r"steps? down", r"resign\w*", r"succeeds?", r"promot\w+",
    r"new (?:ceo|chief executive|cfo|cto)",
    # Money
    r"rais(?:e[sd]?|ing)", r"funding round", r"series [a-k]\b",
    r"secures? (?:\$|€|£|₹)?[\d.,]+", r"invest(?:s|ment|ing)\b",
    # Pay
    r"pay ris\w+", r"pay increase", r"salar\w+ (?:increase|rise)",
    r"wage (?:increase|rise)", r"bonus\w*", r"minimum wage",
    # Non-English subjects, same three intents
    r"contrata\w*", r"embauch\w+", r"einstell\w+", r"assunzion\w+",
    r"nomm\w+", r"ernennt", r"nombra\w*", r"nomea\w*", r"nomina\w*",
    r"lev\w*e de fonds", r"finanzierungsrunde", r"ronda de financiaci\w*n",
    r"rodada de investimento", r"round di finanziamento",
    # Czech, Danish, Hebrew — the same three intents. Without these the
    # ordering heuristic below has no subject to find in those languages, so
    # "Acme raised $40m after last year's redundancies" reads as a reduction
    # story and goes to the sibling.
    r"nabír\w+", r"nábor\w*", r"jmenova\w+", r"jmenuj\w+",
    r"získal\w*\s+(?:\S+\s+){0,4}?(?:investic\w+|milion\w+|miliard\w+|korun\w*)",
    r"kolo financování", r"investiční kolo",
    r"ansæt\w+", r"rekrutter\w+", r"udnævn\w+", r"tiltræder",
    r"finansieringsrunde", r"kapitalrunde", r"kapitalindsprøjtning",
    r"henter\s+(?:[\d.,]+|million\w*|milliard\w*|kapital|investering\w*)",
    r"rejse[rt]?\s+(?:en\s+|ny\s+)?(?:runde|kapital|finansiering|"
    r"[\d.,]+\s*(?:mio|mia|million\w*|milliard\w*))",
    *_hebrew(
        r"גיוס(?:ים)?", r"גייס(?:ה|ו)?", r"מגייס(?:ת|ים|ות)?", r"לגייס",
        r"השקע(?:ה|ות)", r"מינו(?:י|יים)", r"מונ(?:ה|תה)", r"ימונה",
        r"מגייס(?:ת|ים)? עובדים", r"קליט(?:ה|ת) עובדים",
    ),
)
_IN_SCOPE_SUBJECT = re.compile(
    r"\b(?:" + "|".join(_IN_SCOPE_SUBJECT_TERMS) + r")", re.I | re.UNICODE
)


def workforce_reduction_term(text: str) -> str | None:
    """The term that makes `text` a story the SIBLING owns, or None.

    The honest heuristic: a headline leads with its subject. So a reduction
    term counts only when no in-scope subject (hiring, funding, an
    appointment, a pay action, a location decision) appears EARLIER in the
    text. That keeps "Klarna hires 1,000 after AI job cuts" here and sends
    "Verizon despedirá a 3,000 empleados" to the sibling, without needing to
    parse a sentence.

    It is a heuristic and it has a known blind spot: "Amid layoffs, Acme hires
    500" reads as a reduction story. That is the direction to be wrong in — a
    layoff row on a page promising it collects none is a broken promise, while
    a missed hiring row is one row.
    """
    if not text:
        return None

    hit = _REDUCTION.search(text) or _RIF.search(text)
    if not hit:
        return None

    subject = _IN_SCOPE_SUBJECT.search(text)
    if subject and subject.start() < hit.start():
        return None
    return hit.group(0)


# --- The same boundary, read off a DOCUMENT instead of a headline ----------
#
# `workforce_reduction_term` above is shaped for a headline: it assumes the
# text leads with its subject, and lets an in-scope subject appearing earlier
# win the race. That assumption is exactly right for a headline and exactly
# wrong for a filing.
#
# It is wrong twice over on `sec_edgar`, which is where it failed in
# production. That collector stamps ONE synthetic headline —
# "<Company> 8-K filing (Item 5.02): officer or director change" — onto every
# document it fetches, so the headline arm of the guard is reading a string
# the collector wrote rather than anything the company said, and it can never
# contain a reduction term. The reduction language lives in `raw_text`, which
# no arm of the guard read. Four filings reached the live page that way:
# Atlassian (~10% of its workforce), Groupon (up to 400 positions), IO Biotech
# and Lyra Therapeutics. And running the headline rule over the BODY instead
# would not have saved it: every Item 5.02 filing opens with "appointed" or
# "resigned", so the subject-leads race would suppress a reduction announced
# three paragraphs later, every single time.
#
# So a body needs its own rule, and the question it has to answer is not
# "does this document mention a cut" but "does this document ANNOUNCE one".
# Getting that distinction right is the whole job: over-reject and we hand the
# sibling every 8-K that mentions last year's restructuring, or every officer
# departure whose separation terms use the word "termination".
#
# A filing that announces a reduction says so the way the SEC makes it say so:
#
#   * **Item 2.05** — "Costs Associated with Exit or Disposal Activities" —
#     is the item code that exists for precisely this event. On its own it is
#     decisive, because a registrant does not file one for a cut it is only
#     mentioning.
#   * It names a **plan**: approved a restructuring plan, a reduction in
#     force, a workforce reduction plan.
#   * It states a **scale**: a percentage of the workforce, or a count of
#     positions, roles or employees.
#
# A filing that merely mentions a cut carries the term and none of that
# corroboration NEAR the term, which is what the window below tests.
#
# Note what is deliberately NOT a corroborator: "severance", "termination
# benefits" and "one-time charge". Those are the standard furniture of an
# Item 5.02 officer departure — nearly every CEO exit filing contains all
# three — so admitting them would turn this guard into a rule that rejects
# leadership changes, which is the pillar this product is largest in.

#: Item 2.05 is the SEC's own item code for an exit or disposal plan, which is
#: how a workforce reduction is reported. A filing that declares it has
#: announced a reduction by definition, so this needs no second opinion.
_FILING_EXIT_ITEM = re.compile(
    r"item\s*2\.05\b"
    r"|costs?\s+associated\s+with\s+exit\s+or\s+disposal",
    re.I,
)

#: The PLAN half of "announces". Filing prose is formal and hedged, so this is
#: the language a registrant uses when it has decided rather than mused.
_FILING_PLAN_TERMS = (
    r"restructuring plan", r"restructuring and workforce reduction",
    r"reduction[- ]in[- ]force", r"workforce reduction plan",
    r"reduction plan", r"cost reduction plan", r"plan to reduce",
    # "approved a" and not "approved the": the definite article is what an
    # equity incentive plan gets ("approved the 2026 Equity Incentive Plan"),
    # and the whole point is not to reject the pillar this product is
    # largest in.
    r"approved a (?:\w+\s+){0,3}?plan",
    r"reduc\w+\s+(?:of\s+)?(?:its|our|the company'?s?|the)\s+"
    r"(?:global\s+|total\s+|worldwide\s+)?(?:workforce|headcount)",
)
_FILING_PLAN = re.compile(r"(?:" + "|".join(_FILING_PLAN_TERMS) + r")", re.I)

#: The SCALE half. A registrant announcing a reduction says how big it is;
#: one mentioning somebody else's does not. "approximately 10% of the
#: Company's workforce" (Atlassian), "up to 400 positions" (Groupon),
#: "substantially all remaining employees" (Lyra Therapeutics).
_FILING_SCALE_TERMS = (
    r"[\d.]{1,5}\s?%\s+of\s+(?:the\s+|its\s+|our\s+)?(?:company'?s?\s+)?"
    r"(?:global\s+|total\s+|worldwide\s+)?(?:workforce|employees|headcount|"
    r"work\s?force)",
    r"(?:approximately|about|up to|around|roughly)\s+[\d,]+\s+"
    r"(?:positions|roles|employees|jobs|people)",
    r"reduction of (?:up to\s+)?(?:approximately\s+)?[\d,]+\s+"
    r"(?:positions|roles|employees|jobs)",
    r"substantially all (?:of )?(?:its |the |our )?(?:remaining )?employees",
)
_FILING_SCALE = re.compile(r"(?:" + "|".join(_FILING_SCALE_TERMS) + r")", re.I)

#: How far apart two of those signals may sit and still be describing the SAME
#: event. Roughly a paragraph of filing prose. Wider, and a departing officer's
#: severance paragraph starts corroborating a sentence about something else;
#: narrower, and "the Company approved a restructuring plan. ... The plan
#: includes a reduction of approximately 400 positions" splits in two.
PLAN_WINDOW_CHARS = 400


def filing_reduction_plan(text: str) -> str | None:
    """The phrase that makes a DOCUMENT a workforce-reduction announcement.

    Returns None for a document that merely mentions a cut. See the long note
    above for why this cannot be `workforce_reduction_term` with a different
    argument.

    The rule is **Item 2.05, or any two of {a reduction term, a plan, a
    scale} within a paragraph of each other.** Two, because filing prose says
    the same thing several ways and any one of them alone is ambiguous:

      * a reduction term alone is the passing mention this must not reject
        ("she led finance through the 2024 layoffs at her former employer");
      * a plan alone is most often a compensation plan;
      * a scale alone is a share count or a shareholder percentage.

    Together they are a registrant saying it has decided to cut, and how many.
    That symmetry also covers the phrasings the reduction vocabulary does not
    reach — "elimination of certain roles", "cut approximately 400 positions" —
    which matters because that vocabulary was written for headlines.

    The reduction half deliberately reuses `_REDUCTION`. Fourteen languages of
    it are hard-won (`פיטר` is excluded because it is also how "Peter" is
    spelled; lowercase "rif" matched inside "tariff"), and a second copy would
    drift away from the first.
    """
    if not text:
        return None

    item = _FILING_EXIT_ITEM.search(text)
    if item:
        return item.group(0)

    hits: list[tuple[int, str, str]] = []
    for kind, pattern in (("cut", _REDUCTION), ("cut", _RIF),
                          ("plan", _FILING_PLAN), ("scale", _FILING_SCALE)):
        for match in pattern.finditer(text):
            hits.append((match.start(), kind, match.group(0)))
    if len(hits) < 2:
        return None
    hits.sort()

    for start, kind, phrase in hits:
        near = [h for h in hits
                if h[1] != kind and abs(h[0] - start) <= PLAN_WINDOW_CHARS]
        if not near:
            continue
        # Report the reduction term when one is involved: it is the phrase a
        # reader needs to see to agree with the verdict.
        if kind == "cut":
            return phrase
        cuts = [h[2] for h in near if h[1] == "cut"]
        return cuts[0] if cuts else phrase
    return None


# --- Geography gate --------------------------------------------------------
#
# We claim eight markets. A signal in a place we do not cover gets rejected by
# validate.py anyway ("no geography"), so classifying it first is pure waste —
# the first successful live run paid for exactly that on Uzbekistan, Somalia,
# Ohio and Anglesey. Checking here costs nothing.
#
# CORRECTION 2026-07-29: this line used to read "grows automatically as
# source_registry.MARKETS grows: nothing to hand-edit", and that was never true.
# The function below reads `vocab.COUNTRY_NAMES`, `vocab._CITY_ALIASES`,
# `vocab._COUNTRY_ALIASES` and the hardcoded short-code list further down. It has
# never referenced MARKETS. The claim mattered, because it is the reason somebody
# would believe that adding a market widens this gate — it does not, and a market
# added on the strength of that belief would be swept by an edition whose
# candidates this gate could still drop. What actually widens it is the country
# and city VOCABULARY, which carries the whole world (see the 2026-07-28 lesson
# about tit_country_names holding 52 of ~200 codes).

def _geography_terms() -> tuple[re.Pattern, re.Pattern]:
    from . import vocab

    long_terms, short_codes = set(), set()

    def add(term: str) -> None:
        (long_terms if len(term) >= 4 else short_codes).add(re.escape(term))

    for alias, (city, _region, _iso2) in vocab._CITY_ALIASES.items():
        add(alias)
        add(city)
    for name in vocab.COUNTRY_NAMES.values():
        add(name)
    for alias in vocab._COUNTRY_ALIASES:
        add(alias)
    # The two-letter codes of the editions we actually query. Deliberately NOT
    # all 198: uppercase "IT", "IN", "AT" and "NO" are ordinary words in a
    # headline, and this helper feeds a geography hint, not a gate, so a false
    # positive costs nothing while a false "IT department" would be noise.
    for code in ("US", "GB", "CA", "AU", "IE", "IN", "SG", "NZ", "ZA", "PH",
                 "NG", "DE", "FR", "NL", "ES", "MX", "AR", "CL", "CO", "BR",
                 "PT", "AT", "CH", "BE"):
        short_codes.add(re.escape(code))

    # Adjectival forms carry the geography just as well: "across German sites".
    long_terms.update({
        "irish", "german", "french", "dutch", "belgian", "british", "english",
        "scottish", "welsh", "spanish", "portuguese", "italian", "swedish",
        "danish", "norwegian", "finnish", "swiss", "polish", "czech",
        "romanian", "indian", "japanese", "australian", "american",
    })

    return (
        re.compile(r"\b(?:" + "|".join(sorted(long_terms)) + r")\b", re.I),
        # Short codes match case-sensitively on purpose: a lowercase "us" is
        # the pronoun, and "\bus\b" would let "join us" through as the USA.
        re.compile(r"\b(?:" + "|".join(sorted(c.upper() for c in short_codes)) + r")\b"),
    )


_GEO_LONG, _GEO_SHORT = _geography_terms()


def has_covered_geography(text: str) -> bool:
    return bool(_GEO_LONG.search(text) or _GEO_SHORT.search(text))


def passes(text: str) -> tuple[bool, str]:
    """Return (keep, reason). Reason is empty when kept."""
    if not text or not text.strip():
        return False, "empty text"

    # Word-boundary matching, not substring: the sibling's equivalent loop went
    # inert for a day because "RIF" matched inside "tariff".
    if _OFF_TOPIC.search(text):
        hit = _OFF_TOPIC.search(text).group(0)
        return False, f"off-topic domain ({hit})"

    if not (_EMPLOYMENT.search(text)
            or site_event_term(text)
            or _WORK_POLICY.search(text)
            or _CJK.search(text)):
        return False, "no employment, site or work-policy term"

    # Checked AFTER the employment gate on purpose: a layoff story always
    # passes that gate (it is full of "jobs" and "employees"), so this is the
    # cheapest possible place to hand it to the sibling — before a model is
    # paid to read a story we are not allowed to publish.
    cut = workforce_reduction_term(text)
    if cut:
        return False, f"workforce reduction belongs to the layoff tracker ({cut})"

    # NOTE: geography is deliberately NOT a gate here, though the helper above
    # exists and is tested. Gating on it looked like an easy saving — several
    # items were classified and then rejected for uncovered geography — but it
    # drops "Revolut CEO steps down" (no place in the headline at all), "Intel
    # opens new facility in Leixlip" and "BMS opens Mumbai capability centre".
    # A headline often carries no place while the body does, and the model can
    # infer it from the employer. Recall is the harder problem; validate.py
    # rejects on geography later with full context, for a fraction of a cent.
    return True, ""
