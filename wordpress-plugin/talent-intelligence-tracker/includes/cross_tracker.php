<?php
/**
 * THE ONE SIGNAL NOBODY ELSE CAN PRODUCE, AND WHY IT IS SWITCHED OFF.
 *
 * The owner runs both trackers. An employer cutting in one place while hiring in
 * another is a claim no competitor holding half the data can make, and this file
 * is the plumbing for it: fetch the sibling's PUBLIC API over HTTP at render
 * time, cache the answer, pair it conservatively with what this tracker holds,
 * and render the pairs that survive.
 *
 * IT SHIPS DISABLED (tit_cross_tracker_enabled() is false by default) BECAUSE
 * THE PAIRS DO NOT SURVIVE. That is a measurement, not a hedge, and it is the
 * whole content of this comment. Measured 2026-07-30 against the live sibling
 * API and the 15,711 current rows here:
 *
 *   our employers                                    7,377
 *   sibling company names on /layoffs/v1/companies  20,000, folding to
 *                                                   18,648 keys under our own
 *                                                   pipeline/vocab.company_key
 *   keys present in BOTH                               559
 *   ...of those, ones we hold a HIRING-direction row for  6
 *
 * Six. And reading those six is what settles it:
 *
 *   us bank        the sibling's own ?company=US Bank returns PIRAEUS BANK for
 *                  three of its four most recent rows. Rendering that pair
 *                  publishes "US Bank cut 200 jobs in Greece" about a real
 *                  company that did no such thing.
 *   tesla          matches "TRIGO (Tesla)", a supplier working on Tesla's site.
 *   saint-gobain   matches "Saint-Gobain Sekurit" and "Saint-Gobain Cristaleria",
 *                  subsidiaries, which may or may not be the parent's decision.
 *   infosys        cuts dated 2024 and 2025 against a July 2026 pay-rise story.
 *   southstate     one cut, 2025-05-31, against a July 2026 hiring plan.
 *   hsbc           the only near-defensible pair: 20,000 cut in the United
 *                  Kingdom on 2026-03-19 against a July 2026 plan to hire 200 in
 *                  wealth management. Four months apart, and the hiring row's
 *                  own geography is wrong in our database (city London, country
 *                  SG), so even the good pair would print a wrong place.
 *
 * So the count of pairs this would publish today, under rules a person would
 * defend out loud, is ZERO, and the count it would publish under loose rules
 * includes a fabricated claim about a named bank. A false pairing cannot be
 * taken back, and one is worse than the feature is good.
 *
 * WHAT WOULD MAKE IT SHIPPABLE, in the order that matters:
 *
 *  1. AN EMPLOYER IDENTITY THAT SPANS BOTH SIDES. Not a name match. Both
 *     trackers would need to agree on a ticker, a CIK, or an LEI, and this side
 *     already stores `ticker` and `cik` on rows that have them. A pair backed by
 *     a shared ticker is checkable; a pair backed by a normalised string is a
 *     guess wearing a database's clothes. This is the whole of the work.
 *  2. A SUBSIDIARY RULE, decided rather than inferred. "Saint-Gobain Sekurit
 *     cut 160 in Spain" is either Saint-Gobain's decision or it is not, and only
 *     the owner can say which this product asserts.
 *  3. RECENCY ON BOTH SIDES. The signal is concurrency. A window (180 days is
 *     the obvious candidate) has to bind BOTH the cut and the hire, and today
 *     that window leaves one pair.
 *  4. MORE HIRING ROWS. 49 of 15,711 current rows carry signal_direction
 *     'hiring'. The pairing cannot be denser than that column, so this feature
 *     is downstream of hiring-signal coverage rather than of anything here.
 *
 * WHAT THIS FILE DOES NOT DO, and must never start doing: it does not import a
 * file from the sibling, join its database, or copy a Python module. Layoffs are
 * not collected here. This reads the sibling's public HTTP API exactly as any
 * other consumer would, and treats it as untrusted: slow, absent, or changed
 * without notice. Every failure hides the section rather than delaying the page.
 */

if (!defined('ABSPATH')) exit;

/** The sibling's public namespace, on the same host. Never a bare domain. */
const TIT_SIBLING_API = 'https://asktherecruiter.com/blog/wp-json/layoffs/v1/';

/**
 * Short. This runs inside a page render, so the budget is what a reader will
 * wait for, not what the API might eventually manage. Shared hosting 500s under
 * load, so one retry, and then the section is simply not there.
 */
const TIT_SIBLING_TIMEOUT = 4;
const TIT_SIBLING_RETRIES = 1;

/**
 * OFF, and it takes a deliberate act to change that.
 *
 * An option rather than a constant so the owner can turn it on without a deploy
 * once the identity work above is done, and a filter beside it so a staging site
 * can try it. Both default to false: a feature whose failure mode is publishing
 * a false claim about a named employer does not get a default-on switch.
 */
