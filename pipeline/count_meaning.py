"""What a headcount NUMBER on a signal MEANS, and whether it is hiring at all.

The editorial review of two daily editions (2026-08-24) found one wrong number
repeated in several shapes: a workforce event covering EXISTING employees was
presented as the biggest hiring signal, by its headcount.

  - "La ONCE bate records con 80.000 profesionales" is 80,000 people ALREADY on
    staff, not 80,000 openings. It was the #1 row by headcount.
  - A GM tentative labour agreement covers 4,600 EXISTING employees. Not 4,600
    new jobs, and not hiring at all.
  - "500 jobs in Costa Rica by 2030" is a PROJECTION, not current openings.
  - "job board: 20 to 26" is 6 newly OBSERVED vacancies, not 6 hires.
  - A funding round or a leadership move carries no hiring count at all.

`headcount` alone can be summed, sorted and headlined as "jobs" only because
nobody asked what it counts. This module is the ONE place that decides, and it
decides DETERMINISTICALLY from columns already on the row: no model call, no
network, backward-computable over the whole table exactly like
`validate.compute_materiality` and `pipeline/money_raised.basis`.

The rule the whole thing rests on:

    a headcount counts toward a CURRENT "jobs"/roles figure only when the scope
    is `new_roles`, the direction is `hiring`, AND the figure is not a future
    projection.

Everything else is NAMED for what it is and kept out of the figure. A
`total_workforce`, `single_site` or `affected` scope describes existing people,
so it never contributes to a hiring total however large the number is; a
projected figure is labelled projected rather than shown as an opening; a
funding or leadership row has no hiring count to show.

This module classifies; it does not display. `daily_digest.py` renders the
labels, and the site can read the same verdict off the same columns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- The taxonomy ----------------------------------------------------------
#
# Six values. Five are the editorial review's own distinctions; `other` is the
# honest sixth for a row that carries no headcount and is neither funding nor a
# leadership move (a bare site opening, an RTO policy).

CONFIRMED_HIRES = "confirmed_hires"      # new roles, hiring, actually happening
OPEN_VACANCIES = "open_vacancies"        # advertised/observed openings (a job board)
PLANNED_JOBS = "planned_jobs"            # a future or conditional figure ("500 by 2030")
WORKFORCE_EVENT = "workforce_event"      # existing employees: total/site/affected, or non-hiring
FUNDING_OR_LEADERSHIP = "funding_or_leadership"  # no direct hiring count
OTHER = "other"                          # no headcount, not funding/leadership

TYPE_LABELS = {
    CONFIRMED_HIRES: "Confirmed hires",
    OPEN_VACANCIES: "Open vacancies",
    PLANNED_JOBS: "Planned jobs",
    WORKFORCE_EVENT: "Workforce event",
    FUNDING_OR_LEADERSHIP: "Funding / leadership",
    OTHER: "Other signal",
}

#: Scopes that describe people ALREADY employed. A number with one of these
#: scopes is never a hiring/openings figure, whatever its size or the model's
#: `signal_direction`. This is the ONCE 80,000 and the GM 4,600 case.
EXISTING_EMPLOYEE_SCOPES = ("total_workforce", "single_site", "affected")

#: The only scope a current roles/openings figure may carry.
HIRING_SCOPE = "new_roles"


# --- Projection detection --------------------------------------------------
#
# A future or conditional figure. The markers are deliberately future-tense or
# horizon phrases, NOT the bare verb "hire": "is hiring 7,500 positions" is a
# present opening, while "plans to hire 50,000", "will hire", "to hire ... by
# 2030" and "over the next five years" are projections. False negatives (a
# projection we fail to flag) are safer than false positives (a real present
# opening mislabelled), so the list stays conservative and specific.
#
# Spanish / Italian / Portuguese / French markers are included because the
# corpus is multilingual even though the English `summary` usually restates the
# figure; matching either side is enough.
_PROJECTION_MARKERS = re.compile(
    r"""
      \bby\s+20\d\d\b                                   # by 2030
    | \b(?:over|within|in)\s+the\s+(?:next|coming)\b    # over the next ...
    | \b(?:next|coming)\s+\d+\s+year                    # next five years
    | \bin\s+\d+\s+year                                 # in 5 years
    | \bplan(?:s|ned|ning)?\s+to\b                      # plans to / planning to
    | \bplan(?:s|ned)?\s+to\s+(?:hire|create|add)\b
    | \bwill\s+(?:hire|create|add|recruit)\b            # will hire
    | \bto\s+(?:hire|create|add|recruit)\b              # Deloitte to hire 50,000
    | \baim(?:s|ing)?\s+to\b                            # aims to
    | \bintend(?:s|ing)?\s+to\b
    | \bexpect(?:s|ed|ing)?\s+to\s+(?:hire|create|add|reach)\b
    | \btarget(?:s|ing|ed)?\s+(?:more\s+than\s+)?\d     # targets 14,000
    | \bset\s+to\s+(?:hire|create|add)\b
    | \bprojected\b | \bprojection\b
    | \bproyecta\b | \bprev[ée]\b | \bplanea\b          # es: proyecta / prevé / planea
    | \bgenerar[aá]?\b | \bcrear[aá]?\b                 # es: generará / creará
    | \bassumer[aà]\b | \bcreer[aà]\b                   # it/fr: assumerà / créera
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _is_projection(text: str) -> bool:
    return bool(_PROJECTION_MARKERS.search(text or ""))


