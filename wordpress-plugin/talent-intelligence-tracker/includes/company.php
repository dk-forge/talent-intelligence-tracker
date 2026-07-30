<?php
/**
 * Company profile pages: /talent-intelligence-tracker/company/{slug}/
 *
 * One page per employer, showing every signal we hold for them in one
 * timeline. The difference from a funding database is the receipts: every line
 * links to the filing or article that makes the claim, so the page is citable
 * rather than merely informative.
 *
 * These are also the SEO surface. Each page is unique factual content nobody
 * else has assembled, internally linked to the tracker, marked up as
 * schema.org/Organization.
 *
 * EVERYTHING HERE IS COMPUTED ON RENDER, straight off {prefix}tit_signals.
 * Nothing is generated, nothing is frozen at publish time, and there is no
 * regeneration step to forget to run: the page, its title, its description and
 * the sitemap all read the live table, so the moment a row lands the page is
 * right. That is also why the threshold below is enforced in SQL rather than in
 * a build script.
 */

if (!defined('ABSPATH')) exit;

const TIT_COMPANY_BASE = 'talent-intelligence-tracker/company';
const TIT_COMPANY_SITEMAP_PATH = 'talent-intelligence-tracker/company-sitemap.xml';

/*
 * ---------------------------------------------------------------------------
 * THE THRESHOLD GATE
 * ---------------------------------------------------------------------------
 *
 * Only employers we hold enough on get an indexable page. A thin programmatic
 * set is filtered at the SET level, so the weak pages take the strong ones down
 * with them: a smaller set that all ranks beats a larger one that gets
 * suppressed. The numbers below are measured, not guessed.
 *
 * MEASURED 2026-07-29 against the live /query endpoint (15,630 current rows,
 * 7,408 employers):
 *
 *     rows per employer   1: 4,840   2: 751   3: 376   4: 503   5: 393
 *                         6:   135   7:  90   8: 137   9: 183
 *
 *     docs per employer   1: 5,317   2: 1,215   3: 274   4: 70   5: 60
 *                         6:    66   7:    87   8: 137   9: 182
 *
 * Three things that distribution says, in the order they change the answer:
 *
 * 1. ROWS ARE THE WRONG UNIT. 235 employers carry four rows behind ONE
 *    document, because sec_execcomp splits a single pay-versus-performance
 *    table into a row per fiscal year. A row count therefore measures how
 *    finely we parse a filing, not how much we know about an employer. The gate
 *    counts DISTINCT SOURCE DOCUMENTS.
 *
 * 2. ONE DOCUMENT RESTATED IS NOT A PAGE. 5,317 employers (72%) sit behind a
 *    single document; a reader is better served by that document than by our
 *    paraphrase of it. Three documents is where a timeline starts being a
 *    history rather than a record.
 *
 * 3. THREE DOCUMENTS FROM ONE FEED IS STILL ONE THING SAID THREE TIMES. The UK
 *    pay gap rows carry an IDENTICAL read-through sentence with a different
 *    percentage in it, one row per reporting year, and 638 employers would
 *    clear a plain "three documents" bar on that alone. That is precisely the
 *    template-plus-a-number shape that gets a set filtered. So an employer
 *    whose evidence all comes from one source needs FIVE documents, which is a
 *    multi-year series a reader can read a trend off; an employer with two
 *    independent kinds of evidence qualifies at three.
 *
 * The resulting set is ~713 employers of 7,301, or 9.8%. Everything below the
 * bar still renders, still links to its sources, and is still reachable from
 * the dashboard table, but is sent noindex and is absent from the sitemap. It
 * is not 404ed: the dashboard links to it and a recruiter following that link
 * should get the page, not an error.
 */
const TIT_COMPANY_MIN_DOCS = 3;   // distinct source documents, always
const TIT_COMPANY_MIN_KINDS = 2;  // distinct kinds of evidence behind those documents
const TIT_COMPANY_MIN_DOCS_ONE_KIND = 5; // documents needed when there is only one kind

/**
 * The gate itself, as one predicate over two measured counts.
 *
 * The page and the sitemap both go through here, so an employer cannot be
 * indexable on one and absent from the other. The sibling tracker shipped
 * noindex URLs inside its own sitemap and heard about it from Search Console;
 * the fix is not care, it is having one function.
 */
function tit_company_meets_threshold($docs, $kinds) {
    $docs  = (int) $docs;
    $kinds = (int) $kinds;
    if ($docs < TIT_COMPANY_MIN_DOCS) return false;
    return $kinds >= TIT_COMPANY_MIN_KINDS || $docs >= TIT_COMPANY_MIN_DOCS_ONE_KIND;
}

/**
 * The same predicate as a SQL HAVING clause, for the sitemap, which cannot
 * afford to load every employer's rows to ask the question in PHP.
 *
 * Built from the SAME constants, so the two can only disagree if somebody
 * writes a number in here by hand. tests/test_company_page.py refuses that.
 */
function tit_company_gate_having() {
    return sprintf(
        'HAVING COUNT(DISTINCT source_url) >= %d
            AND (COUNT(DISTINCT source_name) >= %d OR COUNT(DISTINCT source_url) >= %d)',
        TIT_COMPANY_MIN_DOCS,
        TIT_COMPANY_MIN_KINDS,
        TIT_COMPANY_MIN_DOCS_ONE_KIND
    );
}

