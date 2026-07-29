<?php
/**
 * Plugin Name: Talent Intelligence Tracker
 * Description: Hiring, leadership, compensation and location signals, sourced to primary documents.
 * Version: 1.45.2
 * Author: dk-forge
 * License: MIT
 *
 * SEPARATE PLUGIN, DELIBERATELY. This shares no code, no database table and no
 * REST namespace with the AI Layoff Tracker. WordPress fatals an entire plugin
 * on one bad require, and the layoff tracker is live — a shared file would mean
 * one mistake takes down both products.
 *
 * Everything here is prefixed tit_ / TIT_ and writes only to {prefix}tit_signals.
 * If you are editing this file and see the sibling's prefix anywhere, you are
 * in the wrong plugin.
 */

if (!defined('ABSPATH')) exit;

define('TIT_VERSION', '1.45.2');
define('TIT_PATH', plugin_dir_path(__FILE__));
define('TIT_URL', plugin_dir_url(__FILE__));
define('TIT_TABLE_SUFFIX', 'tit_signals');

/**
 * FTP deploys land files one at a time, so an include can be missing for a few
 * seconds mid-upload. A hard require of a not-yet-uploaded file fatals the
 * whole plugin on every request until the upload finishes. Guard each one and
 * carry on without it.
 */
function tit_require($relative) {
    $path = TIT_PATH . $relative;
    if (is_readable($path)) {
        require_once $path;
        return true;
    }
    return false;
}

tit_require('includes/db.php');
tit_require('includes/api.php');
tit_require('includes/export.php');
tit_require('includes/shortcodes.php');
tit_require('includes/page.php');
tit_require('includes/company.php');
tit_require('includes/sources.php');
tit_require('includes/corrections.php');
tit_require('includes/recall.php');
tit_require('includes/board_series.php');
tit_require('includes/htaccess.php');

// Stub fallbacks so a partial upload degrades instead of fatalling.
if (!function_exists('tit_table_name')) {
    function tit_table_name() {
        global $wpdb;
        return $wpdb->prefix . TIT_TABLE_SUFFIX;
    }
}

/**
 * Chrome for our own routed pages (the sources page, company profiles).
 *
 * Those routes render straight into the response, so they have to produce the
 * page shell themselves. They called get_header() / get_footer(), which is the
 * CLASSIC theme API: it looks for the theme's header.php. The active theme is
 * Twenty Twenty-Five, a BLOCK theme, which has no header.php at all, so
 * get_header() fell through to WordPress's theme-compat fallback and printed
 * little more than the site title as a bare underlined link. Every company
 * profile and the sources page therefore shipped with no logo and no
 * navigation, while the main tracker page (a normal post, rendered by the
 * theme) looked right. Those routed pages are the SEO surface, so the broken
 * chrome was on exactly the pages a stranger arrives at first.
 *
 * A block theme's chrome is a template part, not a PHP file, so it is rendered
 * with block_template_part() inside the same wp-site-blocks wrapper the theme's
 * own templates use. wp_head() and wp_footer() fire exactly once on either
 * path: our own enqueues and the SEO plugin's meta tags hang off them, and
 * firing twice would duplicate the entire head.
 *
 * The classic path is kept as the fallback so this keeps working if the site
 * ever moves to a classic theme, or runs a WordPress without
 * block_template_part() (added in 6.0).
 */
function tit_block_shell() {
    static $block = null;
    if ($block === null) {
        $block = (function_exists('wp_is_block_theme') && wp_is_block_theme()
                  && function_exists('block_template_part'));
    }
    return $block;
}

function tit_render_header() {
    if (!tit_block_shell()) {
        get_header();
        return;
    }
    ?><!DOCTYPE html>
<html <?php language_attributes(); ?>>
<head>
<meta charset="<?php bloginfo('charset'); ?>" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<?php
    // WordPress's classic theme-compat header prints a <title> tag of its own,
    // which is what used to give these routes a title. wp_head() alone does not
    // always: _wp_render_title_tag is removed by the SEO plugin, and its
    // replacement only fires for queries it recognises, which ours are not. The
    // sources page shipped with no <title> at all on 1.30.1 as a result.
    //
    // Buffer the head, and supply the title only when nothing else did. That
    // cannot double-print, and it cannot fight a plugin that is doing its job.
    ob_start();
    wp_head();
    $tit_head = ob_get_clean();
    if (stripos($tit_head, '<title') === false) {
        echo '<title>' . esc_html(wp_get_document_title()) . "</title>\n";
    }
    echo $tit_head;
?>
</head>
<body <?php body_class(); ?>>
<?php
    wp_body_open();
    // The theme's own canvas wrapper. Without it the header template part
    // renders outside the layout its styles are written against, and the site
    // header sits at a different width from every other page.
    echo '<div class="wp-site-blocks">';
    block_template_part('header');
    // Named so the skip link has somewhere to land, matching the theme.
    echo '<main id="wp--skip-link--target" class="tit-main">';
}

