<?php
/**
 * LINKS TO THE SIBLING LAYOFF TRACKER (todo#1881 item 5): the same company,
 * country or sector there. NAVIGATION ONLY, NO PAIRED FIGURES: this file's
 * neighbour cross_tracker.php measured that pairing the two datasets by name
 * publishes wrong claims, so this prints a link and the target's own name.
 * ONLY TO PAGES THAT EXIST: data/cross-links.json is the intersection of the
 * two live sitemaps, ambiguous slugs left out.
 */
if (!defined('ABSPATH')) exit;

function tit_cross_links_data() {
    static $data = null;
    if ($data !== null) return $data;
    $data = array('companies' => array(), 'countries' => array(), 'industries' => array());
    $path = TIT_PATH . 'data/cross-links.json';
    if (is_readable($path)) {
        $raw = json_decode((string) file_get_contents($path), true);
        if (is_array($raw)) $data = array_merge($data, $raw);
    }
    $data['companies'] = array_flip((array) $data['companies']);
    $data['countries'] = array_flip((array) $data['countries']);
    return $data;
}

/** [url, label] pairs on the layoff tracker for one TIT company slug or place. */
function tit_cross_links_targets($kind, $slug, $display) {
    $d = tit_cross_links_data();
    $slug = trim(preg_replace('/[^a-z0-9]+/', '-', strtolower((string) $slug)), '-');
    if ($kind === 'company' && isset($d['companies'][$slug])) {
        return array(array(home_url('/company-layoffs/' . $slug . '/'), (string) $display));
    }
    if ($kind === 'country' && isset($d['countries'][$slug])) {
        return array(array(home_url('/country-layoffs/' . $slug . '/'), (string) $display));
    }
    if ($kind === 'industry' && !empty($d['industries'][$slug])) {
        $out = array();
        foreach ((array) $d['industries'][$slug] as $pair) {
            $out[] = array(home_url('/industry-layoffs/' . $pair[0] . '/'), (string) $pair[1]);
        }
        return $out;
    }
    return array();
}

function tit_cross_links_html($kind, $slug, $display) {
    $targets = tit_cross_links_targets($kind, $slug, $display);
    if (!$targets) return '';
    $links = array();
    foreach ($targets as $t) {
        $links[] = '<a href="' . esc_url($t[0]) . '">' . esc_html($t[1]) . ' in the layoff tracker</a>';
    }
    return '<p class="tit-cross-links"><b>Who is cutting?</b> ' . implode(' · ', $links) . '</p>';
}
