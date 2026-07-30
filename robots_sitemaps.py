#!/usr/bin/env python3
"""Add the tracker's two sitemaps to the live robots.txt, without ever being
able to break it.

    python3 robots_sitemaps.py                     # dry run: print the diff, write nothing
    python3 robots_sitemaps.py --apply             # fetch, append, verify, roll back on doubt
    python3 robots_sitemaps.py --targets blog      # one copy only
    python3 robots_sitemaps.py --no-ftp            # diff from HTTP alone, no credentials

WHY THIS IS NOT AN FTP PUT
--------------------------
`robots.txt` is a real file on disk. Apache serves it before WordPress ever
runs (CLAUDE.md, Bluehost gotcha 5), so no plugin, no filter and no REST route
can add a line to it — it has to be uploaded.

And it is the same danger class as `.htaccess`: a truncated or mangled
robots.txt still answers 200 and still looks like a file. Nothing 500s, nothing
goes red, the page renders exactly as before — Google simply stops crawling the
domain, and the first symptom is a traffic graph three weeks later. So this
reuses the shape `wordpress-plugin/.../includes/htaccess.php` already proved on
this host, one layer out:

    keep the old bytes -> write -> probe the live URL -> restore on any doubt

with two additions that file does not need. The probe is a CACHE-BUSTED fetch,
because Cloudflare will happily serve the pre-write copy back and make a failed
write look like a success (gotcha 7). And the probe RETRIES a 5xx, because this
host 500s at random under load (gotcha 8) and a rollback triggered by somebody
else's bad minute is a self-inflicted outage.

THE PATH IS NEVER GUESSED
-------------------------
An FTP account here is chrooted, so an absolute-looking path from the control
panel is not what the session sees, and `/robots.txt` may be the site root's or
somebody else's. Writing to the wrong one is unrecoverable in the only sense
that matters: we would not know we had.

So the remote path is BOUND BY CONTENT, not by assumption. We fetch the URL over
HTTP, then look for a candidate remote file whose bytes are identical to what
that URL served. A byte-identical match is proof that this path is what answers
that URL. No match, no write.

APPEND ONLY, AND RE-RUNNABLE
----------------------------
The file is never rewritten. If a Sitemap line is already there the target is a
no-op and reports as one, so a second dispatch is free and safe. The existing
`sitemap_index.xml` line is a PRECONDITION: if it is not in the file we fetched,
something changed that we do not understand, and the run stops rather than
appending to a file it cannot recognise.

WHAT THE LIVE SITE ACTUALLY HAS, measured 2026-07-30
----------------------------------------------------
There are not two robots.txt files. There is one.

    https://asktherecruiter.com/blog/robots.txt   200  175 bytes  text/plain
    https://asktherecruiter.com/robots.txt        200  13,181 bytes  text/HTML

The second is not a robots.txt at all: the apex serves the same 13,181-byte
"Coming soon" landing page for `/robots.txt`, for `/definitely-not-here.txt`,
and for every other unmatched path. There is no file there to append to, and
the content-binding above refuses the root target for exactly that reason.

That matters more than it looks, because RFC 9309 is explicit that a crawler
reads `/robots.txt` at the host root and nowhere else. A robots.txt in a
subdirectory is not consulted, so the `Sitemap:` lines in `/blog/robots.txt` —
including the `sitemap_index.xml` one that has been there all along — are read
by nothing. Adding two more to that file is correct, harmless, and on its own
will not get either sitemap crawled. Getting them crawled needs a real file at
the apex, or a Search Console submission, and creating a root robots.txt where
none exists changes the crawl rules for the whole domain in one step. That is
the owner's decision to make with the evidence in front of them, not a default
this workflow should quietly take.
"""

from __future__ import annotations

import argparse
import difflib
import io
import os
import random
import sys
import time

SITE = "https://asktherecruiter.com"

# The line that must already be there. Its absence is not a thing to fix; it is
# a thing to stop for.
EXISTING_SITEMAP = f"{SITE}/blog/sitemap_index.xml"