function tit_render_footer() {
    if (!tit_block_shell()) {
        get_footer();
        return;
    }
    echo '</main>';
    block_template_part('footer');
    echo '</div>';
    wp_footer();
    echo '</body></html>';
}

/**
 * FTP deploys bypass activation hooks entirely, so nothing runs on "activate".
 * A version change is the only reliable signal that new code has landed: use it
 * to run migrations and flush caches on the next request.
 */
function tit_maybe_upgrade() {
    if (get_option('tit_installed_version') === TIT_VERSION) {
        return;
    }
    if (function_exists('tit_create_or_update_table')) {
        tit_create_or_update_table();
    }
    if (function_exists('tit_flush_caches')) {
        tit_flush_caches();
    }
    // The .htaccess header block is guarded by a 12h "verified" transient. A
    // deploy that CHANGES those rules (cache lifetimes, endpoint list) would
    // otherwise sit unapplied for up to 12 hours while every other cache
    // updated instantly — the sibling lost half a day to exactly this. A
    // version bump means the desired block may differ, so drop the guard and
    // let tit_htaccess_ensure() re-verify on this deploy.
    delete_transient('tit_htaccess_ok');
    update_option('tit_installed_version', TIT_VERSION, false);
}

/**
 * Columns the running code cannot work without.
 *
 * /query names these in its SELECT and its ORDER BY, so a missing one is not a
 * degraded feature: it is an empty table on a page that reports thousands of
 * rows, which is exactly what 1.30.0 shipped.
 */
function tit_required_columns() {
    return array('cik', 'employer_type', 'funding_amount_usd', 'funding_stage',
                 'deal_type', 'site_event', 'materiality');
}

/**
 * Prove the schema is actually there, and repair it if it is not.
 *
 * The version gate above is necessary and not sufficient. An FTP deploy lands
 * files ONE AT A TIME, so the first request after an upload can be served by a
 * new plugin header and an old includes/db.php: tit_maybe_upgrade sees a new
 * TIT_VERSION, runs dbDelta against the PREVIOUS schema, adds nothing, and then
 * writes the new version into tit_installed_version. Every later request skips
 * the migration because the version now matches, and the column never appears.
 *
 * Not hypothetical. On 1.30.0 the deploy was green, the plugin reported
 * 1.30.0, /aggregate answered, and every row query returned an empty list
 * because `materiality` did not exist. A one-shot migration triggered by a
 * version bump cannot survive a racy transport; a check that verifies the
 * RESULT can. Same retry-until-verified shape as the .htaccess block.
 *
 * Cheap: one transient read per request, and a SHOW COLUMNS only when that has
 * lapsed. The success marker is written ONLY when the schema is genuinely
 * complete, so a real failure retries in five minutes instead of being cached
 * as healthy for six hours.
 */
function tit_verify_schema() {
    if (get_transient('tit_schema_ok')) return;
    if (!function_exists('tit_create_or_update_table')) return;

    global $wpdb;
    $table = tit_table_name();
    $need  = tit_required_columns();

    $cols = $wpdb->get_col("SHOW COLUMNS FROM {$table}");
    if (!is_array($cols) || array_diff($need, $cols)) {
        tit_create_or_update_table();
        $cols = $wpdb->get_col("SHOW COLUMNS FROM {$table}");
        // The repair changes what a query can see, so anything cached before it
        // was computed against the old shape.
        if (function_exists('tit_flush_caches')) tit_flush_caches();
    }

    $complete = is_array($cols) && !array_diff($need, $cols);
    set_transient('tit_schema_ok', $complete ? 'ok' : 'retry',
                  $complete ? 6 * HOUR_IN_SECONDS : 5 * MINUTE_IN_SECONDS);
}

