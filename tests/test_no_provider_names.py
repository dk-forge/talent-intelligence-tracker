"""Standalone-brand rule: commercial data-provider names stay out of the repo.

The four banned patterns are stored base64-encoded so that this file itself
carries none of them, and failure output masks the match so CI logs stay
clean too.

Every git-tracked file is scanned, case-insensitive, except the database
(exemption below). Code that functionally needs one of the
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
#
# NARROWED 2026-08-13 to the database alone. `data/gate_labels/
# bootstrap-weak.jsonl` was exempt on the reasoning above and the exemption was
# doing the opposite of its job: three provider names sat in that file, on a
# public repo, for as long as it has existed, and the one test that exists to
# find them was looking away by construction. They are gone now and no record
# went with them — `pipeline/provider_names.redact` swaps the name for an
# opaque tag and leaves the rest of the line standing, so the observation
# survives intact and the falsification argument never has to be made. The
# database stays exempt because it is binary (the scan skips it regardless) and
# because it is the system's memory rather than a published artifact.
_EXEMPT = frozenset({
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
