<?php
/**
 * Breadcrumb trails (owner request 2026-10-12).
 *
 * The place, directory and company pages already printed a visible trail
 * but no BreadcrumbList, so Google only saw Rank Math's one-item "Home". ONE
 * trail per page now drives both the visible <nav aria-label="Breadcrumb">
 * and the JSON-LD, so the two cannot disagree. A trail is a list of
 * array('name' => .., 'url' => ..); the last item is the current page and
 * renders as plain text with aria-current="page".
 */
if (!defined('ABSPATH')) exit;

function tit_breadcrumbs_hub() {
    return array('name' => 'Talent Intelligence Tracker', 'url' => home_url('/talent-intelligence-tracker/'));
}

/** Talent Intelligence Tracker > Cities > London. */
function tit_breadcrumbs_place_trail($many, $places_url, $name, $url) {
    return array(
        tit_breadcrumbs_hub(),
        array('name' => (string) $many, 'url' => (string) $places_url),
        array('name' => (string) $name, 'url' => (string) $url),
    );
}

/** Talent Intelligence Tracker > Countries, Cities And Industries. */
function tit_breadcrumbs_places_trail($places_url) {
    return array(tit_breadcrumbs_hub(),
        array('name' => 'Countries, Cities And Industries', 'url' => (string) $places_url));
}

/** Talent Intelligence Tracker > Acme. There is no companies index to link. */
function tit_breadcrumbs_company_trail($name, $url) {
    return array(tit_breadcrumbs_hub(), array('name' => (string) $name, 'url' => (string) $url));
}

/** Talent Intelligence Tracker > Press (press, recall, sources, corrections). */
function tit_breadcrumbs_section_trail($name, $url) {
    return array(tit_breadcrumbs_hub(), array('name' => (string) $name, 'url' => (string) $url));
}

/** Visible trail. Pure. Keeps the existing .tit-crumb look; the list wraps. */
function tit_breadcrumbs_html($trail) {
    $trail = array_values((array) $trail);
    $n = count($trail);
    if ($n < 2) return '';
    $out = '<nav class="tit-crumb" aria-label="Breadcrumb"><ol class="tit-crumb-list">';
    foreach ($trail as $i => $c) {
        $out .= '<li>';
        if ($i > 0) $out .= '<span aria-hidden="true">&rsaquo;</span>';
        $out .= ($i === $n - 1)
            ? '<span aria-current="page">' . esc_html($c['name']) . '</span>'
            : '<a href="' . esc_url($c['url']) . '">' . esc_html($c['name']) . '</a>';
        $out .= '</li>';
    }
    return $out . '</ol></nav>';
}

/** BreadcrumbList JSON-LD for the SAME trail. Pure. */
function tit_breadcrumbs_jsonld($trail) {
    $items = array();
    foreach (array_values((array) $trail) as $i => $c) {
        $items[] = array('@type' => 'ListItem', 'position' => $i + 1, 'name' => $c['name'], 'item' => $c['url']);
    }
    return '<script type="application/ld+json">' . wp_json_encode(array(
        '@context' => 'https://schema.org',
        '@type'    => 'BreadcrumbList',
        'itemListElement' => $items,
    ), JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_HEX_TAG) . "</script>\n";
}

/** The trail for the current request, or null. Used by the head hook. */
function tit_breadcrumbs_current() {
    $sections = array(
        'tit_press'       => array('Press', 'tit_press_url'),
        'tit_recall'      => array('Measured recall', 'tit_recall_url'),
        'tit_sources'     => array('Sources', 'tit_sources_url'),
        'tit_corrections' => array('Corrections', 'tit_corrections_url'),
    );
    foreach ($sections as $qv => $sec) {
        if (get_query_var($qv) && function_exists($sec[1])) {
            return tit_breadcrumbs_section_trail($sec[0], call_user_func($sec[1]));
        }
    }
    if (get_query_var('tit_places') && function_exists('tit_places_url')) {
        return tit_breadcrumbs_places_trail(tit_places_url());
    }
    if (function_exists('tit_place_current') && ($cur = tit_place_current()) && !empty($cur['cell']['gated'])) {
        $kinds = tit_place_kinds();
        return tit_breadcrumbs_place_trail($kinds[$cur['kind']]['many'], tit_places_url(),
            $cur['cell']['name'], tit_place_url($cur['kind'], $cur['cell']['key']));
    }
    if (function_exists('tit_company_current') && ($cur = tit_company_current())) {
        return tit_breadcrumbs_company_trail($cur['profile']['name'],
            tit_company_url($cur['rows'][0]['company_key']));
    }
    return null;
}

function tit_breadcrumbs_head() {
    $trail = tit_breadcrumbs_current();
    if ($trail) echo tit_breadcrumbs_jsonld($trail);
}
add_action('wp_head', 'tit_breadcrumbs_head', 21);

/**
 * Rank Math puts a one-item "Home" BreadcrumbList in its @graph on every
 * virtual route, so our pages carried two lists that disagreed. Where we print
 * our own trail, drop Rank Math's and the WebPage pointer to it. Pure.
 */
function tit_breadcrumbs_strip_rank_math($data, $ours) {
    if (!$ours || !is_array($data)) return $data;
    foreach ($data as $k => $entity) {
        if (!is_array($entity)) continue;
        if (($entity['@type'] ?? '') === 'BreadcrumbList') { unset($data[$k]); continue; }
        if (isset($entity['breadcrumb'])) unset($data[$k]['breadcrumb']);
    }
    return $data;
}

function tit_breadcrumbs_rank_math_filter($data) {
    return tit_breadcrumbs_strip_rank_math($data, (bool) tit_breadcrumbs_current());
}
add_filter('rank_math/json_ld', 'tit_breadcrumbs_rank_math_filter', 99);