# The two the owner asked for. Both verified live 200 before this was written.
NEW_SITEMAPS = (
    f"{SITE}/blog/talent-intelligence-tracker/company-sitemap.xml",
    f"{SITE}/blog/talent-intelligence-tracker/places-sitemap.xml",
)

# ModSecurity on this host blocks `python-requests` outright (gotcha 1).
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# A robots.txt larger than this is not a robots.txt. Refusing is cheaper than
# reasoning about what we would be appending to.
MAX_PLAUSIBLE_BYTES = 64 * 1024

# The fetched length may differ from the computed length by this much and still
# count as "nothing was truncated" — a trailing newline the host normalises is
# not a corrupted file, and two bytes is not room to lose a directive.
LENGTH_TOLERANCE = 2


class Refusal(Exception):
    """A precondition failed. Nothing was written."""


class Rolledback(Exception):
    """A write was made, failed verification, and was restored."""


class RollbackFailed(Exception):
    """A write failed verification AND the restore did not take. Wake a human."""


class Target:
    """One copy of robots.txt: the URL that serves it, and where it might live.

    `forbidden_fragment` is what stops the two targets binding to each other.
    The root and blog files may hold identical bytes, and if they do, a
    content match alone would let the root target adopt `/blog/robots.txt` and
    write the blog file twice while reporting two successes.
    """

    def __init__(self, name, url, candidates, required_fragment="",
                 forbidden_fragment="", absent_note="", proven=()):
        self.name = name
        self.url = url
        self.candidates = candidates
        # Paths derived from somewhere that has actually been written to, tried
        # first and exempt from the name-shape filters below — those filters
        # exist to discipline GUESSES, and a proven path is not one. It still
        # has to serve byte-identical content, and it still cannot be inside
        # wp-content.
        self.proven = list(proven)
        self.required_fragment = required_fragment
        self.forbidden_fragment = forbidden_fragment
        # What a human should understand when this URL turns out not to serve a
        # robots.txt at all. A bare "served HTML" is true and useless.
        self.absent_note = absent_note

    def __repr__(self):
        return f"<Target {self.name} {self.url}>"


# A robots.txt lives at the root of a WordPress install. It is never inside
# wp-content, and a path that says otherwise is a path we have misunderstood.
# This is a WRITE guard, not a preference: it is what keeps this job out of the
# directory deploy-plugin.yml owns, whatever a secret or an override says.
NEVER_WRITE_INSIDE = "/wp-content/"


def derived_candidates(remote_dir: str) -> dict[str, str]:
    """robots.txt paths derived from a remote path already PROVEN to work.

    The first real dispatch refused: the FTP session is rooted somewhere none
    of the four hand-written candidates reach, and guessing a fifth and a sixth
    blind is how this ends up writing into whatever directory the session
    happened to land in.

    `WP_PLUGIN_REMOTE_DIR` is not a guess. deploy-plugin.yml mirrors into it
    successfully with these same credentials, so it is a working path for this
    exact account, and

        <wp-root>/wp-content/plugins/talent-intelligence-tracker

    walks up three levels to the WordPress root — which is where robots.txt is.

    Reading that secret does NOT widen the plugin deploy's write path. It is
    read to derive a candidate to LOOK at; every candidate still has to serve
    byte-identical content before a byte is written, and NEVER_WRITE_INSIDE
    refuses any path under wp-content outright. The shape is checked rather
    than trusted: a secret that is not a plugin directory derives nothing.
    """
    remote_dir = (remote_dir or "").strip().rstrip("/")
    if not remote_dir or NEVER_WRITE_INSIDE.strip("/") not in remote_dir:
        return {}
    if "/plugins/" not in remote_dir + "/":
        return {}
    wp_root = os.path.dirname(os.path.dirname(os.path.dirname(remote_dir)))
    if not wp_root or wp_root == "/" or NEVER_WRITE_INSIDE in wp_root + "/":
        return {}
    site_root = os.path.dirname(wp_root)
    out = {"blog": f"{wp_root}/robots.txt"}
    if site_root and site_root != wp_root:
        out["root"] = f"{site_root.rstrip('/')}/robots.txt" if site_root != "/" \
            else "/robots.txt"
    return out