function tit_company_rewrite() {
    add_rewrite_rule(
        '^' . TIT_COMPANY_BASE . '/([^/]+)/?$',
        'index.php?tit_company=$matches[1]',
        'top'
    );
    // The sitemap is a sibling route rather than a child of /company/, so the
    // profile rule above cannot swallow it. Only the dot needs escaping, and it
    // is escaped rather than left as "any character" so nothing else can match.
    add_rewrite_rule(
        '^' . str_replace('.', '\.', TIT_COMPANY_SITEMAP_PATH) . '$',
        'index.php?tit_company_sitemap=1',
        'top'
    );
}
add_action('init', 'tit_company_rewrite');

function tit_company_query_var($vars) {
    $vars[] = 'tit_company';
    $vars[] = 'tit_company_sitemap';
    return $vars;
}
add_filter('query_vars', 'tit_company_query_var');

/**
 * Rewrite rules live in the database, and an FTP deploy runs no activation
 * hook. Flush once per version, driven by the same bump that migrates tables.
 */
function tit_company_maybe_flush() {
    if (get_option('tit_rewrites_version') === TIT_VERSION) return;
    tit_company_rewrite();
    flush_rewrite_rules(false);
    update_option('tit_rewrites_version', TIT_VERSION, false);
}
add_action('init', 'tit_company_maybe_flush', 99);

/**
 * The canonical slug: plain ASCII, [a-z0-9-] and nothing else.
 *
 * WHY IT IS NOT JUST company_key WITH THE SPACES HYPHENATED.
 *
 * That was the old rule, and it produced URLs that cannot be published,
 * measured live rather than reasoned about:
 *
 *   "&"  no encoding of it is safe. %26 does not survive the rewrite (404).
 *        The XML entity &#038; in a <loc> 301s to /company/b-&/ and then 404s
 *        for any consumer that does not resolve the entity. Only a consumer
 *        that does resolve it gets 200. 144 of 7,301 keys carry one.
 *   accents and non-Latin scripts  answer 404 percent-encoded AND literal.
 *        18 keys, one of them over the publishing threshold.
 *
 * So the slug is transliterated instead: "&" becomes "and" and everything
 * outside [a-z0-9] becomes a hyphen. "b & m retail" is b-and-m-retail,
 * "atkinsréalis uk" is atkinsrealis-uk. 167 of 7,301 keys change, and all 162
 * that previously had no publishable URL get one.
 *
 * The old form still resolves and 301s here, so no live link breaks. See
 * tit_company_rows() for the two-step lookup that makes that work.
 *
 * A key with nothing ASCII in it (one Hebrew key) canonicalises to an empty
 * string. It keeps its old, unreachable URL rather than becoming /company//,
 * so the dashboard link is no worse than it is today, and
 * tit_company_servable_slug() keeps it out of the sitemap and out of the index.
 */
function tit_company_slug($company_key) {
    $slug = strtolower((string) $company_key);
    // WordPress core, always loaded: Latin-1 and Latin Extended-A to ASCII.
    if (function_exists('remove_accents')) $slug = remove_accents($slug);
    $slug = str_replace('&', ' and ', $slug);
    $slug = preg_replace('/[^a-z0-9]+/', '-', $slug);
    $slug = trim((string) $slug, '-');

    if ($slug !== '') return $slug;
    return rawurlencode(str_replace(' ', '-', (string) $company_key));
}

/**
 * The pre-1.46 slug, which is still a live URL on this site and has to stay one.
 *
 * REGRESSION NOTE, kept from the 2026-07-28 fix: the space -> hyphen direction
 * is TOTAL and the reverse is not. company_key legitimately contains hyphens
 * ("reme-d"), so a slug is never converted back into a key. This is the forward
 * mapping, used only to compare in slug space.
 */
function tit_company_legacy_slug($company_key) {
    return str_replace(' ', '-', (string) $company_key);
}

/**
 * canonical slug -> company_key, for the keys where the two forms differ, plus
 * the slugs that two keys would both claim.
 *
 * Only the DIFFERING keys are stored: 167 of 7,301, a few kilobytes. Every
 * other key is found by the direct SQL comparison in tit_company_rows() with no
 * map at all, which is what keeps this from becoming a 7,301-entry array read
 * on every request.
 *
 * COLLISIONS ARE REFUSED, NOT RESOLVED. A canonical slug claimed by two keys is
 * one employer recorded twice, and serving either one under a shared URL would
 * silently show half of a history. So neither is served and neither is
 * published. The fix is upstream, in employer identity, and it is a merge
 * rather than a routing rule: pipeline/vocab.py EMPLOYER_KEY_ALIASES states the
 * merge, correct_company_key.py moves the stored rows onto the surviving key,
 * and ops_status.py [1c] names any new pair that has not been merged yet. The
 * refusal stays because it is what makes an unmerged pair harmless.
 *
 * 'moved' IS THE THIRD MAP, AND IT IS WHAT KEEPS A CORRECTED KEY'S OLD URL
 * ALIVE. company_key is a normalised name, so a fix to the normaliser changes
 * it, and the slug is derived from the key: correcting '-operative group' to
 * 'co-operative group' moves /company/operative-group/ to
 * /company/co-operative-group/, and the old URL was in the sitemap. A
 * correction never overwrites — it appends a revision and the old row survives
 * at is_current = 0, still carrying the old key — so the old URL is not lost
 * information, it is stored. This maps it back to the key the same signal now
 * carries, and tit_company_template() then issues its ordinary canonical 301.
 *
 * That is a property of revisions and not a list of redirects, so it covers
 * every key correction there will ever be, including ones nobody has thought of
 * yet. A live key always wins: nothing here can redirect away from an employer
 * that currently holds the slug.
 */
