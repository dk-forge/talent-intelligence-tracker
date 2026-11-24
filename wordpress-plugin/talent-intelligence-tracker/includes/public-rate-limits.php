<?php
/** Shared origin budgets for public REST callbacks, downloads and forms. */
if (!defined('ABSPATH')) exit;

/** A database lock serializes counters across PHP workers, without object-cache state. */
function tit_public_limit_spend($identity, $limit, $now = null) {
    global $wpdb;
    $now = $now === null ? time() : (int) $now;
    $key = 'rl_tit_public_' . hash_hmac('sha256', $identity, wp_salt('auth'));
    $lock = 'rl_' . substr(hash('sha256', $wpdb->options . $key), 0, 60);
    if ((string) $wpdb->get_var($wpdb->prepare('SELECT GET_LOCK(%s, %d)', $lock, 1)) !== '1') return -1;
    try {
        $raw = $wpdb->get_var($wpdb->prepare("SELECT option_value FROM {$wpdb->options} WHERE option_name = %s", $key));
        if ($wpdb->last_error || ($raw !== null && !preg_match('/^\d+:\d+$/D', $raw))) return -1;
        $parts = $raw === null ? array(0, 0) : array_map('intval', explode(':', $raw));
        [$expires, $count] = $parts;
        if ($expires <= $now) { $expires = $now + 60; $count = 0; }
        if ($count >= $limit) return max(1, $expires - $now);
        $value = $expires . ':' . ($count + 1);
        $saved = $wpdb->query($wpdb->prepare(
            "INSERT INTO {$wpdb->options} (option_name, option_value, autoload) VALUES (%s, %s, 'no') " .
            "ON DUPLICATE KEY UPDATE option_value = VALUES(option_value), autoload = 'no'", $key, $value));
        return $saved === false ? -1 : 0;
    } finally {
        $wpdb->get_var($wpdb->prepare('SELECT RELEASE_LOCK(%s)', $lock));
    }
}

/** True when $ip lies inside any of the CIDR $ranges. */
function tit_public_ip_in_ranges($ip, $ranges) {
    $bin = @inet_pton($ip);
    if ($bin === false) return false;
    foreach ($ranges as $range) {
        [$base, $bits] = array_pad(explode('/', $range, 2), 2, null);
        $net = @inet_pton($base);
        if ($net === false || strlen($net) !== strlen($bin)) continue;
        $bits = $bits === null ? strlen($bin) * 8 : (int) $bits;
        $bytes = intdiv($bits, 8); $rest = $bits % 8;
        if ($bytes && strncmp($bin, $net, $bytes) !== 0) continue;
        if ($rest && ((ord($bin[$bytes]) ^ ord($net[$bytes])) >> (8 - $rest)) !== 0) continue;
        return true;
    }
    return false;
}

/** Published Cloudflare edge ranges. Extend with the tit_public_trusted_proxy_ranges filter. */
function tit_public_trusted_ranges() {
    $cloudflare = array('173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22',
        '141.101.64.0/18', '108.162.192.0/18', '190.93.240.0/20', '188.114.96.0/20', '197.234.240.0/22',
        '198.41.128.0/17', '162.158.0.0/15', '104.16.0.0/13', '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22',
        '2400:cb00::/32', '2606:4700::/32', '2803:f800::/32', '2405:b500::/32', '2405:8100::/32',
        '2a06:98c0::/29', '2c0f:f248::/32');
    $internal = array('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', '127.0.0.0/8',
        '169.254.0.0/16', '::1/128', 'fc00::/7', 'fe80::/10');
    $extra = function_exists('apply_filters') ? apply_filters('tit_public_trusted_proxy_ranges', array()) : array();
    return array($cloudflare, array_merge($internal, is_array($extra) ? $extra : array()));
}

/**
 * Real client address, or null when it cannot be told (callers then fail OPEN for the IP budget).
 * Forwarding headers count ONLY when the TCP peer is a known proxy: a Cloudflare edge
 * (CF-Connecting-IP, then X-Forwarded-For), or a private/configured hop (X-Forwarded-For
 * walked right to left past trusted hops). Any other peer is the client; its headers are ignored.
 */
