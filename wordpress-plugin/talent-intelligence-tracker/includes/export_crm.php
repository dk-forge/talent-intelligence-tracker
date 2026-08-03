<?php
/**
 * CRM-shaped CSV downloads: the SAME rows as the plain CSV export, with the
 * headers each CRM's import wizard maps automatically.
 *
 * Same machinery as export.php on purpose: tit_export_walk streams the same
 * filtered set (tit_build_where, detail excluded), tit_csv_guard neutralises
 * formula injection, tit_export_throttle applies the same per-IP cap. The only
 * thing that changes is the header row and which columns land under it.
 *
 * HEADER MAPPINGS, with the vendor's own documentation:
 *
 * HubSpot company imports match columns to properties by header name.
 *   https://knowledge.hubspot.com/import-and-export/set-up-your-import-file
 *   https://knowledge.hubspot.com/import-and-export/import-objects
 *   Recognised headers used here: "Company name", "Company domain name",
 *   "City", "State/Region", "Country/Region", "Industry", "Description".
 *
 * Salesforce's Data Import Wizard auto-maps Account fields by header label.
 *   https://help.salesforce.com/s/articleView?id=platform.import_with_data_import_wizard.htm
 *   https://help.salesforce.com/s/articleView?id=platform.field_mapping_for_other_data_sources_and_organization_import.htm
 *   Recognised headers used here: "Account Name", "Website", "Billing City",
 *   "Billing State/Province", "Billing Country", "Industry", "Description".
 *
 * "Company domain name" / "Website" are emitted but left EMPTY: this dataset
 * holds no company websites, and the source_url is the publisher's article,
 * not the employer's site. Writing the publisher's domain into a CRM dedupe
 * key would be invented data with consequences. The column is present so the
 * wizard shows the mapping and the user can fill it from their own enrichment.
 *
 * Signal fields that have no standard CRM property (date, direction, evidence,
 * source link) ride along as extra columns; both wizards let them be mapped to
 * custom properties or skipped.
 */

if (!defined('ABSPATH')) exit;

add_action('admin_post_tit_export_hubspot', 'tit_export_hubspot');
add_action('admin_post_nopriv_tit_export_hubspot', 'tit_export_hubspot');
add_action('admin_post_tit_export_salesforce', 'tit_export_salesforce');
add_action('admin_post_nopriv_tit_export_salesforce', 'tit_export_salesforce');

/** The one row shape both presets share; headers differ, values do not. */
function tit_crm_row($row) {
    $place_city = $row->city ?: $row->hq_city;
    $country    = $row->country ?: $row->hq_country;
    $desc = trim((string) $row->headline);
    $rt = trim((string) $row->talent_readthrough);
    if ($rt !== '') $desc .= ($desc === '' ? '' : ' ') . $rt;
    return array(
        'name'        => (string) $row->company,
        'website'     => '', // no invented data: we do not hold company websites
        'city'        => (string) $place_city,
        'state'       => (string) $row->state,
        'country'     => (string) $country,
        'industry'    => (string) $row->industry,
        'description' => $desc,
        'signal_date' => $row->published_date ?: substr((string) $row->captured_at, 0, 10),
        'direction'   => (string) $row->signal_direction,
        'evidence'    => (string) $row->confidence,
        'source_name' => (string) $row->source_name,
        'source_url'  => (string) $row->source_url,
    );
}

function tit_crm_headers($preset) {
    if ($preset === 'hubspot') {
        return array(
            'Company name', 'Company domain name', 'City', 'State/Region',
            'Country/Region', 'Industry', 'Description',
            'Signal Date', 'Signal Direction', 'Evidence',
            'Source Name', 'Source URL',
        );
    }
    return array(
        'Account Name', 'Website', 'Billing City', 'Billing State/Province',
        'Billing Country', 'Industry', 'Description',
        'Signal Date', 'Signal Direction', 'Evidence',
        'Source Name', 'Source URL',
    );
}

/**
 * The body of the download, onto any stream. Split from the HTTP wrapper so
 * the harness can run the real header row and the real value mapping against
 * real rows without exit() ending the test process.
 */
function tit_export_crm_stream($preset, $out) {
    // UTF-8 BOM: same reason as the plain CSV, Excel and both import wizards
    // read accents and CJK correctly.
    fwrite($out, "\xEF\xBB\xBF");
    fputcsv($out, tit_crm_headers($preset), ',', '"', '\\');

    tit_export_walk(function ($row) use ($out) {
        $r = tit_crm_row($row);
        fputcsv($out, array(
            tit_csv_guard($r['name']),
            $r['website'],
            tit_csv_guard($r['city']),
            tit_csv_guard($r['state']),
            tit_csv_guard($r['country']),
            tit_csv_guard($r['industry']),
            tit_csv_guard($r['description']),
            $r['signal_date'],
            tit_csv_guard($r['direction']),
            tit_csv_guard($r['evidence']),
            tit_csv_guard($r['source_name']),
            tit_csv_guard($r['source_url']),
        ), ',', '"', '\\');
    });
}

function tit_export_crm($preset) {
    if (!function_exists('tit_export_ready') || !function_exists('tit_export_walk')) {
        // export.php can be mid-upload for a few seconds on an FTP deploy.
        status_header(503);
        nocache_headers();
        header('Content-Type: text/plain; charset=utf-8');
        echo 'Export is briefly unavailable while an update lands. Try again in a minute.';
        exit;
    }
    tit_export_ready();
    tit_export_throttle();
    nocache_headers();
    header('Content-Type: text/csv; charset=utf-8');
    header('Content-Disposition: attachment; filename="talent-intelligence-tracker-'
        . $preset . '-' . (tit_export_is_filtered() ? 'filtered-' : '')
        . gmdate('Y-m-d') . '.csv"');

    $out = fopen('php://output', 'w');
    tit_export_crm_stream($preset, $out);
    fclose($out);
    exit;
}

function tit_export_hubspot() { tit_export_crm('hubspot'); }
function tit_export_salesforce() { tit_export_crm('salesforce'); }