/**
 * Cache-busting version for a bundled asset: the plugin version plus the
 * file's own modification time.
 *
 * The version alone is not enough. FTP deploys can ship a CSS-only fix without
 * moving the constant, and this host runs Autoptimize, which serves a rewritten
 * copy of the file keyed on whatever version string we hand it. Same string,
 * same stale copy, and the deploy looks like it never landed.
 *
 * filemtime() on a missing file warns and returns false, and an FTP deploy has
 * a window where the file is not there yet, so it is guarded.
 */
function tit_asset_version($relative_path) {
    $file = TIT_PATH . $relative_path;
    $mtime = is_readable($file) ? filemtime($file) : 0;
    return $mtime ? TIT_VERSION . '.' . $mtime : TIT_VERSION;
}

/**
 * ISO codes are how the data is stored and a bad thing to read. "BE" is not a
 * country to anyone who is not already thinking in codes.
 *
 * The list covers what we actually collect from; an unknown code falls through
 * to the code itself rather than to a guess.
 */
function tit_country_names() {
    return array(
        'US' => 'United States', 'CA' => 'Canada', 'GB' => 'United Kingdom',
        'IE' => 'Ireland', 'DE' => 'Germany', 'FR' => 'France',
        'NL' => 'Netherlands', 'BE' => 'Belgium', 'ES' => 'Spain',
        'IT' => 'Italy', 'SE' => 'Sweden', 'NO' => 'Norway',
        'DK' => 'Denmark', 'FI' => 'Finland', 'PL' => 'Poland',
        'CH' => 'Switzerland', 'AT' => 'Austria', 'PT' => 'Portugal',
        'CZ' => 'Czechia', 'GR' => 'Greece', 'RO' => 'Romania',
        'HU' => 'Hungary', 'IN' => 'India', 'SG' => 'Singapore',
        'JP' => 'Japan', 'CN' => 'China', 'HK' => 'Hong Kong',
        'AU' => 'Australia', 'NZ' => 'New Zealand', 'KR' => 'South Korea',
        'MY' => 'Malaysia', 'PH' => 'Philippines', 'ID' => 'Indonesia',
        'TH' => 'Thailand', 'VN' => 'Vietnam', 'TW' => 'Taiwan',
        'BR' => 'Brazil', 'MX' => 'Mexico', 'AR' => 'Argentina',
        'CL' => 'Chile', 'CO' => 'Colombia', 'PE' => 'Peru',
        'AE' => 'United Arab Emirates', 'SA' => 'Saudi Arabia',
        'IL' => 'Israel', 'QA' => 'Qatar', 'TR' => 'Turkey',
        'ZA' => 'South Africa', 'NG' => 'Nigeria', 'KE' => 'Kenya',
        'EG' => 'Egypt', 'MA' => 'Morocco',
        // Everything below was missing, and a missing code is not a blank, it
        // is the raw code printed in the middle of a chart of country names:
        // "LV" and "NA" sat next to "United States" and "Ireland". We read 38
        // countries and this list held 52 of roughly 200, so the gap was always
        // going to show. Cheaper to carry the world than to keep patching it
        // one embarrassment at a time.
        'LV' => 'Latvia', 'LT' => 'Lithuania', 'EE' => 'Estonia',
        'SK' => 'Slovakia', 'SI' => 'Slovenia', 'HR' => 'Croatia',
        'BG' => 'Bulgaria', 'RS' => 'Serbia', 'UA' => 'Ukraine',
        'IS' => 'Iceland', 'LU' => 'Luxembourg', 'MT' => 'Malta',
        'CY' => 'Cyprus', 'AL' => 'Albania', 'BA' => 'Bosnia and Herzegovina',
        'ME' => 'Montenegro', 'MK' => 'North Macedonia', 'MD' => 'Moldova',
        'BY' => 'Belarus', 'MC' => 'Monaco', 'LI' => 'Liechtenstein',
        'AD' => 'Andorra', 'SM' => 'San Marino', 'VA' => 'Vatican City',
        'XK' => 'Kosovo', 'RU' => 'Russia', 'GE' => 'Georgia',
        'AM' => 'Armenia', 'AZ' => 'Azerbaijan', 'KZ' => 'Kazakhstan',
        'UZ' => 'Uzbekistan',
        'NA' => 'Namibia', 'TZ' => 'Tanzania', 'UG' => 'Uganda',
        'ZM' => 'Zambia', 'ZW' => 'Zimbabwe', 'BW' => 'Botswana',
        'MZ' => 'Mozambique', 'AO' => 'Angola', 'SN' => 'Senegal',
        'CI' => "Cote d'Ivoire", 'CM' => 'Cameroon', 'DZ' => 'Algeria',
        'TN' => 'Tunisia', 'LY' => 'Libya', 'SD' => 'Sudan',
        'SS' => 'South Sudan', 'RW' => 'Rwanda', 'MW' => 'Malawi',
        'MU' => 'Mauritius', 'MG' => 'Madagascar', 'CD' => 'DR Congo',
        'CG' => 'Congo', 'GA' => 'Gabon', 'BJ' => 'Benin',
        'BF' => 'Burkina Faso', 'ML' => 'Mali', 'NE' => 'Niger',
        'TD' => 'Chad', 'SO' => 'Somalia', 'SL' => 'Sierra Leone',
        'LR' => 'Liberia', 'GM' => 'Gambia', 'GH' => 'Ghana', 'ET' => 'Ethiopia',
        'PK' => 'Pakistan', 'BD' => 'Bangladesh', 'LK' => 'Sri Lanka',
        'NP' => 'Nepal', 'MM' => 'Myanmar', 'KH' => 'Cambodia',
        'LA' => 'Laos', 'MN' => 'Mongolia', 'MO' => 'Macau',
        'BN' => 'Brunei', 'MV' => 'Maldives',
        'KW' => 'Kuwait', 'BH' => 'Bahrain', 'OM' => 'Oman',
        'JO' => 'Jordan', 'LB' => 'Lebanon', 'IQ' => 'Iraq',
        'IR' => 'Iran', 'SY' => 'Syria', 'YE' => 'Yemen', 'PS' => 'Palestine',
        'UY' => 'Uruguay', 'CR' => 'Costa Rica', 'EC' => 'Ecuador',
        'BO' => 'Bolivia', 'PY' => 'Paraguay', 'VE' => 'Venezuela',
        'GT' => 'Guatemala', 'HN' => 'Honduras', 'SV' => 'El Salvador',
        'NI' => 'Nicaragua', 'PA' => 'Panama', 'DO' => 'Dominican Republic',
        'CU' => 'Cuba', 'JM' => 'Jamaica', 'TT' => 'Trinidad and Tobago',
        'HT' => 'Haiti', 'BZ' => 'Belize', 'GY' => 'Guyana', 'SR' => 'Suriname',
        'FJ' => 'Fiji', 'PG' => 'Papua New Guinea',
        // The REST of ISO 3166-1 alpha-2, territories included. "PR" reached a
        // live chart as a bare code sitting between "Ireland" and "United
        // States", which is the same failure as "LV" and "NA" before it and
        // was fixed the same wrong way twice: by adding the codes that had
        // embarrassed us. A tracker that reads SEC filings will see Puerto
        // Rico, Guam and the Virgin Islands, and an employer can be anywhere,
        // so the list is now the whole standard and there is nothing left to
        // patch. tit_country_name() also refuses to print a bare code.
        'AF' => 'Afghanistan', 'AG' => 'Antigua and Barbuda', 'AI' => 'Anguilla',
        'AQ' => 'Antarctica', 'AS' => 'American Samoa', 'AW' => 'Aruba',
        'AX' => 'Aland Islands', 'BB' => 'Barbados', 'BI' => 'Burundi',
        'BL' => 'Saint Barthelemy', 'BM' => 'Bermuda',
        'BQ' => 'Caribbean Netherlands', 'BS' => 'Bahamas', 'BT' => 'Bhutan',
        'BV' => 'Bouvet Island', 'CC' => 'Cocos Islands',
        'CF' => 'Central African Republic', 'CK' => 'Cook Islands',
        'CV' => 'Cabo Verde', 'CW' => 'Curacao', 'CX' => 'Christmas Island',
        'DJ' => 'Djibouti', 'DM' => 'Dominica', 'EH' => 'Western Sahara',
        'ER' => 'Eritrea', 'FK' => 'Falkland Islands', 'FM' => 'Micronesia',
        'FO' => 'Faroe Islands', 'GD' => 'Grenada', 'GF' => 'French Guiana',
        'GG' => 'Guernsey', 'GI' => 'Gibraltar', 'GL' => 'Greenland',
        'GN' => 'Guinea', 'GP' => 'Guadeloupe', 'GQ' => 'Equatorial Guinea',
        'GS' => 'South Georgia', 'GU' => 'Guam', 'GW' => 'Guinea-Bissau',
        'HM' => 'Heard and McDonald Islands', 'IM' => 'Isle of Man',
        'IO' => 'British Indian Ocean Territory', 'JE' => 'Jersey',
        'KG' => 'Kyrgyzstan', 'KI' => 'Kiribati', 'KM' => 'Comoros',
        'KN' => 'Saint Kitts and Nevis', 'KP' => 'North Korea',
        'KY' => 'Cayman Islands', 'LC' => 'Saint Lucia', 'LS' => 'Lesotho',
        'MF' => 'Saint Martin', 'MH' => 'Marshall Islands',
        'MP' => 'Northern Mariana Islands', 'MQ' => 'Martinique',
        'MR' => 'Mauritania', 'MS' => 'Montserrat', 'NC' => 'New Caledonia',
        'NF' => 'Norfolk Island', 'NR' => 'Nauru', 'NU' => 'Niue',
        'PF' => 'French Polynesia', 'PM' => 'Saint Pierre and Miquelon',
        'PN' => 'Pitcairn', 'PR' => 'Puerto Rico', 'PW' => 'Palau',
        'RE' => 'Reunion', 'SB' => 'Solomon Islands', 'SC' => 'Seychelles',
        'SH' => 'Saint Helena', 'SJ' => 'Svalbard and Jan Mayen',
        'ST' => 'Sao Tome and Principe', 'SX' => 'Sint Maarten', 'SZ' => 'Eswatini',
        'TC' => 'Turks and Caicos Islands', 'TF' => 'French Southern Territories',
        'TG' => 'Togo', 'TJ' => 'Tajikistan', 'TK' => 'Tokelau',
        'TL' => 'Timor-Leste', 'TM' => 'Turkmenistan', 'TO' => 'Tonga',
        'TV' => 'Tuvalu', 'UM' => 'United States Minor Outlying Islands',
        'VC' => 'Saint Vincent and the Grenadines', 'VG' => 'British Virgin Islands',
        'VI' => 'United States Virgin Islands', 'VU' => 'Vanuatu',
        'WF' => 'Wallis and Futuna', 'WS' => 'Samoa', 'YT' => 'Mayotte',
    );
}

