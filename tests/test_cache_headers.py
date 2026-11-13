"""The host's duplicate no-store must stay stripped.

Bluehost's Apache appends its own "Cache-Control: no-cache, no-store,
must-revalidate" after PHP on every response, so the plugin's max-age=300 on
the public endpoints is merged away and the whole cache layer is inert. The
only place that can win is a mod_headers block in the WP root .htaccess.

A broken .htaccess 500s the entire /blog, so the safety properties here are
not style preferences: without the probe-and-rollback, one bad deploy takes
the site down with no way to reach wp-admin to undo it.
"""

import re
from pathlib import Path

PLUGIN = Path(__file__).parent.parent / "wordpress-plugin" / "talent-intelligence-tracker"
HTACCESS = PLUGIN / "includes" / "htaccess.php"
BOOTSTRAP = PLUGIN / "talent-intelligence-tracker.php"
API = PLUGIN / "includes" / "api.php"

# The real path WordPress hands the block on this install, and the one the
# emitted regex is exercised against below.
PAGE_PATH = "/blog/talent-intelligence-tracker/"


def php():
    return HTACCESS.read_text()


def cache_ttl():
    """TIT_CACHE_TTL, the lifetime the server-side render bundle is stored for."""
    return int(re.search(r"const TIT_CACHE_TTL\s*=\s*(\d+)", API.read_text()).group(1))


def directives():
    """Only the lines that actually become Apache config.

    Asserting against the whole file would match the explanatory comments too,
    which is how a test starts passing on prose instead of on behaviour.

    Two emission styles, because the page block is built conditionally: array
    literal entries ('...',) and appends ($lines[] = '...';). Both are read, so
    a block cannot escape these assertions by changing how it is assembled.
    """
    body = php()
    # The condition lines are PHP concatenations. Resolve the interpolated
    # variables so the assertions read the Apache directive, not the source.
    anon = re.search(r"\$anon\s*=\s*'([^']+)'", body).group(1)
    edge = re.search(r"\$edge\s*=\s*defined\('TIT_CACHE_TTL'\)\s*\?\s*\(int\) TIT_CACHE_TTL\s*:\s*(\d+)", body)
    assert edge, "the page block's edge lifetime must be derived from TIT_CACHE_TTL"

    pattern = r"^\s*(?:'|\$lines\[\] = ')(<If .*|Header .*|</?IfModule.*|</If>)'\s*[,;]$"
    lines = []
    for m in re.finditer(pattern, body, re.M):
        line = m.group(1)
        line = line.replace("' . $anon . '", anon)
        line = line.replace("' . $edge . '", str(cache_ttl()))
        line = re.sub(r"' \. \$(rest|assets|page) \. '", r"{\1}", line)
        assert "$" not in line, f"unresolved PHP in a directive: {line}"
        lines.append(line)
    return lines


def blocks():
    """One (condition, directives) pair per <If> block, in emission order."""
    out, cond, current = [], None, None
    for line in directives():
        if line.startswith("<If "):
            cond, current = line, []
        elif line == "</If>":
            out.append((cond, current))
            cond, current = None, None
        elif current is not None:
            current.append(line)
    assert cond is None, "an <If> block was never closed"
    return out


def block_for(needle):
    """The single block whose condition contains needle."""
    found = [b for b in blocks() if needle in b[0]]
    assert len(found) == 1, f"expected exactly one block matching {needle!r}, got {len(found)}"
    return found[0]


def page_regex():
    """The emitted page-block regex, compiled.

    tit_htaccess_safe_path() escapes '.' and nothing else, and PAGE_PATH has no
    '.' in it, so the real path substitutes in verbatim.
    """
    cond = block_for("{page}")[0]
    body = re.search(r"m#(.+?)#\s*&&", cond).group(1)
    return re.compile(body.replace("{page}", PAGE_PATH))


def test_the_module_exists_and_is_loaded_by_the_bootstrap():
    assert HTACCESS.is_file()
    boot = BOOTSTRAP.read_text()
    # Guarded, not required: an FTP deploy has a window where this file is not
    # on disk yet and a hard require would fatal the whole plugin.
    assert "tit_require('includes/htaccess.php')" in boot
    assert "require_once includes/htaccess.php" not in boot


def test_it_uses_its_own_marker_and_never_the_siblings():
    """Both plugins write into the SAME WP root .htaccess. Sharing a marker
    means each deploy silently deletes the other's rules."""
    body = php()
    assert "const TIT_HTACCESS_MARKER = 'Talent Intelligence Tracker';" in body
    # Every read and write of the block must go through the constant; a
    # literal here is how the two markers drift apart.
    calls = re.findall(
        r"(?:insert_with_markers|extract_from_markers)\([^,()\n]+,\s*([^,()\n]+)", body)
    assert len(calls) >= 3, f"expected read + write + rollback, found {calls}"
    for call in calls:
        assert call.strip() == "TIT_HTACCESS_MARKER", call