function tit_company_slug_index() {
    static $memo = null;
    if ($memo !== null) return $memo;

    $cached = get_transient('tit_company_slug_index');
    if (is_array($cached) && isset($cached['map']) && isset($cached['moved'])) {
        $memo = $cached;
        return $memo;
    }

    global $wpdb;
    $table = tit_table_name();
    $keys = $wpdb->get_col(
        "SELECT DISTINCT company_key FROM {$table}
          WHERE is_current = 1 AND company_key IS NOT NULL AND company_key <> ''"
    );
    $keys = is_array($keys) ? $keys : array();

    $claims = array();
    foreach ($keys as $key) {
        $slug = tit_company_slug($key);
        if ($slug === '') continue;
        $claims[$slug][] = $key;
    }

    $map = array();
    $collisions = array();
    foreach ($claims as $slug => $owners) {
        if (count($owners) > 1) {
            $collisions[$slug] = true;
            continue;
        }
        // Stored only when the canonical form differs from the legacy one; the
        // rest are already reachable by the direct comparison.
        if ($slug !== tit_company_legacy_slug($owners[0])) {
            $map[$slug] = $owners[0];
        }
    }

    $memo = array(
        'map'        => $map,
        'collisions' => $collisions,
        'moved'      => tit_company_moved_slugs($claims),
    );
    // Dropped by tit_flush_caches() on every write, so a new employer appears
    // as soon as its row lands rather than up to two hours later.
    set_transient('tit_company_slug_index', $memo, 2 * HOUR_IN_SECONDS);
    return $memo;
}

/**
 * slug -> the company_key that a superseded revision's slug now belongs to.
 *
 * One query, joining each withdrawn revision to the current revision of the
 * same signal. Only rows where the key actually MOVED come back, so this is
 * empty until a key correction runs and small forever after — a correction
 * moves employers, and there have been eleven.
 *
 * BOTH slug forms of the old key are indexed, canonical and pre-1.46, because
 * both were live URLs for it. The old key '-operative group' canonicalises to
 * "operative-group" (the leading hyphen is trimmed) and legacy-slugs to
 * "-operative-group", and the sitemap published the first.
 *
 * Two refusals, and they are the same refusal the collision map makes:
 *
 *  - a slug a CURRENT key claims is never redirected. A live employer owning
 *    the URL outranks any history of it, and this is not hypothetical: a merge
 *    like "perma-fix" -> "perma fix" leaves both keys on one slug, which the
 *    surviving employer still serves.
 *  - a slug two moved keys claim, pointing at different employers, is dropped
 *    rather than resolved to whichever the query returned first.
 *
 * @param array $claims canonical slug -> the current keys claiming it.
 */
function tit_company_moved_slugs($claims) {
    global $wpdb;
    $table = tit_table_name();

    // The aliases are `prev` and `live`, not the obvious `old` and `new`.
    // MySQL has reserved both of those at one version or another for row
    // aliases, and an unquoted reserved word here is a parse error that takes
    // out every company page at once. The harness runs on SQLite and would not
    // catch it, so the safe names are the ones written.
    $pairs = $wpdb->get_results(
        "SELECT DISTINCT prev.company_key AS old_key, live.company_key AS new_key
           FROM {$table} prev
           INNER JOIN {$table} live
                   ON live.signal_id = prev.signal_id AND live.is_current = 1
          WHERE prev.is_current = 0
            AND prev.company_key <> live.company_key
            AND prev.company_key <> ''",
        ARRAY_A
    );
    if (!is_array($pairs) || !$pairs) return array();

    $moved = array();
    $ambiguous = array();
    foreach ($pairs as $pair) {
        $forms = array(
            tit_company_slug($pair['old_key']),
            tit_company_legacy_slug($pair['old_key']),
        );
        foreach (array_unique($forms) as $slug) {
            if ($slug === '' || isset($claims[$slug]) || isset($ambiguous[$slug])) continue;
            if (isset($moved[$slug]) && $moved[$slug] !== $pair['new_key']) {
                unset($moved[$slug]);
                $ambiguous[$slug] = true;
                continue;
            }
            $moved[$slug] = $pair['new_key'];
        }
    }
    return $moved;
}

/**
 * Whether this employer has a URL we can publish.
 *
 * Two ways to fail, and only two, now that the slug transliterates:
 *
 *  - nothing survives canonicalisation, so there is no ASCII slug at all (one
 *    Hebrew key);
 *  - two keys claim the same canonical slug, so the URL would be ambiguous.
 *
 * A key that fails either is not indexable AND not in the sitemap, which is the
 * same single decision the threshold goes through, for the same reason: a URL
 * in a sitemap has to be a promise, and a sitemap full of 404s or of pages
 * showing half an employer is what gets a whole set distrusted.
 */
function tit_company_servable_slug($company_key) {
    $slug = tit_company_slug($company_key);
    if ($slug === '' || preg_match('/[^a-z0-9-]/', $slug)) return false;
    $index = tit_company_slug_index();
    return !isset($index['collisions'][$slug]);
}

function tit_company_url($company_key) {
    return home_url('/' . TIT_COMPANY_BASE . '/' . tit_company_slug($company_key) . '/');
}

function tit_company_sitemap_url() {
    return home_url('/' . TIT_COMPANY_SITEMAP_PATH);
}

