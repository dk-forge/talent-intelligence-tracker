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
