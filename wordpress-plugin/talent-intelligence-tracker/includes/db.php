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
    //
    // No SQL comments inside this string, ever. dbDelta parses it line by line
    // with regular expressions and reads a `-- note` line as a malformed field.
    //
    // materiality: how much an update is worth someone's attention (high /
    // medium / routine), computed deterministically in the pipeline and never
    // by a model. It is a RANKING, never a reason to drop a row: a routine
    // officer change is accurate, sits in our best-evidenced tier, and is the
    // basis of a leadership-churn dataset. It simply must not be the first
    // thing a recruiter sees. NULL means "not judged yet", and the whole
    // product treats NULL as notable rather than routine, so a row we have not
    // classified is never hidden.
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
        ticker VARCHAR(12) NULL,
        cik VARCHAR(12) NULL,
        employer_type VARCHAR(20) NULL,
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
        headcount_scope VARCHAR(16) NULL,
        funding_amount VARCHAR(32) NULL,
        funding_amount_usd BIGINT NULL,
        funding_stage VARCHAR(24) NULL,
        work_mode VARCHAR(16) NULL,
        deal_type VARCHAR(16) NULL,
        money_basis VARCHAR(24) NULL,
        site_event VARCHAR(16) NULL,
        materiality VARCHAR(12) NULL,
        confidence VARCHAR(12) NOT NULL,
        source_url TEXT NOT NULL,
        source_name VARCHAR(255) NOT NULL,
        discovery_url TEXT NULL,
        archive_url TEXT NULL,
        published_date DATE NULL,
        effective_date DATE NULL,
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
        KEY idx_cik (cik),
        KEY idx_funding_usd (funding_amount_usd),
        KEY idx_materiality (materiality),
        KEY idx_effective (effective_date),
        KEY idx_signal (signal_id, revision)
    ) {$charset};";

    dbDelta($sql);
}

/**
 * DURABLE SHORT-LIVED STATE, DELIBERATELY NOT A TRANSIENT.
 *
 * `tit_flush_caches()` below deletes every `_transient_tit_%` row, and it runs
 * on EVERY write route - four or more times a day in ordinary operation. That
 * is right for cached DATA, whose whole purpose is to be thrown away when the
 * data changes. It was catastrophic for the three things that had quietly moved
 * into the same namespace and are not caches at all:
 *
 *   - `tit_export_rl_<ip>`   the 20-exports-per-10-minutes throttle
 *   - `tit_feed_rl_<ip>`     the 60-feed-builds-per-10-minutes throttle
 *   - `tit_alert_<subject>`  the legacy three-day alert suppression
 *
 * A throttle that resets four times a day is not a throttle: the counter a
 * caller has to stay under is wiped for them, on a schedule, by our own
 * collectors. And the suppression window is what stops a persistent breakage
 * mailing the owner on every single run, so wiping it turns one alert into a
 * daily one, which is precisely how a sender gets filtered - the same defect
 * this repo keeps paying for at the other end of the same channel.
 *
 * OPTIONS, NOT RENAMED TRANSIENTS. Renaming out of the `tit_` prefix would fix
 * the LIKE-delete and nothing else: `tit_flush_caches()` also calls
 * `wp_cache_flush()`, and under a persistent object cache that drops every
 * transient regardless of its name. An option survives both, which is exactly
 * why `tit_ci_alert_state` was already built this way. Expiry is carried in the
 * row rather than in a companion `_timeout_` option, so a value can never
 * outlive its own clock.
 *
 * These are `autoload = no`: they are read on the routes that need them and
 * must never join the autoload bundle loaded on every page view.
 */
define('TIT_EPHEMERAL_PREFIX', 'tit_eph_');

function tit_ephemeral_get($name) {
    $row = get_option(TIT_EPHEMERAL_PREFIX . $name, null);
    if (!is_array($row) || !isset($row['x'], $row['v'])) return null;
    if ((int) $row['x'] <= time()) {
        // Expired. Delete on read so a key nobody writes again cannot linger.
        delete_option(TIT_EPHEMERAL_PREFIX . $name);
        return null;
    }
    return $row['v'];
}

function tit_ephemeral_set($name, $value, $ttl) {
    update_option(TIT_EPHEMERAL_PREFIX . $name,
                  array('v' => $value, 'x' => time() + max(1, (int) $ttl)), false);
}

/**
 * Drop rows whose clock has run out, so wp_options stays bounded: the throttle
 * keys are per-IP and would otherwise accumulate forever, and an unbounded
 * wp_options is a slow site, a worse bug than the one this store fixes.
 *
 * CALLED FROM tit_flush_caches(), DETERMINISTICALLY, and that is the second
 * version of this function. The first rolled a 1-in-50 die inside
 * tit_ephemeral_set(), which put a SELECT on a reader's request path with
 * probability - so it passed locally, then fataled in CI when the roll came up
 * on the PHP harness, whose stub database has no wp_options table. A cleanup
 * that runs sometimes is a cleanup you cannot test and a stack trace you cannot
 * reproduce. tit_flush_caches() already runs a DELETE against wp_options
 * several times a day, on writes rather than reads, which is exactly the right
 * place and the right cadence.
 *
 * It only ever deletes rows that have EXPIRED or are malformed. The whole point
 * of this store is surviving the flush, so a live row must come through it
 * untouched.
 *
 * Bounded per sweep, and non-fatal: a store whose cleanup can take down a page
 * is not an improvement on a transient.
 */
