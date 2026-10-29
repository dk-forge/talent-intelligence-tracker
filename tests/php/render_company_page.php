<?php
/**
 * Renders a company profile from fixture rows and checks the layout contract:
 * one h1 inside the header card, section h2s, timeline items as h3, the fact
 * chips, the KPI tiles, the follow card, the method note and the next step,
 * the canonical-bearing pieces untouched, and no em-dash in what is printed.
 *
 * No database: tit_company_render() takes the rows and the profile directly.
 * Set TIT_RENDER_OUT to a path to keep the rendered markup (used to take the
 * after screenshots for the 1.112.0 redesign).
 */
define('ABSPATH', __DIR__);
$tit_plugin = __DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/';

function add_action($h, $f, $p = 10, $a = 1) {}
function add_filter($h, $f, $p = 10, $a = 1) {}
function home_url($path = '') { return 'https://example.test/blog' . $path; }
function rest_url($p = '') { return 'https://example.test/blog/wp-json/' . $p; }
function esc_html($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function number_format_i18n($n, $d = 0) { return number_format((float) $n, (int) $d); }
function date_i18n($f, $t = null) { return gmdate($f, $t === null ? time() : $t); }
function human_time_diff($a, $b = null) { return '2 days'; }
function _n($single, $plural, $count, $domain = '') { return $count == 1 ? $single : $plural; }
function get_option($k, $d = false) { return $d; }
function get_query_var($v) { return ''; }
// An empty slug index: no collisions, nothing moved, so every key is servable.
function get_transient($k) { return array('map' => array(), 'moved' => array(), 'disambiguated' => array()); }
function add_query_arg($k, $v, $url) { return $url . '?' . rawurlencode($k) . '=' . rawurlencode($v); }
function remove_accents($s) { return $s; }
function get_header() {}
function get_footer() {}
function tit_render_header() {}
function tit_render_footer($ctx = '') {}
function tit_lang_attr($r) { return ''; }
function tit_hreflang_attr($r) { return ''; }
function tit_country_name($cc) { return $cc === 'US' ? 'United States' : (string) $cc; }
function tit_archive_pending_note($c) { return ''; }
function tit_direction_labels() {
    return array('hiring' => 'Adding Roles', 'cutting' => 'Cutting Roles',
                 'neutral' => 'Headcount Not Stated', 'mixed' => 'Mixed');
}
function tit_confidence_labels() { return array('reported' => 'News Report'); }
function tit_money_short($usd) { return '$' . round($usd / 1e6, 1) . 'M'; }
function tit_money_basis_summable() { return 'company_raise'; }

require $tit_plugin . 'includes/company.php';
require $tit_plugin . 'includes/company_layout.php';
require $tit_plugin . 'includes/freshness.php';
require $tit_plugin . 'includes/feed.php';
require $tit_plugin . 'includes/breadcrumbs.php';


$rows = array(array(
    'company' => 'Blue Arbor Technologies', 'company_key' => 'blue arbor technologies',
    'source_url' => 'https://www.citybiz.co/article/925585/', 'source_name' => 'citybiz',
    'funding_amount_usd' => 10500000, 'funding_amount' => '$10.5M', 'money_basis' => 'company_raise',
    'funding_stage' => '', 'pillar' => 'company_development', 'confidence' => 'reported',
    'signal_direction' => 'neutral', 'published_date' => '2026-10-28', 'captured_at' => '2026-10-28 22:00:00',
    'city' => '', 'country' => 'US', 'hq_city' => 'Boston', 'hq_country' => 'US',
    'industry' => 'medical_devices', 'headcount' => 0, 'archive_url' => '', 'collector' => 'press',
    'headline' => 'Blue Arbor Technologies Raises $10.5M Series A',
    'talent_readthrough' => 'Blue Arbor Technologies has $10.5M of new capital.',
));
// TIT_FIXTURE_BARE: the row as Blue Arbor's live row carries it (no industry,
// no place), for the screenshot only; the assertions below need the full row.
$bare = getenv('TIT_FIXTURE_BARE');
if ($bare) {
    foreach (array('industry', 'hq_city', 'hq_country', 'country') as $k) $rows[0][$k] = '';
    $rows[0]['funding_stage'] = 'series_a';
}
$profile = tit_company_profile($rows);
$profile['indexable'] = false;

ob_start();
tit_company_render($rows, 'blue-arbor-technologies', $profile);
$html = ob_get_clean();
if ($out = getenv('TIT_RENDER_OUT')) file_put_contents($out, $html);
if ($bare) { echo "rendered bare fixture, no checks\n"; exit(0); }

$fails = array();
$check = function ($ok, $why) use (&$fails) { if (!$ok) $fails[] = $why; };

$check(substr_count($html, '<h1') === 1, 'exactly one h1');
$check(preg_match('#<header class="tit-co-head">.*?<h1>Blue Arbor Technologies</h1>#s', $html) === 1,
       'the h1 sits in the header card');
$check(strpos($html, 'class="tit-co-avatar" aria-hidden="true">BA<') !== false, 'decorative initials avatar');
foreach (array('Industry</span> Medical devices', 'Headquarters</span> Boston, United States') as $chip) {
    $check(strpos($html, $chip) !== false, "chip: $chip");
}
$check(strpos($html, '<time datetime="2026-10-28">') !== false, 'freshness line (dateModified twin) kept');
foreach (array('At a glance', 'Signal timeline', 'Sources and method', 'Applying to Blue Arbor Technologies?') as $h2) {
    $check(preg_match('#<h2[^>]*>' . preg_quote($h2, '#') . '</h2>#', $html) === 1, "h2: $h2");
}
$check(strpos($html, '<h3 class="tit-h">Blue Arbor Technologies Raises') !== false, 'timeline items are h3 under the timeline h2');
$check(strpos($html, '<h2 class="tit-h"') === false, 'no timeline item is an h2');
$check(strpos($html, 'disclosed funding') !== false && strpos($html, 'tit-stats-4') !== false, 'KPI tiles');
$check(strpos($html, 'class="tit-co-follow"') !== false && strpos($html, 'type="application/rss+xml"') !== false,
       'follow card carries the RSS link');
$check(strpos($html, 'href="https://asktherecruiter.com/">Adapt my resume</a>') !== false, 'next-step CTA');
$check(strpos($html, 'Data licensed CC BY 4.0') !== false, 'licence line kept');
$check(strpos($html, 'This profile is thin') !== false, 'thin-profile note kept');
$check(strpos($html, 'application/ld+json') === false, 'no structured data on a noindex profile');
$check(strpos($html, '—') === false, 'no em-dash in rendered copy');
foreach (array('blind', 'honest', 'hallucinat') as $w) {
    $check(stripos($html, $w) === false, "banned word: $w");
}

if ($fails) { echo "render_company_page FAILED:\n  - " . implode("\n  - ", $fails) . "\n"; exit(1); }
echo "render_company_page OK\n";