/**
 * Rows for one employer, newest first, looked up BY SLUG, in two steps.
 *
 * STEP 1 is the pre-1.46 comparison and it is kept exactly as it was, because
 * every /company/ URL that has ever worked on this site is of that shape and
 * has to keep resolving. It is done in SLUG SPACE:
 *
 *   REGRESSION NOTE (fixed 2026-07-28, confirmed live): the lookup once rebuilt
 *   the key from the slug with hyphens -> spaces. That mapping is not
 *   reversible: company_key legitimately contains hyphens (key "reme-d" renders
 *   /company/reme-d/, which un-slugged to "reme d" and 404ed). The space ->
 *   hyphen direction IS total, so the match is REPLACE(company_key,' ','-') =
 *   slug. Never reintroduce a slug -> key conversion here.
 *
 * STEP 2 handles the canonical slugs that step 1 cannot express, because they
 * involve transliteration SQL has no function for: "b-and-m-retail" for
 * "b & m retail", "atkinsrealis-uk" for "atkinsréalis uk". Those go through the
 * small precomputed index, and then match company_key exactly on its own index.
 * This is still not a slug -> key conversion: the index is built by applying the
 * forward mapping to every key and remembering the result.
 *
 * STEP 3 is the slug of a key that has since been CORRECTED. Nothing current
 * claims it, but a superseded revision does, and the signal that revision
 * belongs to is still here under its new key. Returning that employer's current
 * rows makes tit_company_template() redirect to the canonical URL by the same
 * comparison it already runs for a pre-1.46 slug, so the caller needs no new
 * branch and there is no second redirect rule to keep in step with the first.
 *
 * The three steps are ordered this way so the common path is one indexed query
 * and touches no map at all, and so a live employer can never be redirected
 * away from a URL it holds.
 */
function tit_company_rows($slug) {
    global $wpdb;
    $table = tit_table_name();
    $columns = "headline, summary, talent_readthrough, company, company_key, pillar, signal_direction,
                city, region, country, hq_city, hq_country, state, functions, industry,
                headcount, funding_amount, funding_amount_usd, funding_stage,
                confidence, source_url, source_name, archive_url,
                published_date, captured_at, collector";
    $order = "ORDER BY COALESCE(published_date, DATE(captured_at)) DESC";

    $rows = $wpdb->get_results($wpdb->prepare(
        "SELECT {$columns} FROM {$table}
          WHERE is_current = 1 AND REPLACE(company_key, ' ', '-') = %s {$order}",
        $slug
    ), ARRAY_A);
    if ($rows) return $rows;

    $index = tit_company_slug_index();
    $key = '';
    if (!empty($index['map'][$slug]))        $key = $index['map'][$slug];
    elseif (!empty($index['moved'][$slug]))  $key = $index['moved'][$slug];
    if ($key === '') return array();

    return $wpdb->get_results($wpdb->prepare(
        "SELECT {$columns} FROM {$table}
          WHERE is_current = 1 AND company_key = %s {$order}",
        $key
    ), ARRAY_A) ?: array();
}

/**
 * Everything the page, its title, its description and the gate need, derived
 * from the rows we already have. One pass, no second query.
 *
 * Every figure here is a sum or a count of stored rows. Nothing is inferred and
 * nothing is carried over from a previous render.
 */
function tit_company_profile($rows) {
    $docs = array();
    $kinds = array();
    $funding_usd = 0;
    $leadership = 0;
    $verified = 0;
    $tracked_since = '';
    $latest_place = '';

    foreach ($rows as $r) {
        if (!empty($r['source_url']))  $docs[$r['source_url']]   = true;
        if (!empty($r['source_name'])) $kinds[$r['source_name']] = true;
        if (!empty($r['funding_amount_usd'])) $funding_usd += (float) $r['funding_amount_usd'];
        if ($r['pillar'] === 'leadership_change') $leadership++;
        if ($r['confidence'] === 'verified') $verified++;

        /*
          "Tracked since" is the date of the EARLIEST DOCUMENT, not the date we
          first read one.

          It was MIN(captured_at), which is when this pipeline happened to
          collect the row. Every one of the 15,711 current rows was captured in
          July 2026, because that is when the backfills ran, so all 715 indexable
          profiles said "since July 2026" -- while the same page said "last
          update 3 months ago" a few lines away, because published_date runs back
          to 2017. Two dates from one employer's history contradicting each other
          on one screen, and both of them in the meta description.

          COALESCE(published_date, DATE(captured_at)) is the expression the
          dashboard's span note and the place pages already use for exactly this
          reason (shortcodes.php's $date_expr, places.php's own). A row whose
          source stated no date falls back to when we saw it, which is the only
          answer left and is never earlier than the truth.
        */
        $dated = substr((string) ($r['published_date'] ?? ''), 0, 10);
        $seen = $dated !== '' ? $dated : substr((string) $r['captured_at'], 0, 10);
        if ($seen && ($tracked_since === '' || $seen < $tracked_since)) $tracked_since = $seen;

        if ($latest_place === '') {
            $place = $r['city'] ?: $r['hq_city'];
            $cc    = $r['country'] ?: $r['hq_country'];
            $latest_place = trim(($place ? $place . ', ' : '') . tit_country_name($cc), ', ');
        }
    }

    $n_docs  = count($docs);
    $n_kinds = count($kinds);

    return array(
        'name'          => $rows ? $rows[0]['company'] : '',
        'updates'       => count($rows),
        'documents'     => $n_docs,
        'kinds'         => $n_kinds,
        'funding_usd'   => $funding_usd,
        'leadership'    => $leadership,
        'verified'      => $verified,
        'tracked_since' => $tracked_since,
        'place'         => $latest_place,
        'indexable'     => tit_company_meets_threshold($n_docs, $n_kinds)
                           && tit_company_servable_slug($rows ? $rows[0]['company_key'] : ''),
    );
}

