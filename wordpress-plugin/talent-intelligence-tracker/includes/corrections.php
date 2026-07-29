<?php
/**
 * The corrections log: /talent-intelligence-tracker/corrections/
 *
 * Every correction made to already-published records, newest first.
 *
 * The entries are a hand-written constant rather than a table, and that is
 * deliberate: a correction is a piece of editorial writing about what was
 * wrong, and there is no machine that can produce "the badge said Hiring up on
 * a filing that discloses no hiring". The counts in each entry come from the
 * run that made the correction, so they are pasted once and then never move.
 *
 * Routed exactly like the sources page (rewrite rule, query var, template
 * redirect), so the two pages behave identically for a reader and a crawler.
 */

if (!defined('ABSPATH')) exit;

const TIT_CORRECTIONS_PATH = 'talent-intelligence-tracker/corrections';

function tit_corrections_rewrite() {
    add_rewrite_rule('^' . TIT_CORRECTIONS_PATH . '/?$', 'index.php?tit_corrections=1', 'top');
}
add_action('init', 'tit_corrections_rewrite');

function tit_corrections_query_var($vars) {
    $vars[] = 'tit_corrections';
    return $vars;
}
add_filter('query_vars', 'tit_corrections_query_var');

function tit_corrections_url() {
    return home_url('/' . TIT_CORRECTIONS_PATH . '/');
}

/**
 * Newest first. Each entry: date, how many rows, which fields, and what is
 * wrong in words a reader can check against the page.
 *
 * `status` is 'scheduled' or 'applied', and it is the switch this page turns
 * on. A defect we have found but not yet fixed is published as soon as it is
 * understood, because the reader looking at an inflated headline number today
 * is better served by knowing it is wrong than by our waiting for a tidy
 * past-tense sentence.
 *
 * WHEN A CORRECTION RUNS, the flip is deliberately small:
 *   1. 'status'     => 'applied', and add 'applied_on'
 *   2. 'projection' => 'measured', with the projected column KEPT and the
 *      measured one appended, so the table becomes before / projected / now
 *   3. the sentences marked TENSE
 * Everything else — the badge, the standing notice, the extra stat, the table
 * caption and its columns — derives from `status` and changes on its own.
 *
 * The projected column is never overwritten. When a projection turns out wrong
 * the difference is explained in a note, because a corrections page that
 * quietly revises its own numbers is doing the thing it exists to prevent.
 */