# --- Source class, for the "primary source" question -----------------------
#
# The editorial review asked why an edition read "0 of 34 primary-verified" when
# employer job boards (Ashby / Greenhouse / Lever) are direct employer evidence.
# The answer is that "confidence" (verified/reported/rumored) and "who published
# it" (first party / news) are DIFFERENT axes, and confusing them is what makes
# a first-party board look like unsourced noise.
#
#   confidence == 'verified'  is reserved for a FILED primary document (an SEC
#     8-K, a company register), because that is a number the employer PUBLISHED.
#   an ATS board is the employer's OWN publication, so it is first-party, but the
#     "20 to 26" count is OUR measurement of that board on two dates, not a
#     figure the employer filed -- honestly `reported`, per ats_boards.py.
#
# So a first-party employer board is first-party AND `reported`, both true, and a
# digest that shows only "primary-verified" hides that. We surface first_party as
# its own flag rather than promoting the confidence tier (which would overstate a
# measured delta as a filed figure).

#: Collectors that read an employer's OWN publication (its careers board).
_FIRST_PARTY_COLLECTORS = frozenset({"ats_boards"})


def _is_first_party(collector: str, source_name: str, primary: bool) -> bool:
    if primary:
        return True
    if (collector or "") in _FIRST_PARTY_COLLECTORS:
        return True
    # Belt and braces: the ATS items name themselves "... job board".
    return bool(re.search(r"\bjob board\b", source_name or "", re.IGNORECASE))


def _is_job_board(collector: str, source_name: str) -> bool:
    if (collector or "") in _FIRST_PARTY_COLLECTORS:
        return True
    return bool(re.search(r"\bjob board\b", source_name or "", re.IGNORECASE))


@dataclass(frozen=True)
class CountMeaning:
    """The verdict on one signal's headcount.

    `type`               one of the six constants above.
    `label`              display string for `type`.
    `roles`              the headcount when, and ONLY when, it counts toward a
                         current roles/openings figure; None otherwise. This is
                         the number a "jobs"/"roles named" total may sum. A
                         workforce event and a projection both return None here.
    `counts_as_roles`    whether the headcount is a current openings figure.
    `hiring_intent`      whether the row is about hiring at all (confirmed, open
                         OR planned) -- planned counts here but not in `roles`.
    `projected`          the figure is future/conditional.
    `primary`            confidence == 'verified' (a filed primary document).
    `first_party`        the fact comes from the employer's own publication.
    `reason`             short human string, for a tooltip or a test message.
    """

    type: str
    label: str
    roles: int | None
    counts_as_roles: bool
    hiring_intent: bool
    projected: bool
    primary: bool
    first_party: bool
    reason: str