function tit_public_client_ip() {
    $peer = (string) ($_SERVER['REMOTE_ADDR'] ?? '');
    if (!filter_var($peer, FILTER_VALIDATE_IP)) return null;
    $peer = inet_ntop(inet_pton($peer));
    [$cloudflare, $internal] = tit_public_trusted_ranges();
    $all = array_merge($cloudflare, $internal);
    if (!tit_public_ip_in_ranges($peer, $all)) return $peer;
    $cf = trim((string) ($_SERVER['HTTP_CF_CONNECTING_IP'] ?? ''));
    if ($cf !== '' && tit_public_ip_in_ranges($peer, $cloudflare) && filter_var($cf, FILTER_VALIDATE_IP)) {
        $cf = inet_ntop(inet_pton($cf));
        if (!tit_public_ip_in_ranges($cf, $all)) return $cf;
    }
    $chain = array_reverse(array_map('trim', explode(',', (string) ($_SERVER['HTTP_X_FORWARDED_FOR'] ?? ''))));
    foreach ($chain as $hop) {
        if (!filter_var($hop, FILTER_VALIDATE_IP)) return null;
        $hop = inet_ntop(inet_pton($hop));
        if (!tit_public_ip_in_ranges($hop, $all)) return $hop;
    }
    return null;
}

function tit_public_limit_check() {
    $budgets = array();
    $ip = tit_public_client_ip();
    if ($ip !== null) $budgets[] = array('ip:' . $ip, 120);
    $uid = (int) get_current_user_id();
    if ($uid > 0) $budgets[] = array('user:' . $uid, 60);
    foreach ($budgets as [$identity, $limit]) {
        $retry = tit_public_limit_spend($identity, $limit);
        if ($retry === 0) continue;
        $status = $retry < 0 ? 503 : 429;
        return new WP_REST_Response(array('code' => $retry < 0 ? 'rate_limit_unavailable' : 'rate_limited',
            'message' => 'Please wait and try again.'), $status,
            array('Retry-After' => (string) ($retry < 0 ? 1 : $retry), 'Cache-Control' => 'no-store, private'));
    }
    return null;
}

/** Identify the outer HTTP request without charging internal digest dispatches. */
function tit_public_limit_capture($result, $server, $request) {
    if (defined('REST_REQUEST') && REST_REQUEST && !isset($GLOBALS['tit_public_http_request'])) {
        $GLOBALS['tit_public_http_request'] = $request;
    }
    return $result;
}
add_filter('rest_pre_dispatch', 'tit_public_limit_capture', PHP_INT_MIN, 3);

/** WordPress calls this after permission checks and before the selected callback. */
function tit_public_endpoint_limit($result, $request, $route, $handler) {
    if (!defined('REST_REQUEST') || !REST_REQUEST
            || $request !== ($GLOBALS['tit_public_http_request'] ?? null)
            || $result !== null || strpos($route, '/talent/v1/') !== 0
            || ($handler['permission_callback'] ?? null) !== '__return_true') return $result;
    return tit_public_limit_check();
}
add_filter('rest_dispatch_request', 'tit_public_endpoint_limit', 10, 4);

function tit_public_limit_form() {
    $response = tit_public_limit_check();
    if ($response === null) return;
    foreach ($response->get_headers() as $name => $value) header($name . ': ' . $value);
    $message = 'Please wait and try again. <p><a style="display:inline-block;padding:12px;" href="' .
        'https://asktherecruiter.com/resume-score?utm_source=talent_tracker&amp;utm_medium=referral' .
        '&amp;utm_campaign=public_rate_limit#start-intake">Check my match, free</a></p>';
    wp_die($message, 'Request limit', array('response' => $response->get_status()));
}
foreach (array('tit_export_csv', 'tit_export_json', 'tit_export_hubspot', 'tit_export_salesforce') as $action) {
    add_action('admin_post_' . $action, 'tit_public_limit_form', 0);
    add_action('admin_post_nopriv_' . $action, 'tit_public_limit_form', 0);
}

/** Keep expired per-identity rows bounded, without resetting active quotas on cache flush. */
function tit_public_limit_cleanup() {
    global $wpdb;
    $wpdb->query($wpdb->prepare("DELETE FROM {$wpdb->options} WHERE option_name LIKE %s " .
        'AND CAST(option_value AS UNSIGNED) <= %d', $wpdb->esc_like('rl_tit_public_') . '%', time()));
}
function tit_public_limit_schedule() {
    if (!wp_next_scheduled('tit_public_limit_cleanup')) {
        wp_schedule_event(time() + 3600, 'hourly', 'tit_public_limit_cleanup');
    }
}
add_action('init', 'tit_public_limit_schedule');
add_action('tit_public_limit_cleanup', 'tit_public_limit_cleanup');

function tit_public_limit_deactivate() {
    global $wpdb;
    wp_clear_scheduled_hook('tit_public_limit_cleanup');
    $wpdb->query($wpdb->prepare("DELETE FROM {$wpdb->options} WHERE option_name LIKE %s",
        $wpdb->esc_like('rl_tit_public_') . '%'));
}
if (function_exists('register_deactivation_hook')) {
    register_deactivation_hook(dirname(__DIR__) . '/talent-intelligence-tracker.php', 'tit_public_limit_deactivate');
}
