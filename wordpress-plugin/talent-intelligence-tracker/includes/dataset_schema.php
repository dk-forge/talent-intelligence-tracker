<?php
/**
 * schema.org Dataset for the hub, and the "primary source" share.
 *
 * Both are computed from the dashboard's own facts bundle (tit_dashboard_facts),
 * so they describe exactly the set the hero describes and cost no query.
 *
 * THE SHARE IS A FLOOR. Its numerator is confidence = 'verified', which
 * pipeline/validate.py only permits when the source host is in
 * PRIMARY_SOURCE_DOMAINS. A first-party release we rate `reported` is not
 * counted, so the copy says "at least" and the figure is floored, never rounded
 * up. No corpus, no claim: an empty table prints nothing.
 *
 * Guard: tests/test_dataset_schema.py.
 */
if (!defined('ABSPATH')) exit;

/** PURE. Whole percent, floored; null when there is no denominator. */
function tit_primary_source_share($verified, $total) {
    $total = (int) $total;
    if ($total <= 0) return null;
    return (int) floor(100 * max(0, min((int) $verified, $total)) / $total);
}

/** PURE. The schema.org Dataset node for the hub. */
function tit_dataset_node(array $facts) {
    $total = (int) ($facts['notable'] ?? 0);
    $share = tit_primary_source_share($facts['verified'] ?? 0, $total);
    $desc = 'Hiring, funding, leadership and pay signals from employers, each '
          . 'record linked to the document behind it.';
    if ($share !== null) {
        $desc .= sprintf(' At least %d%% of %s records link a primary source '
                       . '(a filing, register entry or official announcement).',
                         $share, number_format($total));
    }
    $hub = home_url('/talent-intelligence-tracker/');
    $node = array(
        '@context'    => 'https://schema.org',
        '@type'       => 'Dataset',
        'name'        => 'Talent Intelligence Tracker',
        'description' => $desc,
        'url'         => $hub,
        'isAccessibleForFree' => true,
        'license'     => 'https://creativecommons.org/licenses/by/4.0/',
        'creator'     => array('@type' => 'Organization', 'name' => 'Ask The Recruiter',
                               'url' => 'https://asktherecruiter.com/'),
        'keywords'    => array('hiring', 'talent market', 'funding', 'leadership changes'),
        'spatialCoverage' => array('@type' => 'Place', 'name' => 'Worldwide'),
        'distribution' => array(array(
            '@type' => 'DataDownload',
            'encodingFormat' => 'text/csv',
            'contentUrl' => admin_url('admin-post.php?action=tit_export_csv'),
        )),
    );
    $lo = substr((string) ($facts['span_lo'] ?? ''), 0, 10);
    $hi = substr((string) ($facts['span_hi'] ?? ''), 0, 10);
    if ($lo !== '' && $hi !== '') $node['temporalCoverage'] = $lo . '/' . $hi;
    $mod = substr((string) ($facts['newest_run'] ?? ''), 0, 10);
    if ($mod !== '') $node['dateModified'] = $mod;
    return $node;
}

/** The node as a script tag. JSON_HEX_TAG keeps a stray "</script>" inert. */
function tit_dataset_jsonld_html(array $facts) {
    return '<script type="application/ld+json">'
         . wp_json_encode(tit_dataset_node($facts), JSON_HEX_TAG | JSON_UNESCAPED_SLASHES)
         . '</script>';
}

/** The visible line under the hero. Empty when there is no denominator. */
function tit_primary_source_line_html(array $facts) {
    $total = (int) ($facts['notable'] ?? 0);
    $share = tit_primary_source_share($facts['verified'] ?? 0, $total);
    if ($share === null) return '';
    return '<span class="tit-fine-say" data-testid="tit-primary-share">'
         . esc_html(sprintf('At least %d%% of the %s tracked records link a primary source: '
                          . 'a filing, register entry or official announcement. '
                          . 'The rest link the news report.',
                            $share, number_format_i18n($total)))
         . '</span>';
}
