<?php
/**
 * The methodology page: /talent-intelligence-tracker/methodology/
 *
 * What counts as a record, where it comes from, how it is checked, deduplicated
 * and measured, and what the tracker cannot see. It is the long form of the
 * answers in page_faq.php and of the rules written down in the repository's own
 * docs (CLAUDE.md, docs/VERIFICATION_TIERS.md), so a journalist can read here
 * what an engineer would read there.
 *
 * Until 1.116.0 this path redirected to /sources/ (found live 2026-11-11, when
 * it had no route and WordPress sent it to the SIBLING tracker's methodology
 * page). The redirect was the right stopgap and the wrong destination: a list
 * of sources is not an account of method. The route now renders a page.
 *
 * Prose is fixed on purpose and carries NO figures. A number typed into method
 * copy is the first thing to go stale; every live figure on this site is
 * computed and lives on the page that owns it (sources, recall, corrections),
 * and this page links there instead. The one exception is the intro, which
 * reads its counts from the same facts bundle as every other page.
 *
 * Styled by the classes the corrections and sources pages already use.
 */

if (!defined('ABSPATH')) exit;

const TIT_METHODOLOGY_PATH = 'talent-intelligence-tracker/methodology';

function tit_methodology_rewrite() {
    add_rewrite_rule('^' . TIT_METHODOLOGY_PATH . '/?$', 'index.php?tit_methodology=1', 'top');
}
add_action('init', 'tit_methodology_rewrite');

function tit_methodology_query_var($vars) {
    $vars[] = 'tit_methodology';
    return $vars;
}
add_filter('query_vars', 'tit_methodology_query_var');

function tit_methodology_url() {
    return home_url('/' . TIT_METHODOLOGY_PATH . '/');
}

/** The page ships no script: it is prose. */
function tit_methodology_needs_no_js($needs) {
    return get_query_var('tit_methodology') ? false : $needs;
}
add_filter('tit_route_needs_js', 'tit_methodology_needs_no_js');

function tit_methodology_template() {
    if (!get_query_var('tit_methodology')) return;
    tit_methodology_render();
    exit;
}
add_action('template_redirect', 'tit_methodology_template');

/**
 * Pure: the sections as array(id, heading, array of paragraphs, optional list).
 * A paragraph is plain text, except that "{sources}", "{recall}", "{corrections}",
 * "{update_log}" and "{contact}" become links. Kept as data so a test can read
 * every claim without rendering WordPress.
 */
