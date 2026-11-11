"""The daily talent-intelligence digest, rebuilt around what a number MEANS.

An editorial review of two consecutive editions (2026-08-24) found the digest
counted non-hiring events as hiring jobs: a workforce agreement covering
existing employees led the edition by its headcount, projected figures and a
job-board delta were shown as current openings, and funding and leadership rows
sat under "hiring signals" with a count that was not a hiring count. Only 3 of
27 featured items were primary-verified and nothing said so; the two editions'
windows overlapped by a day.

This module fixes the CONTENT, reading the verdicts from `pipeline/count_meaning`
so the digest and the site agree on what each headcount means. It is offline and
read-only: it opens the committed database `mode=ro`, calls no model, makes no
network request, and writes nothing.

Every figure it prints obeys one rule: a headcount reaches a "roles"/"jobs"
total only when count_meaning says it is a current opening (`counts_as_roles`).
A workforce event never contributes its headcount; a projection is labelled
projected; a funding or leadership row has no hiring count to show.

    python3 daily_digest.py                       # yesterday -> today, UTC
    python3 daily_digest.py --since 2026-08-23 --until 2026-08-24
    python3 daily_digest.py --prev-ytd 30150      # enables the backfill note
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from pipeline import count_meaning as cm
from pipeline import digest_quality as dq
from pipeline import schema
from pipeline import planned_hiring
from pipeline import tiers


# --- Windowing -------------------------------------------------------------
#
# An edition for a UTC day D covers exactly [D-1 00:00Z, D 00:00Z): the single
# prior calendar day, half-open. Adjacent editions then TILE -- no subscriber
# sees a signal in two editions -- which is the overlap the review flagged (the
# Aug-23 edition covered Aug 22-23 and the Aug-24 edition Aug 23-24). The window
# is on `captured_at`: "what we added" is the honest meaning of a daily edition,
# and captured_at is a clean ISO timestamp, unlike the many source date formats
# in published_date.

def default_window(as_of: date) -> tuple[datetime, datetime]:
    until = datetime(as_of.year, as_of.month, as_of.day, tzinfo=timezone.utc)
    since = until - timedelta(days=1)
    return since, until


def _parse_day(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


# --- Language: show an English summary for a non-English headline ----------
#
# The stored `summary` is written in English by the extractor even when the
# headline is not (verified across the corpus: "La ONCE bate records..." ->
# "ONCE closed the 2025 fiscal year with 80,000 professionals..."). So for a
# non-English headline the digest leads with the English summary and keeps the
# original headline beneath it, rather than inventing a translation.
#
# Detection is a heuristic and errs toward leaving a headline alone: a real
# English headline mis-flagged would only gain its own summary as a lead, which
# is harmless. Non-ASCII letters catch accented languages; a small stop-word set
# catches the accent-free ones (Italian "posti di lavoro", etc.).

_FOREIGN_STOPWORDS = {
    # es
    "de", "los", "las", "empleos", "puestos", "trabajo", "contratacion",
    "millones", "nuevos", "busca", "genera", "generar", "empresa",
    # it
    "posti", "lavoro", "assume", "assumera", "aziende", "nuovi", "senza",
    # pt
    "empregos", "vagas", "contratar", "mais", "trabalho",
    # fr
    "emplois", "recrute", "recrutement", "postes", "entreprise",
    # de
    "stellen", "mitarbeiter", "einstellung", "arbeitsplatze",
    # tr
    "istihdam", "calisan", "ise", "alim",
}


def looks_non_english(headline: str) -> bool:
    if not headline:
        return False
    for ch in headline:
        if not ch.isalpha() or ord(ch) <= 127:
            continue
        name = unicodedata.name(ch, "")
        # A non-Latin script (Arabic, Cyrillic, CJK, Hebrew, Greek, ...) is
        # unambiguously not an English headline; a Latin letter with a
        # diacritic (é, ñ, ü) is the accented-European case.
        if "LATIN" not in name or "WITH" in name:
            return True
    tokens = re.findall(r"[a-zA-Zàáâäãåéèêëíìîïóòôöõúùûüñç]+", headline.lower())
    ascii_tokens = [re.sub(r"[^a-z]", "", t) for t in tokens]
    hits = sum(1 for t in ascii_tokens if t in _FOREIGN_STOPWORDS)
    return hits >= 2


# --- The featured item -----------------------------------------------------

@dataclass
class Featured:
    company: str
    headline: str
    summary: str
    meaning: cm.CountMeaning
    source_name: str
    source_url: str
    country: str | None
    published_date: str | None
    non_english: bool
    summary_english: bool
    # Public trust tier (pipeline/tiers.py, docs/VERIFICATION_TIERS.md).
    tier: str = "reported"
    rumored: bool = False
    # Reader-facing facts checked by pipeline/digest_quality (2026-11-04).
    link: str = ""
    announced: str | None = None
    iso2: str | None = None
    city: str | None = None
    industry: str = dq.INDUSTRY_NOT_STATED
    applications_open: bool = False
    roles_stated: str | None = None

    @property
    def location(self) -> str:
        country = dq.country_name(self.iso2)
        if self.iso2 and self.city:
            return f"{self.city}, {country}"
        return country

    @property
    def _substitute(self) -> bool:
        # Lead with the English summary only for a non-English headline AND when
        # the stored summary actually reads as English. Some rows carry a
        # non-English summary too (an Arabic careers post), and pretending that
        # is a translation would be a worse lie than showing the original.
        return (self.non_english and self.summary_english
                and self.summary.strip() != self.headline.strip())

    @property
    def lead(self) -> str:
        """The line to show first: the English summary when we have one."""
        if self._substitute:
            return self.summary.strip()
        return self.headline.strip()

    @property
    def original_note(self) -> str | None:
        """The original-language headline to show beneath, when substituted."""
        if self._substitute:
            return self.headline.strip()
        return None

    @property
    def needs_translation(self) -> bool:
        """A non-English headline with no English summary to stand in for it."""
        return self.non_english and not self.summary_english

    def provenance(self) -> str:
        if self.tier == "measured":
            return "Measured: first-party employer board"
        if self.tier == "verified":
            return "Verified: primary source"
        if self.rumored:
            return "Reported: unconfirmed"
        return "Reported"


def _featured(row, employer: str | None = None) -> Featured:
    headline = row["headline"] or ""
    _tier = tiers.tier_of_row(row)
    city = dq._get(row, "city")
    return Featured(
        company=employer if employer is not None else (row["company"] or ""),
        headline=headline,
        summary=row["summary"] or "",
        meaning=cm.classify(row),
        source_name=row["source_name"] or "",
        source_url=row["source_url"] or "",
        country=(row["country"] if _has(row, "country") else None),
        published_date=(row["published_date"] if _has(row, "published_date") else None),
        non_english=looks_non_english(headline),
        summary_english=not looks_non_english(row["summary"] or ""),
        tier=_tier.tier,
        rumored=_tier.rumored,
        link=dq.link(row),
        announced=dq.announced(row),
        iso2=dq.place(row),
        city=(city.strip() if city else None),
        industry=dq.industry(row),
        applications_open=dq.applications_open(row),
        roles_stated=dq.stated_roles(row),
    )


def _has(row, key) -> bool:
    try:
        row[key]
        return True
    except (KeyError, IndexError):
        return False


# --- The edition -----------------------------------------------------------

@dataclass
class Edition:
    since: datetime
    until: datetime
    as_of: datetime
    featured: list[Featured]
    ytd_total: int
    prev_ytd: int | None = None
    naming_roles: list[Featured] = field(default_factory=list)
    planned: list[Featured] = field(default_factory=list)
    workforce: list[Featured] = field(default_factory=list)
    funding_leadership: list[Featured] = field(default_factory=list)
    #: Everything count_meaning classifies as OTHER: a row with no headcount
    #: that is neither funding nor a leadership move (a bare site opening, an
    #: RTO policy). It has a home BECAUSE it is a real sixth class -- see the
    #: bucket loop for what its absence did.
    other: list[Featured] = field(default_factory=list)
    #: Items held back by the pre-send checks: (employer, reason). They are
    #: not in `featured`, so no headline counts them.
    held: list[tuple[str, str]] = field(default_factory=list)
    #: Stale or republished plans filtered out of this edition.
    stale_count: int = 0

    @property
    def unique_employers(self) -> int:
        return len({f.company.strip().casefold() for f in self.featured})

    @property
    def confirmed_count(self) -> int:
        return sum(1 for f in self.featured if f.meaning.primary)

    @property
    def early_count(self) -> int:
        return len(self.featured) - self.confirmed_count

    @property
    def current_roles_total(self) -> int:
        return sum(f.meaning.roles or 0 for f in self.naming_roles)

    @property
    def backfilled(self) -> bool:
        return (self.prev_ytd is not None
                and (self.ytd_total - self.prev_ytd) > len(self.featured))

    @property
    def sectioned(self) -> list[Featured]:
        """Every featured row, in whichever bucket it landed.

        The edition's headline counts `featured`. This is what the reader is
        actually shown. They must be the same rows, and `test_digest_sections`
        holds that: a row counted in a headline and printed nowhere is a
        number the reader cannot check against anything.
        """
        return (self.naming_roles + self.planned + self.workforce
                + self.funding_leadership + self.other)


def build_edition(rows, since, until, as_of, ytd_total, prev_ytd=None) -> Edition:
    featured = []
    held: list[tuple[str, str]] = []
    stale = 0
    for r in rows:
        verdict = dq.check_attribution(r)
        if verdict.status == dq.HELD:
            held.append((verdict.employer, verdict.reason))
            continue
        if not dq.link(r):
            held.append((verdict.employer, "no source or careers link"))
            continue
        if dq.is_stale(r, until) or dq.is_republished(r, until):
            stale += 1
            continue
        featured.append(_featured(r, verdict.employer))
    ed = Edition(since=since, until=until, as_of=as_of, featured=featured,
                 ytd_total=ytd_total, prev_ytd=prev_ytd, held=held,
                 stale_count=stale)
    for f in featured:
        t = f.meaning.type
        if t in (cm.CONFIRMED_HIRES, cm.OPEN_VACANCIES):
            ed.naming_roles.append(f)
        elif t == cm.PLANNED_JOBS:
            ed.planned.append(f)
        elif t == cm.WORKFORCE_EVENT:
            ed.workforce.append(f)
        elif t == cm.FUNDING_OR_LEADERSHIP:
            ed.funding_leadership.append(f)
        else:
            # THE BIN THAT WAS A TRAPDOOR. count_meaning has SIX types and
            # this loop had five branches, so every `other` row fell out of
            # the edition entirely: counted in the headline ("adds N
            # signals"), named in no breakdown, printed in no section. On the
            # committed database that is 1,545 of 35,789 current rows.
            #
            # An `else` rather than a sixth `elif cm.OTHER`, deliberately: a
            # seventh type added later lands in a section a reader can see,
            # instead of silently going missing again. Being visible in the
            # wrong bucket is a bug somebody reports; being invisible is not.
            ed.other.append(f)
    ed.naming_roles.sort(key=lambda f: f.meaning.roles or 0, reverse=True)
    ed.planned.sort(key=_headcount, reverse=True)
    ed.workforce.sort(key=_headcount, reverse=True)
    return ed


def _headcount(f: Featured) -> int:
    """The size for ORDERING a planned/workforce row, read back from the text.

    count_meaning deliberately keeps a projection's and a workforce event's
    figure OUT of `roles` so it can never reach a total. This recovers it from
    the headline for ORDERING and display only, and is never summed.
    """
    m = re.search(r"\b(\d[\d,\.]{2,})\b", f.headline)
    return int(re.sub(r"[,\.]", "", m.group(1))) if m else 0


def _projected_size(f: Featured) -> int:
    return _headcount(f)


# --- Rendering (plain text; adaptable to the HTML the mailer wraps) ---------

def regional_ranking(ed: Edition) -> list[tuple[str, int]]:
    """Countries by item count. An unplaced item is never ranked."""
    counts: dict[str, int] = {}
    for f in ed.featured:
        if f.iso2:
            name = dq.country_name(f.iso2)
            counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%MZ")


def what_changed(ed: Edition) -> str:
    n = len(ed.featured)
    if n == 0:
        return "No new signals were captured in this window."
    parts = []
    if ed.naming_roles:
        parts.append(f"{len(ed.naming_roles)} naming current roles")
    if ed.planned:
        parts.append(f"{len(ed.planned)} projected")
    if ed.workforce:
        parts.append(f"{len(ed.workforce)} workforce events")
    if ed.funding_leadership:
        parts.append(f"{len(ed.funding_leadership)} funding/leadership")
    # NAMED, SO THE BREAKDOWN SUMS TO THE FIGURE IN FRONT OF IT. While `other`
    # had no bucket these rows were in the "adds N" and in none of the parts,
    # so the parenthesis quietly failed to account for up to 4% of the
    # edition and nothing said a word about the shortfall.
    if ed.other:
        parts.append(f"{len(ed.other)} other")
    breakdown = ", ".join(parts) if parts else "none carrying a hiring figure"
    lead = ""
    if ed.naming_roles:
        top = ed.naming_roles[0]
        lead = (f" The largest confirmed opening is {top.company} at "
                f"{top.meaning.roles:,} roles.")
    return (f"Today's edition adds {n} signal{'s' if n != 1 else ''} "
            f"({breakdown}).{lead}")


def render(ed: Edition) -> str:
    L: list[str] = []
    L.append("TALENT INTELLIGENCE DAILY")
    L.append(f"Window: {_fmt_dt(ed.since)} -> {_fmt_dt(ed.until)} "
             f"(captured; half-open, non-overlapping with adjacent editions)")
    L.append("")
    L.append(what_changed(ed))
    L.append("")
    L.append(f"Confidence: {ed.confirmed_count} confirmed via primary source "
             f"· {ed.early_count} early indications "
             f"(reported or first-party, not a filed figure).")

    ytd_line = f"Year to date: {ed.ytd_total:,} signals."
    if ed.backfilled:
        grew = ed.ytd_total - (ed.prev_ytd or 0)
        ytd_line += (f" YTD grew by {grew:,} while this edition added "
                     f"{len(ed.featured):,}: the difference is backfill of "
                     f"older stories now captured, not new activity today.")
    L.append(ytd_line)
    L.append(f"Counts: {len(ed.featured)} report"
             f"{'s' if len(ed.featured) != 1 else ''} from "
             f"{ed.unique_employers} unique employer"
             f"{'s' if ed.unique_employers != 1 else ''}. Job-board "
             f"listings are not vacancies: one opening can be listed many "
             f"times, so no listing count is a count of jobs.")
    if ed.held:
        L.append(f"Held back for an employer or link check: {len(ed.held)}.")
    if ed.stale_count:
        L.append(f"Filtered out as stale or republished plans: "
                 f"{ed.stale_count}.")
    L.append("")

    L.append("SIGNALS NAMING THE MOST ROLES")
    L.append("(current openings only; workforce and projected figures are "
             "listed separately below and are NOT counted here)")
    if ed.naming_roles:
        for f in ed.naming_roles[:10]:
            L.extend(_render_item(f, show_roles=True))
        L.append(f"  Current roles named in this window: "
                 f"{ed.current_roles_total:,}.")
    else:
        L.append("  None this window.")
    L.append("")

    if ed.planned:
        L.append("PLANNED / PROJECTED HIRING (future or conditional; not current openings)")
        for f in ed.planned[:8]:
            L.extend(_render_item(f, projected=True))
        L.append("")

    if ed.workforce:
        L.append("WORKFORCE EVENTS (existing employees, NOT hiring)")
        for f in ed.workforce[:8]:
            L.extend(_render_item(f, workforce=True))
        L.append("")

    if ed.funding_leadership:
        L.append("FUNDING & LEADERSHIP (no hiring count)")
        for f in ed.funding_leadership[:8]:
            L.extend(_render_item(f))
        L.append("")

    # THE SIXTH SECTION, FOR THE SIXTH TYPE. The heading says what these rows
    # are -- no headcount, and not funding or leadership either -- rather than
    # borrowing a heading that would make a claim about them. That is the same
    # rule the four headings above follow, and the reason this section exists
    # at all is that the rows were previously shown under no heading anywhere.
    if ed.other:
        L.append("OTHER SIGNALS (no hiring count)")
        for f in ed.other[:8]:
            L.extend(_render_item(f))
        L.append("")

    ranking = regional_ranking(ed)
    unplaced = sum(1 for f in ed.featured if not f.iso2)
    if ranking or unplaced:
        L.append("BY COUNTRY (items per country)")
        for name, n in ranking[:10]:
            L.append(f"  {name}: {n}")
        if unplaced:
            L.append(f"  Not ranked ({dq.LOCATION_NOT_STATED}): {unplaced}")
        L.append("")

    if ed.featured:
        L.append(f"Applying? Tailor your CV for any of these roles with "
                 f"Ask The Recruiter: {dq.TAILOR_CV_URL}")

    return "\n".join(L).rstrip() + "\n"


def _render_item(f: Featured, show_roles=False, projected=False,
                 workforce=False) -> list[str]:
    tag = f"[{f.meaning.label} · {f.provenance()}]"
    if show_roles and f.meaning.roles is not None:
        head = f"  {f.meaning.roles:,} roles - {f.company} {tag}"
    elif projected:
        size = _projected_size(f)
        num = f"{size:,} projected" if size else "projected"
        head = f"  {num} - {f.company} {tag}"
    elif workforce:
        size = _headcount(f)
        num = f"{size:,} existing staff" if size else "existing staff"
        head = f"  {num} - {f.company} {tag}"
    else:
        head = f"  {f.company} {tag}"
    lines = [head, f"      {f.lead}"]
    when = (f"Announced {f.announced}" if f.announced
            else "Announcement date not stated")
    lines.append(f"      {when} · {f.location} · {f.industry}")
    facts = []
    if f.applications_open:
        facts.append("Applications open")
    if f.roles_stated:
        facts.append(f"Roles: {f.roles_stated}")
    if f.iso2:
        facts.append(f"Location: {f.location}")
    if f.applications_open or f.roles_stated:
        facts.append(f"tailor your CV: {dq.TAILOR_CV_URL}")
    if facts:
        lines.append("      " + " · ".join(facts))
    if f.original_note:
        lines.append(f"      original: {f.original_note}")
    elif f.needs_translation:
        lines.append("      (original language; no English summary on record)")
    lines.append(f"      {f.source_name} · {f.link}")
    return lines


# --- DB access (read-only) -------------------------------------------------

def _load(db_path, since, until):
    conn = schema.connect_ro(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM signals"
            " WHERE is_current = 1 AND captured_at >= ? AND captured_at < ?"
            + planned_hiring.public_sql() +
            " ORDER BY captured_at DESC",
            (since.isoformat(), until.isoformat())).fetchall()
        ytd_start = datetime(until.year, 1, 1, tzinfo=timezone.utc).isoformat()
        ytd_total = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE is_current = 1 AND captured_at >= ?"
            + planned_hiring.public_sql(),
            (ytd_start,)).fetchone()[0]
        return rows, ytd_total
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="path to talent_intel.db")
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD window start (UTC)")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD window end (UTC, exclusive)")
    ap.add_argument("--prev-ytd", type=int, default=None,
                    help="previous edition's YTD count; enables the backfill note")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    as_of = _parse_day(args.as_of).date() if args.as_of else now.date()
    if args.since and args.until:
        since, until = _parse_day(args.since), _parse_day(args.until)
    else:
        since, until = default_window(as_of)

    rows, ytd_total = _load(args.db, since, until)
    ed = build_edition(rows, since, until, now, ytd_total, prev_ytd=args.prev_ytd)
    sys.stdout.write(render(ed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