/**
 * Rows and profile for the slug currently being rendered, computed once.
 *
 * The title filter, the head tags and the body all need the same figures, and
 * they run at three different points in the request.
 */
function tit_company_current($slug = null) {
    static $cache = array();
    if ($slug === null) {
        $var = get_query_var('tit_company');
        if (!$var) return null;
        $slug = rawurldecode(sanitize_text_field($var));
    }
    if (!isset($cache[$slug])) {
        $rows = tit_company_rows($slug);
        $cache[$slug] = $rows
            ? array('slug' => $slug, 'rows' => $rows, 'profile' => tit_company_profile($rows))
            : false;
    }
    return $cache[$slug] ?: null;
}

/** Reader-facing wording for the four stored directions. */
function tit_company_direction_labels() {
    return array(
        'hiring'      => 'Hiring up',
        'displacement' => 'Cutting back',
        'comp_shift'  => 'Pay change reported',
        'neutral'     => 'Update reported',
    );
}

/** Reader-facing wording for the four stored pillars. */
function tit_company_pillar_labels() {
    return array(
        'company_development' => 'Growing and expanding',
        'leadership_change'   => 'Leadership moves',
        'rewards_comp'        => 'Pay and benefits',
        'how_we_work'         => 'Ways of working',
    );
}

/**
 * The one line a reader glances at before deciding to read on: what the most
 * recent signal was, and how long ago.
 *
 * Read off the newest row only, never averaged over the timeline. An employer
 * whose last three years were quiet and whose last week was a funding round is
 * described by the funding round.
 *
 * Dates can legitimately sit in the future here: a pay-versus-performance table
 * is filed for a fiscal year that has not ended. A future date is printed as a
 * date rather than being run through a "time ago" that would read "3 months
 * ago" for something that has not happened.
 */
function tit_company_status_line($rows) {
    if (!$rows) return '';
    $r = $rows[0];

    $stages = function_exists('tit_funding_stage_labels') ? tit_funding_stage_labels() : array();
    $what = '';
    if (!empty($r['funding_stage']) && isset($stages[$r['funding_stage']])) {
        $what = 'Funding reported, ' . $stages[$r['funding_stage']];
    } elseif (!empty($r['funding_amount'])) {
        $what = 'Funding reported, ' . $r['funding_amount'];
    } else {
        $directions = tit_company_direction_labels();
        $pillars    = tit_company_pillar_labels();
        $what = $directions[$r['signal_direction']] ?? '';
        if ($what === '' || $r['signal_direction'] === 'neutral') {
            $what = $pillars[$r['pillar']] ?? 'Update reported';
        }
    }

    $when = $r['published_date'] ?: substr((string) $r['captured_at'], 0, 10);
    $ts = $when ? strtotime($when . ' 00:00:00 UTC') : 0;
    if (!$ts) return $what;
    if ($ts > time()) {
        return sprintf('%s, dated %s', $what, $when);
    }
    return sprintf('%s, last update %s ago', $what, human_time_diff($ts, time()));
}

/**
 * The stats strip. A tile reading "0" or a dash is not a fact, it is an empty
 * slot, and four of those make a young profile look broken. Only what we hold.
 */
function tit_company_facts($profile) {
    $facts = array();
    $facts[] = array(
        number_format_i18n($profile['updates']),
        $profile['updates'] === 1 ? 'update tracked' : 'updates tracked',
    );
    if ($profile['funding_usd'] > 0 && function_exists('tit_money_short')) {
        $facts[] = array(tit_money_short($profile['funding_usd']), 'disclosed funding');
    }
    if ($profile['leadership'] > 0) {
        $facts[] = array(
            number_format_i18n($profile['leadership']),
            $profile['leadership'] === 1 ? 'leadership change' : 'leadership changes',
        );
    }
    if ($profile['tracked_since']) {
        $ts = strtotime($profile['tracked_since'] . ' 00:00:00 UTC');
        $facts[] = array($ts ? date_i18n('j M Y', $ts) : $profile['tracked_since'], 'tracked since');
    }
    if ($profile['place']) {
        $facts[] = array($profile['place'], 'where');
    }
    return $facts;
}

function tit_company_template() {
    if (!get_query_var('tit_company')) return;
    $current = tit_company_current();

    if (!$current) {
        status_header(404);
        nocache_headers();
        // A company we hold nothing for is a 404, not an empty page. An empty
        // page for every possible slug is a doorway-page pattern.
        include get_404_template();
        exit;
    }

    /*
     * One URL per employer. A profile reached by its pre-1.46 slug redirects to
     * the canonical one, permanently, so no link that has ever worked breaks
     * and no employer is indexable at two addresses.
     *
     * This cannot loop: the canonical slug resolves to the same key, and the
     * comparison is then equal. It is skipped for a key with no servable
     * canonical form, which would otherwise redirect to a URL that 404s.
     */
    $canonical = tit_company_slug($current['rows'][0]['company_key']);
    if ($canonical !== $current['slug'] && tit_company_servable_slug($current['rows'][0]['company_key'])) {
        wp_safe_redirect(tit_company_url($current['rows'][0]['company_key']), 301);
        exit;
    }

    // Below the threshold: the page renders and stays linkable, but is not
    // offered to a search engine and is not in the sitemap. Sent as a header
    // rather than only as a meta tag, so it applies whatever an SEO plugin
    // decides to print into the head of a route it does not recognise.
    if (!$current['profile']['indexable']) {
        header('X-Robots-Tag: noindex, follow', true);
    }

    tit_company_render($current['rows'], $current['slug'], $current['profile']);
    exit;
}
add_action('template_redirect', 'tit_company_template');

