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


def php():
    return HTACCESS.read_text()


def directives():
    """Only the lines that actually become Apache config.

    Asserting against the whole file would match the explanatory comments too,
    which is how a test starts passing on prose instead of on behaviour.
    """
    body = php()
    # The condition lines are PHP concatenations. Resolve the interpolated
    # variables so the assertions read the Apache directive, not the source.
    anon = re.search(r"\$anon\s*=\s*'([^']+)'", body).group(1)
    lines = []
    for m in re.finditer(r"^\s*'(<If .*|Header .*|</?IfModule.*|</If>)',?$", body, re.M):
        line = m.group(1)
        line = line.replace("' . $anon . '", anon)
        line = re.sub(r"' \. \$(rest|assets) \. '", r"{\1}", line)
        assert "$" not in line, f"unresolved PHP in a directive: {line}"
        lines.append(line)
    return lines


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
    assert "%{HTTP_COOKIE} !~ /wordpress_logged_in/" in endpoint, (
        "logged-in responses must not be handed to a shared cache"
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
    # Split the emitted directives into one group per <If> block.
    blocks, current = [], None
    for line in directives():
        if line.startswith("<If "):
            current = []
        elif line == "</If>":
            blocks.append(current)
            current = None
        elif current is not None:
            current.append(line)

    assert len(blocks) == 2, f"expected an endpoint block and an asset block, got {len(blocks)}"
    for block in blocks:
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
    api = (PLUGIN / "includes" / "api.php").read_text()
    php_max_age = re.search(
        r"header\('Cache-Control',\s*'public, max-age=(\d+)'\)", api).group(1)
    endpoint = next(d for d in directives()
                    if d.startswith("Header set Cache-Control") and "immutable" not in d)
    assert f"max-age={php_max_age}" in endpoint, f"{endpoint} vs PHP's {php_max_age}"


def test_assets_get_an_immutable_lifetime():
    """Safe only because the URLs are fingerprinted with TIT_VERSION.filemtime,
    so any change mints a new URL."""
    body = php()
    assert "max-age=31536000, immutable" in body
    assert "TIT_URL . 'assets/'" in body, "asset path must be derived, not hardcoded"
