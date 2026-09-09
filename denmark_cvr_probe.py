#!/usr/bin/env python3
"""Denmark CVR: the arm-first dry-run diagnostic.

    python3 denmark_cvr_probe.py            # rehearse, store nothing
    python3 denmark_cvr_probe.py --days 14

`collectors/denmark_cvr.py` ships DORMANT behind two locks and, unlike every
other connector in this repository, it has NEVER SEEN A LIVE RESPONSE: the CVR
distribution service answers 401 to every search path and the credentials
Erhvervsstyrelsen issues free on request are not held here. This probe is what a
human reads before deciding to arm it.

It prints, in order:

  1. the credential state, in four values and not two;
  2. whether the cluster is alive, read WITHOUT a credential;
  3. the public-mapping field check, which is the one guard that can be
     exercised against production today (`/cvr-permanent/_mapping` needs no
     credential);
  4. the exact query body a run would send;
  5. and, ONLY if credentials are present, one counting query per employee band
     so the owner can see the real population before anything reads a person.

It stores nothing, calls no model, and writes no file. Step 5 is the only step
that sends a credential, and that credential travels in CLEARTEXT because
distribution.virk.dk listens on port 80 and nothing else. That is stated at the
top of the output every single time on purpose.

Arming afterwards is a separate decision and three steps:

    # 1. the owner copies DENMARK_DATA_USER and DENMARK_DATA_PASSWORD from
    #    dk-forge/ai-layoff-tracker into this repository's secrets
    # 2. read this probe with them set
    # 3. a real dry run through the standing gate:
    TIT_DK_CVR=on python3 run_collect.py --source denmark_cvr --dry-run
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta, timezone, datetime

import requests

from collectors import denmark_cvr as dk


def _rule(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=dk.DEFAULT_DAYS,
                        help=f"window in days (default {dk.DEFAULT_DAYS})")
    args = parser.parse_args()

    end_day = datetime.now(timezone.utc).date()
    start = (end_day - timedelta(days=args.days)).isoformat()
    end = end_day.isoformat()

    print("denmark_cvr rehearsal - stores nothing, calls no model")
    print("THE CREDENTIAL TRAVELS IN CLEARTEXT: distribution.virk.dk listens on")
    print("port 80 only (443 times out on all three of its addresses), so HTTP")
    print("Basic over plain HTTP is the only shape the service offers.")

    _rule("1. credentials")
    state = dk.credential_state()
    print(f"  {dk.USER_ENV}: {'set' if state == dk.CRED_PRESENT else 'NOT SET'}")
    print(f"  {dk.PASSWORD_ENV}: "
          f"{'set' if state == dk.CRED_PRESENT else 'NOT SET'}")
    print(f"  state: {state}")
    if state == dk.CRED_ABSENT:
        print("  ABSENT is green, not broken: this collector is dormant. The")
        print("  two secrets exist on dk-forge/ai-layoff-tracker and must be")
        print("  copied to this repository before an armed run can work.")
    print(f"  arming flag {dk.ARM_ENV}: {'ON' if dk.armed() else 'off'}")

    _rule("2. is the service alive (no credential sent)")
    try:
        resp = requests.get(dk.BASE + "/", headers={"User-Agent": dk.USER_AGENT},
                            timeout=30, allow_redirects=False)
        body = resp.json() if resp.status_code == 200 else {}
        print(f"  GET {dk.BASE}/ -> HTTP {resp.status_code} "
              f"{body.get('cluster_name', '')} "
              f"elasticsearch {(body.get('version') or {}).get('number', '?')}")
    except Exception as exc:                       # noqa: BLE001 - a probe
        print(f"  UNKNOWN: {exc}")
        print("  Unreachable is UNKNOWN, never a pass and never a fault.")

    _rule("3. the public mapping (no credential sent)")
    try:
        found = dk.mapping_check()
        print(f"  {len(dk.REQUIRED_FIELDS)} required field paths, all present "
              f"in a mapping carrying {len(found)}.")
        print("  This is the guard that makes a renamed field a loud refusal")
        print("  instead of an empty result set.")
    except Exception as exc:                       # noqa: BLE001 - a probe
        print(f"  REFUSED: {exc}")

    _rule("4. the query an armed run would send")
    print(f"  window {start}..{end} ({args.days}d)")
    print(f"  POST {dk.BASE}{dk.SEARCH_PATHS[0]}?scroll={dk.SCROLL_TTL}")
    for line in json.dumps(dk.search_body(start, end),
                           ensure_ascii=False, indent=2).splitlines():
        print("  " + line)
    print(f"  bands read as material: {', '.join(dk.MATERIAL_BANDS)}")
    print(f"  band floor {dk.MATERIAL_BANDS[0]}, whose LOWER EDGE IS 200 and")
    print( "  not 250: the register publishes no boundary at 250.")

    if state != dk.CRED_PRESENT:
        _rule("5. the live population")
        print("  SKIPPED: no credential. This is the honest end of a dormant")
        print("  rehearsal, not a zero.")
        print("\nNothing was stored, nothing was spent, no credential was sent.")
        return 0

    _rule("5. the live population (this step sends the cleartext credential)")
    auth = dk.credentials()
    try:
        search_url = dk.resolve_search_url(auth=auth)
        print(f"  search path that answers: {search_url}")
        for band in dk.BAND_LABELS:
            body = dk.search_body(start, end, bands=(band,), size=0)
            payload = dk._json(dk._request("post", search_url, body=body,
                                           auth=auth),
                               f"the count for {band}")
            marker = "  <- material" if band in dk.MATERIAL_BANDS else ""
            print(f"  {band:<20} {dk.BAND_LABELS[band]:<14} "
                  f"{dk._total(payload):>8} changed in window{marker}")
    except dk.CvrCredentialsRejected as exc:
        print(f"  REJECTED: {exc}")
        print("  A human rotates the secret. This is not something a retry")
        print("  can reach.")
    except Exception as exc:                       # noqa: BLE001 - a probe
        print(f"  UNKNOWN: {exc}")

    print("\nNothing was stored and no model was called. The counts above are")
    print("the first measurement this collector has ever had: write them into")
    print("the module docstring and derive an emptiness floor from them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