def test_the_block_is_scoped_to_anonymous_public_reads():
    conditions = [d for d in directives() if d.startswith("<If ")]
    assert conditions, "no <If> conditions found"
    for cond in conditions:
        assert "(GET|HEAD)" in cond, f"applies to writes: {cond}"
        # /source-health reports collector staleness. A cached staleness report
        # is worse than none: it is how you believe a dead source is alive.
        assert "source-health" not in cond, cond

    endpoint = next(c for c in conditions if "query" in c)
    assert "(query|aggregate|facets)" in endpoint
    for cond in (endpoint, block_for("{page}")[0]):
        assert "%{HTTP_COOKIE} !~ /wordpress_logged_in/" in cond, (
            f"logged-in responses must not be handed to a shared cache: {cond}"
        )


def test_every_block_strips_the_hosts_duplicate_before_setting_its_own():
    """Setting Cache-Control without unsetting first leaves TWO headers and
    no-store still wins. Pragma and Expires are injected too and carry the
    same meaning to intermediaries, so EVERY block has to clear all three --
    the first live deploy shipped an asset block that cleared only
    Cache-Control and left Pragma/Expires on the response.
    """
    required = (
        "Header always unset Cache-Control",
        "Header always unset Pragma",
        "Header always unset Expires",
        "Header unset Pragma",
        "Header unset Expires",
    )
    found = blocks()
    assert len(found) == 3, (
        f"expected an endpoint, an asset and a page block, got {len(found)}"
    )
    for _cond, block in found:
        for directive in required:
            assert directive in block, f"{directive} missing from {block}"
        assert any(d.startswith("Header set Cache-Control ") for d in block), block


def test_a_bad_write_is_probed_and_rolled_back():
    """This is the property that keeps a syntax error in the block from 500-ing
    every page under /blog, wp-admin included."""
    body = php()
    assert "wp_remote_get" in body, "no loopback probe"
    assert "'cb'" in body, "probe must bust the Cloudflare edge to reach origin Apache"
    assert "file_get_contents($file)" in body, "no backup taken before writing"
    assert re.search(r"\$code === 0 \|\| \$code >= 500", body), "5xx and no-answer must both roll back"
    assert "file_put_contents($file, $backup" in body, "no restore path"


def test_a_failed_attempt_does_not_retry_forever():
    body = php()
    assert "tit_htaccess_lock" in body, "no throttle between attempts"
    assert "'status' => 'failed'" in body
    assert "TIT_VERSION" in body, "failure state must be keyed to the version that failed"


def test_a_version_bump_reverifies_the_block():
    """The 12h 'verified' transient means a deploy that CHANGES the rules would
    otherwise sit unapplied while every other cache updated instantly."""
    boot = BOOTSTRAP.read_text()
    assert "delete_transient('tit_htaccess_ok')" in boot


def test_paths_are_validated_before_they_reach_the_regex():
    """ap_expr has no escaping helper. A path carrying a regex metacharacter or
    a '#' produces a block Apache refuses to parse, which is the 500."""
    body = php()
    assert "tit_htaccess_safe_path" in body
    allowlist = re.search(r"preg_match\('#\^\[([^\]]+)\]\+\\z#'", body)
    assert allowlist, "path allowlist must be anchored with \\z, not $"
    for meta in "#?*+()|{}[]^$":
        assert meta not in allowlist.group(1).replace("\\-", ""), meta
    assert "if (!$desired)" in body, "an unvalidatable path must write nothing"


def test_the_endpoint_lifetime_matches_the_one_php_asks_for():
    """The .htaccess block REPLACES tit_public_response()'s header rather than
    repairing it. If the two drift, the value PHP sets becomes fiction and the
    real lifetime is whatever this file happens to say."""
    php_max_age = re.search(
        r"header\('Cache-Control',\s*'public, max-age=(\d+)'\)", API.read_text()).group(1)
    # Selected by its own condition, not by position: "the first non-immutable
    # Header set" silently became the wrong block the day a third one was added.
    _cond, block = block_for("(query|aggregate|facets)")
    endpoint = next(d for d in block if d.startswith("Header set Cache-Control"))
    assert f"max-age={php_max_age}" in endpoint, f"{endpoint} vs PHP's {php_max_age}"


