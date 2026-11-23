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

function tit_public_limit_check() {
    $peer = (string) ($_SERVER['REMOTE_ADDR'] ?? '');
    $peer = filter_var($peer, FILTER_VALIDATE_IP) ? inet_ntop(inet_pton($peer)) : 'unknown';
    $budgets = array(array('ip:' . $peer, 120));
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
