"""Ask Codex for a security review when a pull request touches risky paths.

dk-forge/todo#1852 (owner 2026-11-18, "yes security scans"). Run by
.github/workflows/codex-security-review.yml on pull_request opened/synchronize.
If any changed file matches RISKY_PATTERNS, post the comment
"@codex security review" ONCE per head SHA: a hidden marker carrying the SHA
is checked first, so a re-run never double-posts. No AI here, no secrets:
GITHUB_TOKEN with pull-requests: write. Stdlib only.

Rule: a Codex security finding blocks the merge until it is fixed.
"""
import json
import os
import re
import sys
import urllib.request

# Case-insensitive, matched against each changed path. Tuned per repo.
RISKY_PATTERNS = [
    r"wordpress-plugin/.*(rest|api|ajax|admin)",
    r"subscriber",
    r"unsubscribe",
    r"opsmail",
    r"(^|/)wp-config",
]

COMMON_PATTERNS = [
    r"(^|/)\.env",                       # .env files and env handling
    r"(^|[/_.-])env[/_.-]",
    r"(^|[/_.-])forms?([/_.-]|$)",
    r"contact", r"newsletter", r"subscri", r"brevo", r"mailchimp",
    r"auth(?!or)", r"login", r"logout", r"sign-?up", r"sign-?in", r"session",
    r"password", r"oauth", r"(^|[/_.-])tokens?([/_.-]|$)",
    r"stripe", r"payment", r"billing", r"checkout", r"invoice",
    r"cookie", r"consent",
    r"(^|[/_.-])pii([/_.-]|$)", r"profile", r"(^|[/_.-])e?mails?([/_.-]|$)",
    r"personal[-_]data", r"gdpr", r"dsar", r"privacy",
    r"mu-plugins", r"webhook",
]

_MARKER = "<!-- codex-security-review sha={sha} -->"
API = "https://api.github.com"


def compiled():
    return [re.compile(p, re.I) for p in COMMON_PATTERNS + RISKY_PATTERNS]


# This gate's own workflow, script and tests are never risky by themselves.
SELF = re.compile(r"codex[-_]security[-_]review", re.I)


def risky_files(paths, patterns=None):
    """The changed paths that match at least one risky pattern."""
    pats = patterns if patterns is not None else compiled()
    return [p for p in paths if not SELF.search(p) and any(rx.search(p) for rx in pats)]


def marker(sha):
    return _MARKER.format(sha=sha)


def already_posted(comment_bodies, sha):
    m = marker(sha)
    return any(m in (b or "") for b in comment_bodies)


def build_body(sha, files):
    shown = "\n".join(f"- `{f}`" for f in files[:20])
    more = f"\n- ... and {len(files) - 20} more" if len(files) > 20 else ""
    return (
        "@codex security review\n\n"
        f"This pull request touches risky paths (head `{sha[:12]}`):\n{shown}{more}\n\n"
        "A Codex security finding blocks the merge until it is fixed. If Codex has not "
        "reviewed within about 30 minutes, or replies that this repo has no Codex "
        "environment, the security review is unavailable (no Codex environment) and "
        "does not block the merge.\n\n"
        f"{marker(sha)}"
    )


def _get_all(url, token):
    out, page = [], 1
    while True:
        req = urllib.request.Request(f"{url}?per_page=100&page={page}", headers=_headers(token))
        with urllib.request.urlopen(req, timeout=30) as r:
            batch = json.load(r)
        out += batch
        if len(batch) < 100:
            return out
        page += 1


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "codex-security-review",
    }


def run(repo, pr, sha, token, get_all=_get_all, post=None):
    files = [f["filename"] for f in get_all(f"{API}/repos/{repo}/pulls/{pr}/files", token)]
    hits = risky_files(files)
    if not hits:
        print(f"No risky paths in {len(files)} changed files; no review requested.")
        return "skip"
    bodies = [c.get("body") for c in get_all(f"{API}/repos/{repo}/issues/{pr}/comments", token)]
    if already_posted(bodies, sha):
        print(f"Already requested for {sha}; not posting again.")
        return "duplicate"
    body = build_body(sha, hits)
    (post or _post)(f"{API}/repos/{repo}/issues/{pr}/comments", token, body)
    print(f"Requested Codex security review for {sha} ({len(hits)} risky files).")
    return "posted"


def _post(url, token, body):
    data = json.dumps({"body": body}).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers=_headers(token))
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def main():
    env = os.environ
    run(env["GITHUB_REPOSITORY"], env["PR"], env["HEAD_SHA"], env["GH_TOKEN"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
