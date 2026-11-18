<?php
/**
 * List the tracker's two sitemaps in the site's sitemap index.
 *
 * /blog/robots.txt names exactly one sitemap, /blog/sitemap_index.xml, which
 * the site's SEO plugin builds. Measured 2026-10-12: that index carried the
 * sibling tracker's sitemaps and NEITHER of ours, so the ~1,371 company URLs
 * and ~103 place URLs were findable by a crawler only through links.
 *
 * THIS FILE NAMES SEO PLUGINS ON PURPOSE, and is the only file that does.
 * company.php refuses to (test_the_robots_fix_names_no_seo_plugin) because its
 * robots fix must hold whatever is installed. An index entry has no
 * plugin-agnostic form: the index is the plugin's file. So the hooks of the two
 * plugins that build a sitemap_index.xml are both registered, each only
 * appends, and a site running neither fires neither and loses nothing.
 */

if (!defined('ABSPATH')) exit;

function tit_sitemap_index_entries($xml) {
    $urls = array();
    if (function_exists('tit_company_sitemap_url')) $urls[] = tit_company_sitemap_url();
    if (function_exists('tit_places_sitemap_url'))  $urls[] = tit_places_sitemap_url();
    if (function_exists('tit_roles_sitemap_url'))   $urls[] = tit_roles_sitemap_url();
    $add = '';
    foreach ($urls as $url) {
        // Never twice, whichever plugin or filter order put it there first.
        if (strpos((string) $xml, $url) !== false) continue;
        $add .= '<sitemap><loc>' . esc_url($url) . '</loc></sitemap>' . "\n";
    }
    return $xml . $add;
}
add_filter('wpseo_sitemap_index', 'tit_sitemap_index_entries');
add_filter('rank_math/sitemap/index', 'tit_sitemap_index_entries');

/**
 * PURE. The hub page's sitemap <lastmod> follows the data, not the post.
 *
 * Measured 2026-11-18: page-sitemap.xml gave the hub lastmod 2026-07-29 (the
 * WordPress page's last edit) though rows land daily, and Search Console
 * showed the hub "Crawled - currently not indexed", last crawled 2026-09-06.
 * A months-old lastmod tells a crawler there is nothing new to fetch. The hub
 * now carries the newest collection run, clamped to today (never a future
 * date). Any other entry, or no usable date, is returned unchanged.
 */
function tit_hub_sitemap_entry($url, $hub_loc, $newest, $today = null) {
    if (!is_array($url) || empty($url['loc'])) return $url;
    if (rtrim((string) $url['loc'], '/') !== rtrim((string) $hub_loc, '/')) return $url;
    $d = substr((string) $newest, 0, 10);
    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $d)) return $url;
    $today = $today ?: gmdate('Y-m-d');
    if ($d > $today) $d = $today;
    $old = substr((string) ($url['mod'] ?? ''), 0, 10);
    if ($old !== '' && $old >= $d) return $url;
    $url['mod'] = $d . 'T00:00:00+00:00';
    return $url;
}

function tit_hub_sitemap_entry_filter($url, $type = '', $object = null) {
    if (!defined('TIT_PAGE_SLUG') || !function_exists('tit_dashboard_facts') || !function_exists('tit_table_name')) return $url;
    if (!is_array($url) || empty($url['loc']) || strpos((string) $url['loc'], '/' . TIT_PAGE_SLUG) === false) return $url;
    $facts = tit_dashboard_facts(tit_table_name());
    return tit_hub_sitemap_entry($url, home_url('/' . TIT_PAGE_SLUG . '/'), $facts['newest_run'] ?? '');
}
add_filter('rank_math/sitemap/entry', 'tit_hub_sitemap_entry_filter', 10, 3);
add_filter('wpseo_sitemap_entry', 'tit_hub_sitemap_entry_filter', 10, 3);
