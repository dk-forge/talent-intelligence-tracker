<?php
if (!defined('ABSPATH')) exit;

/**
 * Bluehost's Apache appends "Cache-Control: no-cache, no-store, must-revalidate"
 * + "Pragma: no-cache" + "Expires: 0" AFTER PHP's own headers, on every PHP
 * response. So the public API goes out with TWO Cache-Control headers:
 * tit_public_response()'s "public, max-age=300" and the host's no-store.
 * Intermediaries merge the directives and no-store wins, which makes the whole
 * edge/browser cache layer this plugin builds dead on arrival.
 *
 * No WP filter can touch headers injected outside PHP, so the fix has to live
 * at the same layer: a mod_headers block in the WP root .htaccess. Apache
 * merges <If> sections after every other config level (server config, vhost,
 * <Location>), so these directives always run last and win.
 *
 * Scope is deliberately narrow: anonymous GET/HEAD on the three cached public
 * read endpoints, the plugin's fingerprinted assets, and the dashboard page
 * itself. /source-health is left alone on purpose — it reports collector
 * staleness and must not be served from a cache. THE_REQUEST is the original
 * request line, so it is immune to WP's rewrite to index.php.
 *
 * The page block is the newest and the one worth re-reading before touching:
 * every OTHER route this plugin serves (the place pages, the sources page, the
 * corrections log, the company profiles, the recall page, the CSV/JSON export)
 * deliberately keeps the host's no-store and needs its own decision. See the
 * long note over that block.
 *
 * Deployed like the dashboard page: FTP deploys bypass hooks and race
 * mid-upload, so this is a retry-until-verified init hook, not a one-shot.
 * Because a broken .htaccess takes down the whole /blog with 500s, every write
 * is followed by a cache-busted loopback probe; a 5xx (or no answer) restores
 * the previous file content and marks the attempt failed until the next
 * version bump.
 *
 * This is a port of the pattern the sibling AI Layoff Tracker proved on this
 * host. It shares no code with it, by design.
 */

const TIT_HTACCESS_MARKER = 'Talent Intelligence Tracker';

/**
 * ap_expr's m#...# regex has no escaping helper, and a path carrying a '#' or
 * a regex metacharacter would produce a block Apache refuses to parse — which
 * is exactly the failure that 500s all of /blog. Anything unexpected means we
 * write nothing rather than guess.
 *
 * A plain-permalink install returns "/blog/index.php?rest_route=/talent/v1/"
 * from rest_url(), which is not a path we can match on either.
 *
 * Note what this function does NOT do, because it reads as though it does:
 * parse_url() hands back the query in a separate component, so the '?' never
 * reaches the allowlist below and a front-controller URL arrives here looking
 * like the perfectly ordinary path "/blog/index.php". The endpoint and asset
 * blocks survive that by accident — they append a literal segment, so the
 * condition simply matches nothing. A caller that appends an OPTIONAL query
 * group does not get off so lightly: "/blog/index.php" plus "(\?[^ ]*)?"
 * matches every URL on a plain-permalink install. So a caller that allows a
 * query string has to reject a front controller itself, which is what
 * tit_htaccess_page_url() does and why.
 */
function tit_htaccess_safe_path($url) {
    $path = wp_parse_url($url, PHP_URL_PATH);
    if (!is_string($path) || $path === '' || $path[0] !== '/') return '';
    // \z, not $: '$' also matches before a trailing newline, and a newline
    // smuggled into the path would close the directive and open a line of
    // whatever followed it.
    if (!preg_match('#^[A-Za-z0-9/_.\-]+\z#', $path)) return '';
    return str_replace('.', '\.', $path);
}

