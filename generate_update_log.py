#!/usr/bin/env python3
"""Build the reader-facing update log (/talent-intelligence-tracker/update-log/).

Source of truth: git. A plugin release is a commit that changes `TIT_VERSION`
in the plugin's main file (the deploy needs that bump to flush rewrites, so it
is the one reliable record of "a release shipped"). For each such commit this
reads the version FROM THAT COMMIT'S COPY of the file, takes the date, and
cleans the subject line into a sentence a reader can follow.

Writes wordpress-plugin/talent-intelligence-tracker/data/update-log.json, which
includes/update_log.php renders. The PHP never reads git: the deployed plugin
has no repository.

    python3 generate_update_log.py                      # regenerate from git
    python3 generate_update_log.py --pending "What this release changes"

`--pending` adds an entry for a version that is in the working tree but not yet
committed (the release you are about to make), dated today. Without it that
release would have no entry until after it merged, and the guard in
tests/test_update_log.py (newest entry == TIT_VERSION) would fail the PR that
bumps the version. After the merge, a plain regeneration replaces it with the
commit's own subject.

Needs full history. A shallow clone sees only the newest commits, so the script
refuses to write fewer entries than the file already holds: a log that silently
shrinks is the failure this guards against.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN_FILE = "wordpress-plugin/talent-intelligence-tracker/talent-intelligence-tracker.php"
OUT = ROOT / "wordpress-plugin" / "talent-intelligence-tracker" / "data" / "update-log.json"

_VERSION_RE = re.compile(r"define\('TIT_VERSION',\s*'([0-9]+\.[0-9]+\.[0-9]+)'\)")
_PR_RE = re.compile(r"\s*\(#(\d+)\)\s*$")
# "plugin 1.99.2:", "v1.100.0 —", "vNEXT —", "1.114.0 feat:", "1.108.0 -"
_LEAD_VERSION_RE = re.compile(
    r"^(?:plugin\s+|v)?(?:\d+\.\d+\.\d+|NEXT)\s*(?:[:—–-]+\s*)?", re.I)
_LEAD_PLUGIN_RE = re.compile(r"^plugin\s*[:—–-]\s*", re.I)
_TRAIL_VERSION_RE = re.compile(r"\s*\(v?\d+\.\d+\.\d+\)\s*$")
_CONVENTIONAL_RE = re.compile(r"^(feat|fix|chore|docs|refactor|perf|seo|design)(\([^)]*\))?!?:\s*", re.I)


def parse_version(text: str) -> str | None:
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


def clean_subject(subject: str) -> tuple[str, str, int | None]:
    """(tag, sentence, pr number) from a commit subject. Pure."""
    s = subject.strip()
    pr = None
    m = _PR_RE.search(s)
    if m:
        pr = int(m.group(1))
        s = s[: m.start()]
    # Peel in a loop: "vNEXT — fix(security): ..." has two prefixes.
    for _ in range(3):
        before = s
        s = _LEAD_VERSION_RE.sub("", s).strip()
        s = _LEAD_PLUGIN_RE.sub("", s).strip()
        if s == before:
            break
    is_fix = bool(re.search(r"\bfix(?:es|ed)?\b", s, re.I))
    s = _CONVENTIONAL_RE.sub("", s).strip()
    s = _TRAIL_VERSION_RE.sub("", s).strip()
    s = s.rstrip(" .;:—-")
    if s:
        s = s[0].upper() + s[1:]
    if s and not s.endswith((".", "?", "!")):
        s += "."
    return ("Fix" if is_fix else "Change"), s, pr


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout


def entries_from_git(ref: str = "HEAD") -> list[dict]:
    log = _git("log", ref, "-G", r"define\('TIT_VERSION'", "--format=%H%x1f%ad%x1f%s",
               "--date=short", "--", PLUGIN_FILE)
    out: list[dict] = []
    for line in log.splitlines():
        sha, day, subject = line.split("\x1f", 2)
        try:
            version = parse_version(_git("show", f"{sha}:{PLUGIN_FILE}"))
        except subprocess.CalledProcessError:
            continue
        if not version:
            continue
        tag, text, pr = clean_subject(subject)
        if not text:
            continue
        entry = {"version": version, "date": day, "tag": tag, "text": text}
        if pr:
            entry["pr"] = pr
        out.append(entry)
    return out


def _key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def build(entries: list[dict]) -> list[dict]:
    """One entry per version (the earliest commit that introduced it wins, which
    is the release; a later commit that only re-touches the constant is not a
    second release), newest version first. Pure."""
    seen: dict[str, dict] = {}
    for e in reversed(entries):          # git log is newest first; walk oldest first
        seen.setdefault(e["version"], e)
    return sorted(seen.values(), key=lambda e: _key(e["version"]), reverse=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pending", help="sentence for the uncommitted working-tree release")
    ap.add_argument("--ref", default="HEAD")
    a = ap.parse_args(argv)

    entries = entries_from_git(a.ref)
    current = parse_version((ROOT / PLUGIN_FILE).read_text(encoding="utf-8"))
    if current and current not in {e["version"] for e in entries}:
        if not a.pending:
            print(f"working tree is at {current}, which no commit has released yet; "
                  f"pass --pending 'what it changes'", file=sys.stderr)
            return 2
        tag, text, _ = clean_subject(a.pending)
        entries.insert(0, {"version": current, "date": date.today().isoformat(),
                           "tag": tag, "text": text})
    log = build(entries)

    if OUT.exists():
        previous = json.loads(OUT.read_text(encoding="utf-8")).get("entries", [])
        if len(log) < len(previous):
            print(f"refusing to shrink the log from {len(previous)} to {len(log)} entries; "
                  f"is this a shallow clone? (git fetch --unshallow)", file=sys.stderr)
            return 3
    payload = {
        "generated_from": "git history of TIT_VERSION in the plugin's main file",
        "entries": log,
    }
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(log)} releases, newest {log[0]['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
