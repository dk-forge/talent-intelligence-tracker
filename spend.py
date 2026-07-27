#!/usr/bin/env python3
"""Report what we have actually spent, and enforce a ceiling.

Every cost figure in this project has been arithmetic from published prices.
OpenRouter's key endpoint reports real usage against the real limit, so this
turns the budget from a forecast into a measurement — and, with --enforce, into
something the pipeline cannot exceed.

    python spend.py             # report
    python spend.py --enforce   # exit 1 if over the monthly allowance
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

KEY_URL = "https://openrouter.ai/api/v1/auth/key"
USER_AGENT = "TalentIntel/1.0 (+https://asktherecruiter.com)"

# The budget the owner set. Kept here rather than in a secret so it is
# reviewable in a diff — it is a policy, not a credential.
MONTHLY_ALLOWANCE_USD = 10.0

# Stop collecting with headroom left, so a long run cannot overshoot mid-batch.
STOP_AT_FRACTION = 0.9


def fetch() -> dict:
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    resp = requests.get(
        KEY_URL,
        headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise SystemExit(f"OpenRouter {resp.status_code}: {resp.text[:200]}")
    return resp.json().get("data") or {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Report and enforce LLM spend.")
    parser.add_argument("--enforce", action="store_true",
                        help="exit non-zero when the allowance is exhausted")
    args = parser.parse_args()

    d = fetch()
    used = float(d.get("usage") or 0)
    limit = d.get("limit")
    remaining = (float(limit) - used) if limit is not None else None

    print("=" * 56)
    print("LLM SPEND")
    print("=" * 56)
    print(f"  spent on this key   ${used:,.4f}")
    if limit is None:
        print("  key limit           none set  <- a runaway run has no backstop")
    else:
        pct = 100 * used / float(limit) if float(limit) else 0
        print(f"  key limit           ${float(limit):,.2f}  ({pct:.1f}% used)")
        print(f"  remaining on key    ${remaining:,.4f}")
    print(f"  monthly allowance   ${MONTHLY_ALLOWANCE_USD:,.2f} (policy, in spend.py)")
    if d.get("is_free_tier"):
        print("  tier                free")

    problems = []
    if limit is None:
        problems.append("no hard cap on the key — set one in the OpenRouter dashboard")
    elif remaining is not None and remaining <= 0:
        problems.append("key limit reached: collection will fail with 402")
    elif remaining is not None and remaining < 1:
        problems.append(f"under $1 left on the key (${remaining:.2f})")

    over = used >= MONTHLY_ALLOWANCE_USD * STOP_AT_FRACTION
    if over:
        problems.append(
            f"spend ${used:.2f} is at or past {int(STOP_AT_FRACTION*100)}% of the "
            f"${MONTHLY_ALLOWANCE_USD:.0f} allowance"
        )

    print()
    if problems:
        for p in problems:
            print(f"  ACTION NEEDED: {p}")
    else:
        print("  Within budget.")

    # Enforcement is deliberately a hard stop, not a warning. A budget that only
    # warns is a forecast; this makes it a fact.
    if args.enforce and over:
        print("\nSTOPPING: spend ceiling reached. Collection will not run.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
