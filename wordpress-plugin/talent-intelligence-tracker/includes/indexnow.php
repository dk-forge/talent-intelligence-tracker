<?php
/**
 * IndexNow on publish (SEO audit 2026-10-15).
 *
 * Measured: the tracker never told a search engine that anything changed. Its
 * pages were found only by crawl, on a host whose crawl budget the sibling's
 * 7,000+ pages share. IndexNow pushes changed URLs to Bing (and through it to
 * ChatGPT search), Yandex, Seznam and Naver in one request.
 *
 * THE KEY IS THE SIBLING'S, ON PURPOSE. Both plugins run in the same WordPress
 * install at /blog, and the sibling already mints a private key, keeps it in
 * the database (never in a public repo) and serves it at /blog/<key>.txt. A
 * key at that path is scoped to every URL under /blog/, which includes every
 * page of this tracker. A second key for the same host would be a second file
 * to keep alive for no gain, so this file submits through
 * alt_indexnow_submit_urls() when it exists and records "no key" when it does
 * not. It never invents its own key.
 *
 * SHAPE. A write never blocks on the network: the bulk/add route only queues
 * the touched company keys (capped) and schedules one single event ten minutes
 * out. The event filters the queue down to company pages that are actually in
 * the sitemap (the same servable + threshold decision, so a noindex page is
 * never pushed), adds the hub pages at most once a day, and submits once.
 * Guard: tests/test_indexnow.py.
 */
if (!defined('ABSPATH')) exit;

const TIT_INDEXNOW_PENDING = 'tit_indexnow_pending';
const TIT_INDEXNOW_LAST    = 'tit_indexnow_last';
const TIT_INDEXNOW_MAX     = 500;   // per submission; the protocol allows 10,000

/** PURE. Merge newly touched keys into the pending set, de-duplicated and capped. */
function tit_indexnow_merge_pending($pending, $keys, $cap = TIT_INDEXNOW_MAX) {
    $out = array();
    foreach (array_merge((array) $pending, (array) $keys) as $k) {
        $k = trim((string) $k);
        if ($k === '' || isset($out[$k])) continue;
        $out[$k] = true;
        if (count($out) >= $cap) break;
    }
    return array_keys($out);
}

/**
 * PURE. The URL list for one submission: hubs (when due) first, then the
 * pending company keys that are publishable, as URLs, de-duplicated, capped.
 * $publishable is a set (key => true) of company keys in the sitemap;
 * $url_for maps a key to its canonical URL.
 */
function tit_indexnow_url_list($hubs, $pending, $publishable, $url_for, $cap = TIT_INDEXNOW_MAX) {
    $urls = array();
    foreach ((array) $hubs as $u) {
        if (is_string($u) && $u !== '') $urls[$u] = true;
    }
    foreach ((array) $pending as $k) {
        if (!isset($publishable[$k])) continue;
        $u = (string) call_user_func($url_for, $k);
        if ($u !== '') $urls[$u] = true;
    }
    return array_slice(array_keys($urls), 0, $cap);
}

/** The tracker's hub pages whose content changes whenever rows land. */
function tit_indexnow_hubs() {
    $hubs = array(home_url('/talent-intelligence-tracker/'));
    foreach (array('tit_places_url', 'tit_roles_url', 'tit_press_url', 'tit_net_index_url', 'tit_funded_week_url') as $fn) {
        if (function_exists($fn)) $hubs[] = (string) $fn();
    }
    return $hubs;
}

/** Runtime: called by the write routes with the company keys they stored. */
function tit_indexnow_after_write(array $company_keys) {
    if (!$company_keys) return;
    $pending = get_option(TIT_INDEXNOW_PENDING, array());
    update_option(TIT_INDEXNOW_PENDING, tit_indexnow_merge_pending($pending, $company_keys), false);
    if (function_exists('wp_next_scheduled') && !wp_next_scheduled('tit_indexnow_flush')) {
        wp_schedule_single_event(time() + 10 * MINUTE_IN_SECONDS, 'tit_indexnow_flush');
    }
}

/** Runtime: the scheduled submission. */
function tit_indexnow_flush() {
    $pending = (array) get_option(TIT_INDEXNOW_PENDING, array());
    delete_option(TIT_INDEXNOW_PENDING);
    if (!function_exists('alt_indexnow_submit_urls')) {
        update_option(TIT_INDEXNOW_LAST, array('at' => time(), 'urls' => 0,
            'error' => 'no key: the sibling plugin that serves /blog/<key>.txt is not loaded'), false);
        return;
    }
    $publishable = array();
    if (function_exists('tit_company_sitemap_entries')) {
        foreach (tit_company_sitemap_entries() as $e) $publishable[$e['company_key']] = true;
    }
    // An option, not a transient: tit_flush_caches() deletes every tit_
    // transient on each write, which would make the daily hub throttle reset
    // on exactly the event it throttles.
    $hubs_due = (time() - (int) get_option('tit_indexnow_hubs_at', 0)) >= DAY_IN_SECONDS;
    $urls = tit_indexnow_url_list($hubs_due ? tit_indexnow_hubs() : array(), $pending,
                                  $publishable, 'tit_company_url');
    if (!$urls) return;
    if ($hubs_due) update_option('tit_indexnow_hubs_at', time(), false);
    alt_indexnow_submit_urls($urls, TIT_INDEXNOW_LAST);
}
add_action('tit_indexnow_flush', 'tit_indexnow_flush');
