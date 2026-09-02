<?php
/**
 * The sources page: /talent-intelligence-tracker/sources/
 *
 * Generated from the pipeline's source registry, never hand-maintained — a
 * hand-written coverage table drifts from reality within a week, and a table
 * that implies coverage we do not have is a lie told in a grid.
 *
 * The live/candidate split is the honesty mechanism. A source is "live" only
 * when a connector runs, reports health, and has a passing test. Everything
 * else is published as roadmap, clearly marked.
 */

if (!defined('ABSPATH')) exit;

const TIT_SOURCES_PATH = 'talent-intelligence-tracker/sources';

function tit_sources_rewrite() {
    add_rewrite_rule('^' . TIT_SOURCES_PATH . '/?$', 'index.php?tit_sources=1', 'top');
}
add_action('init', 'tit_sources_rewrite');

function tit_sources_query_var($vars) {
    $vars[] = 'tit_sources';
    return $vars;
}
add_filter('query_vars', 'tit_sources_query_var');

function tit_sources_url() {
    return home_url('/' . TIT_SOURCES_PATH . '/');
}

/** The catalogue, written by the pipeline. */
function tit_sources_data() {
    $file = TIT_PATH . 'data/sources.json';
    if (!is_readable($file)) return array();
    $data = json_decode(file_get_contents($file), true);
    return is_array($data) ? $data : array();
}

function tit_sources_template() {
    if (!get_query_var('tit_sources')) return;
    tit_sources_render(tit_sources_data());
    exit;
}
add_action('template_redirect', 'tit_sources_template');

/**
 * The registry names a source for a reader; the pipeline names its collector
 * for a machine. This is the join between them, and it is DERIVED from the
 * generated catalogue rather than typed here.
 *
 * It used to be a hand-written five-entry map beside nine live collectors, so
 * `national_press` (the largest source by items found), `sec_execcomp` and
 * `uk_paygap` — which between them supply most of the rows in the database, and
 * 4,761 of the UK's 4,793 — each rendered "not yet reported" on a page whose
 * whole job is saying what actually runs. A source added to the registry now
 * arrives here with its collector attached; a hand-typed copy could not.
 *
 * Collectors deliberately absent stay absent, because they are not sources:
 * `archive_sources` and `link_check` maintain the ledger behind the links,
 * `recall` measures what we miss, and `sec_form_d_bulk` is a backfill of a
 * source already listed. None of them reads a new document, so none of them
 * belongs in a table of where the data comes from.
 */
function tit_sources_collector_map($sources) {
    $out = array();
    foreach ($sources as $s) {
        $collector = trim((string) ($s['collector'] ?? ''));
        if ($collector !== '' && ($s['status'] ?? '') === 'live') {
            $out[$collector] = $s['name'];
        }
    }
    return $out;
}

/**
 * Last run per collector, keyed by the name shown on the page.
 *
 * Only running sources have one. A researched source has never run, and
 * inventing a dash for it would imply it was tried and returned nothing.
 */
function tit_sources_health_map($sources = null) {
    $health = get_option('tit_source_health', array());
    if (!is_array($health)) return array();

    if ($sources === null) $sources = tit_sources_data();
    $by_collector = tit_sources_collector_map($sources);

    $out = array();
    foreach ($health as $key => $row) {
        $name = $by_collector[$key] ?? '';
        if ($name !== '') $out[$name] = $row;
    }
    return $out;
}

/**
 * How much of what we cite carries a saved copy, and how much of it needs one.
 *
 * WHY THIS IS COMPUTED AND NOT WRITTEN DOWN.
 *
 * The reader-facing half of the link-rot work is an "Archived" link on a record
 * card, and it is printed only where a snapshot actually exists. Today that is a
 * small share of rows, which without a sentence beside it reads as a gap: 99% of
 * the page apparently missing something the other 1% has. It is not a gap. The
 * overwhelming majority of what we cite is filings held by regulators and
 * government registers, whose publishers keep them on file, and copying those to
 * a third party preserves nothing that is not already preserved.
 *
 * So the page has to say which share is which, and it has to keep saying it
 * correctly while the archived figure climbs. Every number here is a count.
 *
 * THE SPLIT IS DERIVED, NOT TYPED. A hand-written list of "the SEC ones" in PHP
 * is the same mistake the collector map made: it was typed with five of nine
 * entries and three collectors that run twice a day rendered as never having
 * run. `data/sources.json` already carries a category per collector, written by
 * build_sources_json.py from the registry, and the filing systems are exactly
 * the categories whose name ends in "filings". A collector added tomorrow
 * arrives here classified; a typed copy could not.
 *
 * WHAT THIS DELIBERATELY DOES NOT CLAIM. The archive ledger (`source_links`)
 * lives in the pipeline database and knows three states: archived, asked for and
 * still pending, and confirmed to have no copy available. Only the first of
 * those reaches WordPress, as the `archive_url` on a row. So a document with no
 * Archived link here means "no copy on file", never "no copy exists" and never
 * "we looked and there was none". ops_status [2c] draws that distinction (it
 * separates "never answered about" from "confirmed absent") and this page must
 * not flatten it into an absence it cannot see. Hence the closing sentence.
 *
 * One query, cached. The sources page cost zero before this and costs one now.
 */
