<?php
/**
 * RSS 2.0 feed of the current view: GET talent/v1/feed.
 *
 * Takes the SAME filter params as /query (it calls tit_build_where, so the two
 * can never drift) and returns the newest 50 matching signals as RSS. The point
 * is "subscribe to what I am looking at": the dashboard's RSS link carries the
 * active querystring, so a reader can put a one-country or one-industry cut of
 * the feed into their reader.
 *
 * Caching discipline is /query's own: a transient keyed on the whitelisted
 * filter params (tit_cache_key), wiped by tit_flush_caches() on every data
 * write, plus the same public Cache-Control for the CDN edge. The rate cap
 * only counts requests that MISS the transient: a cached hit costs one option
 * read, and throttling those would punish exactly the polling a feed invites.
 *
 * Item fields, and why: title is the headline, link is the SOURCE document
 * (the whole promise of this product is that every update links to the
 * document behind it, so the feed points there and never at ourselves), guid
 * is the signal_id with isPermaLink="false" (it is an identifier, not a URL),
 * pubDate is the source's own published_date falling back to capture date,
 * category is the pillar.
 */

if (!defined('ABSPATH')) exit;

const TIT_FEED_MAX_ITEMS = 50;

function tit_register_feed_route() {
    // api.php can be mid-upload on an FTP deploy; without it there is no
    // namespace and no WHERE builder, so register nothing and degrade.
    if (!defined('TIT_NS') || !function_exists('tit_build_where')) return;
    register_rest_route(TIT_NS, '/feed', array(
        'methods' => 'GET', 'callback' => 'tit_api_feed',
        'permission_callback' => '__return_true',
    ));
}
add_action('rest_api_init', 'tit_register_feed_route');

/**
 * XML text escape. ENT_QUOTES covers & < > " ' which is everything XML needs;
 * on top of that, control characters are stripped because they are ILLEGAL in
 * XML 1.0 however they are escaped, and one stray byte from a scraped headline
 * would invalidate the whole document for every subscriber.
 */
function tit_feed_esc($s) {
    $s = preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F]/', '', (string) $s);
    return htmlspecialchars($s, ENT_QUOTES, 'UTF-8');
}

/**
 * RFC 822 date, as RSS 2.0 requires. published_date is a bare date, so it is
 * pinned to midnight UTC; captured_at carries a time and keeps it.
 */
function tit_feed_date($date) {
    $ts = strtotime((string) $date . ' UTC');
    if (!$ts) return '';
    return gmdate('D, d M Y H:i:s', $ts) . ' +0000';
}

/**
 * The channel and items, as a string. Split from the route callback so the
 * harness can run it against real rows without a REST server.
 *
 * $self is the feed URL including filters (the atom:self link every validator
 * asks for); $page_url is the HTML view the feed mirrors.
 */
function tit_feed_xml(array $rows, $self, $page_url) {
    $x  = '<?xml version="1.0" encoding="UTF-8"?>' . "\n";
    $x .= '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">' . "\n<channel>\n";
    $x .= '<title>Talent Intelligence Tracker</title>' . "\n";
    $x .= '<link>' . tit_feed_esc($page_url) . '</link>' . "\n";
    $x .= '<description>Sourced talent market signals: hiring, funding, leadership and ways-of-working updates, each one linked to the document behind it. Filtered views carry their filters.</description>' . "\n";
    $x .= '<language>en</language>' . "\n";
    $x .= '<ttl>60</ttl>' . "\n";
    $x .= '<lastBuildDate>' . gmdate('D, d M Y H:i:s') . ' +0000</lastBuildDate>' . "\n";
    $x .= '<atom:link href="' . tit_feed_esc($self) . '" rel="self" type="application/rss+xml" />' . "\n";

    foreach ($rows as $r) {
        $r = (array) $r;
        $x .= "<item>\n";
        $x .= '<title>' . tit_feed_esc($r['headline'] ?? '') . '</title>' . "\n";
        // The source document, never our own page: the feed's promise is the
        // page's promise, every update links to what it is based on.
        $x .= '<link>' . tit_feed_esc($r['source_url'] ?? '') . '</link>' . "\n";
        $x .= '<guid isPermaLink="false">' . tit_feed_esc($r['signal_id'] ?? '') . '</guid>' . "\n";
        $when = !empty($r['published_date']) ? $r['published_date'] : ($r['captured_at'] ?? '');
        $pub = tit_feed_date($when);
        if ($pub !== '') $x .= '<pubDate>' . $pub . '</pubDate>' . "\n";
        if (!empty($r['pillar'])) {
            $x .= '<category>' . tit_feed_esc($r['pillar']) . '</category>' . "\n";
        }
        $desc = trim((string) ($r['summary'] ?? ''));
        $rt = trim((string) ($r['talent_readthrough'] ?? ''));
        if ($rt !== '') $desc = trim($desc . ($desc === '' ? '' : ' ') . $rt);
        if (!empty($r['source_name'])) {
            $desc = trim($desc . ($desc === '' ? '' : ' ') . 'Source: ' . $r['source_name'] . '.');
        }
        $x .= '<description>' . tit_feed_esc($desc) . '</description>' . "\n";
        $x .= "</item>\n";
    }

    return $x . "</channel>\n</rss>\n";
}