def default_targets() -> list[Target]:
    """Both copies, each with the paths an FTP session on this host might show.

    Ordered proven-first: anything derived from WP_PLUGIN_REMOTE_DIR comes
    before the hand-written guesses, because it is the only path in the list
    that something has actually written to.

    Overridable per target by env var, because a control panel that moves the
    document root should not need a code change — but never DEFAULTED from one,
    because a default path is how files land in the wrong folder when a secret
    goes missing (the lesson deploy-plugin.yml records).
    """
    root_override = (os.environ.get("TIT_ROBOTS_ROOT_PATH") or "").strip()
    blog_override = (os.environ.get("TIT_ROBOTS_BLOG_PATH") or "").strip()
    derived = derived_candidates(os.environ.get("WP_PLUGIN_REMOTE_DIR") or "")
    return [
        Target(
            "root",
            f"{SITE}/robots.txt",
            [root_override] if root_override else [
                "/robots.txt",
                "/public_html/robots.txt",
                "robots.txt",
                "public_html/robots.txt",
            ],
            proven=() if root_override else
                   [p for p in (derived.get("root"),) if p],
            forbidden_fragment="/blog/",
            absent_note=(
                "Measured 2026-07-30: the apex has NO robots.txt. It answers "
                "/robots.txt, and every other unmatched path, with the same "
                "13,181-byte 'Coming soon' landing page at HTTP 200. So there "
                "is nothing here to append to, and putting one there is a "
                "CREATE, not an edit — a root robots.txt where none existed "
                "changes the crawl rules for the whole domain in one step. "
                "Decide that deliberately; this workflow will not do it as a "
                "side effect of adding two sitemap lines."),
        ),
        Target(
            "blog",
            f"{SITE}/blog/robots.txt",
            [blog_override] if blog_override else [
                "/blog/robots.txt",
                "/public_html/blog/robots.txt",
                "blog/robots.txt",
                "public_html/blog/robots.txt",
            ],
            proven=() if blog_override else
                   [p for p in (derived.get("blog"),) if p],
            required_fragment="blog/robots.txt",
        ),
    ]


# --- the pure part: everything below is decided without a network call -------

def sitemap_urls(body: str) -> list[str]:
    """Every URL a `Sitemap:` directive names, in file order.

    Case-insensitive on the directive because the spec is, and tolerant of
    surrounding whitespace because editors add it.
    """
    found = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("sitemap:"):
            url = stripped.split(":", 1)[1].strip()
            if url:
                found.append(url)
    return found


def guard_fetched(target: Target, status: int, body: str) -> None:
    """May we append to what we just fetched? Raises Refusal if not.

    Every branch here is a surprise, and the response to a surprise is to stop.
    A robots.txt we cannot recognise is one we must not extend.
    """
    if status != 200:
        raise Refusal(f"{target.name}: {target.url} answered HTTP {status}, not 200")
    if not body.strip():
        raise Refusal(f"{target.name}: {target.url} served an empty file")
    if len(body.encode("utf-8")) > MAX_PLAUSIBLE_BYTES:
        raise Refusal(f"{target.name}: {len(body)} bytes is not a robots.txt")
    lowered = body.lower()
    if "<html" in lowered or "<!doctype" in lowered:
        # A host error page, a Cloudflare interstitial, or a catch-all landing
        # page — all of which arrive as 200 and none of which is a robots.txt.
        raise Refusal(f"{target.name}: {target.url} served HTML, not a "
                      f"robots.txt. {target.absent_note}".rstrip())
    if EXISTING_SITEMAP not in sitemap_urls(body):
        raise Refusal(
            f"{target.name}: the expected line 'Sitemap: {EXISTING_SITEMAP}' is "
            f"not in the file we fetched. Something changed. Refusing to append "
            f"to a file we do not recognise.")