function tit_sources_archive_facts($sources) {
    $cached = get_transient('tit_sources_archive');
    if (is_array($cached)) return $cached;

    global $wpdb;
    $table = tit_table_name();

    // COUNT(DISTINCT source_url), because the ledger is keyed on the URL and
    // never on the row: thousands of SEC rows sit behind a handful of index
    // pages, and one snapshot serves all of them. Counting rows would report a
    // corpus we do not have and a coverage share that is not the one the
    // archiver is working through.
    $rows = $wpdb->get_results(
        "SELECT collector,
                COUNT(DISTINCT source_url) AS urls,
                COUNT(DISTINCT CASE WHEN archive_url IS NOT NULL AND archive_url <> ''
                                    THEN source_url END) AS archived
           FROM {$table}
          WHERE is_current = 1
       GROUP BY collector",
        ARRAY_A
    );

    $category = array();
    foreach ((array) $sources as $s) {
        $c = trim((string) ($s['collector'] ?? ''));
        if ($c !== '') $category[$c] = (string) ($s['category'] ?? '');
    }

    $out = array('total' => 0, 'archived' => 0, 'filed' => 0, 'perishable' => 0,
                 'perishable_archived' => 0);
    foreach ((array) $rows as $r) {
        $urls = (int) $r['urls'];
        $arch = (int) $r['archived'];
        $out['total']    += $urls;
        $out['archived'] += $arch;
        // A collector we cannot classify counts as perishable. That is the safe
        // direction: it overstates what needs preserving and never claims a
        // publisher keeps something on our behalf.
        $cat = $category[$r['collector']] ?? '';
        if ($cat !== '' && substr($cat, -8) === ' filings') {
            $out['filed'] += $urls;
        } else {
            $out['perishable']          += $urls;
            $out['perishable_archived'] += $arch;
        }
    }

    set_transient('tit_sources_archive', $out, 2 * HOUR_IN_SECONDS);
    return $out;
}

/**
 * The collection-rate chart, moved here from the dashboard on 2026-08-05.
 *
 * "Updates Collected a Day" plots OUR OWN collection rate: it moves when the
 * market moves and when we start reading somewhere new, and no reader can tell
 * which from the lines. That makes it an operations measure, and this page,
 * which lists the collectors and their last runs, is where an operations
 * measure belongs. The dashboard's slot now carries a market trend built on a
 * fixed source panel instead (tit_market_trend in shortcodes.php).
 *
 * Moved, not deleted, and not recomputed differently: this is the same
 * tit_signal_trend() the dashboard used, whole-tracker default clause, with
 * the tap-to-filter sentence off because this page has no filters and loads
 * no dashboard.js. Two queries cold, cached in its own transient; every write
 * route flushes tit_ transients, so a fresh run appears immediately.
 */
function tit_sources_trend_html() {
    if (!function_exists('tit_signal_trend') || !function_exists('tit_signal_trend_html')) {
        return '';
    }
    $cached = get_transient('tit_sources_trend');
    if (is_string($cached)) return $cached;

    $html = tit_signal_trend_html(tit_signal_trend(tit_table_name()), false);
    set_transient('tit_sources_trend', $html,
        defined('TIT_CACHE_TTL') ? TIT_CACHE_TTL : 5 * MINUTE_IN_SECONDS);
    return $html;
}

function tit_sources_last_run($row) {
    if (empty($row['run_at'])) return '';
    $ts = strtotime($row['run_at'] . (str_ends_with($row['run_at'], 'Z') ? '' : ' UTC'));
    if (!$ts) return '';
    $ago = human_time_diff($ts, time());
    return sprintf(
        '%s ago · found %s, stored %s',
        $ago,
        number_format_i18n((int) ($row['items_found'] ?? 0)),
        number_format_i18n((int) ($row['items_stored'] ?? 0))
    );
}

