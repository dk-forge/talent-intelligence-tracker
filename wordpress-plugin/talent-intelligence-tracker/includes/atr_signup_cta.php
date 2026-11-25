<?php
/**
 * The AskTheRecruiter sign-up line on the tracker page and company pages.
 *
 * CLAUDE.md conversion rule (owner 2026-11-17): every published page carries a
 * visible, UTM-tagged route to the free match check on phone and desktop. Until
 * 1.117.0 the dashboard had no tracked route at all and the company "Adapt my
 * resume" button pointed at the bare home page, so no sign-up from here could
 * be counted. The destination is /resume-score, which is free at every tier
 * (no model call), so "Free" is true; no time is promised.
 * Pinned by tests/php/atr_signup_cta.php and tests/php/render_company_page.php.
 */
if (!defined('ABSPATH')) exit;

function tit_atr_cta_url($campaign, $content = '') {
    $campaign = preg_replace('/[^a-z0-9_-]/', '', strtolower((string) $campaign));
    $content  = preg_replace('/[^a-z0-9_-]/', '', strtolower((string) $content));
    $query = array(
        'utm_source'   => 'talent_tracker',
        'utm_medium'   => 'cta',
        'utm_campaign' => $campaign !== '' ? $campaign : 'tracker',
    );
    // Placement (inline line, sticky bar, llms.txt) so GA4 can credit a sign-up to it.
    if ($content !== '') $query['utm_content'] = $content;
    return 'https://asktherecruiter.com/resume-score?' . http_build_query($query);
}

/** One plain line in the page flow: shows at every width, no script. */
function tit_atr_cta_line_html($campaign, $label = 'Resume help') {
    // $label: a second line on one page (the top-of-page one) needs its own
    // landmark name, so a screen reader's landmark list can tell them apart.
    return '<aside class="tit-atr-cta" aria-label="' . esc_attr($label) . '"'
        . ' style="margin:1rem 0;padding:.7rem .9rem;border:1px solid rgba(127,127,127,.4);border-radius:8px;line-height:1.5">'
        . '<b>Applying for a role?</b> Check how your resume fits the job. Free, no card. '
        . '<a href="' . esc_url(tit_atr_cta_url($campaign, 'inline')) . '" target="_blank" rel="noopener nofollow"'
        . ' style="font-weight:600;text-decoration:underline;display:inline-block;min-height:24px">'
        . 'Check my resume</a></aside>';
}
