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
 */

if (!defined('ABSPATH')) exit;

const TIT_COMPANY_BASE = 'talent-intelligence-tracker/company';

function tit_company_rewrite() {
    add_rewrite_rule(
        '^' . TIT_COMPANY_BASE . '/([^/]+)/?$',
        'index.php?tit_company=$matches[1]',
        'top'
    );
}
add_action('init', 'tit_company_rewrite');

function tit_company_query_var($vars) {
    $vars[] = 'tit_company';
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
                headcount, funding_amount, confidence, source_url, source_name,
                published_date, captured_at, collector
           FROM {$table}
          WHERE is_current = 1 AND REPLACE(company_key, ' ', '-') = %s
          ORDER BY COALESCE(published_date, DATE(captured_at)) DESC",
        $slug
    ), ARRAY_A) ?: array();
}

function tit_company_template() {
    $key = get_query_var('tit_company');
    if (!$key) return;

    // The slug goes to the lookup as-is; see the regression note above.
    $slug = rawurldecode(sanitize_text_field($key));
    $rows = tit_company_rows($slug);
    if (!$rows) {
        status_header(404);
        nocache_headers();
        // A company we hold nothing for is a 404, not an empty page. An empty
        // page for every possible slug is a doorway-page pattern.
        include get_404_template();
        exit;
    }

    tit_company_render($rows, $slug);
    exit;
}
add_action('template_redirect', 'tit_company_template');

function tit_company_render($rows, $key) {
    $name = $rows[0]['company'];
    $labels = array(
        'company_development' => 'Growing and expanding',
        'leadership_change'   => 'Leadership moves',
        'rewards_comp'        => 'Pay and benefits',
        'how_we_work'         => 'Ways of working',
    );
    $directions = array(
        'hiring' => 'Hiring up', 'displacement' => 'Cutting back',
        'comp_shift' => 'Pay change', 'neutral' => 'Other change',
    );

    // Summary facts, all derived from stored rows — nothing inferred.
    $funding = array();
    $latest_place = '';
    $verified = 0;
    foreach ($rows as $r) {
        if ($r['funding_amount']) $funding[] = $r['funding_amount'];
        if (!$latest_place) {
            $place = $r['city'] ?: $r['hq_city'];
            $cc    = $r['country'] ?: $r['hq_country'];
            $latest_place = trim(($place ? $place . ', ' : '') . tit_country_name($cc), ', ');
        }
        if ($r['confidence'] === 'verified') $verified++;
    }

    get_header();
    ?>
    <div class="tit-wrap tit-company">
      <nav class="tit-crumb">
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Talent Intelligence Tracker</a>
        <span aria-hidden="true">›</span> <?php echo esc_html($name); ?>
      </nav>

      <h1><?php echo esc_html($name); ?></h1>
      <p class="tit-note">
        Everything we hold on <?php echo esc_html($name); ?>, newest first.
        Every line links to the filing or report it came from.
      </p>

      <?php
      // Only facts we hold. A tile reading "0" or a dash is not a fact, it is
      // an empty slot, and four of them make a thin profile look broken rather
      // than young.
      $facts = array(array(count($rows), count($rows) === 1 ? 'update' : 'updates'));
      if ($verified) $facts[] = array($verified, 'from official filings');
      if ($funding)  $facts[] = array($funding[0], 'latest raise');
      if ($latest_place) $facts[] = array($latest_place, 'where');
      $latest_kind = $rows ? ($labels[$rows[0]['pillar']] ?? '') : '';
      if ($latest_kind) $facts[] = array($latest_kind, 'most recent');
      ?>
      <div class="tit-stats tit-stats-<?php echo count($facts); ?>">
        <?php foreach ($facts as [$n, $label]) : ?>
          <div class="tit-stat">
            <span class="tit-n<?php echo is_string($n) && strlen($n) > 3 ? ' tit-n-word' : ''; ?>"><?php echo esc_html($n); ?></span>
            <span class="tit-l"><?php echo esc_html($label); ?></span>
          </div>
        <?php endforeach; ?>
      </div>

      <?php if (count($rows) < 3) : ?>
        <p class="tit-thin">
          This profile is thin because we hold
          <?php echo count($rows) === 1 ? 'one update' : count($rows) . ' updates'; ?>
          on <?php echo esc_html($name); ?> so far, not because nothing else has
          happened there. We only publish what we have read on a primary source
          and can link to, so a profile fills up as filings and reports come in
          rather than being seeded from an estimate.
        </p>
      <?php endif; ?>

      <ol class="tit-timeline">
        <?php foreach ($rows as $r) :
            $place = $r['city'] ?: $r['hq_city'];
            $cc    = $r['country'] ?: $r['hq_country'];
            $where = trim(($place ? $place . ', ' : '') . tit_country_name($cc), ', ');
            $fns   = $r['functions'] ? json_decode($r['functions'], true) : array(); ?>
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
                <span class="tit-conf tit-c-<?php echo esc_attr($r['confidence']); ?>"><?php echo esc_html($r['confidence']); ?></span>
                · <a href="<?php echo esc_url($r['source_url']); ?>" rel="nofollow noopener" target="_blank"><?php echo esc_html($r['source_name']); ?></a>
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

    <script type="application/ld+json"><?php
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
        }, array_slice($rows, 0, 10)),
      ), JSON_UNESCAPED_SLASHES);
    ?></script>
    <?php
    get_footer();
}

/** Title and canonical, so the page is indexable on its own terms. */
function tit_company_title($title) {
    $key = get_query_var('tit_company');
    if (!$key) return $title;
    $rows = tit_company_rows(rawurldecode(sanitize_text_field($key)));
    if (!$rows) return $title;
    return $rows[0]['company'] . ' — hiring, funding and leadership signals';
}
add_filter('pre_get_document_title', 'tit_company_title');