function tit_sources_render($sources) {
    // Registry-derived sources carry ISO codes while catalogue rows carry full
    // names, so the country filter read "CA, DE, ES, FR, GB" next to "France"
    // and "Slovenia", and the same country appeared twice under two spellings.
    // Normalise to names once, here, so the facet list, the filter values and
    // the Where column can never disagree. An unknown code falls through to
    // itself rather than to a guess.
    foreach ($sources as &$s) {
        $c = trim((string) ($s['country'] ?? ''));
        if (preg_match('/^[A-Z]{2}$/', $c) && function_exists('tit_country_name')) {
            $s['country'] = tit_country_name($c);
        }
    }
    unset($s);

    $health = tit_sources_health_map($sources);
    $live = array_values(array_filter($sources, fn($s) => ($s['status'] ?? '') === 'live'));
    // Counted apart from "researched" on purpose. A backstop country IS
    // collected twice a day, so calling it research understates it; but there
    // is no publisher behind the name, so calling it a live source would imply
    // a relationship with an outlet that does not exist.
    $back = array_values(array_filter($sources, fn($s) => ($s['status'] ?? '') === 'backstop'));
    $cand = array_values(array_filter(
        $sources,
        fn($s) => ($s['status'] ?? '') !== 'live' && ($s['status'] ?? '') !== 'backstop'
    ));

    $countries  = array_values(array_unique(array_filter(array_column($sources, 'country'))));
    $categories = array_values(array_unique(array_filter(array_column($sources, 'category'))));
    sort($countries); sort($categories);

    // Never get_header() directly: this site runs a BLOCK theme, which has no
    // header.php, and the classic call silently degrades to a bare site-title
    // link with no logo and no navigation. See tit_render_header().
    if (function_exists('tit_render_header')) tit_render_header(); else get_header();
    ?>
    <div class="tit-wrap tit-sources" id="tit-sources">
      <nav class="tit-crumb">
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Talent Intelligence Tracker</a>
        <span aria-hidden="true">›</span> Sources
      </nav>

      <h1 data-tit-route-heading>Where this data comes from</h1>
      <p class="tit-note">
        <?php
        // Lead with what is READ versus merely researched. "160 sources" was
        // the skimmer's takeaway before, and it read as coverage we do not
        // have. Both numbers are computed from the registry, never typed.
        printf(
            esc_html('We read %1$s %2$s today. We have researched and queued another %3$s listed here, and we do not read those yet.'),
            esc_html(number_format_i18n(count($live))),
            count($live) === 1 ? 'source' : 'sources',
            esc_html(number_format_i18n(count($cand)))
        );
        ?>
        Every record on this tracker links to the document that makes the
        claim. We build this page from the collectors themselves, so it cannot
        drift from what actually runs.
      </p>

      <div class="tit-stats">
        <div class="tit-stat"><span class="tit-n"><?php echo count($live); ?></span><span class="tit-l">running now</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo count($cand); ?></span><span class="tit-l">researched</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo count($countries); ?></span><span class="tit-l">countries</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo count($categories); ?></span><span class="tit-l">categories</span></div>
      </div>

      <div class="tit-callout">
        <strong>Country here means the home country of a source, not the places
        we cover.</strong> Most of what we read is not tied to one country. We
        read Google News in each national edition's own language (the edition
        and language count is on its row below), and we ask GDELT for
        English-language articles only. We file those as Worldwide, so
        filtering by country narrows to sources that are specific to it rather
        than to everything that covers it.
      </div>

      <div class="tit-callout">
        <strong>What "running now" means.</strong> A source counts as running
        only when a collector reads it, reports its health, and has a passing
        test. We list everything else as researched, so the roadmap is public,
        and we never count it as coverage. A source appearing on this page is not
        a claim that we cover it.
      </div>

      <?php
      /*
       * The archived-copy line.
       *
       * It exists because of what the dashboard now shows: an "Archived" link
       * beside the source on the records that have a saved copy, and nothing on
       * the ones that do not. Printed without this paragraph, a sparse link
       * reads as a hole in a page whose entire claim is that every figure still
       * reaches its document. It is not a hole, and the reason is countable.
       *
       * The wording has to survive the figure moving. The archiver is working
       * through the perishable tail, so the share below climbs on its own; every
       * number is a count and none of the sentences depend on the share being
       * small. It reads the same at half a percent and at forty.
       *
       * THE SPLIT IS ALWAYS SAID. THE COVERAGE FIGURE IS ONLY SAID WHEN THERE IS
       * ONE. The two sentences answer different questions and only one of them
       * depends on a snapshot existing. "Most of what we cite needs no copy" is
       * true of this corpus whether or not a single capture has landed, and it is
       * the sentence a reader needs. "N documents carry a copy" is a claim about
       * a link on the dashboard, and at N = 0 there is no link to explain, so
       * printing "0 of 12,970 (0.0%)" would be a paragraph about a feature the
       * reader cannot see. Measured 2026-07-30: the pipeline held 72 snapshots
       * and the live table held none of them, because they travel here as a
       * later enrichment rather than with the row. That is a real state this
       * page has to render honestly rather than a hypothetical.
       */
      $arc = tit_sources_archive_facts($sources);
      if ($arc['total'] > 0) : ?>
        <div class="tit-callout">
          <strong>We save a copy of the citations that can disappear.</strong>
          <?php
          printf(
              esc_html('Of the %1$s documents cited on this tracker, %2$s are filings held by '
                     . 'regulators and government registers. Those bodies keep their own copies, '
                     . 'so a second copy of a filing preserves nothing. The other %3$s come from '
                     . 'news publishers and employer sites, which unpublish stories, change their '
                     . 'URL schemes and let domains lapse, and those are the ones worth saving.'),
              esc_html(number_format_i18n($arc['total'])),
              esc_html(number_format_i18n($arc['filed'])),
              esc_html(number_format_i18n($arc['perishable']))
          );
          if ($arc['archived'] > 0) {
              printf(
                  ' ' . esc_html('%1$s of all cited documents (%2$s) carry a copy at the Internet '
                               . 'Archive, and the records behind them show an "Archived" link '
                               . 'beside the publisher\'s own. A record with no such link is not a '
                               . 'record whose document has gone. We record a copy we hold, never '
                               . 'an absence we have checked for, so the missing ones are mostly '
                               . 'documents nobody has asked the archive about yet.'),
                  esc_html(number_format_i18n($arc['archived'])),
                  esc_html(number_format_i18n($arc['archived'] / $arc['total'] * 100, 1) . '%')
              );
          } else {
              echo ' ' . esc_html('None of them carries a saved copy on this site yet. When one '
                                . 'does, an "Archived" link appears beside the publisher\'s own, '
                                . 'and only on the records that actually have one.');
          }
          ?>
        </div>
      <?php endif; ?>

      <div class="tit-callout">
        <strong>A list of sources is not evidence of coverage.</strong> So we
        also measure what these collectors miss. The test uses a fixed set of
        real events, assembled from public sources without ever looking at our
        own database. We publish the result, including the categories where we
        come off badly.
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/recall/')); ?>">See the measured recall</a>.
      </div>

      <div class="tit-callout">
        <strong>What we exclude, and what that costs.</strong> A Form D reports
        money raised, and most Form D filers are not employers. They are pooled
        investment funds, single-asset property vehicles, and insurance or
        annuity products where the "amount sold" is premium collected from
        policyholders. We exclude all three. We also exclude Form D filings in
        the real-estate industry group outright, because the overwhelming
        majority of them are single-asset vehicles. This does drop
        a small number of genuine real-estate employers along with them, and the
        dataset offers no field that separates the two. Funding
        records also carry no hiring badge, because a filing states an amount
        and says nothing about headcount.
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/corrections/')); ?>">See the corrections log</a>.
      </div>

      <?php $tit_trend_html = tit_sources_trend_html(); ?>
      <?php if ($tit_trend_html !== '') : ?>
        <h2>Updates Collected a Day</h2>
        <p class="tit-note">
          How many updates these collectors stored per day, smoothed over seven
          days. This is a measure of our own reading, not of the market. The
          lines move when the market moves, and also when we start reading
          somewhere new. That is why this chart lives here, beside the
          collectors it describes. The dashboard carries the market view,
          counted so that a new source cannot appear as a market move.
        </p>
        <?php echo $tit_trend_html; // phpcs:ignore - built and escaped in tit_signal_trend_html ?>
      <?php endif; ?>

      <div class="tit-filters">
        <select id="tit-s-status" aria-label="Filter by status">
          <option value="">Running and researched</option>
          <option value="live">Running now</option>
          <option value="backstop">Discovery backstop</option>
          <option value="candidate">Researched only</option>
        </select>
        <select id="tit-s-country" aria-label="Filter by country">
          <option value="">Any country</option>
          <?php foreach ($countries as $c) : ?>
            <option value="<?php echo esc_attr($c); ?>"><?php echo esc_html($c); ?></option>
          <?php endforeach; ?>
        </select>
        <select id="tit-s-category" aria-label="Filter by category">
          <option value="">Any category</option>
          <?php foreach ($categories as $c) : ?>
            <option value="<?php echo esc_attr($c); ?>"><?php echo esc_html($c); ?></option>
          <?php endforeach; ?>
        </select>
        <input type="search" id="tit-s-q" placeholder="Search sources" aria-label="Search sources">
      </div>

      <?php
      // Never "160 sources": the honest skim line is the live/researched
      // split, computed from the same registry the table renders.
      ?>
      <p class="tit-note" id="tit-s-count"><?php
        printf(
            '%s live %s, %s %s reached by discovery search, %s researched',
            esc_html(number_format_i18n(count($live))),
            count($live) === 1 ? 'collector' : 'collectors',
            esc_html(number_format_i18n(count($back))),
            count($back) === 1 ? 'country' : 'countries',
            esc_html(number_format_i18n(count($cand)))
        );
      ?></p>

      <div class="tit-table-scroll">
        <table class="tit-table">
          <thead><tr>
            <th>Source</th><th>Status</th><th>Last run</th><th>Covers</th><th>Entries</th><th>Where</th>
          </tr></thead>
          <tbody id="tit-s-rows">
          <?php foreach ($sources as $s) :
            $sig = implode(', ', array_slice($s['signals'] ?? array(), 0, 4)); ?>
            <tr data-status="<?php echo esc_attr($s['status']); ?>"
                data-country="<?php echo esc_attr($s['country']); ?>"
                data-category="<?php echo esc_attr($s['category']); ?>"
                data-search="<?php echo esc_attr(strtolower($s['name'] . ' ' . $s['category'] . ' ' . $sig)); ?>">
              <td class="tit-headline" data-label="Source">
                <?php // A backstop row names a COUNTRY, not a publisher, so there
                      // is no site to link to and inventing one would be the
                      // exact implication this row exists to avoid. ?>
                <?php if (!empty($s['url'])) : ?>
                  <span class="tit-h"><a href="<?php echo esc_url($s['url']); ?>"
                     rel="nofollow noopener" target="_blank"><?php echo esc_html($s['name']); ?></a></span>
                <?php else : ?>
                  <span class="tit-h"><?php echo esc_html($s['name']); ?></span>
                <?php endif; ?>
                <?php if (!empty($s['notes'])) : ?>
                  <span class="tit-rt"><?php echo esc_html($s['notes']); ?></span>
                <?php endif; ?>
              </td>
              <td class="tit-meta" data-label="Status">
                <?php
                $h = $health[$s['name']] ?? null;
                $state = $h['status'] ?? '';
                if ($s['status'] === 'backstop') : ?>
                  <span class="tit-conf">discovery backstop</span>
                <?php elseif ($s['status'] !== 'live') : ?>
                  <span class="tit-conf">researched</span>
                <?php elseif ($state === 'degraded' || $state === 'error') : ?>
                  <span class="tit-conf tit-c-degraded">running, degraded</span>
                <?php else : ?>
                  <span class="tit-conf tit-c-verified">running now</span>
                <?php endif; ?>
              </td>
              <td class="tit-lastrun tit-meta" data-label="Last run">
                <?php
                // Blank, not a dash, for a source that has never run: a dash
                // reads as "tried and found nothing".
                $when = $h ? tit_sources_last_run($h) : '';
                if ($when) {
                    echo esc_html($when);
                    if (!empty($h['detail'])) {
                        echo '<span class="tit-rt">' . esc_html($h['detail']) . '</span>';
                    }
                } elseif ($s['status'] === 'live') {
                    echo '<span class="tit-nowhere">not yet reported</span>';
                }
                ?>
              </td>
              <!-- Covers and Signals stay full-width labelled rows: they are
                   descriptive text, not the short self-describing values that
                   belong on the meta line. -->
              <td data-label="Covers"><?php echo esc_html($s['coverage'] ?? ''); ?></td>
              <td data-label="Entries"><?php echo esc_html($sig); ?></td>
              <td class="tit-meta" data-label="Where"><?php echo esc_html($s['country'] ?: 'Worldwide'); ?></td>
            </tr>
          <?php endforeach; ?>
          </tbody>
        </table>
      </div>

      <p class="tit-cite">
        We deliberately do not collect layoff and redundancy data here. We read it
        from the <a href="https://asktherecruiter.com/blog/ai-layoff-tracker/">AI
        Layoff Tracker</a>, so there is one source of truth per fact.
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/corrections/')); ?>">Corrections</a>
        &middot;
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Back to the tracker</a>
      </p>
    </div>

    <script>
    /* Filtering is client-side: the whole catalogue is already in the page, so
       there is nothing to fetch and it works with JavaScript disabled too (all
       rows simply stay visible). */
    (function () {
      var rows = Array.prototype.slice.call(document.querySelectorAll('#tit-s-rows tr'));
      var count = document.getElementById('tit-s-count');
      var f = {
        status: document.getElementById('tit-s-status'),
        country: document.getElementById('tit-s-country'),
        category: document.getElementById('tit-s-category')
      };
      var q = document.getElementById('tit-s-q');

      function apply() {
        var term = (q.value || '').trim().toLowerCase();
        var liveShown = 0, backShown = 0, candShown = 0, shown = 0;
        rows.forEach(function (tr) {
          var ok = (!f.status.value   || tr.dataset.status   === f.status.value)
                && (!f.country.value  || tr.dataset.country  === f.country.value)
                && (!f.category.value || tr.dataset.category === f.category.value)
                && (!term || tr.dataset.search.indexOf(term) !== -1);
          tr.style.display = ok ? '' : 'none';
          if (ok) {
            shown++;
            if (tr.dataset.status === 'live') liveShown++;
            else if (tr.dataset.status === 'backstop') backShown++;
            else candShown++;
            /* Re-stripe by class: display:none rows still count for CSS
               nth-child, so a filtered table would stripe unevenly. */
            tr.classList.toggle('tit-even', shown % 2 === 0);
          } else {
            tr.classList.remove('tit-even');
          }
        });
        /* Same shape as the server-rendered line: live vs researched, never a
           bare "N sources" that reads as coverage. */
        count.textContent = liveShown +
          (liveShown === 1 ? ' live collector, ' : ' live collectors, ') +
          backShown +
          (backShown === 1 ? ' country reached by discovery search, '
                           : ' countries reached by discovery search, ') +
          candShown + ' researched';
      }

      Object.keys(f).forEach(function (k) { f[k].addEventListener('change', apply); });
      q.addEventListener('input', apply);
      apply(); /* initial stripes */
    })();
    </script>
    <?php
    if (function_exists('tit_render_footer')) tit_render_footer(); else get_footer();
}