/**
 * The newest matching rows, under the caller's own filters. Newest first is
 * the only order a feed reader understands; the dashboard's richer sorts stay
 * on the dashboard.
 */
function tit_feed_rows(WP_REST_Request $req) {
    global $wpdb;
    $params = array();
    $where  = tit_build_where($req, $params);
    $table  = tit_table_name();
    $sql = "SELECT signal_id, headline, summary, talent_readthrough, company,
                   pillar, source_url, source_name, published_date, captured_at
              FROM {$table} WHERE {$where}
             ORDER BY COALESCE(published_date, DATE(captured_at)) DESC, row_id DESC
             LIMIT %d";
    return $wpdb->get_results(
        $wpdb->prepare($sql, array_merge($params, array(TIT_FEED_MAX_ITEMS))), ARRAY_A
    ) ?: array();
}

/**
 * The rate cap, counted only on transient MISSES: building the document costs
 * a query, serving the cached copy costs an option read, and a feed exists to
 * be polled. 60 builds / 10 min per IP is far above any reader's poll rate and
 * far below what would let one client hammer the origin.
 */
function tit_feed_over_cap() {
    $ip = isset($_SERVER['REMOTE_ADDR'])
        ? preg_replace('/[^0-9a-f:.]/i', '', (string) $_SERVER['REMOTE_ADDR']) : '0';
    // An OPTION, via tit_ephemeral_* in db.php. As a transient this counter was
    // swept by tit_flush_caches() on every write - the one thing a rate limit
    // must not be, since our own collectors were resetting it several times a
    // day for whoever was polling.
    if (!function_exists('tit_ephemeral_get')) return false;  // FTP race: fail open
    $key = 'feed_rl_' . md5($ip);
    $n = (int) tit_ephemeral_get($key);
    if ($n >= 60) return true;
    tit_ephemeral_set($key, $n + 1, 10 * MINUTE_IN_SECONDS);
    return false;
}

function tit_api_feed(WP_REST_Request $req) {
    // The feed takes /query's filters, so it inherits /query's answer to an
    // unknown value: refuse it. A feed that quietly served the WHOLE corpus
    // because one word in the subscribed URL was misspelled is the same defect
    // wearing a different content type, and a feed reader polls it forever.
    if (function_exists('tit_validate_filters')) {
        $invalid = tit_validate_filters($req);
        if (is_wp_error($invalid)) return $invalid;
    }

    $cache_key = tit_cache_key('rss', $req);
    $xml = get_transient($cache_key);

    if ($xml === false) {
        if (tit_feed_over_cap()) {
            return new WP_Error('tit_feed_rate', 'Feed rate limit reached. The cached feed refreshes every few minutes; please poll less often.',
                                array('status' => 429));
        }
        // The share querystring: the whitelisted params only, in a stable
        // order, so the self link and the page link both restore this view.
        $qs_parts = array();
        foreach (array('country', 'country_basis', 'city', 'pillar', 'direction',
                       'confidence', 'company', 'industry', 'state', 'function',
                       'funding', 'since', 'until', 'min_headcount', 'q',
                       'min_funding_usd', 'funding_stage', 'detail',
                       'stated_headcount', 'employer_type', 'work_mode',
                       'deal_type', 'site_event') as $k) {
            $v = $req->get_param($k);
            if ($v !== null && $v !== '') $qs_parts[$k] = (string) $v;
        }
        $qs = http_build_query($qs_parts);
        $self = rest_url('talent/v1/feed') . ($qs ? '?' . $qs : '');
        $page = home_url('/talent-intelligence-tracker/') . ($qs ? '?' . $qs : '');
        $xml = tit_feed_xml(tit_feed_rows($req), $self, $page);
        set_transient($cache_key, $xml, TIT_CACHE_TTL);
    }

    $response = new WP_REST_Response($xml);
    $response->header('Content-Type', 'application/rss+xml; charset=utf-8');
    $response->header('Cache-Control', 'public, max-age=300');
    return $response;
}

/**
 * Serve the XML raw. The REST server JSON-encodes everything by default,
 * which would wrap the whole document in quotes; this filter takes over for
 * exactly the responses whose Content-Type says RSS and echoes the string.
 */
function tit_feed_serve($served, $result, $request) {
    if ($served) return $served;
    if (!($result instanceof WP_REST_Response)) return $served;
    $headers = $result->get_headers();
    $type = isset($headers['Content-Type']) ? $headers['Content-Type'] : '';
    if (strpos((string) $type, 'application/rss+xml') !== 0) return $served;
    echo $result->get_data();
    return true;
}
add_filter('rest_pre_serve_request', 'tit_feed_serve', 10, 3);

/**
 * Advertise the UNFILTERED feed in the head of the dashboard page, so a feed
 * reader pointed at the page URL discovers it. The filtered feed is not
 * advertised here: it belongs to whoever built the filtered view, and the RSS
 * link under the exports hands it to them with their filters attached.
 */
function tit_feed_head_link() {
    if (!defined('TIT_PAGE_SLUG')) return; // page.php mid-upload
    if (!function_exists('is_page') || !is_page(TIT_PAGE_SLUG)) return;
    printf('<link rel="alternate" type="application/rss+xml" title="%s" href="%s" />' . "\n",
        esc_attr('Talent Intelligence Tracker updates'),
        esc_url(rest_url('talent/v1/feed')));
}
add_action('wp_head', 'tit_feed_head_link');
