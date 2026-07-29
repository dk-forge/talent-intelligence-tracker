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
 * Last run per collector, keyed by the name used in source_registry.
 *
 * Only running sources have one. A researched source has never run, and
 * inventing a dash for it would imply it was tried and returned nothing.
 */
function tit_sources_health_map() {
    $health = get_option('tit_source_health', array());
    if (!is_array($health)) return array();

    // The registry names a source for a reader; the pipeline names its
    // collector for a machine. This is the join between them.
    $by_collector = array(
        'google_news' => 'Google News RSS',
        'gdelt'       => 'GDELT DOC 2.0',
        'sec_edgar'   => 'SEC EDGAR 8-K (Item 5.02)',
        'sec_form_d'  => 'SEC EDGAR Form D',
        'ats_boards'  => 'Employer job boards (Greenhouse, Lever, Ashby)',
    );

    $out = array();
    foreach ($health as $key => $row) {
        $name = $by_collector[$key] ?? '';
        if ($name !== '') $out[$name] = $row;
    }
    return $out;
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

    $health = tit_sources_health_map();
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

      <h1>Where this data comes from</h1>
      <p class="tit-note">
        <?php
        // Lead with what is READ versus merely researched. "160 sources" was
        // the skimmer's takeaway before, and it read as coverage we do not
        // have. Both numbers are computed from the registry, never typed.
        printf(
            esc_html('We read %1$s %2$s today. The other %3$s listed here are researched and queued, not yet read.'),
            esc_html(number_format_i18n(count($live))),
            count($live) === 1 ? 'source' : 'sources',
            esc_html(number_format_i18n(count($cand)))
        );
        ?>
        Every record on this tracker links to the document that makes the
        claim, and this page is generated from the collectors themselves so it
        cannot drift from what actually runs.
      </p>

      <div class="tit-stats">
        <div class="tit-stat"><span class="tit-n"><?php echo count($live); ?></span><span class="tit-l">running now</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo count($cand); ?></span><span class="tit-l">researched</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo count($countries); ?></span><span class="tit-l">countries</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo count($categories); ?></span><span class="tit-l">categories</span></div>
      </div>

      <div class="tit-callout">
        <strong>Country here means where a source is based, not where we have
        coverage.</strong> Most of what we read is not tied to one country:
        Google News is read in 25 national editions across 7 languages, and
        GDELT is machine-translated from 65. Those are filed as Worldwide, so
        filtering by country narrows to sources that are specific to it rather
        than to everything that covers it.
      </div>

      <div class="tit-callout">
        <strong>What "running now" means.</strong> A source counts as running
        only when a collector reads it, reports its health, and has a passing
        test. Everything else is listed as researched so the roadmap is public,
        and is never counted as coverage. A source appearing on this page is not
        a claim that we cover it.
      </div>

      <div class="tit-callout">
        <strong>A list of sources is not evidence of coverage.</strong> So we
        also measure what these collectors miss, against a fixed set of real
        events assembled from public sources without ever looking at our own
        database, and publish the result including the categories where we come
        off badly.
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/recall/')); ?>">See the measured recall</a>.
      </div>

      <div class="tit-callout">
        <strong>What we exclude, and what that costs.</strong> A Form D reports
        money raised, and most Form D filers are not employers: pooled
        investment funds, single-asset property vehicles, and insurance or
        annuity products where the "amount sold" is premium collected from
        policyholders. All three are excluded. Form D filings in the
        real-estate industry group are excluded outright, because the
        overwhelming majority of them are single-asset vehicles &mdash; this
        does drop a small number of genuine real-estate employers along with
        them, and the dataset offers no field that separates the two. Funding
        records also carry no hiring badge, because a filing states an amount
        and says nothing about headcount.
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/corrections/')); ?>">See the corrections log</a>.
      </div>

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
            <th>Source</th><th>Status</th><th>Last run</th><th>Covers</th><th>Signals</th><th>Where</th>
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
              <td data-label="Signals"><?php echo esc_html($sig); ?></td>
              <td class="tit-meta" data-label="Where"><?php echo esc_html($s['country'] ?: 'Worldwide'); ?></td>
            </tr>
          <?php endforeach; ?>
          </tbody>
        </table>
      </div>

      <p class="tit-cite">
        Layoff and redundancy data is deliberately not collected here. It is read
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
    return get_query_var('tit_sources')
        ? 'Sources — Talent Intelligence Tracker'
        : $title;
}
add_filter('pre_get_document_title', 'tit_sources_title');
