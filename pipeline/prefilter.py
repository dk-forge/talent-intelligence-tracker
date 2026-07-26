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
    r"salar\w+", r"pay(?:rise|\srise)?", r"wages?", r"bonus\w*", r"compensation",
    r"remote work", r"hybrid work\w*", r"return to office", r"four-day week",
)
_EMPLOYMENT = re.compile(r"\b(?:" + "|".join(_EMPLOYMENT_TERMS) + r")\b", re.I)

# Site-establishment terms. A company opening a capability centre IS a hiring
# event, even when the headline never says "jobs" — and this is precisely the
# phrasing the standalone euphemism queries exist to surface. The first version
# of this filter dropped every one of them, which would have made those queries
# dead on arrival exactly as the sibling's did.
#
# "GCC" is deliberately absent: it is also the Gulf Cooperation Council.
_SITE_TERMS = (
    r"capability cent(?:re|er)s?", r"cent(?:re|er)s? of excellence",
    r"delivery cent(?:re|er)s?", r"shared services", r"tech(?:nology)? cent(?:re|er)s?",
    r"engineering cent(?:re|er)s?", r"r&d cent(?:re|er)s?", r"innovation cent(?:re|er)s?",
    r"development cent(?:re|er)s?", r"opens? (?:a |its |new )?(?:office|hub|campus|site)",
    r"sets? up (?:a |its |new )?(?:office|hub|centre|center)",
    r"new (?:office|hub|campus|facility|plant|site)",
)
_SITE = re.compile(r"\b(?:" + "|".join(_SITE_TERMS) + r")\b", re.I)

# Domains where "expansion", "hiring" and "roster" mean something else entirely.
# Cheap to check, and they were most of the noise in the first live run.
_OFF_TOPIC_TERMS = (
    r"nba", r"nfl", r"mlb", r"wnba", r"nhl", r"premier league", r"playoffs?",
    r"franchise", r"roster", r"draft pick", r"touchdown", r"season opener",
    r"medicaid", r"medicare", r"nuclear weapons?", r"ceasefire", r"airstrikes?",
    r"herd", r"cattle", r"livestock", r"acreage",
    r"world of warcraft", r"dlc", r"expansion pack", r"video game",
)
_OFF_TOPIC = re.compile(r"\b(?:" + "|".join(_OFF_TOPIC_TERMS) + r")\b", re.I)


def passes(text: str) -> tuple[bool, str]:
    """Return (keep, reason). Reason is empty when kept."""
    if not text or not text.strip():
        return False, "empty text"

    # Word-boundary matching, not substring: the sibling's equivalent loop went
    # inert for a day because "RIF" matched inside "tariff".
    if _OFF_TOPIC.search(text):
        hit = _OFF_TOPIC.search(text).group(0)
        return False, f"off-topic domain ({hit})"

    if not (_EMPLOYMENT.search(text) or _SITE.search(text)):
        return False, "no employment or site-opening term"

    return True, ""
