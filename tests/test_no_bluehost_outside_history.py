"""No file may name Bluehost as the current host.

DK 2026-11-19: the web host is ChemiCloud (since about Aug 2026). Bluehost is
not used. Sessions kept repeating Bluehost because stale text said it.

Only dated history may still mention it: TECHLOG, HANDOVER, the resolved
writer-queue note, and comments that explain a guard by the 2026-07-31 outage.
Three plugin files keep old comments on purpose: changing them needs a plugin
version bump and a live deploy for a comment. Do not add a path to make this
pass: write ChemiCloud (or "the WP host") instead. Shrinking the list is fine.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

HISTORY_ALLOWLIST = frozenset({
    ".github/workflows/ci-alert.yml",
    ".github/workflows/deploy-plugin.yml",
    ".github/workflows/host-watch.yml",
    ".github/workflows/schedule-link-hygiene.yml",
    "CLAUDE.md",
    "alert_outbox.py",
    "backfill_slices.py",
    "ci_alert.py",
    "data/writer_queue.json",
    "docs/HANDOVER.md",
    "docs/TECHLOG.md",
    "gh_fallback.py",
    "host_watch.py",
    "ops_status.py",
    "opsmail.py",
    "tests/test_backfill_slices.py",
    "tests/test_ci_alert.py",
    "tests/test_host_watch.py",
    "tests/test_no_bluehost_outside_history.py",
    "wordpress-plugin/talent-intelligence-tracker/includes/db.php",
    "wordpress-plugin/talent-intelligence-tracker/includes/htaccess.php",
    "wordpress-plugin/talent-intelligence-tracker/includes/reference_render.php",
})


def _files_mentioning_bluehost() -> set[str]:
    out = subprocess.run(
        ["git", "grep", "-l", "-i", "-I", "bluehost"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert out.returncode in (0, 1), out.stderr
    return {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}


def test_bluehost_only_in_dated_history() -> None:
    offenders = sorted(_files_mentioning_bluehost() - HISTORY_ALLOWLIST)
    assert not offenders, f"web host is ChemiCloud (DK 2026-11-19): {offenders}"


def test_allowlist_paths_exist() -> None:
    missing = sorted(p for p in HISTORY_ALLOWLIST if not (ROOT / p).exists())
    assert not missing, f"remove stale allow-list entries: {missing}"
