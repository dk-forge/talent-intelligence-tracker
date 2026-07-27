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

function tit_sources_render($sources) {
    $live = array_values(array_filter($sources, fn($s) => ($s['status'] ?? '') === 'live'));
    $cand = array_values(array_filter($sources, fn($s) => ($s['status'] ?? '') !== 'live'));

    $countries  = array_values(array_unique(array_filter(array_column($sources, 'country'))));
    $categories = array_values(array_unique(array_filter(array_column($sources, 'category'))));
    sort($countries); sort($categories);

    get_header();
    ?>
    <div class="tit-wrap tit-sources" id="tit-sources">
      <nav class="tit-crumb">
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Talent Intelligence Tracker</a>
        <span aria-hidden="true">›</span> Sources
      </nav>

      <h1>Where this data comes from</h1>
      <p class="tit-note">
        Every record on this tracker links to the document that makes the claim.
        This page lists every source we read and every source we have researched,
        and is generated from the collectors themselves so it cannot drift from
        what actually runs.
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

      <div class="tit-filters">
        <select id="tit-s-status" aria-label="Filter by status">
          <option value="">Running and researched</option>
          <option value="live">Running now</option>
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

      <p class="tit-note" id="tit-s-count"><?php echo count($sources); ?> sources</p>

      <div class="tit-table-scroll">
        <table class="tit-table">
          <thead><tr>
            <th>Source</th><th>Status</th><th>Covers</th><th>Signals</th><th>Where</th>
          </tr></thead>
          <tbody id="tit-s-rows">
          <?php foreach ($sources as $s) :
            $sig = implode(', ', array_slice($s['signals'] ?? array(), 0, 4)); ?>
            <tr data-status="<?php echo esc_attr($s['status']); ?>"
                data-country="<?php echo esc_attr($s['country']); ?>"
                data-category="<?php echo esc_attr($s['category']); ?>"
                data-search="<?php echo esc_attr(strtolower($s['name'] . ' ' . $s['category'] . ' ' . $sig)); ?>">
              <td class="tit-headline">
                <span class="tit-h"><a href="<?php echo esc_url($s['url']); ?>"
                   rel="nofollow noopener" target="_blank"><?php echo esc_html($s['name']); ?></a></span>
                <?php if (!empty($s['notes'])) : ?>
                  <span class="tit-rt"><?php echo esc_html($s['notes']); ?></span>
                <?php endif; ?>
              </td>
              <td>
                <?php if ($s['status'] === 'live') : ?>
                  <span class="tit-conf tit-c-verified">running now</span>
                <?php else : ?>
                  <span class="tit-conf">researched</span>
                <?php endif; ?>
              </td>
              <td><?php echo esc_html($s['coverage'] ?? ''); ?></td>
              <td><?php echo esc_html($sig); ?></td>
              <td><?php echo esc_html($s['country'] ?: 'Worldwide'); ?></td>
            </tr>
          <?php endforeach; ?>
          </tbody>
        </table>
      </div>

      <p class="tit-cite">
        Layoff and redundancy data is deliberately not collected here. It is read
        from the <a href="https://asktherecruiter.com/blog/ai-layoff-tracker/">AI
        Layoff Tracker</a>, so there is one source of truth per fact.
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
        var shown = 0;
        rows.forEach(function (tr) {
          var ok = (!f.status.value   || tr.dataset.status   === f.status.value)
                && (!f.country.value  || tr.dataset.country  === f.country.value)
                && (!f.category.value || tr.dataset.category === f.category.value)
                && (!term || tr.dataset.search.indexOf(term) !== -1);
          tr.style.display = ok ? '' : 'none';
          if (ok) shown++;
        });
        count.textContent = shown + (shown === 1 ? ' source' : ' sources');
      }

      Object.keys(f).forEach(function (k) { f[k].addEventListener('change', apply); });
      q.addEventListener('input', apply);
    })();
    </script>
    <?php
    get_footer();
}

function tit_sources_title($title) {
    return get_query_var('tit_sources')
        ? 'Sources — Talent Intelligence Tracker'
        : $title;
}
add_filter('pre_get_document_title', 'tit_sources_title');
