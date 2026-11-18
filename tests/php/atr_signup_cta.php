<?php
/** The tracked sign-up line (CLAUDE.md conversion rule): UTM tags, free match check, clean copy. */
define('ABSPATH', __DIR__);
function esc_url($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
require __DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/includes/atr_signup_cta.php';
$fail = 0;
$check = function ($ok, $what) use (&$fail) { if (!$ok) { $fail++; echo "FAIL: $what\n"; } };
$html = tit_atr_cta_line_html('tracker_home');
$check(strpos($html, 'https://asktherecruiter.com/resume-score?utm_source=talent_tracker&amp;utm_medium=cta&amp;utm_campaign=tracker_home') !== false, 'UTM-tagged free match check link');
$check(strpos($html, 'rel="noopener nofollow"') !== false, 'nofollow on our own product link');
$check(strpos($html, 'aria-label="Resume help"') !== false, 'landmark has a name');
foreach (array('—', '–', 'blind', 'honest', 'hallucinat', 'résumé', '!') as $bad) {
    $check(stripos(strip_tags($html), $bad) === false, "copy avoids $bad");
}
$check(strpos(tit_atr_cta_url('Bad Campaign<x>'), 'utm_campaign=badcampaignx') !== false, 'campaign sanitised');
$check(strpos(tit_atr_cta_url(''), 'utm_campaign=tracker') !== false, 'empty campaign defaults');
$src = file_get_contents(__DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/includes/shortcodes.php');
$check(strpos($src, "tit_atr_cta_line_html('tracker_home')") !== false, 'dashboard carries the line');
// Every other published page is a virtual route that closes through
// tit_render_footer($related); this is the one place that reaches all of
// them (places, roles, recall, corrections, press, methodology, update log,
// sources, every place page). The company page is skipped here because
// company_layout.php already carries its own CTA, and a second one would
// be a duplicate.
$src2 = file_get_contents(__DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/talent-intelligence-tracker.php');
$check(strpos($src2, "\$related !== '' && \$related !== 'company' && function_exists('tit_atr_cta_line_html')") !== false,
       'every routed page carries the line via the shared footer, except the company page which has its own');
$check(strpos($src2, 'echo tit_atr_cta_line_html($related);') !== false,
       'the footer actually prints the line, keyed by the page that is closing');
if ($fail) { echo "atr_signup_cta: $fail failed\n"; exit(1); }
echo "atr_signup_cta: all checks passed\n";
