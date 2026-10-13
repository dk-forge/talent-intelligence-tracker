<?php
/**
 * GLEIF Legal Entity Identifiers on company profiles.
 *
 * data/company_lei.json is written by gleif_lei.py (weekly, gleif-lei.yml)
 * from the GLEIF golden copy, CC0 1.0. An employer is listed only on a UNIQUE
 * normalised-name match whose legal-address country equals the employer's one
 * known country; anything ambiguous is absent, so absence means "not
 * asserted", never "has no LEI".
 *
 * Every function is guarded by its callers with function_exists(): an FTP
 * deploy can land company.php before this file.
 */
if (!defined('ABSPATH')) exit;

/** company_key -> LEI, read once per request. Empty on a missing or bad file. */
function tit_company_lei_map() {
    static $map = null;
    if ($map !== null) return $map;
    $map = array();
    $file = TIT_PATH . 'data/company_lei.json';
    if (!is_readable($file)) return $map;
    $doc = json_decode((string) file_get_contents($file), true);
    if (is_array($doc) && isset($doc['companies']) && is_array($doc['companies'])) {
        $map = $doc['companies'];
    }
    return $map;
}

/** The LEI for a company_key, or '' when none was asserted. PURE given the map. */
function tit_company_lei($company_key, $map = null) {
    $map = $map === null ? tit_company_lei_map() : $map;
    $lei = isset($map[$company_key]) ? (string) $map[$company_key] : '';
    // ISO 17442: 20 characters, 18 alphanumerics then two check digits.
    return preg_match('/^[A-Z0-9]{18}[0-9]{2}$/', $lei) ? $lei : '';
}

function tit_company_lei_url($lei) {
    return 'https://search.gleif.org/#/record/' . rawurlencode($lei);
}

/** Organization node fields for an LEI: schema.org leiCode plus a PropertyValue. */
function tit_company_lei_jsonld($lei) {
    if ($lei === '') return array();
    return array(
        'leiCode'    => $lei,
        'identifier' => array('@type' => 'PropertyValue', 'propertyID' => 'LEI',
                              'value' => $lei, 'url' => tit_company_lei_url($lei)),
    );
}

/** The visible line, with the GLEIF attribution. '' when there is no LEI. */
function tit_company_lei_html($lei) {
    if ($lei === '') return '';
    return '<p class="tit-note tit-lei">LEI: <a href="' . esc_url(tit_company_lei_url($lei))
         . '" rel="noopener">' . esc_html($lei) . '</a>'
         . ' <span class="tit-lei-source">(Legal Entity Identifier from the'
         . ' <a href="https://www.gleif.org/en/lei-data/gleif-golden-copy" rel="noopener">GLEIF Golden Copy</a>,'
         . ' CC0; matched on legal name and country)</span></p>';
}