function tit_company_render($rows, $key, $profile) {
    // Everything below derives from the KEY WE RESOLVED, never from the slug
    // that was requested. Two slugs now reach this page (the canonical one and
    // the pre-1.46 one, which 301s), so the requested slug is not a stable
    // identity and using it would put the wrong URL in the canonical tag and
    // in the structured data.
    $company_key = $rows[0]['company_key'];
    $name = $profile['name'];
    $labels = tit_company_pillar_labels();
    $directions = tit_company_direction_labels();
    $status = tit_company_status_line($rows);
    $facts = tit_company_facts($profile);

    // Block theme, so never get_header(): see tit_render_header(). Company
    // profiles are the SEO surface, and they were the pages shipping with no
    // logo and no navigation.
    if (function_exists('tit_render_header')) tit_render_header(); else get_header();
    ?>
    <div class="tit-wrap tit-company">
      <nav class="tit-crumb">
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Talent Intelligence Tracker</a>
        <span aria-hidden="true">›</span> <?php echo esc_html($name); ?>
      </nav>

      <h1><?php echo esc_html($name); ?></h1>

      <?php if ($status) : ?>
        <p class="tit-status"><?php echo esc_html($status); ?></p>
      <?php endif; ?>

      <?php
      // Industry and headquarters, where the rows carry them. Printed as a
      // subhead rather than as tiles, because either can be absent and an empty
      // tile reads as a missing fact rather than an unstated one.
      $industry = '';
      $hq = '';
      foreach ($rows as $r) {
          if ($industry === '' && !empty($r['industry'])) {
              $industry = ucfirst(str_replace('_', ' ', $r['industry']));
          }
          if ($hq === '' && (!empty($r['hq_city']) || !empty($r['hq_country']))) {
              $hq = trim(($r['hq_city'] ? $r['hq_city'] . ', ' : '')
                         . tit_country_name($r['hq_country']), ', ');
          }
      }
      $subhead = array_filter(array($industry, $hq ? 'Headquarters ' . $hq : ''));
      if ($subhead) : ?>
        <p class="tit-note"><?php echo esc_html(implode(' · ', $subhead)); ?></p>
      <?php endif; ?>

      <p class="tit-note">
        Everything we hold on <?php echo esc_html($name); ?>, newest first.
        Every line links to the filing or report it came from.
      </p>

      <div class="tit-stats tit-stats-<?php echo count($facts); ?>">
        <?php foreach ($facts as [$n, $label]) : ?>
          <div class="tit-stat">
            <span class="tit-n<?php echo is_string($n) && strlen($n) > 3 ? ' tit-n-word' : ''; ?>"><?php echo esc_html($n); ?></span>
            <span class="tit-l"><?php echo esc_html($label); ?></span>
          </div>
        <?php endforeach; ?>
      </div>

      <?php
      // Job-posting volume, when we have been counting this employer's own
      // board. Guarded: an FTP deploy can land company.php before
      // board_series.php, and a hard call would fatal the page for the seconds
      // in between.
      // board_series.php matches on the LEGACY slug form (it does its own
      // str_replace(' ', '-') against the key it was given), so it is handed
      // that form explicitly rather than whichever slug the reader arrived on.
      if (function_exists('tit_board_series_panel')) {
          echo tit_board_series_panel(tit_company_legacy_slug($company_key));  // built and escaped there
      }
      ?>

      <?php if (!$profile['indexable']) : ?>
        <p class="tit-thin">
          This profile is thin because we hold
          <?php echo $profile['documents'] === 1
                ? 'one source document'
                : esc_html(number_format_i18n($profile['documents'])) . ' source documents'; ?>
          on <?php echo esc_html($name); ?> so far, not because nothing else has
          happened there. We only publish what we have read on a primary source
          and can link to, so a profile fills up as filings and reports come in
          rather than being seeded from an estimate. Profiles at this stage are
          left out of our sitemap and marked noindex until they carry enough to
          be worth a search result of their own.
        </p>
      <?php endif; ?>

      <ol class="tit-timeline">
        <?php foreach ($rows as $r) :
            $place = $r['city'] ?: $r['hq_city'];
            $cc    = $r['country'] ?: $r['hq_country'];
            $where = trim(($place ? $place . ', ' : '') . tit_country_name($cc), ', '); ?>
          <li class="tit-event">
            <div class="tit-event-when">
              <?php echo esc_html($r['published_date'] ?: substr($r['captured_at'], 0, 10)); ?>
            </div>
            <div class="tit-event-body">
              <span class="tit-tag tit-<?php echo esc_attr($r['signal_direction']); ?>">
                <?php echo esc_html($directions[$r['signal_direction']] ?? $r['signal_direction']); ?>
              </span>
              <span class="tit-tag"><?php echo esc_html($labels[$r['pillar']] ?? $r['pillar']); ?></span>
              <h2 class="tit-h"><?php echo esc_html($r['headline']); ?></h2>
              <p class="tit-rt"><?php echo esc_html($r['talent_readthrough']); ?></p>
              <p class="tit-event-meta">
                <?php if ($where) : ?><?php echo esc_html($where); ?> · <?php endif; ?>
                <?php if ($r['headcount']) : ?><strong><?php echo (int) $r['headcount']; ?></strong> roles · <?php endif; ?>
                <?php if ($r['funding_amount']) : ?><strong><?php echo esc_html($r['funding_amount']); ?></strong> raised · <?php endif; ?>
                <?php /* The same reader-facing labels as the dashboard table.
                         A profile page reading "rumored" while the tracker it
                         links from reads "Unconfirmed" is one product speaking
                         two languages. */ ?>
                <span class="tit-conf tit-c-<?php echo esc_attr($r['confidence']); ?>"><?php
                  $conf_labels = function_exists('tit_confidence_labels') ? tit_confidence_labels() : array();
                  echo esc_html($conf_labels[$r['confidence']] ?? $r['confidence']); ?></span>
                · <a href="<?php echo esc_url($r['source_url']); ?>" rel="nofollow noopener" target="_blank"><?php echo esc_html($r['source_name']); ?></a>
                <?php if (!empty($r['archive_url'])) : ?>
                  · <a href="<?php echo esc_url($r['archive_url']); ?>" rel="nofollow noopener" target="_blank">archived copy</a>
                <?php endif; ?>
              </p>
            </div>
          </li>
        <?php endforeach; ?>
      </ol>

      <p class="tit-cite">
        The read-through on each line is our interpretation. The headline and
        figures come from the linked source. Data licensed CC BY 4.0.
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Back to the tracker</a>
      </p>
    </div>

    <?php
    /*
     * Structured data describes ONLY what is on the page above it: the
     * employer's name, its URL, and the updates rendered in the timeline. The
     * sibling tracker earned a manual-action risk by emitting identical
     * FAQPage markup across roughly 1,830 URLs where the answers were not
     * visible anywhere in the document, so nothing is asserted here that a
     * reader cannot read.
     *
     * Emitted on indexable profiles only. On a noindex page it would be markup
     * addressed to a crawler that has been told not to index the page.
     */
    if ($profile['indexable']) : ?>
    <script type="application/ld+json"><?php
      $visible = array_slice($rows, 0, 10);
      echo wp_json_encode(array(
        '@context' => 'https://schema.org',
        '@type'    => 'Organization',
        'name'     => $name,
        'url'      => tit_company_url($company_key),
        'subjectOf' => array_map(function ($r) {
            return array(
                '@type'         => 'NewsArticle',
                'headline'      => $r['headline'],
                'datePublished' => $r['published_date'],
                'url'           => $r['source_url'],
            );
        }, $visible),
      ), JSON_UNESCAPED_SLASHES);
    ?></script>
    <?php endif;
    if (function_exists('tit_render_footer')) tit_render_footer(); else get_footer();
}

