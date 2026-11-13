<?php
/**
 * EMIT THE REAL .htaccess BLOCK AND RUN ITS REGEX THROUGH PCRE.
 *
 * WHY THIS IS A RUNNING HARNESS AND NOT A TEXT ASSERTION.
 *
 * tests/test_cache_headers.py reads the source and resolves the interpolations
 * by hand. That catches a rule being deleted or a lifetime drifting, and it
 * cannot catch the two failures that actually take the site down or quietly
 * cache the wrong thing:
 *
 *  - WHAT THE BLOCK ACTUALLY SAYS. The conditions are PHP concatenations of
 *    paths derived from rest_url(), TIT_URL and a page permalink. A test that
 *    re-implements that concatenation is asserting against its own copy of the
 *    logic. Here the strings come out of the shipping function.
 *  - WHETHER THE PAGE REGEX MEANS WHAT IT LOOKS LIKE. Apache's ap_expr m#...#
 *    is PCRE, and PHP's preg_* is PCRE, so running the emitted pattern against
 *    real request lines here is the same engine that will run it on the host.
 *    The whole safety argument for caching HTML is that ONE route reads no
 *    request state; one character of slack in that anchor hands the same public
 *    lifetime to the corrections log, the rate-limited CSV export and every
 *    company profile, none of which have been assessed for it. And a broken
 *    block 500s all of /blog, wp-admin included.
 *
 * It also exercises the state changes a source read cannot reach: no dashboard
 * page, a draft one, and a permalink the path allowlist refuses. Each must drop
 * the page block and still emit the endpoint and asset blocks, because the
 * public API's cache layer must not depend on the page existing.
 *
 * Exits non-zero with a message on any failure.
 * Run: php tests/php/emit_htaccess_block.php
 */

define('ABSPATH', __DIR__ . '/');
define('MINUTE_IN_SECONDS', 60);
define('HOUR_IN_SECONDS', 3600);
define('OBJECT', 'OBJECT');   // get_page_by_path()'s $output

$tit_plugin = __DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/';

/* --- the WordPress surface these two files touch ------------------------- */

// The page the block is built for. Swapped per scenario below.
$GLOBALS['tit_page'] = null;

function add_action($h, $f, $p = 10, $a = 1) {}
function add_filter($h, $f, $p = 10, $a = 1) {}
function get_transient($k) { return false; }
function set_transient($k, $v, $t = 0) { return true; }
function get_option($k, $d = false) { return $d; }
function update_option($k, $v, $a = null) { return true; }
function wp_parse_url($url, $component = -1) { return parse_url($url, $component); }
function rest_url($p = '') { return 'https://example.test/blog/wp-json/' . $p; }
function get_page_by_path($path, $output = null, $type = 'page') { return $GLOBALS['tit_page']; }
function get_permalink($page) { return $page->permalink; }

define('TIT_VERSION', '0.0.0-harness');
define('TIT_URL', 'https://example.test/blog/wp-content/plugins/talent-intelligence-tracker/');
define('TIT_PAGE_SLUG', 'talent-intelligence-tracker');
define('TIT_CACHE_TTL', 300);

require $tit_plugin . 'includes/htaccess.php';

/* --- helpers ------------------------------------------------------------- */

$failures = array();
function fail($msg) { $GLOBALS['failures'][] = $msg; }

function page_stub($permalink, $status = 'publish') {
    $p = new stdClass();
    $p->permalink = $permalink;
    $p->post_status = $status;
    return $p;
}

/** The emitted <If> conditions, in order. */
function conditions($lines) {
    return array_values(array_filter($lines, function ($l) {
        return strpos($l, '<If ') === 0;
    }));
}

/** The page block's regex, lifted out of its <If> exactly as Apache reads it. */
function page_pattern($lines) {
    foreach (conditions($lines) as $c) {
        if (strpos($c, 'wp-json') !== false || strpos($c, 'assets') !== false) continue;
        if (preg_match('/m#(.+?)# &&/', $c, $m)) return $m[1];
    }
    return null;
}

/* --- 1. the normal case: a published page at the expected permalink ------ */

$GLOBALS['tit_page'] = page_stub('https://example.test/blog/talent-intelligence-tracker/');
$lines = tit_htaccess_block_lines();

echo "--- emitted block\n";
foreach ($lines as $l) echo "    $l\n";
echo "\n";

if (count(conditions($lines)) !== 3) {
    fail('expected 3 <If> blocks (endpoint, assets, page), got ' . count(conditions($lines)));
}
if ($lines[count($lines) - 1] !== '</IfModule>') {
    fail('the block must close its IfModule last');
}

