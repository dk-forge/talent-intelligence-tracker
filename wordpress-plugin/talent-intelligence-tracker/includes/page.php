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
        || get_query_var('tit_corrections')
        // /recall/ and the place pages were missing here, so both were still
        // getting a table of contents injected into their heading structure.
        // Same argument as the dashboard: right for an article, wrong for a
        // page whose headings are chart titles.
        || get_query_var('tit_recall')
        // The press page is headings and tables, so Easy Table of Contents
        // would inject a list of links to them above the figures a journalist
        // came for. Same argument as every other route this plugin owns.
        || get_query_var('tit_press')
        || get_query_var('tit_places')
        || get_query_var('tit_place')
        || is_page(TIT_PAGE_SLUG);
}

add_filter('ez_toc_maybe_apply_the_content_filter', function ($apply) {
    return tit_is_our_page() ? false : $apply;
}, 99);

/*
 * ---------------------------------------------------------------------------
 * ONE DESCRIPTION MECHANISM, FOR EVERY ROUTE THIS PLUGIN OWNS
 * ---------------------------------------------------------------------------
 *
 * The company and place pages have printed a meta description since they
 * shipped. The four pages a reader is most likely to land on had none at all:
 * the DASHBOARD, which is the flagship, and /sources/, /recall/ and
 * /corrections/, which are the three pages that exist to be checked. A search
 * result for those was whatever the theme or the SEO plugin decided to lift out
 * of the markup, which on the dashboard is a filter panel.
 *
 * og:description was missing everywhere, on all six, so a link shared in Slack
 * or LinkedIn had no summary card text on any page of this product.
 *
 * Hence one helper rather than six copies. It prints the description twice --
 * once as `meta[name=description]` for a crawler, once as `og:description` for a
 * share card -- from a single string, so the two can never disagree. The
 * canonical tag is deliberately NOT printed here: the company and place pages
 * build theirs from a resolved key rather than from the requested URL, that
 * resolution is the whole subject of tit_company_moved_slugs(), and moving it
 * into a shared helper would put a redirect decision behind a description
 * argument.
 *
 * No superlatives, and the truncation is at a sentence rather than mid-figure:
 * a description ending "...raised $1.2" is worse than a shorter one.
 */
const TIT_DESCRIPTION_MAX = 300;

function tit_head_description($description) {
    $text = trim(preg_replace('/\s+/', ' ', (string) $description));
    if ($text === '') return;

    if (strlen($text) > TIT_DESCRIPTION_MAX) {
        $cut = substr($text, 0, TIT_DESCRIPTION_MAX - 3);
        // Prefer the last sentence end, then the last space. Cutting inside a
        // number is the one outcome worth code to avoid.
        $stop = max(strrpos($cut, '. '), strrpos($cut, ' '));
        if ($stop !== false && $stop > 80) $cut = substr($cut, 0, $stop);
        $text = rtrim($cut, " .,;:") . '...';
    }

    echo "\n" . '<meta name="description" content="' . esc_attr($text) . '" />' . "\n";
    echo '<meta property="og:description" content="' . esc_attr($text) . '" />' . "\n";
}

/**
 * The dashboard's own description, computed from what the page is showing.
 *
 * Read out of the same cached bundle the page renders from, so it costs no
 * query: tit_dashboard_facts() has already run for this request by the time
 * wp_head fires on a page whose content contains the shortcode -- and when it
 * has not, get_transient returns the warm bundle. A figure typed here would be
 * stale within a day, which is the whole reason the page computes its own.
 */
function tit_dashboard_head() {
    if (!is_page(TIT_PAGE_SLUG)) return;
    if (!function_exists('tit_dashboard_facts') || !function_exists('tit_table_name')) return;

    $facts = tit_dashboard_facts(tit_table_name());
    $bits = array(sprintf(
        'Hiring, funding and leadership updates from %s %s across %s %s and %s %s',
        number_format_i18n((int) $facts['notable']),
        ((int) $facts['notable']) === 1 ? 'tracked update' : 'tracked updates',
        number_format_i18n((int) $facts['companies']),
        ((int) $facts['companies']) === 1 ? 'employer' : 'employers',
        number_format_i18n((int) $facts['countries']),
        ((int) $facts['countries']) === 1 ? 'country' : 'countries'
    ));
    $bits[] = 'Every update links to the filing or report that makes the claim';
    tit_head_description(implode('. ', $bits) . '.');
}
add_action('wp_head', 'tit_dashboard_head', 1);