def plan_append(body: str) -> tuple[str, list[str]]:
    """The file we intend, and the lines we intend to add.

    Append only: the original text is never re-emitted, only extended. A
    sitemap already present is simply not added again, which is what makes a
    re-dispatch a no-op instead of a duplicate.
    """
    present = set(sitemap_urls(body))
    missing = [url for url in NEW_SITEMAPS if url not in present]
    if not missing:
        return body, []
    lines = [f"Sitemap: {url}" for url in missing]
    prefix = body if body.endswith("\n") else body + "\n"
    return prefix + "\n".join(lines) + "\n", lines


def verify_after(before: str, intended: str, after: str, status: int) -> list[str]:
    """Everything that must be true of the live file after a write.

    Returns the problems, so the caller can print all of them rather than the
    first. An empty list is the only pass.
    """
    problems = []
    if status != 200:
        problems.append(f"re-fetch answered HTTP {status}, not 200")
        return problems          # nothing below is meaningful without a body
    if not after.strip():
        problems.append("re-fetch served an empty file")
        return problems

    urls = sitemap_urls(after)
    if EXISTING_SITEMAP not in urls:
        problems.append(f"the original 'Sitemap: {EXISTING_SITEMAP}' line is gone")
    for url in NEW_SITEMAPS:
        if url not in urls:
            problems.append(f"'Sitemap: {url}' is not in the file we just wrote")

    # Truncation. Every non-blank line that was there has to still be there;
    # a file that kept the first directive and lost the tenth would pass a
    # length check alone if the appended bytes happened to make up the shortfall.
    after_lines = {line.strip() for line in after.splitlines() if line.strip()}
    lost = [line.strip() for line in before.splitlines()
            if line.strip() and line.strip() not in after_lines]
    if lost:
        problems.append(f"{len(lost)} line(s) from the original are missing: {lost[:5]}")

    expected = len(intended.encode("utf-8"))
    actual = len(after.encode("utf-8"))
    if abs(actual - expected) > LENGTH_TOLERANCE:
        problems.append(
            f"length is {actual} bytes, expected about {expected} "
            f"(before: {len(before.encode('utf-8'))}) — the file was truncated "
            f"or something else wrote it")
    return problems


def render_diff(before: str, after: str, label: str) -> str:
    if before == after:
        return f"  (no change to {label})"
    lines = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"{label} (live)", tofile=f"{label} (intended)", n=3)
    return "".join("  " + line if line.endswith("\n") else "  " + line + "\n"
                   for line in lines).rstrip("\n")


# --- transports: the only two things that touch the network ------------------

class HttpFetcher:
    """Cache-busted, browser-shaped, and patient with this host's random 5xx."""

    def __init__(self, *, attempts: int = 3, timeout: int = 30, sleep=time.sleep):
        self.attempts = attempts
        self.timeout = timeout
        self.sleep = sleep

    def get(self, url: str) -> tuple[int, str]:
        import requests

        bust = f"cb=tit{int(time.time())}{random.randint(1000, 9999)}"
        target = f"{url}{'&' if '?' in url else '?'}{bust}"
        last = 0
        for attempt in range(1, self.attempts + 1):
            try:
                resp = requests.get(
                    target,
                    headers={"User-Agent": USER_AGENT,
                             "Cache-Control": "no-cache",
                             "Pragma": "no-cache"},
                    timeout=self.timeout,
                )
            except Exception as exc:                       # network, DNS, TLS
                last = 0
                print(f"    fetch attempt {attempt} failed: "
                      f"{type(exc).__name__}: {exc}")
            else:
                last = resp.status_code
                # A 5xx here is this host under load, not a verdict about the
                # file. Rolling back on one would be an outage we caused.
                if resp.status_code < 500:
                    return resp.status_code, resp.text
                print(f"    fetch attempt {attempt} got HTTP {resp.status_code}")
            if attempt < self.attempts:
                self.sleep(attempt * 3)
        return last, ""


