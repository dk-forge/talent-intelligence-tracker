<?php
/**
 * Shared SEO helpers (SEO audit 2026-10-12, items 3, 5 and 10).
 *
 *  - Company names stored in a registry's ALL-CAPS legal form
 *    ("2 SISTERS FOOD GROUP LIMITED") are shown title-cased with the legal
 *    suffix dropped in titles; titles are capped at 60 characters.
 *  - Generated meta descriptions are capped at 155 characters, cut at a word.
 *  - The page publisher is the Organization "Ask The Recruiter", never a
 *    person. Third-party news items carry datePublished + dateModified.
 *
 * Guard: tests/test_seo_meta.py.
 */
if (!defined('ABSPATH')) exit;

function tit_publisher_org() {
    $org = array('@type' => 'Organization', 'name' => 'Ask The Recruiter', 'url' => 'https://asktherecruiter.com/');
    $logo = function_exists('get_site_icon_url') ? (string) get_site_icon_url(512) : '';
    if ($logo !== '') $org['logo'] = array('@type' => 'ImageObject', 'url' => $logo);
    return $org;
}

/** PURE. Description capped at $max chars at a word boundary, with an ellipsis. */
function tit_seo_cap_description($text, $max = 155) {
    $text = trim(preg_replace('/\s+/u', ' ', (string) $text));
    if ($text === '' || mb_strlen($text, 'UTF-8') <= $max) return $text;
    $cut = mb_substr($text, 0, $max - 1, 'UTF-8');
    $sp = mb_strrpos($cut, ' ', 0, 'UTF-8');
    if ($sp !== false && $sp > (int) ($max * 0.6)) $cut = mb_substr($cut, 0, $sp, 'UTF-8');
    return rtrim($cut, " ,;:.-\u{2013}\u{2014}") . "\u{2026}";
}

/**
 * PURE. A display name for titles. Mixed-case names are left alone (brands
 * like "eBay" or "IBM Research" are written as their owners write them); a
 * name with no lowercase letter is title-cased, short all-letter tokens of
 * three or fewer that look like acronyms are kept upper ("IBM", "UK"), and a
 * trailing legal suffix is dropped.
 */
function tit_company_display_name($name) {
    $name = trim(preg_replace('/\s+/u', ' ', (string) $name));
    if ($name === '') return '';
    $suffix = '/[\s,]+(LIMITED|LTD\.?|INC\.?|INCORPORATED|PLC|LLC|L\.L\.C\.|CORP\.?|CORPORATION|GMBH|S\.?A\.?|AG|N\.?V\.?|B\.?V\.?)$/iu';
    $is_caps = !preg_match('/\p{Ll}/u', $name) && preg_match('/\p{Lu}.*\p{Lu}/u', $name);
    if ($is_caps) {
        $words = explode(' ', mb_strtolower($name, 'UTF-8'));
        $small = array('and', 'of', 'the', 'for', 'de', 'la', 'du', 'van', 'von');
        foreach ($words as $i => $w) {
            $orig = explode(' ', $name)[$i];
            if ($i > 0 && in_array($w, $small, true)) {
                $words[$i] = $w;
            } elseif (preg_match('/^\p{Lu}{2,3}$/u', $orig) && !in_array($w, array('and', 'the', 'for', 'ltd', 'inc', 'plc', 'llc', 'lab', 'bio', 'one', 'two', 'new', 'big', 'red', 'top', 'pay'), true)) {
                $words[$i] = $orig;
            } else {
                $words[$i] = preg_replace_callback('/(^|[\-\/&(\'])(\p{Ll})/u', function ($m) {
                    return $m[1] . mb_strtoupper($m[2], 'UTF-8');
                }, $w);
            }
        }
        $name = implode(' ', $words);
    }
    $stripped = trim(preg_replace($suffix, '', $name));
    return $stripped !== '' ? $stripped : $name;
}

/** PURE. A title no longer than $max characters, cut at a word. */
function tit_seo_cap_title($title, $max = 60) {
    $title = trim((string) $title);
    if (mb_strlen($title, 'UTF-8') <= $max) return $title;
    $cut = mb_substr($title, 0, $max - 1, 'UTF-8');
    $sp = mb_strrpos($cut, ' ', 0, 'UTF-8');
    if ($sp !== false && $sp > (int) ($max * 0.5)) $cut = mb_substr($cut, 0, $sp, 'UTF-8');
    return rtrim($cut, " ,;:.-") . "\u{2026}";
}

/**
 * PURE. The company page title: "<Name>: N tracked updates on hiring,
 * funding and leadership" when it fits in 60, else "<Name>: N tracked
 * updates", else the name cut to fit.
 */
function tit_company_seo_title($name, $updates) {
    $display = tit_company_display_name($name);
    $n = number_format((int) $updates);
    $noun = ((int) $updates === 1) ? 'update' : 'updates';
    $long = sprintf('%s: %s tracked %s on hiring, funding and leadership', $display, $n, $noun);
    if (mb_strlen($long, 'UTF-8') <= 60) return $long;
    $short = sprintf('%s: %s tracked %s', $display, $n, $noun);
    if (mb_strlen($short, 'UTF-8') <= 60) return $short;
    return tit_seo_cap_title($display, 60);
}

/**
 * PURE. A news item node with both dates. No publisher: the item was
 * published by the outlet at `url`, not by us, and the stored source_name is
 * a feed id, not an outlet name, so naming either would be a false claim.
 */
function tit_news_item_node($row) {
    $node = array('@type' => 'NewsArticle', 'headline' => (string) ($row['headline'] ?? ''),
                  'url' => (string) ($row['source_url'] ?? ''));
    $d = substr((string) ($row['published_date'] ?? ''), 0, 10);
    if ($d === '') $d = substr((string) ($row['captured_at'] ?? ''), 0, 10);
    if ($d !== '') { $node['datePublished'] = $d; $node['dateModified'] = $d; }
    return $node;
}
