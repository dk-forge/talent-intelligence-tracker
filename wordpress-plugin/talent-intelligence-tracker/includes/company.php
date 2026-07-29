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
 * company_key holds spaces ("peace coffee"); a URL should not. Hyphens survive
 * the rewrite rule intact where %20 does not, so the slug is the hyphenated
 * form — and the lookup compares in SLUG space, never by converting back.
 */
function tit_company_slug($company_key) {
    return rawurlencode(str_replace(' ', '-', $company_key));
}

function tit_company_url($company_key) {
    return home_url('/' . TIT_COMPANY_BASE . '/' . tit_company_slug($company_key) . '/');
}

function tit_company_sitemap_url() {
    return home_url('/' . TIT_COMPANY_SITEMAP_PATH);
}

/**
 * Rows for one employer, newest first, looked up BY SLUG.
 *
 * REGRESSION NOTE (fixed 2026-07-28, confirmed live): the old lookup rebuilt
 * the key from the slug with hyphens -> spaces. That mapping is not
 * reversible: company_key legitimately contains hyphens (key "reme-d" renders
 * the link /company/reme-d/, which un-slugged to "reme d" and 404ed). The
 * space -> hyphen direction IS total, so the match is done in slug space:
 * REPLACE(company_key, ' ', '-') = slug. Space-keyed companies keep working
 * ("peace coffee" -> "peace-coffee") and hyphen-keyed ones match verbatim.
 * Never reintroduce a slug -> key conversion here.
 */
function tit_company_rows($slug) {
    global $wpdb;
    $table = tit_table_name();
    return $wpdb->get_results($wpdb->prepare(
        "SELECT headline, summary, talent_readthrough, company, pillar, signal_direction,
                city, region, country, hq_city, hq_country, state, functions, industry,
                headcount, funding_amount, funding_amount_usd, funding_stage,
                confidence, source_url, source_name, archive_url,
                published_date, captured_at, collector
           FROM {$table}
          WHERE is_current = 1 AND REPLACE(company_key, ' ', '-') = %s
          ORDER BY COALESCE(published_date, DATE(captured_at)) DESC",
        $slug
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

        $seen = substr((string) $r['captured_at'], 0, 10);
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
        'indexable'     => tit_company_meets_threshold($n_docs, $n_kinds),
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
      if (function_exists('tit_board_series_panel')) {
          echo tit_board_series_panel($key);  // built and escaped in that file
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
        'url'      => tit_company_url($key),
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
    echo '<link rel="canonical" href="' . esc_url(tit_company_url($current['slug'])) . '" />' . "\n";

    // Yoast prints a robots tag of its own on these routes: measured on 1.45.0,
    // a below-threshold profile served BOTH "noindex, follow" from us and
    // "follow, index" from Yoast. Google resolves that by taking the most
    // restrictive, so the page was in fact noindex, but two head tags
    // contradicting each other is a defect a reader of the source cannot
    // resolve and an audit will report. Yoast is told instead, through
    // tit_company_yoast_robots() below, and we stay quiet so there is exactly
    // one tag. The X-Robots-Tag header is sent either way, so a Yoast that
    // ever stops printing on our routes cannot leave the page indexable.
    if (!$p['indexable'] && !defined('WPSEO_VERSION')) {
        echo '<meta name="robots" content="noindex, follow" />' . "\n";
    }
}
add_action('wp_head', 'tit_company_head', 1);

/** The same directive, expressed in Yoast's own vocabulary. */
function tit_company_yoast_robots($robots) {
    $current = tit_company_current();
    if (!$current || $current['profile']['indexable']) return $robots;
    if (is_array($robots)) $robots['index'] = 'noindex';
    return $robots;
}
add_filter('wpseo_robots_array', 'tit_company_yoast_robots');

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

/** Point crawlers at it, so it does not depend on anyone submitting it. */
function tit_company_robots_txt($output) {
    return $output . "\nSitemap: " . tit_company_sitemap_url() . "\n";
}
add_filter('robots_txt', 'tit_company_robots_txt');