class FtpTransport:
    """FTPS read and write, one connection for the whole run."""

    def __init__(self, host: str, user: str, password: str, port: int = 21,
                 timeout: int = 60):
        from ftplib import FTP_TLS

        self.ftp = FTP_TLS(timeout=timeout)
        self.ftp.connect(host, port)
        self.ftp.login(user, password)
        self.ftp.prot_p()

    def read(self, path: str) -> bytes | None:
        buffer = io.BytesIO()
        try:
            self.ftp.retrbinary(f"RETR {path}", buffer.write)
        except Exception:
            return None
        return buffer.getvalue()

    def write(self, path: str, data: bytes) -> None:
        if NEVER_WRITE_INSIDE in path:
            # deploy-plugin.yml owns everything under wp-content/plugins, and a
            # robots.txt is never in there anyway. Last line before the socket.
            raise Refusal(f"refusing to write inside wp-content: {path}")
        self.ftp.storbinary(f"STOR {path}", io.BytesIO(data))

    def session_report(self, paths: list[str]) -> list[str]:
        return session_report_lines(self.ftp.pwd, self.ftp.nlst, paths)

    def close(self) -> None:
        try:
            self.ftp.quit()
        except Exception:
            pass


def candidate_paths(target: Target) -> list[str]:
    """Every path worth looking at, proven ones first, guesses filtered."""
    out = []
    for path in target.proven:
        if path and path not in out and NEVER_WRITE_INSIDE not in path:
            out.append(path)
    for path in target.candidates:
        if not path or path in out:
            continue
        if NEVER_WRITE_INSIDE in path:
            continue
        if target.required_fragment and target.required_fragment not in path:
            continue
        if target.forbidden_fragment and target.forbidden_fragment in path:
            continue
        out.append(path)
    return out


def session_report_lines(pwd, lister, paths: list[str]) -> list[str]:
    """Where the server actually put us, and what is there. READ ONLY.

    Directories in the order most likely to answer the question: the login
    directory the server chose, every parent of it, the parent of each
    candidate we tried, and `/`. A server that refuses a listing SAYS SO — an
    empty report and a forbidden one are different facts, and printing nothing
    for both is how the next dispatch learns nothing either.

    Takes the two callables rather than a connection so the fakes in the test
    suite exercise this code instead of a lookalike of it.
    """
    lines = []
    try:
        cwd = pwd()
        lines.append(f"login directory: {cwd}")
    except Exception as exc:
        cwd = ""
        lines.append(f"login directory: refused ({type(exc).__name__}: {exc})")

    wanted, seen = [], set()

    def add(directory):
        directory = directory or "/"
        if directory not in seen:
            seen.add(directory)
            wanted.append(directory)

    add(cwd)
    walk = cwd
    while walk and walk not in ("/", "."):
        parent = os.path.dirname(walk.rstrip("/")) or "/"
        if parent == walk:
            break
        walk = parent
        add(walk)
    for path in paths:
        add(os.path.dirname(path) or "/")
    add("/")

    for directory in wanted[:10]:
        try:
            entries = sorted(lister(directory))
        except Exception as exc:
            lines.append(f"{directory}: not listable ({type(exc).__name__}: {exc})")
            continue
        names = [os.path.basename(str(e).rstrip("/")) or str(e) for e in entries]
        marker = "  <-- HAS robots.txt" if "robots.txt" in names else ""
        lines.append(f"{directory}: {len(names)} entries{marker}")
        shown = names[:40]
        lines.append("    " + (", ".join(shown) if shown else "(empty)")
                     + (f", ... +{len(names) - len(shown)} more"
                        if len(names) > len(shown) else ""))
    return lines


