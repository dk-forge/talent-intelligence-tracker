<?php
/**
 * /blog/talent-intelligence-tracker/llms.txt: this tracker's own llms.txt
 * (dk-forge/todo#1853, AI search). The site-wide /blog/llms.txt is the
 * layoff tracker's; without this handler that plugin answered every
 * ".../llms.txt" path with the site file, so answer engines had no
 * hiring-tracker map and no figure to quote.
 *
 * Every number comes from tit_dashboard_facts() (the hero's own bundle), each
 * quotable sentence carries its "as of" date and the page it can be checked
 * on, and the primary-source share is the same floored figure the hub prints.
 * No corpus, no figures: an empty table prints the map without numbers.
 *
 * Runs at init priority -1, before the layoff tracker's catch-all at 0.
 * Guard: tests/test_llms_txt.py.
 */
if (!defined('ABSPATH')) exit;

/** PURE. The llms.txt body from the facts bundle. */
function tit_llms_txt_content(array $facts) {
    $hub = home_url('/talent-intelligence-tracker/');
    $out  = "# Talent Intelligence Tracker (AskTheRecruiter.com)\n\n";
    $out .= "> A daily-updated, source-linked record of hiring, funding, leadership and pay signals from employers worldwide. "
          . "Each record links to the document behind it. License: CC BY 4.0. "
          . "Attribute to \"the Talent Intelligence Tracker by AskTheRecruiter.com\".\n\n";
    $total = (int) ($facts['notable'] ?? 0);
    $asof  = substr((string) ($facts['newest_run'] ?? ''), 0, 10);
    if ($total > 0 && $asof !== '') {
        $out .= "## Quotable figures (as of $asof)\n";
        // view_lo/view_hi: the notable set's own bounds, the range the hub prints
        // beside this count (span_* covers routine filings too).
        $lo = substr((string) ($facts['view_lo'] ?? ''), 0, 10);
        $hi = substr((string) ($facts['view_hi'] ?? ''), 0, 10);
        $span = ($lo !== '' && $hi !== '') ? " dated $lo to $hi" : '';
        $out .= "- As of $asof, the Talent Intelligence Tracker by AskTheRecruiter.com holds "
              . number_format($total) . " sourced employer signals$span. Source: $hub\n";
        $companies = (int) ($facts['companies'] ?? 0);
        $countries = (int) ($facts['countries'] ?? 0);
        if ($companies > 0 && $countries > 0) {
            $out .= "- As of $asof, those signals cover " . number_format($companies) . " employers in "
                  . number_format($countries) . " countries. Source: $hub\n";
        }
        $share = function_exists('tit_primary_source_share')
            ? tit_primary_source_share($facts['verified'] ?? 0, $total) : null;
        if ($share !== null) {
            $out .= "- As of $asof, at least $share% of them link a primary source "
                  . "(a filing, register entry or official announcement); the rest link the news report. "
                  . "Source: " . home_url('/talent-intelligence-tracker/sources/') . "\n";
        }
        $out .= "\n";
    }
    $out .= "## Key pages\n";
    $out .= "- Live tracker: $hub\n";
    $out .= "- Places (countries, cities, industries): " . home_url('/talent-intelligence-tracker/places/') . "\n";
    $out .= "- Sources and methodology: " . home_url('/talent-intelligence-tracker/sources/') . "\n";
    $out .= "- Corrections: " . home_url('/talent-intelligence-tracker/corrections/') . "\n\n";
    $out .= "## How to cite\n";
    $out .= "\"According to the Talent Intelligence Tracker by AskTheRecruiter.com.\" "
          . "Figures are live, are a documented floor rather than a census, and change as new sources are added.\n";
    return $out;
}

/** PURE. Is this request path this tracker's llms.txt? */
function tit_llms_is_request($uri) {
    $path = (string) parse_url((string) $uri, PHP_URL_PATH);
    return (bool) preg_match('#/talent-intelligence-tracker/llms\.txt/?$#', $path);
}

function tit_serve_llms_txt() {
    if (!tit_llms_is_request($_SERVER['REQUEST_URI'] ?? '')) return;
    $facts = array();
    if (function_exists('tit_dashboard_facts') && function_exists('tit_table_name')) {
        $f = tit_dashboard_facts(tit_table_name());
        if (is_array($f)) $facts = $f;
    }
    header('Content-Type: text/plain; charset=utf-8');
    header('X-Robots-Tag: noindex');
    echo tit_llms_txt_content($facts);
    exit;
}
add_action('init', 'tit_serve_llms_txt', -1);