function tit_sources_title($title) {
    // A middot, not an em dash. docs/HANDOVER.md bans em dashes in UI copy and a
    // document title is UI copy; five routes had FOUR different separators
    // between them (em dash here and on /corrections/, a pipe on /recall/, a
    // middot on /places/, a colon on the company and place pages).
    return get_query_var('tit_sources')
        ? 'Sources · Talent Intelligence Tracker'
        : $title;
}
add_filter('pre_get_document_title', 'tit_sources_title');

/**
 * What this page is, for a crawler and for a share card.
 *
 * Counted from the catalogue the table renders from, never typed: the whole
 * argument of this page is that it cannot drift from what actually runs, and a
 * description claiming a source count somebody wrote down would be the one part
 * of it that could.
 */
function tit_sources_head() {
    if (!get_query_var('tit_sources')) return;
    if (!function_exists('tit_head_description')) return;

    $sources = tit_sources_data();
    $live = 0; $cand = 0; $countries = array();
    foreach ($sources as $s) {
        if (($s['status'] ?? '') === 'live') $live++;
        elseif (($s['status'] ?? '') !== 'backstop') $cand++;
        if (!empty($s['country'])) $countries[$s['country']] = true;
    }
    tit_head_description(sprintf(
        'The %d %s the Talent Intelligence Tracker reads today, and the %s more '
        . 'that we have researched but do not yet read. A source counts as running '
        . 'only when a collector reads it, reports its health and has a passing test.',
        $live, $live === 1 ? 'source' : 'sources', number_format_i18n($cand)
    ));
    echo '<link rel="canonical" href="' . esc_url(tit_sources_url()) . '" />' . "\n";
}
add_action('wp_head', 'tit_sources_head', 1);
