#!/usr/bin/env python3
"""Mirror this repo's open GitHub security alerts into ONE issue.

Why: a Claude session's integration token gets 403 on the code-scanning,
Dependabot and secret-scanning alert APIs but can read issues. A daily job
(`.github/workflows/security-alerts-mirror.yml`) reads the alerts with the
workflow's tokens and keeps the issue "Security alerts (mirror)" (label
`security-mirror`) edited in place: counts per type and severity, then a table.

Tokens, per alert type, first one that works wins:
  * ALERTS_READ_TOKEN (optional fine-grained PAT: Dependabot alerts read,
    Secret scanning alerts read, Code scanning alerts read, Metadata);
  * GITHUB_TOKEN (`security-events: read` covers code scanning; Dependabot and
    secret scanning are usually 403 for it).
A type no token can read is shown as UNREADABLE with the HTTP reason, never
as zero, and the run stays green.

NEVER writes a secret value: secret-scanning rows carry only the secret type,
file:line and the alert link (the `secret` field is never read).

The issue is closed only when every type was readable and all counts are 0,
and reopened when anything is open again. Stdlib only.

Usage: python3 security_alerts_mirror.py apply   (env GITHUB_REPOSITORY, GITHUB_TOKEN)
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
TITLE = "Security alerts (mirror)"
LABEL = "security-mirror"
SEVERITIES = ("critical", "high", "medium", "low", "unknown")
KINDS = ("code_scanning", "dependabot", "secret_scanning")
PATHS = {
    "code_scanning": "code-scanning/alerts?state=open&per_page=100",
    "dependabot": "dependabot/alerts?state=open&per_page=100",
    "secret_scanning": "secret-scanning/alerts?state=open&per_page=100&hide_secret=true",
}
NAMES = {"code_scanning": "Code scanning", "dependabot": "Dependabot", "secret_scanning": "Secret scanning"}
MAX_PAGES = 30


class ApiError(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(f"HTTP {status}: {reason}")
        self.status = status
        self.reason = reason


def _next_link(header: str | None) -> str | None:
    for part in (header or "").split(","):
        m = re.match(r'\s*<([^>]+)>\s*;\s*rel="next"', part)
        if m:
            return m.group(1)
    return None


def _request(method: str, url: str, token: str, body: dict | None = None, opener=urllib.request.urlopen):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "security-alerts-mirror",
    })
    try:
        with opener(req, timeout=30) as resp:
            raw = resp.read()
            return (json.loads(raw) if raw else None), resp.headers.get("Link")
    except urllib.error.HTTPError as e:
        msg = ""
        try:
            msg = json.loads(e.read() or b"{}").get("message", "")
        except (ValueError, AttributeError):
            pass
        raise ApiError(e.code, msg or str(e.reason)) from None


def _get_all(url: str, token: str, opener=urllib.request.urlopen) -> list:
    out: list = []
    for _ in range(MAX_PAGES):
        page, link = _request("GET", url, token, opener=opener)
        out.extend(page or [])
        url = _next_link(link)
        if not url:
            break
    return out


def fetch_kind(repo: str, kind: str, tokens: list[tuple[str, str]], opener=urllib.request.urlopen) -> dict:
    """{'ok': True, 'alerts': [...], 'token': name} or {'ok': False, 'reason': ...}."""
    reasons = []
    for name, tok in tokens:
        if not tok:
            continue
        try:
            alerts = _get_all(f"{API}/repos/{repo}/{PATHS[kind]}", tok, opener)
            return {"ok": True, "alerts": alerts, "token": name}
        except ApiError as e:
            reasons.append(f"{name}: HTTP {e.status} {e.reason}".strip())
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            reasons.append(f"{name}: {e}")
    return {"ok": False, "reason": "; ".join(reasons) or "no token available"}


def _sev(value) -> str:
    v = str(value or "").lower()
    if v in ("error",):
        return "high"
    if v in ("warning",):
        return "medium"
    if v in ("note",):
        return "low"
    return v if v in SEVERITIES else "unknown"


def _cell(s) -> str:
    return str(s if s not in (None, "") else "-").replace("|", "\\|").replace("\n", " ")


def normalize(kind: str, a: dict) -> dict:
    """One table row. Secret scanning: type, location and link ONLY."""
    created = str(a.get("created_at") or "")[:10]
    link = a.get("html_url") or ""
    if kind == "code_scanning":
        rule = a.get("rule") or {}
        loc = (a.get("most_recent_instance") or {}).get("location") or {}
        sev = rule.get("security_severity_level") or rule.get("severity")
        where = f"{loc.get('path', '')}:{loc.get('start_line', '')}" if loc.get("path") else ""
        return {"kind": kind, "what": rule.get("id") or rule.get("name") or "", "severity": _sev(sev),
                "where": where, "created": created, "link": link}
    if kind == "dependabot":
        adv = a.get("security_advisory") or {}
        dep = a.get("dependency") or {}
        pkg = (dep.get("package") or {}).get("name") or ""
        sev = (a.get("security_vulnerability") or {}).get("severity") or adv.get("severity")
        what = f"{pkg} ({adv.get('ghsa_id', '')})" if adv.get("ghsa_id") else pkg
        return {"kind": kind, "what": what, "severity": _sev(sev), "where": dep.get("manifest_path") or "",
                "created": created, "link": link}
    # secret_scanning: never touch a["secret"].
    where = ""
    loc = a.get("first_location_detected") or {}
    if loc.get("path"):
        where = f"{loc['path']}:{loc.get('start_line', '')}"
    return {"kind": kind, "what": a.get("secret_type_display_name") or a.get("secret_type") or "",
            "severity": "high", "where": where, "created": created, "link": link}


def build_body(results: dict, run_url: str = "", now: str = "") -> str:
    lines = ["Mirror of this repository's open GitHub security alerts, updated daily by "
             "`.github/workflows/security-alerts-mirror.yml` so sessions without alert-API access can read them. "
             "Secret values are never included.", ""]
    if now:
        lines.append(f"Last checked: {now}" + (f" ([run]({run_url}))" if run_url else ""))
        lines.append("")
    lines += ["| Type | Total | " + " | ".join(s.title() for s in SEVERITIES) + " | Read with |",
              "|---|---|" + "---|" * len(SEVERITIES) + "---|"]
    rows: list[dict] = []
    for kind in KINDS:
        r = results[kind]
        if not r["ok"]:
            lines.append(f"| {NAMES[kind]} | UNREADABLE | " + " | ".join("-" for _ in SEVERITIES)
                         + f" | {_cell(r['reason'])} |")
            continue
        krows = [normalize(kind, a) for a in r["alerts"]]
        rows += krows
        counts = [sum(1 for x in krows if x["severity"] == s) for s in SEVERITIES]
        lines.append(f"| {NAMES[kind]} | {len(krows)} | " + " | ".join(map(str, counts)) + f" | {r['token']} |")
    lines.append("")
    if rows:
        order = {s: i for i, s in enumerate(SEVERITIES)}
        rows.sort(key=lambda x: (order[x["severity"]], x["kind"], x["created"]))
        lines += ["| Type | Rule / package / secret type | Severity | File:line or manifest | Created | Link |",
                  "|---|---|---|---|---|---|"]
        for x in rows:
            lines.append(f"| {NAMES[x['kind']]} | {_cell(x['what'])} | {x['severity']} | {_cell(x['where'])} "
                         f"| {_cell(x['created'])} | {'[open](' + x['link'] + ')' if x['link'] else '-'} |")
    else:
        lines.append("No open alerts in the readable types.")
    unreadable = [NAMES[k] for k in KINDS if not results[k]["ok"]]
    if unreadable:
        lines += ["", f"**Not readable by this workflow's tokens:** {', '.join(unreadable)}. "
                  "Counts for these are unknown, not zero. Owner fix: add repository secret "
                  "`ALERTS_READ_TOKEN` (fine-grained PAT on this repo with read access to Dependabot "
                  "alerts, Secret scanning alerts and Code scanning alerts)."]
    return "\n".join(lines) + "\n"


def decide(results: dict, issue: dict | None, body: str) -> dict:
    """Pure: what to do with the mirror issue."""
    total = sum(len(results[k]["alerts"]) for k in KINDS if results[k]["ok"])
    all_readable = all(results[k]["ok"] for k in KINDS)
    want_open = total > 0 or not all_readable
    if issue is None:
        return {"action": "create" if want_open else "none", "body": body}
    state = issue.get("state")
    if want_open:
        if state == "closed":
            return {"action": "reopen", "body": body}
        return {"action": "edit" if issue.get("body") != body else "none", "body": body}
    if state == "open":
        return {"action": "close", "body": body}
    return {"action": "edit" if issue.get("body") != body else "none", "body": body}


def _body_for_compare(body: str) -> str:
    return re.sub(r"Last checked: .*\n", "", body or "")


def apply(env=os.environ, opener=urllib.request.urlopen) -> int:
    import datetime as dt
    repo = env["GITHUB_REPOSITORY"]
    gh = env.get("GITHUB_TOKEN", "")
    tokens = [("ALERTS_READ_TOKEN", env.get("ALERTS_READ_TOKEN", "")), ("GITHUB_TOKEN", gh)]
    results = {k: fetch_kind(repo, k, tokens, opener) for k in KINDS}
    for k in KINDS:
        r = results[k]
        print(f"{k}: " + (f"{len(r['alerts'])} open (read with {r['token']})" if r["ok"] else f"UNREADABLE ({r['reason']})"))
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = build_body(results, env.get("RUN_URL", ""), now)
    issues = _get_all(f"{API}/repos/{repo}/issues?state=all&labels={LABEL}&per_page=100", gh, opener)
    issue = next((i for i in issues if i.get("title") == TITLE and "pull_request" not in i), None)
    cmp_issue = None if issue is None else {**issue, "body": _body_for_compare(issue.get("body"))}
    act = decide(results, cmp_issue, _body_for_compare(body))
    print(f"issue action: {act['action']}")
    if act["action"] == "create":
        try:
            _request("POST", f"{API}/repos/{repo}/labels", gh,
                     {"name": LABEL, "color": "b60205", "description": "Daily security alerts mirror"}, opener)
        except ApiError:
            pass  # already exists
        _request("POST", f"{API}/repos/{repo}/issues", gh, {"title": TITLE, "body": body, "labels": [LABEL]}, opener)
    elif act["action"] in ("edit", "reopen", "close"):
        patch = {"body": body}
        if act["action"] == "reopen":
            patch["state"] = "open"
        if act["action"] == "close":
            patch.update(state="closed", state_reason="completed")
        _request("PATCH", f"{API}/repos/{repo}/issues/{issue['number']}", gh, patch, opener)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "apply":
        print(__doc__)
        sys.exit(2)
    sys.exit(apply())
