<?php
/**
 * The visible "Last updated <date> · <N> sources" line on company and place
 * pages (owner-approved SEO work, 2026-10-13).
 *
 * dateModified was already in the structured data, so a crawler could read it
 * and a reader could not. This prints the same fact where people look, computed
 * from the rows the page already holds: nothing is stored, so it cannot go stale.
 *
 * A future-dated row (a fiscal year that has not ended) never becomes a future
 * "last updated" date. It is clamped to today, which is the same rule the
 * sitemaps apply to <lastmod>, so the page and the sitemap cannot disagree.
 *
 * PURE: no query, no global. The caller hands in the newest date and the number
 * of distinct sources it already counted.
 */

if (!defined('ABSPATH')) exit;

function tit_freshness_line($last_date, $sources, $today = null) {
    $today = $today ?: gmdate('Y-m-d');
    $date = substr((string) $last_date, 0, 10);
    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $date)) $date = '';
    if ($date !== '' && $date > $today) $date = $today;
    $sources = (int) $sources;
    if ($date === '' && $sources <= 0) return '';

    $bits = array();
    if ($date !== '') {
        $ts = strtotime($date . ' 00:00:00 UTC');
        $bits[] = 'Last updated <time datetime="' . esc_attr($date) . '">'
                . esc_html($ts ? date_i18n('j M Y', $ts) : $date) . '</time>';
    }
    if ($sources > 0) {
        $bits[] = esc_html(sprintf(_n('%s source', '%s sources', $sources, 'tit'),
                                   number_format_i18n($sources)));
    }
    return '<p class="tit-freshness tit-note">' . implode(' · ', $bits) . '</p>';
}
