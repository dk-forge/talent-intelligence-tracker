<?php
/**
 * Renders the dashboard. Server-side first so the page is useful (and
 * indexable) before any JavaScript runs; the filters then talk to /query.
 *
 * UI copy here uses no em-dashes, matching the house style.
 */

if (!defined('ABSPATH')) exit;

function tit_dashboard_shortcode() {
    // Enqueue from INSIDE the shortcode as well as from wp_enqueue_scripts.
    // The hook's guard asks has_shortcode($post->post_content, ...), which is
    // FALSE whenever the shortcode reaches the page through a block, pattern,
    // template part or reusable block rather than sitting raw in post_content.
    // The dashboard then rendered with no stylesheet at all -- every tit- class
    // inert, the page raw HTML (observed live 2026-07-28). Enqueuing where the
    // markup is actually produced cannot drift from where it is used.
    if (function_exists('tit_enqueue_dashboard_assets')) tit_enqueue_dashboard_assets();
    global $wpdb;
    $table = tit_table_name();

    $total = (int) $wpdb->get_var("SELECT COUNT(*) FROM {$table} WHERE is_current = 1");

    ob_start();

    if ($total === 0) {
        ?>
        <div class="tit-wrap">
          <div class="tit-empty">
            <h2>No signals published yet</h2>
            <p>Collection has not been switched on. Every record here has to
               link to the article that makes the claim, and that sourcing is
               still being proven out, so an empty table is the honest state
               rather than a broken one.</p>
            <p class="tit-empty-note">This page will fill in once collection is
               armed. It will not be backfilled with anything unsourced.</p>
          </div>
        </div>
        <?php
        return ob_get_clean();
    }

    $by_pillar = $wpdb->get_results(
        "SELECT pillar, COUNT(*) n FROM {$table} WHERE is_current = 1 GROUP BY pillar ORDER BY n DESC",
        ARRAY_A
    ) ?: array();

    $countries = (int) $wpdb->get_var(
        "SELECT COUNT(DISTINCT COALESCE(country, hq_country)) FROM {$table} WHERE is_current = 1"
    );
    $companies = (int) $wpdb->get_var(
        "SELECT COUNT(DISTINCT company_key) FROM {$table} WHERE is_current = 1"
    );
    $verified = (int) $wpdb->get_var(
        "SELECT COUNT(*) FROM {$table} WHERE is_current = 1 AND confidence = 'verified'"
    );

    // Bounds for the date inputs. The sibling can offer years, quarters and
    // months because it holds years; we hold days. Letting the control ask for
    // a period we have nothing in is a control that manufactures empty states
    // and makes thin coverage look like a broken filter.
    $span = $wpdb->get_row(
        "SELECT MIN(COALESCE(published_date, DATE(captured_at))) lo,
                MAX(COALESCE(published_date, DATE(captured_at))) hi
           FROM {$table} WHERE is_current = 1", ARRAY_A) ?: array();
    $span_lo = $span['lo'] ?? '';
    $span_hi = $span['hi'] ?? '';

    $newest_run = $wpdb->get_var("SELECT MAX(captured_at) FROM {$table} WHERE is_current = 1");
    $glance = tit_glance_matrix($table);
    $counts_by_country = array_column($wpdb->get_results(
        "SELECT COALESCE(country, hq_country) k, COUNT(*) n FROM {$table}
          WHERE is_current = 1 AND COALESCE(country, hq_country) IS NOT NULL
          GROUP BY k", ARRAY_A) ?: array(), 'n', 'k');
    // 40, matching /aggregate, not 6. The chart scrolls and expands, so a short
    // list is no longer what keeps the card small -- and a hard six meant the
    // World view could not show two of the eight countries we actually hold.
    $by_country = $wpdb->get_results(
        "SELECT COALESCE(country, hq_country) k, COUNT(*) n FROM {$table}
          WHERE is_current = 1 AND COALESCE(country, hq_country) IS NOT NULL
          GROUP BY k ORDER BY n DESC LIMIT 40", ARRAY_A) ?: array();
    $by_direction = $wpdb->get_results(
        "SELECT signal_direction k, COUNT(*) n FROM {$table} WHERE is_current = 1
          GROUP BY signal_direction ORDER BY n DESC", ARRAY_A) ?: array();

    $rows = $wpdb->get_results(
        "SELECT signal_id, headline, talent_readthrough, company, company_key, pillar, signal_direction,
                city, country, hq_city, hq_country, confidence, source_url, source_name,
                published_date
           FROM {$table} WHERE is_current = 1
          ORDER BY COALESCE(published_date, DATE(captured_at)) DESC, row_id DESC
          LIMIT 50",
        ARRAY_A
    ) ?: array();

    // Recruiter language, not ours. "Pillar" and "signal direction" are
    // internal vocabulary and never appear on the page.
    $labels = array(
        'company_development' => 'Growing and expanding',
        'leadership_change'   => 'Leadership moves',
        'rewards_comp'        => 'Pay and benefits',
        'how_we_work'         => 'Ways of working',
    );
    $directions = array(
        'hiring'       => 'Hiring up',
        'displacement' => 'Cutting back',
        'comp_shift'   => 'Pay change',
        'neutral'      => 'Other change',
    );
    $functions = array(
        'engineering' => 'Engineering', 'data_ai' => 'Data & AI',
        'it_infrastructure' => 'IT & infrastructure', 'product' => 'Product',
        'design' => 'Design', 'finance' => 'Finance', 'hr_people' => 'HR & people',
        'sales' => 'Sales', 'marketing' => 'Marketing',
        'customer_support' => 'Customer support', 'operations' => 'Operations',
        'supply_chain' => 'Supply chain', 'manufacturing' => 'Manufacturing',
        'legal_compliance' => 'Legal & compliance', 'research' => 'Research',
        'clinical_healthcare' => 'Clinical & healthcare', 'executive' => 'Executive',
    );
    $industries = array(
        'technology' => 'Technology', 'financial_services' => 'Financial services',
        'healthcare' => 'Healthcare', 'pharma_biotech' => 'Pharma & biotech',
        'retail_ecommerce' => 'Retail & e-commerce', 'manufacturing' => 'Manufacturing',
        'energy_utilities' => 'Energy & utilities', 'telecom' => 'Telecom',
        'media_entertainment' => 'Media & entertainment',
        'transport_logistics' => 'Transport & logistics',
        'professional_services' => 'Professional services',
        'public_sector' => 'Public sector', 'hospitality_travel' => 'Hospitality & travel',
        'education' => 'Education', 'food_beverage' => 'Food & beverage',
        'automotive' => 'Automotive', 'aerospace_defence' => 'Aerospace & defence',
        'real_estate_construction' => 'Real estate & construction',
    );
    ?>
    <!--
      The config also rides on the element, not only on wp_localize_script.
      Autoptimize aggregates INLINE scripts into a bundle, and our
      autoptimize_filter_js_exclude only protects assets matched by path, so
      dashboard.js stays where it is while the inline `TIT` it depends on gets
      swept into a bundle that loads after it. The script then saw
      `typeof TIT === "undefined"`, returned on its first statement, and every
      filter, the region strip and the quick views did nothing at all on the
      live site while looking perfectly fine. Markup cannot be reordered away
      from the element it describes.
    -->
    <div class="tit-wrap" id="tit-dashboard"
         data-api="<?php echo esc_attr(rest_url('talent/v1/')); ?>"
         data-countries="<?php echo esc_attr(wp_json_encode(tit_country_names())); ?>">

      <div class="tit-hero">
        <div class="tit-hero-top">
          <h2>Who is hiring, who is raising money, and who is changing leadership</h2>
          <?php /* The sibling's freshness shape: the absolute timestamp WITH
                   its timezone is the primary fact ("Live · updated Jul 28,
                   1:51 AM EDT"); the relative time and the next-run note live
                   on Roo's quieter line below. */ ?>
          <div class="tit-live"><span class="tit-live-dot"></span>
            Live<?php if ($newest_run) : ?> · updated
            <?php echo esc_html(tit_local_datetime($newest_run)); ?>
            <?php endif; ?>
          </div>
        </div>

        <div class="tit-roo-row"><?php tit_roo($newest_run); ?></div>

        <div class="tit-glance" id="tit-glance">
          <?php echo tit_glance_matrix_html($glance); ?>
        </div>
        <?php $span_note = tit_span_note($span_lo, $span_hi); ?>
        <?php if ($span_note) : ?>
          <p class="tit-span"><?php echo esc_html($span_note); ?></p>
        <?php endif; ?>

        <p class="tit-hero-fine">
          <span class="tit-fine-figures"><?php
            printf(
              /* translators: totals restated by JavaScript when a filter changes. */
              '%s · %s · %s · %s from official filings. ',
              esc_html(sprintf(_n('%s update', '%s updates', $total, 'tit'), number_format_i18n($total))),
              esc_html(sprintf(_n('%s employer', '%s employers', $companies, 'tit'), number_format_i18n($companies))),
              esc_html(sprintf(_n('%s country', '%s countries', $countries, 'tit'), number_format_i18n($countries))),
              esc_html(number_format_i18n($verified))
            );
          ?></span>Every update links to the document that makes the claim, and no figure
          appears unless the source states it.
          <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/sources/')); ?>">Every source</a>
          · <a href="/blog/ai-layoff-tracker/">Layoffs are tracked separately</a>
        </p>
      </div>

      <div class="tit-sec">
        <h3>The market right now</h3>
        <p>Pick a region to narrow the updates below.</p>
      </div>

      <div class="tit-regions" role="group" aria-label="Filter by region">
        <?php foreach (tit_regions($counts_by_country) as $r) : ?>
          <button type="button" class="tit-region<?php echo $r['codes'] === '' ? ' is-on' : ''; ?>"
                  data-codes="<?php echo esc_attr($r['codes']); ?>">
            <span class="tit-region-flag" aria-hidden="true"><?php echo $r['flag']; ?></span>
            <span class="tit-region-name"><?php echo esc_html($r['name']); ?></span>
            <span class="tit-region-n"><?php echo esc_html(number_format_i18n($r['n'])); ?></span>
          </button>
        <?php endforeach; ?>
      </div>

      <div class="tit-quick" role="group" aria-label="Quick views">
        <span class="tit-quick-label">Quick views</span>
        <button type="button" class="tit-qv" data-qv="">Everything</button>
        <?php
        // Periods are computed, never written down. "2026" in a hardcoded label
        // is a bug with a date on it, and a quarter start typed by hand is
        // three more. All four move with the clock.
        $today = current_time('Y-m-d');
        $qstart = date('Y-m-01', strtotime(date('Y', strtotime($today)) . '-'
                  . sprintf('%02d', (intdiv((int) date('n', strtotime($today)) - 1, 3) * 3) + 1) . '-01'));
        $views = array(
            'This week'  => date('Y-m-d', strtotime($today . ' -6 days')),
            'This month' => date('Y-m-01', strtotime($today)),
            'This quarter' => $qstart,
            date('Y', strtotime($today)) . ' so far' => date('Y-01-01', strtotime($today)),
        );
        foreach ($views as $label => $from) : ?>
          <button type="button" class="tit-qv" data-qv="since=<?php echo esc_attr($from); ?>"><?php echo esc_html($label); ?></button>
        <?php endforeach; ?>
        <button type="button" class="tit-qv" data-qv="direction=hiring">Hiring up</button>
        <button type="button" class="tit-qv" data-qv="funding=1">Raised money</button>
        <button type="button" class="tit-qv" data-qv="pillar=leadership_change">Leadership moves</button>
        <button type="button" class="tit-qv" data-qv="confidence=verified">From official filings</button>
        <select id="tit-f-sort" class="tit-sort" aria-label="Sort the updates">
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="employer">By employer</option>
        </select>
      </div>

      <div class="tit-filters">
        <label class="tit-field tit-field--stack">
          <span class="tit-field-l">Kind of update</span>
          <select id="tit-f-pillar" aria-label="What kind of update">
            <option value="">Anything happening</option>
            <?php foreach ($labels as $k => $v) : ?>
              <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
            <?php endforeach; ?>
          </select>
        </label>
        <label class="tit-field tit-field--stack">
          <span class="tit-field-l">Headcount direction</span>
          <select id="tit-f-direction" aria-label="Is the employer growing or shrinking">
            <option value="">Growing or shrinking</option>
            <?php foreach ($directions as $k => $v) : ?>
              <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
            <?php endforeach; ?>
          </select>
        </label>
        <label class="tit-field tit-field--stack">
          <span class="tit-field-l">Roles affected</span>
          <select id="tit-f-function" aria-label="Which roles are affected">
            <option value="">Any roles</option>
            <?php foreach ($functions as $k => $v) : ?>
              <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
            <?php endforeach; ?>
          </select>
        </label>
        <label class="tit-field tit-field--stack">
          <span class="tit-field-l">Industry</span>
          <select id="tit-f-industry" aria-label="Which industry">
            <option value="">Any industry</option>
            <?php foreach ($industries as $k => $v) : ?>
              <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
            <?php endforeach; ?>
          </select>
        </label>
        <label class="tit-field tit-field--stack">
          <span class="tit-field-l">Country</span>
          <select id="tit-f-country" aria-label="Which country">
            <option value="">Anywhere</option>
          </select>
        </label>
        <label class="tit-field tit-field--stack">
          <span class="tit-field-l">US state</span>
          <select id="tit-f-state" aria-label="Which US state">
            <option value="">Any US state</option>
          </select>
        </label>
        <label class="tit-field tit-field--stack">
          <span class="tit-field-l">How solid</span>
          <select id="tit-f-confidence" aria-label="How solid the record is">
            <option value="">Any confidence</option>
            <option value="verified">From official filings</option>
            <option value="reported">Reported by a publisher</option>
            <option value="rumored">Rumored</option>
          </select>
        </label>
        <!-- The charts already admit they fall back to headquarters. Until now
             the page surfaced that ambiguity without letting anyone resolve it,
             even though the API has taken country_basis all along. -->
        <label class="tit-field tit-field--stack">
          <span class="tit-field-l">Place basis</span>
          <select id="tit-f-country_basis" aria-label="How to decide where a record belongs">
            <option value="any">Place, or the employer's HQ</option>
            <option value="location">Only where the source named a place</option>
          </select>
        </label>
        <label class="tit-field tit-field--stack">
          <span class="tit-field-l">Employer</span>
          <input type="search" id="tit-f-company" placeholder="e.g. Apple"
                 aria-label="Search by employer">
        </label>
        <label class="tit-field tit-field--stack">
          <span class="tit-field-l">Search</span>
          <input type="search" id="tit-f-q" placeholder="Company, industry, keyword…"
                 aria-label="Search headlines and read-throughs">
        </label>
        <label class="tit-field"><span>From</span>
          <input type="date" id="tit-f-since" aria-label="Earliest date"
                 min="<?php echo esc_attr($span_lo); ?>" max="<?php echo esc_attr($span_hi); ?>"></label>
        <label class="tit-field"><span>To</span>
          <input type="date" id="tit-f-until" aria-label="Latest date"
                 min="<?php echo esc_attr($span_lo); ?>" max="<?php echo esc_attr($span_hi); ?>"></label>
      </div>

      <!--
        What is currently being filtered, in words, with a way out of each one.
        Nine controls spread over three rows means a reader can easily be looking
        at a narrowed page without remembering why. This is the sibling's best
        idea and the cheapest thing on the page: the dashboard states its own
        scope instead of leaving the reader to reconstruct it.
      -->
      <div class="tit-active" id="tit-active" hidden>
        <span class="tit-active-label">Filtering</span>
        <span class="tit-active-chips" id="tit-active-chips"></span>
        <button type="button" class="tit-reset" id="tit-reset">Reset all</button>
      </div>

      <div class="tit-charts">
      <div class="tit-chart" id="chart-kind">
        <?php /* Headings name what a recruiter or job seeker GETS from the chart,
                 not what the chart is made of. "What kind of update" described
                 the axis; "What is moving" answers the question they opened the
                 page with. The rows are buttons because they ARE filters:
                 dashboard.js routes a click through the same state as the
                 dropdowns, so the subtitle may promise it. Buttons hold span
                 children only (phrasing content), never divs. */ ?>
        <?php tit_chart_head('What is moving', 'Hiring, funding, leadership changes and pay news, ranked by how much of it we are seeing. Click a row to filter.', 'kind'); ?>
      <div class="tit-pillars">
        <?php foreach ($by_pillar as $p) :
            $key = $p['pillar'];
            $pct = $total ? round(100 * $p['n'] / $total) : 0; ?>
          <button type="button" class="tit-pillar" data-k="<?php echo esc_attr($key); ?>" aria-pressed="false">
            <span class="tit-pillar-head">
              <span class="tit-pillar-name"><?php echo esc_html($labels[$key] ?? $key); ?></span>
              <span class="tit-pillar-n"><?php echo esc_html(number_format_i18n($p['n'])); ?></span>
            </span>
            <span class="tit-bar"><span style="width:<?php echo esc_attr($pct); ?>%"></span></span>
          </button>
        <?php endforeach; ?>
      </div>
      </div>
        <div class="tit-chart" id="chart-place">
          <?php tit_chart_head('Where the jobs are', "Counted where the work sits. When a source does not name a place, the employer's head office stands in. Click a row to filter.", 'place'); ?>
          <div class="tit-rank" tabindex="0" role="group" aria-label="Activity by place">
            <?php
            $cmax = $by_country ? max(array_map('intval', array_column($by_country, 'n'))) : 1;
            foreach ($by_country as $c) : ?>
              <button type="button" class="tit-rank-row" data-k="<?php echo esc_attr($c['k']); ?>" aria-pressed="false">
                <span class="tit-rank-name"><?php echo esc_html(tit_country_name($c['k'])); ?></span>
                <span class="tit-rank-track"><span class="tit-rank-fill"
                  style="width:<?php echo esc_attr(max(4, round(100 * $c['n'] / $cmax))); ?>%"></span></span>
                <span class="tit-rank-n"><?php echo (int) $c['n']; ?></span>
              </button>
            <?php endforeach; ?>
          </div>
        </div>

        <div class="tit-chart" id="chart-direction">
          <?php tit_chart_head('Which way headcount is going', 'Whether each update points to more hiring, fewer roles, or a pay change at that employer. Click a row to filter.', 'direction'); ?>
          <div class="tit-rank" tabindex="0" role="group" aria-label="Activity by direction">
            <?php
            $dmax = $by_direction ? max(array_map('intval', array_column($by_direction, 'n'))) : 1;
            foreach ($by_direction as $d) : ?>
              <button type="button" class="tit-rank-row" data-k="<?php echo esc_attr($d['k']); ?>"
                      data-dir="<?php echo esc_attr($d['k']); ?>" aria-pressed="false">
                <span class="tit-rank-name"><?php echo esc_html($directions[$d['k']] ?? $d['k']); ?></span>
                <span class="tit-rank-track"><span class="tit-rank-fill"
                  style="width:<?php echo esc_attr(max(4, round(100 * $d['n'] / $dmax))); ?>%"></span></span>
                <span class="tit-rank-n"><?php echo (int) $d['n']; ?></span>
              </button>
            <?php endforeach; ?>
          </div>
        </div>
      </div>


      <div class="tit-table-scroll">
        <table class="tit-table">
          <thead>
            <tr>
              <th>Employer</th><th>What happened</th><th>Where</th>
              <th>What it means</th><th>How solid</th><th>Source</th>
            </tr>
          </thead>
          <tbody id="tit-rows">
            <?php foreach ($rows as $r) : ?>
              <tr>
                <td class="tit-eyebrow" data-label="Employer"><?php
                  $ck = $r['company_key'] ?? '';
                  if ($ck && function_exists('tit_company_url')) {
                      printf('<a href="%s">%s</a>', esc_url(tit_company_url($ck)), esc_html($r['company']));
                  } else {
                      echo esc_html($r['company']);
                  }
                ?></td>
                <td class="tit-headline" data-label="What happened">
                  <span class="tit-h"><?php echo esc_html($r['headline']); ?></span>
                  <span class="tit-rt"><?php echo esc_html($r['talent_readthrough']); ?></span>
                </td>
                <td class="tit-meta" data-label="Where">
                  <?php
                  $place = $r['city'] ?: $r['hq_city'];
                  $cc    = $r['country'] ?: $r['hq_country'];
                  $is_hq = !$r['city'] && !$r['country'];
                  $where = trim(($place ? $place . ', ' : '') . tit_country_name($cc), ', ');
                  if ($where === '') {
                      // Stored anyway: geography is how we segment, not what
                      // makes the record true. Saying so beats a blank cell.
                      echo '<span class="tit-nowhere">Location not stated</span>';
                  } else {
                      echo esc_html($where);
                      if ($is_hq) echo ' <span class="tit-hq" title="Employer headquarters, not a location named in the source">HQ</span>';
                  }
                  ?>
                </td>
                <td class="tit-meta" data-label="What it means"><span class="tit-tag tit-<?php echo esc_attr($r['signal_direction']); ?>"><?php echo esc_html($directions[$r['signal_direction']] ?? $r['signal_direction']); ?></span></td>
                <td class="tit-meta" data-label="How solid"><span class="tit-conf tit-c-<?php echo esc_attr($r['confidence']); ?>"><?php echo esc_html($r['confidence']); ?></span></td>
                <td class="tit-meta" data-label="Source"><a href="<?php echo esc_url($r['source_url']); ?>" rel="nofollow noopener" target="_blank"><?php echo esc_html($r['source_name']); ?></a></td>
              </tr>
            <?php endforeach; ?>
          </tbody>
        </table>
      </div>

      <!--
        Download exactly what the filters show. The hrefs are server-rendered
        pointing at the whole dataset; dashboard.js rewrites them with the
        current querystring on every refresh, and the scope word flips between
        "all" and "filtered" so the link says which set it hands over.
      -->
      <div class="tit-export">
        <span class="tit-export-label">Download this view</span>
        <a class="tit-export-link" id="tit-export-csv"
           data-base="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_csv')); ?>"
           href="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_csv')); ?>">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14"/></svg>
          CSV<span class="tit-export-scope" id="tit-export-csv-scope"> · all</span></a>
        <a class="tit-export-link" id="tit-export-json"
           data-base="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_json')); ?>"
           href="<?php echo esc_url(admin_url('admin-post.php?action=tit_export_json')); ?>">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14"/></svg>
          JSON<span class="tit-export-scope" id="tit-export-json-scope"> · all</span></a>
        <span class="tit-export-note">Every matching update, not just this page. Free to reuse, CC BY 4.0.</span>
      </div>

      <p class="tit-cite">
        Data licensed CC BY 4.0. Cite as: Talent Intelligence Tracker,
        asktherecruiter.com. Layoff and redundancy data is not collected here;
        see the
        <a href="/blog/ai-layoff-tracker/">AI Layoff Tracker</a>.
      </p>
    </div>
    <?php
    return ob_get_clean();
}
add_shortcode('talent_intelligence_dashboard', 'tit_dashboard_shortcode');

/**
 * The at-a-glance matrix: signals down the side, periods across the top.
 *
 * This replaced five period tiles that each printed the same sentence shape.
 * On a young dataset several periods hold the same records, so the strip said
 * almost the same thing five times and never answered "what kind, and is it
 * accelerating". Rows are what a recruiter or job seeker actually asks for.
 *
 * Row definitions use existing columns only, and they OVERLAP by design (a
 * funded employer can also be hiring), which the note under the matrix says
 * out loud so the columns are not read as sums:
 *   Hiring up          signal_direction = 'hiring'
 *   Funding raised     funding_amount present
 *   Leadership moves   pillar = 'leadership_change'
 *   Pay changes        signal_direction = 'comp_shift' — the direction column,
 *                      NOT pillar = 'rewards_comp': direction is a closed
 *                      vocabulary the pipeline always sets, and it also
 *                      catches a pay change filed under another pillar.
 *   All updates        every row in the period (the emphasised total row)
 *
 * One query, not twenty-five: conditional aggregation over the widest window,
 * one SUM per cell. Dates come from published_date, the date the source
 * carries, falling back to capture date — same rule as everywhere else.
 * Quarter start stays derived, never hardcoded.
 *
 * $where/$params let /aggregate reuse this under the caller's own filters, so
 * the matrix the server renders and the one JavaScript repaints on a filtered
 * page cannot describe the world differently.
 */
function tit_glance_matrix($table, $where = 'is_current = 1', array $params = array()) {
    global $wpdb;
    $today = current_time('Y-m-d');
    $q_month = (intdiv((int) date('n', strtotime($today)) - 1, 3) * 3) + 1;
    $periods = array(
        array('Today',        $today),
        array('This week',    date('Y-m-d', strtotime($today . ' -6 days'))),
        array('This month',   date('Y-m-01', strtotime($today))),
        array('This quarter', sprintf('%s-%02d-01', date('Y', strtotime($today)), $q_month)),
        // Spelled out, never "YTD".
        array(date('Y', strtotime($today)) . ' so far', date('Y-01-01', strtotime($today))),
    );

    // key, reader-facing label, the filter a cell click applies, SQL condition.
    $defs = array(
        array('hiring',     'Hiring up',        'direction=hiring',         "signal_direction = 'hiring'"),
        array('funded',     'Funding raised',   'funding=1',                "(funding_amount IS NOT NULL AND funding_amount <> '')"),
        array('leadership', 'Leadership moves', 'pillar=leadership_change', "pillar = 'leadership_change'"),
        array('pay',        'Pay changes',      'direction=comp_shift',     "signal_direction = 'comp_shift'"),
        array('total',      'All updates',      '',                         '1 = 1'),
    );

    $date_expr = 'COALESCE(published_date, DATE(captured_at))';
    $select = array();
    $select_params = array();
    foreach ($periods as $pi => $p) {
        foreach ($defs as $di => $d) {
            $select[] = "SUM(({$d[3]}) AND {$date_expr} >= %s) AS c_{$di}_{$pi}";
            $select_params[] = $p[1];
        }
    }
    // SELECT placeholders precede WHERE placeholders in statement order, so
    // the select params go first.
    $sql = 'SELECT ' . implode(', ', $select) . " FROM {$table} WHERE {$where}";
    $row = $wpdb->get_row(
        $wpdb->prepare($sql, array_merge($select_params, $params)),
        ARRAY_A
    ) ?: array();

    $rows = array();
    foreach ($defs as $di => $d) {
        $cells = array();
        foreach ($periods as $pi => $p) {
            $cells[] = (int) ($row["c_{$di}_{$pi}"] ?? 0);
        }
        $rows[] = array('key' => $d[0], 'label' => $d[1], 'filter' => $d[2], 'cells' => $cells);
    }

    return array(
        'periods' => array_column($periods, 0),
        'starts'  => array_column($periods, 1),
        'rows'    => $rows,
    );
}

/**
 * Render the matrix. dashboard.js mirrors this markup when it repaints from
 * /aggregate, the same contract renderRow() has with the table: the classes
 * and attributes here must match what the JS emits.
 *
 * Cells are buttons: a click applies the row's filter plus the column's
 * period, a second click clears them (wired in dashboard.js). Zero renders as
 * a muted 0, never blank — a real zero is information.
 */
function tit_glance_matrix_html(array $m) {
    ob_start(); ?>
    <div class="tit-matrix-scroll">
      <table class="tit-matrix">
        <thead>
          <tr>
            <th scope="col"><span class="tit-sr">Signal</span></th>
            <?php foreach ($m['periods'] as $p) : ?>
              <th scope="col"><?php echo esc_html($p); ?></th>
            <?php endforeach; ?>
          </tr>
        </thead>
        <tbody>
          <?php foreach ($m['rows'] as $r) :
            // Each signal keeps one hue across the whole product, and the cell
            // tint is scaled to the biggest number in ITS OWN row. Rows differ
            // by orders of magnitude (leadership moves ran 1,726 against 235
            // pay changes), so a table-wide scale would wash every row but one
            // out to nothing. Colour is a second read here, never the only
            // one: the number is always printed.
            $row_max = 0;
            foreach ($r['cells'] as $n) { $row_max = max($row_max, (int) $n); }
            ?>
            <tr class="tit-matrix-row<?php echo $r['key'] === 'total' ? ' tit-matrix-total' : ''; ?>"
                data-signal="<?php echo esc_attr($r['key']); ?>">
              <th scope="row"><?php echo esc_html($r['label']); ?></th>
              <?php foreach ($r['cells'] as $i => $n) :
                $n = (int) $n;
                // Square-rooted so a single dominant period does not flatten
                // every smaller one to invisible, and floored at 0.14 so any
                // real activity is still tinted.
                $intensity = ($row_max > 0 && $n > 0)
                    ? max(0.14, round(sqrt($n / $row_max), 3)) : 0;
                ?>
                <td><button type="button"
                    class="tit-cell<?php echo $n === 0 ? ' tit-cell-zero' : ''; ?>"
                    style="--i:<?php echo esc_attr($intensity); ?>"
                    data-filter="<?php echo esc_attr($r['filter']); ?>"
                    data-since="<?php echo esc_attr($m['starts'][$i]); ?>"
                    aria-pressed="false"
                    aria-label="<?php echo esc_attr($r['label'] . ', ' . $m['periods'][$i]); ?>"><?php
                    echo esc_html(number_format_i18n($n));
                ?></button></td>
              <?php endforeach; ?>
            </tr>
          <?php endforeach; ?>
        </tbody>
      </table>
    </div>
    <p class="tit-matrix-note">Stronger colour means more activity, measured
       across each row. Rows overlap on purpose: a funded employer can also be
       hiring up, so columns are not sums.
       <strong>Click any number to filter the whole page.</strong></p>
    <?php
    return ob_get_clean();
}

/**
 * Absolute time in the SITE's configured timezone with its abbreviation:
 * "Jul 28, 1:51 PM EDT". The sibling tracker leads with exactly this shape.
 * wp_date() renders in wp_timezone(), so a change under Settings > General
 * moves every timestamp with it; never a hardcoded offset. Accepts a UTC
 * MySQL datetime or a Unix timestamp.
 */
function tit_local_datetime($utc) {
    $ts = is_numeric($utc) ? (int) $utc : strtotime($utc . ' UTC');
    if (!$ts) return '';
    // A site whose timezone is set to plain UTC renders 'T' as "GMT+0000",
    // which reads like a machine header, not a time a person wrote. Same
    // instant, human label. Named zones (EDT, CET) pass through untouched.
    return str_replace('GMT+0000', 'UTC', wp_date('M j, g:i A T', $ts));
}

/**
 * When the next collection run is due.
 *
 * "Resting until the next run" invites the obvious question and then does not
 * answer it. The schedule is a cron in collect.yml, so the times are fixed and
 * known: 06:00 and 18:00 UTC. Printed in the site's own timezone, because a
 * reader should not have to convert.
 */
function tit_next_run() {
    $now = time();
    foreach (array(0, 1) as $day) {
        foreach (array(6, 18) as $hour) {
            $t = strtotime(gmdate('Y-m-d', $now + $day * DAY_IN_SECONDS)
                           . sprintf(' %02d:00:00 UTC', $hour));
            if ($t > $now) return $t;
        }
    }
    return 0;
}

/**
 * How much history actually sits behind those tiles, in words.
 *
 * Printed under the glance block because the tiles alone cannot say it: "46
 * updates" reads like a running total from a tracker that has been going a
 * while, and right now it is eight days of collection.
 */
function tit_span_note($lo, $hi) {
    if (!$lo || !$hi) return '';
    $days = (int) floor((strtotime($hi) - strtotime($lo)) / DAY_IN_SECONDS) + 1;
    return sprintf(
        /* translators: 1: number of days, 2: first date, 3: last date */
        _n('Everything here spans %1$s day, %2$s.', 'Everything here spans %1$s days, %2$s to %3$s.', $days, 'tit'),
        number_format_i18n($days),
        date_i18n('j M', strtotime($lo)),
        date_i18n('j M Y', strtotime($hi))
    );
}

/**
 * Roo, the AskTheRecruiter robot, ported from the sibling tracker.
 *
 * He reports the state of COLLECTION, not the state of the page. Wiring him to
 * the dashboard's own fetches would have been easier and would have made him
 * animate far more often, but it would also have meant a mascot that looks busy
 * because someone changed a dropdown. He works when a run has just landed and
 * sleeps the rest of the time, which is the truth and is usually "asleep".
 *
 * State is decided server-side from the newest capture, so the first paint is
 * already right; no request, and nothing to get wrong before JavaScript runs.
 */
function tit_roo($newest_run) {
    $ts     = $newest_run ? strtotime($newest_run . ' UTC') : 0;
    $ago    = $ts ? (time() - $ts) : null;
    // A run writes over a few minutes, so anything inside the last quarter hour
    // is treated as still in progress rather than finished.
    $working = ($ago !== null && $ago >= 0 && $ago < 900);
    $state   = $working ? 'tit-roo-working' : 'tit-roo-sleeping';

    $next = tit_next_run();

    // The absolute timestamp with timezone leads up in the Live pill; down
    // here the relative time is the secondary fact and the next-run note is
    // the quietest part of the line (its own span, styled smaller). Next-run
    // times go through tit_local_datetime() too: the site's timezone, never a
    // hand-converted hour.
    if ($working) {
        $say = 'Roo is pulling in new filings and news';
    } elseif ($ago !== null) {
        $say = sprintf('Roo pulled the latest data %s ago.', human_time_diff($ts, time()));
    } else {
        $say = 'Roo is resting until the next run.';
    }
    $next_note = $next
        ? sprintf('Next run %s, in %s.', tit_local_datetime($next), human_time_diff(time(), $next))
        : '';
    ?>
    <span class="tit-roo-wrap<?php echo $working ? ' is-working' : ' is-sleeping'; ?>" aria-hidden="true">
      <span class="tit-zzz"><i>z</i><i>z</i><i>z</i></span>
      <svg class="tit-roo <?php echo esc_attr($state); ?>" width="52" height="56" viewBox="0 0 140 150">
        <line x1="70" y1="14" x2="70" y2="26" stroke="var(--roo-deep)" stroke-width="3" stroke-linecap="round"/>
        <circle class="roo-bulb" cx="70" cy="10" r="5" fill="var(--roo-accent)"/>
        <rect x="26" y="24" width="88" height="40" rx="20" fill="var(--roo-surface)" stroke="var(--roo-deep)" stroke-width="3.5"/>
        <g class="roo-eyes">
          <circle cx="51" cy="44" r="12" fill="var(--roo-soft)" stroke="var(--roo-deep)" stroke-width="2.5"/>
          <g class="roo-pupil"><circle cx="53.5" cy="44" r="5.5" fill="var(--roo-deep)"/><circle cx="55.5" cy="42" r="1.8" fill="var(--roo-surface)"/></g>
          <circle cx="89" cy="44" r="12" fill="var(--roo-soft)" stroke="var(--roo-deep)" stroke-width="2.5"/>
          <g class="roo-pupil"><circle cx="86.5" cy="44" r="5.5" fill="var(--roo-deep)"/><circle cx="88.5" cy="42" r="1.8" fill="var(--roo-surface)"/></g>
        </g>
        <path d="M 59 57 Q 70 61 81 57" fill="none" stroke="var(--roo-deep)" stroke-width="2.5" stroke-linecap="round"/>
        <g class="roo-body-group">
          <rect x="64" y="64" width="12" height="8" fill="var(--roo-deep)" rx="2"/>
          <rect class="roo-arm-l" x="18" y="82" width="14" height="28" rx="7" fill="var(--roo-surface)" stroke="var(--roo-deep)" stroke-width="3"/>
          <rect class="roo-arm-r" x="108" y="82" width="14" height="28" rx="7" fill="var(--roo-surface)" stroke="var(--roo-deep)" stroke-width="3"/>
          <rect x="36" y="72" width="68" height="46" rx="10" fill="var(--roo-surface)" stroke="var(--roo-deep)" stroke-width="3.5"/>
          <rect x="48" y="80" width="44" height="30" rx="5" fill="var(--roo-tint)" stroke="var(--roo-deep)" stroke-width="2"/>
          <rect class="roo-line" x="54" y="86" height="3.5" width="26" rx="1.75" fill="var(--roo-deep)"/>
          <rect class="roo-line" x="54" y="93" height="3.5" width="18" rx="1.75" fill="var(--roo-deep)"/>
          <rect class="roo-line" x="54" y="100" height="3.5" width="22" rx="1.75" fill="var(--roo-deep)"/>
        </g>
        <rect x="30" y="122" width="80" height="14" rx="7" fill="var(--roo-soft)" stroke="var(--roo-deep)" stroke-width="2.5"/>
        <circle class="roo-tread-dot" cx="42" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
        <circle class="roo-tread-dot" cx="56" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
        <circle class="roo-tread-dot" cx="70" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
        <circle class="roo-tread-dot" cx="84" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
        <circle class="roo-tread-dot" cx="98" cy="129" r="3" fill="var(--roo-deep)" opacity=".55"/>
      </svg>
    </span>
    <span class="tit-roo-say"><?php echo esc_html($say); ?><?php
      if ($next_note) : ?> <span class="tit-roo-next"><?php echo esc_html($next_note); ?></span><?php endif;
    ?></span>
    <?php
}

/**
 * The heading block every chart card shares, plus its expand control.
 *
 * The button ships `hidden` and dashboard.js reveals it. A control that only
 * works with JavaScript should not be visible without it, and rendering it
 * server-side (rather than injecting it) keeps its label and markup in one
 * place with the heading it belongs to.
 */
function tit_chart_head($title, $sub, $id = '') {
    ?>
    <div class="tit-chart-head">
      <div class="tit-chart-titles">
        <h3><?php echo esc_html($title); ?></h3>
        <p class="tit-sub"><?php echo esc_html($sub); ?></p>
      </div>
      <div class="tit-chart-tools">
        <button type="button" class="tit-ctl tit-chart-share" data-chart="<?php echo esc_attr($id); ?>"
                title="Copy a link to this view" aria-label="Copy a link to this view" hidden>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 10.7l6.8-4M8.6 13.3l6.8 4"/></svg>
        </button>
        <button type="button" class="tit-ctl tit-chart-dl" data-chart="<?php echo esc_attr($id); ?>"
                title="Download this chart as CSV" aria-label="Download this chart as CSV" hidden>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14"/></svg>
        </button>
        <button type="button" class="tit-ctl tit-expand" aria-expanded="false"
                title="Expand this chart" aria-label="Expand this chart" hidden>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7"/></svg>
          <span class="tit-sr tit-expand-t">Expand</span>
        </button>
      </div>
    </div>
    <?php
}

/**
 * The region strip above the charts.
 *
 * Regions are groups of country codes, not a separate dimension in the data —
 * "Europe" is a shorthand for a list, and the tab sends that list to the API.
 *
 * A region with nothing in it is dropped rather than shown at zero. An empty
 * tab reads as a filter that broke, and a strip of them reads as coverage we
 * do not have. Worldwide always survives so there is always a way back.
 */
function tit_regions(array $counts) {
    // Same regions and the same names as the sibling tracker, so one brand does
    // not describe the world two different ways. India keeps its own tab, which
    // the sibling has no need of: it is one of the largest hiring markets we
    // read, and burying it inside Asia would hide the thing people came for.
    //
    // Every list is the WHOLE region, never a shortlist of the big names. A
    // Latvian employer once fell outside 'Europe' because LV was missing, and a
    // Namibian one outside 'Africa' because NA was, which is a filter quietly
    // lying about its own name. A code costs nothing to carry and a missing one
    // costs a record.
    $defs = array(
        array('World',         '🌐', ''),
        array('USA',           '🇺🇸', 'US'),
        array('Canada',        '🇨🇦', 'CA'),
        array('Latin America', '🌎', 'BR,MX,AR,CL,CO,PE,UY,CR,EC,BO,PY,VE,GT,HN,SV,NI,PA,DO,CU,JM,TT,HT,BZ,GY,SR'),
        array('Europe',        '🇪🇺', 'GB,IE,DE,FR,NL,ES,IT,SE,PL,CH,BE,DK,NO,FI,AT,PT,'
                                     . 'CZ,GR,RO,HU,LV,LT,EE,SK,SI,HR,BG,RS,UA,IS,LU,'
                                     . 'MT,CY,AL,BA,ME,MK,MD,BY,MC,LI,AD,SM,VA,XK,RU,GE,AM,AZ'),
        array('UK',            '🇬🇧', 'GB'),
        array('Middle East',   '🕌', 'AE,SA,IL,QA,KW,BH,OM,TR,JO,LB,IQ,IR,SY,YE,PS'),
        array('Africa',        '🌍', 'ZA,NG,KE,EG,MA,GH,ET,NA,TZ,UG,ZM,ZW,BW,MZ,AO,SN,CI,CM,'
                                     . 'DZ,TN,LY,SD,RW,MW,MU,MG,CD,CG,GA,BJ,BF,ML,NE,TD,SO,SL,LR,GM,SS'),
        array('India',         '🇮🇳', 'IN'),
        array('Asia',          '🌏', 'IN,SG,JP,CN,HK,KR,MY,PH,ID,TH,VN,TW,PK,BD,LK,NP,MM,KH,LA,MN,MO,BN,MV,KZ,UZ'),
        array('Australia',     '🇦🇺', 'AU,NZ,FJ,PG'),
    );
    $total = array_sum(array_map('intval', $counts));

    $out = array();
    foreach ($defs as [$name, $flag, $codes]) {
        if ($codes === '') {
            $out[] = compact('name', 'flag', 'codes') + array('n' => $total);
            continue;
        }
        $n = 0;
        foreach (explode(',', $codes) as $c) {
            $n += (int) ($counts[$c] ?? 0);
        }
        if ($n > 0) {
            $out[] = compact('name', 'flag', 'codes') + array('n' => $n);
        }
    }
    return $out;
}

/**
 * Enqueue the dashboard assets. Idempotent: WordPress ignores a second
 * enqueue of the same handle, so both the wp_enqueue_scripts hook and the
 * shortcode itself can call this safely.
 */
function tit_enqueue_dashboard_assets() {
    // Version is TIT_VERSION plus the file's own mtime, the same shape the
    // sibling plugin uses. TIT_VERSION alone is not enough: an FTP deploy can
    // change the stylesheet without the constant moving (a CSS-only fix), and
    // this site runs Autoptimize, which caches a rewritten copy of the file
    // keyed on that version string. Without the mtime the visitor keeps the
    // old rewritten copy and the deploy appears not to have landed.
    wp_enqueue_style('tit-dashboard', TIT_URL . 'assets/dashboard.css', array(),
        tit_asset_version('assets/dashboard.css'));
    wp_enqueue_script('tit-dashboard', TIT_URL . 'assets/dashboard.js', array(),
        tit_asset_version('assets/dashboard.js'), true);
    wp_localize_script('tit-dashboard', 'TIT', array(
        'api' => esc_url_raw(rest_url('talent/v1/')),
        // The filtered rows are rendered in the browser, so it needs the same
        // country names the server used. Two copies of this list would drift.
        'countries' => tit_country_names(),
    ));
}

function tit_enqueue_assets() {
    // Our own routed pages (company profiles, sources) carry no shortcode and
    // are not singular posts, so a shortcode-only check leaves them completely
    // unstyled. Ask each route, rather than naming them one at a time — that
    // omission is exactly how the sources page shipped unstyled.
    $is_plugin_route = (bool) get_query_var('tit_company')
                    || (bool) get_query_var('tit_sources');

    if (!$is_plugin_route) {
        if (!is_singular()) return;
        global $post;
        if (!$post || !has_shortcode($post->post_content, 'talent_intelligence_dashboard')) return;
    }

    tit_enqueue_dashboard_assets();
}
add_action('wp_enqueue_scripts', 'tit_enqueue_assets');

/**
 * Keep Autoptimize's hands off this plugin's assets.
 *
 * The live site runs Autoptimize with CSS aggregation on: it strips every
 * <link rel="stylesheet"> and inlines the combined result. Our dashboard.css
 * was enqueued correctly and still never reached the page - the aggregated
 * bundle simply did not contain a single .tit- rule, so the dashboard rendered
 * as raw HTML with every class inert (observed live 2026-07-28).
 *
 * The sibling plugin carries the same two filters for the same reason. Ours
 * were missing, which is why "the CSS exists and is enqueued" and "the page has
 * no styles" were both true at once.
 */
function tit_autoptimize_exclude_css($exclude) {
    return $exclude . ', talent-intelligence-tracker/assets';
}
add_filter('autoptimize_filter_css_exclude', 'tit_autoptimize_exclude_css');

function tit_autoptimize_exclude_js($exclude) {
    // Two entries, not one. The path keeps our file out of the bundle; the
    // `var TIT` string keeps the inline object wp_localize_script prints out of
    // it too. Excluding only the path split the pair apart and left the file
    // running before the object it reads.
    return $exclude . ', talent-intelligence-tracker/assets, var TIT';
}
add_filter('autoptimize_filter_js_exclude', 'tit_autoptimize_exclude_js');