/**
 * The flag for an ISO 3166-1 alpha-2 code, derived rather than looked up.
 *
 * Regional indicator symbols: 'A' maps to U+1F1E6, so a two-letter code becomes
 * two code points at a fixed offset. There is no map to maintain and therefore
 * no map to fall behind. A hardcoded table is exactly how "PR" ended up
 * rendering as a bare code, and the country list grows every week.
 *
 * The flag is DECORATION and never the label. Every caller prints the country
 * name beside it and marks the glyph aria-hidden, because a platform with no
 * font for a given flag renders the two regional-indicator letters instead, or
 * a blank box, and a reader must never be left with only that.
 *
 * Two refusals, both deliberate:
 *  - a code we do not recognise gets no flag, so this can never disagree with
 *    tit_country_name(), which prints "XX (unmapped)" for the same input;
 *  - codes with no flag in Unicode's recommended set get none either, rather
 *    than a tofu square.
 *
 * Encoded by hand rather than through mb_chr(): every code point in this range
 * is four UTF-8 bytes, and mbstring is not guaranteed on shared hosting.
 */
function tit_country_flag($code) {
    $code = strtoupper(trim((string) $code));
    if (!preg_match('/^[A-Z]{2}$/', $code)) return '';

    // Not a real ISO 3166-1 assignment (XK is user-assigned for Kosovo), so no
    // flag sequence exists and every platform would draw a placeholder.
    static $no_flag = array('XK' => true);
    if (isset($no_flag[$code])) return '';

    // Unknown to our own name map means we do not trust the code enough to
    // decorate it. The name guard and this must agree.
    $names = tit_country_names();
    if (!isset($names[$code])) return '';

    $out = '';
    for ($i = 0; $i < 2; $i++) {
        $cp = 0x1F1E6 + (ord($code[$i]) - 65);
        $out .= chr(0xF0 | ($cp >> 18))
              . chr(0x80 | (($cp >> 12) & 0x3F))
              . chr(0x80 | (($cp >> 6) & 0x3F))
              . chr(0x80 | ($cp & 0x3F));
    }
    return $out;
}

