"""The flushed namespace holds CACHES ONLY.

`tit_flush_caches()` runs `DELETE FROM wp_options WHERE option_name LIKE
'_transient_tit_%'` and then `wp_cache_flush()`, and it fires on EVERY write
route - four or more times a day in ordinary operation. For cached DATA that is
exactly right: the data changed, so throw the derived copy away.

Three things that are NOT caches had drifted into that namespace:

    tit_export_rl_<ip>    the 20-exports-per-10-minutes throttle
    tit_feed_rl_<ip>      the 60-feed-builds-per-10-minutes throttle
    tit_alert_<subject>   the legacy three-day alert suppression

so our own collectors were resetting a stranger's rate-limit counter several
times a day, and collapsing a three-day alert window to "until the next collect
run". The second one is the more expensive: a persistent breakage mailing the
owner several times a day is an alarm that gets filtered, and a filtered alarm
is the original silence in a new hat. This endpoint's keyed path dedupes by
cause precisely to avoid that, and the legacy path was undoing it underneath.

WHY OPTIONS AND NOT A RENAMED TRANSIENT. Renaming out of the `tit_` prefix
would dodge the LIKE-delete and nothing else: `tit_flush_caches()` also calls
`wp_cache_flush()`, which under a persistent object cache drops every transient
whatever it is called. An option survives both, which is why
`tit_ci_alert_state` was already built that way.

These tests read the PHP as text; the suite cannot execute it.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN = (Path(__file__).resolve().parent.parent / "wordpress-plugin"
          / "talent-intelligence-tracker")
INCLUDES = PLUGIN / "includes"
DB = (INCLUDES / "db.php").read_text()

#: Every include that could put a key in the flushed namespace.
PHP_FILES = sorted(INCLUDES.glob("*.php")) + [
    PLUGIN / "talent-intelligence-tracker.php"]

#: Keys whose loss is free: they are derived from the database, so a flush
#: costs one recomputation and nothing else. Everything NOT in this shape has
#: to justify itself below.
_TRANSIENT_CALL = re.compile(r"(?:get|set|delete)_transient\(\s*'([^']+)'")


def test_the_durable_store_exists_and_is_an_option():
    assert "function tit_ephemeral_get" in DB
    assert "function tit_ephemeral_set" in DB
    body = DB[DB.index("function tit_ephemeral_set"):]
    assert "update_option(" in body.split("function tit_ephemeral_gc")[0], (
        "the whole point is that it is an option: a transient dies to the LIKE "
        "delete AND to wp_cache_flush()")
    assert "get_transient" not in body.split("function tit_ephemeral_gc")[0]


def test_the_durable_store_does_not_autoload():
    """These are read on the routes that need them. Joining the autoload bundle
    would put a per-IP counter on the query of every page view."""
    setter = DB[DB.index("function tit_ephemeral_set"):]
    setter = setter[:setter.index("function tit_ephemeral_gc")]
    assert "false);" in setter, "update_option's $autoload must be false"


def test_the_value_carries_its_own_expiry():
    """Not a companion `_timeout_` row. Two rows can be separated - by a flush,
    by a partial delete - and then a value outlives its own clock, which for a
    suppression window means an alert that is never sent again."""
    getter = DB[DB.index("function tit_ephemeral_get"):]
    getter = getter[:getter.index("function tit_ephemeral_set")]
    assert "'x'" in getter and "time()" in getter
    assert "delete_option" in getter, "an expired row is dropped on read"


def test_expired_rows_are_swept_so_wp_options_stays_bounded():
    """The throttle keys are per-IP, so without a sweep this grows forever, and
    an unbounded wp_options is a slow site: a worse bug than the one fixed."""
    assert "function tit_ephemeral_gc" in DB
    gc = DB[DB.index("function tit_ephemeral_gc"):]
    assert "LIMIT %d" in gc, "one sweep must never become a long-running DELETE"


def test_the_sweep_is_deterministic_and_on_a_write_path():
    """The first version rolled a 1-in-50 die inside tit_ephemeral_set(), which
    put a SELECT on a READER's request path with probability. It passed locally
    and then fataled in CI when the roll came up on the PHP harness, whose stub
    database has no wp_options table. A cleanup that runs sometimes is a cleanup
    you cannot test and a stack trace you cannot reproduce."""
    setter = DB[DB.index("function tit_ephemeral_set"):]
    setter = setter[:setter.index("/**")]
    assert "rand(" not in setter and "tit_ephemeral_gc" not in setter, (
        "the sweep must not fire probabilistically from a setter")
    flush = DB[DB.index("function tit_flush_caches"):]
    assert "tit_ephemeral_gc()" in flush[:flush.index("\n}")], (
        "tit_flush_caches already deletes from wp_options several times a day, "
        "on writes, which is the right place and cadence for the sweep")


def test_the_sweep_can_never_take_down_a_page():
    """A store whose cleanup can fatal is not an improvement on a transient."""
    gc = DB[DB.index("function tit_ephemeral_gc"):]
    gc = gc[:gc.index("\nfunction ")]
    assert "catch (Exception" in gc and "catch (Error" in gc
    assert "method_exists($wpdb, 'get_col')" in gc


def test_the_sweep_only_removes_expired_rows():
    """Surviving the flush is the whole point; a live row deleted here would
    reintroduce the bug inside the fix."""
    gc = DB[DB.index("function tit_ephemeral_gc"):]
    gc = gc[:gc.index("\nfunction ")]
    assert "(int) $row['x'] <= time()" in gc
    assert "delete_option($option_name)" in gc


def test_the_flush_only_touches_the_transient_namespace():
    flush = DB[DB.index("function tit_flush_caches"):]
    flush = flush[:flush.index("\n}")]
    assert "'_transient_tit_%'" in flush
    assert "tit_eph_" not in flush, (
        "the durable store must not be swept by the very thing it exists to "
        "survive")


def test_the_three_keys_that_are_not_caches_left_the_flushed_namespace():
    """The regression guard. Each of these is a counter or a window, not a
    derived copy of the database, and putting any of them back on a transient
    restores the exact defect."""
    export = (INCLUDES / "export.php").read_text()
    feed = (INCLUDES / "feed.php").read_text()
    api = (INCLUDES / "api.php").read_text()

    htaccess = (INCLUDES / "htaccess.php").read_text()

    throttle = export[export.index("function tit_export_throttle"):]
    throttle = throttle[:throttle.index("\n}")]
    assert "tit_ephemeral_get" in throttle and "tit_ephemeral_set" in throttle
    assert not _TRANSIENT_CALL.search(throttle)

    cap = feed[feed.index("function tit_feed_over_cap"):]
    cap = cap[:cap.index("\n}")]
    assert "tit_ephemeral_get" in cap and "tit_ephemeral_set" in cap
    assert not _TRANSIENT_CALL.search(cap)

    legacy = api[api.index("---- LEGACY: suppress by subject"):]
    legacy = legacy[:legacy.index("function tit_api_add")]
    assert "tit_ephemeral_get" in legacy and "tit_ephemeral_set" in legacy
    assert not _TRANSIENT_CALL.search(legacy)
    assert "'tit_alert_'" not in api

    # The fourth, found by the named-shape sweep below rather than by the
    # audit: the ONLY mutual exclusion around a write to the site's .htaccess.
    # A flush mid-window admitted a second writer into insert_with_markers().
    ensure = htaccess[htaccess.index("function tit_htaccess_ensure"):]
    ensure = ensure[:ensure.index("$desired = tit_htaccess_block_lines")]
    assert "tit_ephemeral_get('htaccess_lock')" in ensure
    assert "'tit_htaccess_lock'" not in htaccess
    # ...but tit_htaccess_ok stays a transient: losing it costs one
    # re-verification, which is what a cache is for.
    assert "get_transient('tit_htaccess_ok')" in htaccess


def test_no_rate_limit_or_suppression_key_is_a_transient_anywhere():
    """Named-shape sweep, so a NEW throttle cannot be added on a transient and
    quietly inherit the same bug."""
    suspicious = re.compile(r"_(?:rl|ratelimit|throttle|lock|suppress)_?|alert_")
    for path in PHP_FILES:
        for key in _TRANSIENT_CALL.findall(path.read_text()):
            assert not suspicious.search(key), (
                f"{path.name}: '{key}' looks like a counter, a lock or a "
                "suppression window, and it is in the namespace "
                "tit_flush_caches() deletes several times a day. Use "
                "tit_ephemeral_get/set instead.")


def test_every_call_site_degrades_rather_than_fatals_on_the_ftp_race():
    """db.php can be missing for a few seconds mid-upload, exactly like every
    other cross-file call in this plugin. An unthrottled export for those
    seconds is a far better outcome than a fatal on the route."""
    for name, fn in (("export.php", "tit_export_throttle"),
                     ("feed.php", "tit_feed_over_cap")):
        text = (INCLUDES / name).read_text()
        body = text[text.index(f"function {fn}"):]
        body = body[:body.index("\n}")]
        assert "function_exists('tit_ephemeral_get')" in body, name

    api = (INCLUDES / "api.php").read_text()
    legacy = api[api.index("---- LEGACY: suppress by subject"):]
    legacy = legacy[:legacy.index("function tit_api_add")]
    assert "function_exists('tit_ephemeral_get')" in legacy
    # And it fails OPEN: a duplicate email costs a second read, a swallowed one
    # costs the thing this route exists for.
    assert "function_exists('tit_ephemeral_get') && tit_ephemeral_get(" in legacy
