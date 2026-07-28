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
 * read endpoints, plus the plugin's fingerprinted assets. /source-health is
 * left alone on purpose — it reports collector staleness and must not be
 * served from a cache. THE_REQUEST is the original request line, so it is
 * immune to WP's rewrite to index.php.
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
 * from rest_url(), which is not a path we can match on either; the '?' is
 * rejected here too.
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

function tit_htaccess_block_lines() {
    $rest   = tit_htaccess_safe_path(rest_url('talent/v1/'));           // /blog/wp-json/talent/v1/
    $assets = tit_htaccess_safe_path(TIT_URL . 'assets/');              // /blog/wp-content/plugins/talent-intelligence-tracker/assets/
    if ($rest === '' || $assets === '') return array();

    $anon = '%{HTTP_COOKIE} !~ /wordpress_logged_in/';
    return array(
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
        '<If "%{THE_REQUEST} =~ m#^(GET|HEAD) ' . $assets . '#">',
        'Header always unset Cache-Control',
        'Header always unset Pragma',
        'Header always unset Expires',
        'Header unset Pragma',
        'Header unset Expires',
        'Header set Cache-Control "public, max-age=31536000, immutable"',
        '</If>',
        '</IfModule>',
    );
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
        update_option('tit_htaccess_state', array('version' => TIT_VERSION, 'status' => 'verified', 'at' => time()), false);
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
    update_option('tit_htaccess_state', array(
        'version'       => TIT_VERSION,
        'status'        => 'verified',
        'code'          => $code,
        'cache_control' => is_array($seen) ? implode(' || ', $seen) : (string) $seen,
        'at'            => time(),
    ), false);
    set_transient('tit_htaccess_ok', 1, 12 * HOUR_IN_SECONDS);
}
add_action('init', 'tit_htaccess_ensure');
