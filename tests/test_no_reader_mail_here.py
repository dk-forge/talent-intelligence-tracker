"""This repository sends NO reader mail; the subscriber design lives in the sibling.

Board item "Finish tracker email redesign (both trackers)", 2026-10-13. The
talent section of every subscriber email is composed by ai-layoff-tracker's
`alt_digest_compose_talent` and rendered by its `railway/digest_layout.py`
(blue #1d4ed8 accent), and the confirm/welcome emails live there too. The only
mail this repo sends is owner-only operational mail (`/alert` in api.php,
`opsmail.py`). If someone adds a reader email here it must get the shared
design, the postal footer and the unsubscribe/preferences links, so this test
fails first and points them at the sibling.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wordpress-plugin" / "talent-intelligence-tracker"


def test_wp_mail_is_only_the_owner_alert_route():
    callers = {}
    for php in PLUGIN.rglob("*.php"):
        n = len(re.findall(r"\bwp_mail\s*\(", php.read_text(encoding="utf-8", errors="ignore")))
        if n:
            callers[php.relative_to(PLUGIN).as_posix()] = n
    assert set(callers) == {"includes/api.php"}, callers
    src = (PLUGIN / "includes" / "api.php").read_text(encoding="utf-8")
    for m in re.finditer(r"wp_mail\s*\(([^,]+),\s*'\[Talent Intelligence Tracker\] '", src):
        assert m.group(1).strip() == "$to"
    assert len(re.findall(r"wp_mail\s*\(\s*\$to\s*,\s*'\[Talent Intelligence Tracker\] '", src)) == callers["includes/api.php"]


def test_no_suite_number_in_any_mail_source():
    # Owner 2026-09-29: the suite number is left out of every email.
    for path in list(PLUGIN.rglob("*.php")) + list(ROOT.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "#E313" not in text, path
