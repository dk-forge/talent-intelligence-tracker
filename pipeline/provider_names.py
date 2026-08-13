"""The standalone-brand rule, as a function a write path can call.

WHY THIS EXISTS
---------------
`tests/test_no_provider_names.py` says no commercial data-provider name may
appear in any tracked file. That test is a DETECTOR: it tells you a name got
in, hours after a bot committed it, and on 2026-08-13 it told nobody at all
(main's `tests` never runs on a bot push — see `.github/workflows/tests.yml`).

The names do not get in by being typed. They get in because a collector
captures a real headline, or a real publisher host, and something appends that
text to a tracked data file. `data/gate_labels/labels-2026-08.jsonl` picked up
three different providers across 8 of its 13,455 lines that way, in two commits
eight hours apart, without a human touching either.

So the guard has to live at the WRITE, not after it. `analysis/ranking/
gold_bucket.py` reached the same conclusion on 2026-08-12 and dropped the free
text entirely, keeping a sha1 prefix — right for that file, where the headline
was only ever context for a verdict.

IT IS WRONG HERE, AND THAT IS THE WHOLE DESIGN DECISION
-------------------------------------------------------
The gate ledger's `headline` and `teaser` ARE the payload. `train_gate_
classifier.features(headline, teaser)` is the only thing the local classifier
is ever fitted on, and that classifier is the entire route to the owner's $5
target. Dropping the text to satisfy the rule would leave 13,455 verdicts
attached to nothing to learn from — the rule kept and the dataset destroyed.

So this REDACTS rather than drops: each banned occurrence becomes an opaque,
stable tag derived from the name's own sha1. The sentence around it survives
intact, and two different providers stay two different tokens, so a classifier
learns whatever was there to learn without the file spelling anybody's name.
It is the precedent's reasoning (opaque prefix, no plaintext) applied to a file
whose free text is not decoration.

The patterns are held base64-encoded, so a grep of this tree surfaces no name
here either — the same convention `collectors/national_press.py` uses for its
aggregator blocklist. Encoding, not secrecy: nothing here is a secret, the
point is only that the plaintext never lands in a tracked file.

Keep this list identical to the one in `tests/test_no_provider_names.py`.
`tests/test_provider_names.py` fails if the two ever drift, because a name the
redactor does not know is a name the detector will find tomorrow.
"""

from __future__ import annotations

import base64
import hashlib
import re

# base64 of the lowercase banned patterns. Same six as the guard test.
_ENCODED = (
    "dHJhY3hu",
    "ZGVhbHJvb20=",
    "Y3J1bmNoYmFzZQ==",
    "cGl0Y2hib29r",
    "Y2JpbnNpZ2h0cw==",
    "Y2IgaW5zaWdodHM=",
)

BANNED = tuple(base64.b64decode(s).decode("ascii") for s in _ENCODED)

#: What replaces a hit. Short, obviously not a word, and stable across runs so
#: the same provider always folds to the same token — a classifier can use it,
#: a reader can tell two of them apart, and neither can recover the name.
TAG_PREFIX = "dp"


def tag(name: str) -> str:
    return f"[{TAG_PREFIX}-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:6]}]"


# Longest first: the two spellings of one provider overlap, and replacing the
# short one first would leave the rest of the long one in the text.
_PATTERN = re.compile(
    "|".join(re.escape(p) for p in sorted(BANNED, key=len, reverse=True)),
    re.IGNORECASE,
)


def redact(text: str) -> str:
    """`text` with every banned provider name replaced by its opaque tag.

    Case-insensitive, because a headline capitalises a company name and a host
    does not. Returns the input unchanged when it holds none, which is all but
    a fraction of a percent of the lines this runs on.
    """
    if not text:
        return text or ""
    return _PATTERN.sub(lambda m: tag(m.group(0).lower()), text)


def contains(text: str) -> bool:
    """Whether `text` holds a banned name. For tests and for callers that want
    to count rather than rewrite; the write paths call `redact` unconditionally
    because a check that can be skipped eventually is."""
    return bool(text) and bool(_PATTERN.search(text))