function tit_cross_tracker_enabled() {
    $on = get_option('tit_cross_tracker', '') === '1';
    return (bool) apply_filters('tit_cross_tracker_enabled', $on);
}

/**
 * GET a path on the sibling API, cached, or null.
 *
 * Cached under the same `tit_` prefix every other transient here uses, so
 * tit_flush_caches() clears it with the rest, and keyed on TIT_VERSION so a
 * deploy cannot serve a shape the new code does not expect. TTL matches
 * TIT_CACHE_TTL: this is somebody else's data and five minutes stale is the
 * correct amount of stale.
 *
 * A browser-ish User-Agent, because ModSecurity on this host rejects the default
 * one outright and answers a "Not Acceptable!" HTML page where JSON was expected
 * -- which parses as a failure here, correctly, but silently, and this comment
 * is the only thing that would tell the next person why.
 */
function tit_sibling_get($path) {
    $ttl = defined('TIT_CACHE_TTL') ? TIT_CACHE_TTL : 300;
    $key = 'tit_sib_' . md5(TIT_VERSION . '|' . $path);

    $cached = get_transient($key);
    if ($cached !== false) return $cached === 'MISS' ? null : $cached;

    if (!function_exists('wp_remote_get')) return null;

    $data = null;
    for ($attempt = 0; $attempt <= TIT_SIBLING_RETRIES; $attempt++) {
        $response = wp_remote_get(TIT_SIBLING_API . ltrim($path, '/'), array(
            'timeout'     => TIT_SIBLING_TIMEOUT,
            'redirection' => 2,
            'user-agent'  => 'TalentIntel/1.0 (+https://asktherecruiter.com)',
            'headers'     => array('Accept' => 'application/json'),
        ));
        if (function_exists('is_wp_error') && is_wp_error($response)) continue;
        $code = (int) wp_remote_retrieve_response_code($response);
        // Only a transient 5xx is worth a second attempt. A 404 means the route
        // moved, and asking twice will not move it back.
        if ($code >= 500) continue;
        if ($code !== 200) break;
        $decoded = json_decode(wp_remote_retrieve_body($response), true);
        if (is_array($decoded)) { $data = $decoded; }
        break;
    }

    // A miss is cached too, at a shorter life. Without this, a sibling that is
    // down turns every single page render into a four-second wait.
    set_transient($key, $data === null ? 'MISS' : $data,
                  $data === null ? min(60, $ttl) : $ttl);
    return $data;
}

/**
 * The rules a pair has to satisfy, in one place so they can be argued with.
 *
 * Every one of these exists because of a specific wrong pair in the measurement
 * at the top of this file. They are deliberately stricter than they need to be
 * for the six employers we have: the cost of a missed pair is an absent section,
 * and the cost of a wrong one is a false claim about a named company.
 */
const TIT_PAIR_WINDOW_DAYS = 180;   // both sides, because the claim is concurrency
const TIT_PAIR_MIN_JOBS    = 25;    // a handful of WARN rows is not "cutting"

/**
 * Does this sibling row describe the SAME employer, or a different one?
 *
 * Name equality after our own normalisation is the only identity available
 * across the two systems today, and it is not enough. `?company=US Bank`
 * answered with Piraeus Bank, so the sibling's own matching is looser than ours
 * and the answer has to be re-checked here rather than trusted.
 *
 * Returns false for anything that is not an exact key match: a subsidiary, a
 * contractor on the employer's site, or a substring hit. Those are real
 * relationships and this is not the file that gets to decide what they mean.
 */
function tit_pair_is_same_employer($our_key, $their_name) {
    $theirs = tit_company_key_like($their_name);
    return $theirs !== '' && $theirs === $our_key;
}

/**
 * A PHP echo of pipeline/vocab.company_key, for comparison only.
 *
 * It normalises case, punctuation and the commonest legal suffixes, and that is
 * all. It is NOT authoritative and nothing is ever stored from it: the Python
 * function is the identity this product has, and a second implementation of it
 * would drift. This one only ever answers "could these two strings be the same
 * employer", and a disagreement between the two costs a missing pair rather than
 * a wrong row.
 */
function tit_company_key_like($name) {
    $k = strtolower(trim((string) $name));
    if (function_exists('remove_accents')) $k = remove_accents($k);
    $k = str_replace('&', ' and ', $k);
    $k = preg_replace('/[^a-z0-9]+/', ' ', $k);
    $k = preg_replace(
        '/\b(inc|incorporated|corp|corporation|co|company|ltd|limited|llc|llp|lp|plc'
        . '|pbc|gmbh|ag|sa|nv|bv|ab|as|oy|spa|srl|pty|holdings|group)\b/', ' ', $k);
    return trim(preg_replace('/\s+/', ' ', $k));
}

