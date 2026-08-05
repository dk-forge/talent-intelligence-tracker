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
            'date'   => '2026-07-31',
            'applied_on' => '2026-07-31',
            'measured_on' => '2026-07-31',
            'status' => 'applied',
            'title'  => 'Money nobody raised: takeovers paid in shares, running totals, and one round counted twice',
            'rows'   => 318,
            'fields' => array('withdrawn'),
            'body'   => array(
                // This entry was published as pending on 31 July 2026 and the
                // run landed the same day, so both tenses have been true on this
                // page within hours of each other. If a defect is ever disclosed
                // here again before it is fixed, only // comments may carry the
                // future wording: the tense tests strip these and not /* */, so
                // a block comment quoting it is read as page copy.
                '318 published records reported money that no company raised. A
                 Form D reports an "amount sold". For three kinds of filing,
                 that amount is not capital arriving to be spent on anything.
                 We published all three here as funding rounds. Between them
                 they overstated the money total on this tracker by $14.25bn.',
                'The first is a takeover paid for in shares. One company buys
                 another and hands the sellers stock instead of cash. That stock
                 goes on a Form D, and its value appears in the same box a
                 startup uses to report a round. So one filing recorded that
                 Danaher is acquiring Masimo at $180 a share, for "total
                 consideration of $9.9 billion". We published it as Masimo
                 raising $9.9bn. Another recorded a merger of W.D. Company, Inc.
                 with and into Dillard\'s, Inc., valued off a closing share price
                 with no cash anywhere in the document. We published that one as
                 $2.39bn. The form asks the issuer outright whether the offering
                 is part of a business combination, and 177 published records
                 answer yes. That answer is a field in the public data set, and
                 we had never read it.',
                'Seven of those 177 stay. Those seven say, in the filer\'s own
                 words, that cash came in and the company then spent it on a
                 deal. Two examples: "a portion of the proceeds of the sale of
                 securities to investors was used to acquire", "funds are being
                 used to acquire a hospital". That is a real raise and the
                 record is true.',
                'The second is an offering with no ceiling that has been selling
                 for years. Where the amount offered is the word "Indefinite",
                 the figure filed is everything sold since the first sale rather
                 than a round. OPTCAPITAL LLC\'s $1.77bn is one of these. It is
                 the fourteenth annual amendment to an offering whose first sale
                 was in July 2012, so the figure covers fourteen years of sales
                 presented as one raise.',
                'The third is the same round counted twice. A Form D amendment
                 restates the running total for an offering already filed, so an
                 offering we published twice is one raise on the page twice.
                 Fluidstack appears at $450m in January and $842m in May: one
                 offering, one raise, and the May figure is the whole of it. Its
                 separate $730m offering opened in June under its own file
                 number, and it is a different raise. That is why we match these
                 on the offering\'s number and never on the company.',
                'We withdrew all 318 on 31 July 2026 rather than restating them.
                 What was wrong was not the figure, which is what the filing
                 says. What was wrong is that we called the figure money raised
                 at all, and there is no smaller true number to put in its
                 place. We deleted nothing. A withdrawn record keeps its row and
                 the reason it went, so we can still count it here.',
            ),
            // The BEFORE column is read off the same aggregate the dashboard
            // prints, not off a query of our own: a corrections page quoting
            // $118.4bn beside a dashboard headline of $122B is a reader's first
            // reason to distrust both.
            'measured' => array(
                array('Funding records', '3,344', '3,026', '3,026'),
                array('Money raised', '$122.0bn', '$107.7bn', '$107.7bn'),
                array('Records drawn from Form D', '3,013', '2,695', '2,695'),
                array('Takeovers published as raises', '177 records, $8.5bn',
                      '7 records, $0.7bn', '7 records, $0.7bn'),
                array('Employers with a funding record', '3,127', '2,906', '2,937'),
            ),
            'notes' => array(
                array(
                    'One row of the projection missed, and it stays visible.',
                    'Four of the five figures landed exactly where this page said
                     they would. The fifth did not: we said 2,906 employers would
                     keep a funding record, and the answer came out at 2,937.
                     Those 31 are not records the withdrawal missed. They are
                     employers that arrived between the projection and the run.
                     A historical backfill and a night of collection added 32
                     funding records worth $3.55bn while this correction sat in
                     the queue. We leave the projection above rather than
                     replacing it, because a corrections page that quietly
                     revises its own numbers is doing the thing it exists to
                     prevent.',
                ),
                array(
                    'Keeping the last figure filed, not the first and not the sum.',
                    'An amendment restates an offering\'s running total, so the
                     last filing for an offering is the whole raise and every
                     earlier one is that same money again. Across the 66
                     offerings this touches, the last figure is also the largest
                     in 65 of them. In the one exception the filer revised its
                     own total downwards, and its latest answer is still the one
                     we should be showing.',
                ),
                array(
                    'What each rule costs in real records, measured rather than assumed.',
                    'Withdrawing on the word "Indefinite" alone would take 138
                     more records worth $1.70bn, including a $200m round that
                     opened this quarter. An uncapped offering is only a running
                     total once it has been running. So the rule also requires
                     the first sale to be more than a year before the filing.
                     Matching duplicate offerings on the company rather than on
                     the offering\'s file number would delete Fluidstack\'s
                     genuinely separate $730m. And the takeover rule is the one
                     with a cost we cannot measure away: 115 of the 177 filings
                     answer yes and then explain nothing. Among the 62 that do
                     explain, 7 turn out to be cash raises. If the silent ones
                     behave the same way, roughly a dozen real raises go with
                     them.',
                ),
                array(
                    'A rule we considered and rejected.',
                    'Eleven of these filings say the money paid a sales
                     commission. That is what a company pays a broker to sell
                     securities, and a merger does not need one. Using that to
                     rescue a record would have kept five filings that state in
                     words that the shares were merger consideration. Four of
                     those five are bank mergers where the fee is the adviser\'s.
                     The rule rescued fewer records than it wrongly kept, so we
                     do not use it.',
                ),
                array(
                    'What we cannot promise about the records that remain.',
                    'Two things. The quarterly data set that carries these fields
                     only appears once a quarter has ended. So this pass does not
                     cover 9 records filed this month, worth $0.09bn, and we
                     check those when that data set appears. A filing can answer
                     yes to the takeover question and then explain nothing. We
                     cannot tell that filing apart from a cash placement that
                     happens to fund a deal, so some real raises go with the
                     rest. A survivor would look like a funding record for a
                     company that a buyer was taking over rather than one
                     raising money. Or it would look like a figure covering
                     years of an evergreen fund\'s sales. If you see one, we
                     would like to be told.',
                ),
            ),
        ),
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
                /* The badge name is quoted here so a reader can go and find it,
                   which is the whole point of a corrections log. It was
                   "Headcount not stated" and is now Title Cased with the rest of
                   the page's labels (see the one-vocabulary note in
                   shortcodes.php). tests/test_corrections_page.py reads the live
                   label out of shortcodes.php and fails if this quote drifts
                   from it, which is what caught the rename: a log describing a
                   badge that no longer exists sends a reader looking for
                   something they cannot find. */
                'We corrected 3,005 records on 29 July 2026. The badge is now
                 "Headcount Not Stated". Each read-through says only what its
                 filing records: who raised how much, when, and the address on
                 the filing, followed by the gap named plainly. For example:
                 "The filing records the money only; it names no roles and no
                 hiring plan."',
            ),
        ),
        array(
            'date'   => '2026-07-28',
            'applied_on' => '2026-07-29',
            'status' => 'applied',
            'title'  => 'We listed entities that employ nobody as employers, and they were inflating our money totals',
            'rows'   => 998,
            'fields' => array('withdrawn'),
            'body'   => array(
                '998 published records were not companies raising money to hire.
                 They were single-asset property vehicles, insurance separate
                 accounts and synthetic guaranteed investment contracts, and we
                 published all of them as startup funding.',
                'A large share of Form D filings come from entities that exist
                 to hold an asset rather than to employ anyone. Examples: a
                 limited company formed to buy one building, a numbered series
                 vehicle, a non-traded credit fund. We published them as
                 employers raising money, where they were useless to a recruiter
                 or a job seeker. Because each raise is large, they distorted
                 every money total on the tracker.',
                'Insurance products were the same failure in a different form. A
                 life insurer files a Form D for each variable life or annuity
                 product it sells, and the "amount sold" is premium collected from
                 policyholders, not capital the company raised. The largest single
                 record on the tracker was one of these, at $7.4bn.',
                'We withdrew all 998 on 29 July 2026. We deleted nothing. A
                 withdrawn record keeps its row and carries the reason we
                 withdrew it, so we can still count it here.',
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
                    'The money total has since fallen from $124.0bn to $101.4bn, and
                     we corrected nothing downwards to make that happen.',
                    'The figure above is what the live tracker held on 29 July 2026,
                     the day this correction ran, and we leave it as measured.
                     Two later passes moved it. We re-issued every stale
                     company_key, which merged eleven employers we had counted as
                     more than eleven. We also found five funding amounts off by
                     a factor of a million, because the multiplier parser read
                     only English. Two of those five were Danish kroner sitting
                     in a US dollar column. This page promises we leave amounts
                     in other currencies out rather than converting them at a
                     rate nobody published, so we removed those dollar figures
                     rather than restating them. The current total is on the
                     dashboard, computed on request. This page does not restate
                     it, because a corrections log that keeps rewriting its own
                     history is not a log.',
                ),
                array(
                    'We published a projection of $114.1bn and the result was $124.0bn.',
                    'Before this ran, this page said the money total would land near
                     $114.1bn. It landed at $124.0bn, about $10bn higher, and that
                     difference is not the correction falling short. $9.25bn of it
                     is ten records added by a new national-press collector whose
                     first run happened between the projection and the correction,
                     including a single $8.6bn semiconductor raise. The same run is
                     why the country count moved at the same time. The remaining
                     $0.9bn is twenty-one records from other collectors. Those
                     collectors were already live, but the copy of the database
                     we computed the projection against was missing their
                     records. We are leaving the projection visible above rather
                     than replacing it, because a
                     corrections page that quietly revises its own numbers is doing
                     the thing it exists to prevent.',
                ),
                array(
                    'What we can and cannot promise about the records that remain.',
                    'The withdrawal reached every record it could reach, and all
                     998 landed. We checked afterwards. No remaining record on
                     the tracker matches any of the vehicle name patterns. The
                     "Hiring up" badge now appears on 43 records across the whole
                     tracker, rather than 4,018 records. What we cannot promise
                     is that no inflated record survives anywhere, because a
                     correction can only reach the records the pipeline holds. A
                     survivor would look like a funding record whose employer is
                     a numbered or single-address entity. It could also be an
                     insurance separate account, or a name that reads like one
                     building rather than one company. If you see one, we would
                     like to be told.',
                ),
                array(
                    'A cost worth stating.',
                    'We exclude Form D filings in the real-estate industry group
                     outright, because the overwhelming majority of them are
                     single-asset vehicles. This does drop a small number of
                     genuine real-estate employers along with them, and the
                     dataset offers no field that separates the two. We think
                     carrying billions in vehicles that employ nobody is the worse
                     of the two errors, but it is a real cost and not a free one.',
                ),
                array(
                    'We checked the fix rather than assuming it worked.',
                    'A first pass at these exclusions left the four largest
                     records on the tracker still wrong. We had written the rule
                     from the spelled-out phrase "guaranteed investment
                     contract", and the filings use the trade\'s abbreviations
                     instead. The first two are "Synthetic GICs issued to
                     insurance carriers of BOLI/COLI policies" at $4.21bn and
                     "Synthetic GICs issued to IRC Section 529 plans" at
                     $3.23bn. The other two are "Allocated Units of Precious
                     Metals" at $2.51bn and "AGL Institutional Life" at $0.59bn.
                     Seven filings and $12.4bn in all, found by reading the money
                     list after the fix instead of trusting it. The 998 include
                     them.',
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
        Every figure on this tracker comes from a primary document, and
        sometimes what we say about that document is wrong. When it is, we fix
        it and we write it down here: what was wrong, how many records it
        touched, and what they say now. A system that finds and discloses its
        own errors is more trustworthy than one that hides them. A correction
        you can read is the only way to tell the two apart. We correct a record
        in place where the underlying document has not changed. We withdraw it
        where we should never have published it. Either way, nothing is ever
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
              esc_html('%1$s published %2$s below %3$s wrong, and %4$s in the queue for correction or withdrawal. Until that runs, the figures on the tracker still include %5$s. Each entry says what it affects and what the numbers will be afterwards.'),
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
              <?php
              /*
                THE DATE THE FIGURES WERE MEASURED, not the date the page is
                being read. This printed date_i18n('j F Y'), so a table measured
                on 29 July 2026 was captioned with today's date, whatever today
                was -- and it kept doing so while a LATER correction moved the
                money total from $124.0bn down to about $99bn. A stale figure is
                bad; a stale figure wearing today's date as a credential is the
                thing this page exists to prevent.
              */
              $measured_ts = strtotime(($e['measured_on'] ?? $e['applied_on'] ?? $e['date']) . ' 00:00:00 UTC');
              ?>
              <p class="tit-asof">Measured on the live tracker
                <?php echo esc_html($measured_ts ? date_i18n('j F Y', $measured_ts) : ''); ?>,
                the day this correction ran. It is a snapshot of what this
                correction did, not a current total, and later corrections have
                moved these figures again: see the note below where one has.</p>
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
        from, so you can check it. Write to
        <a href="/blog/contact/">the contact page</a> and we will correct it
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
        ? 'Corrections · Talent Intelligence Tracker'
        : $title;
}
add_filter('pre_get_document_title', 'tit_corrections_title');

/**
 * A page that exists to be checked has to say so where it is found.
 *
 * Fixed prose rather than a computed count, and deliberately: the figures on
 * this page are individual corrections with dates on them, and a description
 * that summarised "N corrections" would go stale the day one lands and would
 * read as a defect count rather than as a record.
 */
function tit_corrections_head() {
    if (!get_query_var('tit_corrections')) return;
    if (!function_exists('tit_head_description')) return;
    tit_head_description(
        'Every figure this tracker has published and later changed, what it was, '
        . 'what it is now, and what caused the difference. Corrections append a '
        . 'revision rather than overwriting a record, so what we said on an '
        . 'earlier date stays answerable.'
    );
    echo '<link rel="canonical" href="'
       . esc_url(home_url('/talent-intelligence-tracker/corrections/')) . '" />' . "\n";
}
add_action('wp_head', 'tit_corrections_head', 1);
