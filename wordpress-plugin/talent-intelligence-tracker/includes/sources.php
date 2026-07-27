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

    // Group by category so the page reads as a catalogue, not a dump.
    $by_cat = array();
    foreach ($sources as $s) {
        $by_cat[$s['category'] ?? 'Other'][] = $s;
    }
    ksort($by_cat);

    get_header();
    ?>
    <div class="tit-wrap tit-sources">
      <nav class="tit-crumb">
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Talent Intelligence Tracker</a>
        <span aria-hidden="true">›</span> Sources
      </nav>

      <h1>Where this data comes from</h1>
      <p class="tit-note">
        Every record on this tracker links to the document that makes the claim.
        This page lists every source, and is generated from the collectors
        themselves, so it cannot drift from what actually runs.
      </p>

      <div class="tit-stats">
        <div class="tit-stat"><span class="tit-n"><?php echo count($live); ?></span><span class="tit-l">running now</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo count($cand); ?></span><span class="tit-l">researched, not yet built</span></div>
        <div class="tit-stat"><span class="tit-n"><?php echo count($by_cat); ?></span><span class="tit-l">categories</span></div>
        <div class="tit-stat"><span class="tit-n"><?php
            echo count(array_unique(array_filter(array_column($sources, 'country'))));
        ?></span><span class="tit-l">countries named</span></div>
      </div>

      <div class="tit-callout">
        <strong>What "running now" means.</strong> A source counts as running
        only when a collector reads it, reports its health, and has a passing
        test. Everything else is listed as researched so the roadmap is public,
        but it is never counted as coverage. A source appearing on this page is
        not a claim that we cover it.
      </div>

      <?php foreach ($by_cat as $cat => $items) : ?>
        <h2 class="tit-src-cat"><?php echo esc_html($cat); ?></h2>
        <div class="tit-table-scroll">
          <table class="tit-table">
            <thead><tr>
              <th>Source</th><th>Status</th><th>Covers</th><th>Signals</th><th>Where</th>
            </tr></thead>
            <tbody>
            <?php foreach ($items as $s) : ?>
              <tr>
                <td class="tit-headline">
                  <span class="tit-h"><a href="<?php echo esc_url($s['url']); ?>"
                     rel="nofollow noopener" target="_blank"><?php echo esc_html($s['name']); ?></a></span>
                  <?php if (!empty($s['notes'])) : ?>
                    <span class="tit-rt"><?php echo esc_html($s['notes']); ?></span>
                  <?php endif; ?>
                </td>
                <td>
                  <?php if (($s['status'] ?? '') === 'live') : ?>
                    <span class="tit-conf tit-c-verified">running now</span>
                  <?php else : ?>
                    <span class="tit-conf">researched</span>
                  <?php endif; ?>
                </td>
                <td><?php echo esc_html($s['coverage'] ?? ''); ?></td>
                <td><?php echo esc_html(implode(', ', $s['signals'] ?? array())); ?></td>
                <td><?php echo esc_html($s['country'] ?: 'Worldwide'); ?></td>
              </tr>
            <?php endforeach; ?>
            </tbody>
          </table>
        </div>
      <?php endforeach; ?>

      <p class="tit-cite">
        Layoff and redundancy data is deliberately not collected here. It is read
        from the <a href="https://asktherecruiter.com/blog/ai-layoff-tracker/">AI
        Layoff Tracker</a>, so there is one source of truth per fact.
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Back to the tracker</a>
      </p>
    </div>
    <?php
    get_footer();
}

function tit_sources_title($title) {
    return get_query_var('tit_sources')
        ? 'Sources — Talent Intelligence Tracker'
        : $title;
}
add_filter('pre_get_document_title', 'tit_sources_title');
