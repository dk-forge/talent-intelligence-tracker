#!/usr/bin/env python3
"""Derive the public "next run" schedule from the REAL collect cron.

The dashboard strip promises a next-collection time (Roo's "Next run ..." note)
and the FAQ states the cadence. Both promises must come from
`.github/workflows/collect.yml`, the cron that actually runs, never from typed
copy: the typed "06:00 and 18:00 UTC" kept promising a 6 AM run for hours after
the schedule moved to once daily at 16:00 UTC (df0efdf), and it would lie again
the next time the schedule moves. The sibling tracker shipped the same defect
and the same fix (its generate_ingest_schedule.py); this is that pattern ported.

Writes wordpress-plugin/talent-intelligence-tracker/data/ingest-schedule.json,
which the plugin reads (tit_ingest_schedule in includes/shortcodes.php). If the
file is missing or malformed, every consumer renders NOTHING there: an absent
schedule is honest, a stale typed one is not (same contract as data/recall.json).

collect.yml is the schedule of record on purpose. collect-press.yml trails it
by an hour under the same owner decision, so "when does collection run" has one
answer, and a second promise line an hour later would be noise, not honesty.

Output is deterministic (no timestamp), so a regen with an unchanged cron is a
byte-for-byte no-op. tests/test_ingest_schedule.py fails when the committed
JSON drifts from collect.yml, so the cron cannot move without this file
following it.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / ".github" / "workflows" / "collect.yml"
OUT = (ROOT / "wordpress-plugin" / "talent-intelligence-tracker"
       / "data" / "ingest-schedule.json")

_CRON = re.compile(r"^\s*-\s*cron:\s*['\"]([^'\"]+)['\"]", re.M)


def parse_cron_schedule(workflow_text: str) -> dict:
    """Extract collect.yml's cron lines and reduce them to daily UTC hours.

    Only the shape this schedule actually uses is supported (fixed minute,
    plain hour or comma list, every day). Anything else raises: a silent
    fallback here would put a wrong promise on the flagship page.
    """
    crons = _CRON.findall(workflow_text)
    if not crons:
        raise ValueError("no `- cron:` line found in collect.yml")

    hours: list[int] = []
    minute = None
    for cron in crons:
        fields = cron.split()
        if len(fields) != 5:
            raise ValueError(f"unexpected cron shape: {cron!r}")
        minute_f, hour_f, dom, month, dow = fields
        if (dom, month, dow) != ("*", "*", "*"):
            raise ValueError(
                f"schedule is not simple-daily, refusing to summarise: {cron!r}")
        if not re.fullmatch(r"\d{1,2}", minute_f):
            raise ValueError(f"unsupported minute field: {minute_f!r}")
        m = int(minute_f)
        if not 0 <= m <= 59:
            raise ValueError(f"minute out of range: {m}")
        if minute is None:
            minute = m
        elif minute != m:
            raise ValueError("cron lines disagree about the minute; refusing "
                             "to summarise a schedule with two minute hands")
        for h in hour_f.split(","):
            if not re.fullmatch(r"\d{1,2}", h):
                raise ValueError(f"unsupported hour field: {hour_f!r}")
            hv = int(h)
            if not 0 <= hv <= 23:
                raise ValueError(f"hour out of range: {hv}")
            hours.append(hv)

    if not hours:
        raise ValueError("no hours found in collect.yml crons")
    return {
        "cron": "; ".join(crons),
        "utc_hours": sorted(set(hours)),
        "utc_minute": minute,
        "cadence": "daily",
        "source": ".github/workflows/collect.yml on.schedule cron",
    }


def main() -> int:
    schedule = parse_cron_schedule(WORKFLOW.read_text(encoding="utf-8"))
    rendered = json.dumps(schedule, indent=2, sort_keys=True) + "\n"
    if OUT.exists() and OUT.read_text(encoding="utf-8") == rendered:
        print(f"unchanged: {OUT}")
        return 0
    OUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUT}: {schedule['cron']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
