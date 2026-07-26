<?php
/**
 * Custom indexed table. NOT post meta — filtered queries over post meta stop
 * scaling within a few thousand rows, which the sibling learned the hard way.
 *
 * Table: {prefix}tit_signals. It never reads or writes the sibling's table.
 */

if (!defined('ABSPATH')) exit;

function tit_table_name() {
    global $wpdb;
    return $wpdb->prefix . TIT_TABLE_SUFFIX;
}

function tit_create_or_update_table() {
    global $wpdb;
    require_once ABSPATH . 'wp-admin/includes/upgrade.php';

    $table   = tit_table_name();
    $charset = $wpdb->get_charset_collate();

    // dbDelta is additive: it adds new columns to an existing table rather than
    // rebuilding it, so history survives a schema change.
    $sql = "CREATE TABLE {$table} (
        row_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        signal_id VARCHAR(64) NOT NULL,
        revision INT NOT NULL DEFAULT 1,
        is_current TINYINT(1) NOT NULL DEFAULT 1,
        supersedes_row_id BIGINT UNSIGNED NULL,
        headline TEXT NOT NULL,
        summary TEXT NOT NULL,
        talent_readthrough TEXT NOT NULL,
        company VARCHAR(255) NOT NULL,
        company_key VARCHAR(255) NOT NULL,
        pillar VARCHAR(40) NOT NULL,
        signal_direction VARCHAR(20) NOT NULL,
        city VARCHAR(120) NULL,
        region VARCHAR(60) NULL,
        country CHAR(2) NULL,
        hq_city VARCHAR(120) NULL,
        hq_country CHAR(2) NULL,
        confidence VARCHAR(12) NOT NULL,
        source_url TEXT NOT NULL,
        source_name VARCHAR(255) NOT NULL,
        discovery_url TEXT NULL,
        archive_url TEXT NULL,
        published_date DATE NULL,
        captured_at DATETIME NOT NULL,
        as_of DATETIME NOT NULL,
        content_hash VARCHAR(64) NOT NULL,
        predicted_outcome TEXT NULL,
        check_after_date DATE NULL,
        outcome_observed TEXT NULL,
        outcome_source_url TEXT NULL,
        outcome_checked_at DATETIME NULL,
        collector VARCHAR(60) NOT NULL,
        notes TEXT NULL,
        PRIMARY KEY (row_id),
        UNIQUE KEY uniq_hash_rev (content_hash, revision),
        KEY idx_current (is_current),
        KEY idx_geo (country, city),
        KEY idx_hq (hq_country, hq_city),
        KEY idx_pillar (pillar),
        KEY idx_published (published_date),
        KEY idx_company (company_key),
        KEY idx_signal (signal_id, revision)
    ) {$charset};";

    dbDelta($sql);
}

function tit_flush_caches() {
    global $wpdb;
    $wpdb->query(
        "DELETE FROM {$wpdb->options} WHERE option_name LIKE '_transient_tit_%'
         OR option_name LIKE '_transient_timeout_tit_%'"
    );
}

/**
 * Insert one signal. Returns 'stored' | 'duplicate' | WP_Error.
 *
 * Never an UPDATE of the facts: a correction appends a revision and the old row
 * survives with is_current = 0, so "what did we publish on date D" stays
 * answerable.
 */
function tit_insert_signal(array $row) {
    global $wpdb;
    $table = tit_table_name();

    $hash = isset($row['content_hash']) ? (string) $row['content_hash'] : '';
    if ($hash === '') {
        return new WP_Error('tit_no_hash', 'content_hash is required', array('status' => 400));
    }

    // No source URL, no record. Enforced server-side too, not only in the
    // pipeline — this endpoint is the only way in, so it is the right place.
    if (empty($row['source_url'])) {
        return new WP_Error('tit_no_source', 'source_url is required', array('status' => 400));
    }

    $existing = $wpdb->get_var(
        $wpdb->prepare("SELECT row_id FROM {$table} WHERE content_hash = %s AND is_current = 1", $hash)
    );
    if ($existing) {
        return 'duplicate';
    }

    $data = array(
        'signal_id'          => substr((string) ($row['signal_id'] ?? $hash), 0, 64),
        'revision'           => 1,
        'is_current'         => 1,
        'headline'           => (string) ($row['headline'] ?? ''),
        'summary'            => (string) ($row['summary'] ?? ''),
        'talent_readthrough' => (string) ($row['talent_readthrough'] ?? ''),
        'company'            => (string) ($row['company'] ?? ''),
        'company_key'        => (string) ($row['company_key'] ?? ''),
        'pillar'             => (string) ($row['pillar'] ?? ''),
        'signal_direction'   => (string) ($row['signal_direction'] ?? ''),
        'city'               => $row['city'] ?: null,
        'region'             => $row['region'] ?: null,
        'country'            => $row['country'] ?: null,
        'hq_city'            => $row['hq_city'] ?: null,
        'hq_country'         => $row['hq_country'] ?: null,
        'confidence'         => (string) ($row['confidence'] ?? 'reported'),
        'source_url'         => (string) $row['source_url'],
        'source_name'        => (string) ($row['source_name'] ?? ''),
        'discovery_url'      => $row['discovery_url'] ?: null,
        'published_date'     => $row['published_date'] ?: null,
        'captured_at'        => (string) ($row['captured_at'] ?? current_time('mysql', true)),
        'as_of'              => (string) ($row['as_of'] ?? current_time('mysql', true)),
        'content_hash'       => $hash,
        'predicted_outcome'  => $row['predicted_outcome'] ?: null,
        'check_after_date'   => $row['check_after_date'] ?: null,
        'collector'          => (string) ($row['collector'] ?? 'unknown'),
    );

    $ok = $wpdb->insert($table, $data);
    if ($ok === false) {
        return new WP_Error('tit_insert_failed', $wpdb->last_error, array('status' => 500));
    }

    tit_flush_caches();
    return 'stored';
}
