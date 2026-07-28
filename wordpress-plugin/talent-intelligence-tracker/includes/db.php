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
        state CHAR(2) NULL,
        functions TEXT NULL,
        industry VARCHAR(40) NULL,
        headcount INT NULL,
        funding_amount VARCHAR(32) NULL,
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
        KEY idx_state (state),
        KEY idx_industry (industry),
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

    // Clearing our transients refreshes the DATA while leaving the previously
    // generated HTML sitting in front of it. This host runs a page cache, and
    // crawlers and first-time visitors request the bare URL, so unlike our own
    // checks they never carry a cache-busting query string and keep receiving
    // the old page. That is why deploys here have repeatedly looked like they
    // did not land: on 2026-07-28 the assets were verified present on the
    // server while the rendered page still ran the previous version's PHP. The
    // sibling plugin hit exactly this and purges the page cache too.
    if (function_exists('wp_cache_clear_cache')) wp_cache_clear_cache(); // WP Super Cache
    if (function_exists('w3tc_flush_all'))       w3tc_flush_all();
    if (has_action('litespeed_purge_all'))       do_action('litespeed_purge_all');
    do_action('nfd_purge_all');                                          // Bluehost/Newfold
    if (function_exists('wp_cache_flush'))       wp_cache_flush();       // object cache
    // Autoptimize is deliberately NOT cleared: its filenames are content
    // hashes, so a changed asset already gets a new aggregate, and deleting the
    // old files only opens a window where in-flight HTML points at a 410.
}

/**
 * Insert one signal. Returns 'stored' | 'duplicate' | 'retracted' | WP_Error.
 *
 * Never an UPDATE of the facts: a correction appends a revision and the old row
 * survives with is_current = 0, so "what did we publish on date D" stays
 * answerable.
 *
 * $flush lets /bulk defer the cache flush: flushing per inserted row inside a
 * batch loop purged the page cache dozens of times per run for one visible
 * change. The bulk endpoint flushes ONCE after its loop instead.
 */
function tit_insert_signal(array $row, $flush = true) {
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

    // Dedup against ANY revision, not only is_current = 1 — mirroring the
    // pipeline's exact_duplicate. Checking only current rows meant a hash whose
    // row had been retracted WP-side (is_current = 0) passed this check, hit
    // the uniq_hash_rev unique key on insert, and came back as a WP_Error 500.
    // The pipeline re-collects the same story every run, so one retracted row
    // failed the /bulk batch and exited 1 every 12 hours, forever. If the hash
    // exists at any revision the insert must never be attempted: report
    // 'retracted' when the newest revision was withdrawn, 'duplicate' otherwise.
    $existing = $wpdb->get_row(
        $wpdb->prepare(
            "SELECT is_current FROM {$table} WHERE content_hash = %s
             ORDER BY revision DESC LIMIT 1",
            $hash
        ),
        ARRAY_A
    );
    if ($existing) {
        // A superseded revision always has a newer current one above it, so a
        // newest revision at is_current = 0 can only mean a retraction.
        return ((int) $existing['is_current'] === 0) ? 'retracted' : 'duplicate';
    }

    // Second, cheap guard behind the exact-hash check. content_hash arrives
    // from the pipeline and has proven nondeterministic across runs: the model
    // echoes the headline slightly differently, so the SAME story lands twice
    // with two hashes (observed live: same outlet, same story, two hashes).
    // Same employer + same pillar + same direction within two weeks of the
    // same published date is the same event for our purposes.
    if (!empty($row['company_key']) && !empty($row['pillar'])
        && !empty($row['signal_direction']) && !empty($row['published_date'])) {
        $near = $wpdb->get_var($wpdb->prepare(
            "SELECT row_id FROM {$table}
              WHERE is_current = 1 AND company_key = %s AND pillar = %s
                AND signal_direction = %s AND published_date IS NOT NULL
                AND published_date BETWEEN DATE_SUB(%s, INTERVAL 14 DAY)
                                       AND DATE_ADD(%s, INTERVAL 14 DAY)
              LIMIT 1",
            (string) $row['company_key'], (string) $row['pillar'],
            (string) $row['signal_direction'],
            (string) $row['published_date'], (string) $row['published_date']
        ));
        if ($near) {
            return 'duplicate';
        }
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
        'state'              => $row['state'] ?: null,
        'functions'          => $row['functions'] ?: null,
        'industry'           => $row['industry'] ?: null,
        'headcount'          => isset($row['headcount']) && $row['headcount'] ? (int) $row['headcount'] : null,
        'funding_amount'     => $row['funding_amount'] ?: null,
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

    if ($flush) {
        tit_flush_caches();
    }
    return 'stored';
}