def test_assets_get_an_immutable_lifetime():
    """Safe only because the URLs are fingerprinted with TIT_VERSION.filemtime,
    so any change mints a new URL."""
    body = php()
    assert "max-age=31536000, immutable" in body
    assert "TIT_URL . 'assets/'" in body, "asset path must be derived, not hardcoded"


# --- the dashboard page block -------------------------------------------------
#
# The page is the only HTML this plugin hands to a shared cache. Everything
# below is about the two ways that goes wrong: matching a route that is not the
# page, and outliving the render bundle it copied.

PAGE_MATCHES = [
    "GET /blog/talent-intelligence-tracker/ HTTP/1.1",
    "HEAD /blog/talent-intelligence-tracker/ HTTP/1.1",
    "GET /blog/talent-intelligence-tracker/ HTTP/2.0",
    # Newsletter and social traffic. The edge keys on the full URL and no query
    # parameter reaches the render, so these are safe and worth catching.
    "GET /blog/talent-intelligence-tracker/?utm_source=newsletter HTTP/1.1",
    "GET /blog/talent-intelligence-tracker/?cb=htp1753848000 HTTP/1.1",
    "GET /blog/talent-intelligence-tracker/? HTTP/1.1",
]

PAGE_MUST_NOT_MATCH = [
    # Every other route the plugin serves. Each keeps the host's no-store and
    # gets its own decision; the corrections log because it is the audit trail,
    # and the export because it reads $_GET and rate-limits with a 429 that
    # must not outlive its cause.
    "GET /blog/talent-intelligence-tracker/sources/ HTTP/1.1",
    "GET /blog/talent-intelligence-tracker/corrections/ HTTP/1.1",
    "GET /blog/talent-intelligence-tracker/places/ HTTP/1.1",
    "GET /blog/talent-intelligence-tracker/places/toronto/ HTTP/1.1",
    "GET /blog/talent-intelligence-tracker/recall/ HTTP/1.1",
    "GET /blog/talent-intelligence-tracker/company/openai/ HTTP/1.1",
    "GET /blog/talent-intelligence-tracker/company-sitemap.xml HTTP/1.1",
    "GET /blog/talent-intelligence-tracker/places-sitemap.xml HTTP/1.1",
    "GET /blog/talent-intelligence-tracker/export.csv HTTP/1.1",
    # Writes, admin and login are out of scope by construction.
    "POST /blog/talent-intelligence-tracker/ HTTP/1.1",
    "PUT /blog/talent-intelligence-tracker/ HTTP/1.1",
    "DELETE /blog/talent-intelligence-tracker/ HTTP/1.1",
    "GET /blog/wp-admin/ HTTP/1.1",
    "GET /blog/wp-admin/admin.php?page=tit HTTP/1.1",
    "GET /blog/wp-login.php HTTP/1.1",
    "POST /blog/wp-login.php HTTP/1.1",
    "POST /blog/wp-json/talent/v1/ingest HTTP/1.1",
    "GET /blog/wp-json/talent/v1/source-health HTTP/1.1",
    # Near misses.
    "GET /blog/talent-intelligence-tracker HTTP/1.1",       # the 301, not the page
    "GET /blog/talent-intelligence-tracker/x HTTP/1.1",
    "GET /blog/talent-intelligence-tracker/index.php HTTP/1.1",
    "GET /blog/talent-intelligence-tracker/../wp-admin/ HTTP/1.1",
    "GET /elsewhere/blog/talent-intelligence-tracker/ HTTP/1.1",
    # A space closes the [^ ]* run, so the tail of the request line cannot be
    # smuggled back into the match.
    "GET /blog/talent-intelligence-tracker/?a=b c HTTP/1.1",
]


def test_the_page_block_matches_the_page():
    rx = page_regex()
    for request in PAGE_MATCHES:
        assert rx.search(request), f"page block should match: {request}"


def test_the_page_block_matches_nothing_but_the_page():
    """The whole safety argument for caching HTML here is that ONE route reads
    no request state. A prefix match would hand the same public lifetime to the
    corrections log, the rate-limited export and every company profile, none of
    which have been assessed. Anchoring is the mechanism, so it is asserted
    rather than trusted: THE_REQUEST is "GET <path> HTTP/1.1", and requiring an
    optional query string and then the space before HTTP/ is what stops one
    character of extra path getting in."""
    rx = page_regex()
    for request in PAGE_MUST_NOT_MATCH:
        assert not rx.search(request), f"page block must not match: {request}"


