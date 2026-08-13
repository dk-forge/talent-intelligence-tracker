"""Deterministic chief-executive appointments in languages other than English.

`cheap_extract` closes candidates for free by parsing what the headline states,
and its rule 4 says English only, deliberately: its name-span validation leans
on English capitalisation and English generic-word lists. That rule was correct
for the code it guarded. It was also the reason `_parse_leadership` — shipped
2026-07-29, live for the whole priced window — closed **zero** rows.

The measurement is in `docs/MEASURE-throughput-levers.md` and was reproduced
before this module was written (`analysis/throughput/leadership_decline.py`):
of the 922 google_news `leadership_change` rows stored in the priced window,
**883 (95.8%) died at `_LEADERSHIP_SHAPE.match(headline)`** — the English
appoints/names/taps verb list — and the languages behind them are French 140,
Spanish 118, Swedish 102, Portuguese 85, Italian 71, German 68, Korean 61,
Hebrew 54, Turkish 54, Dutch 36. Leadership is 46% of paid news volume. Nothing
was wrong with the parser; it was never shown a sentence it could read.

So this module is the same parser with eight more grammars, and it keeps every
one of `cheap_extract`'s rules rather than earning an exemption:

1. PRECISION OVER RECALL. Everything ambiguous declines. A decline costs one
   paid read; a wrong $0 close puts a wrong row on a public page.
2. Only what the text LITERALLY STATES. No inference, no transliteration, no
   translation of a name.
3. COMPLETE or nothing.
4. **The scripts are Latin, deliberately** — this rule is rule 4 moved rather
   than removed. Person and employer names are captured as written, so a
   grammar is only safe where the name span is Latin script and the language
   marks a name boundary the way these patterns assume. Korean, Hebrew,
   Japanese and Vietnamese headlines still take the paid path: they are 176 of
   the 922 and getting them wrong is worse than paying for them.

Only the CHIEF EXECUTIVE seat is parsed. `directeur général`, `amministratore
delegato`, `consejero delegado`, `vd`, `Vorstandsvorsitzender` and the literal
`CEO` all mean one seat and one title label. Divisional and second-tier titles
(`directeur marketing`, `Geschäftsführer` of a subsidiary, `genel müdür
yardımcısı`) shade into descriptions exactly as the English list's "head of"
and "VP" do, and they stay out for the same reason.

A DEPARTURE IS NOT AN APPOINTMENT. `tritt zurück`, `quitte ses fonctions`,
`dimite`, `avgår`, `istifa`, `사임` all describe the same event from the other
end, and the record this module builds says somebody arrived. Every one of them
declines, and the paid path reads the story.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from . import prefilter, vocab

# The canonical title every grammar here resolves to. One seat, one label, so
# two languages describing the same appointment produce the same row and the
# duplicate pre-check in `dedupe.leadership_event_duplicate` can see it.
CEO_TITLE = "Chief Executive Officer"

# Stored on the row's `notes`, alongside cheap_extract's own marker, so a
# reader of the database can tell which parser produced a $0 row.
EVIDENCE_NOTE = (
    "deterministic extraction: the appointment is stated verbatim in the "
    "headline; no model read this item (pipeline/leadership_intl.py)"
)

STATS = {"attempted": 0, "closed": 0, "declined": 0}

# Languages this module will read. A `lang` outside this set declines before
# any pattern runs, which is what keeps rule 4 checkable rather than implied.
LANGUAGES = frozenset({"fr", "es", "pt", "it", "de", "sv", "nl", "tr"})

# --- Titles ------------------------------------------------------------------
#
# Written as one alternation per language rather than one global one: a global
# list would let a French pattern accept a Swedish title and report a match
# nobody wrote. "CEO" is in every list because every one of these languages
# prints it untranslated, and it is by volume the commonest form in Spanish,
# Portuguese, Turkish and German headlines.
_CEO = r"CEO"
_TITLES = {
    "fr": rf"{_CEO}|PDG|directeur\s+g[ée]n[ée]ral|directrice\s+g[ée]n[ée]rale",
    "es": rf"{_CEO}|consejer[oa]\s+delegad[oa]",
    "pt": rf"{_CEO}|diretor[- ]presidente|presidente[- ]executiv[oa]",
    "it": rf"{_CEO}|amministrat(?:ore|rice)\s+delegat[oa]",
    "de": rf"{_CEO}|Vorstandsvorsitzende[rn]?|Vorstandschefin?",
    "sv": rf"{_CEO}|vd|verkst[äa]llande\s+direkt[öo]r",
    "nl": rf"{_CEO}|bestuursvoorzitter|algemeen\s+directeur",
    "tr": rf"{_CEO}",
}

# --- The knock-outs ----------------------------------------------------------
#
# Every one of these describes something the record cannot carry. They are
# matched against the WHOLE text, not the headline, exactly as
# `_parse_leadership` matches its own.

# A departure. The commonest single shape in this corpus after the appointment
# itself, and the one a naive "COMPANY + CEO + NAME" match would silently
# invert: "Ecotel-CEO Markus Hendrich tritt zurück" is not an appointment.
_DEPARTURE = re.compile(
    r"tritt\s+zur[üu]ck|r[üu]cktritt|verl[äa]sst|"
    r"quitte|d[ée]mission|se\s+retire|c[èe]de\s+sa\s+place|"
    r"dimite|renuncia|abandona|deja\s+(?:el\s+)?cargo|cesa\s+como|"
    r"demiss[ãa]o|renuncia|deixa\s+(?:o\s+)?cargo|sai\s+da|"
    r"si\s+dimette|lascia|addio|"
    r"avg[åa]r|l[äa]mnar|slutar|"
    r"vertrekt|stapt\s+op|"
    r"istifa|ayr[ıi]l|veda\s+etti|g[öo]revi\s+b[ıi]rak",
    re.I,
)

# Month names in the eight languages, for the stated-start knock-out below.
# Written out rather than derived from a locale: a runner's locale is not a
# fact about the news, and this list is checkable by eye.
_MONTHS = (
    "januar", "janvier", "enero", "janeiro", "gennaio", "januari", "ocak",
    "februar", "février", "febrero", "fevereiro", "febbraio", "februari", "şubat",
    "märz", "mars", "marzo", "março", "maart", "mart",
    "april", "avril", "abril", "aprile", "nisan",
    "mai", "mayo", "maio", "maggio", "mei", "mayıs",
    "juni", "juin", "junio", "junho", "giugno", "haziran",
    "juli", "juillet", "julio", "julho", "luglio", "temmuz",
    "august", "août", "agosto", "augusti", "augustus", "ağustos",
    "september", "septembre", "septiembre", "setembro", "settembre", "eylül",
    "oktober", "octobre", "octubre", "outubro", "ottobre", "ekim",
    "november", "novembre", "noviembre", "novembro", "kasım",
    "dezember", "décembre", "diciembre", "dezembro", "dicembre", "december", "aralık",
)

# An arrangement the row has no column for: a stated start date, an acting or
# interim appointment, a succession not yet effective. cheap_extract declines
# the English forms of all three (_LEADERSHIP_UNCARRIED) and so does this.
_UNCARRIED = re.compile(
    r"\bint[ée]rim\b|\binterim\b|\bad\s+interim\b|par\s+int[ée]rim|"
    r"interin[oa]\b|provis[óo]ri[oa]|ad\s+interim|"
    r"tillf[öo]rordnad|kommissarisch|waarnemend|"
    r"ge[çc]ici|vekil|"
    r"[àa]\s+compter\s+du|avec\s+effet|"
    r"a\s+partir\s+del?\s+\d|com\s+efeito|a\s+decorrere|"
    r"mit\s+wirkung|fr[åa]n\s+den\s+\d|per\s+\d{1,2}\s|"
    r"itibar[ıi]yla|itibaren|"
    # A stated start month or year. "Rudolf Bruder nommé directeur général de
    # Swica dès novembre" and "Marc Schuler wird CEO bei Blaser Swisslube ab
    # März 2026" both parsed with the date welded onto the employer's name
    # before this line existed; the record has no column for a start date, so
    # rule 3 says decline and let the model read the nuance.
    rf"\b(?:d[èe]s|ab|vanaf|fr[åa]n|dal|desde|a\s+partir\s+de|per)\s+"
    rf"(?:{'|'.join(_MONTHS)}|\d)",
    re.I,
)

# Two events in one headline. A round, a deal or a cut beside an appointment is
# a story, and a story is a read.
_DEAL = re.compile(
    r"\b(?:acquisi|acquist|rachat|fusion|fus[ãa]o|fusione|[üu]bernahme|"
    r"uppk[öo]p|f[öo]rv[äa]rv|overname|sat[ıi]n\s+al|birle[şs]me|"
    r"merger|takeover|ipo|b[öo]rsg[åa]ng)",
    re.I,
)
_AMOUNT = re.compile(
    r"(?:US\$|USD|EUR|SEK|CHF|TRY|BRL|[$€£₺R]\s?\$?)\s?\d"
    r"|\d[\d.,]*\s?(?:milj[oa]|milli[oa]n|mil(?:h[õo]es)?|miliard|milyar|"
    r"mrd|md€|bn|mn)\b",
    re.I,
)

# --- Name spans --------------------------------------------------------------
#
# A person is two or three Latin-script tokens, each opening with an uppercase
# letter. A particle ("de", "van", "von", "del", "Ben") may sit between them
# lowercase; anything else lowercase ends the span, which is what stops a
# grammar from swallowing the rest of the sentence.
#
# `i`, `e`, `y` and `och` are NOT here. They are prepositions and conjunctions,
# not name particles, and admitting them let "Styrelsen i White Arkitekter"
# read as one employer name.
_PARTICLES = frozenset(
    "de del della di da das dos du van von der den ter ten le la el bin ben "
    "af av mac mc o’ d’ l’".split()
)

# Words that mark a span as a description rather than a bare name. Per
# language, because "nuovo" is not "new" to a regex. `cheap_extract`'s English
# list is applied on top of these, never instead of them.
_NOT_A_NAME = frozenset("""
    nouveau nouvelle nommé nommée ancien ancienne futur future groupe société
    nuevo nueva nombrado nombrada antiguo exdirector grupo empresa compañia
    novo nova nomeado nomeada antigo grupo empresa presidente
    nuovo nuova nominato nominata gruppo societa azienda ex cambio vertice
    neuer neue neuen ehemaliger konzern gruppe unternehmen chef chefin
    ny nya nye tidigare koncern bolaget bolag foretag styrelsen styrelse
    nieuwe nieuw voormalig bedrijf concern
    yeni eski grup sirket holding genel mudur
    interim acting former new next chief officer president director general
    ceo cfo coo cto md vd pdg