/**
 * The dashboard page's own path, or '' when we cannot establish one.
 *
 * Read from the page's PERMALINK rather than assembled from the slug, because
 * the owner is free to move the page under a parent and a guessed path would
 * produce a block that matches a URL nobody requests — caching nothing while
 * reporting itself verified.
 *
 * '' on anything unexpected, and the caller then writes the other blocks and
 * leaves the page on the host's no-store. Three ways that happens, all benign:
 *
 *  - page.php has not landed yet, so TIT_PAGE_SLUG does not exist. An FTP
 *    deploy ships files one at a time and this file can run a few seconds
 *    ahead of that one; reading an undefined constant would fatal the plugin.
 *  - the owner has not created the page yet. page.php waits for it too.
 *  - it is not published. A draft or trashed page answers 404 at that URL, and
 *    a public max-age on a 404 is a 404 the edge then repeats. The 12h
 *    re-verify picks the page up once it is published.
 *  - the install is not using pretty permalinks, covered below.
 *
 * THE PLAIN-PERMALINK CASE IS THE DANGEROUS ONE and it is why this is a
 * separate function rather than a call to tit_htaccess_safe_path().
 *
 * With pretty permalinks off, get_permalink() returns
 * "/blog/index.php?page_id=8506" (or "/blog/?page_id=8506"). parse_url() puts
 * the query in its own component, so the path handed to the allowlist is just
 * "/blog/index.php" — which passes, looking entirely ordinary. Combine that
 * with the page block's optional query group and the condition reads
 * "/blog/index\.php(\?[^ ]*)?", which matches EVERY URL on such an install:
 * every page, every feed, every ?rest_route= read. A public max-age on all of
 * it, from one block that was meant to cover a single page.
 *
 * So the rule here is that the permalink must be NOTHING BUT A PATH: no query,
 * no fragment, and not a .php front controller. Stated that way rather than as
 * a list of known-bad shapes, because both of the shapes that got through in
 * testing got through the same way — parse_url() files a component somewhere
 * this code was not looking, and what is left reads as an ordinary path. A
 * fourth component would do it again.
 *
 * Failing the rule leaves the page on the host's no-store, which is the status
 * quo and costs nothing but a slow page.
 *
 * tests/php/emit_htaccess_block.php exercises every one of these. It is the
 * harness that caught the plain-permalink case while every source-reading test
 * passed, which is the argument for it existing.
 */
function tit_htaccess_page_url() {
    if (!defined('TIT_PAGE_SLUG')) return '';
    $page = get_page_by_path(TIT_PAGE_SLUG, OBJECT, 'page');
    if (!$page || $page->post_status !== 'publish') return '';

    $url = (string) get_permalink($page);
    if ($url === '') return '';
    foreach (array(PHP_URL_QUERY, PHP_URL_FRAGMENT) as $part) {
        $value = wp_parse_url($url, $part);
        if ($value !== null && $value !== '') return '';
    }
    if (substr((string) wp_parse_url($url, PHP_URL_PATH), -4) === '.php') return '';
    return $url;
}

function tit_htaccess_page_path() {
    $url = tit_htaccess_page_url();
    return $url === '' ? '' : tit_htaccess_safe_path($url);
}

