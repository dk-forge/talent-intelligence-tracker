<?php
/**
 * "Related" link row on every page this plugin publishes (1.104.0,
 * ai-layoff-tracker#446 item 4, SEO route B; owner ruling 2026-10-14).
 *
 * ONE map, here. The plugin publishes one WordPress page (the dashboard,
 * via the [talent_intelligence_dashboard] shortcode) and the rest as virtual
 * routes that all close through tit_render_footer($key). Every page links
 * Data Sources except the Sources page itself. Link text only: intro and FAQ
 * copy are the owner's to write.
 */
if (!defined('ABSPATH')) exit;

/** Target key => array(label, path under the blog home). */
function tit_related_links_targets() {
    return array(
        'dashboard'   => array('Tracker', '/talent-intelligence-tracker/'),
        'places'      => array('Countries, Cities And Industries', '/talent-intelligence-tracker/places/'),
        'roles'       => array("Who's Hiring", '/talent-intelligence-tracker/roles/'),
        'recall'      => array('Measured Recall', '/talent-intelligence-tracker/recall/'),
        'corrections' => array('Corrections', '/talent-intelligence-tracker/corrections/'),
        'press'       => array('Press', '/talent-intelligence-tracker/press/'),
        'sources'     => array('Data Sources', '/talent-intelligence-tracker/sources/'),
    );
}

/** Page key => ordered target keys. Data Sources is appended where missing. */
function tit_related_links_map() {
    return array(
        'dashboard'   => array('places', 'roles', 'recall'),
        'sources'     => array('dashboard', 'recall', 'corrections'),
        'press'       => array('dashboard', 'places', 'corrections'),
        'recall'      => array('dashboard', 'corrections'),
        'corrections' => array('dashboard', 'recall'),
        'places'      => array('dashboard', 'roles'),
        'place'       => array('places', 'dashboard'),
        'roles'       => array('dashboard', 'places'),
        'role'        => array('roles', 'dashboard'),
        'company'     => array('dashboard', 'places'),
    );
}

/** Pure: the row for a page key, '' when unmapped. */
function tit_related_links_html($page) {
    $map = tit_related_links_map();
    if (!isset($map[$page])) return '';
    $keys = $map[$page];
    if ($page !== 'sources' && !in_array('sources', $keys, true)) $keys[] = 'sources';
    $targets = tit_related_links_targets();
    $items = '';
    foreach (array_unique($keys) as $k) {
        if (!isset($targets[$k])) continue;
        $items .= '<li><a href="' . esc_url(home_url($targets[$k][1])) . '">' . esc_html($targets[$k][0]) . '</a></li>';
    }
    if ($items === '') return '';
    return '<nav class="tit-related" aria-label="Related pages"><h2 class="tit-related-h">Related</h2>'
         . '<ul class="tit-related-list">' . $items . '</ul></nav>';
}