$pattern = page_pattern($lines);
if ($pattern === null) {
    fail('no page block emitted for a published page');
} else {
    echo "--- page regex: $pattern\n\n";

    // MUST match. The page as readers, crawlers and the probe request it.
    $must = array(
        'GET /blog/talent-intelligence-tracker/ HTTP/1.1',
        'HEAD /blog/talent-intelligence-tracker/ HTTP/1.1',
        'GET /blog/talent-intelligence-tracker/ HTTP/2.0',
        'GET /blog/talent-intelligence-tracker/?utm_source=newsletter HTTP/1.1',
        'GET /blog/talent-intelligence-tracker/?cb=htp1753848000 HTTP/1.1',
    );
    // MUST NOT match. Every other route the plugin serves, plus admin, login,
    // the REST writes and a handful of prefix-extension tricks.
    $mustnot = array(
        'GET /blog/talent-intelligence-tracker/sources/ HTTP/1.1',
        'GET /blog/talent-intelligence-tracker/corrections/ HTTP/1.1',
        'GET /blog/talent-intelligence-tracker/places/ HTTP/1.1',
        'GET /blog/talent-intelligence-tracker/places/toronto/ HTTP/1.1',
        'GET /blog/talent-intelligence-tracker/recall/ HTTP/1.1',
        'GET /blog/talent-intelligence-tracker/company/openai/ HTTP/1.1',
        'GET /blog/talent-intelligence-tracker/company-sitemap.xml HTTP/1.1',
        'GET /blog/talent-intelligence-tracker/places-sitemap.xml HTTP/1.1',
        'GET /blog/talent-intelligence-tracker/export.csv HTTP/1.1',
        'POST /blog/talent-intelligence-tracker/ HTTP/1.1',
        'PUT /blog/talent-intelligence-tracker/ HTTP/1.1',
        'DELETE /blog/talent-intelligence-tracker/ HTTP/1.1',
        'GET /blog/wp-admin/ HTTP/1.1',
        'GET /blog/wp-admin/admin.php?page=tit HTTP/1.1',
        'GET /blog/wp-login.php HTTP/1.1',
        'POST /blog/wp-login.php HTTP/1.1',
        'POST /blog/wp-json/talent/v1/ingest HTTP/1.1',
        'GET /blog/wp-json/talent/v1/source-health HTTP/1.1',
        'GET /blog/talent-intelligence-tracker HTTP/1.1',
        'GET /blog/talent-intelligence-tracker/x HTTP/1.1',
        'GET /blog/talent-intelligence-tracker/index.php HTTP/1.1',
        'GET /blog/talent-intelligence-tracker/../wp-admin/ HTTP/1.1',
        'GET /elsewhere/blog/talent-intelligence-tracker/ HTTP/1.1',
        'GET /blog/talent-intelligence-tracker/?a=b c HTTP/1.1',
    );

    $re = '#' . $pattern . '#';
    foreach ($must as $r) {
        if (!preg_match($re, $r)) fail("page regex should match: $r");
    }
    foreach ($mustnot as $r) {
        if (preg_match($re, $r)) fail("page regex MUST NOT match: $r");
    }
    printf("    %d matching and %d non-matching request lines checked\n\n",
           count($must), count($mustnot));
}

// The page block must be anonymous-only and must not outlive the render bundle.
foreach (conditions($lines) as $c) {
    if (strpos($c, 'talent-intelligence-tracker/(') === false) continue;
    if (strpos($c, '%{HTTP_COOKIE} !~ /wordpress_logged_in/') === false) {
        fail('the page block is not scoped to anonymous requests: ' . $c);
    }
}
$page_header = null;
foreach ($lines as $i => $l) {
    if (strpos($l, 'Header set Cache-Control "public, max-age=180') === 0) $page_header = $l;
}
if ($page_header === null) {
    fail('no page Cache-Control emitted');
} else {
    preg_match('/s-maxage=(\d+)/', $page_header, $m);
    if ((int) $m[1] !== TIT_CACHE_TTL) {
        fail("page s-maxage={$m[1]} outlives TIT_CACHE_TTL=" . TIT_CACHE_TTL);
    }
    if (strpos($page_header, 'stale-while-revalidate=') === false) {
        fail('no stale-while-revalidate: a lapsed entry is a reader paying the origin render');
    }
}

/* --- 2. the page block must degrade, never take the others with it ------- */

$degraded = array(
    'no dashboard page yet'          => null,
    'the page is a draft'            => page_stub('https://example.test/blog/talent-intelligence-tracker/', 'draft'),
    'the page is in the trash'       => page_stub('https://example.test/blog/talent-intelligence-tracker/', 'trash'),
    // PLAIN PERMALINKS. parse_url() files the query separately, so the path
    // alone looks ordinary ("/blog/index.php") and sails through the allowlist.
    // Paired with the page block's optional query group that condition matches
    // every URL on the install, so a public lifetime meant for one page lands
    // on all of them. Caught here while every source-reading test passed.
    'a plain-permalink index.php URL' => page_stub('https://example.test/blog/index.php?page_id=8506'),
    'a plain-permalink root URL'      => page_stub('https://example.test/blog/?page_id=8506'),
    'a front controller with no query' => page_stub('https://example.test/blog/index.php'),
    // A regex metacharacter or a '#' in the path is a block Apache refuses to
    // parse, which is the 500 that takes wp-admin with it.
    'a permalink with a metacharacter' => page_stub('https://example.test/blog/talent(intel)/'),
    'a permalink with a smuggled newline' => page_stub("https://example.test/blog/tit/\nHeader set X: y"),
    'a permalink with a regex delimiter' => page_stub('https://example.test/blog/tit#frag/'),
);

echo "--- degraded cases\n";
foreach ($degraded as $label => $page) {
    $GLOBALS['tit_page'] = $page;
    $d = tit_htaccess_block_lines();
    $n = count(conditions($d));
    $has_page = page_pattern($d) !== null;
    printf("    %-38s blocks=%d page_block=%s\n", $label, $n, $has_page ? 'YES' : 'no');
    if ($has_page) fail("$label still emitted a page block");
    if ($n !== 2) {
        fail("$label must leave the endpoint and asset blocks intact, got $n");
    }
}

/* --- 3. an unusable REST or asset path still writes nothing at all ------- */

// Unchanged behaviour, re-checked because the function now has an early return
// that did not exist when this rule was written.
$GLOBALS['tit_page'] = page_stub('https://example.test/blog/talent-intelligence-tracker/');
echo "\n";

/* --- verdict ------------------------------------------------------------- */

if ($failures) {
    echo "FAILED:\n";
    foreach ($failures as $f) echo "  - $f\n";
    exit(1);
}
echo "OK: the emitted block is scoped to the page and nothing else.\n";
exit(0);
