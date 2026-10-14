<?php
/**
 * The hub chart, embeddable (owner-approved SEO work, 2026-10-13).
 *
 *   /talent-intelligence-tracker/embed/market-trend/   a bare page for an iframe
 *
 * THE SAME CHART THE DASHBOARD DRAWS, not a copy: tit_market_trend() under the
 * dashboard's own unfiltered clause (is_current plus tit_notable_where()), and
 * tit_market_trend_html() to draw it. A second implementation would drift and a
 * journalist's embed would quietly disagree with the page it credits.
 *
 * ATTRIBUTION IS VISIBLE TWICE: inside the frame (a credit line linking the
 * hub, CC BY 4.0) and in the snippet itself, outside the <iframe>, so the
 * backlink and the licence survive a site that blocks or strips frames.
 *
 * The embed page is noindex with a canonical to the hub: it is the hub's chart
 * on a second URL and must not compete with it. No JavaScript, no theme.
 */

if (!defined('ABSPATH')) exit;

const TIT_EMBED_PATH = 'talent-intelligence-tracker/embed';
const TIT_EMBED_CHART = 'market-trend';
const TIT_EMBED_TITLE = 'Weekly Updates by Stated Headcount Direction';

function tit_embed_url() {
    return home_url('/' . TIT_EMBED_PATH . '/' . TIT_EMBED_CHART . '/');
}

function tit_embed_hub_url() {
    return home_url('/talent-intelligence-tracker/');
}

function tit_embed_rewrites() {
    return array('^' . TIT_EMBED_PATH . '/([a-z0-9-]+)/?$' => 'index.php?tit_embed=$matches[1]');
}

function tit_embed_rewrite() {
    foreach (tit_embed_rewrites() as $pattern => $target) add_rewrite_rule($pattern, $target, 'top');
}
add_action('init', 'tit_embed_rewrite');

function tit_embed_query_vars($vars) {
    $vars[] = 'tit_embed';
    return $vars;
}
add_filter('query_vars', 'tit_embed_query_vars');

/** The copy-paste snippet. PURE. The backlink sits OUTSIDE the frame. */
function tit_embed_snippet() {
    return '<iframe src="' . esc_url(tit_embed_url()) . '" title="' . esc_attr(TIT_EMBED_TITLE)
         . '" width="100%" height="440" style="border:0;max-width:760px" loading="lazy"></iframe>'
         . "\n" . '<p>Chart: <a href="' . esc_url(tit_embed_hub_url())
         . '">Talent Intelligence Tracker</a>, data licensed CC BY 4.0.</p>';
}

/** The whole framed document around an already-built chart. PURE. */
function tit_embed_document($chart_html, $caveat) {
    $css = TIT_URL . 'assets/dashboard.css?ver=' . rawurlencode(TIT_VERSION);
    $hub = tit_embed_hub_url();
    return '<!doctype html>' . "\n"
         . '<html lang="en"><head><meta charset="utf-8">'
         . '<meta name="viewport" content="width=device-width, initial-scale=1">'
         . '<meta name="robots" content="noindex, follow">'
         . '<link rel="canonical" href="' . esc_url($hub) . '">'
         . '<title>' . esc_html(TIT_EMBED_TITLE . ' · Talent Intelligence Tracker') . '</title>'
         . '<link rel="stylesheet" href="' . esc_url($css) . '">'
         . '<style>body{margin:0;padding:8px;background:#fff}</style>'
         . '</head><body><div class="tit-wrap tit-embed">'
         . '<h2 class="tit-place-h2">' . esc_html(TIT_EMBED_TITLE) . '</h2>'
         . ($caveat !== '' ? '<p class="tit-chart-caveat">' . esc_html($caveat) . '</p>' : '')
         . '<div class="tit-market-box" role="group" aria-label="Weekly updates by stated headcount direction">' . $chart_html . '</div>'
         . '<p class="tit-cite">Source: <a href="' . esc_url($hub)
         . '" target="_blank" rel="noopener">Talent Intelligence Tracker</a>'
         . ' · data licensed CC BY 4.0 · every update links to the document behind it.</p>'
         . '</div></body></html>' . "\n";
}

/** The live chart, built the dashboard's way and cached like it. */
function tit_embed_chart_parts() {
    $cached = get_transient('tit_embed_market');
    if (is_array($cached)) return $cached;
    if (!function_exists('tit_market_trend') || !function_exists('tit_market_trend_html')) {
        return array('', '');
    }
    $notable = function_exists('tit_notable_where') ? tit_notable_where() : '1 = 1';
    $market = tit_market_trend(tit_table_name(), "is_current = 1 AND {$notable}");
    $parts = array(tit_market_trend_html($market),
                   function_exists('tit_market_caveat') ? tit_market_caveat($market) : '');
    set_transient('tit_embed_market', $parts, defined('TIT_CACHE_TTL') ? TIT_CACHE_TTL : 300);
    return $parts;
}

function tit_embed_template() {
    $which = (string) get_query_var('tit_embed');
    if ($which === '') return;
    if ($which !== TIT_EMBED_CHART) {
        status_header(404);
        nocache_headers();
        include get_404_template();
        exit;
    }
    list($chart, $caveat) = tit_embed_chart_parts();
    status_header(200);
    header('Content-Type: text/html; charset=UTF-8', true);
    // Framable from anywhere: that is the point of the page.
    if (function_exists('header_remove')) header_remove('X-Frame-Options');
    header('Content-Security-Policy: frame-ancestors *', true);
    echo tit_embed_document($chart, $caveat);
    exit;
}
add_action('template_redirect', 'tit_embed_template', 1);

/** The press page's "Embed the chart" section. No query: the snippet is static. */
function tit_embed_press_section() {
    return '<h2 id="press-embed">Embed the Chart</h2>'
         . '<p class="tit-note">Put the tracker\'s weekly chart on your page. It updates by itself,'
         . ' and the credit line linking back here is part of the CC BY 4.0 licence, so please keep it.'
         . ' <a href="' . esc_url(tit_embed_url()) . '">Preview the embed</a>.</p>'
         . '<textarea class="tit-embed-code" readonly rows="4" style="width:100%;font-family:monospace"'
         . ' aria-label="Embed code">' . esc_html(tit_embed_snippet()) . '</textarea>';
}