/**
 * Flag plus name, as markup, with the flag hidden from assistive technology.
 * One helper so no caller can forget the aria-hidden or drop the name.
 */
function tit_country_label_html($code) {
    $flag = tit_country_flag($code);
    $name = tit_country_name($code);
    return ($flag ? '<span class="tit-flag" aria-hidden="true">' . $flag . '</span>' : '')
         . '<span class="tit-cname">' . esc_html($name) . '</span>';
}

/**
 * A code must never reach the page as a code.
 *
 * "PR" appeared as a bar label between "Ireland" and "United States", which is
 * the third time an unmapped code has been found by a reader rather than by us.
 * The map is now the whole of ISO 3166-1 alpha-2, so this should be
 * unreachable; if it ever is reached it says so in words, and leaves a trace in
 * the log for us to act on, rather than printing two letters and hoping nobody
 * notices.
 */
function tit_country_name($code) {
    $code = strtoupper(trim((string) $code));
    if ($code === '') return '';
    $names = tit_country_names();
    if (isset($names[$code])) return $names[$code];

    if (defined('WP_DEBUG') && WP_DEBUG) {
        error_log('[talent-intelligence-tracker] unmapped country code: ' . $code);
    }
    return $code . ' (unmapped)';
}

add_action('init', 'tit_maybe_upgrade', 1);
// Priority 2, so it runs immediately after the version-gated migration and can
// catch a deploy where that migration ran against a half-uploaded plugin.
add_action('init', 'tit_verify_schema', 2);

