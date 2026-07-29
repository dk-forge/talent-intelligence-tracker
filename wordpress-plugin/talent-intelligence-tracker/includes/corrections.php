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
 * Newest first. Each entry: date, how many rows, which fields, and what was
 * wrong in words a reader can check against the page.
 */
function tit_corrections_entries() {
    return array(
        array(
            'date'   => '2026-07-28',
            'title'  => 'Form D rows said "Hiring up" on filings that disclose no hiring',
            'rows'   => 3009,
            'fields' => array('signal_direction', 'talent_readthrough'),
            'body'   => array(
                'Every record drawn from SEC Form D filings carried the badge
                 "Hiring up". A Form D reports money raised in a private
                 placement. It states an amount and it states nothing at all
                 about headcount, so the badge was our claim and not the
                 filing\'s.',
                'Those rows also carried a read-through asserting that "capital
                 raised is spent on headcount within the following two to six
                 quarters". That sentence appears in no filing. It was a
                 generalisation printed identically on thousands of records,
                 presented as though it had been read off the document.',
                'The badge is now "Headcount not stated", and each read-through
                 says only what its filing records: who raised how much, when,
                 and the address on the filing, followed by the gap named
                 plainly. For example: "The filing records the money only; it
                 names no roles and no hiring plan."',
            ),
        ),
        array(
            'date'   => '2026-07-28',
            'title'  => 'Entities that employ nobody were listed as employers',
            'rows'   => 994,
            'fields' => array('withdrawn'),
            'body'   => array(
                'A large share of Form D filings are made by entities that exist
                 to hold an asset rather than to employ anyone: a limited company
                 formed to buy one building, a numbered series vehicle, a
                 non-traded credit fund. They were being published as employers
                 raising money, which is useless to a recruiter or a job seeker
                 and, because each raise is large, badly distorted every total on
                 the page.',
                'Insurance and annuity products were the same failure in a
                 different form. A life insurer files a Form D for each variable
                 life or annuity product it sells, and the "amount sold" is
                 premium collected from policyholders, not capital the company
                 raised. The largest single record on the tracker was one of
                 these, at $7.4bn.',
                'These records have been withdrawn. Nothing is deleted: a
                 withdrawn record keeps its row and carries the reason it was
                 withdrawn.',
                'This is why the total money raised fell from roughly $200bn to
                 roughly $120bn on this date. The drop is the correction working,
                 not a loss of data. The old figure counted property vehicles and
                 insurance premiums as company fundraising; the new one does not.',
            ),
            'note'   => 'Form D filings in the real-estate industry group are now
                excluded outright, because the overwhelming majority of them are
                single-asset vehicles. This does drop a small number of genuine
                real-estate employers along with them, and the dataset offers no
                field that separates the two. We think carrying billions in
                vehicles that employ nobody is the worse of the two errors, but
                it is a real cost and not a free one.',
        ),
    );
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

      <div class="tit-stats">
        <div class="tit-stat">
          <span class="tit-n"><?php echo count($entries); ?></span>
          <span class="tit-l"><?php echo count($entries) === 1 ? 'correction' : 'corrections'; ?></span>
        </div>
        <div class="tit-stat">
          <span class="tit-n"><?php echo esc_html(number_format_i18n($total)); ?></span>
          <span class="tit-l">records affected</span>
        </div>
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
            ?>
          </p>
          <h2><?php echo esc_html($e['title']); ?></h2>
          <?php foreach ($e['body'] as $para) : ?>
            <p><?php echo esc_html(preg_replace('/\s+/', ' ', trim($para))); ?></p>
          <?php endforeach; ?>
          <?php if (!empty($e['note'])) : ?>
            <div class="tit-callout">
              <strong>A cost worth stating.</strong>
              <?php echo esc_html(preg_replace('/\s+/', ' ', trim($e['note']))); ?>
            </div>
          <?php endif; ?>
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
