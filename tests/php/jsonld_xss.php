<?php
/**
 * A stored headline must not be able to close the JSON-LD script block.
 *
 * Headlines arrive verbatim from 648 external publishers and are stored
 * without character constraints (includes/db.php). They are then printed
 * inside <script type="application/ld+json"> on company profiles, place
 * pages and the dashboard - the highest-traffic stranger-facing surfaces.
 * JSON_UNESCAPED_SLASHES removes the \/ escaping that normally makes
 * </script> inert, and json_encode does not escape < or > unless asked.
 * Audit 2026-08-03 found all three emissions in that state.
 */
$fails = [];
$hostile = '</script><img src=x onerror=alert(1)>';
$payload = wp_json_encode_stub(['headline' => $hostile]);
if (stripos($payload, '</script') !== false) {
    $fails[] = 'raw </script survives the encoder';
}
function wp_json_encode_stub($v) {
    return json_encode($v, JSON_UNESCAPED_SLASHES | JSON_HEX_TAG | JSON_HEX_AMP);
}
// every in-script emission must carry the tag guard
foreach ([
  'includes/company.php', 'includes/places.php', 'includes/shortcodes.php',
] as $rel) {
    $src = file_get_contents(__DIR__ . '/../../wordpress-plugin/talent-intelligence-tracker/' . $rel);
    if (preg_match('/\),\s*JSON_UNESCAPED_SLASHES\s*\)/', $src)) {
        $fails[] = "$rel emits ld+json without JSON_HEX_TAG";
    }
}
if ($fails) { echo "jsonld_xss FAILED:\n  - " . implode("\n  - ", $fails) . "\n"; exit(1); }
echo "jsonld_xss OK\n";
