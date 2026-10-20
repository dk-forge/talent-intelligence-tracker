#!/usr/bin/env python3
"""Daily snapshot of this repository's open GitHub security alerts.

Owner request 2026-10-20: every session should see the open alert counts
without asking. `.github/workflows/security-alerts.yml` runs `fetch` once a day
and commits the result; session-start tooling runs `status` (or imports
`summarize`) and reads the committed file OFFLINE.

Two sources, each with its own state, because they fail differently:
  * dependabot     GET /repos/{repo}/dependabot/alerts, token ALERTS_READ_TOKEN
                   (a fine-grained PAT: Dependabot alerts read + Metadata).
  * code_scanning  GET /repos/{repo}/code-scanning/alerts, token GITHUB_TOKEN
                   with `security-events: read`. A private repo without paid
                   Code Security answers 403/404; that is UNKNOWN, never 0.

A missing token or any API error is state UNKNOWN with a reason. It is never
written as a zero, and `fetch` still exits 0 so a daily job does not go red
for a credential the owner has to fix (the UNKNOWN is what the next session
reads). The only non-zero exit is "could not write the snapshot at all".

Stdlib only, no secrets in the output: the file holds counts, alert numbers,
package names, severities and fixed-in versions.

Usage:
  python3 security_alerts.py fetch --out <path>
  python3 security_alerts.py status [<path>] [--max-age-hours 48]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
SEVERITIES = ("critical", "high", "medium", "low")
MAX_AGE_HOURS = 48
# Same-day re-runs keep the old checked_at so an unchanged snapshot is not a
# new commit; a daily heartbeat still refreshes it well inside MAX_AGE_HOURS.
HEARTBEAT_HOURS = 20
MAX_PAGES = 50
MESSAGE_MAX = 300


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _next_link(header: str | None) -> str | None:
    for part in (header or "").split(","):
        m = re.match(r'\s*<([^>]+)>\s*;\s*rel="next"', part)
        if m:
            return m.group(1)
    return None


def _get_all(url: str, token: str, opener=urllib.request.urlopen) -> list:
    """Every page of a list endpoint. Raises urllib.error.HTTPError / URLError."""
    out: list = []
    for _ in range(MAX_PAGES):
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "security-alerts-snapshot",
        })
        with opener(req, timeout=30) as resp:
            page = json.loads(resp.read().decode("utf-8"))
            nxt = _next_link(resp.headers.get("Link"))
        if not isinstance(page, list):
            raise ValueError("expected a JSON list")
        out.extend(page)
        if not nxt:
            return out
        url = nxt
    raise ValueError(f"more than {MAX_PAGES} pages")


def _unknown(reason: str) -> dict:
    return {"state": "UNKNOWN", "reason": reason, "total": None, "counts": None, "alerts": []}


def _counts(alerts: list) -> dict:
    c = {s: 0 for s in SEVERITIES}
    for a in alerts:
        c[a["severity"] if a["severity"] in c else "low"] += 1
    return c


def _dependabot_row(a: dict) -> dict:
    dep = a.get("dependency") or {}
    pkg = dep.get("package") or {}
    vul = a.get("security_vulnerability") or {}
    adv = a.get("security_advisory") or {}
    fixed = (vul.get("first_patched_version") or {}).get("identifier")
    return {
        "id": a.get("number"),
        "package": pkg.get("name"),
        "ecosystem": pkg.get("ecosystem"),
        "manifest": dep.get("manifest_path"),
        "severity": (vul.get("severity") or adv.get("severity") or "unknown").lower(),
        "fixed_in": fixed,
        "advisory": adv.get("ghsa_id"),
    }


def _code_scanning_row(a: dict) -> dict:
    rule = a.get("rule") or {}
    sev = (rule.get("security_severity_level") or rule.get("severity") or "unknown").lower()
    # Non-security rule severities (error/warning/note) map onto the same scale.
    sev = {"error": "high", "warning": "medium", "note": "low"}.get(sev, sev)
    inst = a.get("most_recent_instance") or {}
    loc = inst.get("location") or {}
    # The analyser's own message names the expression/variable, never a value;
    # it is clipped so one odd alert cannot bloat the committed snapshot.
    msg = (inst.get("message") or {}).get("text")
    return {
        "id": a.get("number"),
        "package": None,
        "rule": rule.get("id"),
        "tool": (a.get("tool") or {}).get("name"),
        "path": loc.get("path"),
        "start_line": loc.get("start_line"),
        "end_line": loc.get("end_line"),
        "message": msg[:MESSAGE_MAX] if isinstance(msg, str) else None,
        "severity": sev,
        "fixed_in": None,
    }


def _section(url: str, token: str | None, token_name: str, row, opener) -> dict:
    if not token:
        return _unknown(f"{token_name} is not set in this run")
    try:
        raw = _get_all(url, token, opener)
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return _unknown(f"HTTP {e.code}: not readable with {token_name} "
                            "(feature off, plan, or token scope); not a zero")
        return _unknown(f"HTTP {e.code} from the alerts API")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return _unknown(f"API unreachable or malformed: {type(e).__name__}")
    rows = sorted((row(a) for a in raw), key=lambda r: (r["id"] is None, r["id"] or 0))
    return {"state": "OK", "reason": None, "total": len(rows), "counts": _counts(rows), "alerts": rows}


def build(repo: str, env=os.environ, opener=urllib.request.urlopen) -> dict:
    base = f"{API}/repos/{repo}"
    return {
        "schema": 1,
        "repo": repo,
        "source": ".github/workflows/security-alerts.yml",
        "dependabot": _section(f"{base}/dependabot/alerts?state=open&per_page=100",
                               env.get("ALERTS_READ_TOKEN"), "ALERTS_READ_TOKEN",
                               _dependabot_row, opener),
        "code_scanning": _section(f"{base}/code-scanning/alerts?state=open&per_page=100",
                                  env.get("GITHUB_TOKEN"), "GITHUB_TOKEN",
                                  _code_scanning_row, opener),
    }


def write_snapshot(path: str, snap: dict, now: dt.datetime | None = None) -> bool:
    """Write `snap`; keep the old checked_at when nothing changed recently.

    Returns True when the file content changed (i.e. there is something to commit).
    """
    now = now or _now()
    prev = None
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        prev = None
    checked_at = _iso(now)
    if isinstance(prev, dict):
        same = {k: v for k, v in prev.items() if k != "checked_at"} == snap
        old = _parse_iso(prev.get("checked_at"))
        if same and old and (now - old).total_seconds() < HEARTBEAT_HOURS * 3600:
            checked_at = prev["checked_at"]
    out = dict(snap, checked_at=checked_at)
    text = json.dumps(out, indent=2, sort_keys=True) + "\n"
    old_text = None
    try:
        with open(path, encoding="utf-8") as f:
            old_text = f.read()
    except OSError:
        pass
    if text == old_text:
        return False
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def summarize(snap: dict | None, now: dt.datetime | None = None,
              max_age_hours: float = MAX_AGE_HOURS) -> tuple[str, list[str]]:
    """(verdict, lines). verdict: OK (no open alerts) | ALERTS | UNKNOWN."""
    now = now or _now()
    if not isinstance(snap, dict):
        return "UNKNOWN", ["no security alert snapshot (file absent or unreadable): UNKNOWN, not zero"]
    when = _parse_iso(snap.get("checked_at"))
    if when is None:
        return "UNKNOWN", ["snapshot has no valid checked_at: UNKNOWN"]
    age_h = (now - when).total_seconds() / 3600
    lines = [f"checked {snap['checked_at']} ({age_h:.0f}h ago)"]
    stale = age_h > max_age_hours
    if stale:
        lines.append(f"STALE: older than {max_age_hours:.0f}h, so every count below is UNKNOWN "
                     "(is security-alerts.yml running?)")
    unknown, alerts = stale, False
    for key, label in (("dependabot", "Dependabot"), ("code_scanning", "Code scanning")):
        sec = snap.get(key) or {}
        if sec.get("state") != "OK":
            lines.append(f"{label}: UNKNOWN ({sec.get('reason') or 'no data'})")
            unknown = True
            continue
        c = sec.get("counts") or {}
        tag = "" if not stale else " [stale]"
        lines.append(f"{label}: {sec.get('total', 0)} open{tag}  "
                     + "  ".join(f"{s}={c.get(s, 0)}" for s in SEVERITIES))
        for a in (sec.get("alerts") or [])[:10]:
            what = a.get("package") or a.get("rule") or "?"
            fix = f" -> fixed in {a['fixed_in']}" if a.get("fixed_in") else ""
            where = f" {a['path']}:{a['start_line']}" if a.get("path") and a.get("start_line") else ""
            lines.append(f"    #{a.get('id')} {a.get('severity')} {what}{fix}{where}")
        alerts = alerts or bool(sec.get("total") and not stale)
    # Known open alerts outrank a half-unknown picture: they are actionable now.
    return ("ALERTS" if alerts else "UNKNOWN" if unknown else "OK"), lines


def _load(path: str) -> dict | None:
    try:
        src = sys.stdin if path == "-" else open(path, encoding="utf-8")
        with src:
            return json.load(src)
    except (OSError, ValueError):
        return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--out", required=True)
    f.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    s = sub.add_parser("status")
    s.add_argument("path", nargs="?", default="-")
    s.add_argument("--max-age-hours", type=float, default=MAX_AGE_HOURS)
    a = p.parse_args(argv)
    if a.cmd == "fetch":
        if not a.repo:
            print("GITHUB_REPOSITORY / --repo not set; nothing written")
            return 1
        snap = build(a.repo)
        try:
            changed = write_snapshot(a.out, snap)
        except OSError as e:
            print(f"could not write {a.out}: {e}")
            return 1
        verdict, lines = summarize(_load(a.out))
        print(f"security alerts for {a.repo}: {verdict} ({'changed' if changed else 'unchanged'})")
        for line in lines:
            print("  " + line)
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as g:
                g.write(f"changed={'yes' if changed else 'no'}\nverdict={verdict}\n")
        return 0
    verdict, lines = summarize(_load(a.path), max_age_hours=a.max_age_hours)
    print(f"security alerts: {verdict}")
    for line in lines:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