function tit_ephemeral_gc($limit = 200) {
    global $wpdb;
    if (!isset($wpdb) || !method_exists($wpdb, 'get_col')) return;
    $like = method_exists($wpdb, 'esc_like')
        ? $wpdb->esc_like(TIT_EPHEMERAL_PREFIX) . '%'
        : TIT_EPHEMERAL_PREFIX . '%';
    try {
        $names = $wpdb->get_col($wpdb->prepare(
            "SELECT option_name FROM {$wpdb->options} WHERE option_name LIKE %s LIMIT %d",
            $like, (int) $limit));
    } catch (Exception $e) {
        return;
    } catch (Error $e) {
        return;
    }
    foreach ((array) $names as $option_name) {
        $row = get_option($option_name, null);
        if (!is_array($row) || !isset($row['x']) || (int) $row['x'] <= time()) {
            delete_option($option_name);
        }
    }
}

function tit_flush_caches() {
    global $wpdb;
    // `_transient_tit_%` ONLY, and that is a boundary, not an implementation
    // detail. Anything that must survive a write lives under
    // TIT_EPHEMERAL_PREFIX as an option and is untouched here by construction.
    $wpdb->query(
        "DELETE FROM {$wpdb->options} WHERE option_name LIKE '_transient_tit_%'
         OR option_name LIKE '_transient_timeout_tit_%'"
    );

    // ...and while we are already deleting from wp_options, drop the durable
    // rows whose clock has run out. EXPIRED ONLY - a live one surviving this
    // call is the entire purpose of the store. This is a write path, so the
    // sweep never lands on a reader's request.
    tit_ephemeral_gc();

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
        // !empty() rather than the ?: shorthand used above: these columns
        // arrive only from a pipeline new enough to send them, and an older
        // caller (or a replayed payload) must not raise an undefined-index
        // notice on every row of a 25-row batch.
        'ticker'             => !empty($row['ticker']) ? substr((string) $row['ticker'], 0, 12) : null,
        'cik'                => !empty($row['cik']) ? substr((string) $row['cik'], 0, 12) : null,
        'employer_type'      => !empty($row['employer_type']) ? (string) $row['employer_type'] : null,
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
        'headcount_scope'    => !empty($row['headcount_scope']) ? (string) $row['headcount_scope'] : null,
        'funding_amount'     => $row['funding_amount'] ?: null,
        // Parsed in Python from funding_amount, never re-derived here: one
        // parser, one answer. NULL means the string was not a US dollar figure
        // we could read, which is different from a round of zero.
        'funding_amount_usd' => !empty($row['funding_amount_usd']) ? (int) $row['funding_amount_usd'] : null,
        'funding_stage'      => !empty($row['funding_stage']) ? (string) $row['funding_stage'] : null,
        'work_mode'          => !empty($row['work_mode']) ? (string) $row['work_mode'] : null,
        // The corporate event, from the row employer's side of it: 'acquisition'
        // is buying, 'acquired' is being bought.
        'deal_type'          => !empty($row['deal_type']) ? (string) $row['deal_type'] : null,
        // WHY THE FIGURE IS, OR IS NOT, MONEY THIS EMPLOYER RAISED. Decided in
        // Python by pipeline/money_raised.py and never re-derived here, for
        // exactly the reason funding_amount_usd is not: one definition, one
        // answer. NULL means the row was never examined and is a third state,
        // not a quiet 'company_raise' - every money sum on this site asks for
        // that value by name, so an unexamined row cannot reach a total.
        'money_basis'        => !empty($row['money_basis']) ? (string) $row['money_basis'] : null,
        // What the employer did with a place of work. An event type, never
        // a headcount claim: 'opened' does not mean the row is hiring.
        'site_event'         => !empty($row['site_event']) ? (string) $row['site_event'] : null,
        // Absent from an older pipeline's payload, and NULL is the right answer
        // when it is: "we have not judged this" is not "this is routine".
        'materiality'        => !empty($row['materiality']) ? (string) $row['materiality'] : null,
        'confidence'         => (string) ($row['confidence'] ?? 'reported'),
        'source_url'         => (string) $row['source_url'],
        'source_name'        => (string) ($row['source_name'] ?? ''),
        'discovery_url'      => $row['discovery_url'] ?: null,
        // archive_url is deliberately absent: a row's Wayback snapshot is
        // captured after ingest, so it is always empty at insert time and
        // arrives later through /enrich instead.
        'published_date'     => $row['published_date'] ?: null,
        'effective_date'     => !empty($row['effective_date']) ? (string) $row['effective_date'] : null,
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