/**
 * Title, from live figures. Sentence case, no em-dash, no superlative.
 *
 * The count is read on render like everything else, so a profile that gained
 * two updates this morning says so this morning.
 */
function tit_company_title($title) {
    $current = tit_company_current();
    if (!$current) return $title;
    $p = $current['profile'];
    return sprintf(
        '%s: %s tracked %s on hiring, funding and leadership',
        $p['name'],
        number_format_i18n($p['updates']),
        $p['updates'] === 1 ? 'update' : 'updates'
    );
}
add_filter('pre_get_document_title', 'tit_company_title');

/**
 * Description, canonical and the robots directive, all from the live row set.
 *
 * These routes are not queries the SEO plugin recognises (the same reason the
 * sources page shipped with no title on 1.30.1), so if this file does not print
 * them nothing does.
 */
function tit_company_head() {
    $current = tit_company_current();
    if (!$current) return;
    $p = $current['profile'];

    $bits = array(sprintf(
        '%s %s tracked for %s',
        number_format_i18n($p['updates']),
        $p['updates'] === 1 ? 'update' : 'updates',
        $p['name']
    ));
    if ($p['tracked_since']) {
        $ts = strtotime($p['tracked_since'] . ' 00:00:00 UTC');
        if ($ts) $bits[0] .= ' since ' . date_i18n('F Y', $ts);
    }
    if ($p['funding_usd'] > 0 && function_exists('tit_money_short')) {
        $bits[] = tit_money_short($p['funding_usd']) . ' disclosed funding';
    }
    if ($p['leadership'] > 0) {
        $bits[] = $p['leadership'] . ($p['leadership'] === 1 ? ' leadership change' : ' leadership changes');
    }
    $status = tit_company_status_line($current['rows']);
    $desc = implode('. ', array_filter(array(
        implode(', ', $bits),
        $status,
        'Each linked to the filing or report behind it',
    ))) . '.';
    // Search results cut a description around 160 characters; a sentence that
    // ends mid-figure is worse than a shorter one.
    if (strlen($desc) > 300) $desc = rtrim(substr($desc, 0, 297)) . '...';

    echo "\n" . '<meta name="description" content="' . esc_attr($desc) . '" />' . "\n";
    echo '<link rel="canonical" href="' . esc_url(tit_company_url($current['rows'][0]['company_key'])) . '" />' . "\n";

    // The robots directive is NOT printed here. See tit_company_head_close().
}
add_action('wp_head', 'tit_company_head', 1);

/*
 * EXACTLY ONE ROBOTS TAG, whatever else is installed.
 *
 * Measured live on 1.45.0: a below-threshold profile served BOTH
 * "noindex, follow" from us and "follow, index" from the site's SEO plugin,
 * printed one after the other. Google resolves a conflict by taking the most
 * restrictive, so the page genuinely was noindex, but two head tags
 * contradicting each other is a defect an audit reports and a reader of the
 * source cannot resolve.
 *
 * The first fix named a plugin's filter, and it did nothing, because the tag
 * was coming from a DIFFERENT plugin than the fingerprint in the page
 * suggested. Naming a plugin pins us to that plugin and to its current hook
 * names, and gets this wrong again the day the site changes one. So the head is
 * buffered and every robots tag in it is replaced with ours. Same trick
 * tit_render_header() already uses to supply a <title> only when nothing else
 * did, and it does not care what is installed.
 *
 * The X-Robots-Tag header goes out independently in tit_company_template(), so
 * even a request where this buffer never closes cannot leave a thin profile
 * indexable.
 */
