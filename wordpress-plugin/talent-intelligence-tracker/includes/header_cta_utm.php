<?php
/**
 * THE SITE HEADER'S SIGN-UP LINK CARRIES THIS TRACKER'S SOURCE (todo#1881
 * item 2, review B8). The header CTA is a theme menu link tagged
 * utm_source=blog&utm_medium=nav&utm_campaign=header-cta on every /blog page,
 * so a sign-up that started on a tracker page was counted as "blog". On this
 * tracker's own pages the source becomes talent_tracker (the scheme every
 * other CTA here uses); medium and campaign stay, so the slot is still
 * readable. Other blog pages are untouched.
 */
if (!defined('ABSPATH')) exit;

function tit_header_cta_retag($html) {
    if (strpos($html, 'utm_source=blog') === false) return $html;
    return preg_replace(
        '#(href="https://asktherecruiter\.com/[^"]*?[?&](?:amp;|\#038;)?)utm_source=blog((?:&|&amp;|&\#038;)utm_medium=nav)#',
        '$1utm_source=talent_tracker$2',
        $html
    );
}

/** This tracker's pages, the role routes included (tit_is_our_page omits them). */
function tit_header_cta_applies() {
    if (function_exists('tit_is_our_page') && tit_is_our_page()) return true;
    return (bool) (get_query_var('tit_role') || get_query_var('tit_roles'));
}

/**
 * The filter sees every block's output, on the dashboard (a theme page) and
 * on sub-pages (tit_render_header() renders the part through do_blocks).
 * tit_render_header() also retags its buffer as a belt-and-braces pass; the
 * rewrite is idempotent, so the second pass changes nothing.
 * The regex only touches an asktherecruiter.com link tagged
 * utm_source=blog&utm_medium=nav, so the block type does not matter.
 */
function tit_header_cta_filter($block_content, $block) {
    // Cheap test first: this runs for every block on every page.
    if (strpos($block_content, 'utm_source=blog') === false) return $block_content;
    static $ours = null;
    if ($ours === null) $ours = tit_header_cta_applies();
    return $ours ? tit_header_cta_retag($block_content) : $block_content;
}
add_filter('render_block', 'tit_header_cta_filter', 20, 2);