/**
 * Cuts the sibling holds for one employer, filtered to what this page may claim.
 *
 * Never more than one request per employer, and the caller decides how many
 * employers are worth asking about. Returns an empty array on any failure, which
 * is the same thing this function returns for "no cuts": the section renders
 * nothing either way, and a reader is never shown a difference between "we
 * asked and there were none" and "we could not ask".
 */
function tit_sibling_cuts($our_key, $company_name) {
    $data = tit_sibling_get('query?' . http_build_query(array(
        'company' => $company_name, 'per_page' => 10,
    )));
    if (!is_array($data) || empty($data['data']) || !is_array($data['data'])) {
        return array();
    }

    $cutoff = time() - (TIT_PAIR_WINDOW_DAYS * DAY_IN_SECONDS);
    $out = array();
    foreach ($data['data'] as $row) {
        if (!is_array($row)) continue;
        // The sibling's matching is looser than ours. Re-check every row.
        if (!tit_pair_is_same_employer($our_key, $row['company_name'] ?? '')) continue;
        $when = strtotime((string) ($row['layoff_date'] ?? '') . ' 00:00:00 UTC');
        if (!$when || $when < $cutoff) continue;
        if ((int) ($row['job_count'] ?? 0) < TIT_PAIR_MIN_JOBS) continue;
        // No source document, no claim. Same rule as every row on this tracker.
        if (empty($row['source_url'])) continue;
        $out[] = array(
            'jobs'    => (int) $row['job_count'],
            'country' => (string) ($row['country'] ?? ''),
            'state'   => (string) ($row['state'] ?? ''),
            'date'    => (string) $row['layoff_date'],
            'url'     => (string) $row['source_url'],
            'source'  => (string) ($row['source_name'] ?? ''),
        );
    }
    return $out;
}

/**
 * The section, or nothing at all.
 *
 * Costs ZERO database queries: the hiring rows come from the dashboard bundle
 * the caller has already computed, and everything else is HTTP against a cached
 * transient. That is not an accident of the current code, it is the constraint —
 * tests/php/render_dashboard.php asserts the cold render at TIT_DASH_QUERY_BUDGET
 * and the warm one at zero, and this must not move either.
 */
function tit_cross_tracker_html(array $hiring_rows) {
    if (!tit_cross_tracker_enabled()) return '';

    $pairs = array();
    foreach ($hiring_rows as $row) {
        if (count($pairs) >= 3) break;   // a page section, not a report
        $key  = (string) ($row['company_key'] ?? '');
        $name = (string) ($row['company'] ?? '');
        if ($key === '' || $name === '') continue;
        $where_hiring = trim(($row['city'] ?: $row['hq_city']) . '');
        if ($where_hiring === '') continue;   // "hiring somewhere" is not the signal

        foreach (tit_sibling_cuts($key, $name) as $cut) {
            $where_cutting = trim($cut['state'] . ' ' . $cut['country']);
            // Same place on both sides is not the story, and is usually one
            // reorganisation described twice.
            if ($where_cutting === '' || $where_cutting === $where_hiring) continue;
            $pairs[] = array('row' => $row, 'cut' => $cut,
                             'hiring_in' => $where_hiring, 'cutting_in' => $where_cutting);
            break;
        }
    }
    if (!$pairs) return '';

    ob_start();
    ?>
    <div class="tit-sec tit-cross">
      <h3>Cutting In One Place, Hiring In Another</h3>
      <p class="tit-note">Paired from two separately sourced trackers, on an
         exact employer-name match only. The cut is from the
         <a href="https://asktherecruiter.com/blog/ai-layoff-tracker/">AI Layoff
         Tracker</a> and the hire is from this one, each linked to its own
         document. Both sides fall inside
         <?php echo esc_html(number_format_i18n(TIT_PAIR_WINDOW_DAYS)); ?> days.
         A subsidiary or a contractor on an employer's site is not treated as the
         employer, so pairs are missing rather than assumed.</p>
      <ul class="tit-cross-list">
      <?php foreach ($pairs as $p) : ?>
        <li>
          <span class="tit-cross-co"><?php echo esc_html($p['row']['company']); ?></span>
          <span class="tit-cross-cut">cut
            <?php echo esc_html(number_format_i18n($p['cut']['jobs'])); ?> in
            <?php echo esc_html($p['cutting_in']); ?>
            (<a href="<?php echo esc_url($p['cut']['url']); ?>" rel="nofollow noopener"
                target="_blank"><?php echo esc_html($p['cut']['source'] ?: 'source'); ?></a>)</span>
          <span class="tit-cross-hire">and is hiring in
            <?php echo esc_html($p['hiring_in']); ?>
            (<a href="<?php echo esc_url($p['row']['source_url']); ?>" rel="nofollow noopener"
                target="_blank"><?php echo esc_html($p['row']['source_name'] ?: 'source'); ?></a>)</span>
        </li>
      <?php endforeach; ?>
      </ul>
    </div>
    <?php
    return ob_get_clean();
}
