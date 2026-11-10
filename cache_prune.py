"""Prune the repo's Actions caches: by age, then LRU down to a size target.

Run by .github/workflows/actions-cache-prune.yml. The newest cache for each
key prefix on the default branch is never deleted, so builds do not start cold.
The age-only prune left the cache at 100% (limits-watch, 2026-11-10); the size
target (default 7 GB) sits under the 80% amber line of the 10 GB cap.
"""
import datetime as dt
import json
import os
import re
import urllib.request

GB = 10**9


_VOLATILE = re.compile(r"^(\d+|[0-9a-f]{6,})$")


def key_prefix(key):
    """Cache key without its trailing hash/number segments.

    'pip-Linux-abc123' -> 'pip-Linux'; CodeQL's '...-2.27.4-<sha>-<run id>-1'
    -> '...-2.27.4' (stripping only the last segment gave every CodeQL cache
    its own prefix, so all were protected and nothing was freed, 2026-11-10).
    """
    parts = key.split("-")
    while len(parts) > 1 and _VOLATILE.match(parts[-1]):
        parts.pop()
    return "-".join(parts)


def _seen(c):
    return dt.datetime.fromisoformat(c["last_accessed_at"].replace("Z", "+00:00"))


def select_deletions(caches, *, now, max_age_days, target_bytes, default_ref):
    if max_age_days < 1:
        raise ValueError("max_age_days must be at least 1")
    protected = {}
    for c in caches:
        if c.get("ref") == default_ref:
            p = key_prefix(c["key"])
            if p not in protected or _seen(c) > _seen(protected[p]):
                protected[p] = c
    keep_ids = {c["id"] for c in protected.values()}
    cutoff = now - dt.timedelta(days=max_age_days)
    doomed = [c for c in caches if c["id"] not in keep_ids and _seen(c) < cutoff]
    gone = {c["id"] for c in doomed}
    total = sum(c.get("size_in_bytes", 0) for c in caches if c["id"] not in gone)
    for c in sorted((c for c in caches if c["id"] not in keep_ids | gone), key=_seen):
        if total <= target_bytes:
            break
        doomed.append(c)
        total -= c.get("size_in_bytes", 0)
    return doomed


def main():
    repo, token = os.environ["REPO"], os.environ["GH_TOKEN"]
    days = int(os.environ.get("MAX_AGE_DAYS", "2"))
    target = int(float(os.environ.get("TARGET_GB", "7")) * GB)
    default_ref = "refs/heads/" + os.environ.get("DEFAULT_BRANCH", "main")

    def call(method, path):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/{path}", method=method,
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
        return json.loads(body) if body else None

    caches, page = [], 1
    while True:
        batch = call("GET", f"actions/caches?per_page=100&page={page}").get("actions_caches", [])
        caches += batch
        if len(batch) < 100:
            break
        page += 1
    groups = {}
    for c in caches:
        g = groups.setdefault((c.get("ref"), key_prefix(c["key"])), [0, 0, c["key"]])
        g[0] += 1
        g[1] += c.get("size_in_bytes", 0)
    print(f"{len(groups)} (ref, prefix) groups; largest:")
    for (ref, pre), (n, size, sample) in sorted(groups.items(), key=lambda kv: -kv[1][1])[:8]:
        print(f"  {size/GB:6.2f} GB  {n:4d}  {ref}  {pre}  e.g. {sample}")
    before = sum(c.get("size_in_bytes", 0) for c in caches)
    doomed = select_deletions(caches, now=dt.datetime.now(dt.timezone.utc),
                              max_age_days=days, target_bytes=target, default_ref=default_ref)
    freed = 0
    for c in doomed:
        call("DELETE", f"actions/caches/{c['id']}")
        freed += c.get("size_in_bytes", 0)
    print(f"Caches before: {len(caches)} ({before/GB:.2f} GB). Deleted {len(doomed)} "
          f"(age {days}d + LRU to {target/GB:.0f} GB), freed {freed/GB:.2f} GB, "
          f"now {(before-freed)/GB:.2f} GB")


if __name__ == "__main__":
    main()