function tit_company_head_open() {
    if (!tit_company_current()) return;
    ob_start();
}
add_action('wp_head', 'tit_company_head_open', 0);

function tit_company_head_close() {
    $current = tit_company_current();
    if (!$current) return;
    $head = ob_get_clean();

    $head = preg_replace('#<meta\s+name=(["\'])robots\1[^>]*>#i', '', $head);
    $directive = $current['profile']['indexable'] ? 'index, follow' : 'noindex, follow';
    echo '<meta name="robots" content="' . esc_attr($directive) . '" />' . "\n" . $head;
}
add_action('wp_head', 'tit_company_head_close', 9999);

/*
 * ---------------------------------------------------------------------------
 * THE SITEMAP
 * ---------------------------------------------------------------------------
 *
 * /talent-intelligence-tracker/company-sitemap.xml, generated on request from
 * the live table through tit_company_gate_having() — the same gate the page
 * itself is indexed by. Nothing is written to disk, so it cannot go stale, and
 * a URL cannot be listed here while the page it points at says noindex.
 */
function tit_company_sitemap_entries() {
    $cached = get_transient('tit_company_sitemap');
    if (is_array($cached)) return $cached;

    global $wpdb;
    $table = tit_table_name();
    $rows = $wpdb->get_results(
        "SELECT company_key,
                MAX(COALESCE(published_date, DATE(captured_at))) AS lastmod
           FROM {$table}
          WHERE is_current = 1 AND company_key IS NOT NULL AND company_key <> ''
          GROUP BY company_key
          " . tit_company_gate_having() . "
          ORDER BY company_key ASC",
        ARRAY_A
    );
    $rows = is_array($rows) ? $rows : array();

    // The same servability check the page's own indexable flag carries, so a
    // URL we cannot serve is never published. Done here rather than in SQL
    // because a byte-range test is one line of PHP and an unreadable REGEXP in
    // MySQL, and there are only a few hundred rows to walk.
    $rows = array_values(array_filter($rows, function ($r) {
        return tit_company_servable_slug($r['company_key']);
    }));

    // Two hours. The gate needs several documents to move, so this set changes
    // slowly, and the query groups the whole table. tit_flush_caches() drops
    // every tit_ transient on any write, so a real change is not waited for.
    set_transient('tit_company_sitemap', $rows, 2 * HOUR_IN_SECONDS);
    return $rows;
}

/**
 * WordPress adds a trailing slash to anything it does not recognise as a file,
 * so the sitemap answered 301 to .../company-sitemap.xml/ before serving. A
 * sitemap URL that redirects is a redirect reported in Search Console for every
 * fetch, and the slashed form is not a name anyone would submit. Measured on
 * 1.45.0, fixed here rather than by publishing the slashed URL.
 */
function tit_company_sitemap_no_canonical_redirect($redirect) {
    return get_query_var('tit_company_sitemap') ? false : $redirect;
}
add_filter('redirect_canonical', 'tit_company_sitemap_no_canonical_redirect');

function tit_company_sitemap_template() {
    if (!get_query_var('tit_company_sitemap')) return;

    $entries = tit_company_sitemap_entries();
    $today = gmdate('Y-m-d');

    status_header(200);
    header('Content-Type: application/xml; charset=UTF-8', true);
    header('X-Robots-Tag: noindex', true); // the sitemap itself is not a page

    echo '<?xml version="1.0" encoding="UTF-8"?>' . "\n";
    echo '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' . "\n";
    foreach ($entries as $e) {
        // A future-dated row (a fiscal year that has not ended) must not become
        // a future lastmod: crawlers treat that as a broken date.
        $lastmod = $e['lastmod'] && $e['lastmod'] <= $today ? $e['lastmod'] : $today;
        echo '  <url><loc>' . esc_url(tit_company_url($e['company_key'])) . '</loc>'
           . '<lastmod>' . esc_html($lastmod) . '</lastmod></url>' . "\n";
    }
    echo '</urlset>' . "\n";
    exit;
}
add_action('template_redirect', 'tit_company_sitemap_template');

/**
 * Advertise it in robots.txt, WHICH IS CURRENTLY INERT, and this is written
 * down rather than left as an assumption.
 *
 * Checked live 2026-07-29: /blog/robots.txt is a PHYSICAL FILE, so Apache
 * serves it from disk and WordPress never runs this filter (gotcha 5 in
 * CLAUDE.md, the same reason llms.txt had to be a real file). And the robots.txt
 * a crawler actually reads for this host is https://asktherecruiter.com/
 * robots.txt, which belongs to the separate root app and lists only the SEO
 * plugin's sitemap index. Neither file is reachable from this repo.
 *
 * So sitemap discovery today is the internal links: the dashboard table links
 * every employer to its profile. Getting the sitemap itself in front of a
 * crawler is a ONE-LINE MANUAL STEP for the owner, recorded in the handover:
 * submit it in Search Console, or add its URL to the root robots.txt.
 *
 * The filter stays because it costs nothing and starts working by itself the
 * day that physical file goes away. It is not counted as working now.
 */
function tit_company_robots_txt($output) {
    return $output . "\nSitemap: " . tit_company_sitemap_url() . "\n";
}
add_filter('robots_txt', 'tit_company_robots_txt');
