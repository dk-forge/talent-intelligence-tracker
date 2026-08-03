"""Standalone-brand rule: commercial data-provider names stay out of the repo.

The four banned patterns are stored base64-encoded so that this file itself
carries none of them, and failure output masks the match so CI logs stay
clean too.

Some tracked files still carry the strings today and are exempted below,
each with the reason. The exemptions are a snapshot taken 2026-08-03, when
the catalogue rows and the handover doc were anonymized: the remaining
carriers are functional blocklist code (a domain must be spelled to be
blocked), the registry and its generated manifest, the tests of that
blocklist, historical logs, and captured label data. Shrinking this list is
an owner decision (the files are owned by other active work streams);
GROWING it is what this test exists to stop. Any new tracked file that
mentions a banned pattern fails here.

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
)
_BANNED = tuple(base64.b64decode(s).decode("ascii") for s in _ENCODED)

# Exact tracked paths that legitimately still carry a pattern (see module
# docstring). Paths only; no banned string appears in any path.
_EXEMPT = frozenset({
    # Hand-written registry entries (owner-supplied candidate catalogue).
    "source_registry.py",
    # The aggregator domain blocklist: a domain must be spelled to be blocked.
    "collectors/national_press.py",
    # Generated verbatim from source_registry.py; fix the registry first.
    "wordpress-plugin/talent-intelligence-tracker/data/sources.json",
    # Tests that pin the blocklist and its subdomain semantics.
    "tests/test_source_widening.py",
    "tests/test_national_press.py",
    "tests/test_sources_page.py",
    # Historical engineering log; scrubbing history is an owner decision.
    "docs/TECHLOG.md",
})

# Captured weak-label data: headlines and hosts recorded as observed.
_EXEMPT_PREFIXES = ("data/gate_labels/",)


def _tracked_files():
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def test_no_banned_provider_name_in_any_tracked_file():
    offenders = []
    for rel in _tracked_files():
        if rel in _EXEMPT or rel.startswith(_EXEMPT_PREFIXES):
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