function tit_methodology_sections() {
    return array(
        array('counts', 'What counts as a signal', array(
            'A signal is a public record that an employer is adding roles, raising money, '
            . 'changing leadership, changing how it pays people or moving where it works. '
            . 'Each one is filed under one of four pillars: company developments and M&A, '
            . 'leadership changes, rewards and compensation, and how we work and location strategy.',
            'The tracker follows hiring-side signals only. It is not a list of open jobs, and a '
            . 'signal is not a count of vacancies: an acquisition or a new office is a sign that '
            . 'hiring may follow, and the record says what the document said, no more.',
            'Layoffs and redundancies are not collected here. They are read from the sibling AI '
            . 'Layoff Tracker at the moment a page is drawn, so each fact has one source of truth.',
        ), array()),

        array('announced-vs-measured', 'Announced plans and job-board changes are different things', array(
            'Every record carries exactly one of three labels, and the label is derived from where '
            . 'the record came from, never typed in by hand.',
        ), array(
            'Verified: an official or primary filed document, such as a regulatory filing or an '
            . 'exchange announcement. A record can only be verified when its source host is on a '
            . 'fixed list of primary publishers, so a news article cannot reach this label however '
            . 'confident the text sounds.',
            'Reported: news, a press release, or an employer\'s own site. A claim the outlet '
            . 'itself calls unconfirmed is marked rumored and is never promoted.',
            'Measured: our own reading of an employer\'s public job board, on two dates. It is '
            . 'a count of open roles, not an announcement, and it is never counted as an announced '
            . 'hiring event. Where role titles are shown, each links straight to the employer\'s '
            . 'own application page; we do not host job pages.',
        )),

        array('sources', 'Where the data comes from', array(
            'Collectors read regulatory registers and filings, exchange disclosures, employer '
            . 'announcements and news. {sources} lists every source that runs today, and separately '
            . 'the ones we have researched but do not read yet. A source counts as running only when '
            . 'a collector reads it, reports its own health and has a passing test.',
            'Search engines and news aggregators are discovery pointers, never stored sources. When '
            . 'an aggregator leads us to a story, we find the publisher\'s own article and store '
            . 'that. A model asked what we might be missing is a discovery pointer too: whatever it '
            . 'returns is only a lead to chase to a primary document.',
            'No source link, no record. A record without a document behind it is rejected before it '
            . 'is stored.',
        ), array()),

        array('checking', 'How a record is made and checked', array(
            'Every record passes the same path: a collector returns raw text, a classifier reads it, '
            . 'a validator checks it, duplicates are removed, and only then is it stored. A source '
            . 'cannot write to the database directly.',
        ), array(
            'A model reads the text but never supplies a number. Any figure in a summary must appear '
            . 'word for word in the source text, or the whole record is discarded rather than repaired.',
            'Fields are normalised through fixed vocabularies. A value that will not fit one is a '
            . 'rejected record, not a new category.',
            'Confidence is capped by the source. It can be lowered by a later review, and it is never '
            . 'silently raised.',
            'Nothing is overwritten. A correction adds a new revision and the earlier one is kept, so '
            . '"what did the tracker say on a given date" can still be answered.',
            'Source links are checked on a schedule and each cited document gets an archived copy as a '
            . 'fallback. A link that dies is flagged; we do not quietly edit the record to hide it.',
        )),

        array('dedupe', 'How we avoid counting an event twice', array(
            'One acquisition reported by forty outlets must be one record. Duplicates are caught in two '
            . 'layers. First, an exact-match check on the content, and a record we have already judged '
            . 'and withdrawn is not stored again. Second, the same employer and the same kind of '
            . 'development within 14 days is treated as one event, and a near-identical headline is '
            . 'matched over a much longer window to catch a re-report months later.',
            'Machine-generated headlines, such as the standard line written for every filing of one '
            . 'type, are not treated as evidence that two filings are the same event.',
        ), array()),

        array('money', 'What "money raised" means', array(
            'Totals of money raised count only capital that came into the company: funding rounds and '
            . 'comparable raises. A sale of a division, an acquisition price, an investor closing its '
            . 'own fund, money a company spends, a government award or an announced pledge can all be '
            . 'real news, and none of them is money the employer raised, so none of them is added in. '
            . 'Each stored figure is judged once when it is written and the basis is kept on the record. '
            . 'A figure on a regulatory filing is read for what it is: a cumulative total on an amended '
            . 'filing, or a merger consideration, is not new money.',
        ), array()),

        array('recall', 'How we measure what we miss', array(
            'A tracker can only say what it found. To measure what it did not, a person assembles a '
            . 'fixed list of real events from public sources without consulting our own database, and '
            . 'we check how many of them we hold. {recall} publishes the result every week, including '
            . 'the countries and document types where we do badly.',
        ), array(
            'Every rate is published with its 95% interval. A small set gives a wide interval, and a '
            . 'city-sized group of events is a work list, not a rate.',
            'Worldwide, EU and US sets are measured separately and never blended, because a world set '
            . 'built to be global says little about one market.',
            'A set cannot be rebuilt out of what is easy to find, and one set is not used for ever: '
            . 'it would end up measuring memory instead of reach.',
            'A short list of named landmark events, each backed by the company\'s own announcement, is '
            . 'checked weekly against the stored data and against what a reader actually sees, so a '
            . 'single very large event going missing cannot hide inside a good average.',
        )),

        array('limits', 'Coverage and honest limitations', array(
            'A market is covered when it has a working collector, a health check and a passing test, '
            . 'not when it appears on a list. Coverage differs a lot by country, and the recall page '
            . 'shows where it is thin.',
            'A low count in a place means we read few sources there, not that nothing happened. A '
            . 'ranking by number of updates is a ranking by records, not by jobs.',
            'Reading a story costs money and the budget is fixed, so on a busy day some candidate '
            . 'stories are read later rather than never; the pipeline spreads reads across countries '
            . 'so one country does not use the whole allowance.',
            'The tracker is automated almost end to end, but not entirely. People repair scrapers that '
            . 'break, judge new kinds of source, assemble each new recall test list and add each '
            . 'quarter\'s landmark events.',
            'Figures change as records arrive and as corrections are made. Cite the date you read a '
            . 'number.',
        ), array()),

        array('corrections', 'Corrections and changes', array(
            'When a published figure turns out to be wrong, we correct it and write down what was wrong, '
            . 'how many records it touched and what they say now, in the {corrections}. Changes to the '
            . 'tracker itself, newest first, are in the {update_log}.',
        ), array()),

        array('using', 'Using the data', array(
            'The data is licensed CC BY 4.0. Cite it as: Talent Intelligence Tracker, '
            . 'asktherecruiter.com, with the date you read the figure. CSV and JSON downloads carry the '
            . 'same records. To report an error, use the {contact} and name the record; each one links to '
            . 'the document it came from so you can check it first.',
        ), array()),
    );
}

