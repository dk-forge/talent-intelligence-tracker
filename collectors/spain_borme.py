"""Spain's chief-executive spine, off the Boletín Oficial del Registro Mercantil.

Every act inscribed in a Spanish commercial register is published, by law, in
BORME Section A the following week, and the bulletin prints each act under a
FIXED HEADING and each office under a FIXED ABBREVIATION. So an appointment and
a removal are typed the same way SEC Item 5.02, SEBI Regulation 30 and the
Korea Exchange's report titles are typed, and no model is needed to tell one
from the other. Keyless, free, daily, and it states BOTH directions.

    discovery  https://www.boe.es/datosabiertos/api/borme/sumario/{YYYYMMDD}
    document   https://www.boe.es/diario_borme/txt.php?id=BORME-A-YYYY-NNN-PP

**Read the second URL before changing it.** The obvious one is
`/diario_borme/xml.php?id=`, which serves the same text as clean XML — and
`boe.es/robots.txt` says `Disallow: /diario_borme/xml.php?` in as many words.
The HTML at `txt.php` carries the identical `<h5 class="articulo">` /
`<p class="parrafo">` structure the XSLT renders the XML into, and is not
disallowed by any line of that file. The open-data API is not an alternative
either: it publishes SUMMARIES only, and `/datosabiertos/api/borme/id/{ident}`
answers 404 `No se ha localizado la operación requerida`.

WHAT IT IS, MEASURED LIVE 2026-07-22..07-30
============================================

Section A runs to about **30 province files a day, ~2,180 company entries**,
each entry one company's acts for one inscription. Over 89 cached province
files (~3 publication days) there were **6,475 company entries**. The acts
break down, per day:

    Nombramientos 1,607   Ceses/Dimisiones 401   Revocaciones 342
    Reelecciones ~95      Constitución 612       Datos registrales (all)

THE POPULATION IS NOT THE BULLETIN, and the number says why
------------------------------------------------------------

Taking every leadership act in BORME is **123,455 rows a year** — the register
of a country with 1.3 million active companies, most of them micro. That is the
Companies House failure (5.7 million companies, 1.4 million appointments a
year) and the Estonian one (74,000 appointments a year, 86% at one-person `OÜ`
companies) for the third time.

**And Spain gives no employee count to threshold on.** BORME prints the act,
the office, the person and the registry sheet; it never prints headcount,
capital or turnover, and the accounts that would are deposited with the Colegio
de Registradores and sold rather than published. So the threshold that works
for the UK (the pay-gap duty's 9,230 employers), Czechia (the RES employee
band) and Estonia (the annual report's FTE figure) has no equivalent here.

**The materiality filter is therefore the OFFICE, and it is drawn on the same
line Japan and Korea are drawn on.** `edinet_japan` collects one clause of 44 —
代表取締役の異動, the REPRESENTATIVE director alone — and `opendart_korea`
collects 대표이사변경 for the same reason: the office that can bind the company
is a different kind of event from a seat on the board. Spain's equivalent is
the **consejero delegado**, the director to whom the board has delegated its
powers under article 249 of the Ley de Sociedades de Capital. This collector
reads that office and nothing else.

    all leadership acts     123,455 a year   494 a day   REFUSED, above
    board-grade offices     123,455 a year   494 a day   the same set
    **consejero delegado**  **~16,000 a year, 64 a day, ~310 a week**

which is the same order as `bse_india` (~350 a week) and lands Spain beside
India rather than on top of everything else in the database.

**What that costs, stated.** A plain `Consejero` joining a board is not
collected, and neither is a `Presidente` of the board (measured: 173 distinct
companies over three days). Widening to those is one entry in `OFFICES` below
and eight times the volume; it was declined here rather than left unconsidered.

BOTH DIRECTIONS, WHICH IS RARE HERE
------------------------------------

Only `czechia_ares` and this collector state a departure. BORME's act headings
carry the direction themselves:

    Nombramientos      an appointment       78 of 190 CEO-grade events
    Ceses/Dimisiones   a removal or a resignation      104
    Revocaciones       a revocation of the delegation    8

`Reelecciones` is DECLINED and counted. A re-election is the same person
continuing in the same office, so storing it would report a leadership change
where the register records that leadership did not change.

THE DATE IS THE INSCRIPTION DATE, AND IT IS NOT THE PUBLICATION DATE
---------------------------------------------------------------------

Every entry ends `Datos registrales. S 8 , H VI 23115, I/A 2 (22.07.26).` and
that parenthesised date is when the registrar inscribed the act. The bulletin
publishes it about a week later: measured over 7,281 entries, the lag is
**median 7 days, p90 8, p99 11**. The inscription date is what the source
states about the event, so that is `published_date`; the publication day is
only how the run finds it.

**The year is two digits and the pivot matters.** `(22.07.97)` read as
`2000 + 97` is the year 2097 — a date 71 years in the future, which
`validate.py` would reject and which would read as a typo in our code rather
than in the register. One such entry appeared in the 7,281. Any two-digit year
that lands in the future is read as the previous century.

**And 11 of 7,281 were inscribed more than a year before publication** — a
registrar finally writing up an old act. `MAX_BACKLOG_DAYS` declines those with
a count, for the reason `czechia_ares` declines its own seven: the true date
puts a decade-old change on a dashboard of this week's market, and today's date
is a figure nobody stated.

PERSONAL DATA
-------------

BORME publishes the office-holder's NAME and nothing else for these acts — no
birth date, no address, no national identifier, unlike the Czech and Estonian
files. `scrub_person` is still the only path from the bulletin to a row, and
the tests drive a fixture carrying invented birth dates and addresses to prove
that a future change to the bulletin's format cannot leak them.

Names are stored **exactly as the register prints them** and are never
reordered. BORME writes some people surname-first (`AROSA BELASTEGUI JON`) and
some given-name-first (`MARC BAIGET MORENO`), and there is no field that says
which — guessing would rewrite a person's name to make a column look tidy.

A LEGAL PERSON CAN HOLD THE OFFICE, and those are declined. A consejero
delegado is often a company (`TALDE ADVISOR, S.L.U.`), which then names a
natural person to represent it under a separate `Representan` act. 149 of the
first 1,614 board-grade holders were companies. They are declined rather than
stored as people.

WHAT IS REFUSED, so nobody re-derives it
-----------------------------------------

* `xml.php` — robots-disallowed, above. This is the whole reason the parser
  reads HTML.
* The BORME **Section B** (`Otros actos publicados`) and **Section C**
  (`Anuncios y avisos legales`) carry no office acts.
* Diffing one day's entry against another company's to recover who a
  `Reelecciones` replaced. The bulletin states no such relation.
* `Otros conceptos` free prose, which sometimes narrates the change in
  sentences. That is the paragraph a model would have to read, and reading it
  is exactly what this collector exists to avoid.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

from pipeline import vocab

SITE = "https://www.boe.es"
SUMMARY_URL = SITE + "/datosabiertos/api/borme/sumario/{day}"
DOCUMENT_URL = SITE + "/diario_borme/txt.php?id={ident}"

COLLECTOR = "spain_borme"
SOURCE_NAME = "BORME Section A (Registro Mercantil, Spain)"

USER_AGENT = ("TalentIntel/1.0 (+https://asktherecruiter.com; "
              "info@asktherecruiter.com)")

ATTRIBUTION = ("Source: Boletín Oficial del Registro Mercantil, Sección "
               "Primera (Empresarios. Actos inscritos), published by the "
               "Agencia Estatal Boletín Oficial del Estado.")

# The bulletin's own act headings. Only the four below carry a direction; the
# rest of the vocabulary is here so the splitter knows where an act ENDS, which
# is the only way to tell an office label from a word inside somebody's name.
ACT_HEADINGS = (
    "Nombramientos y ceses", "Nombramientos", "Ceses/Dimisiones",
    "Revocaciones", "Reelecciones", "Datos registrales", "Otros conceptos",
    "Modificaciones estatutarias", "Constitución", "Constitucion",
    "Disolución", "Disolucion", "Extinción", "Extincion",
    "Ampliación de capital", "Ampliacion de capital",
    "Reducción de capital", "Reduccion de capital",
    "Cambio de domicilio social", "Cambio de objeto social",
    "Cambio de denominación social", "Cambio de denominacion social",
    "Declaración de unipersonalidad", "Declaracion de unipersonalidad",
    "Pérdida del carácter de unipersonalidad",
    "Perdida del caracter de unipersonalidad",
    "Cambio de identidad del socio único",
    "Cambio de identidad del socio unico",
    "Situación concursal", "Situacion concursal",
    "Fusión por absorción", "Fusion por absorcion",
    "Escisión múltiple", "Escision multiple",
    "Escisión parcial", "Escision parcial",
    "Transformación", "Transformacion",
    "Reactivación", "Reactivacion",
    "Emisión de obligaciones", "Emision de obligaciones",
    "Adaptada según", "Adaptada segun",
    "Sociedad unipersonal", "Socio único", "Socio unico",
    "Primera sucursal de sociedad extranjera",
    "Cierre provisional hoja registral", "Reapertura hoja registral",
    "Fe de erratas", "Suspensión de pagos", "Suspension de pagos",
    "Quiebra", "Cambio de duración", "Cambio de duracion",
    "Cambio de fecha de cierre de ejercicio social",
)

# The direction is the heading, and the heading is the register's word.
ARRIVALS = ("Nombramientos",)
DEPARTURES = ("Ceses/Dimisiones", "Revocaciones")
# Same person, same office, continuing. Declined with a count, never stored.
CONTINUATIONS = ("Reelecciones",)

# THE TRAP, and it is 15% of the feed. A Spanish board renewal is inscribed as
# a total cancellation followed by a total re-appointment, so one paragraph
# reads `Ceses/Dimisiones. Con.Delegado: X. Nombramientos. Con.Delegado: X.` —
# the same person, the same office, the same inscription date, in both
# directions. Nobody left. Measured over the seven cached publication days: 58
# of 373 person-company-date keys carry both directions and **46 of them carry
# the same office too**, which is 12% of everything this collector would
# otherwise store as a departure.
#
# So a matched pair at the SAME office is a re-inscription and neither half is
# stored — it is a `Reelecciones` written the long way, and this collector
# declines those too. A pair at DIFFERENT offices is kept, because the register
# is then stating two different things: SPLA SA ceased Javier Muñoz Gómez as
# `Con.Delegado` and appointed him `Cons.Del.Sol` on one date, and a sole
# delegation becoming a joint one is a change the register made rather than one
# we inferred.
#
# This is the Czech `datumVymazu` finding in a new shape: reading the register's
# cancellations as exits reports a leaving rate that is not real.
COLLAPSE_REINSCRIPTIONS = True

# The office. BORME abbreviates it to a fixed width and prints the SAME office
# in two case styles in one day's bulletin, so both are in the vocabulary and
# neither is normalised away. Every spelling below was counted in the live
# census of 2026-07-22..07-30; nothing here is invented.
OFFICES = {
    "Con.Delegado": "Consejero delegado",
    "CONS. DELEG.": "Consejero delegado",
    "CONS.DELEG.": "Consejero delegado",
    "CONSEJ.DELEG.": "Consejero delegado",
    "Cons.Del.Sol": "Consejero delegado solidario",
    "CONS.DEL.SOL": "Consejero delegado solidario",
    "Cons.Del.Man": "Consejero delegado mancomunado",
    "CONS.DEL.MAN": "Consejero delegado mancomunado",
}

# Offices deliberately NOT collected, kept as data so the docstring's claim is
# checkable and so widening is an edit to one tuple. See the volume table above.
OFFICES_DECLINED = (
    "Presidente", "PRESIDENTE", "Vicepresid.", "VICEPRESIDEN",
    "Consejero", "CONSEJERO", "Secretario", "SECRETARIO",
    "Vicesecret.", "SecreNoConsj", "VsecrNoConsj", "Cons.Ejecuti",
    "Cons.NO Ejec", "Consj.Domini", "CONS.INDEPEN",
    "Adm. Unico", "ADM.UNICO", "Adm. Solid.", "ADM.SOLIDAR.",
    "Adm. Mancom.", "ADM.CONJUNTO", "Apoderado", "APODERADO",
    "Apo.Sol.", "APODERAD.SOL", "Apo.Manc.", "Apo.Man.Soli",
    "APOD.MANCOMU", "APOD.SOL/MAN", "Auditor", "AUDIT.CUENT.",
    "Liquidador", "LIQUIDADOR", "LiqUnico", "Representan",
    "REPR.143 RRM", "Soc.Prof.",
)

# A holder that is a company, not a person. The office is often delegated to a
# corporate director which then names a natural person to represent it.
_LEGAL_PERSON = re.compile(
    r"(?:^|[\s,.])(?:S\.?\s?L\.?\s?U?|S\.?\s?A\.?\s?U?|S\.?\s?L\.?\s?P|"
    r"S\.?\s?C\.?\s?P|S\.?\s?A\.?\s?S|S\.?\s?COOP|SOCIEDAD|SOC\.|"
    r"LTD|LIMITED|INC|LLC|GMBH|B\.?V|N\.?V|PLC|S\.?\s?R\.?\s?L|"
    r"A\.?\s?I\.?\s?E|U\.?\s?T\.?\s?E|FUNDACION|FUNDACIÓN|ASOCIACION|"
    r"ASOCIACIÓN|AYUNTAMIENTO|GENERALITAT|DIPUTACION|DIPUTACIÓN)\.?\s*$",
    re.IGNORECASE)

# Fields BORME does not publish for these acts. Named so the scrubber is a
# vocabulary rather than a habit, and so a future format change cannot smuggle
# one through by appearing in a dict this collector did not expect.
PERSONAL_FIELDS_DROPPED = ("birth_date", "fecha_nacimiento", "nacimiento",
                           "dni", "nif", "nie", "address", "domicilio",
                           "nacionalidad", "nationality", "identifier")

REVISITS_ITS_SOURCE_URL = True

# Windows. BORME publishes on business days only, so a 7-day window is 5 or 6
# publication days and a run that lands on a Sunday still sees the whole week.
DEFAULT_DAYS = 7
MAX_DAYS = 60

# A registrar finally writing up an old act. 11 of 7,281 entries measured.
MAX_BACKLOG_DAYS = 365

# The last item of every day's Section A is not a province. It is
# `ÍNDICE ALFABÉTICO DE SOCIEDADES`, an A-to-Z of every company named that day
# pointing back at the province file each one is in, and it parses to zero
# company entries because it has none. Skipping it by TITLE as well as by the
# `-99` suffix is deliberate: either alone would silently start counting an
# index as an empty province the day the other changes.
INDEX_SUFFIX = "-99"
INDEX_TITLE = "ÍNDICE ALFABÉTICO DE SOCIEDADES"

# Sanity floors, all measured on the live bulletin 2026-07-22..07-30.
MIN_PROVINCE_FILES_PER_DAY = 10      # measured 28 to 32
# Per DAY, never per file. Province files are wildly uneven — Madrid runs to
# 653 company entries and Soria to a handful — so a per-file floor fires on a
# small province rather than on a broken parser. Measured: 15,642 entries over
# seven publication days, about 2,230 a day.
MIN_ENTRIES_PER_DAY = 500
FLOOR_EVENTS_PER_DAY = 8             # measured 64

_ARTICULO = re.compile(
    r'<h5 class="articulo">(.*?)</h5>\s*<p class="parrafo">(.*?)</p>', re.S)
_TAG = re.compile(r"<[^>]+>")
_ENTITIES = (("&amp;", "&"), ("&nbsp;", " "), ("&quot;", '"'), ("&#39;", "'"),
             ("&lt;", "<"), ("&gt;", ">"), ("\xa0", " "))
_COMPANY = re.compile(r"^(\d+)\s*-\s*(.+?)\.?$")
_INSCRIBED = re.compile(r"\((\d{2})\.(\d{2})\.(\d{2})\)")
_ACT = re.compile(
    r"(?:(?<=^)|(?<=[.\s]))("
    + "|".join(re.escape(a) for a in sorted(ACT_HEADINGS, key=len, reverse=True))
    + r")\s*[.:]")
# An office label sits between a sentence boundary and a colon, and contains
# neither a colon nor a semicolon. Names that follow are semicolon-separated.
_FIELD = re.compile(
    r"(?:^|(?<=[.;])\s)\s*([^:;]{2,22}?)\s*:\s*([^:]*?)"
    r"(?=(?:(?<=[.;])\s*[^:;]{2,22}?\s*:)|$)")


class BormeError(RuntimeError):
    """The bulletin moved, or the run read something that is not it."""


# --- knobs -----------------------------------------------------------------

def days_from_env(default_days: int | None = None) -> int:
    """`TIT_BORME_DAYS`, bounded. A window is publication days, not events."""
    raw = os.environ.get("TIT_BORME_DAYS", "").strip()
    fallback = DEFAULT_DAYS if default_days is None else default_days
    try:
        value = int(raw) if raw else fallback
    except ValueError:
        return fallback
    return max(1, min(MAX_DAYS, value))


def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept-Language": "es-ES,es;q=0.9"}


def _get(url: str, *, session=None, timeout: int = 60, accept: str) -> str:
    if session is not None:
        headers = dict(_headers())
        headers["Accept"] = accept
        response = session.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.text
    request = urllib.request.Request(
        url, headers={**_headers(), "Accept": accept})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return answer.read().decode("utf-8", "replace")


# --- the bulletin ----------------------------------------------------------

def _listify(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def section_a_items(payload: dict) -> list[tuple[str, str]]:
    """(identifier, province) for every Section A file in one day's summary.

    Every level of this document is singular-or-list — one `diario`, one
    `seccion`, one `item` all arrive as objects rather than one-element arrays,
    and Section B arrives with no `item` key at all on a quiet day. Reading
    `payload["data"]["sumario"]["diario"][0]` works right up until it doesn't.
    """
    data = (payload or {}).get("data") or {}
    out: list[tuple[str, str]] = []
    for diario in _listify((data.get("sumario") or {}).get("diario")):
        for seccion in _listify(diario.get("seccion")):
            if (seccion or {}).get("codigo") != "A":
                continue
            for item in _listify(seccion.get("item")):
                ident = (item or {}).get("identificador")
                if not ident:
                    continue
                title = (item.get("titulo") or "").strip()
                if ident.endswith(INDEX_SUFFIX) or title.upper() == INDEX_TITLE:
                    continue
                out.append((ident, title))
    return out


def fetch_summary(day: date, *, session=None) -> dict:
    url = SUMMARY_URL.format(day=day.strftime("%Y%m%d"))
    try:
        raw = _get(url, session=session, accept="application/json")
    except urllib.error.HTTPError as problem:
        if problem.code == 404:
            return {}
        raise
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # A day with no bulletin answers with an XML error document rather than
        # a 404, so "it did not parse as JSON" is a holiday and not a break.
        return {}
    if str(((payload.get("status") or {}).get("code") or "")) != "200":
        return {}
    return payload


def clean(fragment: str) -> str:
    text = _TAG.sub("", fragment)
    for entity, plain in _ENTITIES:
        text = text.replace(entity, plain)
    return " ".join(text.split())


def parse_document(html: str) -> list[tuple[str, str, str]]:
    """(registry entry, company name, act paragraph) for one province file."""
    out = []
    for heading, paragraph in _ARTICULO.findall(html):
        matched = _COMPANY.match(clean(heading))
        if not matched:
            continue
        out.append((matched.group(1), matched.group(2).strip(),
                    clean(paragraph)))
    return out


def split_acts(paragraph: str) -> list[tuple[str, str]]:
    """The paragraph as (heading, body) pairs, in the order it prints them."""
    hits = list(_ACT.finditer(paragraph))
    acts = []
    for index, found in enumerate(hits):
        stop = hits[index + 1].start() if index + 1 < len(hits) else len(paragraph)
        acts.append((found.group(1), paragraph[found.end():stop].strip()))
    return acts


def inscribed_on(paragraph: str, *, published: date) -> date | None:
    """The date the registrar inscribed the act, from `Datos registrales`.

    The bulletin prints a TWO-DIGIT year. `(22.07.97)` is 1997, not 2097, and
    the only thing that can say so is the publication date it must precede.
    """
    found = _INSCRIBED.search(paragraph)
    if not found:
        return None
    day, month, year = (int(part) for part in found.groups())
    for century in (2000, 1900):
        try:
            candidate = date(century + year, month, day)
        except ValueError:
            continue
        if candidate <= published:
            return candidate
    return None


def is_legal_person(name: str) -> bool:
    return bool(_LEGAL_PERSON.search(name.strip().rstrip(".")))


def scrub_person(entry) -> dict | None:
    """A name and nothing else, whatever else the bulletin grows.

    BORME publishes no birth date, address or identifier for these acts today,
    so unlike the Czech and Estonian files there is nothing here to strip. That
    is exactly why this function has to be more than a `.strip()`: a scrubber
    written against a source that publishes nothing private is a scrubber that
    has never been tested, and the first version of this one passed the name
    through whole. A fixture entry reading
    `SOLER MARTI CARMEN (nacida el 04.06.1975, DNI 12345678Z, domicilio en
    Calle Mayor 1, 28013 Madrid)` went into the headline, the summary and the
    stored signal intact.

    Two rules, both checkable against the real bulletin:

    * anything from the first parenthesis onward is dropped. **No holder string
      in 534 real ones carries a parenthesis at all**, so this can only ever
      remove something the bulletin does not print today.
    * a name that still contains a DIGIT is refused rather than trimmed. Two of
      those 534 contained one — `GRUPO MOORE 2019 SL` and
      `PUERTO 58 SOCIEDAD LIMITADA` — and both are companies, which
      `is_legal_person` has already declined. A person's name has no digits in
      it, and a string that does is not one.
    """
    if isinstance(entry, dict):
        raw = str(entry.get("name") or "")
    elif isinstance(entry, str):
        raw = entry
    else:
        return None
    raw = raw.split("(")[0]
    name = " ".join(raw.split()).strip(" .,;")
    if not name or any(character.isdigit() for character in name):
        return None
    return {"name": name}


def holders(body: str) -> list[tuple[str, str, list[str]]]:
    """(label, canonical office, names) for the offices this collector reads."""
    out = []
    for label, names in _FIELD.findall(body):
        label = label.strip().lstrip(".;, ")
        office = OFFICES.get(label)
        if not office:
            continue
        people = [part.strip(" .;,") for part in names.split(";")]
        people = [person for person in people if person]
        if people:
            out.append((label, office, people))
    return out


# --- the record ------------------------------------------------------------

def _pretty(when: date) -> str:
    return f"{when.day} {when.strftime('%B')} {when.year}"


def _verb(heading: str) -> tuple[str, str]:
    """(headline verb, the register's own Spanish word)."""
    if heading in ARRIVALS:
        return "appointed", "Nombramientos"
    if heading == "Revocaciones":
        return "had the delegation revoked as", "Revocaciones"
    return "removed or resigned as", "Ceses/Dimisiones"


def _row(*, company: str, registry_entry: str, province: str, heading: str,
         office: str, label: str, person: dict, inscribed: date,
         published: date, ident: str) -> dict | None:
    name = " ".join(company.split()).strip()
    who = person.get("name") or ""
    if not (name and who):
        return None
    url = DOCUMENT_URL.format(ident=ident)
    verb, spanish = _verb(heading)
    headline = f"{name}: {who} {verb} {office} on {_pretty(inscribed)}"

    # Composed HERE and returned unchanged by `as_classified`, so the summary is
    # a literal prefix of `raw_text` and every figure in it is verbatim in the
    # source by construction rather than by care. `validate._NUMBER` reads
    # "2026. BAUHOF" as the figure `2026b` — a defect it names and deliberately
    # leaves alone — and that discarded twelve of Estonia's first sixty-six
    # rows. Composing the two sentences separately is what let them differ.
    summary = (
        f"The Registro Mercantil inscribed on {_pretty(inscribed)} that {who} "
        f"was {verb} {office} of {name}, and the Boletín Oficial del Registro "
        f"Mercantil published the act on {_pretty(published)} under registry "
        f"entry {registry_entry}. {ATTRIBUTION}"
    )
    body = (
        f"{summary} The bulletin heads the act {spanish} and abbreviates the "
        f"office {label}. A consejero delegado is the director to whom the "
        f"board has delegated its powers under article 249 of the Ley de "
        f"Sociedades de Capital, which is why this collector reads that office "
        f"and not a seat on the board. The company is registered in the "
        f"commercial register of {province or 'Spain'}."
    )

    return {
        "raw_text": f"{headline}\n\n{body}",
        "summary": summary,
        "headline": headline,
        "source_url": url,
        "source_name": SOURCE_NAME,
        "discovery_url": SUMMARY_URL.format(day=published.strftime("%Y%m%d")),
        "published_date": inscribed.isoformat(),
        "company": name,
        "country": "Spain",
        # Personal data stops here: scrub_person returned a name and nothing
        # else, so no later stage can leak what it never received.
        "person_name": who,
        "act_heading": heading,
        "act_direction": "arrival" if heading in ARRIVALS else "departure",
        "office_label": label,
        "office": office,
        "registry_entry": registry_entry,
        # The province of the commercial register the company is filed in. It
        # is NOT stated to be the company's operating city, which is why it is
        # passed to vocab.normalize_city rather than written into `city`.
        "province": province,
        "borme_id": ident,
        "inscribed_on": inscribed.isoformat(),
        "published_on": published.isoformat(),
        "collector": COLLECTOR,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --- the run ---------------------------------------------------------------

def drop_reinscriptions(rows: list[dict]) -> tuple[list[dict], int]:
    """Remove both halves of a cancel-and-re-inscribe pair. See the note above.

    The key is (registry entry, person, office, inscription date). A pair at
    two DIFFERENT offices is not a re-inscription and survives.
    """
    if not COLLAPSE_REINSCRIPTIONS:
        return rows, 0
    directions: dict[tuple, set] = {}
    for row in rows:
        key = (row["registry_entry"], row["person_name"], row["office"],
               row["inscribed_on"])
        directions.setdefault(key, set()).add(row["act_direction"])
    paired = {key for key, seen in directions.items() if len(seen) > 1}
    kept = [row for row in rows
            if (row["registry_entry"], row["person_name"], row["office"],
                row["inscribed_on"]) not in paired]
    return kept, len(rows) - len(kept)


def emptiness_floor(publication_days: int) -> int:
    """How few chief-executive acts is too few to be a quiet week."""
    if publication_days <= 0:
        return 0
    return max(1, publication_days * FLOOR_EVENTS_PER_DAY // 4)


LAST_RUN: dict = {}


def collect(queries=None, *, days: int | None = None, today: date | None = None,
            session=None, documents=None, summaries=None,
            pause: float = 0.3) -> list[dict]:
    """Every consejero delegado act inscribed in Spain inside the window.

    `queries` is accepted and ignored so this collector is interchangeable with
    the others in run_collect: the bulletin IS the population and there is
    nothing to search for.

    `summaries` maps a `date` to an already-parsed summary payload and
    `documents` maps a BORME identifier to already-fetched HTML, so a test can
    drive the whole path with no network at all.
    """
    window = days if days is not None else days_from_env()
    end_day = today or datetime.now(timezone.utc).date()
    calendar = [end_day - timedelta(days=offset) for offset in range(window)]

    events: list[dict] = []
    seen: set[tuple] = set()
    publication_days = province_files = entries = 0
    acts_read = declined_continuation = declined_legal = 0
    declined_backlog = declined_no_date = 0

    for day in sorted(calendar):
        if summaries is not None:
            payload = summaries.get(day) or {}
        else:
            payload = fetch_summary(day, session=session)
        items = section_a_items(payload)
        if not items:
            continue
        if len(items) < MIN_PROVINCE_FILES_PER_DAY:
            raise BormeError(
                f"{day} listed {len(items)} Section A province files, against "
                f"a measured 28 to 32. That is the summary document having "
                f"moved, not a quiet day in Spain.")
        publication_days += 1
        entries_today = 0
        for ident, province in items:
            province_files += 1
            if documents is not None:
                html = documents.get(ident)
                if html is None:
                    continue
            else:
                try:
                    html = _get(DOCUMENT_URL.format(ident=ident),
                                session=session, accept="text/html")
                except (urllib.error.HTTPError, urllib.error.URLError) as bad:
                    print(f"[{COLLECTOR}] {ident} unreadable: {bad}")
                    continue
                if pause:
                    time.sleep(pause)
            rows = parse_document(html)
            entries += len(rows)
            entries_today += len(rows)
            for registry_entry, company, paragraph in rows:
                inscribed = inscribed_on(paragraph, published=day)
                for heading, body in split_acts(paragraph):
                    if heading in CONTINUATIONS:
                        if holders(body):
                            declined_continuation += 1
                        continue
                    if heading not in ARRIVALS + DEPARTURES:
                        continue
                    for label, office, people in holders(body):
                        for raw_name in people:
                            acts_read += 1
                            if is_legal_person(raw_name):
                                declined_legal += 1
                                continue
                            if inscribed is None:
                                declined_no_date += 1
                                continue
                            if (day - inscribed).days > MAX_BACKLOG_DAYS:
                                declined_backlog += 1
                                continue
                            person = scrub_person(raw_name)
                            if not person:
                                continue
                            row = _row(company=company,
                                       registry_entry=registry_entry,
                                       province=province, heading=heading,
                                       office=office, label=label,
                                       person=person, inscribed=inscribed,
                                       published=day, ident=ident)
                            if row is None:
                                continue
                            fingerprint = (registry_entry, row["person_name"],
                                           office, heading,
                                           inscribed.isoformat())
                            if fingerprint in seen:
                                continue
                            seen.add(fingerprint)
                            events.append(row)
        if entries_today < MIN_ENTRIES_PER_DAY:
            raise BormeError(
                f"{day} parsed to {entries_today} company entries across "
                f"{len(items)} province files, against a measured ~2,230 a "
                f"day. That is the bulletin's markup having moved, not a "
                f"quiet day in Spain.")

    events, declined_reinscribed = drop_reinscriptions(events)
    arrivals = sum(1 for row in events if row["act_direction"] == "arrival")
    print(f"[{COLLECTOR}] {publication_days} publication day(s), "
          f"{province_files} province files, {entries} company entries, "
          f"{acts_read} chief-executive acts read")
    print(f"[{COLLECTOR}] {len(events)} stored ({arrivals} arrivals, "
          f"{len(events) - arrivals} departures); declined: "
          f"{declined_reinscribed} halves of a cancel-and-re-inscribe pair, "
          f"{declined_legal} legal-person holders, {declined_continuation} "
          f"re-elections, {declined_backlog} inscribed over "
          f"{MAX_BACKLOG_DAYS} days before publication, {declined_no_date} "
          f"with no inscription date")

    LAST_RUN.clear()
    LAST_RUN.update({"read": acts_read, "entries": entries,
                     "province_files": province_files,
                     "publication_days": publication_days,
                     "reinscriptions": declined_reinscribed,
                     "events": len(events)})

    floor = emptiness_floor(publication_days)
    if len(events) < floor:
        raise BormeError(
            f"{publication_days} publication day(s) produced {len(events)} "
            f"consejero delegado acts, against a measured 64 a day. That is "
            f"the act headings, the office abbreviations or the markup having "
            f"moved, not a quiet week in Spain.")
    return events


# --- the derived record ----------------------------------------------------

def as_classified(item: dict) -> dict:
    """The `classified` half of build_signal, derived rather than generated.

    Every value is a heading the bulletin printed, an abbreviation from its own
    fixed vocabulary, a date it stated, or a fixed editorial line. Nothing on
    the record is something a model believed, and there is no LLM cost at all.
    """
    name = item["company"]
    place = vocab.normalize_city(item.get("province") or "")
    return {
        "company": name,
        "pillar": "leadership_change",
        # Always neutral, in both directions, and for the reasons czechia_ares
        # gives: the register records that a delegation began or ended, not
        # whether the person came from outside the business, and one director
        # leaving is a change of leadership rather than a workforce reduction.
        "signal_direction": "neutral",
        "headline": item["headline"],
        # Built in `_row` and returned unchanged, so it is a literal prefix of
        # `raw_text`. See the note there for why that is structural.
        "summary": item["summary"],
        "talent_readthrough": (
            "Spanish company law makes the consejero delegado the director the "
            "board has delegated its powers to, and every delegation and every "
            "revocation must be inscribed in the commercial register and "
            "published. So this is a complete record of who can bind a Spanish "
            "company, in both directions, rather than a selective one — the "
            "only other source here that reports departures at all is the "
            "Czech register. Read two limits with it. The bulletin publishes "
            "the act about a week after the registrar inscribes it, so the "
            "date on the row is the inscription and the signal is a week old "
            "by construction. And it publishes no headcount, so a delegation "
            "at a fifteen-person company and one at a listed employer arrive "
            "looking identical; the office is the filter, not the size."
        ),
        "country": "Spain",
        # The province is where the commercial register sits, which is the
        # company's registered office — not a stated operating city. It is only
        # written down when it resolves to a city the vocabulary already knows.
        "city": place[0] if place else None,
        "region": place[1] if place else None,
        "headquarters_country": "Spain",
        # The state bulletin in which the act is legally published. Same class
        # of host as sec.gov: the venue, not a report of it. infer_confidence
        # caps at what the host is worth and boe.es is in
        # vocab.PRIMARY_SOURCE_DOMAINS, so this lands at 'verified'.
        "confidence": "verified",
    }
