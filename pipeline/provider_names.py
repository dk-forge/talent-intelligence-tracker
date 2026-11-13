"""The standalone-brand rule, as a function rather than as a review habit.

`tests/test_no_provider_names.py` says no commercial data-provider name appears
in any tracked file. Authored files obey that because a human wrote them. The
gate ledger does not: `pipeline/gate_ledger.py` writes a committed JSONL line
for every candidate the gate looks at, built from live headlines, hosts and
teasers, so the moment a collector meets one of those publishers the name is in
the repo — automatically, twice a day, with nobody to notice.

That is exactly what happened. On 2026-08-13 `data/gate_labels/labels-2026-08
.jsonl` carried three of the banned names across eight lines (seven google_news
items whose `host` was the provider's own domain and whose headline repeated
it, one national_press teaser), main went red, and the failure was first seen
by somebody else's pull request.

So the scrub happens AT WRITE TIME, in the one module that writes the file.
Scrubbing after the fact is a chore somebody has to remember on a day nobody is
looking; this cannot be forgotten because nothing else writes a label line.

WHY REDACT AND NOT DROP
-----------------------
A ledger line is a gate DECISION, not a stored record. Dropping the eight lines
would bias the training set in exactly the direction that matters — a candidate
from an aggregator host is a candidate the gate should learn to refuse, and
those are the lines the classifier most needs. The provider's NAME is not the
feature; "this came from a data-aggregator host" is, and the placeholder carries
that perfectly well. So the name is replaced and the label survives.

This is also why the rule stops here and does not reach `pipeline/store.py`.
Stored rows are observations, and rewriting an observation is falsification —
`talent_intel.db` is exempt in the guard for that reason. A ledger line is our
own bookkeeping about our own decision, and redacting one loses nothing that was
ever evidence.

ENCODING, NOT SECRECY
---------------------
The patterns are held base64-encoded for the same reason
`collectors/national_press.py` holds its aggregator hosts that way: a grep of
the tree must surface no provider name, including from the file whose job is to
remove them. The list is duplicated in the guard test on purpose — a test that
imported its expectations from the code it guards would pass by agreement rather
than by checking. `test_the_scrubber_covers_every_banned_pattern` is what keeps
the two copies honest, and it is the test that fails if a seventh name is added
to the guard and not to here.
"""

from __future__ import annotations

import base64
import re

# base64 of the lowercase banned patterns. Kept in step with `_ENCODED` in
# tests/test_no_provider_names.py by that file's own coverage test.
_ENCODED = (
    "dHJhY3hu",
    "ZGVhbHJvb20=",
    "Y3J1bmNoYmFzZQ==",
    "cGl0Y2hib29r",
    "Y2JpbnNpZ2h0cw==",
    "Y2IgaW5zaWdodHM=",
)
BANNED = tuple(base64.b64decode(s).decode("ascii") for s in _ENCODED)

#: What a removed name leaves behind. Deliberately not a plausible word: in a
#: host it renders `app.[provider].co`, which reads as a redaction rather than
#: as a hostname somebody might try to fetch.
PLACEHOLDER = "[provider]"

# Longest first, so a pattern that is a prefix of another cannot win the
# alternation and leave the rest of the longer one in place.
_PATTERN = re.compile(
    "|".join(re.escape(p) for p in sorted(BANNED, key=len, reverse=True)),
    re.IGNORECASE,
)


def contains(text: str) -> bool:
    """True if `text` names a banned provider, case-insensitively."""
    return bool(_PATTERN.search(text or ""))


def scrub(text: str) -> str:
    """`text` with every banned provider name replaced by `PLACEHOLDER`."""
    return _PATTERN.sub(PLACEHOLDER, text or "")


def scrub_line(line: dict) -> bool:
    """Redact every string value of one ledger line, in place.

    Returns True if anything changed, so the caller can mark the line. Every
    string field is swept rather than a named few: a field added later would
    otherwise be an unscrubbed field, and the one thing this module exists to
    prevent is a write path nobody remembered to cover.
    """
    changed = False
    for field, value in line.items():
        if isinstance(value, str) and contains(value):
            line[field] = scrub(value)
            changed = True
    return changed
