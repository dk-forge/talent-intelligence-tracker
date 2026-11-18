"""File ONE issue in dk-forge/todo for a failing finish line, deduped by title.

Shared by every finish-line workflow (same file in each repo). Stdlib only.
- First FAIL: open an issue titled exactly TITLE, labels Claude + the product.
- Repeat FAIL while that issue is still open: comment and add the Overdue label.
- No token, or every token is refused: print what GitHub said and exit 0, so the
  run summary and the status file stay the record (never invent or ask for a secret).
- Dedupe lists the repo's open issues, not /search/issues: the search API answers
  422 "cannot be searched" for a private repo the token cannot see, which looked
  like a validation error and stopped the fallback token being tried (2026-11-18).

Usage: python3 finish_line_issue.py --title T --product ATR --body-file status.md
Token: env TODO_ISSUES_TOKEN (falls back to GH_TOKEN).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

REPO = "dk-forge/todo"
API = "https://api.github.com"


def _call(method, path, token, body=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Accept": "application/vnd.github+json",
                                          "User-Agent": "dk-forge-finish-line"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def _github_message(e: urllib.error.HTTPError) -> str:
    """GitHub's own reason (e.g. a missing label or an unsearchable repo), short."""
    try:
        data = json.loads(e.read() or b"{}")
    except Exception:  # noqa: BLE001
        return e.reason or "no reason given"
    errs = "; ".join(str(x.get("message") or x.get("code") or x) for x in data.get("errors") or [] if isinstance(x, dict))
    return (str(data.get("message") or e.reason) + (f" ({errs})" if errs else ""))[:300]


MAX_PAGES = 100  # 10,000 open issues; past that the scan is incomplete


class IncompleteScan(RuntimeError):
    """The open-issue list could not be read to the end, so dedupe is unsafe."""


def open_issues(token, call) -> list:
    """Every open issue in REPO, paged to the end. Raises IncompleteScan rather
    than return a partial list: a partial list could miss the existing issue and
    file a duplicate."""
    hits = []
    for page in range(1, MAX_PAGES + 1):
        batch = call("GET", f"/repos/{REPO}/issues?state=open&per_page=100&page={page}", token) or []
        hits += batch
        if len(batch) < 100:
            return hits
    raise IncompleteScan(f"more than {MAX_PAGES * 100} open issues in {REPO}; not filing to avoid a duplicate")


def find_open(issues, title):
    """The open issue (not PR) whose title matches exactly, or None."""
    for i in issues:
        if i.get("title") == title and "pull_request" not in i and i.get("state") == "open":
            return i
    return None


def plan(existing, title, product):
    """What to do: ('create', labels) or ('repeat', number)."""
    if existing:
        return ("repeat", existing["number"])
    return ("create", ["Claude", product])


def main(argv=None, call=_call) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--product", required=True)
    ap.add_argument("--body-file", required=True)
    a = ap.parse_args(argv)
    tokens = [t for t in (os.environ.get(k, "") for k in ("TODO_ISSUES_TOKEN", "TODO_ISSUES_TOKEN_FALLBACK", "GH_TOKEN")) if t]
    body = open(a.body_file, encoding="utf-8").read()[:60000]
    if not tokens:
        print("NOT FILED: no token that can write dk-forge/todo; the run summary and status file are the record.")
        return 0
    for n, token in enumerate(tokens):
        try:
            return _file(a, body, token, call)
        except urllib.error.HTTPError as e:
            why = _github_message(e)
            if e.code in (401, 403, 404, 422) and n + 1 < len(tokens):
                print(f"token {n + 1} got {e.code} from {REPO} ({why}); trying the next one")
                continue
            print(f"NOT FILED: GitHub said {e.code} for {REPO} with token {n + 1} of {len(tokens)}: {why}. "
                  "The run summary and status file are the record.")
        except IncompleteScan as e:
            print(f"NOT FILED: {e}. The run summary and status file are the record.")
            return 1
        except Exception as e:  # noqa: BLE001
            print(f"NOT FILED: {type(e).__name__}; the run summary and status file are the record.")
        return 0
    return 0


def _file(a, body, token, call) -> int:
    hits = open_issues(token, call)
    action, arg = plan(find_open(hits, a.title), a.title, a.product)
    if action == "create":
        i = call("POST", f"/repos/{REPO}/issues", token, {"title": a.title, "body": body, "labels": arg})
        print(f"FILED: {i['html_url']}")
    else:
        call("POST", f"/repos/{REPO}/issues/{arg}/comments", token, {"body": "Still failing.\n\n" + body})
        call("POST", f"/repos/{REPO}/issues/{arg}/labels", token, {"labels": ["Overdue"]})
        print(f"REPEAT: https://github.com/{REPO}/issues/{arg} (labelled Overdue)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
