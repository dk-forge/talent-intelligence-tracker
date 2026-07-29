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
 * WHEN THE CORRECTION RUNS, the flip is deliberately small:
 *   1. 'status'      => 'applied'
 *   2. 'projection'  => 'measured', once the figures are the observed ones
 *   3. the three sentences marked TENSE below
 * Everything else — the badge, the standing notice, the stat labels, the
 * before/after heading — is derived from `status` and changes on its own.
 */
function tit_corrections_entries() {
    return array(
        array(
            'date'   => '2026-07-28',
            'status' => 'scheduled',
            'title'  => 'Form D records say "Hiring up" on filings that disclose no hiring',
            'rows'   => 3005,
            'fields' => array('signal_direction', 'talent_readthrough'),
            'body'   => array(
                // TENSE: "carries" -> "carried", "says" -> "said".
                'Every record drawn from SEC Form D filings carries the badge
                 "Hiring up". A Form D reports money raised in a private
                 placement. It states an amount and it states nothing at all
                 about headcount, so the badge is our claim and not the
                 filing\'s.',
                'Those records also carry a read-through asserting that "capital
                 raised is spent on headcount within the following two to six
                 quarters". That sentence appears in no filing. It is a
                 generalisation printed identically on thousands of records,
                 presented as though it had been read off the document.',
                // TENSE: "are scheduled to be corrected" -> "have been corrected".
                '3,005 records are scheduled to be corrected. The badge becomes
                 "Headcount not stated", and each read-through will say only what
                 its filing records: who raised how much, when, and the address on
                 the filing, followed by the gap named plainly. For example: "The
                 filing records the money only; it names no roles and no hiring
                 plan." Until that runs, the badge and the sentence you see on a
                 Form D record are the wrong ones described here.',
            ),
        ),
        array(
            'date'   => '2026-07-28',
            'status' => 'scheduled',
            'title'  => 'Entities that employ nobody are listed as employers, and they are inflating our money totals',
            'rows'   => 998,
            'fields' => array('withdrawal pending'),
            'body'   => array(
                '998 published records are not companies raising money to hire.
                 They are single-asset property vehicles, insurance separate
                 accounts and synthetic guaranteed investment contracts, all
                 published as startup funding.',
                'A large share of Form D filings are made by entities that exist
                 to hold an asset rather than to employ anyone: a limited company
                 formed to buy one building, a numbered series vehicle, a
                 non-traded credit fund. Published as employers raising money they
                 are useless to a recruiter or a job seeker, and because each
                 raise is large they distort every money total on the tracker.',
                'Insurance products are the same failure in a different form. A
                 life insurer files a Form D for each variable life or annuity
                 product it sells, and the "amount sold" is premium collected from
                 policyholders, not capital the company raised. The largest single
                 record on the tracker is one of these, at $7.4bn.',
                // TENSE: "are scheduled for withdrawal" -> "have been withdrawn";
                // drop the last two sentences, which describe the wait.
                'These 998 records are scheduled for withdrawal. Nothing will be
                 deleted: a withdrawn record keeps its row and carries the reason
                 it was withdrawn. Until that runs, every figure on the dashboard
                 still includes them, so the headline money total is currently
                 overstated by roughly $86bn. The projection below is what the
                 tracker will show afterwards.',
            ),
            'projection' => array(
                array('Funding records', '4,024', '3,026'),
                array('Money raised', '$199.7bn', '$114.1bn'),
                array('New York', '$59.04bn', '$8.44bn'),
                array('Real estate', '$13.16bn across 875 records', '$1.00bn across 1 record'),
            ),
            'notes' => array(
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
              echo $scheduled ? 'not yet applied' : 'applied';
            ?></span>
          </p>
          <h2><?php echo esc_html($e['title']); ?></h2>
          <?php foreach ($e['body'] as $para) : ?>
            <p><?php echo esc_html(preg_replace('/\s+/', ' ', trim($para))); ?></p>
          <?php endforeach; ?>

          <?php if (!empty($e['projection'])) : ?>
            <?php // Labelled as a projection while it is one. When the run
                  // lands, the same table is the measured result. ?>
            <table class="tit-table tit-projection">
              <caption><?php echo $scheduled
                ? 'Projected effect, not yet applied'
                : 'Measured effect'; ?></caption>
              <thead><tr>
                <th></th><th>Now</th>
                <th><?php echo $scheduled ? 'After the correction' : 'After'; ?></th>
              </tr></thead>
              <tbody>
              <?php foreach ($e['projection'] as $line) : ?>
                <tr>
                  <th scope="row"><?php echo esc_html($line[0]); ?></th>
                  <td><?php echo esc_html($line[1]); ?></td>
                  <td><?php echo esc_html($line[2]); ?></td>
                </tr>
              <?php endforeach; ?>
              </tbody>
            </table>
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
