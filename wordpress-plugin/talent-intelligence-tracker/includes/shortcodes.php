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
    $glance = tit_glance($table);
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
    <div class="tit-wrap" id="tit-dashboard">

      <div class="tit-hero">
        <div class="tit-hero-top">
          <h2>Who is hiring, who is raising money, and who is changing leadership</h2>
          <div class="tit-live"><span class="tit-live-dot"></span>
            Live<?php if ($newest_run) : ?> ·
            <?php echo esc_html(date_i18n('M j, g:i A', strtotime($newest_run . ' UTC'))); ?>
            <?php endif; ?>
          </div>
        </div>

        <div class="tit-roo-row"><?php tit_roo($newest_run); ?></div>

        <div class="tit-glance">
          <?php foreach ($glance as $g) : ?>
            <div class="tit-glance-cell">
              <span class="tit-glance-when"><?php echo esc_html($g['when']); ?></span>
              <span class="tit-glance-n"><?php echo esc_html(number_format_i18n($g['n'])); ?></span>
              <span class="tit-glance-detail"><?php echo esc_html($g['detail']); ?></span>
            </div>
          <?php endforeach; ?>
        </div>

        <p class="tit-hero-fine">
          <span class="tit-fine-figures"><?php
            printf(
              /* translators: totals restated by JavaScript when a filter changes. */
              '%s updates · %s employers · %s countries · %s from official filings. ',
              esc_html(number_format_i18n($total)),
              esc_html(number_format_i18n($companies)),
              esc_html(number_format_i18n($countries)),
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

      <div class="tit-charts">
      <div class="tit-chart">
        <?php tit_chart_head('What kind of update', 'Every update falls into one of four kinds.'); ?>
      <div class="tit-pillars">
        <?php foreach ($by_pillar as $p) :
            $key = $p['pillar'];
            $pct = $total ? round(100 * $p['n'] / $total) : 0; ?>
          <div class="tit-pillar">
            <div class="tit-pillar-head">
              <span class="tit-pillar-name"><?php echo esc_html($labels[$key] ?? $key); ?></span>
              <span class="tit-pillar-n"><?php echo esc_html(number_format_i18n($p['n'])); ?></span>
            </div>
            <div class="tit-bar"><span style="width:<?php echo esc_attr($pct); ?>%"></span></div>
          </div>
        <?php endforeach; ?>
      </div>
      </div>
        <div class="tit-chart">
          <?php tit_chart_head('Where the activity is', "Job location, falling back to the employer's headquarters."); ?>
          <div class="tit-rank" tabindex="0" role="group" aria-label="Activity by place">
            <?php
            $cmax = $by_country ? max(array_map('intval', array_column($by_country, 'n'))) : 1;
            foreach ($by_country as $c) : ?>
              <div class="tit-rank-row">
                <span class="tit-rank-name"><?php echo esc_html(tit_country_name($c['k'])); ?></span>
                <span class="tit-rank-track"><span class="tit-rank-fill"
                  style="width:<?php echo esc_attr(max(4, round(100 * $c['n'] / $cmax))); ?>%"></span></span>
                <span class="tit-rank-n"><?php echo (int) $c['n']; ?></span>
              </div>
            <?php endforeach; ?>
          </div>
        </div>

        <div class="tit-chart">
          <?php tit_chart_head('Growing or shrinking', 'What each update means for headcount at that employer.'); ?>
          <div class="tit-rank" tabindex="0" role="group" aria-label="Activity by direction">
            <?php
            $dmax = $by_direction ? max(array_map('intval', array_column($by_direction, 'n'))) : 1;
            foreach ($by_direction as $d) : ?>
              <div class="tit-rank-row" data-dir="<?php echo esc_attr($d['k']); ?>">
                <span class="tit-rank-name"><?php echo esc_html($directions[$d['k']] ?? $d['k']); ?></span>
                <span class="tit-rank-track"><span class="tit-rank-fill"
                  style="width:<?php echo esc_attr(max(4, round(100 * $d['n'] / $dmax))); ?>%"></span></span>
                <span class="tit-rank-n"><?php echo (int) $d['n']; ?></span>
              </div>
            <?php endforeach; ?>
          </div>
        </div>
      </div>

      <div class="tit-sec">
        <h3>Every update</h3>
        <p>Newest first. The read-through is ours; the headline, the figures and
           the confidence all come from the linked source.</p>
      </div>

      <div class="tit-quick" role="group" aria-label="Quick views">
        <span class="tit-quick-label">Quick views</span>
        <button type="button" class="tit-qv" data-qv="">Everything</button>
        <button type="button" class="tit-qv" data-qv="since=<?php echo esc_attr(date('Y-m-d', strtotime('-7 days'))); ?>">This week</button>
        <button type="button" class="tit-qv" data-qv="direction=hiring">Hiring up</button>
        <button type="button" class="tit-qv" data-qv="funding=1">Raised money</button>
        <button type="button" class="tit-qv" data-qv="pillar=leadership_change">Leadership moves</button>
        <button type="button" class="tit-qv" data-qv="confidence=verified">From official filings</button>
        <select id="tit-f-sort" class="tit-sort" aria-label="Sort the updates">
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="largest">Most roles first</option>
          <option value="employer">By employer</option>
        </select>
      </div>

      <div class="tit-filters">
        <select id="tit-f-pillar" aria-label="What kind of update">
          <option value="">Anything happening</option>
          <?php foreach ($labels as $k => $v) : ?>
            <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
          <?php endforeach; ?>
        </select>
        <select id="tit-f-direction" aria-label="Is the employer growing or shrinking">
          <option value="">Growing or shrinking</option>
          <?php foreach ($directions as $k => $v) : ?>
            <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
          <?php endforeach; ?>
        </select>
        <select id="tit-f-function" aria-label="Which roles are affected">
          <option value="">Any roles</option>
          <?php foreach ($functions as $k => $v) : ?>
            <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
          <?php endforeach; ?>
        </select>
        <select id="tit-f-industry" aria-label="Which industry">
          <option value="">Any industry</option>
          <?php foreach ($industries as $k => $v) : ?>
            <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
          <?php endforeach; ?>
        </select>
        <select id="tit-f-country" aria-label="Which country">
          <option value="">Anywhere</option>
        </select>
        <select id="tit-f-state" aria-label="Which US state">
          <option value="">Any US state</option>
        </select>
        <select id="tit-f-confidence" aria-label="How solid the record is">
          <option value="">Any confidence</option>
          <option value="verified">From official filings</option>
          <option value="reported">Reported by a publisher</option>
          <option value="rumored">Rumored</option>
        </select>
        <!-- The charts already admit they fall back to headquarters. Until now
             the page surfaced that ambiguity without letting anyone resolve it,
             even though the API has taken country_basis all along. -->
        <select id="tit-f-country_basis" aria-label="How to decide where a record belongs">
          <option value="any">Place, or the employer's HQ</option>
          <option value="location">Only where the source named a place</option>
        </select>
        <input type="search" id="tit-f-company" placeholder="Employer name"
               aria-label="Search by employer">
        <input type="search" id="tit-f-q" placeholder="Search anything"
               aria-label="Search headlines and read-throughs">
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
                <td data-label="Employer"><?php
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
                <td data-label="Where">
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
                <td data-label="What it means"><span class="tit-tag tit-<?php echo esc_attr($r['signal_direction']); ?>"><?php echo esc_html($directions[$r['signal_direction']] ?? $r['signal_direction']); ?></span></td>
                <td data-label="How solid"><span class="tit-conf tit-c-<?php echo esc_attr($r['confidence']); ?>"><?php echo esc_html($r['confidence']); ?></span></td>
                <td data-label="Source"><a href="<?php echo esc_url($r['source_url']); ?>" rel="nofollow noopener" target="_blank"><?php echo esc_html($r['source_name']); ?></a></td>
              </tr>
            <?php endforeach; ?>
          </tbody>
        </table>
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
 * The four at-a-glance lines: today, this week, this month, this year.
 *
 * A period with nothing in it still prints, saying so in words. Hiding the
 * empty ones would make a quiet Sunday look like a busy one, and the whole
 * point of the block is to be readable in about ten seconds.
 *
 * Dates come from published_date, the date the source carries, not the date we
 * happened to read it. A filing published on Friday and collected on Monday
 * belongs to Friday.
 */
function tit_glance($table) {
    global $wpdb;
    $today = current_time('Y-m-d');
    $periods = array(
        array('Today',      $today),
        array('This week',  date('Y-m-d', strtotime($today . ' -6 days'))),
        array('This month', date('Y-m-01', strtotime($today))),
        array(date('Y', strtotime($today)) . ' so far', date('Y-01-01', strtotime($today))),
    );

    $out = array();
    foreach ($periods as [$when, $from]) {
        $row = $wpdb->get_row($wpdb->prepare(
            "SELECT COUNT(*) n,
                    SUM(signal_direction = 'hiring') hiring,
                    SUM(funding_amount IS NOT NULL AND funding_amount <> '') funded,
                    SUM(confidence = 'verified') verified
               FROM {$table}
              WHERE is_current = 1
                AND COALESCE(published_date, DATE(captured_at)) >= %s",
            $from
        ), ARRAY_A);

        $n = (int) ($row['n'] ?? 0);
        if ($n === 0) {
            $out[] = array('when' => $when, 'n' => 0, 'detail' => 'nothing yet');
            continue;
        }

        $bits = array();
        if ((int) $row['hiring'])   $bits[] = number_format_i18n((int) $row['hiring']) . ' hiring up';
        if ((int) $row['funded'])   $bits[] = number_format_i18n((int) $row['funded']) . ' raised money';
        if ((int) $row['verified']) $bits[] = number_format_i18n((int) $row['verified']) . ' from filings';

        $out[] = array(
            'when'   => $when,
            'n'      => $n,
            'detail' => $bits ? implode(' · ', $bits) : ($n === 1 ? 'one update' : 'updates'),
        );
    }
    return $out;
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

    if ($working) {
        $say = 'Roo is pulling in new filings and news';
    } elseif ($ago !== null) {
        $say = sprintf('Roo pulled the latest data %s ago, resting until the next run',
                       human_time_diff($ts, time()));
    } else {
        $say = 'Roo is resting until the next run';
    }
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
    <span class="tit-roo-say"><?php echo esc_html($say); ?></span>
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
function tit_chart_head($title, $sub) {
    ?>
    <div class="tit-chart-head">
      <div class="tit-chart-titles">
        <h3><?php echo esc_html($title); ?></h3>
        <p class="tit-sub"><?php echo esc_html($sub); ?></p>
      </div>
      <button type="button" class="tit-expand" aria-expanded="false" hidden>
        <span class="tit-expand-i" aria-hidden="true">&#10530;</span>
        <span class="tit-expand-t">Expand</span>
      </button>
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
    $defs = array(
        array('World',         '🌐', ''),
        array('United States', '🇺🇸', 'US'),
        array('Canada',        '🇨🇦', 'CA'),
        array('United Kingdom','🇬🇧', 'GB'),
        // All of Europe, not a shortlist. A Latvian employer was landing
        // outside 'Europe' because LV was not on the list, which is a filter
        // that quietly lies about its own name.
        array('Europe',        '🇪🇺', 'GB,IE,DE,FR,NL,ES,IT,SE,PL,CH,BE,DK,NO,FI,AT,PT,'
                                     . 'CZ,GR,RO,HU,LV,LT,EE,SK,SI,HR,BG,RS,UA,IS,LU,'
                                     . 'MT,CY,AL,BA,ME,MK,MD,BY,MC,LI,AD,SM'),
        array('India',         '🇮🇳', 'IN'),
        array('Asia Pacific',  '🌏', 'IN,SG,JP,CN,HK,AU,NZ,KR,MY,PH,ID,TH,VN,TW'),
        array('Latin America', '🌎', 'BR,MX,AR,CL,CO,PE,UY,CR'),
        array('Middle East',   '🕌', 'AE,SA,IL,QA,KW,BH,OM,TR'),
        array('Africa',        '🌍', 'ZA,NG,KE,EG,MA,GH,ET'),
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
    return $exclude . ', talent-intelligence-tracker/assets';
}
add_filter('autoptimize_filter_js_exclude', 'tit_autoptimize_exclude_js');
