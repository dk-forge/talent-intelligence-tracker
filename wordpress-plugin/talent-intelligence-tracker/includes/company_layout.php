<?php
/**
 * Company profile layout pieces: the header card, its fact chips, and the
 * method and next-step sections at the foot of the page.
 *
 * PURE apart from the WordPress escaping and URL helpers. No query runs here;
 * everything is handed in from tit_company_render(), which keeps the figures
 * and the markup in separate files and lets tests/php/render_company_layout.php
 * render the pieces without a database.
 *
 * Every chip is a fact the rows already carry. A chip we cannot fill is left
 * out rather than printed empty, for the same reason the stats strip omits a
 * tile: an empty slot reads as a missing fact, not an unstated one.
 */

if (!defined('ABSPATH')) exit;

/** One or two letters for the avatar. Decorative: the h1 carries the name. */
function tit_company_initials($name) {
    $words = preg_split('/[^\p{L}\p{N}]+/u', (string) $name, -1, PREG_SPLIT_NO_EMPTY);
    $skip = array('inc', 'ltd', 'llc', 'plc', 'gmbh', 'ag', 'sa', 'the', 'co', 'corp');
    $words = array_values(array_filter($words, function ($w) use ($skip) {
        return !in_array(strtolower($w), $skip, true);
    })) ?: $words;
    $out = '';
    foreach (array_slice($words, 0, 2) as $w) {
        $out .= function_exists('mb_substr') ? mb_substr($w, 0, 1) : substr($w, 0, 1);
    }
    return function_exists('mb_strtoupper') ? mb_strtoupper($out) : strtoupper($out);
}

/**
 * Industry, headquarters and where the latest signal was, read from the rows.
 * Returns a list of [label, value] pairs, possibly empty.
 */
function tit_company_chips($rows, $profile) {
    $industry = '';
    $hq = '';
    foreach ($rows as $r) {
        if ($industry === '' && !empty($r['industry'])) {
            $industry = ucfirst(str_replace('_', ' ', $r['industry']));
        }
        if ($hq === '' && (!empty($r['hq_city']) || !empty($r['hq_country']))) {
            $hq = trim((!empty($r['hq_city']) ? $r['hq_city'] . ', ' : '')
                       . tit_country_name($r['hq_country'] ?? ''), ', ');
        }
    }
    $chips = array();
    if ($industry !== '') $chips[] = array('Industry', $industry);
    if ($hq !== '') $chips[] = array('Headquarters', $hq);
    $place = (string) ($profile['place'] ?? '');
    if ($place !== '' && $place !== $hq) $chips[] = array('Latest signal', $place);
    return $chips;
}

/** The header card: avatar, name, status line, chips, freshness. */
function tit_company_header_html($name, $status, $chips, $freshness_html = '') {
    $h  = '<header class="tit-co-head">';
    $h .= '<div class="tit-co-avatar" aria-hidden="true">' . esc_html(tit_company_initials($name)) . '</div>';
    $h .= '<div class="tit-co-id">';
    $h .= '<p class="tit-co-kicker">Employer profile</p>';
    $h .= '<h1>' . esc_html($name) . '</h1>';
    if ($status !== '') $h .= '<p class="tit-status">' . esc_html($status) . '</p>';
    if ($chips) {
        $h .= '<ul class="tit-co-chips" aria-label="Key facts">';
        foreach ($chips as $c) {
            $h .= '<li class="tit-co-chip"><span class="tit-co-chip-k">' . esc_html($c[0])
                . '</span> ' . esc_html($c[1]) . '</li>';
        }
        $h .= '</ul>';
    }
    $h .= $freshness_html;  // built and escaped in includes/freshness.php
    $h .= '</div></header>';
    return $h;
}

/** Source and method note, then the next step into AskTheRecruiter's tools. */
function tit_company_method_html($name, $tracker_url, $sources_url, $tools_url) {
    $h  = '<section class="tit-co-section tit-co-method" aria-labelledby="tit-co-method-h">';
    $h .= '<h2 id="tit-co-method-h" class="tit-co-h2">Sources and method</h2>';
    $h .= '<p class="tit-cite">The read-through on each line is our interpretation. The headline and'
        . ' figures come from the linked source. We only publish what we have read on a primary'
        . ' source and can link to, and we never fill a profile from an estimate. Data licensed CC BY 4.0. '
        . '<a href="' . esc_url($sources_url) . '">How we collect</a> · '
        . '<a href="' . esc_url($tracker_url) . '">Back to the tracker</a></p>';
    $h .= '</section>';
    $h .= '<section class="tit-co-cta" aria-labelledby="tit-co-cta-h">';
    $h .= '<h2 id="tit-co-cta-h" class="tit-co-h2">Applying to ' . esc_html($name) . '?</h2>';
    $h .= '<p>Adapt your resume to the role in a few minutes with AskTheRecruiter, then follow this'
        . ' employer for new signals.</p>';
    $h .= '<a class="tit-co-btn" href="' . esc_url($tools_url) . '">Adapt my resume</a>';
    $h .= '</section>';
    return $h;
}