function tit_htaccess_block_lines() {
    $rest   = tit_htaccess_safe_path(rest_url('talent/v1/'));           // /blog/wp-json/talent/v1/
    $assets = tit_htaccess_safe_path(TIT_URL . 'assets/');              // /blog/wp-content/plugins/talent-intelligence-tracker/assets/
    if ($rest === '' || $assets === '') return array();

    $anon = '%{HTTP_COOKIE} !~ /wordpress_logged_in/';
    $lines = array(
        '# Managed by the Talent Intelligence Tracker plugin (includes/htaccess.php).',
        '# Strips the host-injected duplicate "no-store" Cache-Control/Pragma/Expires',
        '# from the anonymous public read endpoints, then sets the single intended',
        '# header. Manual edits inside this block are overwritten.',
        '<IfModule mod_headers.c>',
        '<If "%{THE_REQUEST} =~ m#^(GET|HEAD) ' . $rest . '(query|aggregate|facets)\b# && ' . $anon . '">',
        'Header always unset Cache-Control',
        'Header always unset Pragma',
        'Header always unset Expires',
        'Header unset Pragma',
        'Header unset Expires',
        'Header set Cache-Control "public, max-age=300, s-maxage=300, stale-while-revalidate=600"',
        '</If>',
        '# Plugin assets are URL-fingerprinted (?ver=TIT_VERSION.filemtime), so a',
        '# year-long immutable lifetime is safe: any change mints a new URL.',
        '# The Pragma/Expires unsets below do NOT take on this block, verified',
        '# live on 1.27.2: a static-file response keeps "Pragma: no-cache" and',
        '# "Expires: 0" while the identical directives strip them cleanly from',
        '# the PHP responses above, so the host injects them onto static files at',
        '# a stage .htaccess cannot reach. They are kept because they are correct',
        '# and cost nothing, not because they work. Harmless in practice:',
        '# Cache-Control beats Expires in any HTTP/1.1 cache (RFC 7234 5.3) and',
        '# Pragma is a REQUEST directive that caches ignore on a response. Do not',
        '# spend another afternoon on it -- the sibling never stripped them here',
        '# either, and its assets have cached correctly for months.',
        '<If "%{THE_REQUEST} =~ m#^(GET|HEAD) ' . $assets . '#">',
        'Header always unset Cache-Control',
        'Header always unset Pragma',
        'Header always unset Expires',
        'Header unset Pragma',
        'Header unset Expires',
        'Header set Cache-Control "public, max-age=31536000, immutable"',
        '</If>',
    );

    /*
      THE DASHBOARD PAGE ITSELF.

      WHY A SHARED COPY IS SAFE. The render reads nothing from the request: not
      $_GET, not a cookie, not a user. tit_dashboard_facts() in shortcodes.php
      owns that invariant and says so above its cache key, and it holds through
      the whole page — the only thing wp_localize_script puts on it is the REST
      base URL and the country name list, both identical for everybody. The
      plugin creates no nonce anywhere (there is not one wp_create_nonce in it
      outside the wp-admin settings form, which this block cannot reach), so
      there is no per-reader token to leak into a shared cache and no form whose
      nonce could go stale behind the edge. Filtering is JavaScript's job
      against /query, which caches on its own parameters. If a future session
      ever makes this render read the request, this block has to go with it.

      ANCHORED, NOT A PREFIX, and this is the part to get right. THE_REQUEST is
      "GET /blog/talent-intelligence-tracker/ HTTP/1.1", so matching the path,
      then an OPTIONAL query string, then the space before HTTP/ matches the
      page and nothing underneath it. Everything else this plugin serves keeps
      the host's no-store and gets its own decision another day:

        /sources/, /corrections/, /places/, /places/<place>/, /recall/,
        /company/<employer>/, both sitemaps, and the CSV/JSON export

      The corrections log is the one that should probably never be cached: it is
      the audit trail, and tit_flush_caches() cannot reach an edge, so a
      correction that had landed would still read as pending there. The export
      must not be either — it reads $_GET and rate-limits with a 429, and a
      cached 429 is a denial of service that outlives its cause. A prefix match
      would have quietly swept up all of them; the sibling's own sources page is
      what that looks like live (two Cache-Control headers, neither intended).
      tests/test_cache_headers.py asserts the non-match rather than probing for
      it, because a regex is cheaper to check offline than over HTTP.

      THE QUERY STRING IS ALLOWED THROUGH. Readers arrive on ?utm_source= from
      newsletters and social; the edge keys on the full URL, so those become
      their own warm entries instead of a permanent cold path, and no query
      parameter reaches the render to vary what it prints. It also leaves an
      easy way to reach origin on purpose: any unique ?cb= value is a guaranteed
      miss, which is how the probe below and the deploy verification measure the
      real render.

      LIFETIMES. s-maxage tracks TIT_CACHE_TTL, because the edge must not
      outlive the server-side bundle it copied: past that point the page and
      /aggregate publish different figures, which is the exact thing that
      bundle's TTL was chosen to avoid. max-age is deliberately shorter — a
      browser cache can be neither reached nor revalidated, an edge cache can.
      stale-while-revalidate is the whole point of the exercise: it is what
      stops a crawler or an unlucky reader paying the cold origin render (1.4s
      to 4.0s, measured) when an entry lapses.

      WHAT IT COSTS. Worst-case visible staleness is s-maxage plus
      stale-while-revalidate, so about 15 minutes at the edge, against a corpus
      collected twice a day. tit_flush_caches() drops the transient and cannot
      touch the edge, so a correction appears at origin at once and at the edge
      within that window. That is the trade, taken deliberately: purging
      Cloudflare needs an API token, and this repo is public.
    */
    $page = tit_htaccess_page_path();
    if ($page !== '') {
        $edge = defined('TIT_CACHE_TTL') ? (int) TIT_CACHE_TTL : 300;
        $lines[] = '# The dashboard page. Anchored to the page itself: the optional query';
        $lines[] = '# string and the space before HTTP/ keep every sub-route out.';
        $lines[] = '<If "%{THE_REQUEST} =~ m#^(GET|HEAD) ' . $page . '(\?[^ ]*)? HTTP/# && ' . $anon . '">';
        $lines[] = 'Header always unset Cache-Control';
        $lines[] = 'Header always unset Pragma';
        $lines[] = 'Header always unset Expires';
        $lines[] = 'Header unset Pragma';
        $lines[] = 'Header unset Expires';
        $lines[] = 'Header set Cache-Control "public, max-age=180, s-maxage=' . $edge . ', stale-while-revalidate=600"';
        $lines[] = '</If>';
    }

    $lines[] = '</IfModule>';
    return $lines;
}

