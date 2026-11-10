"""cache_prune.py: age rule plus a size target, never the newest default-branch cache per prefix.

limits-watch showed the cache at 10.03/10 GB with the age-only prune green
every day (2026-11-10). The prune now also trims LRU to TARGET_BYTES (7 GB,
under the 80% amber line).
"""
import datetime as dt

from cache_prune import key_prefix, select_deletions

NOW = dt.datetime(2026, 11, 10, tzinfo=dt.timezone.utc)
GB = 10**9
MAIN = "refs/heads/main"


def _c(i, key, days_ago, size_gb, ref=MAIN):
    t = (NOW - dt.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {"id": i, "key": key, "ref": ref, "last_accessed_at": t,
            "created_at": t, "size_in_bytes": int(size_gb * GB)}


def _ids(caches, **kw):
    kw.setdefault("max_age_days", 2)
    kw.setdefault("target_bytes", 7 * GB)
    return {c["id"] for c in select_deletions(caches, now=NOW, default_ref=MAIN, **kw)}


def test_key_prefix_drops_hash_suffix():
    assert key_prefix("pip-Linux-3f2a9c") == "pip-Linux"
    assert key_prefix("plain") == "plain"
    assert key_prefix("classifier-eval-v1") == "classifier-eval-v1"


def test_key_prefix_drops_codeql_sha_run_attempt():
    # 2026-11-10 run: 282 caches, 282 prefixes, 0 deleted. CodeQL keys end in
    # -<commit sha>-<run id>-<attempt>; each must share one prefix.
    k = ("codeql-overlay-base-database-1-d953d79b74456ce0-python-2.27.4-"
         "2da5cadea20c8b4430ce6432a70e42a7d9c2db7e-42679859563-1")
    assert key_prefix(k) == "codeql-overlay-base-database-1-d953d79b74456ce0-python-2.27.4"


def test_codeql_caches_trim_to_target():
    cs = [_c(i, f"codeql-db-python-2.27.4-{i:040x}-4267985{i:04d}-1", i * 0.01, 0.1)
          for i in range(100)]
    got = _ids(cs)
    assert 0 not in got and len(got) == 30


def test_age_rule_kept():
    cs = [_c(1, "pip-a", 0, 0.1), _c(2, "pip-b", 5, 0.1, ref="refs/pull/9/merge")]
    assert _ids(cs) == {2}


def test_trims_lru_to_size_target():
    # 10 GB, all fresh: delete oldest-used until <= 7 GB
    cs = [_c(i, f"k{i}-h{i}", i * 0.1, 1.0, ref="refs/pull/1/merge") for i in range(10)]
    got = _ids(cs)
    assert got == {9, 8, 7}
    left = sum(c["size_in_bytes"] for c in cs if c["id"] not in got)
    assert left <= 7 * GB


def test_protects_newest_main_cache_per_prefix():
    cs = [_c(1, "pip-Linux-aaa111", 1.0, 4.0), _c(2, "pip-Linux-bbb222", 0.5, 4.0),
          _c(3, "npm-Linux-x", 1.9, 3.0)]
    # 11 GB; candidates: only id 1 (2 and 3 are newest of their prefix on main)
    assert _ids(cs) == {1}


def test_protected_even_when_old():
    cs = [_c(1, "pip-Linux-a", 30, 1.0)]
    assert _ids(cs) == set()


def test_feature_branch_not_protected():
    cs = [_c(1, "pip-Linux-a", 30, 1.0, ref="refs/heads/feature")]
    assert _ids(cs) == {1}