/** Pure: a paragraph with its {placeholders} replaced by links, escaped. */
function tit_methodology_para_html($text, $links) {
    $html = esc_html($text);
    foreach ($links as $key => $pair) {
        $html = str_replace('{' . $key . '}',
            '<a href="' . esc_url($pair[1]) . '">' . esc_html($pair[0]) . '</a>', $html);
    }
    return $html;
}

function tit_methodology_links() {
    return array(
        'sources'     => array('The sources page', home_url('/talent-intelligence-tracker/sources/')),
        'recall'      => array('The measured recall page', home_url('/talent-intelligence-tracker/recall/')),
        'corrections' => array('corrections log', home_url('/talent-intelligence-tracker/corrections/')),
        'update_log'  => array('update log', home_url('/talent-intelligence-tracker/update-log/')),
        'contact'     => array('contact page', home_url('/contact/')),
    );
}

function tit_methodology_render() {
    // Block theme: never get_header() directly. See tit_render_header().
    if (function_exists('tit_render_header')) tit_render_header(); else get_header();
    $links = tit_methodology_links();
    ?>
    <div class="tit-wrap tit-corrections tit-methodology" id="tit-methodology">
      <?php if (function_exists('tit_breadcrumbs_html')) echo tit_breadcrumbs_html(tit_breadcrumbs_section_trail('Methodology', tit_methodology_url())); ?>

      <h1>Methodology</h1>
      <?php if (function_exists('tit_page_faq_intro')) echo tit_page_faq_intro('methodology'); ?>

      <p class="tit-note">
        We wrote this for journalists and researchers who need to check a figure before they cite it.
        Every published number traces back to a document, and that document is labelled for what it
        is: a filing, an employer statement, a news report, or our own reading of a job board.
      </p>

      <ul class="tit-note">
        <?php foreach (tit_methodology_sections() as $s) : ?>
          <li><a href="#<?php echo esc_attr($s[0]); ?>"><?php echo esc_html($s[1]); ?></a></li>
        <?php endforeach; ?>
      </ul>

      <?php foreach (tit_methodology_sections() as $s) : ?>
        <div class="tit-correction" id="<?php echo esc_attr($s[0]); ?>">
          <h2><?php echo esc_html($s[1]); ?></h2>
          <?php foreach ($s[2] as $para) : ?>
            <p><?php echo tit_methodology_para_html($para, $links); ?></p>
          <?php endforeach; ?>
          <?php if (!empty($s[3])) : ?>
            <ul>
              <?php foreach ($s[3] as $item) : ?>
                <li><?php echo tit_methodology_para_html($item, $links); ?></li>
              <?php endforeach; ?>
            </ul>
          <?php endif; ?>
        </div>
      <?php endforeach; ?>

      <p class="tit-cite">
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/sources/')); ?>">Data sources</a>
        &middot;
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/corrections/')); ?>">Corrections</a>
        &middot;
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/update-log/')); ?>">Update log</a>
        &middot;
        <a href="<?php echo esc_url(home_url('/talent-intelligence-tracker/')); ?>">Back to the tracker</a>
      </p>
    </div>
    <?php
    if (function_exists('tit_page_faq_section')) echo tit_page_faq_section('methodology');
    if (function_exists('tit_render_footer')) tit_render_footer('methodology'); else get_footer();
}

function tit_methodology_title($title) {
    return get_query_var('tit_methodology')
        ? 'Methodology · Talent Intelligence Tracker'
        : $title;
}
add_filter('pre_get_document_title', 'tit_methodology_title');

function tit_methodology_head() {
    if (!get_query_var('tit_methodology')) return;
    if (!function_exists('tit_head_description')) return;
    tit_head_description(
        'What counts as a signal on the Talent Intelligence Tracker, where it comes from, how it '
        . 'is checked and deduplicated, how we measure what we miss, and what the tracker cannot see.'
    );
    echo '<link rel="canonical" href="' . esc_url(tit_methodology_url()) . '" />' . "\n";
}
add_action('wp_head', 'tit_methodology_head', 1);
