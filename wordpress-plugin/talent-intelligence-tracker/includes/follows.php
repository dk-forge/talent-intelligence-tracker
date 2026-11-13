<?php
/**
 * Follow an employer or a city; new updates ride in the digest (owner ruling
 * 2026-10-15, gap 4 of the 2026-10-12 scored review: per-city and per-company
 * alerts).
 *
 * THERE IS NO EMAIL SYSTEM IN THIS PLUGIN, AND THERE MUST NOT BE A SECOND ONE.
 * The digest, its double opt-in, its unsubscribe, its retention purge and its
 * follow table all live in the sibling plugin (ai-layoff-tracker,
 * includes/follows.php), installed in the same WordPress. A second consent
 * flow here would be a second privacy promise to keep. So this file supplies
 * only what the sibling cannot know: which talent targets exist, what their
 * label and URL are, and which rows are new for them. The sibling stores the
 * follow, sends the confirmation, and composes the mail.
 *
 * IT IS OFF, AND TWO THINGS MUST BOTH BE TRUE TO TURN IT ON:
 *   1. the sibling declares ALT_FOLLOW_EXTERNAL_KINDS, its promise that
 *      alt_follow_submit(), alt_follow_resolve() and alt_follows_matches()
 *      consult the three filters below for kinds it does not own. Today's
 *      sibling hard-codes 'company' and 'state' and would reject the POST, or
 *      worse, read a talent city as a US state; and
 *   2. the option `tit_follows_enabled` is 1 (an owner decision, because it
 *      sends mail).
 * Until both hold, no form renders and no filter is registered, so nothing a
 * reader can click leads to a dead end.
 *
 * Guard: tests/test_follows.py.
 */
if (!defined('ABSPATH')) exit;

const TIT_FOLLOW_KINDS = array('talent_company', 'talent_city');

/** PURE. Both gates. */
function tit_follows_gate($sibling_contract, $option) {
    return (bool) $sibling_contract && (string) $option === '1';
}

function tit_follows_enabled() {
    return tit_follows_gate(defined('ALT_FOLLOW_EXTERNAL_KINDS') && function_exists('alt_digest_signup'),
                            get_option('tit_follows_enabled', '0'));
}

/** PURE. The form. Posts to the sibling's own handler; the label is never trusted from the POST. */
function tit_follow_form_html($kind, $value, $label, $action_url, $sent = false) {
    if (!in_array($kind, TIT_FOLLOW_KINDS, true) || (string) $value === '') return '';
    $out = '<section class="tit-follow alt-follow" aria-labelledby="tit-follow-h"><h2 id="tit-follow-h">Follow '
         . esc_html($label) . '</h2>';
    if ($sent) {
        return $out . '<p role="status">Check your inbox. The follow starts when you click the confirmation link.</p></section>';
    }
    return $out . '<p>Get new hiring, funding and leadership updates for ' . esc_html($label)
        . ' in the digest. We email you a confirmation link first; unsubscribe any time.</p>'
        . '<form method="post" action="' . esc_url($action_url) . '">'
        . '<input type="hidden" name="action" value="alt_follow">'
        . '<input type="hidden" name="alt_follow_kind" value="' . esc_attr($kind) . '">'
        . '<input type="hidden" name="alt_follow_value" value="' . esc_attr($value) . '">'
        . '<input type="text" name="alt_hp" class="alt-hp" tabindex="-1" autocomplete="off" aria-hidden="true">'
        . '<label>Email <input type="email" name="alt_follow_email" required autocomplete="email"></label> '
        . '<button type="submit" class="tit-btn tit-btn-solid">Follow</button></form></section>';
}

/**
 * PURE. Talent rows into the sibling's section row shape
 * {follow, company, jobs, date, place, url}. jobs is 0: a talent update is not
 * a job count, and the sibling prints the figure only when it is positive.
 */
function tit_follow_section_rows(array $rows, $label, $url, $limit = 10) {
    $out = array();
    foreach (array_slice($rows, 0, $limit) as $r) {
        $out[] = array('follow' => (string) $label, 'company' => (string) ($r['company'] ?? ''),
                       'jobs' => 0, 'date' => (string) ($r['d'] ?? ''),
                       'place' => (string) ($r['city'] ?? ''), 'url' => (string) $url);
    }
    return $out;
}

/** Resolve a target to [label, url] or null. Only published pages can be followed. */
function tit_follow_resolve($found, $kind, $value) {
    if ($found !== null || !in_array($kind, TIT_FOLLOW_KINDS, true)) return $found;
    if ($kind === 'talent_company' && function_exists('tit_company_sitemap_entries')) {
        foreach (tit_company_sitemap_entries() as $e) {
            if ($e['company_key'] === $value) {
                $name = function_exists('tit_company_display_name') ? tit_company_display_name($value) : $value;
                return array($name, tit_company_url($value));
            }
        }
        return null;
    }
    if ($kind === 'talent_city' && function_exists('tit_place_published')) {
        $cells = tit_place_published('city');
        if (isset($cells[$value])) return array((string) $cells[$value]['name'], tit_place_url('city', $value));
    }
    return null;
}

/** New rows for one follow in the digest window, biggest news first. */
function tit_follow_rows($rows, $follow, $from, $to) {
    if (!in_array($follow['kind'] ?? '', TIT_FOLLOW_KINDS, true)) return $rows;
    global $wpdb;
    $t = tit_table_name();
    $where = $follow['kind'] === 'talent_company' ? 'company_key = %s' : 'COALESCE(city, hq_city) = %s';
    $found = $wpdb->get_results($wpdb->prepare(
        "SELECT company, COALESCE(published_date, DATE(captured_at)) d, COALESCE(city, hq_city) city
           FROM {$t}
          WHERE is_current = 1 AND {$where}
            AND captured_at BETWEEN %s AND %s
          ORDER BY (materiality = 'routine') ASC, captured_at DESC LIMIT 10",
        $follow['value'], $from . ' 00:00:00', $to . ' 23:59:59'), ARRAY_A) ?: array();
    return array_merge((array) $rows, tit_follow_section_rows($found, $follow['label'], $follow['url']));
}

function tit_follows_register() {
    if (!tit_follows_enabled()) return;
    add_filter('alt_follow_kinds', function ($kinds) { return array_merge((array) $kinds, TIT_FOLLOW_KINDS); });
    add_filter('alt_follow_resolve', 'tit_follow_resolve', 10, 3);
    add_filter('alt_follows_rows', 'tit_follow_rows', 10, 4);
}
add_action('init', 'tit_follows_register', 20);

/** Runtime: the form for a company or city page, '' while switched off. */
function tit_follow_form($kind, $value, $label) {
    if (!tit_follows_enabled()) return '';
    $sent = isset($_GET['alt_follow']) && $_GET['alt_follow'] === 'sent';
    return tit_follow_form_html($kind, $value, $label, admin_url('admin-post.php'), $sent);
}