def describe_session(ftp, paths: list[str]) -> None:
    """What this FTP session can actually see. Read only, and printed only when
    nothing matched.

    The first real dispatch refused with a list of four paths and no way to
    tell whether the file was somewhere else, the session was chrooted
    elsewhere, or the account simply could not list. A refusal that does not
    say what it looked at costs another dispatch to learn anything, which is
    how a fifth and a sixth blind guess get added.
    """
    lister = getattr(ftp, "session_report", None)
    if lister is None:
        return
    print("  what this FTP session can actually see:")
    try:
        for line in lister(paths):
            print(f"    {line}")
    except Exception as exc:                              # never mask the Refusal
        print(f"    (could not describe the session: "
              f"{type(exc).__name__}: {exc})")


def locate(target: Target, body: str, ftp) -> str:
    """The remote path whose bytes are what `target.url` just served.

    Content is the proof. A path that merely looks right is exactly how a
    write lands on the wrong file and reports success.
    """
    wanted = body.encode("utf-8")
    normalised = wanted.replace(b"\r\n", b"\n").strip()
    paths = candidate_paths(target)
    for path in paths:
        found = ftp.read(path)
        if found is None:
            print(f"    {path} — no such file")
            continue
        if found == wanted:
            return path
        if found.replace(b"\r\n", b"\n").strip() == normalised:
            print(f"    {path} matches apart from line endings — accepted")
            return path
        print(f"    {path} exists but is NOT what {target.url} serves "
              f"({len(found)} bytes vs {len(wanted)}) — not this one")

    describe_session(ftp, paths)
    raise Refusal(
        f"{target.name}: no remote file matched what {target.url} serves. "
        f"Tried {paths}. Refusing to write to a path we cannot prove is the "
        f"right one. The listing above is what the session can see; set "
        f"TIT_ROBOTS_{target.name.upper()}_PATH, or fix "
        f"WP_PLUGIN_REMOTE_DIR, once the real path is visible in it.")


# --- one target, end to end --------------------------------------------------

def process(target: Target, *, http, ftp, apply: bool,
            backup_dir: str | None = None) -> dict:
    """Fetch, guard, plan, and — only when `apply` — write, verify, roll back.

    Each target is independent by construction: it raises, the caller records
    it, and the next target starts from its own fetch. A failure on the root
    copy never leaves the blog copy half-written.
    """
    print(f"\n--- {target.name}: {target.url}")
    status, body = http.get(target.url)
    guard_fetched(target, status, body)
    print(f"  fetched {len(body.encode('utf-8'))} bytes, "
          f"{len(sitemap_urls(body))} sitemap line(s)")

    intended, added = plan_append(body)
    print(render_diff(body, intended, f"{target.name}/robots.txt"))

    outcome = {"target": target.name, "url": target.url,
               "before_bytes": len(body.encode("utf-8")),
               "added": added, "path": None, "backup": None}

    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        path = os.path.join(backup_dir, f"robots-{target.name}-before.txt")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(body)
        outcome["backup"] = path
        print(f"  rollback copy kept at {path}")

    if not added:
        print("  both sitemap lines are already present — nothing to do")
        outcome["result"] = "already-present"
        return outcome

    if ftp is not None:
        outcome["path"] = locate(target, body, ftp)
        if NEVER_WRITE_INSIDE in outcome["path"]:
            raise Refusal(f"{target.name}: {outcome['path']} is inside "
                          f"wp-content, which deploy-plugin.yml owns")
        print(f"  remote path proven by content: {outcome['path']}")

    if not apply:
        outcome["result"] = "dry-run"
        print("  DRY RUN — nothing was written")
        return outcome

    if ftp is None:
        raise Refusal(f"{target.name}: --apply needs FTP credentials")

    original = body.encode("utf-8")
    ftp.write(outcome["path"], intended.encode("utf-8"))
    print(f"  wrote {len(intended.encode('utf-8'))} bytes to {outcome['path']}")

    after_status, after = http.get(target.url)
    problems = verify_after(body, intended, after, after_status)
    if not problems:
        outcome["result"] = "written"
        outcome["after_bytes"] = len(after.encode("utf-8"))
        print(f"  VERIFIED: HTTP {after_status}, "
              f"{outcome['before_bytes']} -> {outcome['after_bytes']} bytes, "
              f"{len(sitemap_urls(after))} sitemap lines, original intact")
        return outcome

    print(f"  VERIFICATION FAILED — rolling back {outcome['path']}")
    for problem in problems:
        print(f"    - {problem}")
    ftp.write(outcome["path"], original)

    back_status, back = http.get(target.url)
    if back_status == 200 and back.strip() == body.strip():
        outcome["result"] = "rolled-back"
        raise Rolledback(
            f"{target.name}: the write did not verify and was rolled back. "
            f"The live file is what it was. Problems: {problems}")

    outcome["result"] = "rollback-failed"
    raise RollbackFailed(
        f"{target.name}: the write did not verify AND the rollback did not "
        f"take (re-fetch HTTP {back_status}, {len(back)} bytes vs "
        f"{len(body)} expected). The live robots.txt is in an unknown state. "
        f"The original bytes are in the run's artifact. Fix by hand.")