function tit_corrections_entries() {
    return array(
        array(
            'date'   => '2026-07-28',
            'applied_on' => '2026-07-29',
            'status' => 'applied',
            'title'  => 'Form D records said "Hiring up" on filings that disclose no hiring',
            'rows'   => 3005,
            'fields' => array('signal_direction', 'talent_readthrough'),
            'body'   => array(
                'Every record drawn from SEC Form D filings carried the badge
                 "Hiring up". A Form D reports money raised in a private
                 placement. It states an amount and it states nothing at all
                 about headcount, so the badge was our claim and not the
                 filing\'s.',
                'Those records also carried a read-through asserting that "capital
                 raised is spent on headcount within the following two to six
                 quarters". That sentence appears in no filing. It was a
                 generalisation printed identically on thousands of records,
                 presented as though it had been read off the document.',
                '3,005 records were corrected on 29 July 2026. The badge is now
                 "Headcount not stated", and each read-through says only what its
                 filing records: who raised how much, when, and the address on the
                 filing, followed by the gap named plainly. For example: "The
                 filing records the money only; it names no roles and no hiring
                 plan."',
            ),
        ),
        array(
            'date'   => '2026-07-28',
            'applied_on' => '2026-07-29',
            'status' => 'applied',
            'title'  => 'Entities that employ nobody were listed as employers, and they were inflating our money totals',
            'rows'   => 998,
            'fields' => array('withdrawn'),
            'body'   => array(
                '998 published records were not companies raising money to hire.
                 They were single-asset property vehicles, insurance separate
                 accounts and synthetic guaranteed investment contracts, all
                 published as startup funding.',
                'A large share of Form D filings are made by entities that exist
                 to hold an asset rather than to employ anyone: a limited company
                 formed to buy one building, a numbered series vehicle, a
                 non-traded credit fund. Published as employers raising money they
                 were useless to a recruiter or a job seeker, and because each
                 raise is large they distorted every money total on the tracker.',
                'Insurance products were the same failure in a different form. A
                 life insurer files a Form D for each variable life or annuity
                 product it sells, and the "amount sold" is premium collected from
                 policyholders, not capital the company raised. The largest single
                 record on the tracker was one of these, at $7.4bn.',
                'All 998 were withdrawn on 29 July 2026. Nothing was deleted: a
                 withdrawn record keeps its row and carries the reason it was
                 withdrawn, which is why they can still be counted here.',
            ),
            // Three columns on purpose. The projection we published stays next
            // to the measured result rather than being overwritten by it: a
            // corrections page that silently revises its own numbers is doing
            // the thing it exists to prevent. The gap is explained in the notes.
            'measured' => array(
                array('Funding records', '4,024', '3,026', '3,064'),
                array('Money raised', '$199.7bn', '$114.1bn', '$124.0bn'),
                array('New York', '$59.04bn', '$8.44bn', '$8.44bn across 294 records'),
                array('Real estate', '$13.16bn across 875 records', '$1.00bn across 1',
                      '$1.16bn across 4 records'),
            ),
            'notes' => array(
                array(
                    'We published a projection of $114.1bn and the result was $124.0bn.',
                    'Before this ran, this page said the money total would land near
                     $114.1bn. It landed at $124.0bn, about $10bn higher, and that
                     difference is not the correction falling short. $9.25bn of it
                     is ten records added by a new national-press collector whose
                     first run happened between the projection and the correction,
                     including a single $8.6bn semiconductor raise. The same run is
                     why the country count moved at the same time. The remaining
                     $0.9bn is twenty-one records from other collectors that were
                     already live but were missing from the copy of the database
                     the projection was computed against. We are leaving the
                     projection visible above rather than replacing it, because a
                     corrections page that quietly revises its own numbers is doing
                     the thing it exists to prevent.',
                ),
                array(
                    'What we can and cannot promise about what is left.',
                    'The withdrawal reached every record it could reach, and all
                     998 landed. Checked afterwards: no remaining record on the
                     tracker matches any of the vehicle name patterns, and the
                     "Hiring up" badge now appears on 43 records across the whole
                     tracker rather than 4,018. What we cannot promise is that no
                     inflated record survives anywhere, because a correction can
                     only reach the records the pipeline holds. If you see a
                     funding record whose employer is a numbered or single-address
                     entity, an insurance separate account, or a name that reads
                     like one building rather than one company, that is what a
                     survivor would look like, and we would like to be told.',
                ),
                array(
                    'A cost worth stating.',
                    'Form D filings in the real-estate industry group are excluded
                     outright, because the overwhelming majority of them are
                     single-asset vehicles. This does drop a small number of
                     genuine real-estate employers along with them, and the
                     dataset offers no field that separates the two. We think
                     carrying billions in vehicles that employ nobody is the worse
                     of the two errors, but it is a real cost and not a free one.',
                ),
                array(
                    'The fix was checked rather than assumed.',
                    'A first pass at these exclusions left the four largest
                     records on the tracker still wrong, because the rule was
                     written from the spelled-out phrase "guaranteed investment
                     contract" and the filings use the trade\'s abbreviations:
                     "Synthetic GICs issued to insurance carriers of BOLI/COLI
                     policies" at $4.21bn, "Synthetic GICs issued to IRC Section
                     529 plans" at $3.23bn, "Allocated Units of Precious Metals"
                     at $2.51bn, "AGL Institutional Life" at $0.59bn. Seven
                     filings and $12.4bn, found by reading the money list after
                     the fix instead of trusting it. They are included in the 998.',
                ),
            ),
        ),
    );
}