function tit_htaccess_ensure() {
    if (get_transient('tit_htaccess_ok')) return;
    $state = get_option('tit_htaccess_state', array());
    if (($state['status'] ?? '') === 'failed' && ($state['version'] ?? '') === TIT_VERSION) return;
    if (get_transient('tit_htaccess_lock')) return;
    set_transient('tit_htaccess_lock', 1, MINUTE_IN_SECONDS);

    $desired = tit_htaccess_block_lines();
    if (!$desired) {
        update_option('tit_htaccess_state', array('version' => TIT_VERSION, 'status' => 'failed', 'reason' => 'path', 'at' => time()), false);
        error_log('[talent-intelligence-tracker] refusing to write cache-header block: unexpected REST or asset path');
        return;
    }

    require_once ABSPATH . 'wp-admin/includes/misc.php';
    if (!function_exists('insert_with_markers')) return;

    $file = ABSPATH . '.htaccess';
    if (extract_from_markers($file, TIT_HTACCESS_MARKER) === $desired) {
        /*
          Carry the probe diagnostics forward rather than rebuilding the array.

          This is the path taken by almost every tick, and it is reached on a
          version bump too: the block's own lines carry no TIT_VERSION, so a
          deploy that does not change the rules finds the file already correct,
          returns here, and never probes. Rebuilding the array from scratch
          therefore ERASED the recorded headers on the first tick after every
          deploy, which is precisely when someone goes looking for them — and
          this host gives no shell, so that record is the only evidence there is.
          The probed values are the last ones actually observed; say so with
          'at', and do not re-probe just to refresh them.

          'reason' is deliberately not carried: it belongs to the failure that
          set it, and a stale one reads as a current complaint.
        */
        $carry = array();
        foreach (array('code', 'cache_control', 'page_code', 'page_cache_control') as $k) {
            if (isset($state[$k])) $carry[$k] = $state[$k];
        }
        update_option('tit_htaccess_state', $carry + array(
            'version' => TIT_VERSION,
            'status'  => 'verified',
            'at'      => time(),
        ), false);
        set_transient('tit_htaccess_ok', 1, 12 * HOUR_IN_SECONDS);
        return;
    }

    $backup = @file_get_contents($file);
    if (!insert_with_markers($file, TIT_HTACCESS_MARKER, $desired)) {
        update_option('tit_htaccess_state', array('version' => TIT_VERSION, 'status' => 'failed', 'reason' => 'write', 'at' => time()), false);
        error_log('[talent-intelligence-tracker] could not write cache-header block to ' . $file);
        return;
    }

    // A bad .htaccess 500s the entire install: probe before trusting the write.
    // cb busts the Cloudflare edge so the answer comes from origin Apache, and
    // the endpoint probed is one the new block actually matches.
    $probe = wp_remote_get(
        add_query_arg('cb', 'htx' . time(), rest_url('talent/v1/facets')),
        array('timeout' => 15, 'user-agent' => 'TalentIntelligenceTracker/1.0 (+https://asktherecruiter.com)')
    );
    $code = is_wp_error($probe) ? 0 : (int) wp_remote_retrieve_response_code($probe);
    if ($code === 0 || $code >= 500) {
        if ($backup !== false) {
            @file_put_contents($file, $backup, LOCK_EX);
        } else {
            insert_with_markers($file, TIT_HTACCESS_MARKER, array());
        }
        update_option('tit_htaccess_state', array('version' => TIT_VERSION, 'status' => 'failed', 'reason' => 'probe', 'code' => $code, 'at' => time()), false);
        error_log('[talent-intelligence-tracker] cache-header .htaccess block rolled back: probe returned HTTP ' . $code);
        return;
    }

    // Recorded, not enforced. A block that parses but does not strip the
    // duplicate (mod_headers absent, an override the host applies even later)
    // is harmless, so it is not a rollback — but without this the only way to
    // find out is SSH, which this host does not give us. The header arrives as
    // an ARRAY precisely in the case worth diagnosing: two Cache-Control lines.
    $seen = wp_remote_retrieve_header($probe, 'cache-control');

    // The page block, recorded the same way and DELIBERATELY NOT a rollback
    // trigger.
    //
    // The REST probe above is already sufficient to catch the failure that
    // matters: a block Apache refuses to parse 500s everything under /blog,
    // wp-json included, so it cannot break the page while leaving the endpoint
    // healthy. What this second probe adds is the only evidence we can get for
    // whether the new regex actually took, since the answer lives in a response
    // header on a URL we otherwise never look at from inside PHP.
    //
    // It must not be allowed to roll back, for one specific reason: this is a
    // full cold render behind a unique ?cb=, measured at 1.4-4.0s, on a host
    // that 500s and stalls randomly under load. Treating a timeout here as
    // "the .htaccess is broken" would throw away a perfectly good block on a
    // slow afternoon, and then sit at status=failed until the next version bump.
    // So it is scored, not enforced, and the value it records is what a human
    // reads when the page is somehow still uncached.
    $page_cc   = '';
    $page_code = 0;
    // The same '' guards as the block itself: no page, no probe. Asking for the
    // path (not just the URL) so a permalink the regex refused is not probed as
    // though it had been written.
    $page_url = tit_htaccess_page_path() === '' ? '' : tit_htaccess_page_url();
    if ($page_url !== '') {
        $page_probe = wp_remote_get(
            add_query_arg('cb', 'htp' . time(), $page_url),
            array('timeout' => 15, 'user-agent' => 'TalentIntelligenceTracker/1.0 (+https://asktherecruiter.com)')
        );
        $page_code = is_wp_error($page_probe) ? 0 : (int) wp_remote_retrieve_response_code($page_probe);
        $pseen = is_wp_error($page_probe) ? '' : wp_remote_retrieve_header($page_probe, 'cache-control');
        $page_cc = is_array($pseen) ? implode(' || ', $pseen) : (string) $pseen;
    }

    update_option('tit_htaccess_state', array(
        'version'       => TIT_VERSION,
        'status'        => 'verified',
        'code'          => $code,
        'cache_control' => is_array($seen) ? implode(' || ', $seen) : (string) $seen,
        'page_code'          => $page_code,
        'page_cache_control' => $page_cc,
        'at'            => time(),
    ), false);
    set_transient('tit_htaccess_ok', 1, 12 * HOUR_IN_SECONDS);
}
add_action('init', 'tit_htaccess_ensure');