/**
 * The pipeline's write key. A wp-config.php constant wins over the stored
 * option, so the value can be set before this plugin has ever run.
 */
function tit_get_api_key() {
    if (defined('TALENT_API_KEY') && TALENT_API_KEY) {
        return (string) TALENT_API_KEY;
    }
    return (string) get_option('tit_api_key', '');
}

/**
 * Fails CLOSED. If no key is configured server-side, every keyed request is
 * rejected — an empty stored key must never match an empty header.
 */
function tit_api_permission($request) {
    $stored = tit_get_api_key();
    if ($stored === '') {
        return new WP_Error(
            'tit_key_missing',
            'API key is not configured. Set it under Talent Intel in wp-admin, '
            . 'or define TALENT_API_KEY in wp-config.php.',
            array('status' => 503)
        );
    }
    $provided = (string) $request->get_header('X-Talent-API-Key');
    if ($provided === '' || !hash_equals($stored, $provided)) {
        return new WP_Error('tit_forbidden', 'Invalid or missing API key.', array('status' => 403));
    }
    return true;
}

function tit_register_admin_page() {
    add_menu_page(
        'Talent Intelligence',
        'Talent Intel',
        'manage_options',
        'tit-settings',
        'tit_render_admin_page',
        'dashicons-chart-line',
        31
    );
}
add_action('admin_menu', 'tit_register_admin_page');