def classify(row) -> CountMeaning:
    """Classify one signal. `row` is anything indexable by the column names.

    Reads only stored columns: headcount, headcount_scope, signal_direction,
    pillar, confidence, collector, source_name, funding_amount, headline,
    summary, talent_readthrough, effective_date. Missing keys are tolerated.
    """

    def g(key, default=None):
        try:
            v = row[key]
        except (KeyError, IndexError, TypeError):
            return default
        return default if v is None else v

    headcount = g("headcount")
    try:
        headcount = int(headcount) if headcount not in (None, "") else None
    except (TypeError, ValueError):
        headcount = None

    scope = (g("headcount_scope") or "").strip().lower() or None
    direction = (g("signal_direction") or "").strip().lower()
    pillar = (g("pillar") or "").strip().lower()
    confidence = (g("confidence") or "").strip().lower()
    collector = g("collector") or ""
    source_name = g("source_name") or ""
    funding = (g("funding_amount") or "").strip()

    primary = confidence == "verified"
    first_party = _is_first_party(collector, source_name, primary)

    text = " ".join(str(g(k, "")) for k in
                    ("headline", "summary", "talent_readthrough"))
    projected_text = _is_projection(text)
    # A stated future effective date is a projection even if the prose is terse.
    projected = projected_text or bool(g("effective_date"))

    # 1. No headcount at all: this is a funding or leadership signal, or another
    #    signal with no hiring number to show. It NEVER contributes to a roles
    #    figure, which is the whole point -- funding and leadership were being
    #    listed under "hiring signals" with a count that was not a hiring count.
    if not headcount or headcount <= 0:
        if funding or pillar == "rewards_comp":
            t = FUNDING_OR_LEADERSHIP
            reason = "no headcount; a funding / comp signal"
        elif pillar == "leadership_change":
            t = FUNDING_OR_LEADERSHIP
            reason = "no headcount; a leadership move"
        else:
            t = OTHER
            reason = "no headcount stated"
        return CountMeaning(
            type=t, label=TYPE_LABELS[t], roles=None, counts_as_roles=False,
            hiring_intent=False, projected=False, primary=primary,
            first_party=first_party, reason=reason)

    # 2. The headcount describes EXISTING employees, so it is a workforce event,
    #    not hiring -- the ONCE 80,000 and the GM 4,600 case. This is decided by
    #    the SCOPE, before and regardless of the model's `signal_direction`,
    #    because "80.000 profesionales" was stored direction=hiring and is still
    #    a count of people already on staff.
    if scope in EXISTING_EMPLOYEE_SCOPES:
        return CountMeaning(
            type=WORKFORCE_EVENT, label=TYPE_LABELS[WORKFORCE_EVENT],
            roles=None, counts_as_roles=False, hiring_intent=False,
            projected=False, primary=primary, first_party=first_party,
            reason=f"headcount scope '{scope}' counts existing employees, "
                   f"not new roles")

    # 3. A non-hiring direction with a number: a displacement/neutral/comp row
    #    that happens to carry a headcount. Also a workforce event, never a
    #    hiring figure.
    if direction and direction != "hiring":
        return CountMeaning(
            type=WORKFORCE_EVENT, label=TYPE_LABELS[WORKFORCE_EVENT],
            roles=None, counts_as_roles=False, hiring_intent=False,
            projected=projected, primary=primary, first_party=first_party,
            reason=f"signal_direction '{direction}' is not hiring")

    # From here the row is a genuine hiring figure of NEW roles (scope is
    # new_roles or unstated-but-hiring). Which of the three hiring types?

    # 4. Projected / planned: a future or conditional figure. Labelled projected
    #    and kept OUT of the current-openings figure. "500 by 2030",
    #    "plans to hire 50,000".
    if projected:
        return CountMeaning(
            type=PLANNED_JOBS, label=TYPE_LABELS[PLANNED_JOBS],
            roles=None, counts_as_roles=False, hiring_intent=True,
            projected=True, primary=primary, first_party=first_party,
            reason="a future or conditional hiring figure")

    # 5. Open vacancies: a job-board measurement. Newly OBSERVED openings, not
    #    hires -- "job board: 20 to 26" is 6 openings that appeared, and a role
    #    leaving the board may have been filled OR withdrawn.
    if _is_job_board(collector, source_name):
        return CountMeaning(
            type=OPEN_VACANCIES, label=TYPE_LABELS[OPEN_VACANCIES],
            roles=headcount, counts_as_roles=True, hiring_intent=True,
            projected=False, primary=primary, first_party=True,
            reason="observed openings on the employer's own job board")

    # 6. Confirmed hires: new roles, hiring, present tense, from a filing or an
    #    outlet reporting it as fact.
    return CountMeaning(
        type=CONFIRMED_HIRES, label=TYPE_LABELS[CONFIRMED_HIRES],
        roles=headcount, counts_as_roles=True, hiring_intent=True,
        projected=False, primary=primary, first_party=first_party,
        reason="new roles, hiring")
