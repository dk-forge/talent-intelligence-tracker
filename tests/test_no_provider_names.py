"""Standalone-brand rule: commercial data-provider names stay out of the repo.

The four banned patterns are stored base64-encoded so that this file itself
carries none of them, and failure output masks the match so CI logs stay
clean too.

Every git-tracked file is scanned, case-insensitive, except two collected
data files (exemptions below). Code that functionally needs one of the
strings (the aggregator blocklist must spell a domain to refuse it) stores
it base64-encoded and decodes at import time, so plaintext never appears in
a tracked source file.

Note: git HISTORY still holds the names in earlier revisions of the
anonymized files. A history rewrite is a separate owner decision.
"""
import base64
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# base64 of the lowercase banned patterns (names double as their domains'
# distinctive labels, so matching the name catches the domain too).
_ENCODED = (
    "dHJhY3hu",
    "ZGVhbHJvb20=",
    "Y3J1bmNoYmFzZQ==",
    "cGl0Y2hib29r",
    # Added 2026-08-03: the fifth provider slipped every scrub because the
    # original four patterns simply did not include it, and it stayed on the
    # LIVE sources page through two "complete" passes. Both spellings.
    "Y2JpbnNpZ2h0cw==",
    "Y2IgaW5zaWdodHM=",
)
_BANNED = tuple(base64.b64decode(s).decode("ascii") for s in _ENCODED)

# The ONLY exemptions: collected records. Wild headlines and stored rows that
# mention these companies are observations captured as they occurred, and
# rewriting records is falsification. Everything else in the tree is authored
# and must stay name-free.
_EXEMPT = frozenset({
    "data/gate_labels/bootstrap-weak.jsonl",
    "data/talent_intel.db",
})


def _tracked_files():
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def test_no_banned_provider_name_in_any_tracked_file():
    offenders = []
    for rel in _tracked_files():
        if rel in _EXEMPT:
            continue
        path = ROOT / rel
        if not path.is_file():  # deleted in worktree, submodule, etc.
            continue
        raw = path.read_bytes()
        if b"\x00" in raw[:8192]:  # binary
            continue
        text = raw.decode("utf-8", errors="replace").lower()
        for idx, pattern in enumerate(_BANNED):
            if pattern in text:
                # Mask the match: the name must not reach CI logs either.
                offenders.append(f"{rel} (banned pattern #{idx + 1})")
    assert not offenders, (
        "Banned data-provider name(s) found in tracked files "
        "(pattern numbers index the base64 list in "
        "tests/test_no_provider_names.py): " + "; ".join(sorted(offenders))
    )


def test_the_scrubber_covers_every_banned_pattern():
    """The write-time remedy must know every name this guard refuses.

    `pipeline/provider_names.py` keeps its own copy of the list, deliberately:
    a guard that imported its expectations from the code it guards would pass
    by agreement rather than by checking. This is the seam where the two copies
    are compared, and it is what fails when a seventh provider is added here
    and not there — which is precisely how the fifth one stayed in the tree
    through two "complete" passes.

    It matters because the ledger is the one file in this repo written from
    live text: on 2026-08-13 `data/gate_labels/labels-2026-08.jsonl` carried
    three of these names on eight lines that no human ever typed, and main was
    red for hours before anyone saw it.
    """
    from pipeline import provider_names

    missed = [f"pattern #{idx + 1}" for idx, pattern in enumerate(_BANNED)
              if provider_names.scrub(f"x {pattern} y") != "x [provider] y"]
    assert not missed, (
        "pipeline/provider_names.py does not scrub: " + ", ".join(missed)
        + " — add the same base64 entry to its _ENCODED tuple, or the gate "
        "ledger will write that name into a committed file automatically.")