""".split())

# Descriptor HEADS that make a capitalised span a job description rather than a
# person. This is the cross-language reading of cheap_extract's
# `_PERSON_ROLE_WORDS`, and it exists because German capitalises every noun, so
# "Swisscom Banking-Spezialist wird CEO von Inacta" presents two capitalised
# tokens and no name at all. Checked against every hyphen-separated PART of a
# token, which is what catches "Banking-Spezialist" without rejecting
# "Jean-Baptiste".
_DESCRIPTOR_PARTS = frozenset("""
    spezialist experte expert berater consultant consultante konsult
    manager managerin banker bankier direktor direktorin direktör direktor
    gründer grunder fondateur fondatrice fundador fundadora fondatore
    vorstand vorstandschef aufsichtsrat verwaltungsrat geschäftsführer
    veteran profi chefe jefe capo baas patron patrone
    ingenieur ingénieur ingegnere analyst analista analiste
    professor prof docteur doktor dottore dr mr mrs ms sr sra sig
    styrelseordförande ordförande voorzitter président presidente presidenta
    sjef sef seff yönetici mudur müdür baskan başkan
""".split())


def _fold(token: str) -> str:
    """Accents off, lowercase. `_NOT_A_NAME` is written unaccented so one entry
    covers `nommé`, `nommée` and `nomme` without three spellings to keep in
    step."""
    stripped = unicodedata.normalize("NFKD", token or "")
    return "".join(c for c in stripped if not unicodedata.combining(c)).lower()


_UPPER_START = re.compile(r"^[A-ZÀ-ÞŁŠŽĐÆØÅ]", re.UNICODE)
_LATIN_ONLY = re.compile(r"^[\w \-.'’&/À-ÿŁłŠšŽžĐđÆæØøÅå]+$", re.UNICODE)
_HAS_NON_LATIN = re.compile(r"[^\W\dA-Za-zÀ-ÿŁłŠšŽžĐđÆæØøÅå]", re.UNICODE)


def _clean_span(span: str) -> str:
    return (span or "").strip().strip(" ,:;–—-·|\"'“”‘’()")


def valid_person(span: str) -> str | None:
    """The span accepted as ONE person's bare name, or None.

    Two or three tokens (a middle particle does not count), every token opening
    uppercase, nothing that reads as a role or a qualifier in any of the eight
    languages, and no connective — two people are two records and which is
    which is a read, which is `cheap_extract._valid_person`'s rule and this is
    the same rule spelled for these alphabets.
    """
    name = _clean_span(span)
    if not name or _HAS_NON_LATIN.search(name) or not _LATIN_ONLY.match(name):
        return None
    if re.search(r"[,:;&/|]|\d|\band\b|\bet\b|\by\b|\be\b|\bund\b|\boch\b|\bve\b",
                 name, re.I):
        return None
    tokens = name.split()
    if not 2 <= len(tokens) <= 4:
        return None
    real = [t for t in tokens if _fold(t) not in _PARTICLES]
    if not 2 <= len(real) <= 3:
        return None
    for token in real:
        if not _UPPER_START.match(token):
            return None
        folded = _fold(token).strip(".")
        if folded in _NOT_A_NAME:
            return None
        # Every hyphen-separated part, so a German compound descriptor cannot
        # hide behind its first half.
        for part in re.split(r"[-/’']", folded):
            if part in _DESCRIPTOR_PARTS or part in _NOT_A_NAME:
                return None
        if len(token) < 2:
            return None
    return name


def valid_company(span: str) -> str | None:
    """The span accepted as an employer's name, or None.

    Deliberately looser than `valid_person` on token count (employers are
    "Crédit Agricole Normandie" and "von Bodelschwinghsche Stiftungen Bethel")
    and stricter on what may open it: a lowercase opening token means the
    grammar captured an article or a preposition and the match is wrong.
    """
    name = _clean_span(span)
    if not name or not _LATIN_ONLY.match(name):
        return None
    if re.search(r"[,:;|]|\band\b", name, re.I):
        return None
    # A sentence boundary inside the span means the grammar ran past the end of
    # the employer's name: "Igor de Biasio è il nuovo amministratore delegato
    # di Enav. Tutte le foto" stored the photo caption as part of Enav.
    if ". " in name:
        return None
    tokens = name.split()
    if not 1 <= len(tokens) <= 5:
        return None
    if not _UPPER_START.match(tokens[0]):
        return None
    for token in tokens:
        folded = _fold(token).strip(".")
        if folded in _NOT_A_NAME or folded in _DESCRIPTOR_PARTS:
            return None
        # A LOWERCASE token that is not a name particle is a preposition or an
        # article, which means the span crossed a clause boundary and the tail
        # is a place, a date or a qualifier rather than part of the name:
        # "Alkemy en España", "Tânia Bulhões no Brasil", "Gray d'Albion à
        # Cannes", "Cambio al vertice in Alstom". Every one of those was a
        # measured disagreement with the paid model before this check, and in
        # every one the model was right. Rule 2 says capture what the text
        # states; it does not say weld the next clause on.
        if not _UPPER_START.match(token) and folded not in _PARTICLES:
            return None
    if len(name) < 2 or len(name) > 60:
        return None
    # A bare generic word is a description, not an employer. vocab already owns
    # that judgement for the English forms and validate applies it downstream;
    # this is the cheap half so the grammar does not report a match on "Grupo".
    if len(tokens) == 1 and len(name) <= 2:
        return None
    return name


# --- The grammars ------------------------------------------------------------
#
# Two shapes per language, because two shapes are what the corpus prints:
# PERSON-first ("Michele Serra est nommé directeur général de Korus Group") and
# EMPLOYER-first ("Relier Syd utser Mark Silfver till ny vd"). Every pattern is
# anchored at the start of the headline and every one names its groups
# `person` and `company`, so `parse_appointment` never has to know which
# language it is reading.
#
# `{T}` is substituted with that language's title alternation.

_LINK = r"(?:de\s+la|de\s+l['’]|de|du|des|d['’]|da|do|das|dos|di|della|del|van|von|der|f[öo]r|hos|p[åa]|i|bei|von\s+der)"

_PATTERNS: dict[str, tuple[str, ...]] = {
    # "Michele Serra est nommé directeur général de Korus Group"
    # "Eric Brisard nommé directeur général de Greenergy"
    # "Covéa Insurance : Philippe Domart nommé directeur général"  -> employer-first
    "fr": (
        r"^(?P<person>.+?)\s+(?:est\s+|a\s+[ée]t[ée]\s+)?nomm[ée]e?\s+"
        # The article alternatives are case-SENSITIVE (`(?-i:...)`) inside an
        # otherwise case-insensitive pattern: "directeur général de La Famille"
        # must keep its capital article, and a case-blind `de\s+la` ate it.
        r"(?:au\s+poste\s+de\s+|)(?:{T})\s+(?:(?-i:de\s+la|de\s+l['’])|du|des|de|d['’])\s*(?P<company>.+)$",
        r"^(?P<company>[^:]+)\s*:\s*(?P<person>.+?)\s+(?:est\s+)?nomm[ée]e?\s+(?:{T})\s*$",
    ),
    # "Santander nombra a Mahesh Aditya nuevo CEO"
    # "Gonzalo Martín-Villa, nuevo CEO de la Institución Educativa SEK"
    "es": (
        r"^(?P<company>.+?)\s+(?:nombra|design[ao])\s+(?:a\s+|al\s+)?(?P<person>.+?)"
        r"\s+(?:como\s+)?(?:su\s+)?(?:nuev[oa]\s+)?(?:{T})\s*$",
        r"^(?P<person>.+?)\s*,\s*(?:nombrad[oa]\s+)?(?:nuev[oa]\s+)?(?:{T})\s+de\s+(?:la\s+|el\s+|los\s+|las\s+)?(?P<company>.+)$",
        r"^(?P<person>.+?)\s+es\s+el\s+(?:nuev[oa]\s+)?(?:{T})\s+de\s+(?:la\s+|el\s+)?(?P<company>.+)$",
        r"^(?P<person>.+?)\s+asume\s+como\s+(?:nuev[oa]\s+)?(?:{T})\s+de\s+(?:la\s+|el\s+)?(?P<company>.+)$",
    ),
    # "Marlos Steffen é o novo CEO da Approach Tech"
    # "Leonardo Coelho assume como CEO da Aon no Brasil"
    "pt": (
        r"^(?P<person>.+?)\s+(?:é|e)\s+(?:o|a)\s+(?:nov[oa]\s+)?(?:{T})\s+d[ao]s?\s+(?P<company>.+)$",
        r"^(?P<person>.+?)\s+assume\s+(?:como\s+)?(?:{T})\s+d[ao]s?\s+(?P<company>.+)$",
        r"^(?P<person>.+?)\s*,\s*(?:nomead[oa]\s+)?(?:nov[oa]\s+)?(?:{T})\s+d[ao]s?\s+(?P<company>.+)$",
        r"^(?P<person>.+?)\s+(?:é|e)\s+nomead[oa]\s+(?:{T})\s+d[ao]s?\s+(?P<company>.+)$",
        r"^(?P<company>.+?)\s+(?:nomeia|anuncia)\s+(?P<person>.+?)\s+como\s+"
        r"(?:nov[oa]\s+)?(?:{T})\s*$",
    ),
    # "Francesco Durante è il nuovo Amministratore Delegato di Multiversity"
    # "Carlo Noseda nominato CEO di Balich Wonder Studio"
    "it": (
        r"^(?P<person>.+?)\s+[èe]\s+(?:il|la)\s+(?:nuov[oa]\s+)?(?:{T})\s+(?:di|della|del|dei)\s+(?P<company>.+)$",
        r"^(?P<person>.+?)\s+nominat[oa]\s+(?:{T})\s+(?:di|della|del|dei)\s+(?P<company>.+)$",
        r"^(?P<person>.+?)\s+nuov[oa]\s+(?:{T})\s+(?:di|della|del|dei)\s+(?P<company>.+)$",
        r"^(?P<company>[^:]+)\s*:\s*(?P<person>.+?)\s+(?:è|e)\s+(?:il|la)\s+"
        r"(?:nuov[oa]\s+)?(?:{T})\s*$",
    ),
    # "Marcel Dissel wird CEO der Corvaglia-Gruppe"
    # "Dentsu ernennt Hiroshi Igarashi zum CEO"
    "de": (
        # `des` is deliberately absent. The German genitive takes a descriptive
        # noun phrase as readily as a name ("CEO des Basler Energieversorgers
        # IWB"), and no capitalisation rule can tell the two apart in a
        # language that capitalises every noun. `der`, `von` and `bei` do not
        # have that problem in this corpus.
        r"^(?P<person>.+?)\s+wird\s+(?:neue[rn]?\s+)?(?:{T})\s+(?:der|von|bei)\s+(?P<company>.+)$",
        r"^(?P<person>.+?)\s+ist\s+neue[rn]?\s+(?:{T})\s+(?:der|von|bei)\s+(?P<company>.+)$",
        r"^(?P<company>.+?)\s+ernennt\s+(?P<person>.+?)\s+zu[rm]\s+(?:neuen\s+)?(?:{T})\s*$",
    ),
    # "Mark Silfver blir ny vd i Relier Syd"
    # "Relier Syd utser Mark Silfver till ny vd"
    "sv": (
        r"^(?P<person>.+?)\s+(?:blir|tilltr[äa]der\s+som)\s+ny\s+(?:{T})\s+(?:f[öo]r|p[åa]|i|hos)\s+(?P<company>.+)$",
        r"^(?P<person>.+?)\s+ny\s+(?:{T})\s+(?:f[öo]r|p[åa]|i|hos)\s+(?P<company>.+)$",
        r"^(?P<company>.+?)\s+(?:utser|v[äa]ljer)\s+(?P<person>.+?)\s+till\s+ny\s+(?:{T})\s*$",
    ),
    # "Jan Jansen wordt CEO van Acme"
    "nl": (
        r"^(?P<person>.+?)\s+wordt\s+(?:{T})\s+van\s+(?P<company>.+)$",
        r"^(?P<company>.+?)\s+benoemt\s+(?P<person>.+?)\s+tot\s+(?:{T})\s*$",
    ),
    # "Arçelik'in yeni CEO'su Can Dinçer oldu"
    # "Saint-Gobain Türkiye'nin yeni CEO'su Murat Savcı oldu"
    # Turkish marks the genitive with a suffix on the employer's own name, and
    # the vowel harmonises: Arçelik'in, Lactalis'in, SANKO'nun, Türkiye'nin.
    # One optional buffer `n` plus one harmonised vowel plus `n`, all optional,
    # rather than a fixed spelling.
    "tr": (
        r"^(?P<company>.+?)['’](?:n?[ıiuü]n)?\s+yeni\s+(?:{T})['’]?(?:su|s[uü])\s+"
        r"(?P<person>.+?)\s+oldu\s*$",
    ),
}

_COMPILED: dict[str, tuple[re.Pattern[str], ...]] = {
    lang: tuple(
        re.compile(pattern.replace("{T}", _TITLES[lang]), re.I | re.UNICODE)
        for pattern in patterns
    )
    for lang, patterns in _PATTERNS.items()
}


@dataclass(frozen=True)
class Appointment:
    """One chief-executive appointment, exactly as the headline stated it."""
    company: str
    company_key: str
    person: str
    title: str
    lang: str


def language(item: dict) -> str:
    """The two-letter language this module will read the item as, or "".

    Collectors spell it three ways (`language`, `lang`, and google_news's
    `locale` of "COUNTRY:lang"), the same three `gate_ledger._lang` reads, and
    national_press writes the English name rather than the code. Anything not
    in `LANGUAGES` returns "" and the item takes the paid path.
    """
    for candidate in (item.get("language"), item.get("lang")):
        if candidate:
            code = str(candidate).strip().lower()[:2]
            return code if code in LANGUAGES else ""
    locale = item.get("locale") or ""
    if ":" in locale:
        code = locale.split(":", 1)[1].strip().lower()[:2]
        return code if code in LANGUAGES else ""
    return ""


def strip_publisher(headline: str, source_name: str) -> str:
    """google_news appends " - <publisher>" to every RSS title.

    Stripped with the collector's OWN `source_name` and never with a guess: a
    generic "drop everything after the last dash" would take the employer off
    "Marcel Dissel wird CEO der Corvaglia-Gruppe" the day a publisher forgets
    its own suffix. No source_name, no strip.
    """
    head = (headline or "").strip()
    name = (source_name or "").strip()
    if name and head.endswith(name):
        trimmed = head[: -len(name)].rstrip()
        if trimmed.endswith("-") or trimmed.endswith("–") or trimmed.endswith("|"):
            return trimmed[:-1].strip()
    return head


def parse_appointment(item: dict) -> Appointment | None:
    """The appointment this headline states, or None to take the paid path.

    Cheap enough to call before anything is bought, and the SAME call answers
    both throughput levers: `cheap_extract` uses it to close the record for
    $0, and `dedupe.leadership_event_duplicate` uses it to recognise the
    seventh outlet's translation of an appointment already held. One grammar,
    because the two levers were always one piece of work.
    """
    lang = language(item)
    if lang not in _COMPILED:
        return None
    raw_text = (item.get("raw_text") or "").strip()
    headline = strip_publisher(item.get("headline") or "",
                               item.get("source_name") or "")
    if not headline or not raw_text:
        return None
    if len(headline) > 200 or "?" in headline or ";" in headline:
        return None
    # An ALL CAPS headline erases the capitalisation boundary every span check
    # here depends on, and it also mangles the employer: "CHRISTOPHE
    # PINARD-LEGRY NOMMÉ DIRECTEUR GÉNÉRAL DE CANA L EUROPE" parsed the
    # publisher's own typo as the employer's name. `cheap_extract` declines
    # Title Case for the same reason; this is the louder version of it.
    letters = [c for c in headline if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        return None

    # The knock-outs run on the WHOLE text, and a departure knock-out runs on
    # the headline too: "X nommé DG de Y, Z quitte le groupe" is two events.
    if _DEPARTURE.search(raw_text) or _UNCARRIED.search(raw_text):
        return None
    if _DEAL.search(raw_text) or _AMOUNT.search(raw_text):
        return None
    if prefilter._REDUCTION.search(raw_text) or prefilter._RIF.search(raw_text):
        return None

    # A second seat named anywhere in the headline is a second role. Counted
    # rather than anchored, exactly as `_parse_leadership` counts `_C_TITLE`.
    if len(re.findall(_TITLES[lang], headline, re.I)) > 1:
        return None

    for pattern in _COMPILED[lang]:
        match = pattern.match(headline)
        if not match:
            continue
        person = valid_person(match.group("person"))
        company = valid_company(match.group("company"))
        if not person or not company:
            continue
        key = vocab.company_key(company)
        if not key:
            continue
        return Appointment(company=company, company_key=key, person=person,
                           title=CEO_TITLE, lang=lang)
    return None


def extract(item: dict, *, count: bool = True) -> dict | None:
    """A complete classified-shaped dict for a stated appointment, or None.

    The dict is the same shape `cheap_extract.extract` returns and goes through
    the same `validate.build_signal -> store` path, so every downstream guard
    still applies. `count=False` makes the call a probe.

    NO PLACE IS CLAIMED. The English parser reads a place off a "-based"
    prefix; these grammars have no equivalent they could read without
    guessing, so `city` and `country` are empty and
    `identity.place_if_unplaced` does the one free resolution it already does
    for every otherwise-placeless row. An invented country is the defect that
    had a US-filtered reader seeing 5 of 51 events, and a blank is honest.
    """
    if count:
        STATS["attempted"] += 1
    appointment = parse_appointment(item)
    if appointment is None:
        if count:
            STATS["declined"] += 1
        return None
    if count:
        STATS["closed"] += 1

    summary = (f"{appointment.company} has appointed {appointment.person} as "
               f"{appointment.title}.")
    readthrough = (
        f"{appointment.company} has a new {appointment.title}: "
        f"{appointment.person}. One appointment, not a headcount change; the"
        " report names no wider hiring plans."
    )
    return {
        "is_talent_signal": True,
        "company": appointment.company,
        "pillar": "leadership_change",
        "signal_direction": "neutral",
        "city": "",
        "country": "",
        "headquarters_city": "",
        "headquarters_country": "",
        "confidence": "reported",
        "functions": ["executive"],
        "industry": "",
        "state": "",
        "headcount": 0,
        "headcount_scope": "",
        "funding_amount": "",
        "funding_stage": "",
        "effective_date": "",
        "ticker": "",
        "work_mode": "",
        "deal_type": "",
        "site_event": "",
        "employer_type": "",
        "headline": item.get("headline") or "",
        "summary": summary,
        "talent_readthrough": readthrough,
        "predicted_outcome": "",
        "check_after_date": "",
        # `build_signal` ignores keys it does not name, and `run_collect` reads
        # this one to stamp the row's provenance. A deterministic row already
        # carried a marker; this makes it say WHICH parser, because "no model
        # read this" is a different claim when a different grammar made it.
        "notes": EVIDENCE_NOTE,
    }
