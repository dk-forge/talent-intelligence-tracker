<?php
/**
 * Phone sign-up bar on every hiring-tracker page (dk-forge/todo#1853, CTA).
 *
 * The layoff tracker has had this bar since 2026-11-17; the hiring tracker had
 * none, and its header button is hidden under 600px, so a phone reader had no
 * visible route to the product. Same pattern: phones only, closable, the close
 * remembered per browser (storage wrapped, private windows throw), and body
 * padding equal to the bar so it never covers the last line.
 *
 * Destination: the free match check (/resume-score), UTM-tagged with this
 * tracker's existing source. Guard: tests/test_atr_bar.py.
 */
if (!defined('ABSPATH')) exit;

const TIT_ATR_BAR_URL = 'https://asktherecruiter.com/resume-score?utm_source=talent-tracker&utm_medium=referral&utm_campaign=sticky_bar';

/** PURE. Every hiring-tracker route: hub, company, places, sources, press... */
function tit_atr_bar_is_route($uri) {
    $path = (string) parse_url((string) $uri, PHP_URL_PATH);
    return (bool) preg_match('#/talent-intelligence-tracker(/|$)#', $path)
        && !preg_match('#\.(txt|xml|json|csv)$#', $path);
}

/** PURE. The bar's markup and style. */
function tit_atr_bar_html() {
    return '<style id="tit-atr-bar-css">'
        . '.tit-atr-bar{position:fixed;left:0;right:0;bottom:0;z-index:999;display:flex;align-items:center;gap:.5rem;'
        . 'padding:.5rem 16px calc(.5rem + env(safe-area-inset-bottom));background:#0f2b46;color:#fff;box-shadow:0 -2px 8px rgba(0,0,0,.2)}'
        . '.tit-atr-bar a{flex:1;display:flex;align-items:center;justify-content:center;min-height:44px;border-radius:6px;'
        . 'background:#fff;color:#0f2b46;font-weight:700;text-decoration:none}'
        . '.tit-atr-bar a:focus-visible,.tit-atr-bar button:focus-visible{outline:3px solid #ffbf47;outline-offset:2px}'
        . '.tit-atr-bar button{min-width:44px;min-height:44px;border:0;border-radius:6px;background:transparent;color:#fff;'
        . 'font-size:1.5rem;line-height:1;cursor:pointer}'
        . 'body.tit-atr-bar-on{padding-bottom:calc(64px + env(safe-area-inset-bottom))}'
        . '.tit-atr-bar[hidden]{display:none}'
        . '@media (min-width:601px){.tit-atr-bar{display:none}body.tit-atr-bar-on{padding-bottom:0}}'
        . '@media print{.tit-atr-bar{display:none}}'
        . '</style>'
        . '<div class="tit-atr-bar" id="tit-atr-bar" role="region" aria-label="Resume help" hidden>'
        . '<a href="' . esc_url(TIT_ATR_BAR_URL) . '" target="_blank" rel="noopener nofollow">Check my resume, free</a>'
        . '<button type="button" aria-label="Close" id="tit-atr-bar-close">&times;</button>'
        . '</div>';
}

function tit_atr_bar_footer() {
    if (!tit_atr_bar_is_route($_SERVER['REQUEST_URI'] ?? '')) return;
    echo tit_atr_bar_html();
}
// Priority 5: before footer scripts print at 20, so the bar exists when atr-bar.js runs.
add_action('wp_footer', 'tit_atr_bar_footer', 5);

function tit_atr_bar_enqueue() {
    if (!tit_atr_bar_is_route($_SERVER['REQUEST_URI'] ?? '')) return;
    wp_enqueue_script('tit-atr-bar', TIT_URL . 'assets/atr-bar.js', array(), TIT_VERSION, true);
}
add_action('wp_enqueue_scripts', 'tit_atr_bar_enqueue');
