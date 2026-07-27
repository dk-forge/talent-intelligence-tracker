<?php
/**
 * Renders the dashboard. Server-side first so the page is useful (and
 * indexable) before any JavaScript runs; the filters then talk to /query.
 *
 * UI copy here uses no em-dashes, matching the house style.
 */

if (!defined('ABSPATH')) exit;

function tit_dashboard_shortcode() {
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

    $newest_run = $wpdb->get_var("SELECT MAX(captured_at) FROM {$table} WHERE is_current = 1");
    $hiring     = (int) $wpdb->get_var("SELECT COUNT(*) FROM {$table} WHERE is_current = 1 AND signal_direction = 'hiring'");
    $funded     = (int) $wpdb->get_var("SELECT COUNT(*) FROM {$table} WHERE is_current = 1 AND funding_amount IS NOT NULL");
    $by_country = $wpdb->get_results(
        "SELECT COALESCE(country, hq_country) k, COUNT(*) n FROM {$table}
          WHERE is_current = 1 AND COALESCE(country, hq_country) IS NOT NULL
          GROUP BY k ORDER BY n DESC LIMIT 6", ARRAY_A) ?: array();
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
        <div class="tit-live"><span class="tit-live-dot"></span>
          Live<?php if ($newest_run) : ?> · last updated
          <?php echo esc_html(date_i18n('M j, g:i A', strtotime($newest_run . ' UTC'))); ?>
          <?php endif; ?>
        </div>
        <h2>Who is hiring, who is raising money, and who is changing leadership</h2>
        <p>Every update here links to the filing or article that makes the claim.
           We do not estimate. Figures appear only when the source states them,
           and a source that reports a plan is never shown as a confirmed fact.</p>
        <div class="tit-hero-figs">
          <div class="tit-hero-fig"><strong><?php echo esc_html(number_format_i18n($hiring)); ?></strong>hiring up</div>
          <div class="tit-hero-fig"><strong><?php echo esc_html(number_format_i18n($funded)); ?></strong>raised money</div>
          <div class="tit-hero-fig"><strong><?php echo esc_html(number_format_i18n($companies)); ?></strong>employers tracked</div>
          <div class="tit-hero-fig"><strong><?php echo esc_html(number_format_i18n($verified)); ?></strong>from official filings</div>
        </div>
      </div>

      <div class="tit-stats">
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html(number_format_i18n($total)); ?></span><span class="tit-l">updates</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html(number_format_i18n($companies)); ?></span><span class="tit-l">employers</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html(number_format_i18n($countries)); ?></span><span class="tit-l">countries</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html(number_format_i18n($verified)); ?></span><span class="tit-l">from official filings</span></div>
      </div>

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

      <div class="tit-charts">
        <div class="tit-chart">
          <h3>Where the activity is</h3>
          <p class="tit-sub">Job location, falling back to the employer's headquarters.</p>
          <div class="tit-rank">
            <?php
            $cmax = $by_country ? max(array_map('intval', array_column($by_country, 'n'))) : 1;
            foreach ($by_country as $c) : ?>
              <div class="tit-rank-row">
                <span class="tit-rank-name"><?php echo esc_html($c['k']); ?></span>
                <span class="tit-rank-track"><span class="tit-rank-fill"
                  style="width:<?php echo esc_attr(max(4, round(100 * $c['n'] / $cmax))); ?>%"></span></span>
                <span class="tit-rank-n"><?php echo (int) $c['n']; ?></span>
              </div>
            <?php endforeach; ?>
          </div>
        </div>

        <div class="tit-chart">
          <h3>Growing or shrinking</h3>
          <p class="tit-sub">What each update means for headcount at that employer.</p>
          <div class="tit-rank">
            <?php
            $dmax = $by_direction ? max(array_map('intval', array_column($by_direction, 'n'))) : 1;
            foreach ($by_direction as $d) : ?>
              <div class="tit-rank-row">
                <span class="tit-rank-name"><?php echo esc_html($directions[$d['k']] ?? $d['k']); ?></span>
                <span class="tit-rank-track"><span class="tit-rank-fill"
                  style="width:<?php echo esc_attr(max(4, round(100 * $d['n'] / $dmax))); ?>%"></span></span>
                <span class="tit-rank-n"><?php echo (int) $d['n']; ?></span>
              </div>
            <?php endforeach; ?>
          </div>
        </div>
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
        <input type="search" id="tit-f-company" placeholder="Employer name"
               aria-label="Search by employer">
        <input type="search" id="tit-f-q" placeholder="Search anything"
               aria-label="Search headlines and read-throughs">
      </div>

      <p class="tit-note">
        The read-through is our interpretation. The headline and figures come
        from the linked source. Confidence reflects what the source is, not how
        sure we feel.
      </p>

      <div class="tit-table-scroll">
        <table class="tit-table">
          <thead>
            <tr>
              <th>What happened</th><th>Employer</th><th>Where</th>
              <th>What it means</th><th>How solid</th><th>Source</th>
            </tr>
          </thead>
          <tbody id="tit-rows">
            <?php foreach ($rows as $r) : ?>
              <tr>
                <td class="tit-headline">
                  <span class="tit-h"><?php echo esc_html($r['headline']); ?></span>
                  <span class="tit-rt"><?php echo esc_html($r['talent_readthrough']); ?></span>
                </td>
                <td><?php
                  $ck = $r['company_key'] ?? '';
                  if ($ck && function_exists('tit_company_url')) {
                      printf('<a href="%s">%s</a>', esc_url(tit_company_url($ck)), esc_html($r['company']));
                  } else {
                      echo esc_html($r['company']);
                  }
                ?></td>
                <td>
                  <?php
                  $place = $r['city'] ?: $r['hq_city'];
                  $cc    = $r['country'] ?: $r['hq_country'];
                  $is_hq = !$r['city'] && !$r['country'];
                  echo esc_html(trim(($place ? $place . ', ' : '') . $cc, ', '));
                  if ($is_hq) echo ' <span class="tit-hq" title="Employer headquarters, not a location named in the source">HQ</span>';
                  ?>
                </td>
                <td><span class="tit-tag tit-<?php echo esc_attr($r['signal_direction']); ?>"><?php echo esc_html($directions[$r['signal_direction']] ?? $r['signal_direction']); ?></span></td>
                <td><span class="tit-conf tit-c-<?php echo esc_attr($r['confidence']); ?>"><?php echo esc_html($r['confidence']); ?></span></td>
                <td><a href="<?php echo esc_url($r['source_url']); ?>" rel="nofollow noopener" target="_blank"><?php echo esc_html($r['source_name']); ?></a></td>
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

    // TIT_VERSION busts the cache on every deploy, which matters because FTP
    // uploads do not touch WordPress's own cache-busting.
    wp_enqueue_style('tit-dashboard', TIT_URL . 'assets/dashboard.css', array(), TIT_VERSION);
    wp_enqueue_script('tit-dashboard', TIT_URL . 'assets/dashboard.js', array(), TIT_VERSION, true);
    wp_localize_script('tit-dashboard', 'TIT', array(
        'api' => esc_url_raw(rest_url('talent/v1/')),
    ));
}
add_action('wp_enqueue_scripts', 'tit_enqueue_assets');