function tit_render_admin_page() {
    if (!current_user_can('manage_options')) return;

    $notice = '';

    // Setting the key here means wp-config.php is never touched. Editing a
    // core file over FTP risks 500-ing the whole blog; a stored option cannot.
    // This form is in wp-admin, which is never page-cached, so a nonce is safe
    // here (it would not be on a cached front-end page).
    if (isset($_POST['tit_set_key']) && check_admin_referer('tit_set_key')) {
        $submitted = trim((string) wp_unslash($_POST['tit_api_key_value']));
        if ($submitted === '') {
            $notice = '<div class="notice notice-error"><p>Key was empty. Nothing changed.</p></div>';
        } elseif (strlen($submitted) < 32) {
            $notice = '<div class="notice notice-error"><p>That looks too short to be the full key. Nothing changed.</p></div>';
        } else {
            update_option('tit_api_key', $submitted, false);
            $notice = '<div class="notice notice-success"><p>Key saved. It must match the WP_API_KEY secret in the GitHub repository.</p></div>';
        }
    }

    // Shown exactly once, here, immediately after generating. The stored value
    // is never displayed again, so a key that is not copied now has to be
    // regenerated. Telling someone to "copy it" without showing it, which is
    // what the first version of this did, is a trap.
    if (isset($_POST['tit_generate_key']) && check_admin_referer('tit_set_key')) {
        $generated = bin2hex(random_bytes(32));
        update_option('tit_api_key', $generated, false);
        $notice = '<div class="notice notice-success"><p><strong>New key generated.</strong> '
                . 'Copy it now, it is not shown again. Paste it into the '
                . '<code>WP_API_KEY</code> secret in the GitHub repository, or every '
                . 'write stays rejected.</p>'
                . '<p><input type="text" readonly onclick="this.select()" '
                . 'style="width:100%;max-width:640px;font-family:monospace;padding:6px;" '
                . 'value="' . esc_attr($generated) . '"></p></div>';
    }

    global $wpdb;
    $table = tit_table_name();
    $total = (int) $wpdb->get_var("SELECT COUNT(*) FROM {$table} WHERE is_current = 1");
    $has_key = tit_get_api_key() !== '';
    $from_config = defined('TALENT_API_KEY') && TALENT_API_KEY;
    echo $notice;
    ?>
    <div class="wrap">
        <h1>Talent Intelligence Tracker</h1>
        <p><strong><?php echo esc_html(number_format_i18n($total)); ?></strong> current signals.</p>
        <p>Plugin version <?php echo esc_html(TIT_VERSION); ?>, table <code><?php echo esc_html($table); ?></code>.</p>

        <h2>Pipeline API key</h2>
        <?php if ($from_config) : ?>
            <p>A key is set in <code>wp-config.php</code>, which takes precedence
               over anything stored here.</p>
        <?php elseif ($has_key) : ?>
            <p>A key is stored. The pipeline must send the same value in the
               <code>X-Talent-API-Key</code> header, so it has to match the
               <code>WP_API_KEY</code> secret in the GitHub repository.</p>
        <?php else : ?>
            <p><strong>No key configured, so every write is rejected with 503.</strong>
               Paste the value of your <code>WP_API_KEY</code> GitHub secret below.
               Editing <code>wp-config.php</code> is not required.</p>
        <?php endif; ?>

        <form method="post" style="margin:14px 0 24px;">
            <?php wp_nonce_field('tit_set_key'); ?>
            <p>
                <label for="tit_api_key_value"><strong>Set the key</strong></label><br>
                <input type="password" id="tit_api_key_value" name="tit_api_key_value"
                       class="regular-text" autocomplete="off"
                       placeholder="paste the WP_API_KEY value">
                <button type="submit" name="tit_set_key" class="button button-primary">Save key</button>
            </p>
            <p class="description">
                Stored as an option, never written to a core file. The value is
                write-only here: it is not displayed back.
            </p>
            <p>
                <button type="submit" name="tit_generate_key" class="button"
                        onclick="return confirm('Generate a new key? The pipeline will be locked out until you copy it into the WP_API_KEY GitHub secret.');">
                    Generate a new key instead
                </button>
            </p>
        </form>

        <?php
        // The .htaccess cache-header block writes and probes itself on init.
        // This host gives no shell, so the recorded probe result is the only
        // way to find out whether it landed without curling from outside.
        $ht = get_option('tit_htaccess_state', array());
        if ($ht) : ?>
            <h2>Cache headers</h2>
            <p>.htaccess block: <strong><?php echo esc_html($ht['status'] ?? 'unknown'); ?></strong>
               <?php if (!empty($ht['reason'])) : ?>(<?php echo esc_html($ht['reason']); ?>)<?php endif; ?>
               for version <?php echo esc_html($ht['version'] ?? '?'); ?>.</p>
            <?php if (!empty($ht['cache_control'])) : ?>
                <p>Last probe saw <code><?php echo esc_html($ht['cache_control']); ?></code>.
                   Two values separated by <code>||</code> means the host's
                   duplicate <code>no-store</code> is still getting through.</p>
            <?php endif; ?>
        <?php endif; ?>

        <h2>Page</h2>
        <p>Put <code>[talent_intelligence_dashboard]</code> on the page at
           <code>/blog/talent-intelligence-tracker/</code>.</p>
    </div>
    <?php
}
