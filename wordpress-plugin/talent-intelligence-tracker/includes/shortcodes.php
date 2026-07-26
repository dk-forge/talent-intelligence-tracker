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
            <p>The collectors are running but nothing has cleared the sourcing
               rules yet. Every record here must link to a primary source, so an
               empty table is the honest state rather than a broken one.</p>
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

    $rows = $wpdb->get_results(
        "SELECT signal_id, headline, talent_readthrough, company, pillar, signal_direction,
                city, country, hq_city, hq_country, confidence, source_url, source_name,
                published_date
           FROM {$table} WHERE is_current = 1
          ORDER BY COALESCE(published_date, DATE(captured_at)) DESC, row_id DESC
          LIMIT 50",
        ARRAY_A
    ) ?: array();

    $labels = array(
        'company_development' => 'Company developments',
        'leadership_change'   => 'Leadership changes',
        'rewards_comp'        => 'Rewards and compensation',
        'how_we_work'         => 'How we work',
    );
    ?>
    <div class="tit-wrap" id="tit-dashboard">

      <div class="tit-stats">
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html(number_format_i18n($total)); ?></span><span class="tit-l">signals</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html(number_format_i18n($companies)); ?></span><span class="tit-l">employers</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html(number_format_i18n($countries)); ?></span><span class="tit-l">countries</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo esc_html(number_format_i18n($verified)); ?></span><span class="tit-l">primary sourced</span></div>
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

      <div class="tit-filters">
        <select id="tit-f-pillar" aria-label="Filter by pillar">
          <option value="">All pillars</option>
          <?php foreach ($labels as $k => $v) : ?>
            <option value="<?php echo esc_attr($k); ?>"><?php echo esc_html($v); ?></option>
          <?php endforeach; ?>
        </select>
        <select id="tit-f-direction" aria-label="Filter by direction">
          <option value="">Any direction</option>
          <option value="hiring">Hiring</option>
          <option value="comp_shift">Compensation shift</option>
          <option value="displacement">Displacement risk</option>
          <option value="neutral">Neutral</option>
        </select>
        <select id="tit-f-country" aria-label="Filter by country">
          <option value="">All countries</option>
        </select>
        <input type="search" id="tit-f-company" placeholder="Company" aria-label="Filter by company">
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
              <th>Signal</th><th>Employer</th><th>Where</th>
              <th>Type</th><th>Confidence</th><th>Source</th>
            </tr>
          </thead>
          <tbody id="tit-rows">
            <?php foreach ($rows as $r) : ?>
              <tr>
                <td class="tit-headline">
                  <span class="tit-h"><?php echo esc_html($r['headline']); ?></span>
                  <span class="tit-rt"><?php echo esc_html($r['talent_readthrough']); ?></span>
                </td>
                <td><?php echo esc_html($r['company']); ?></td>
                <td>
                  <?php
                  $place = $r['city'] ?: $r['hq_city'];
                  $cc    = $r['country'] ?: $r['hq_country'];
                  $is_hq = !$r['city'] && !$r['country'];
                  echo esc_html(trim(($place ? $place . ', ' : '') . $cc, ', '));
                  if ($is_hq) echo ' <span class="tit-hq" title="Employer headquarters, not a location named in the source">HQ</span>';
                  ?>
                </td>
                <td><span class="tit-tag tit-<?php echo esc_attr($r['signal_direction']); ?>"><?php echo esc_html(str_replace('_', ' ', $r['signal_direction'])); ?></span></td>
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
    if (!is_singular()) return;
    global $post;
    if (!$post || !has_shortcode($post->post_content, 'talent_intelligence_dashboard')) return;

    // TIT_VERSION busts the cache on every deploy, which matters because FTP
    // uploads do not touch WordPress's own cache-busting.
    wp_enqueue_style('tit-dashboard', TIT_URL . 'assets/dashboard.css', array(), TIT_VERSION);
    wp_enqueue_script('tit-dashboard', TIT_URL . 'assets/dashboard.js', array(), TIT_VERSION, true);
    wp_localize_script('tit-dashboard', 'TIT', array(
        'api' => esc_url_raw(rest_url('talent/v1/')),
    ));
}
add_action('wp_enqueue_scripts', 'tit_enqueue_assets');
