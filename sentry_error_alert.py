#!/usr/bin/env python3
"""Hourly: a genuinely NEW, unresolved Sentry error becomes one email, with an
optional three-line plain-language summary bought through a separate,
hard-capped key.

WHY THIS EXISTS
---------------
Every other alarm in this repo watches WHAT WE COLLECTED (a source going
stale, a wrong published figure, a red CI run). None of them watches the
application throwing an exception in production, because nothing here has
ever reported to an error tracker. This is the first one: it asks Sentry for
issues that are unresolved and first seen in the last hour, dedupes them the
same way `ci_alert.py` dedupes a CI failure (numbers normalised out before
hashing), and sends through this repo's one door, `ops_notify.py`.

THE SUMMARY CALL IS SEPARATE FROM EVERYTHING ELSE THIS REPO SPENDS
--------------------------------------------------------------------
`OPENROUTER_OPS_KEY` is its own key with its own $3.00/month provider limit,
shared across three repositories, and it is used for NOTHING but this one
call. It must never be confused with `OPENROUTER_API_KEY`
(`spend.MONTHLY_ALLOWANCE_USD`, the collection pipeline's own pot) - mixing
the two would let an ops summary eat the collectors' allowance, or let a
runaway collector eat the $3 this job needs to keep functioning.

`data/sentry_ops_spend.json` is this key's own committed ledger, separate
from `data/spend_month.json` (the OpenRouter key snapshot) and from
`data/discretionary_grants.json` (owner-approved one-time spend on the main
key). The cap is read and the call is metered around exactly ONE HTTP
request (`_one_request`) - no SDK, no retry loop, because a callable that
retries internally puts several charges behind one gate read, which is the
defect CLAUDE.md warns about for the main pipeline. Retrying, if it is ever
wanted, belongs to the CALLER of `summarize()`, never to a loop inside it.

THE SUMMARY IS DECORATION, NEVER A GATE
----------------------------------------
`summarize()` never raises. Every one of "no key", "cap reached" and "the
call itself raised" degrades to `(None, <plain-language reason>)`, and that
reason is written straight into the alert body. The alert ALWAYS sends; the
summary is the only thing that is ever missing.

REDACTION
---------
Nothing from this repository may reach the prompt except the Sentry issue's
own title, culprit and metadata, and even those are redacted first: an email
address, a bearer token, an `Authorization:`/`X-Api-Key:` header value, and
anything shaped like an API key are stripped before `summarize()` ever builds
a request body. See `redact()`.

DEDUPE, REUSING THE EXISTING LEDGER'S SEMANTICS RATHER THAN A NEW ONE
-----------------------------------------------------------------------
`alert_state.py` already IS "raise once per cause, remind at 14 days, clear
once and mail nothing when nothing was open" - that is the whole point of
moving it into a committed file with the claim committed before the send. A
second, different implementation of that state machine would be a second
thing to keep correct. So this uses THAT module, pointed at its own ledger
file (`data/sentry_alert_state.json`, beside `data/alert_state.json`, never
inside it - a Sentry cause and a CI cause must never collide on one key) via
`ALERT_STATE_PATH`, which `ops_notify.notify` -> `ci_alert.post_alert` ->
`alert_state.claim()` already reads. Nothing here re-implements the ledger.

Every dedupe key is `sentry-issue:<fp>:open`. To resolve exactly ONE cause
(and not every open Sentry cause at once), the resolve scope is
`sentry-issue:<fp>` - `alert_state.decide()` clears every open key that
starts with `scope + ":"`, and `<fp>` is a fixed-width md5 prefix, so no two
different causes' scopes can ever be a prefix of one another.

Usage:
    python3 sentry_error_alert.py [--dry-run] [--org ORG] [--project PROJECT]

Env: SENTRY_AUTH_TOKEN, OPENROUTER_OPS_KEY, and whatever `ops_notify` needs
(RESEND_API_KEY, OPS_MAIL_TO, OPS_MAIL_FROM, ALERT_STATE_PATH,
ALERT_STATE_COMMIT).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import alert_state
import ci_alert
import ops_notify

ROOT = Path(__file__).resolve().parent

#: Beside data/alert_state.json, never inside it - a Sentry cause and a CI
#: cause must never collide on one key. Overridable so the offline suite can
#: drive the real code against a temporary file.
DEFAULT_STATE_PATH = ROOT / "data" / "sentry_alert_state.json"

#: This key's own committed spend ledger. Separate from data/spend_month.json
#: (the main OPENROUTER_API_KEY snapshot) and data/discretionary_grants.json
#: (owner-approved spend on that same main key) - this is a THIRD, unrelated
#: pot, on a different key, for a different project's share of it.
DEFAULT_SPEND_PATH = ROOT / "data" / "sentry_ops_spend.json"

SENTRY_API = "https://sentry.io/api/0"
#: The org/project this job reads. Not secrets - they are the public slugs
#: Sentry issue URLs are built from - so they are overridable env/CLI rather
#: than repository secrets.
DEFAULT_ORG = os.environ.get("SENTRY_ORG_SLUG", "dk-forge")
DEFAULT_PROJECT = os.environ.get(
    "SENTRY_PROJECT_SLUG", "talent-intelligence-tracker")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_SUMMARY_MODEL = "google/gemini-2.5-flash-lite"
USER_AGENT = "TalentIntelligenceTracker/1.0 (+https://asktherecruiter.com)"

#: OPENROUTER_OPS_KEY's own hard monthly ceiling. Provider-enforced too (the
#: key itself carries a $3.00 limit at OpenRouter, shared across three
#: repositories), but the ledger is what lets THIS repository's share be
#: reported and reasoned about, the same way spend.py's own allowance is a
#: policy number reviewable in a diff rather than a value only OpenRouter
#: knows.
MONTHLY_CAP_USD = 3.00

#: The scope every Sentry-cause dedupe key lives under. A constant, not
#: derived, because `issue_within_last_hour`/`issue_cause` and the resolve
#: pass both have to agree on it without re-deriving it from a string.
SCOPE_PREFIX = "sentry-issue"


@contextlib.contextmanager
def _pin_alert_state_path(path: Path | str):
    """Make `ops_notify.notify`/`.resolve` claim against THIS ledger.

    `alert_state.claim()` reads its path from `ALERT_STATE_PATH`
    (`alert_state.state_path()`), not from a parameter this module can pass
    through `ops_notify`. Pinning the env var for the duration of `run()` is
    what keeps the ledger this module READS (to know what is already open)
    and the ledger the claim WRITES the same file - without it a caller-given
    `state_path` would only ever be read from, never written to, and every
    cause would look new forever.
    """
    prev = os.environ.get("ALERT_STATE_PATH")
    os.environ["ALERT_STATE_PATH"] = str(path)
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("ALERT_STATE_PATH", None)
        else:
            os.environ["ALERT_STATE_PATH"] = prev


def scope_for(fingerprint: str) -> str:
    return f"{SCOPE_PREFIX}:{fingerprint}"


def dedupe_key_for(fingerprint: str) -> str:
    #: The trailing ":open" segment is what makes `scope_for(fp)` resolve
    #: EXACTLY this one cause and no other: alert_state clears every open key
    #: that starts with `scope + ":"`, and no other stored key can share this
    #: prefix because `fp` is a fixed-width hash.
    return f"{scope_for(fingerprint)}:open"


# ---------------------------------------------------------------------------
# Redaction. Nothing else from this repository may enter the prompt.
# ---------------------------------------------------------------------------

_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-=]{6,}")
_AUTH_HEADER = re.compile(r"(?i)\b(Authorization|X-Api-Key)\s*:\s*\S+")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_APIKEY = re.compile(
    r"\b(?:sk|pk|rk|api|apikey|key|token)[-_][A-Za-z0-9]{6,}(?:[-_][A-Za-z0-9]{4,})*\b",
    re.I,
)


def redact(text: str) -> str:
    """Strip anything that must never reach a model prompt.

    Order matters: `_BEARER` has to run before `_AUTH_HEADER`, because once
    the header rule collapses "Authorization: Bearer xyz" to
    "Authorization: <redacted>" the word "Bearer" is gone and a bearer token
    that appeared WITHOUT an Authorization header in front of it (pasted raw
    into a log line, which is exactly how one gets into a stack trace) would
    survive if `_BEARER` had not already caught it.
    """
    out = str(text or "")
    out = _BEARER.sub("Bearer <redacted>", out)
    out = _AUTH_HEADER.sub(lambda m: f"{m.group(1)}: <redacted>", out)
    out = _EMAIL.sub("<redacted-email>", out)
    out = _APIKEY.sub("<redacted-key>", out)
    return out


# ---------------------------------------------------------------------------
# Sentry
# ---------------------------------------------------------------------------

def fetch_new_issues(token: str, *, org: str | None = None,
                     project: str | None = None, timeout: int = 30) -> list:
    """Unresolved issues Sentry itself calls NEW, over the last hour.

    Returns [] rather than raising on a shape Sentry did not promise (a dict
    error body instead of a list, say); `run()` treats a request that raises
    as "could not check right now", which is different from "nothing new".
    """
    org = org or DEFAULT_ORG
    project = project or DEFAULT_PROJECT
    query = urllib.parse.urlencode({
        "query": "is:unresolved is:new",
        "statsPeriod": "1h",
        "sort": "new",
        "limit": "25",
    })
    url = f"{SENTRY_API}/projects/{org}/{project}/issues/?{query}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8") or "[]")
    return payload if isinstance(payload, list) else []


def issue_cause(issue: dict) -> tuple[str, str]:
    """-> (fingerprint, headline). Keyed the way `ci_alert.py` keys a CI
    cause: numbers normalised out before hashing, so a count that drifts
    while the same defect keeps throwing is ONE cause and mails once."""
    metadata = issue.get("metadata") or {}
    title = (issue.get("title") or metadata.get("value")
             or issue.get("culprit") or "unknown error")
    normalised = ci_alert.normalise(str(title))
    fingerprint = hashlib.md5(normalised.encode("utf-8")).hexdigest()[:16]
    return fingerprint, str(title)[:300]


def issue_within_last_hour(issue: dict, now: datetime.datetime | None = None) -> bool:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    first_seen = issue.get("firstSeen")
    if not first_seen:
        return False
    try:
        seen = datetime.datetime.fromisoformat(str(first_seen).replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.timedelta(0) <= (now - seen) <= datetime.timedelta(hours=1)


# ---------------------------------------------------------------------------
# This key's own committed spend ledger
# ---------------------------------------------------------------------------

def _month_key(now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m")


def load_spend(path: Path | str = DEFAULT_SPEND_PATH) -> dict:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def spent_this_month(doc: dict, month: str | None = None) -> float:
    month = month or _month_key()
    entry = doc.get(month) or {}
    try:
        return float(entry.get("usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def record_spend(path: Path | str, usd: float, month: str | None = None) -> dict:
    month = month or _month_key()
    doc = load_spend(path)
    entry = doc.setdefault(month, {"usd": 0.0, "calls": 0})
    entry["usd"] = round(float(entry.get("usd") or 0.0) + float(usd), 6)
    entry["calls"] = int(entry.get("calls") or 0) + 1
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


# ---------------------------------------------------------------------------
# The one metered call
# ---------------------------------------------------------------------------

def _one_request(api_key: str, error_text: str, timeout: int = 30) -> tuple[str, float]:
    """Exactly ONE HTTP request. No SDK, no retry loop, no `attempts=` of its
    own: a callable that retries internally puts several charges behind one
    gate read, which is the once-per-item defect CLAUDE.md warns the main
    pipeline about. If this ever needs a retry, it is the CALLER's `call=`
    argument to `summarize()` that adds one, gate-read-meter on every
    attempt - never a loop added here."""
    body = {
        "model": OPENROUTER_SUMMARY_MODEL,
        "temperature": 0,
        "max_tokens": 220,
        "usage": {"include": True},
        "messages": [
            {"role": "system", "content": (
                "You are given the text of a NEW, unresolved application "
                "error. Reply with exactly three lines and nothing else:\n"
                "What broke: <one sentence>\n"
                "Likely cause: <one sentence>\n"
                "Next step: <one sentence, concrete and actionable>"
            )},
            {"role": "user", "content": error_text[:4000]},
        ],
    }
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choice = payload["choices"][0]
    content = (choice.get("message", {}).get("content") or "").strip()
    if not content:
        raise RuntimeError(
            f"model returned empty content (finish_reason="
            f"{choice.get('finish_reason')!r})")
    usage = payload.get("usage") or {}
    try:
        cost = float(usage.get("cost") or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    return content, cost


def summarize(error_text: str, *, api_key: str,
             spend_path: Path | str = DEFAULT_SPEND_PATH,
             cap: float = MONTHLY_CAP_USD,
             call=None) -> tuple[str | None, str]:
    """-> (summary_or_None, note). NEVER RAISES: a summary is decoration on an
    alert that must send whether or not this succeeds.

    THE GATE: the cap is read immediately before the request and the ledger
    is metered immediately after - the same shape as this repo's other
    "gate read, one request, meter" paths - so a caller cannot spend without
    checking and cannot check without metering. `call` performs exactly ONE
    request by contract; see `_one_request`.
    """
    if not api_key:
        return None, "OPENROUTER_OPS_KEY is not set"
    doc = load_spend(spend_path)
    spent = spent_this_month(doc)
    if spent >= cap:
        return None, (f"the ${cap:.2f}/month OPENROUTER_OPS_KEY cap is reached "
                       f"(${spent:.2f} spent so far this month)")
    # Resolved at CALL time, not baked into the signature as a default: a
    # module-level default is bound once, at import, and a test patching
    # `sentry_error_alert._one_request` afterwards would silently have no
    # effect on it. This lookup is what makes that patch actually take.
    make_call = call or _one_request
    try:
        content, cost = make_call(api_key, redact(error_text))
    except Exception as exc:  # noqa: BLE001 - a summary must never block an alert
        return None, f"the summary call raised ({type(exc).__name__}: {exc})"
    record_spend(spend_path, cost)
    return content, "summary attached"


# ---------------------------------------------------------------------------
# The alert
# ---------------------------------------------------------------------------

def build_body(*, title: str, culprit: str, permalink: str, count,
              summary: str | None, summary_note: str) -> str:
    lines = [
        "A NEW error appeared in Sentry: unresolved, first seen in the last hour.",
        "",
        f"  title:    {title}",
        f"  culprit:  {culprit or '(unknown)'}",
        f"  events:   {count}",
        f"  sentry:   {permalink or '(no link)'}",
        "",
    ]
    if summary:
        lines.append("Summary:")
        lines.append(summary)
    else:
        lines.append(f"No summary was attached: {summary_note}.")
    lines.append("")
    lines.append(
        "What to do: open the Sentry link above and read the stack. You will "
        "get no further mail about this exact cause until it stops appearing "
        "and comes back, or fourteen days pass with it still open.")
    return "\n".join(lines)


def run(*, sentry_token: str, openrouter_ops_key: str,
       org: str | None = None, project: str | None = None,
       state_path: Path | str = DEFAULT_STATE_PATH,
       spend_path: Path | str = DEFAULT_SPEND_PATH,
       dry_run: bool = False,
       now: datetime.datetime | None = None,
       fetch=fetch_new_issues) -> int:
    """Never raises: this IS the alerting path, and an alerter that dies
    while checking for a problem has reported nothing about it."""
    if not sentry_token:
        print("sentry-error-check: SENTRY_AUTH_TOKEN is not set, nothing to check")
        return 0

    try:
        issues = fetch(sentry_token, org=org, project=project)
    except Exception as exc:  # noqa: BLE001
        print(f"sentry-error-check: could not read Sentry "
              f"({type(exc).__name__}: {exc})")
        return 1

    now = now or datetime.datetime.now(datetime.timezone.utc)
    seen: dict[str, dict] = {}
    for issue in issues:
        if not isinstance(issue, dict) or not issue_within_last_hour(issue, now):
            continue
        fingerprint, _ = issue_cause(issue)
        seen[fingerprint] = issue

    with _pin_alert_state_path(state_path):
        ledger = alert_state.load(alert_state.state_path())
        previously_open = set(ledger.get("open") or {})

        raised = 0
        for fingerprint, issue in sorted(seen.items()):
            key = dedupe_key_for(fingerprint)
            already_open = key in previously_open
            _, title = issue_cause(issue)
            culprit = str(issue.get("culprit") or "")
            permalink = str(issue.get("permalink") or "")
            count = issue.get("count") or issue.get("numEvents") or "?"

            summary: str | None = None
            summary_note = "this cause is already open; no new summary is drawn"
            if not already_open:
                metadata = json.dumps(issue.get("metadata") or {}, sort_keys=True)[:2000]
                error_text = f"title: {title}\nculprit: {culprit}\nmetadata: {metadata}"
                summary, summary_note = summarize(
                    error_text, api_key=openrouter_ops_key, spend_path=spend_path)

            body = build_body(title=title, culprit=culprit, permalink=permalink,
                              count=count, summary=summary, summary_note=summary_note)
            subject = f"NEW ERROR: {title}"[:150]
            if dry_run:
                print(f"[dry-run] would raise {key}: {title}")
                continue
            ops_notify.notify(subject, body, dedupe_key=key, what="new-error alert")
            raised += 1

        cleared = 0
        still_new_scopes = {scope_for(fp) for fp in seen}
        for key in sorted(previously_open):
            if not key.startswith(f"{SCOPE_PREFIX}:"):
                continue
            scope = key.rsplit(":", 1)[0]
            if scope in still_new_scopes:
                continue
            if dry_run:
                print(f"[dry-run] would resolve {scope}")
                continue
            ops_notify.resolve(
                scope, f"RESOLVED: {scope}",
                "This Sentry cause is no longer unresolved-and-new. Nothing to do.",
                what="new-error resolve")
            cleared += 1

    print(f"sentry-error-check: {len(seen)} new-in-last-hour, "
          f"{raised} raised, {cleared} cleared")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Sentry new-error check -> ops_notify, with an optional "
                    "one-call plain-language summary")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be sent; alert and spend nothing")
    ap.add_argument("--org", default=None)
    ap.add_argument("--project", default=None)
    args = ap.parse_args(argv)

    return run(
        sentry_token=(os.environ.get("SENTRY_AUTH_TOKEN") or "").strip(),
        openrouter_ops_key=(os.environ.get("OPENROUTER_OPS_KEY") or "").strip(),
        org=args.org, project=args.project, dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
