"""The planned-hiring signal, and the one flag that keeps it off the page.

A state job-creation incentive award is a PROMISE: "will create 500 jobs" in
return for a grant or a tax credit. It is not a hire, it is not a job posting,
and it is not a site event in the sense vocab.SITE_EVENTS means (a model reads
those off news prose). So the value lives here, apart from the classifier
vocabulary, and only the collectors named in COLLECTORS may write it.

HIDDEN BY DEFAULT. Every public route (publish, enrich, the daily digest) adds
`public_sql()` to its query, and with PLANNED_HIRING_ENABLED unset or off that
clause removes every planned-hiring row, so the outputs are byte-identical to a
database that never held one (tests/test_state_incentives.py proves it).
Turning the flag on is the owner's call, never a session's.
"""

from __future__ import annotations

import os

FLAG = "PLANNED_HIRING_ENABLED"
SITE_EVENT = "planned_hiring"
HEADCOUNT_SCOPE = "promised"
LABEL = "planned hiring signal (state incentive award)"

#: The only collectors allowed to write SITE_EVENT / HEADCOUNT_SCOPE.
COLLECTORS = frozenset({"state_incentives"})

_ON = ("1", "true", "yes", "on")


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return (env.get(FLAG) or "").strip().lower() in _ON


def public_sql(column: str = "site_event") -> str:
    """An `AND ...` clause for any public query; empty when the flag is on."""
    if enabled():
        return ""
    return f" AND ({column} IS NULL OR {column} != '{SITE_EVENT}')"


def is_public(row: dict) -> bool:
    return enabled() or (row.get("site_event") != SITE_EVENT)