def _ftp_from_env():
    host = (os.environ.get("FTP_HOST") or "").strip()
    user = (os.environ.get("FTP_USERNAME") or "").strip()
    password = os.environ.get("FTP_PASSWORD") or ""
    port = int((os.environ.get("FTP_PORT") or "21").strip() or 21)
    if not (host and user and password):
        raise Refusal("FTP_HOST / FTP_USERNAME / FTP_PASSWORD are not all set")
    return FtpTransport(host, user, password, port)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="actually write. Without it this is a dry run.")
    # Defaults to `blog` because that is the only copy that exists. `both` is
    # kept, and kept honest: it refuses the root and says why, which is more
    # useful than pretending the target is not there.
    parser.add_argument("--targets", default="blog",
                        choices=("both", "root", "blog"))
    parser.add_argument("--no-ftp", action="store_true",
                        help="diff from HTTP alone: no credentials, no path proof")
    parser.add_argument("--backup-dir", default=None,
                        help="keep each fetched file here before touching anything")
    args = parser.parse_args(argv)

    targets = [t for t in default_targets()
               if args.targets == "both" or t.name == args.targets]

    print("=" * 72)
    print("ROBOTS.TXT SITEMAP LINES")
    print("=" * 72)
    print(f"  mode        {'APPLY' if args.apply else 'dry run'}")
    print(f"  targets     {', '.join(t.name for t in targets)}")
    print(f"  must exist  Sitemap: {EXISTING_SITEMAP}")
    for url in NEW_SITEMAPS:
        print(f"  adding      Sitemap: {url}")

    ftp = None
    if not args.no_ftp:
        try:
            ftp = _ftp_from_env()
        except Refusal as exc:
            if args.apply:
                print(f"\nSTOPPING: {exc}", file=sys.stderr)
                return 1
            print(f"\n  no FTP ({exc}) — HTTP-only dry run")

    http = HttpFetcher()
    outcomes, failures = [], []
    try:
        for target in targets:
            try:
                outcomes.append(process(target, http=http, ftp=ftp,
                                        apply=args.apply,
                                        backup_dir=args.backup_dir))
            except (Refusal, Rolledback, RollbackFailed) as exc:
                failures.append((type(exc).__name__, str(exc)))
                print(f"  {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        if ftp is not None:
            ftp.close()

    print("\n" + "=" * 72)
    for outcome in outcomes:
        print(f"  {outcome['target']:<6} {outcome['result']}"
              + (f"  ({len(outcome['added'])} line(s) added)"
                 if outcome.get("result") == "written" else ""))
    for kind, message in failures:
        print(f"  FAILED {kind}: {message}")
    print("=" * 72)

    if any(kind == "RollbackFailed" for kind, _ in failures):
        print("::error::A robots.txt is in an unknown state. Fix it by hand "
              "from the backup artifact before anything else runs.", file=sys.stderr)
        return 2
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