/** Entries whose correction has not run yet. */
function tit_corrections_outstanding($entries) {
    return array_values(array_filter($entries, fn($e) => ($e['status'] ?? '') === 'scheduled'));
}

function tit_corrections_template() {
    if (!get_query_var('tit_corrections')) return;
    tit_corrections_render(tit_corrections_entries());
    exit;
}
add_action('template_redirect', 'tit_corrections_template');

function tit_corrections_render($entries) {
    // Block theme: never get_header() directly. See tit_render_header().
    if (function_exists('tit_render_header')) tit_render_header(); else get_header();

    $total = 0;
    foreach ($entries as $e) $total += (int) $e['rows'];
    $pending = tit_corrections_outstanding($entries);
    $pending_rows = 0;
    foreach ($pending as $e) $pending_rows += (int) $e['rows'];
    ?>
    <div class="tit-wrap tit-corrections" id="tit-corrections">
      <nav class="tit-crumb">
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Talent Intelligence Tracker</a>
        <span aria-hidden="true">&rsaquo;</span> Corrections
      </nav>

      <h1>Corrections</h1>

      <?php
      // A reader landing on a list of corrections with no framing reads it as
      // a list of failures. It is the opposite: this page exists because the
      // errors were found and published rather than quietly patched.
      ?>
      <p class="tit-note">
        Everything on this tracker is read from a primary document, and
        sometimes what we say about that document is wrong. When it is, we fix
        it and we write it down here: what was wrong, how many records it
        touched, and what they say now. A system that finds and discloses its
        own errors is more trustworthy than one that hides them, and a
        correction you can read is the only way to tell the two apart. Records
        are corrected in place where the underlying document is unchanged, and
        withdrawn where they should never have been published; nothing is ever
        silently deleted.
      </p>

      <?php
      // A defect is published as soon as it is understood, not once it is
      // fixed. Anyone reading an inflated total right now is better served by
      // knowing it is wrong than by our waiting for a tidy past-tense sentence
      // — and a reader who checks a figure against this page and finds nothing
      // has been misled by the silence.
      if ($pending) : ?>
        <div class="tit-callout tit-pending">
          <strong>Some of these are not fixed yet.</strong>
          <?php printf(
              esc_html('%1$s published %2$s below %3$s known to be wrong and %4$s scheduled to be corrected or withdrawn. Until that runs, the figures on the tracker still include %5$s. Each entry says what is affected and what the numbers will be afterwards.'),
              esc_html(number_format_i18n($pending_rows)),
              $pending_rows === 1 ? 'record' : 'records',
              $pending_rows === 1 ? 'is' : 'are',
              $pending_rows === 1 ? 'is' : 'are',
              $pending_rows === 1 ? 'it' : 'them'
          ); ?>
        </div>
      <?php endif; ?>

      <div class="tit-stats">
        <div class="tit-stat">
          <span class="tit-n"><?php echo count($entries); ?></span>
          <span class="tit-l"><?php echo count($entries) === 1 ? 'correction' : 'corrections'; ?></span>
        </div>
        <div class="tit-stat">
          <span class="tit-n"><?php echo esc_html(number_format_i18n($total)); ?></span>
          <span class="tit-l">records affected</span>
        </div>
        <?php if ($pending) : ?>
          <div class="tit-stat">
            <span class="tit-n"><?php echo esc_html(number_format_i18n($pending_rows)); ?></span>
            <span class="tit-l">still to be applied</span>
          </div>
        <?php endif; ?>
      </div>

      <?php foreach ($entries as $e) : ?>
        <div class="tit-correction">
          <p class="tit-meta">
            <time datetime="<?php echo esc_attr($e['date']); ?>"><?php
              echo esc_html(date_i18n('j F Y', strtotime($e['date'])));
            ?></time>
            <span aria-hidden="true">&middot;</span>
            <?php echo esc_html(number_format_i18n((int) $e['rows'])); ?>
            <?php echo (int) $e['rows'] === 1 ? 'record' : 'records'; ?>
            <span aria-hidden="true">&middot;</span>
            <?php
            // The literal column names, because someone reading this may be
            // holding an export taken before the correction.
            foreach ($e['fields'] as $i => $f) {
                echo $i ? ', ' : '';
                echo '<code>' . esc_html($f) . '</code>';
            }
            $scheduled = ($e['status'] ?? '') === 'scheduled';
            ?>
            <span aria-hidden="true">&middot;</span>
            <span class="tit-conf <?php echo $scheduled ? 'tit-c-degraded' : 'tit-c-verified'; ?>"><?php
              // Two dates, because they are two different facts: when we found
              // it, and when the published records actually changed.
              echo $scheduled ? 'not yet applied' : (
                  !empty($e['applied_on'])
                      ? 'applied ' . esc_html(date_i18n('j M Y', strtotime($e['applied_on'])))
                      : 'applied');
            ?></span>
          </p>
          <h2><?php echo esc_html($e['title']); ?></h2>
          <?php foreach ($e['body'] as $para) : ?>
            <p><?php echo esc_html(preg_replace('/\s+/', ' ', trim($para))); ?></p>
          <?php endforeach; ?>

          <?php
          // While a correction is pending this is a two-column projection.
          // Once it has run it becomes three columns, and the projection we
          // published stays beside the measured result instead of being
          // overwritten by it. Anyone who read the projection can see what we
          // said, what happened, and the difference.
          $table = $e['projection'] ?? $e['measured'] ?? null;
          if ($table) : ?>
            <?php // NOT .tit-table: below 860px that class turns a table into
                  // cards and hides the header row, which would leave these as
                  // unlabelled numbers side by side. These columns fit a 375px
                  // phone as an ordinary table. ?>
            <table class="tit-projection">
              <caption><?php echo $scheduled
                ? 'Projected effect, not yet applied'
                : 'What we projected, and what actually happened'; ?></caption>
              <thead><tr>
                <th></th>
                <th>Before</th>
                <?php if (!$scheduled) : ?><th>We projected</th><?php endif; ?>
                <th><?php echo $scheduled ? 'After the correction' : 'Measured now'; ?></th>
              </tr></thead>
              <tbody>
              <?php foreach ($table as $line) : ?>
                <tr>
                  <th scope="row"><?php echo esc_html($line[0]); ?></th>
                  <?php foreach (array_slice($line, 1) as $cell) : ?>
                    <td><?php echo esc_html($cell); ?></td>
                  <?php endforeach; ?>
                </tr>
              <?php endforeach; ?>
              </tbody>
            </table>
            <?php if (!$scheduled) : ?>
              <p class="tit-asof">Measured on the live tracker,
                <?php echo esc_html(date_i18n('j F Y')); ?>. These figures keep
                moving as new records arrive, so treat them as a snapshot of the
                correction&rsquo;s effect rather than a current total.</p>
            <?php endif; ?>
          <?php endif; ?>

          <?php foreach (($e['notes'] ?? array()) as $note) : ?>
            <div class="tit-callout">
              <strong><?php echo esc_html($note[0]); ?></strong>
              <?php echo esc_html(preg_replace('/\s+/', ' ', trim($note[1]))); ?>
            </div>
          <?php endforeach; ?>
        </div>
      <?php endforeach; ?>

      <p class="tit-cite">
        Spotted something wrong? Every record links to the document it came
        from, so it can be checked. Write to
        <a href="/blog/contact/">the contact page</a> and it will be corrected
        here.
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/sources/')); ?>">Where this data comes from</a>
        &middot;
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Back to the tracker</a>
      </p>
    </div>
    <?php
    if (function_exists('tit_render_footer')) tit_render_footer(); else get_footer();
}

function tit_corrections_title($title) {
    return get_query_var('tit_corrections')
        ? 'Corrections — Talent Intelligence Tracker'
        : $title;
}
add_filter('pre_get_document_title', 'tit_corrections_title');
