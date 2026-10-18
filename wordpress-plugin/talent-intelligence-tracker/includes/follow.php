<?php
/**
 * EMAIL FOLLOWS FOR A CITY OR AN EMPLOYER (city and company alerts, step 2).
 *
 * Owner ruling 2026-10-18: the SIBLING plugin's subscriber list may hold talent
 * follow preferences; weekly by default, daily optional. So the subscriber, the
 * consent, the follow row, the stop link and the send all live in the sibling
 * (ai-layoff-tracker includes/follows.php, kinds `talent_city` and
 * `talent_company`). Nothing about a follower is ever stored or seen here.
 *
 * This file answers two questions the sibling asks in-process, both about
 * public data only:
 *   tit_follow_resolve($kind, $key)          what is this, and where does it live
 *   tit_follow_rows($kind, $key, $from, $to) what is new for it in the window
 * and prints the sibling's own follow form on city and company pages when the
 * sibling is installed (function_exists), and nothing when it is not.
 *
 * Rows carry the headline and the source document, never a figure: a headline
 * is the publisher's own sentence, and a number would need count_meaning's
 * rules (TECHLOG 2026-10-12) to be phrased right.
 */
if (!defined('ABSPATH')) exit;

if (!defined('TIT_FOLLOW_MAX_ROWS')) define('TIT_FOLLOW_MAX_ROWS', 10);

/** [label, url] for a followable key, or null. Kinds: 'city', 'company'. */
function tit_follow_resolve($kind, $key) {
    $key = trim((string) $key);
    if ($key === '' || strlen($key) > 191) return null;
    if ($kind === 'city') {
        if (!function_exists('tit_place_index')) return null;
        foreach (tit_place_index('city')['cells'] as $cell) {
            if ($cell['key'] === $key && $cell['gated']) {
                return array((string) $cell['name'], tit_place_url('city', $cell['key']));
            }
        }
        return null;
    }
    if ($kind === 'company') {
        if (!function_exists('tit_company_url')) return null;
        global $wpdb;
        $name = $wpdb->get_var($wpdb->prepare(
            'SELECT company FROM ' . tit_table_name() . ' WHERE is_current = 1 AND company_key = %s LIMIT 1', $key));
        return ($name !== null && $name !== '') ? array((string) $name, tit_company_url($key)) : null;
    }
    return null;
}

/**
 * Newest current rows for one followed key dated inside [$from, $to]
 * (YYYY-MM-DD, inclusive), newest first, at most TIT_FOLLOW_MAX_ROWS.
 * Each: company, headline, source_url, source_name, date.
 */
function tit_follow_rows($kind, $key, $from, $to) {
    global $wpdb;
    if ($kind === 'city') {
        $match = '(city = %s OR (city IS NULL AND hq_city = %s))';
        $args = array((string) $key, (string) $key);
    } elseif ($kind === 'company') {
        $match = 'company_key = %s';
        $args = array((string) $key);
    } else {
        return array();
    }
    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', (string) $from) || !preg_match('/^\d{4}-\d{2}-\d{2}$/', (string) $to)) return array();
    $rows = $wpdb->get_results($wpdb->prepare(
        'SELECT company, headline, source_url, source_name, COALESCE(published_date, DATE(captured_at)) AS d
           FROM ' . tit_table_name() . "
          WHERE is_current = 1 AND {$match}
            AND COALESCE(published_date, DATE(captured_at)) BETWEEN %s AND %s
          ORDER BY d DESC, row_id DESC LIMIT %d",
        array_merge($args, array($from, $to, TIT_FOLLOW_MAX_ROWS))), ARRAY_A) ?: array();
    $out = array();
    foreach ($rows as $r) {
        if (empty($r['source_url'])) continue;   // no source URL, no record
        $out[] = array('company' => (string) $r['company'], 'headline' => (string) $r['headline'],
                       'source_url' => (string) $r['source_url'], 'source_name' => (string) $r['source_name'],
                       'date' => (string) $r['d']);
    }
    return $out;
}

/** The sibling's follow form, or '' when the sibling is not installed. */
function tit_follow_form($kind, $key, $name) {
    if (!function_exists('alt_follow_form_html')) return '';
    return alt_follow_form_html($kind === 'city' ? 'talent_city' : 'talent_company', $key, $name);
}
