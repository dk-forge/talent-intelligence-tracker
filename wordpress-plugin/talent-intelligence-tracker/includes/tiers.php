<?php
/**
 * Public trust tiers: verified / reported / measured.
 *
 * The PHP twin of pipeline/tiers.py (docs/VERIFICATION_TIERS.md). Derived at
 * read time from stored columns; nothing stored changes.
 *   measured  our reading of a job board (collector ats_boards, or a source
 *             named "... job board"). Never an announced hiring event.
 *   verified  confidence 'verified'. validate.infer_confidence only lets a
 *             PRIMARY_SOURCE_DOMAINS host store that, so news cannot reach it.
 *   reported  everything else; a stored 'rumored' row carries rumored=true.
 */

if (!defined('ABSPATH')) exit;

/** @return array{tier:string,rumored:bool} */
function tit_tier($confidence, $collector, $source_name) {
    if ((string) $collector === 'ats_boards'
        || preg_match('/\bjob board\b/i', (string) $source_name)) {
        return array('tier' => 'measured', 'rumored' => false);
    }
    $conf = strtolower(trim((string) $confidence));
    if ($conf === 'verified') {
        return array('tier' => 'verified', 'rumored' => false);
    }
    return array('tier' => 'reported', 'rumored' => $conf === 'rumored');
}

/** Same as tit_tier() over a row object or array. */
function tit_tier_of_row($row) {
    $r = (array) $row;
    return tit_tier($r['confidence'] ?? '', $r['collector'] ?? '', $r['source_name'] ?? '');
}
