#!/usr/bin/env python3
"""Render README's collection facts from the config that actually runs.

Cadence comes from the workflow crons (collection_schedule.py), the budget from
spend.MONTHLY_ALLOWANCE_USD (read as text, so this needs no dependencies), and
the keyed sources from the `secrets.*` each collector workflow maps. The README
carries the output between two markers; tests/test_readme_facts.py fails when
the committed copy drifts.

    python3 readme_facts.py            # print the block
    python3 readme_facts.py --write    # splice it into README.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import collection_schedule as cs

ROOT = Path(__file__).resolve().parent
README = ROOT / "README.md"
BEGIN = "<!-- generated:readme_facts BEGIN (python3 readme_facts.py --write) -->"
END = "<!-- generated:readme_facts END -->"

#: Secrets that are infrastructure (publishing, error reporting, mail), not a
#: data source's key. Everything else a collector workflow maps is a key a
#: source needs, and is listed.
_INFRA = {"WP_API_KEY", "WP_SITE_URL", "SENTRY_DSN", "RESEND_API_KEY"}

#: What each key unlocks, for the reader. A key with no entry here still
#: appears (the list is derived), just without a description.
_KEY_USE = {
    "OPENROUTER_API_KEY": "LLM classification of news candidates, and the tripwire's search-backed queries",
    "EDINET_API_KEY_JP": "EDINET (Japan) filings",
    "OPENDART_API_KEY_KR": "OpenDART (Korea) filings",
    "COMPANIES_HOUSE_API_KEY_UK": "Companies House (UK) officer appointments",
    "DENMARK_DATA_USER": "Denmark CVR (dormant; secret not yet set)",
    "DENMARK_DATA_PASSWORD": "Denmark CVR (dormant; secret not yet set)",
}
_COLLECTOR_WORKFLOWS = ("collect.yml", "collect-press.yml", "collect-structured.yml",
                        "tripwire.yml")

_DOW = ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays"]


def monthly_allowance() -> float:
    text = (ROOT / "spend.py").read_text()
    return float(re.search(r"^MONTHLY_ALLOWANCE_USD\s*=\s*([\d.]+)", text, re.M).group(1))


def describe(cron: str) -> str:
    minute, hour, dom, _mon, dow = cron.split()
    at = f"{int(hour):02d}:{int(minute):02d} UTC"
    if dom != "*":
        return f"monthly on day {dom} at {at}"
    if dow != "*":
        return f"weekly, {_DOW[int(dow) % 7]} at {at}"
    return f"daily at {at}"


def keyed_secrets() -> list[str]:
    found: list[str] = []
    for name in _COLLECTOR_WORKFLOWS:
        path = cs.WORKFLOWS / name
        if not path.exists():
            continue
        for key in re.findall(r"secrets\.([A-Z0-9_]+)", path.read_text()):
            if key not in _INFRA and key not in found:
                found.append(key)
    return found


def render() -> str:
    srcs = cs.configured_sources()
    lines = [BEGIN, "",
             "**Schedule** (from the workflow crons; a missed weekly or monthly slot "
             "is re-queued the next morning by `catch-up.yml`, and every collector "
             "falls back to a GitHub-hosted runner when the self-hosted one stops "
             "answering):", "",
             "| Source | Workflow | Runs |", "|---|---|---|"]
    order = sorted(srcs.values(), key=lambda s: (s.cadence_hours, s.workflow, s.name))
    for s in order:
        lines.append(f"| `{s.name}` | `{s.workflow}` | {describe(s.cron)} |")
    lines += ["",
              f"**Cost.** LLM spend is capped at **${monthly_allowance():.2f}/month** "
              "(`spend.MONTHLY_ALLOWANCE_USD`); past 90% of it paid reads switch off "
              "and the free stages keep running. Run `python3 spend.py` for this "
              "month's actual figure and `python3 cost_projection.py` for demand.",
              "",
              "**Keys.** Most sources are keyless open data. These need a key "
              "(repository secrets, never committed):", ""]
    for key in keyed_secrets():
        use = _KEY_USE.get(key, "")
        lines.append(f"- `{key}`" + (f": {use}" if use else ""))
    lines += ["", END]
    return "\n".join(lines)


def splice(text: str) -> str:
    start, end = text.index(BEGIN), text.index(END) + len(END)
    return text[:start] + render() + text[end:]


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--write" in argv:
        README.write_text(splice(README.read_text()))
        print("README.md facts block regenerated")
    else:
        print(render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
