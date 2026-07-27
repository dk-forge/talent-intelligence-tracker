<?php
/**
 * Make sure the dashboard page carries the shortcode.
 *
 * This runs retry-until-verified rather than once on a version bump. FTP
 * deploys race mid-upload, so a one-shot hook fires while files are still
 * landing and then never runs again. Checking cheaply on every request until
 * it is verified is the only reliable pattern on this host.
 *
 * It only ever APPENDS, and only when the shortcode is absent. It never
 * rewrites or removes anything the owner wrote.
 */

if (!defined('ABSPATH')) exit;

const TIT_PAGE_SLUG = 'talent-intelligence-tracker';
const TIT_SHORTCODE = '[talent_intelligence_dashboard]';

function tit_ensure_dashboard_page() {
    // Verified once, never checked again.
    if (get_option('tit_page_ready') === TIT_VERSION) {
        return;
    }

    // Don't hammer the database on every request while unresolved.
    $last = (int) get_option('tit_page_last_try', 0);
    if ($last && (time() - $last) < 300) {
        return;
    }
    update_option('tit_page_last_try', time(), false);

    $page = get_page_by_path(TIT_PAGE_SLUG, OBJECT, 'page');
    if (!$page) {
        // The owner may not have created it yet. Try again later rather than
        // creating a competing page at a different slug.
        return;
    }

    if (has_shortcode($page->post_content, 'talent_intelligence_dashboard')) {
        update_option('tit_page_ready', TIT_VERSION, false);
        return;
    }

    $content = rtrim((string) $page->post_content);
    $content = $content === '' ? TIT_SHORTCODE : $content . "\n\n" . TIT_SHORTCODE;

    $result = wp_update_post(array(
        'ID'           => $page->ID,
        'post_content' => $content,
    ), true);

    if (!is_wp_error($result)) {
        update_option('tit_page_ready', TIT_VERSION, false);
    }
}
add_action('init', 'tit_ensure_dashboard_page', 20);

/**
 * Keep the site's Table of Contents plugin out of the dashboard.
 *
 * Easy Table of Contents inserts itself into any post carrying headings, and
 * on this page it lands inside the hero: a list of links to two chart titles,
 * above the numbers people came for. It is right for an article and wrong for
 * a dashboard.
 *
 * This only ever returns false on our own pages, so ordinary blog posts keep
 * their table of contents.
 */
function tit_is_our_page() {
    return get_query_var('tit_company')
        || get_query_var('tit_sources')
        || is_page(TIT_PAGE_SLUG);
}

add_filter('ez_toc_maybe_apply_the_content_filter', function ($apply) {
    return tit_is_our_page() ? false : $apply;
}, 99);
