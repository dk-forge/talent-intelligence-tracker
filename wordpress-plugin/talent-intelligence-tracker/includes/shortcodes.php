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
    $glance = tit_glance($table);
    $counts_by_country = array_column($wpdb->get_results(
        "SELECT COALESCE(country, hq_country) k, COUNT(*) n FROM {$table}
          WHERE is_current = 1 AND COALESCE(country, hq_country) IS NOT NULL
          GROUP BY k", ARRAY_A) ?: array(), 'n', 'k');
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
        <div class="tit-hero-top">
          <h2>Who is hiring, who is raising money, and who is changing leadership</h2>
          <div class="tit-live"><span class="tit-live-dot"></span>
            Live<?php if ($newest_run) : ?> ·
            <?php echo esc_html(date_i18n('M j, g:i A', strtotime($newest_run . ' UTC'))); ?>
            <?php endif; ?>
          </div>
        </div>

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
        <h3>What kind of update</h3>
        <p class="tit-sub">Every update falls into one of four kinds.</p>
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
          <h3>Where the activity is</h3>
          <p class="tit-sub">Job location, falling back to the employer's headquarters.</p>
          <div class="tit-rank">
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
          <h3>Growing or shrinking</h3>
          <p class="tit-sub">What each update means for headcount at that employer.</p>
          <div class="tit-rank">
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
        <input type="search" id="tit-f-company" placeholder="Employer name"
               aria-label="Search by employer">
        <input type="search" id="tit-f-q" placeholder="Search anything"
               aria-label="Search headlines and read-throughs">
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
add_action('wp_enqueue_scripts', 'tit_enqueue_assets');