def test_the_page_block_is_derived_from_the_permalink():
    """A path assembled from the slug is a guess. If the owner moves the page
    under a parent, a guessed path matches a URL nobody requests: no caching,
    and a block that reports itself verified anyway."""
    body = php()
    assert "get_permalink($page)" in body
    assert "tit_htaccess_page_path()" in body
    assert "if (!defined('TIT_PAGE_SLUG')) return '';" in body, (
        "page.php may not have landed yet on a racing FTP deploy"
    )
    assert "post_status !== 'publish'" in body, (
        "a draft or trashed page answers 404 at that URL, and a public "
        "max-age on a 404 is a 404 the edge repeats"
    )


def test_a_permalink_that_is_not_purely_a_path_is_refused():
    """The plain-permalink trap, and the reason the running harness exists.

    With pretty permalinks off, get_permalink() returns
    "/blog/index.php?page_id=8506". parse_url() files the query in its own
    component, so the path reaching the allowlist is the entirely ordinary
    "/blog/index.php" — and combined with the page block's optional query group
    the emitted condition matches EVERY URL on the install. A public max-age on
    all of it, from a block meant for one page. Same shape for a fragment.

    tests/php/emit_htaccess_block.php proves the behaviour by emitting the block;
    this pins the guard so it cannot be dropped as redundant-looking.
    """
    body = php()
    assert "PHP_URL_QUERY" in body and "PHP_URL_FRAGMENT" in body, (
        "a permalink carrying a query or a fragment is not a path and must be refused"
    )
    assert "=== '.php'" in body or "'.php'" in body, (
        "a .php path is a front controller, not a page"
    )


def test_the_page_lifetime_cannot_outlive_the_render_bundle():
    """The page's edge copy is a copy of tit_dashboard_facts()' transient. Let
    the edge hold it longer than TIT_CACHE_TTL and the page and /aggregate
    publish different figures — the exact thing that transient's TTL was chosen
    to avoid, only now with no way to flush it."""
    _cond, block = block_for("{page}")
    header = next(d for d in block if d.startswith("Header set Cache-Control"))

    edge = int(re.search(r"s-maxage=(\d+)", header).group(1))
    assert edge == cache_ttl(), f"s-maxage={edge} vs TIT_CACHE_TTL={cache_ttl()}"

    # A browser cache can be neither reached nor revalidated; the edge can. So
    # the private lifetime is the shorter of the two, always.
    browser = int(re.search(r"max-age=(\d+)", header).group(1))
    assert browser < edge, f"browser max-age={browser} must be under s-maxage={edge}"

    # The reason the exercise is worth doing: without this, every lapsed entry
    # is somebody paying the cold origin render.
    assert "stale-while-revalidate=" in header, header


def test_the_page_probe_is_recorded_but_never_rolls_back():
    """A cold render is 1.4-4.0s on a host that stalls randomly under load.
    Treating that timeout as "the .htaccess is broken" would discard a good
    block and then sit at failed until the next version bump. The REST probe is
    already sufficient for the failure that matters: a block Apache refuses to
    parse 500s everything under /blog, wp-json included."""
    body = php()
    assert "'page_cache_control'" in body, "no page header recorded for a human to read"
    assert "'page_code'" in body

    # The rollback must be decided before the page probe is even issued, so
    # there is no path on which the page probe can reach it.
    rollback = body.index("file_put_contents($file, $backup")
    page_probe = body.index("$page_url = ")
    assert rollback < page_probe, (
        "the page probe must come after the rollback decision, not feed into it"
    )


def test_the_probe_diagnostics_survive_the_next_tick():
    """The block's lines carry no TIT_VERSION, so a deploy that does not change
    the rules finds the file already correct and returns without probing. That
    early return used to rebuild the state array from scratch, erasing the
    recorded headers on the first tick after every deploy — exactly when someone
    goes looking for them, on a host with no shell to look any other way."""
    body = php()
    carried = re.search(r"foreach \(array\(([^)]*)\) as \$k\)", body)
    assert carried, "the verified-already path must carry the diagnostics forward"
    for field in ("'cache_control'", "'page_cache_control'", "'code'", "'page_code'"):
        assert field in carried.group(1), f"{field} is dropped on the next tick"
    # A reason belongs to the failure that set it; carried forward it reads as a
    # current complaint about a block that is fine.
    assert "'reason'" not in carried.group(1)


def test_the_admin_screen_shows_what_the_page_probe_saw():
    """Recording a diagnostic nobody can read is not a diagnostic. This host
    gives no shell, so the settings screen is the only place the observed page
    header is visible from inside WordPress."""
    boot = BOOTSTRAP.read_text()
    assert "$ht['page_cache_control']" in boot
    assert "esc_html($ht['page_cache_control'])" in boot, "must be escaped"
