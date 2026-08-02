/* Filters talk to talent/v1/query. The table is already server-rendered, so
   this only ever replaces rows that are already there. If the API is
   unreachable the page keeps working with what the server sent. */

(function () {
  'use strict';

  var root = document.getElementById('tit-dashboard');
  if (!root) return;

  // Prefer the global that wp_localize_script prints, but never depend on it.
  // Autoptimize sweeps inline scripts into an aggregated bundle while leaving
  // this file where it is (it is excluded by path), so the global can be
  // undefined at the moment this runs and defined by the time anything checks.
  // Bailing on that read is what made every control on the live page inert.
  var TIT = window.TIT;
  if (!TIT || !TIT.api) {
    TIT = { api: root.getAttribute('data-api') || '', countries: {}, states: {} };
    try {
      TIT.countries = JSON.parse(root.getAttribute('data-countries') || '{}');
    } catch (e) { /* names degrade to the raw country codes */ }
    try {
      TIT.states = JSON.parse(root.getAttribute('data-states') || '{}');
    } catch (e) { /* names degrade to the raw postal codes */ }
  }
  // The localized object may predate this attribute by one deploy.
  if (!TIT.states) {
    try {
      TIT.states = JSON.parse(root.getAttribute('data-states') || '{}');
    } catch (e) { TIT.states = {}; }
  }
  // The pending-archive note, composed by the SERVER (the collectors the
  // schedule covers, plus the finished sentence with its derived next-check
  // date). This script only ever prints it, so a repaint is byte-identical to
  // the first paint and the date has exactly one derivation. Absent attribute
  // means no promise file on the server, and then no note is rendered at all.
  var ARCHIVE_NOTE = null;
  try {
    ARCHIVE_NOTE = JSON.parse(root.getAttribute('data-archive-note') || 'null');
  } catch (e) { ARCHIVE_NOTE = null; }
  if (!ARCHIVE_NOTE || !ARCHIVE_NOTE.text || !ARCHIVE_NOTE.collectors) ARCHIVE_NOTE = null;
  if (!TIT.api) return;

  var tbody = document.getElementById('tit-rows');
  // Keys are the API's query parameter names, so refresh() can build the
  // querystring straight from this map without a translation layer.
  var inputs = {
    pillar: document.getElementById('tit-f-pillar'),
    direction: document.getElementById('tit-f-direction'),
    'function': document.getElementById('tit-f-function'),
    industry: document.getElementById('tit-f-industry'),
    country: document.getElementById('tit-f-country'),
    state: document.getElementById('tit-f-state'),
    city: document.getElementById('tit-f-city'),
    confidence: document.getElementById('tit-f-confidence'),
    min_funding_usd: document.getElementById('tit-f-min_funding_usd'),
    funding_stage: document.getElementById('tit-f-funding_stage'),
    employer_type: document.getElementById('tit-f-employer_type'),
    work_mode: document.getElementById('tit-f-work_mode'),
    site_event: document.getElementById('tit-f-site_event'),
    deal_type: document.getElementById('tit-f-deal_type'),
    country_basis: document.getElementById('tit-f-country_basis'),
    company: document.getElementById('tit-f-company'),
    q: document.getElementById('tit-f-q'),
    since: document.getElementById('tit-f-since'),
    until: document.getElementById('tit-f-until'),
    detail: document.getElementById('tit-f-detail'),
    sort: document.getElementById('tit-f-sort')
  };

  // Values that narrow nothing. They are never sent, never become a chip, and
  // never make the page count as filtered. Without this, country_basis="any"
  // and sort="newest" would each show as an active filter on a page nobody had
  // touched, which is the fastest way to make a filter bar mean nothing.
  // `detail` is here even though it is not neutral in the API's sense: it does
  // narrow the page. It gets no chip because the detail bar above the table
  // states it far more loudly than a chip could, with both counts and our
  // definition of routine printed in full. A chip would be a quieter version of
  // something already impossible to miss.
  var NEUTRAL = { sort: 'notable', country_basis: 'any', detail: 'notable' };

  // How each filter names itself in the active-filter bar.
  // These name the filter in the active-filter bar, and they mirror the labels
  // above the controls themselves. A chip reading "Roles" over a control
  // reading "Team or function" is two names for one thing.
  // Title Case, and the SAME words as the controls themselves. A chip reading
  // "Roles" over a control reading "Team Or Function" is two names for one
  // thing, and no word here appears anywhere else in the product with a
  // different meaning.
  var FILTER_LABEL = {
    pillar: 'Looking For', direction: 'Looking For', 'function': 'Team',
    industry: 'Industry', country: 'Where', state: 'Where', city: 'Where',
    stated_headcount: 'Headcount', employer_type: 'Employer Type',
    work_mode: 'Work Setup', deal_type: 'Deal Type', site_event: 'Site Change',
    confidence: 'Evidence', country_basis: 'Places', company: 'Employer',
    min_funding_usd: 'Amount Raised', funding_stage: 'Funding Stage',
    q: 'Search', since: 'From', until: 'To', region: 'Region', quickview: 'View'
  };

  // Saved views that no longer have a button of their own but can still be
  // switched on (by a matrix cell, or by a shared link).
  var QV_LABEL = { 'funding=1': 'Funding updates' };

  // "Educational Institution", not "Education". The bare word also names an
  // INDUSTRY in the panel above, so the same string appeared in two groups
  // meaning two different things: the sector an employer trades in, and the
  // kind of organisation it is. A university filing a pay-gap return is an
  // educational institution; an ed-tech startup is in the education industry
  // and is a Startup. The stored value (`education` in
  // pipeline/vocab.EMPLOYER_TYPES) is untouched.
  var EMPLOYER_TYPE_LABEL = {
    'public': 'Public Company', 'private': 'Private Company', startup: 'Startup',
    government: 'Government', nonprofit: 'Nonprofit',
    education: 'Educational Institution'
  };
  var WORK_MODE_LABEL = {
    remote: 'Remote', hybrid: 'Hybrid', onsite: 'Onsite',
    rto_mandate: 'Return To Office', flexible: 'Flexible'
  };
  // "Initial Public Offering" spelled out, because "IPO" is ALSO a funding
  // stage in the panel above and the two groups sat one above the other
  // offering the identical three letters. They are genuinely different
  // questions -- Funding Stage asks what round this was, Deal Type asks what
  // kind of transaction it was -- so the fix is to say the deal one at length
  // rather than to drop either. Stored value (`ipo`) unchanged.
  var DEAL_TYPE_LABEL = {
    acquisition: 'Acquisition', acquired: 'Acquired', merger: 'Merger',
    divestiture: 'Divestiture', joint_venture: 'Joint Venture',
    ipo: 'Initial Public Offering'
  };

  // What an employer did with a place of work. "Announced" is its own value
  // and not a softer word for "Opened": a plant promised for 2028 and a
  // building open this morning are different answers to "who is here now".
  var SITE_EVENT_LABEL = {
    opened: 'Opened', closed: 'Closed', expanded: 'Expanded',
    relocated: 'Relocated', announced: 'Announced'
  };

  // Round names as a reader says them. Mirrors tit_funding_stage_labels().
  var STAGE_LABEL = {
    pre_seed: 'Pre-Seed', seed: 'Seed', series_a: 'Series A',
    series_b: 'Series B', series_c: 'Series C',
    series_d_plus: 'Series D or Later', growth: 'Growth', debt: 'Debt',
    grant: 'Grant', ipo: 'IPO', other: 'Other'
  };

  // What a record is BASED ON, said plainly. The stored values (verified,
  // reported, rumored) are our vocabulary: "verified" reads as a badge we
  // awarded rather than a statement about the source.
  var CONFIDENCE_LABEL = {
    verified: 'Official Filing',
    reported: 'News Report',
    rumored: 'Unconfirmed'
  };

  // Mirrors the `tit-{signal_direction}` class tit_card_html() prints. `neutral`
  // was missing and fell through to the bare tag, which put a different class on
  // a repainted card than on the one the server sent.
  var DIRECTION_CLASS = {
    hiring: 'tit-hiring',
    displacement: 'tit-displacement',
    comp_shift: 'tit-comp_shift',
    neutral: 'tit-neutral'
  };

  // Recruiter language. Colour never carries the meaning on its own, so the
  // words have to be right.
  // MIRRORS $directions IN shortcodes.php AND MUST STAY IDENTICAL TO IT. The
  // server prints these on the first paint and this map reprints them on every
  // repaint, so a divergence shows up as a label that changes the first time a
  // reader touches a filter. See the one-vocabulary note beside $labels there
  // for why "Adding Roles" replaced "Hiring up".
  var DIRECTION_LABEL = {
    hiring: 'Adding Roles',
    displacement: 'Cutting Roles',
    comp_shift: 'Pay Change',
    // Matches the PHP label. This bucket is "the source says nothing about
    // headcount" (a funding round with no hiring plan, a CEO succession), not
    // a vague other-category, and naming it that way is truer to the rule that
    // we never infer a direction the source did not state.
    neutral: 'Headcount Not Stated'
  };

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function populateFacets() {
    fetch(TIT.api + 'facets')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        // Only geography is data-driven; roles and industries are closed
        // vocabularies and are already rendered server-side.
        // Countries are listed by name and sorted by name. /facets returns ISO
        // codes, and a dropdown reading "AE, AR, AT" asks the reader to know
        // the codebook before they can pick a country.
        fill(inputs.country, data.countries, true);
        fill(inputs.state, data.states, false, TIT.states);
        fill(inputs.city, data.cities);
        // Only the rounds we actually hold, so the control cannot offer a
        // stage that returns an empty page.
        fillFacetControl('funding_stage', data.funding_stages, STAGE_LABEL);
        fillFacetControl('employer_type', data.employer_types, EMPLOYER_TYPE_LABEL);
        fillFacetControl('work_mode', data.work_modes, WORK_MODE_LABEL);
        fillFacetControl('deal_type', data.deal_types, DEAL_TYPE_LABEL);
        fillFacetControl('site_event', data.site_events, SITE_EVENT_LABEL);
        fillPlaces(data);
      })
      .catch(function () { /* filters degrade to what the server rendered */ });
  }

  function fill(select, values, asCountries, labels) {
    if (!select || !values) return;
    var items = values.map(function (v) {
      return { value: v, label: asCountries ? countryLabel(v) : ((labels && labels[v]) || v) };
    });
    if (asCountries) {
      items.sort(function (a, b) { return a.label.localeCompare(b.label); });
    }
    items.forEach(function (item) {
      // Never a second copy of a value the select already holds: a shared link
      // is restored before /facets answers, so the value it carried has
      // already been added by hand (see applyUrlState).
      var seen = Array.prototype.some.call(select.options, function (o) {
        return o.value === item.value;
      });
      if (seen) return;
      var opt = document.createElement('option');
      opt.value = item.value;
      opt.textContent = item.label;
      select.appendChild(opt);
    });
  }

  // One "Where" control over three columns, grouped so a reader picks a place
  // and never a column. Three separate geography dropdowns was the clearest
  // symptom of the schema showing through the paint.
  function fillPlaces(data) {
    var sel = document.getElementById('tit-f-place');
    if (!sel || !data) return;
    var groups = [
      // Sorted by NAME, never by ISO code: a list reading "AE, AR, AT" asks the
      // reader to know the codebook before they can pick a country. The flag
      // prefixes the label but the sort key is the name, so Germany still files
      // under G. An <option> cannot carry aria-hidden on part of its text, so a
      // screen reader hears the flag and then the name; the name is what
      // carries the meaning either way.
      ['Countries', 'country', (data.countries || []).map(function (v) {
        var flag = countryFlag(v);
        return { v: v, l: countryLabel(v), t: (flag ? flag + ' ' : '') + countryLabel(v) };
      }).sort(function (a, b) { return a.l.localeCompare(b.l); })],
      ['US states', 'state', (data.states || []).map(function (v) {
        return { v: v, l: stateLabel(v) };
      }).sort(function (a, b) { return a.l.localeCompare(b.l); })],
      ['Cities', 'city', (data.cities || []).map(function (v) { return { v: v, l: v }; })]
    ];
    groups.forEach(function (g) {
      var box = document.createElement('optgroup');
      box.label = g[0];
      g[2].forEach(function (item) {
        var value = g[1] + ':' + item.v;
        var seen = Array.prototype.some.call(sel.options, function (o) { return o.value === value; });
        if (seen) return;
        var opt = document.createElement('option');
        opt.value = value;
        opt.textContent = item.t || item.l;
        box.appendChild(opt);
      });
      if (box.children.length) sel.appendChild(box);
    });
    syncPlace();
  }

  var MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  // The date the SOURCE carries, never the date we happened to capture it, and
  // said out loud when there is not one. Parsed by hand from YYYY-MM-DD rather
  // than through Date(), which reads a bare date as UTC midnight and can show
  // the previous day to anyone west of Greenwich.
  // A real <time datetime> when there is a date, and the shared not-stated
  // string wrapped in the contract's card-nowhere class when there is not.
  // Mirrors tit_card_html() in shortcodes.php.
  function whenCell(r) {
    var iso = (r.published_date || '').slice(0, 10);
    var d = iso.split('-');
    if (d.length !== 3) return '<span class="tit-card-when tit-card-nowhere">Date not stated</span>';
    return '<time class="tit-card-when" datetime="' + esc(iso) + '">' +
      esc(String(+d[2]) + ' ' + (MONTHS[+d[1] - 1] || '') + ' ' + d[0]) + '</time>';
  }

  // The archived copy, and only ever as a SECOND link beside the publisher's
  // own. Source links rot: outlets unpublish, URL schemes change, domains
  // lapse. When that happens a sourced claim silently becomes an unsourced one,
  // which is the one failure this product cannot absorb. A neutral third-party
  // snapshot keeps the evidence reachable without ever replacing the citation.
  // Must render identically to shortcodes.php, or a filtered row would differ
  // from the row it replaced.
  // Three states, mirroring tit_archive_note_html(): the link where a snapshot
  // exists; on a publisher-sourced row without one, the server-composed
  // pending sentence with its derived next-check date (ARCHIVE_NOTE, printed
  // verbatim so both paints agree); nothing on rows whose documents a
  // government already preserves, because promising those a re-check the
  // schedule never makes would be a false sentence. Still never a dead link.
  // The middot before either is a CSS ::before rather than a text node here,
  // so it cannot wrap to the start of a line in the card layout.
  function archivedLink(r) {
    if (!r.archive_url) {
      if (ARCHIVE_NOTE && r.collector &&
          ARCHIVE_NOTE.collectors.indexOf(r.collector) !== -1) {
        return '<span class="tit-archive-wait">' + esc(ARCHIVE_NOTE.text) + '</span>';
      }
      return '';
    }
    return '<span class="tit-archived"><a href="' + esc(r.archive_url) +
      '" rel="nofollow noopener" target="_blank" ' +
      'title="Archived copy at the Internet Archive">Archived</a></span>';
  }

  // ONE RESULT CARD, TO THE SHARED CONTRACT IN docs/card-contract.json.
  //
  // This MUST produce the same markup tit_card_html() produces in
  // shortcodes.php, or a filtered card would lay out differently from the card
  // it replaced, and the same shape the sibling AI Layoff Tracker renders:
  // same regions, same class suffixes, same badge order, same four direction
  // words. tests/test_card_contract.py pins all of it.
  //
  // Reading order, and it is the order a person needs:
  //   rail  who they are, what sector, where
  //   body  what kind of move (direction, evidence, amount), the fact, our read
  //   foot  when, and the document it came from
  function renderCard(r) {
    // Fall back to headquarters when the source named no place, and say so.
    var isHq = !r.city && !r.country;
    var place = r.city || r.hq_city || '';
    var code = r.country || r.hq_country || '';
    var country = countryLabel(code);
    var where = esc([place, country].filter(Boolean).join(', '));
    if (!where) {
      where = '<span class="tit-card-nowhere">Location not stated</span>';
    } else if (isHq) {
      where += ' <span class="tit-hq" title="Employer headquarters, not a location named in the source">HQ</span>';
    }

    // Omitted entirely when the record carries none. Never an empty line and
    // never a placeholder: the contract asks for the field to be absent.
    var industry = r.industry
      ? '<span class="tit-card-industry">' + esc(INDUSTRY_LABEL[r.industry] || r.industry) + '</span>'
      : '';

    // Badge three, and ONLY when there is an amount. There is no "no funding
    // stated" pill: the direction badge already says what the source did and
    // did not tell us, and a second badge repeating it was the duplicate the
    // shared contract removed. Real text for the unit, never a CSS ::after: a
    // bare "$12M" tells a screen reader nothing about what was raised.
    var usd = Number(r.funding_amount_usd || 0);
    var amount = usd > 0
      ? '<span class="tit-card-amt">' + esc(moneyShort(usd)) +
        '<span class="tit-card-amt-unit"> raised</span></span>'
      : '';

    return '<li class="tit-card">' +
      '<div class="tit-card-rail">' +
        '<span class="tit-card-employer">' + esc(r.company) + '</span>' +
        industry +
        '<span class="tit-card-where">' + where + '</span>' +
      '</div>' +
      '<div class="tit-card-body">' +
        // Contract badge order: direction, evidence, amount.
        '<div class="tit-card-badges">' +
          '<span class="tit-card-dir tit-tag ' + (DIRECTION_CLASS[r.signal_direction] || 'tit-neutral') + '">' +
            esc(DIRECTION_LABEL[r.signal_direction] || r.signal_direction) + '</span>' +
          '<span class="tit-card-ev tit-conf tit-c-' + esc(r.confidence) + '">' +
            esc(CONFIDENCE_LABEL[r.confidence] || r.confidence) + '</span>' +
          amount +
        '</div>' +
        '<span class="tit-card-h tit-h">' + esc(r.headline) + '</span>' +
        (r.talent_readthrough
          ? '<p class="tit-card-rt tit-rt">' + esc(r.talent_readthrough) + '</p>' : '') +
        '<div class="tit-card-foot">' +
          whenCell(r) +
          '<span class="tit-card-src"><a href="' + esc(r.source_url) +
            '" rel="nofollow noopener" target="_blank">' + esc(r.source_name) + '</a>' +
            archivedLink(r) + '</span>' +
        '</div>' +
      '</div>' +
      '</li>';
  }

  // --- The rest of the page follows the filters -----------------------------
  // Until now only the table re-rendered, so the hero said "13 updates · 3
  // countries" and the charts drew the whole world while the rows underneath
  // showed one region. The page implied the filter applied to everything, and
  // that implication is exactly what a dashboard must not get wrong.

  // Mirrors $labels in shortcodes.php. Keep identical: see the one-vocabulary
  // note there. Display only -- every chart row carries its key on data-k.
  var PILLAR_LABEL = {
    company_development: 'Growing and Expanding',
    leadership_change: 'Leadership Moves',
    rewards_comp: 'Pay and Benefits',
    how_we_work: 'Ways of Working'
  };

  var INDUSTRY_LABEL = {
    technology: 'Technology', financial_services: 'Financial Services',
    healthcare: 'Healthcare', pharma_biotech: 'Pharma & Biotech',
    retail_ecommerce: 'Retail & E-commerce', manufacturing: 'Manufacturing',
    energy_utilities: 'Energy & Utilities', telecom: 'Telecom',
    media_entertainment: 'Media & Entertainment',
    transport_logistics: 'Transport & Logistics',
    professional_services: 'Professional Services',
    public_sector: 'Public Sector', hospitality_travel: 'Hospitality & Travel',
    education: 'Education', food_beverage: 'Food & Beverage',
    automotive: 'Automotive', aerospace_defence: 'Aerospace & Defence',
    real_estate_construction: 'Real Estate & Construction'
  };

  function nfmt(n) { return Number(n || 0).toLocaleString(); }

  // --- Money ----------------------------------------------------------------
  // These mirror tit_money_short() and tit_money_full() in shortcodes.php
  // exactly. Two formatters would drift, and a filtered page would abbreviate
  // the same figure differently from the one the server rendered.
  var MONEY_UNITS = ['K', 'M', 'B', 'T'];

  function moneyShort(n) {
    n = Number(n) || 0;
    if (n <= 0) return '$0';
    if (n < 1000) return '$' + nfmt(Math.round(n));
    var i = 0, v = n / 1000;
    while (v >= 1000 && i < MONEY_UNITS.length - 1) { v /= 1000; i++; }
    // One decimal below 100, none above, and a figure is never rounded into a
    // different order of magnitude: $999M stays $999M.
    v = (v < 100) ? Math.round(v * 10) / 10 : Math.round(v);
    if (v >= 1000 && i < MONEY_UNITS.length - 1) { v = Math.round(v / 100) / 10; i++; }
    return '$' + String(v) + MONEY_UNITS[i];
  }

  function moneyFull(n) { return '$' + nfmt(Math.round(Number(n) || 0)); }

  // The coverage sentence, mirroring tit_money_coverage_note(). Never a
  // hardcoded pair of numbers: a dollar total that does not say what share of
  // the data it covers is the one number this product must not print.
  function coverageNote(money, dim) {
    if (!money || !money.coverage) return '';
    var withUsd = Number(money.coverage.with) || 0;
    var all = Number(money.coverage.all) || 0;
    if (!all) return 'No funding updates in this view yet, so there is nothing to add up.';

    // "the 3,992 of 3,992" reads as a mistake. Say so plainly when coverage is
    // complete; keep the two numbers only when they actually differ.
    var note = (withUsd >= all
        ? 'All ' + nfmt(all) + (all === 1 ? ' funding update states' : ' funding updates state') +
          ' a US dollar amount'
        : 'Totals cover the ' + nfmt(withUsd) + ' of ' + nfmt(all) +
          (all === 1 ? ' funding update that states' : ' funding updates that state') +
          ' a US dollar amount') +
      '; amounts in other currencies are left out rather than converted at a' +
      ' rate nobody published.';

    var placed = (money.placed && dim && money.placed[dim] != null)
      ? Number(money.placed[dim]) : withUsd;
    var missing = withUsd - placed;
    if (!dim || missing <= 0) return note;
    var names = { country: 'no country', city: 'no city', industry: 'no industry' };
    return note + ' ' + nfmt(missing) +
      (missing === 1 ? ' of those names ' : ' of those name ') +
      (names[dim] || 'no category') +
      (missing === 1 ? ', so it is not on this chart.' : ', so they are not on this chart.');
  }

  // Filtering to a single country produced "1 countries" in the hero, which is
  // a small thing that reads as carelessness on a page whose whole argument is
  // that the details are checked.
  function plural(n, one, many) {
    return nfmt(n) + ' ' + (Number(n) === 1 ? one : (many || one + 's'));
  }

  // $html says the labeller already returns escaped markup (the flag helper is
  // the only one that does), so the name inside it is not double-escaped.
  function paintRank(chart, rows, label, dirKey, html) {
    var wrap = chart && chart.querySelector('.tit-rank');
    if (!wrap) return;
    if (!rows.length) {
      wrap.innerHTML = '<p class="tit-rank-empty">Nothing in this view.</p>';
      return;
    }
    var max = Math.max.apply(null, rows.map(function (r) { return +r.n; })) || 1;
    // Every row the API sent, not the first six. /aggregate already returns up
    // to 40 per group, so slicing here discarded rows that had been fetched,
    // with no control anywhere on the page that could bring them back. The card
    // stays small because the list scrolls, not because the data is cut.
    // Rows are real buttons: each one is a filter (see the chart-row wiring
    // below), and a button is what makes that reachable by keyboard.
    wrap.innerHTML = rows.map(function (r) {
      var pct = Math.max(4, Math.round(100 * r.n / max));
      return '<button type="button" class="tit-rank-row" data-k="' + esc(r.k) + '"' +
        (dirKey ? ' data-dir="' + esc(r.k) + '"' : '') + ' aria-pressed="false">' +
        '<span class="tit-rank-name">' + (html ? label(r.k) : esc(label(r.k))) + '</span> ' +
        '<span class="tit-rank-track"><span class="tit-rank-fill" style="width:' + pct + '%"></span></span> ' +
        '<span class="tit-rank-n">' + nfmt(r.n) + '</span></button>';
    }).join('');
  }

  // Strongest evidence first, always, whatever order the endpoint returned.
  // CONFIDENCE_LABEL is declared in the vocabulary's own order and mirrors
  // tit_confidence_labels(), so one list decides it on both sides.
  function confidenceOrder(rows) {
    var rank = Object.keys(CONFIDENCE_LABEL);
    var at = function (k) {
      var i = rank.indexOf(k);
      // A value the vocabulary does not know goes last rather than first, which
      // is where -1 would have put it.
      return i < 0 ? rank.length : i;
    };
    return rows.slice().sort(function (a, b) { return at(a.k) - at(b.k); });
  }

  function paintAggregate(data) {
    var total = +data.total || 0;

    // Hero fine print: restate the view, do not describe a set the reader is
    // no longer looking at.
    var fine = root.querySelector('.tit-hero-fine');
    if (fine) {
      var lead = fine.querySelector('.tit-fine-figures');
      if (!lead) {
        lead = document.createElement('span');
        lead.className = 'tit-fine-figures';
        fine.insertBefore(lead, fine.firstChild);
      }
      var bits = [
        esc(plural(total, 'update')),
        esc(plural(data.companies, 'employer')),
        esc(plural(data.countries, 'country', 'countries'))
      ];
      // A sum of dollars beside three counts, sitting WITH the other headline
      // figures rather than trailing the sentence, and a link rather than a
      // bare total: it lands on the money section, which prints what share of
      // the funding updates it covers. Both read the same aggregate, so the
      // figure and its caveat cannot drift apart.
      var mt = data.money && data.money.total;
      if (mt > 0) {
        bits.push('<a class="tit-fine-money" href="#chart-money-country" title="' +
          esc(moneyFull(mt) + '. ' + coverageNote(data.money, '')) + '">' +
          esc(moneyShort(mt)) + ' raised</a>');
      }
      bits.push(esc(nfmt(data.verified)) + ' from official filings');
      lead.innerHTML = bits.join(' · ');
    }

    // The at-a-glance matrix moves with the filters like everything else. It
    // used to be the one part of the hero that did not, so a filtered page
    // had its own summary contradicting its own strip. Markup mirrors
    // tit_glance_matrix_html() in shortcodes.php, the same contract renderRow
    // has with the table.
    // The dated panel moves with the filters for the same reason the matrix
    // does, and for one more: Copy as Post reads its rendered rows, so a stale
    // panel would let a reader copy an unfiltered total off a filtered page.
    // The coverage sentence is carried in from the money aggregate rather than
    // recomputed, so the panel and the money cards state one coverage.
    var dgbox = document.getElementById('tit-dg-box');
    if (dgbox && data.glance && data.glance.dated) {
      var dated = data.glance.dated;
      dated.coverage = (data.money && data.money.coverage) || null;
      dgbox.innerHTML = datedHtml(dated);
      showDatedCopy();
    }

    // The trend chart moves with the filters too, and it arrives as markup
    // rather than as a series. See tit_aggregate_trend() in api.php: the chart
    // carries a continuity gate that decides which lines may honestly be drawn,
    // and a second copy of that decision written here could disagree with the
    // server's. A blank string is a real answer (nothing in this view clears
    // the gate), so it is assigned rather than skipped.
    // The box carries the card's (i) panel as well as the plot, because both
    // move with the filters: a narrower view redraws the lines AND changes
    // which signals could honestly be drawn at all. Its panel arrives open
    // (the server always ships them open, see tit_chart_head), so the (i) is
    // told to reassert itself or every filter change would reopen it.
    var trendBox = document.getElementById('tit-trend-box');
    if (trendBox && typeof data.trend_html === 'string') {
      trendBox.innerHTML = data.trend_html;
      syncChartNotes();
    }

    var glance = root.querySelector('.tit-glance');
    if (glance && data.glance && data.glance.rows) {
      glance.innerHTML = matrixHtml(data.glance);
      // matrixHtml() emits <details open>, so a repaint would hand a phone the
      // open wall of prose again. Re-collapse it the same way the first paint
      // did, or every filter change undoes the mobile fix.
      collapseMatrixNoteOnPhone();
    }

    // Cards are found by id, never by position. They used to be indexed out of
    // a querySelectorAll over every .tit-chart on the page, so adding a card
    // anywhere above would have silently repainted the wrong three.
    // Pillars keep their own markup (a bar per pillar), so they are painted
    // separately from the two rank charts.
    var pillars = root.querySelector('.tit-pillars');
    if (pillars) {
      var rows = data.by_pillar || [];
      // Buttons with span children, never divs inside a button: a button may
      // only contain phrasing content, and the spans carry the layout in CSS.
      pillars.innerHTML = rows.length ? rows.map(function (r) {
        var pct = total ? Math.round(100 * r.n / total) : 0;
        return '<button type="button" class="tit-pillar" data-k="' + esc(r.k) + '" aria-pressed="false">' +
          '<span class="tit-pillar-head">' +
          '<span class="tit-pillar-name">' + esc(PILLAR_LABEL[r.k] || r.k) + '</span> ' +
          '<span class="tit-pillar-n">' + nfmt(r.n) + '</span></span>' +
          '<span class="tit-bar"><span style="width:' + pct + '%"></span></span></button>';
      }).join('') : '<p class="tit-rank-empty">Nothing in this view.</p>';
    }
    paintRank(document.getElementById('chart-place'), data.by_country || [],
      countryLabelHtml, false, true);
    paintRank(document.getElementById('chart-direction'), data.by_direction || [], function (k) {
      return DIRECTION_LABEL[k] || k;
    }, true);
    // How solid the evidence is, in the vocabulary's own order rather than by
    // size. /aggregate returns it biggest-first like every other group, and a
    // ladder whose rungs reorder themselves under a filter is a ladder nobody
    // can read twice. The server's first paint orders it the same way.
    paintRank(document.getElementById('chart-confidence'),
      confidenceOrder(data.by_confidence || []), function (k) {
        return CONFIDENCE_LABEL[k] || k;
      });
    // By count, and deliberately not the same numbers as the money card of
    // nearly the same name: that one can only see rows carrying a dollar
    // figure.
    paintRank(document.getElementById('chart-industry'), data.by_industry || [], function (k) {
      return INDUSTRY_LABEL[k] || k;
    });

    // The money cards move with the filters like everything else, coverage
    // sentence included: the sentence describes the filtered set, so it has to
    // be recomputed whenever the set changes.
    var money = data.money || null;
    paintMoney(document.getElementById('chart-money-country'),
      money && money.by_country, countryLabelHtml, money, 'country', true);
    paintMoney(document.getElementById('chart-money-city'),
      money && money.by_city, function (k) { return k; }, money, 'city');
    paintMoney(document.getElementById('chart-money-industry'),
      money && money.by_industry, function (k) { return INDUSTRY_LABEL[k] || k; },
      money, 'industry');

    // What the detail control is setting aside, restated for the filtered set.
    // The counts are computed WITHOUT the detail filter applied (see
    // /aggregate), so switching to notable does not report zero routine
    // filings at exactly the moment the number matters.
    var note = document.getElementById('tit-detail-note');
    if (note && data.materiality) {
      note.textContent = detailNote(
        inputs.detail ? inputs.detail.value : 'notable',
        data.materiality.notable, data.materiality.routine);
    }

    // What the stated-headcount toggle would leave, so the reader sees what it
    // does before using it.
    // The number is what you WOULD see if the box were ticked, under the
    // filters in force, and it sits inside the label so the relationship is
    // stated rather than inferred. When the server cannot give us one, show
    // NOTHING: an ambiguous number is worse than no number.
    var statedN = document.getElementById('tit-stated-n');
    if (statedN) {
      statedN.textContent = (data.stated_headcount == null)
        ? '' : '(' + nfmt(data.stated_headcount) + ')';
    }

    // The one-collector caveat follows the filters: it names whichever country
    // is currently dominated, and hides itself when none is.
    var caveat = document.getElementById('tit-place-caveat');
    if (caveat) {
      var text = data.place_caveat || '';
      caveat.textContent = text;
      caveat.hidden = (text === '');
    }

    // The date range the page actually covers, restated for the filtered set.
    // Both bounds come from one query on the server, so the range and its own
    // label cannot contradict each other the way they did.
    var spanEl = document.getElementById('tit-span');
    if (spanEl && data.span) spanEl.textContent = spanNote(data.span.lo, data.span.hi);

    // Re-rendering wiped the pressed state off every row; put it back.
    syncChartStates();
  }

  // Mirrors tit_country_flag(). Derived from the ISO code, never a map: 'A' is
  // U+1F1E6, so a two-letter code is two code points at a fixed offset. A
  // hardcoded table is how "PR" once rendered as a bare code, and the list of
  // countries we hold grows every week.
  //
  // Refuses on exactly the same inputs the PHP does. If the two disagreed, a
  // country could show a flag beside "XX (unmapped)", which is worse than
  // either alone.
  var NO_FLAG = { XK: 1 };

  function countryFlag(code) {
    if (!/^[A-Z]{2}$/.test(code || '')) return '';
    if (NO_FLAG[code]) return '';
    if (!(TIT.countries && TIT.countries[code])) return '';
    return String.fromCodePoint(0x1F1E6 + code.charCodeAt(0) - 65,
                                0x1F1E6 + code.charCodeAt(1) - 65);
  }

  // Flag plus name, with the flag hidden from assistive technology and the name
  // ALWAYS present: a platform with no font for a flag draws two letters or a
  // blank box, and a reader must never be left with only that.
  function countryLabelHtml(code) {
    var flag = countryFlag(code);
    return (flag ? '<span class="tit-flag" aria-hidden="true">' + esc(flag) + '</span>' : '') +
      '<span class="tit-cname">' + esc(countryLabel(code)) + '</span>';
  }

  // Mirrors tit_country_name(): a code must never reach the page as a code.
  // The map comes from the server and now covers the whole of ISO 3166-1, so
  // the fallback should be unreachable; if it is reached it says so in words
  // and leaves a console trace for us, rather than printing two bare letters
  // into a list of country names and waiting for a reader to find it.
  // Mirrors tit_span_note(). Both bounds always carry their year: printing the
  // low bound without one is exactly what made the live page say "3,318 days,
  // 28 Jun to 28 Jul 2026", nine years of days against thirty days of dates.
  function niceDate(d) {
    var p = (d || '').slice(0, 10).split('-');
    if (p.length !== 3) return '';
    return String(+p[2]) + ' ' + (MONTHS[+p[1] - 1] || '') + ' ' + p[0];
  }

  function spanNote(lo, hi) {
    var a = niceDate(lo), b = niceDate(hi);
    if (!a || !b) return '';
    return a === b ? 'Covering ' + b + '.' : 'Covering ' + a + ' to ' + b + '.';
  }

  // Mirrors tit_state_names(). `tit-f-state` rendered 51 bare postal codes as
  // its option labels while the country control beside it spelled every value
  // out, so the one filter on the page that still asked a reader to know a
  // codebook was the American one. Unlike countryLabel() an unknown code falls
  // through silently to itself: the map covers all 50 states, DC and the five
  // inhabited territories, but `state` is populated from whatever the pipeline
  // stored, and a Canadian province arriving there should read as itself rather
  // than as "(unmapped)".
  function stateLabel(k) {
    if (!k) return '';
    return (TIT.states && TIT.states[k]) || k;
  }

  function countryLabel(k) {
    if (!k) return '';
    var name = TIT.countries && TIT.countries[k];
    if (name) return name;
    if (window.console && console.warn) {
      console.warn('[talent-intelligence-tracker] unmapped country code:', k);
    }
    return k + ' (unmapped)';
  }

  // Mirrors tit_detail_note(). Both counts, always, plus what routine means:
  // that sentence is the reason the default is allowed to hold rows back at
  // all. A reader can see exactly how many and change it in one click.
  //
  // COUNT FIRST NOW, definition trailing. The owner could not parse this note
  // where it sat, and the cause was that the reader met a 17-word definition
  // before any number. The three figures are unchanged and still all printed,
  // so hidden plus shown still visibly equals total. Keep this wording IDENTICAL
  // to tit_detail_note() in shortcodes.php: the server prints one of these
  // sentences on the first paint and this function reprints it on every filter
  // change, so a divergence shows up as the sentence rewriting itself.
  var ROUTINE_MEANS = ' A routine filing records only an officer or director' +
    ' change, with no headcount, no money and no location.';

  function detailNote(mode, notable, routine) {
    notable = Number(notable) || 0;
    routine = Number(routine) || 0;
    var total = notable + routine;
    if (!routine) {
      return 'None of the ' + nfmt(total) +
        (total === 1 ? ' update here is a routine filing.'
                     : ' updates here are routine filings.') + ROUTINE_MEANS;
    }
    if (mode === 'all') {
      return 'You are seeing all ' + nfmt(total) + ' updates, including the ' +
        nfmt(routine) + ' routine ones.' + ROUTINE_MEANS;
    }
    return 'You are seeing ' + nfmt(notable) + ' of ' + nfmt(total) +
      ' updates. ' + nfmt(routine) + ' routine filings are hidden.' + ROUTINE_MEANS;
  }

  // Mirrors tit_money_chart() in shortcodes.php: same classes, same
  // data attributes, same title-attribute exact figure. The CSV download and
  // the click-to-filter wiring both read this markup, so it cannot drift.
  function paintMoney(chart, rows, label, money, dim, html) {
    if (!chart) return;
    rows = rows || [];
    var wrap = chart.querySelector('.tit-rank');
    if (wrap) {
      if (!rows.length) {
        wrap.innerHTML = '<p class="tit-rank-empty">No US dollar amounts in this view yet.</p>';
      } else {
        var max = Math.max.apply(null, rows.map(function (r) { return +r.v; })) || 1;
        wrap.innerHTML = rows.map(function (r) {
          var pct = Math.max(4, Math.round(100 * r.v / max));
          return '<button type="button" class="tit-rank-row" data-k="' + esc(r.k) + '"' +
            ' data-v="' + esc(Math.round(Number(r.v) || 0)) + '" aria-pressed="false">' +
            '<span class="tit-rank-name">' + (html ? label(r.k) : esc(label(r.k))) + '</span> ' +
            '<span class="tit-rank-track"><span class="tit-rank-fill" style="width:' + pct + '%"></span></span> ' +
            '<span class="tit-rank-n" title="' + esc(moneyFull(r.v)) + '">' +
            esc(moneyShort(r.v)) + '</span></button>';
        }).join('');
      }
    }
    var note = chart.querySelector('.tit-money-note');
    if (note) note.textContent = coverageNote(money, dim);
  }

  /*
    THE DATED GLANCE PANEL, REPAINTED UNDER THE ACTIVE FILTERS.

    Mirrors tit_dated_glance_html() in shortcodes.php exactly, the same contract
    matrixHtml() has with tit_glance_matrix_html() and renderRow() has with the
    table. The server paints this once and this repaints it on every filter
    change, so any difference between the two shows up as the panel rewriting
    itself while a reader watches.

    It has to repaint, and not only for consistency. The Copy as Post button
    reads these rendered rows, so a panel left showing unfiltered figures under a
    filtered page would let somebody copy a worldwide total off a one-country
    view. The two are one feature.

    Every suppression rule the server applies is applied here for the same
    reason it exists there: Today is dropped when it holds nothing, and the
    week-over-week comparison is printed only when this view holds a full week
    before the current one.
  */
  function datedHtml(d) {
    if (!d || !d.rows || !d.rows.length) return '';
    var lo = d.history_lo || '';
    var prevStart = d.prev_start || '';
    var prev = +d.prev_n || 0;
    // The corpus, not the week, is what decides this. See the long note in
    // tit_dated_glance_html(): the news collectors here first ran on
    // 2026-07-27, so dividing by a week that mostly predates them prints
    // something like "up 4,000%" and calls it a trend.
    var haveHistory = !!(lo && prevStart && lo <= prevStart);

    var h = '<div class="tit-dg" id="tit-dg"><div class="tit-dg-head">' +
      '<h3 class="tit-dg-title">Today, ' + esc(d.today_label || '') +
      ' <span aria-hidden="true">·</span> Sourced Talent Signals Worldwide</h3>' +
      '<button type="button" class="tit-dg-copy" id="tit-dg-copy">Copy as Post</button></div>';

    d.rows.forEach(function (r) {
      if (r.key === 'today' && (+r.n || 0) === 0) return;
      var bits = [];
      bits.push('<b>' + nfmt(r.n) + '</b> ' + (+r.n === 1 ? 'update' : 'updates'));
      if (+r.e > 0) bits.push('<b>' + nfmt(r.e) + '</b> ' + (+r.e === 1 ? 'employer' : 'employers'));
      if (+r.money > 0) bits.push('<b>' + esc(moneyShort(r.money)) + '</b> raised');
      if (+r.v > 0) bits.push('<b>' + nfmt(r.v) + '</b> from official filings');
      if (r.top && +r.top_usd > 0) {
        // The row's own country field, carried from the same scalar subquery
        // that picked the raise; absent when the source named no place. Never
        // inferred here. Mirrors tit_dated_glance_html().
        var topWhere = r.top_country
          ? ' <span aria-hidden="true">·</span> ' + esc(countryLabel(r.top_country))
          : '';
        bits.push('largest: <b>' + esc(r.top) + '</b> (' + esc(moneyShort(r.top_usd)) + topWhere + ')');
      }
      if (r.key === 'week') {
        if (haveHistory && prev > 0 && (+r.n || 0) > 0) {
          var delta = Math.round(100 * ((+r.n) - prev) / prev);
          bits.push((delta >= 0 ? 'up ' : 'down ') + '<b>' + Math.abs(delta) +
                    '%</b> vs the week before');
        } else {
          bits.push('<span class="tit-dg-nocmp">no week-on-week change yet: ' +
                    'we do not hold a full week before this one</span>');
        }
      }
      // The week's dates beside its label mirror tit_dated_glance_html(); the
      // string is server-derived so both paints agree. The single space
      // between the label button and the body span is a REAL text node, not a
      // CSS gap: without it a selected-and-copied strip pastes as
      // "This week1,366 updates", which is the sibling's "Today1,366" bug.
      var range = r.range_label
        ? ' <span class="tit-dg-range">(' + esc(r.range_label) + ')</span>' : '';
      h += '<div class="tit-dg-row" data-dg="' + esc(r.key) + '">' +
        '<button type="button" class="tit-dg-label" data-since="' + esc(r.since) +
        '" aria-pressed="false">' + esc(r.label) + range + '</button>' +
        ' <span class="tit-dg-body">' +
        bits.join(' <span aria-hidden="true">·</span> ') + '</span></div>';
    });

    var cov = coverageNote({ coverage: d.coverage }, '');
    if (cov) h += '<p class="tit-dg-cov">' + esc(cov) + '</p>';
    return h + '</div>';
  }

  function matrixHtml(m) {
    var h = '<div class="tit-matrix-scroll"><table class="tit-matrix"><thead><tr>' +
      '<th scope="col"><span class="tit-sr">Signal</span></th>';
    m.periods.forEach(function (p) { h += '<th scope="col">' + esc(p) + '</th>'; });
    h += '</tr></thead><tbody>';
    m.rows.forEach(function (r) {
      var money = r.kind === 'money';
      // Row max, so the heat tint is scaled inside the row, matching the PHP.
      var rowMax = 0;
      r.cells.forEach(function (n) { rowMax = Math.max(rowMax, +n || 0); });
      var cls = 'tit-matrix-row' + (r.key === 'total' ? ' tit-matrix-total' : '') +
        (money ? ' tit-matrix-money' : '');
      h += '<tr class="' + cls + '" data-signal="' + esc(r.key) + '">' +
        '<th scope="row">' + esc(r.label) +
        (money ? '<span class="tit-matrix-unit">sum of dollars</span>' : '') + '</th>';
      r.cells.forEach(function (n, i) {
        n = +n || 0;
        var intensity = (rowMax > 0 && n > 0)
          ? Math.max(0.14, Math.round(Math.sqrt(n / rowMax) * 1000) / 1000) : 0;
        var text = money ? moneyShort(n) : nfmt(n);
        var full = money ? moneyFull(n) : nfmt(n);
        var spoken = money
          ? r.label + ', ' + m.periods[i] + ', ' + full + ' in US dollars'
          : r.label + ', ' + m.periods[i];
        h += '<td><button type="button" class="tit-cell' + (n === 0 ? ' tit-cell-zero' : '') +
          (money ? ' tit-cell-money' : '') +
          '" style="--i:' + intensity + '" data-filter="' + esc(r.filter) +
          '" data-since="' + esc(m.starts[i]) + '"' +
          (money ? ' title="' + esc(full) + '"' : '') +
          ' aria-pressed="false" aria-label="' + esc(spoken) + '">' +
          // Mirrors the .tit-cell-p span in tit_glance_matrix_html(). Below
          // 860px the table is laid out as one card per row, which drops the
          // implicit table roles and with them the column header; this is the
          // real text that replaces it. Hidden by CSS on desktop.
          '<span class="tit-cell-p">' + esc(m.periods[i]) + '</span>' +
          esc(text) + '</button></td>';
      });
      h += '</tr>';
    });
    /*
      ONE IDEA PER LINE, AND THE SAME LINES THE SERVER PRINTS.

      The owner's verdict on the old block was "this make s not sentds". Two
      paragraphs carried seven separate ideas between them, and the reader had to
      unpack a clause at a time to find the one they needed. A list is the right
      shape for a list, so it is a list now.

      NOT ONE FACT IS CUT. What the columns count, why the figures above are
      bigger, what the colour means, that the rows overlap so the columns do not
      sum, that a number is tappable, and that one row sums dollars while the
      rest count updates: all still here, all still computed.

      TWO DIVERGENCES FIXED. This function used to omit the "each column counts"
      paragraph entirely, so the first filter change silently deleted an
      explanation the server had rendered. And it is now a <details>, matching
      tit_glance_matrix_html(), or a repaint would revert the phone disclosure to
      an open wall of prose. Keep the two in step: the server paints these once
      and this function repaints them on every filter change, so any difference
      shows up as the block rewriting itself while a reader watches.
    */
    h += '</tbody></table></div>' +
      '<details class="tit-matrix-note" open><summary>How To Read This</summary>' +
      '<ul class="tit-matrix-points">' +
      '<li>Each column counts updates whose source dated them inside that window.</li>' +
      '<li>The headline figures count everything in this view, over the whole ' +
      'period we hold, which is why they are larger.</li>' +
      '<li>Colour shows relative activity within each row.</li>' +
      '<li>Rows overlap, so the columns do not add up. A funded employer may ' +
      'also be hiring.</li>' +
      '<li><strong>Tap any number to filter the page.</strong></li>' +
      '<li class="tit-matrix-money-note">Total Raised sums dollars. Every other ' +
      'row counts updates. ' + esc(coverageNote({ coverage: m.coverage }, '')) +
      '</li></ul></details>';
    return h;
  }

  var pendingAgg = null;

  function refreshAggregate(params) {
    if (pendingAgg) pendingAgg.abort();
    pendingAgg = new AbortController();
    fetch(TIT.api + 'aggregate?' + params.toString(), { signal: pendingAgg.signal })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) paintAggregate(data); })
      .catch(function (err) {
        // Leave the server-rendered numbers alone rather than blanking them.
        if (err && err.name === 'AbortError') return;
      });
  }

  var pending = null;
  var lastQuery = '';

  // The querystring people see and share, which is not the one the API gets:
  // per_page is paging, and sort/country_basis at their defaults say nothing.
  // A link should carry the choices someone actually made.
  function shareQuery(params) {
    var out = new URLSearchParams();
    params.forEach(function (value, key) {
      if (key === 'per_page') return;
      if (NEUTRAL[key] === value) return;
      out.set(key, value);
    });
    return out.toString();
  }

  // Restore a shared link. Anything the page does not recognise is left alone
  // rather than guessed at, and the region strip is matched by its code list so
  // a link to "Europe" comes back as the Europe tab and not a bare country
  // filter that looks nothing like what was sent.
  function applyUrlState() {
    var q = new URLSearchParams(location.search);
    if (!q.toString()) return;

    var country = q.get('country') || '';
    var tab = quickFind(tabs, function (t) { return (t.dataset.codes || '') === country; });
    if (country && tab) {
      setRegion(country);
      q.delete('country');
    }

    if (q.get('stated_headcount') === '1') setStated(true);

    // Saved views are matched by their PARAMETERS, never by a token, because a
    // shared link carries the expanded querystring: "Biggest Raises" arrives as
    // funding=1&sort=raised. A view whose every parameter is present in the
    // link is the view that was sent, and its parameters are then removed so
    // the same narrowing does not also appear as a second chip.
    var qv = quickFind(quickButtons, function (b) {
      var spec = b.dataset.qv || '';
      if (!spec) return false;
      return spec.split('&').every(function (pair) {
        var kv = pair.split('=');
        return q.get(kv[0]) === decodeURIComponent(kv[1] || '');
      });
    });
    if (qv) {
      setQuickView(qv.dataset.qv);
      qv.dataset.qv.split('&').forEach(function (pair) { q.delete(pair.split('=')[0]); });
    } else if (q.get('funding') === '1') {
      // funding=1 is the one shareable param with no input of its own, and no
      // button of its own either since the quick views were cut back. It is
      // still applied by the matrix's funding and money cells, so a link
      // carrying it must come back as a funding view rather than as the
      // unfiltered page. Without this a deep link quietly drops the very thing
      // it was sent to show.
      setQuickView('funding=1');
      q.delete('funding');
    }

    Object.keys(inputs).forEach(function (key) {
      var el = inputs[key];
      if (!el || !q.has(key)) return;
      var value = q.get(key);
      if (MULTI[key]) {
        // Facet-driven options may not have arrived yet, same race the single
        // selects have: add what the link asked for rather than dropping it.
        var wanted = value.split(',').filter(Boolean);
        wanted.forEach(function (v) { ensureOption(el, v, v); });
        setMulti(el, wanted);
        return;
      }
      if (el.tagName === 'SELECT') {
        var ok = Array.prototype.some.call(el.options, function (o) { return o.value === value; });
        if (!ok) {
          // Countries, states, cities and funding stages come from /facets,
          // which is still in flight at this point: this runs on the first
          // paint, and that fetch has not answered yet. Dropping the value
          // because its option does not exist YET made every shared link to a
          // single country come back as the whole world. The closed
          // vocabularies (kind, direction, team, industry, evidence, amount
          // band) are all server-rendered, so a value missing from one of
          // those really is a value we do not have, and is still ignored
          // rather than guessed at.
          // A sort a column header chose has no server-rendered option
          // either, so a link carrying one would come back sorted the default
          // way while the select claimed otherwise.
          if (key === 'sort' && SORT_OPTION_LABEL[value]) {
            ensureOption(el, value, SORT_OPTION_LABEL[value]);
          } else if (FACET_SELECT[key]) {
            ensureOption(el, value,
              key === 'country' ? countryLabel(value)
                : (key === 'funding_stage' ? (STAGE_LABEL[value] || value) : value));
          } else {
            return;
          }
        }
      }
      el.value = value;
    });
  }

  // Selects whose options are fetched rather than rendered by the server.
  var FACET_SELECT = {
    country: 1, state: 1, city: 1, funding_stage: 1,
    employer_type: 1, work_mode: 1, deal_type: 1, site_event: 1
  };

  // --- Filters that take several values at once -----------------------------
  // A recruiter wants "Technology or Healthcare", not one at a time. These are
  // native <select multiple>: keyboard reachable without a line of our own
  // code, scrollable in place, and every choice becomes its own removable chip.
  // Kept single where several genuinely make no sense: Evidence is a floor, and
  // the Amount Raised bands already nest.
  var MULTI = {
    'function': 1, industry: 1, employer_type: 1,
    funding_stage: 1, work_mode: 1, deal_type: 1, site_event: 1
  };

  function multiValues(el) {
    if (!el) return [];
    return Array.prototype.filter.call(el.options, function (o) {
      return o.selected && o.value;
    }).map(function (o) { return o.value; });
  }

  function setMulti(el, values) {
    if (!el) return;
    Array.prototype.forEach.call(el.options, function (o) {
      o.selected = values.indexOf(o.value) >= 0;
    });
  }

  // --- Checkboxes over the multi selects ------------------------------------
  //
  // WHY CHECKBOXES AND NOT PILLS, AND NOT A NATIVE LIST BOX.
  //
  // This control has now been three things. It shipped as a native
  // <select multiple size="5">: keyboard reachable for free, but a five-row
  // scroll window that hides fifteen of Industry's twenty options behind a
  // scrollbar with no affordance saying so, and one that needs ctrl-click to
  // add a second value -- an interaction most readers do not know exists.
  // It then became a row of toggle pills, which solved the discoverability
  // and lost the thing that made the list box worth keeping: a pill row is
  // not a list, so seven of them wrapping into a panel read as one
  // undifferentiated wall of options with no boundary between groups. That is
  // the exact complaint this rewrite answers.
  //
  // So: a real checkbox per option, one per line, inside a box with a capped
  // height that SCROLLS. A checkbox is the only control in HTML that says
  // "several of these, independently" without being taught, it is hit-target
  // sized on a phone, it announces its own state to a screen reader with no
  // aria of ours, and a capped scrolling box gives the group a visible edge so
  // one group cannot run into the next.
  //
  // The <select multiple> IS STILL THE STATE and nothing about that moved.
  // The querystring, the chips bar, resets, the matrix cells, click-to-filter,
  // the exports and the facet refills all read and write the select exactly as
  // before; this function re-renders from it after every change. The select is
  // presentation-hidden rather than removed, which is also what keeps a
  // JavaScript-off visitor with a native control (see the <noscript> rule in
  // dashboard.css).
  function pillify(el) {
    if (!el || !el.multiple) return;
    var host = el.closest('label') || el.parentElement;
    if (!host) return;
    host = asDropdown(host, el.getAttribute('aria-describedby'));
    // Before the early return below, not after it: the option set is unchanged
    // on most repaints and that is exactly when a value was just ticked.
    dropCount(host, multiValues(el).length);
    var group = host.querySelector('.tit-optbox');
    if (!group) {
      group = document.createElement('div');
      group.className = 'tit-optbox';
      group.setAttribute('role', 'group');
      // The select carries the group's accessible name (the visible
      // .tit-field-l beside it), so hand the same name to the box that has
      // replaced it rather than leaving a bare scroll container.
      if (el.id) group.setAttribute('data-for', el.id);
      (host.querySelector('.tit-dd-panel') || host).appendChild(group);
      el.classList.add('tit-select-hidden');
      el.tabIndex = -1;
      el.setAttribute('aria-hidden', 'true');
      // `change`, not `click`: a checkbox toggled with the spacebar fires
      // change and not click in every engine, and a keyboard user is exactly
      // who the native control was protecting.
      group.addEventListener('change', function (e) {
        var box = e.target;
        if (!box || box.type !== 'checkbox') return;
        var opt = quickFind(Array.prototype.slice.call(el.options), function (o) {
          return o.value === box.value;
        });
        if (!opt) return;
        opt.selected = box.checked;
        el.dispatchEvent(new Event('change', { bubbles: false }));
        // Re-render from the select, so the boxes can never disagree with the
        // state they drive.
        pillify(el);
      });
    }
    /*
      Re-rendering the whole box would throw away focus mid-keyboard-run, so
      when the option set has not changed only the checked flags are updated.
      The option set DOES change on a facet refill, which is the case the
      rebuild is for.
    */
    var values = Array.prototype.filter.call(el.options, function (o) { return o.value; });
    var existing = group.querySelectorAll('input[type=checkbox]');
    if (existing.length === values.length) {
      var same = true;
      for (var i = 0; i < values.length; i++) {
        if (existing[i].value !== values[i].value) { same = false; break; }
      }
      if (same) {
        for (var j = 0; j < values.length; j++) {
          existing[j].checked = values[j].selected;
          var row = existing[j].closest('.tit-optrow');
          if (row) row.classList.toggle('is-on', values[j].selected);
        }
        return;
      }
    }
    group.innerHTML = values.map(function (o) {
      return '<label class="tit-optrow' + (o.selected ? ' is-on' : '') + '">' +
        '<input type="checkbox" value="' + esc(o.value) + '"' +
        (o.selected ? ' checked' : '') + '>' +
        '<span class="tit-optrow-t">' + esc(o.textContent) + '</span></label>';
    }).join('');
  }

  /* --- CHECKBOX DROPDOWNS ----------------------------------------------------
     WHY THE CHECKBOXES MOVED BEHIND A BUTTON.

     They did not stop being checkboxes. Everything written above pillify() about
     why a checkbox beats a native list box and beats a pill row still holds, and
     the checkboxes it renders are the same checkboxes. What changed is that all
     seven groups no longer have to be on screen AT ONCE.

     That was the whole cost of the sidebar. Seven capped scrolling boxes stacked
     in a column is 262px of width the table cannot have, and the owner read the
     result on the live page: the What Happened column, which carries a headline
     and a read-through, wrapped to one word per line. A group that is a button
     until you want it costs one line of a wrapped bar.

     THE NATIVE SELECT IS STILL THE STATE, and this layer adds no new state
     channel of any kind. The querystring, the chips bar, resets, the matrix
     cells, click-to-filter, the exports and the facet refills all read and write
     the select exactly as before, and pillify() re-renders from it. Everything
     here is built at RUNTIME, so a reader whose script never ran gets the bar
     with plain native controls in it and nothing missing.

     Three things the sibling tracker's version of this control does not do, each
     one a real defect rather than a preference: Escape does not close it and
     focus is never returned to the trigger; the trigger has no :focus-visible
     ring; and the panel is absolutely positioned at left:0 with no flip, so the
     rightmost control on a row pushes a scrollbar onto the body. All three are
     handled below. Its trigger also claims aria-haspopup="listbox" over a panel
     that contains no listbox roles at all; ours does not claim one, because what
     is in there is a group of checkboxes and that is what it says.
  */
  var openDrop = null;

  function closeDrop(returnFocus) {
    if (!openDrop) return;
    var was = openDrop;
    openDrop = null;
    was.panel.hidden = true;
    was.panel.classList.remove('is-flipped');
    was.btn.setAttribute('aria-expanded', 'false');
    if (returnFocus) was.btn.focus();
  }

  function openDrop_(rec) {
    closeDrop(false);
    rec.panel.hidden = false;
    rec.btn.setAttribute('aria-expanded', 'true');
    openDrop = rec;
    /* Flip only when it would actually run off, and measure rather than guess:
       the trigger's position depends on how the bar happened to wrap, which no
       breakpoint can know. `clientWidth` excludes the scrollbar, which is the
       measurement that decides whether the body gets a SECOND one. */
    var room = document.documentElement.clientWidth;
    if (rec.panel.getBoundingClientRect().right > room - 8) {
      rec.panel.classList.add('is-flipped');
    }
  }

  /* Turn a filter cell into a trigger plus a panel, once. Idempotent: pillify()
     runs on every repaint and must find the dropdown it built last time. */
  function asDropdown(field, describedBy) {
    if (!field) return field;
    if (field.classList.contains('tit-dd')) return field;

    /* A <label> forwards a click on itself to its own control, so a <button>
       inside one activates the hidden select as well as the trigger. The
       wrapper therefore stops being a label the moment it contains a button.
       The select it labelled is display:none, tabindex -1 and aria-hidden by
       then, so it is out of the accessibility tree and has no name to lose. */
    if (field.tagName === 'LABEL') {
      var div = document.createElement('div');
      /* EVERY attribute, not a chosen few. Copying only class and id lost
         `hidden`, and `hidden` is what keeps a facet control whose column is
         still empty off the page: five always-empty controls -- Employer Type,
         Work Setup, Funding Stage, Deal Type, Site Change -- appeared on the
         bar offering nothing, which is the exact failure the hidden attribute
         was added to prevent. `for` is dropped because it means nothing on a
         div and would be a dangling reference. */
      for (var a = 0; a < field.attributes.length; a++) {
        var at = field.attributes[a];
        if (at.name !== 'for') div.setAttribute(at.name, at.value);
      }
      while (field.firstChild) div.appendChild(field.firstChild);
      field.parentNode.replaceChild(div, field);
      field = div;
    }
    field.classList.add('tit-dd');

    var lab = field.querySelector('.tit-field-l');
    var name = lab ? lab.textContent.trim() : 'Filter';
    /* The visible label is now the button's own text. Kept in the DOM rather
       than deleted because the date range names its group through it with
       aria-labelledby, and an aria reference resolves to a hidden element. */
    if (lab) lab.hidden = true;

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tit-dd-btn';
    // Set at construction, not on first interaction: a trigger that reports no
    // expanded state until somebody has already used it is telling a screen
    // reader nothing at exactly the moment it matters.
    btn.setAttribute('aria-expanded', 'false');
    if (describedBy) btn.setAttribute('aria-describedby', describedBy);
    btn.innerHTML = '<span class="tit-dd-btn-t"></span>' +
      '<span class="tit-dd-n" hidden></span>' +
      '<span class="tit-dd-caret" aria-hidden="true"></span>';
    btn.querySelector('.tit-dd-btn-t').textContent = name;

    var panel = document.createElement('div');
    panel.className = 'tit-dd-panel';
    panel.hidden = true;
    if (field.id) {
      panel.id = field.id + '-panel';
      btn.setAttribute('aria-controls', panel.id);
    }

    field.appendChild(btn);
    field.appendChild(panel);

    var rec = { field: field, btn: btn, panel: panel };

    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (openDrop === rec) closeDrop(false);
      else openDrop_(rec);
    });
    // Ticking a checkbox must not close the panel it is in; one at a time is
    // enforced by openDrop_ rather than by an overlay.
    panel.addEventListener('click', function (e) { e.stopPropagation(); });
    // Tabbing out of the panel closes it. Only when there IS a new focus
    // target: a click on the panel's own padding reports relatedTarget null,
    // and closing on that would shut the panel under the reader's finger.
    field.addEventListener('focusout', function (e) {
      if (openDrop !== rec) return;
      if (e.relatedTarget && !field.contains(e.relatedTarget)) closeDrop(false);
    });
    return field;
  }

  document.addEventListener('click', function () { closeDrop(false); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && openDrop) {
      e.stopPropagation();
      closeDrop(true);
    }
  });
  /* A resize closes whatever is open, and this is not tidiness.
     `is-flipped` is decided ONCE, from where the trigger stood when it opened,
     because deciding it continuously would mean measuring on every scroll of a
     sticky bar. So a panel that survives a resize keeps a decision made about a
     viewport that no longer exists: measured here, a panel opened at 390px and
     then widened to 1280px sat at left:0 of a trigger that had wrapped to the
     right-hand end, ran 59px past the edge and put a horizontal scrollbar on
     the BODY -- the one thing this page is not allowed to do. Closing is also
     the honest response to the reader's viewport changing under them.
     Orientation change fires `resize` in every engine we care about. */
  window.addEventListener('resize', function () { closeDrop(false); });

  /* The count is printed, and the trigger also changes fill and weight. Never
     colour alone, and never the fill alone either: "3" answers "how many" where
     a tinted button only answers "some". */
  function dropCount(field, n) {
    if (!field) return;
    var btn = field.querySelector('.tit-dd-btn');
    var badge = field.querySelector('.tit-dd-n');
    if (!btn || !badge) return;
    badge.textContent = String(n);
    badge.hidden = n === 0;
    btn.classList.toggle('is-on', n > 0);
  }

  /*
    THE "HOW TO READ THIS" BLOCK STARTS CLOSED ON A PHONE, OPEN EVERYWHERE ELSE.

    The three explanations under the matrix are honesty surfaces and not one word
    of them is cut, but at 390px they were about fifteen lines of prose between
    the reader and any content. So the markup ships <details open> -- which is
    what a crawler, a desktop reader and a reader with no JavaScript or no CSS
    all get, every word in the initial HTML, nothing fetched -- and this is the
    only thing that closes it, on a narrow viewport, once.

    It has to be script rather than CSS because `open` is an ATTRIBUTE and a
    stylesheet cannot remove one. Hiding the panel's contents with CSS instead
    would leave a summary that says "open me" over content the browser believes
    is already open, so the first tap would close it and the second reopen it.

    Once, on load, and never on resize: re-closing a panel a reader has just
    opened because they rotated the phone is worse than either state.
  */
  function collapseMatrixNoteOnPhone() {
    var d = document.querySelector('details.tit-matrix-note');
    if (!d || !window.matchMedia) return;
    if (window.matchMedia('(max-width: 860px)').matches) d.open = false;
  }

  /* The one dropdown whose panel is not a checkbox group.

     Two date inputs are about 260px side by side, and on a bar they would say
     "no dates chosen" in that space on almost every page view. Behind a trigger
     they cost one slot and still open to the same two labelled inputs, which is
     the sibling tracker's From/To pattern unchanged. The count is a count of
     BOUNDS set, so a reader who set only a start sees 1 and knows why the page
     narrowed. */
  var dateField = document.getElementById('tit-field-daterange');

  function syncDateDrop() {
    if (!dateField) return;
    asDropdown(dateField);
    var panel = dateField.querySelector('.tit-dd-panel');
    var range = dateField.querySelector('.tit-daterange');
    // The inputs are server-rendered inside the cell, so unlike the checkbox
    // groups they are moved rather than built.
    if (panel && range && range.parentNode !== panel) panel.appendChild(range);
    var n = 0;
    if (inputs.since && inputs.since.value) n++;
    if (inputs.until && inputs.until.value) n++;
    dropCount(dateField, n);
  }

  /* Location is a dropdown too, and it is the one where the trade is worth
     stating. Its current value stops being readable ON the bar, which is a real
     loss for the most-used filter here. It buys two things: the qualifier that
     changes what the select MEANS travels with it instead of sitting on the bar
     as a 200px sentence next to an unrelated control, and the bar drops from
     three wrapped rows to two -- 193px of frozen chrome to 150px. The chips bar
     directly under it already names the chosen place in words and offers the
     way out of it, so the value is still on screen, once, where every other
     applied filter is also named. */
  var placeField = document.querySelector('.tit-primary-where');

  function syncPlaceDrop() {
    if (!placeField) return;
    asDropdown(placeField);
    var panel = placeField.querySelector('.tit-dd-panel');
    if (!panel) return;
    ['.tit-where-label', '.tit-basis-check'].forEach(function (sel) {
      var el = placeField.querySelector(sel);
      if (el && el.parentNode !== panel) panel.appendChild(el);
    });
    var n = 0;
    if (placeSel && placeSel.value) n++;
    // Counted, because it narrows the page on its own. A reader who ticked it
    // and chose no place would otherwise see an untouched-looking control.
    var basis = document.getElementById('tit-basis-chk');
    if (basis && basis.checked) n++;
    dropCount(placeField, n);
  }

  function syncAllPills() {
    Object.keys(MULTI).forEach(function (k) { if (inputs[k]) pillify(inputs[k]); });
    syncDateDrop();
    syncPlaceDrop();
  }

  function optionText(el, value) {
    if (!el) return value;
    var hit = quickFind(Array.prototype.slice.call(el.options), function (o) {
      return o.value === value;
    });
    return hit ? hit.textContent : value;
  }

  // A control whose column is still empty is HIDDEN rather than shown returning
  // nothing, and it appears by itself the day the pipeline fills that column.
  // Nothing hardcoded, so nothing to go stale.
  function fillFacetControl(key, values, labels) {
    var el = inputs[key];
    var field = document.getElementById('tit-field-' + key);
    if (!el) return;
    fill(el, values || [], false, labels);
    var has = Array.prototype.some.call(el.options, function (o) { return !!o.value; });
    if (field) field.hidden = !has;
    if (has) pillify(el);
  }

  // --- The two front controls ----------------------------------------------
  // The filter bar used to be twelve controls of equal weight, each labelled
  // with a database column. These two are the visible layer over five of the
  // old ones: they READ and WRITE the same hidden selects, so the querystring,
  // the chips bar, the exports and every click-to-filter chart keep working
  // against exactly the state they always did. Nothing about what a filter
  // means has changed; only what a person has to look at.

  var lookingSel = document.getElementById('tit-f-looking');
  var placeSel = document.getElementById('tit-f-place');

  // "I'm looking for" writes the pillar select, the direction select, or the
  // funding view, depending on which one its option names.
  function applyLooking(spec) {
    if (inputs.pillar) inputs.pillar.value = '';
    if (inputs.direction) inputs.direction.value = '';
    if (quickView === 'funding=1') setQuickView(null);
    if (!spec) return;
    var kv = spec.split('=');
    if (kv[0] === 'funding') { setQuickView('funding=1'); return; }
    if (inputs[kv[0]]) inputs[kv[0]].value = kv[1];
  }

  // And the reverse, so a chart click or a matrix cell leaves the control
  // showing what is actually filtering the page. A control that says "All
  // updates" while the page is filtered to leadership changes is worse than no
  // control at all.
  function syncLooking() {
    if (!lookingSel) return;
    var want = '';
    if (inputs.pillar && inputs.pillar.value) want = 'pillar=' + inputs.pillar.value;
    else if (inputs.direction && inputs.direction.value) want = 'direction=' + inputs.direction.value;
    else if (quickView === 'funding=1') want = 'funding=1';
    var has = Array.prototype.some.call(lookingSel.options, function (o) { return o.value === want; });
    // A combination the control cannot express (two of them at once, or a
    // direction it does not offer) shows as blank rather than as a lie.
    lookingSel.value = has ? want : '';
  }

  // One "Where", three underlying columns. Option values carry which one:
  // country:US, state:CA, city:London.
  function applyPlace(value) {
    if (inputs.country) inputs.country.value = '';
    if (inputs.state) inputs.state.value = '';
    if (inputs.city) inputs.city.value = '';
    if (!value) return;
    var at = value.indexOf(':');
    var key = value.slice(0, at);
    var val = value.slice(at + 1);
    if (!inputs[key]) return;
    ensureOption(inputs[key], val, val);
    inputs[key].value = val;
    // A region and a single country are two answers to one question, and
    // sending both would return nothing while looking like a broken filter.
    if (key === 'country') setRegion(null);
  }

  function syncPlace() {
    if (!placeSel) return;
    var want = '';
    // A region strip selection overrides the country parameter in refresh(),
    // so while a region is on, no single place is what is narrowing the page.
    // Showing "United States" in this box while a Region chip was doing the
    // work made the system's choice look like the reader's.
    if (!region) {
      if (inputs.country && inputs.country.value) want = 'country:' + inputs.country.value;
      else if (inputs.state && inputs.state.value) want = 'state:' + inputs.state.value;
      else if (inputs.city && inputs.city.value) want = 'city:' + inputs.city.value;
    }
    var has = Array.prototype.some.call(placeSel.options, function (o) { return o.value === want; });
    if (!has && want) {
      ensureOption(placeSel, want, placeLabel(want));
      has = true;
    }
    placeSel.value = has ? want : '';
  }

  function placeLabel(value) {
    var at = value.indexOf(':');
    var key = value.slice(0, at), val = value.slice(at + 1);
    return key === 'country' ? countryLabel(val) : val;
  }

  if (lookingSel) {
    lookingSel.addEventListener('change', function () {
      applyLooking(lookingSel.value);
      refresh();
    });
  }
  if (placeSel) {
    placeSel.addEventListener('change', function () {
      applyPlace(placeSel.value);
      refresh();
    });
  }

  // --- Only updates that state a headcount ----------------------------------
  // Its own state rather than a member of `inputs`, because a checkbox has no
  // meaningful .value and everything in that map is read as one.
  var statedBox = document.getElementById('tit-f-stated_headcount');
  var stated = false;

  function setStated(on) {
    stated = !!on;
    if (statedBox) statedBox.checked = stated;
  }

  if (statedBox) {
    statedBox.addEventListener('change', function () {
      setStated(statedBox.checked);
      refresh();
    });
  }

  // --- How places are decided ----------------------------------------------
  // A methodology choice, not a filter, so it stays attached to the Where
  // control rather than floating off as a twelfth dropdown.
  //
  // It is a CHECKBOX now, not a button that rewrote its own label. The button
  // named its destination ("Only use places a source named" when off, "Use head
  // office when no place is named" when on), which meant the words on screen
  // described the state you were NOT in, and the only thing saying which state
  // you were actually in was a three-line paragraph beside it. A checkbox says
  // one thing and shows whether it is on. The explanation moved to the (i) at
  // the top of the panel and reaches this control through aria-describedby.
  //
  // The state itself has not moved: the hidden country_basis select is still
  // what the querystring, the exports and every chart click read and write.
  var basisChk = document.getElementById('tit-basis-chk');

  function basisValue() {
    return (inputs.country_basis && inputs.country_basis.value) || 'any';
  }

  function syncBasis() {
    if (basisChk) basisChk.checked = basisValue() === 'location';
  }

  if (basisChk) {
    basisChk.addEventListener('change', function () {
      if (!inputs.country_basis) return;
      inputs.country_basis.value = basisChk.checked ? 'location' : 'any';
      refresh();
    });
  }

  // --- The filter panel -----------------------------------------------------
  // Nothing to do here any more, deliberately. It shipped as a <details> whose
  // summary read "More filters (1)"; the owner asked three times for that
  // wording to go, so it became a plain <div> that is always open. What was left
  // behind was a syncMore() setting .open on an element that has no .open, a
  // moreActive() nothing called, and a lookup for an id no markup carries. The
  // panel discloses nothing, and the chips bar above the table names what is
  // applied, which is what the summary was standing in for.

  // The CSV and JSON links under the table download exactly what is on screen:
  // the current filters ride along as query params, and the scope word says
  // which set the file will hold. Server-rendered hrefs point at the whole
  // dataset, so a reader without JavaScript still gets a working download.
  function updateExportLinks() {
    ['tit-export-csv', 'tit-export-json'].forEach(function (id) {
      var a = document.getElementById(id);
      if (!a) return;
      var base = a.getAttribute('data-base');
      if (!base) return;
      a.href = base + (lastQuery ? '&' + lastQuery : '');
      var scope = document.getElementById(id + '-scope');
      if (scope) scope.textContent = lastQuery ? ' · filtered' : ' · all';
    });
  }

  function refresh() {
    var params = new URLSearchParams();
    Object.keys(inputs).forEach(function (key) {
      if (!inputs[key]) return;
      if (MULTI[key]) {
        // Comma separated, and /query checks every value against its closed
        // vocabulary before it reaches SQL.
        var chosen = multiValues(inputs[key]);
        if (chosen.length) params.set(key, chosen.join(','));
        return;
      }
      var value = inputs[key].value.trim();
      // `detail` is the one control whose page default differs from the API's
      // own. The endpoint returns everything unless asked, deliberately: an
      // endpoint that quietly withheld two thirds of its rows would be a worse
      // lie than a cluttered page. So the page asks, every time, even at its
      // default value.
      if (value && (key === 'detail' || value !== NEUTRAL[key])) params.set(key, value);
    });
    // A region is a list of country codes, so it takes the same parameter as the
    // country select. Whichever the person touched last is the one that counts —
    // silently ANDing "Europe" with "Japan" would return nothing and look broken.
    if (region) params.set('country', region);
    if (stated) params.set('stated_headcount', '1');
    // A quick view is a saved set of parameters, applied on top of whatever
    // else is selected rather than replacing it: someone who has picked Europe
    // and then clicks "Raised money" means both.
    if (quickView) {
      quickView.split('&').forEach(function (pair) {
        var kv = pair.split('=');
        // Anything the view wrote into a visible control is already in the
        // inputs loop above; re-applying it here would let a quick view move
        // the window (or the sort) while the control still showed the reader's
        // own choice.
        if (kv[0] && !QV_VISIBLE[kv[0]]) {
          params.set(kv[0], decodeURIComponent(kv[1] || ''));
        }
      });
    }
    params.set('per_page', '50');

    // The visible controls follow the state, never the other way round: a
    // chart click or a matrix cell writes the hidden selects, and these put the
    // two front controls back in agreement with them.
    syncLooking();
    syncPlace();
    syncCountryButtons();
    syncCityButtons();
    syncBasis();

    paintActive();
    syncChartStates();

    // Put the view in the address bar. A dashboard whose URL never changes
    // cannot be sent to anyone: the recipient gets the unfiltered page and no
    // idea what they were meant to be looking at. replaceState, not pushState,
    // so typing in the search box does not bury the back button under a history
    // entry per keystroke.
    // Through writeUrl(), which is also where the expanded card is added, so
    // narrowing the page cannot silently drop the card a link was sent to open.
    lastQuery = shareQuery(params);
    writeUrl();

    updateExportLinks();

    // Charts and figures move with the same querystring the table uses, so the
    // two can never disagree about what is being shown.
    refreshAggregate(params);

    if (pending) pending.abort();
    pending = new AbortController();

    fetch(TIT.api + 'query?' + params.toString(), { signal: pending.signal })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        // The empty state is a statement of policy, not an apology: showing
        // nothing beats guessing, and it says so where the rows would be.
        // A one-line "nothing matches" in a seven-column table read as a
        // rendering fault; this reads as an answer, and it carries its own
        // way out (handled by delegation on the list, since this markup is
        // re-created on every empty render).
        tbody.innerHTML = data.rows.length
          ? data.rows.map(renderCard).join('')
          : '<li class="tit-cards-empty">' +
            '<div class="tit-table-empty">' +
            '<p class="tit-table-empty-h">Nothing matches those filters</p>' +
            '<p class="tit-table-empty-p">We would rather show you nothing than guess.</p>' +
            '<button type="button" class="tit-empty-clear">Reset all filters</button>' +
            '</div></li>';
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;
        /* leave the existing rows in place */
      });
  }

  var timer;
  function debounced() {
    clearTimeout(timer);
    timer = setTimeout(refresh, 250);
  }

  Object.keys(inputs).forEach(function (key) {
    if (!inputs[key]) return;
    inputs[key].addEventListener(inputs[key].tagName === 'SELECT' ? 'change' : 'input', function () {
      if (key === 'country') setRegion(null);
      // Editing a control a saved view had taken over means leaving that view.
      // "Biggest Raises" is a sort plus a filter; change the sort by hand and
      // the chip would otherwise keep claiming a view the page no longer
      // shows. What the reader just chose is kept, the view is dropped.
      if (QV_VISIBLE[key] && quickView &&
          quickView.split('&').some(function (p) { return p.split('=')[0] === key; })) {
        var kept = inputs[key].value;
        setQuickView(null);
        inputs[key].value = kept;
      }
      debounced();
    });
  });

  var region = null;
  var tabs = Array.prototype.slice.call(root.querySelectorAll('.tit-region'));

  function setRegion(codes) {
    region = codes || null;
    tabs.forEach(function (t) {
      var on = (t.dataset.codes || '') === (region || '');
      t.classList.toggle('is-on', on);
      t.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  tabs.forEach(function (t) {
    t.addEventListener('click', function () {
      setRegion(t.dataset.codes);
      // A region and a single country answer the same question. Keeping both
      // would send a country list AND a country, which returns nothing whenever
      // they disagree and reads as a broken filter rather than an empty set.
      if (inputs.country) inputs.country.value = '';
      refresh();
    });
  });
  setRegion(null);

  // --- The country row -----------------------------------------------------
  // Derived from live counts on the server, subordinate to the regions above
  // it, and wired through the SAME country input every other control uses, so
  // one click updates the chips bar, the Where picker, the charts, the address
  // bar and the exports in one pass.
  var cbtns = Array.prototype.slice.call(root.querySelectorAll('.tit-cbtn'));

  function syncCountryButtons() {
    var current = region ? '' : (inputs.country ? inputs.country.value : '');
    cbtns.forEach(function (b) {
      var on = current !== '' && b.getAttribute('data-code') === current;
      b.classList.toggle('is-on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  cbtns.forEach(function (b) {
    b.addEventListener('click', function () {
      var code = b.getAttribute('data-code');
      var was = !region && inputs.country && inputs.country.value === code;
      // Picking a country REPLACES the region rather than stacking with it.
      // Regions contain their countries, so this always narrows and can never
      // produce the empty page that Europe-plus-United-States would.
      setRegion(null);
      if (inputs.country) {
        ensureOption(inputs.country, code, countryLabel(code));
        inputs.country.value = was ? '' : code;
      }
      refresh();
    });
  });

  // The city row, wired like the country row through the same city input the
  // Where picker uses. Picking a city clears country and region: a city inside
  // a conflicting country filter is the guaranteed-empty page.
  var citybtns = Array.prototype.slice.call(root.querySelectorAll('.tit-citybtn'));

  function syncCityButtons() {
    var current = inputs.city ? inputs.city.value : '';
    citybtns.forEach(function (b) {
      var on = current !== '' && b.getAttribute('data-city') === current;
      b.classList.toggle('is-on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  citybtns.forEach(function (b) {
    b.addEventListener('click', function () {
      var name = b.getAttribute('data-city');
      var was = inputs.city && inputs.city.value === name;
      setRegion(null);
      if (inputs.country) inputs.country.value = '';
      if (inputs.city) {
        ensureOption(inputs.city, name, name);
        inputs.city.value = was ? '' : name;
      }
      refresh();
    });
  });

  var quickView = null;
  var quickButtons = Array.prototype.slice.call(root.querySelectorAll('.tit-qv'));

  // Parameters a quick view writes into a VISIBLE control rather than straight
  // into the querystring. A view that sorts by size while the sort box still
  // reads "Newest first" is a page ordering itself by a rule nothing on screen
  // reports, which is the same failure as a date filter with an empty From box.
  var QV_VISIBLE = { since: 1, until: 1, sort: 1 };

  function setQuickView(v) {
    var prev = quickView;
    quickView = v || null;
    quickButtons.forEach(function (o) {
      var on = (o.dataset.qv || '') === (quickView || '');
      o.classList.toggle('is-on', on);
      o.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    // Leaving a view puts back whatever control it had taken over, or the sort
    // it chose would outlive the view that chose it.
    if (prev && prev !== quickView) {
      prev.split('&').forEach(function (pair) {
        var k = pair.split('=')[0];
        if (QV_VISIBLE[k] && inputs[k]) inputs[k].value = NEUTRAL[k] || '';
      });
    }
    if (quickView) {
      quickView.split('&').forEach(function (pair) {
        var kv = pair.split('=');
        if (QV_VISIBLE[kv[0]] && inputs[kv[0]]) {
          inputs[kv[0]].value = decodeURIComponent(kv[1] || '');
        }
      });
    }
  }

  quickButtons.forEach(function (b) {
    b.addEventListener('click', function () {
      setQuickView(b.dataset.qv);
      refresh();
    });
  });
  if (quickButtons.length) quickButtons[0].classList.add('is-on');

  // --- What is currently being filtered ------------------------------------
  var activeBar = document.getElementById('tit-active');
  var activeChips = document.getElementById('tit-active-chips');
  var resetBtn = document.getElementById('tit-reset');

  // $value is passed only for the multi-value filters, so a chip removes the
  // ONE choice it names rather than the whole filter. Picking three industries
  // and being able to drop only all three would make multi-select pointless.
  // THE SPACES BETWEEN THESE SPANS ARE LOAD-BEARING AND COST NOTHING.
  // .tit-chip is an inline-flex with a gap, so a whitespace-only text node
  // between two flex items is dropped by layout and changes no pixel. It
  // changes the three things layout cannot reach: the accessible name a screen
  // reader announces, the text a reader copies, and what an answer engine
  // scrapes. Without them this chip is "IndustryTechnology x, remove this
  // filter". Same defect the quick views had with "Moves Headcount(1,869)";
  // see the note beside .tit-qv-n in dashboard.css.
  function chip(key, text, value) {
    return '<button type="button" class="tit-chip" data-clear="' + esc(key) + '"' +
      (value == null ? '' : ' data-value="' + esc(value) + '"') + '>' +
      '<span class="tit-chip-k">' + esc(FILTER_LABEL[key] || key) + '</span> ' +
      '<span class="tit-chip-v">' + esc(text) + '</span> ' +
      '<span class="tit-chip-x" aria-hidden="true">&#215;</span>' +
      '<span class="tit-sr">, remove this filter</span></button>';
  }

  function paintActive() {
    if (!activeBar || !activeChips) return;
    var chips = [];

    if (region) {
      var tab = quickFind(tabs, function (t) { return (t.dataset.codes || '') === region; });
      var name = tab && tab.querySelector('.tit-region-name');
      chips.push(chip('region', name ? name.textContent : region));
    }
    if (stated) {
      /* The SAME words as the checkbox. This read "Only with a stated
         headcount", which is a claim about the headcount column: that column is
         non-null on 11 of 15,711 current rows, and this control does not read it
         at all -- it reads signal_direction. One control had three names (the
         checkbox, this chip, and the SQL), and only the checkbox was right. */
      chips.push(chip('stated_headcount', 'Only Updates That Move Headcount'));
    }
    if (quickView) {
      // A view with no button of its own still has to appear here. funding=1
      // is applied by the matrix's funding and money cells and no longer has a
      // chip in the strip, and a filter narrowing the page while nothing on
      // screen names it is exactly what this bar exists to prevent.
      var qb = quickFind(quickButtons, function (b) { return (b.dataset.qv || '') === quickView; });
      chips.push(chip('quickview', qb ? qb.textContent.trim()
                                      : (QV_LABEL[quickView] || quickView)));
    }
    Object.keys(inputs).forEach(function (key) {
      var el = inputs[key];
      // `detail` gets no chip: the bar above the table states it in full, with
      // both counts and what routine means. A chip would be a quieter copy of
      // something already impossible to miss.
      if (!el || key === 'sort' || key === 'detail') return;
      if (MULTI[key]) {
        multiValues(el).forEach(function (v) {
          chips.push(chip(key, optionText(el, v), v));
        });
        return;
      }
      var value = (el.value || '').trim();
      if (!value || value === NEUTRAL[key]) return;
      // The country select is slaved to the region strip; showing both would
      // be two chips for one narrowing.
      if (key === 'country' && region) return;
      var text = el.tagName === 'SELECT'
        ? ((el.options[el.selectedIndex] || {}).text || value)
        : value;
      chips.push(chip(key, text));
    });

    activeChips.innerHTML = chips.join('');
    syncAllPills();
    activeBar.hidden = chips.length === 0;

    // The phone jump bar's Filters button carries the same count the chips
    // bar shows, so a reader scrolled past the chips still knows the page is
    // narrowed. Looked up per paint: the bar is built later in this file.
    var jumpN = document.getElementById('tit-jump-n');
    if (jumpN) {
      jumpN.textContent = String(chips.length);
      jumpN.hidden = chips.length === 0;
    }
    // The collapsed bar's own count. On a phone the controls are behind this
    // button, so without it a reader who collapsed the bar has no reading of
    // how narrowed the page is except the chips, which scroll away.
    var barN = document.getElementById('tit-bar-n');
    if (barN) {
      barN.textContent = String(chips.length);
      barN.hidden = chips.length === 0;
    }
  }

  function quickFind(list, test) {
    for (var i = 0; i < list.length; i++) { if (test(list[i])) return list[i]; }
    return null;
  }

  function clearOne(key, value) {
    if (key === 'region') setRegion(null);
    else if (key === 'quickview') setQuickView(null);
    else if (key === 'stated_headcount') setStated(false);
    else if (MULTI[key] && inputs[key]) {
      setMulti(inputs[key], multiValues(inputs[key]).filter(function (v) {
        return v !== value;
      }));
    } else if (inputs[key]) inputs[key].value = NEUTRAL[key] || '';
    refresh();
  }

  if (activeChips) {
    activeChips.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest ? e.target.closest('[data-clear]') : null;
      if (btn) clearOne(btn.getAttribute('data-clear'), btn.getAttribute('data-value'));
    });
  }

  // One reset, two doors: the button in the filter panel and the Clear
  // Filters button inside the empty state. They must do the identical thing,
  // so they are the same function rather than two loops that drift apart.
  function resetAll() {
    Object.keys(inputs).forEach(function (k) {
      if (!inputs[k] || k === 'sort') return;
      if (MULTI[k]) { setMulti(inputs[k], []); return; }
      inputs[k].value = NEUTRAL[k] || '';
    });
    setRegion(null);
    setQuickView(null);
    setStated(false);
    refresh();
  }

  if (resetBtn) {
    resetBtn.addEventListener('click', resetAll);
  }

  // The empty state's own way out. The button is re-created on every empty
  // render, so the listener lives on the tbody and finds it by class.
  if (tbody) {
    tbody.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest ? e.target.closest('.tit-empty-clear') : null;
      if (btn) resetAll();
    });
  }

  // --- The bar's phone form: one button, one sheet --------------------------
  //
  // Thirteen controls at a 150px floor is four wrapped rows on a 390px screen,
  // and a sticky four-row bar pins most of the viewport under chrome. So on a
  // phone the bar is its head, and the controls open beneath it in normal flow.
  //
  // The button is revealed HERE rather than shipped visible, and the collapsing
  // is a class this adds rather than a CSS default, so the served markup is a
  // fully open bar: a reader whose script never ran can never end up looking at
  // a "Filters" button that does not open anything. The stylesheet decides at
  // which width any of it applies, so this code runs identically on a desktop
  // and does nothing there.
  var filterBar = document.getElementById('tit-panel');
  var barToggle = document.getElementById('tit-bar-toggle');
  if (filterBar && barToggle) {
    barToggle.hidden = false;
    barToggle.addEventListener('click', function () {
      var open = filterBar.classList.toggle('is-open');
      barToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      // An open dropdown belongs to a bar that is about to close under it.
      if (!open) closeDrop(false);
    });
  }

  // --- The phone jump bar ----------------------------------------------------
  // With twelve chart cards sitting between the filters and the rows, a phone
  // reader is several screens from the controls by the time they reach the
  // updates, and further since 1.63.0 than before it. The design proposal
  // solves this with a bottom-sheet filter panel, which would mean a second
  // copy of every control; this is the same reach-it-with-a-thumb idea at
  // none of that cost: a fixed bar, phones only (the stylesheet decides where
  // it exists), that jumps to the filter block or the rows. Built here rather
  // than shipped in markup so a page without JavaScript never shows chrome
  // that does nothing. paintActive() keeps the count on the Filters button in
  // step with the chips it already paints.
  var jumpBar = document.createElement('div');
  jumpBar.className = 'tit-jump';
  jumpBar.innerHTML =
    '<button type="button" class="tit-jump-btn" data-jump="#tit-filter-sec">Filters' +
    '<span class="tit-jump-n" id="tit-jump-n" hidden></span></button>' +
    '<button type="button" class="tit-jump-btn" data-jump=".tit-detail">Updates</button>';
  jumpBar.addEventListener('click', function (e) {
    var btn = e.target && e.target.closest ? e.target.closest('[data-jump]') : null;
    if (!btn) return;
    var target = root.querySelector(btn.getAttribute('data-jump'));
    if (!target) return;
    /* Scrolling a reader to a collapsed bar and leaving them to find the button
       is two taps for one intention. Filters OPENS the bar as well as reaching
       it; Updates does not touch it. */
    if (btn.getAttribute('data-jump') === '#tit-filter-sec' && filterBar
        && barToggle && !filterBar.classList.contains('is-open')) {
      filterBar.classList.add('is-open');
      barToggle.setAttribute('aria-expanded', 'true');
    }
    var still = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    target.scrollIntoView({ behavior: still ? 'auto' : 'smooth', block: 'start' });
  });
  root.appendChild(jumpBar);

  // --- Per-chart controls ---------------------------------------------------
  // Every card gets its own expand, share and download, and each one acts on
  // that card alone. The buttons ship hidden from the shortcode and are
  // revealed here, so a reader without JavaScript is never offered a control
  // that does nothing.
  function flash(btn) {
    btn.classList.add('is-done');
    setTimeout(function () { btn.classList.remove('is-done'); }, 1200);
  }

  function copyText(text, done) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { done(); });
      return;
    }
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) { /* nothing else to try */ }
    document.body.removeChild(ta);
    done();
  }

  // The chart is a list of labelled bars, not a canvas, so the useful download
  // is the numbers rather than a picture of them. Read from the rendered rows
  // so an export can never disagree with what is on screen.
  function chartCsv(chart) {
    var rows = Array.prototype.slice.call(
      chart.querySelectorAll('.tit-rank-row, .tit-pillar'));
    // A money card downloads the exact dollar figure from data-v, never the
    // abbreviation on screen: "$1.2B" in a spreadsheet is a string nobody can
    // add up, which is the problem these views exist to solve.
    var money = chart.classList.contains('tit-chart-money');
    var out = [['label', money ? 'usd' : 'count']];
    rows.forEach(function (r) {
      var name = r.querySelector('.tit-rank-name, .tit-pillar-name');
      var n = r.querySelector('.tit-rank-n, .tit-pillar-n');
      if (!name || !n) return;
      out.push([name.textContent.trim(),
                money ? (r.getAttribute('data-v') || '') : n.textContent.trim()]);
    });
    return out.map(function (cells) {
      return cells.map(function (c) { return '"' + String(c).replace(/"/g, '""') + '"'; }).join(',');
    }).join('\n');
  }

  function download(name, text) {
    var blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  /*
    THE (i), AND WHY IT IS NOT A title= ATTRIBUTE.

    Every chart card's explanatory prose is in a panel of its own now: what it
    counts, what it cannot say, and which figure is based on how much. Nine
    cards each printing two to six lines of that was a page a reader scrolled
    past to reach the bars, and the three money cards printed one identical
    currency sentence three times.

    NONE OF IT BECAME UNREACHABLE, which is the part that matters, because the
    lines in there are the ones that stop each chart overclaiming. Four things
    hold that open:

      1. The panel ships OPEN from the server and the button ships hidden. This
         closes the panel and reveals the button, in that order. A reader whose
         script never ran gets every caveat as plain prose rather than a button
         that opens nothing.
      2. It is a real <button> with aria-expanded, so it is a keyboard control.
      3. The chart's own group carries aria-describedby pointing at the panel,
         so a screen reader reads the caveat as the chart's description whether
         the panel is open or shut. That is the whole reason it is not a title=
         attribute, which is reachable by neither a keyboard nor a screen
         reader and which this repo's card contract already forbids as the only
         home for anything a reader needs.
      4. The button carries no aria-label. Its name is the visually hidden text
         inside it, so the two cannot say different things.
  */
  function noteOf(chart) {
    var btn = chart.querySelector('.tit-chart-info');
    var id = btn && btn.getAttribute('aria-controls');
    // Looked up by id at call time, never cached: the trend card's panel is
    // replaced wholesale on every filter change.
    return id ? document.getElementById(id) : null;
  }

  function setNote(chart, open) {
    var btn = chart.querySelector('.tit-chart-info');
    var note = noteOf(chart);
    if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (note) note.hidden = !open;
  }

  // Re-assert every panel's state after a repaint has handed us a fresh one.
  function syncChartNotes() {
    Array.prototype.slice.call(root.querySelectorAll('.tit-chart')).forEach(function (chart) {
      var btn = chart.querySelector('.tit-chart-info');
      if (!btn || btn.hidden) return;
      setNote(chart, btn.getAttribute('aria-expanded') === 'true');
    });
  }

  /*
    EXPANDING IS PART OF THE VIEW, SO IT IS PART OF THE LINK.

    The address bar already carries every filter (see refresh()), and the share
    button already carried the card as a hash. What it could not carry was the
    card being OPEN, so a link sent to show somebody a chart landed them on the
    small version of it and left them to find the control.

    `card` is that state, and it is deliberately NOT one of the API parameters:
    shareQuery() builds what /query and /aggregate are asked, and expansion is
    a property of the page rather than of the query. So it is appended here, in
    one place, and every writer of the address bar goes through pageUrl().
  */
  // Read HERE, at wiring time, and not inside openCardFromUrl() below. The
  // first refresh() rewrites the address bar from the filters alone, and this
  // is what stops that rewrite dropping the very parameter the link was sent
  // to carry. A card id we do not have is cleared when it is looked up.
  var expandedCard = new URLSearchParams(location.search).get('card') || '';

  function pageUrl(cardId) {
    var qs = lastQuery;
    if (cardId) qs += (qs ? '&' : '') + 'card=' + encodeURIComponent(cardId);
    return location.pathname + (qs ? '?' + qs : '') + (cardId ? '#' + cardId : '');
  }

  function writeUrl() {
    try {
      // replaceState, not pushState: expanding a card is not a page a reader
      // wants the back button to walk through.
      history.replaceState(null, '', pageUrl(expandedCard) +
        (expandedCard ? '' : location.hash));
    } catch (e) { /* a URL we cannot write is not worth failing the render for */ }
  }

  function setExpanded(chart, on) {
    var expand = chart.querySelector('.tit-expand');
    var label = expand && expand.querySelector('.tit-expand-t');
    chart.classList.toggle('is-expanded', on);
    if (expand) {
      expand.setAttribute('aria-expanded', on ? 'true' : 'false');
      expand.title = on ? 'Collapse this chart' : 'Expand this chart';
    }
    if (label) label.textContent = on ? 'Collapse' : 'Expand';
    // One card at a time. Two expanded cards in a three-column grid is a
    // layout, not a view, and it could not be described by one `card` value.
    if (on) {
      Array.prototype.slice.call(root.querySelectorAll('.tit-chart.is-expanded'))
        .forEach(function (other) { if (other !== chart) setExpanded(other, false); });
    }
    expandedCard = on ? chart.id : (expandedCard === chart.id ? '' : expandedCard);
  }

  Array.prototype.slice.call(root.querySelectorAll('.tit-chart')).forEach(function (chart) {
    var expand = chart.querySelector('.tit-expand');
    var share = chart.querySelector('.tit-chart-share');
    var dl = chart.querySelector('.tit-chart-dl');
    var info = chart.querySelector('.tit-chart-info');

    if (info) {
      info.hidden = false;
      setNote(chart, false);
      info.addEventListener('click', function () {
        setNote(chart, info.getAttribute('aria-expanded') !== 'true');
      });
    }

    if (expand) {
      expand.hidden = false;
      expand.addEventListener('click', function () {
        setExpanded(chart, !chart.classList.contains('is-expanded'));
        writeUrl();
      });
    }

    if (share) {
      share.hidden = false;
      share.addEventListener('click', function () {
        // The filters live in the querystring, so the link reproduces the view
        // rather than just the page; the hash lands on this card; and `card`
        // carries it open when it is open. Read off the class rather than off
        // expandedCard, so the link describes THIS card whichever one is open.
        var open = chart.classList.contains('is-expanded');
        var url = location.origin +
          (open ? pageUrl(chart.id)
                : location.pathname + (lastQuery ? '?' + lastQuery : '') +
                  (chart.id ? '#' + chart.id : ''));
        copyText(url, function () { flash(share); });
      });
    }

    // The trend keeps its download HIDDEN, and that is the honest answer
    // rather than an omission. chartCsv() reads the rendered bar rows, and the
    // trend has none: it would hand over a file containing a header and
    // nothing else, which is worse than no button. The whole page's numbers,
    // including every row behind these lines, are the CSV and JSON under
    // Download This View.
    if (dl && !chart.classList.contains('tit-chart-trend')) {
      dl.hidden = false;
      dl.addEventListener('click', function () {
        download('talent-' + (dl.dataset.chart || 'chart') + '.csv', chartCsv(chart));
        flash(dl);
      });
    }
  });

  // A shared link that named a card opens it, and scrolls to it, once the
  // controls above are wired. Called after applyUrlState() so it sees the same
  // querystring the filters were restored from.
  function openCardFromUrl() {
    var want = expandedCard;
    if (!want) return;
    var chart = document.getElementById(want);
    // An id we do not have is dropped rather than guessed at, the same rule
    // applyUrlState() follows for every other parameter. Dropped from the
    // address bar too, so a stale link does not keep rewriting itself.
    if (!chart || !chart.classList.contains('tit-chart')) {
      expandedCard = '';
      writeUrl();
      return;
    }
    setExpanded(chart, true);
    var still = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    chart.scrollIntoView({ behavior: still ? 'auto' : 'smooth', block: 'start' });
  }

  // --- Clickable chart rows -------------------------------------------------
  // Each bar row is a filter. A click routes through the SAME state the
  // dropdowns and quick views use (the pillar/country/direction inputs), so
  // refresh() carries it into the chips bar, the table, the other charts, the
  // address bar and the export links in one pass. Clicking the row again
  // clears it. Delegated to the card, so the server-rendered rows and every
  // JS re-render are wired identically.
  var CHART_FILTER = [
    { el: document.getElementById('chart-kind'), key: 'pillar' },
    { el: document.getElementById('chart-place'), key: 'country' },
    { el: document.getElementById('chart-direction'), key: 'direction' },
    // The money cards are filters like any other chart: same wiring, so a
    // click on "United States" in a dollar ranking narrows the table, the
    // chips bar, the address bar and the export links in one pass.
    { el: document.getElementById('chart-money-country'), key: 'country' },
    { el: document.getElementById('chart-money-city'), key: 'city' },
    { el: document.getElementById('chart-money-industry'), key: 'industry',
      label: function (k) { return INDUSTRY_LABEL[k] || k; } },
    // The two cards that took the grid to nine. Same wiring as the rest, so a
    // click on "Official Filing" narrows the table, the chips bar, the address
    // bar, the other eight charts and both export links in one pass.
    { el: document.getElementById('chart-confidence'), key: 'confidence',
      label: function (k) { return CONFIDENCE_LABEL[k] || k; } },
    { el: document.getElementById('chart-industry'), key: 'industry',
      label: function (k) { return INDUSTRY_LABEL[k] || k; } }
  ];

  function syncChartStates() {
    CHART_FILTER.forEach(function (cf) {
      if (!cf.el) return;
      // A selected region overrides the country select in refresh(), so while
      // a region is on, no single country row is what is filtering the page.
      var current = cf.key === 'country'
        ? (region ? '' : (inputs.country ? inputs.country.value : ''))
        : (inputs[cf.key] ? inputs[cf.key].value : '');
      Array.prototype.forEach.call(cf.el.querySelectorAll('[data-k]'), function (b) {
        var on = current !== '' && b.getAttribute('data-k') === current;
        b.classList.toggle('is-on', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    });
    syncGlance();
    syncDated();
  }

  // /facets lists values from the location columns only, so a place that only
  // ever appears as an employer's head office can show up in a chart with no
  // option to select. Add it rather than letting the click silently do nothing.
  function ensureOption(select, value, text) {
    if (!select || value === '' || value == null) return;
    var has = Array.prototype.some.call(select.options, function (o) { return o.value === value; });
    if (has) return;
    var opt = document.createElement('option');
    opt.value = value;
    opt.textContent = text || value;
    select.appendChild(opt);
  }

  CHART_FILTER.forEach(function (cf) {
    if (!cf.el) return;
    cf.el.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest ? e.target.closest('[data-k]') : null;
      if (!btn || !cf.el.contains(btn)) return;
      var k = btn.getAttribute('data-k');
      if (cf.key === 'country') {
        var was = !region && inputs.country && inputs.country.value === k;
        setRegion(null); // the region strip overrides the country parameter
        ensureOption(inputs.country, k, countryLabel(k));
        if (inputs.country) inputs.country.value = was ? '' : k;
      } else if (inputs[cf.key]) {
        ensureOption(inputs[cf.key], k, cf.label ? cf.label(k) : k);
        inputs[cf.key].value = inputs[cf.key].value === k ? '' : k;
      }
      refresh();
    });
  });

  // --- Clickable at-a-glance matrix cells -----------------------------------
  // A cell is that row's filter PLUS that column's period, routed through the
  // same inputs and quick-view state everything else uses; clicking the lit
  // cell clears both. The "All updates" row carries no row filter, so its
  // click means "everything in this period" and clears the other row filters.
  var glanceBox = root.querySelector('.tit-glance');

  function glanceFilterOn(f) {
    if (f === 'funding=1') return quickView === 'funding=1';
    if (f) {
      var kv = f.split('=');
      return !!(inputs[kv[0]] && inputs[kv[0]].value === kv[1]);
    }
    // Total row: on only when no matrix row filter is narrowing the page.
    return !(inputs.direction && inputs.direction.value) &&
           !(inputs.pillar && inputs.pillar.value) &&
           quickView !== 'funding=1';
  }

  function setGlanceFilter(f, on) {
    if (f === 'funding=1') { setQuickView(on ? 'funding=1' : null); return; }
    if (f) {
      var kv = f.split('=');
      if (inputs[kv[0]]) inputs[kv[0]].value = on ? kv[1] : '';
      return;
    }
    if (on) {
      // "All updates": the period is the whole filter.
      if (inputs.direction) inputs.direction.value = '';
      if (inputs.pillar) inputs.pillar.value = '';
      if (quickView === 'funding=1') setQuickView(null);
    }
  }

  function syncGlance() {
    if (!glanceBox) return;
    var since = inputs.since ? inputs.since.value : '';
    Array.prototype.forEach.call(glanceBox.querySelectorAll('.tit-cell'), function (b) {
      var on = since !== '' && b.getAttribute('data-since') === since &&
               glanceFilterOn(b.getAttribute('data-filter') || '');
      b.classList.toggle('is-on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  if (glanceBox) {
    glanceBox.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest ? e.target.closest('.tit-cell') : null;
      if (!btn) return;
      var f = btn.getAttribute('data-filter') || '';
      var wasOn = btn.classList.contains('is-on');
      setGlanceFilter(f, !wasOn);
      if (inputs.since) inputs.since.value = wasOn ? '' : (btn.getAttribute('data-since') || '');
      if (!wasOn && inputs.until) inputs.until.value = '';
      refresh();
    });
  }

  // --- The dated glance panel ----------------------------------------------
  // Its period labels carry the SAME data-since the matrix cells do, so one
  // rule drives both: clicking a period narrows the page to it, clicking the
  // lit one clears it. It sets no row filter, because a period row is the
  // period and nothing else.
  var dgBox = document.getElementById('tit-dg-box');

  function syncDated() {
    if (!dgBox) return;
    var since = inputs.since ? inputs.since.value : '';
    Array.prototype.forEach.call(dgBox.querySelectorAll('.tit-dg-label[data-since]'), function (b) {
      var on = since !== '' && b.getAttribute('data-since') === since;
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  if (dgBox) {
    dgBox.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest
        ? e.target.closest('.tit-dg-label[data-since]') : null;
      if (!btn) return;
      var wasOn = btn.getAttribute('aria-pressed') === 'true';
      if (inputs.since) inputs.since.value = wasOn ? '' : (btn.getAttribute('data-since') || '');
      if (!wasOn && inputs.until) inputs.until.value = '';
      refresh();
    });
  }

  /*
    COPY AS POST, BUILT FROM WHAT IS ON SCREEN.

    The rule this button has to satisfy is that it can never hand somebody a
    figure the page is not showing. The sibling's version is scoped only by its
    region tab and ignores the rest of its filter bar, so a reader looking at one
    country could copy a worldwide total; that is a quote-out-of-context bug with
    our own name on it.

    Two things make it honest here. It reads the RENDERED rows out of the DOM
    rather than rebuilding them from an aggregate, so whatever is copied is
    literally what is displayed. And it names the active filters, read from the
    chips bar the page already maintains, so the numbers arrive with the view
    they describe attached rather than as bare worldwide-looking totals. The
    panel itself repaints from /aggregate under those filters (see
    paintAggregate), so the two halves cannot drift.

    The button is rendered `hidden` and revealed here. Its entire function is
    navigator.clipboard, so with no JavaScript, or on a browser without the
    clipboard API, it would be a control that visibly does nothing — worse than
    no control at all.
  */
  var canCopy = !!(navigator.clipboard && navigator.clipboard.writeText);

  // Revealed after every paint, not once: the panel replaces its own innerHTML
  // on each filter change, so the button bound at startup is a node that no
  // longer exists by the second repaint. Delegation on the box handles the
  // click; this handles the reveal.
  function showDatedCopy() {
    if (!dgBox || !canCopy) return;
    var b = dgBox.querySelector('.tit-dg-copy');
    if (b) b.hidden = false;
  }
  showDatedCopy();

  if (dgBox && canCopy) {
    dgBox.addEventListener('click', function (e) {
      var dgCopy = e.target && e.target.closest ? e.target.closest('.tit-dg-copy') : null;
      if (!dgCopy) return;
      var lines = [];
      var title = dgBox.querySelector('.tit-dg-title');
      if (title) lines.push(title.textContent.replace(/\s+/g, ' ').trim());

      Array.prototype.forEach.call(dgBox.querySelectorAll('.tit-dg-row'), function (row) {
        var label = row.querySelector('.tit-dg-label');
        var body = row.querySelector('.tit-dg-body');
        if (!label || !body) return;
        lines.push(label.textContent.trim() + ': ' +
                   body.textContent.replace(/\s+/g, ' ').trim());
      });

      // The all-time rung lives on .tit-hero-fine, outside the panel box, so it
      // is collected by name rather than by position.
      var allFig = root.querySelector('.tit-hero-fine .tit-fine-figures');
      if (allFig) {
        lines.push('Everything we hold: ' +
                   allFig.textContent.replace(/\s+/g, ' ').trim());
      }

      /*
        THE VIEW THESE FIGURES DESCRIBE, from the chips the page is already
        showing. Without this a filtered copy reads as a worldwide one. When
        nothing is filtered it says so explicitly rather than staying silent,
        because "no filters" and "filters I forgot to mention" look identical in
        a pasted block of text.
      */
      var chipEls = activeChips ? activeChips.querySelectorAll('.tit-chip') : [];
      var applied = Array.prototype.map.call(chipEls, function (c) {
        return c.textContent.replace(/\s*×\s*$/, '').replace(/\s+/g, ' ').trim();
      }).filter(Boolean);
      lines.push(applied.length
        ? 'View: ' + applied.join('; ')
        : 'View: everything we hold, unfiltered.');

      lines.push('No figure appears unless its source states it. ' +
                 'Talent Intelligence Tracker: ' + location.origin + location.pathname +
                 (location.search || ''));

      navigator.clipboard.writeText(lines.join('\n')).then(function () {
        var was = dgCopy.textContent;
        dgCopy.textContent = 'Copied';
        setTimeout(function () { dgCopy.textContent = was; }, 1500);
      });
    });
  }

  /*
    --- "Why you can trust this" / Questions, as real tabs -------------------

    THE PANELS ARE ALREADY ON THE PAGE. Both of them, in full, rendered by the
    server. This function does not fetch, build or inject a single word: it puts
    `is-tabbed` on the container and sets `hidden` on the panel that is not
    selected. That ordering is the point — an FAQ that arrives on click is an
    FAQ a crawler never sees, and it is one of the most valuable blocks on the
    page for search. Before this runs, and if it never runs, a reader gets both
    panels stacked under their own headings.

    Full tab semantics, because half of them is worse than none: roving
    tabindex so the strip is one stop rather than two, Left/Right to move
    between tabs, Home/End to jump, and the selected panel focusable so a
    reader who tabs out of the strip lands in the content it just revealed.
  */
  var trust = document.getElementById('tit-trust');
  if (trust) {
    var tabs2 = Array.prototype.slice.call(trust.querySelectorAll('[role="tab"]'));
    var panels = tabs2.map(function (t) {
      return document.getElementById(t.getAttribute('aria-controls'));
    });
    if (tabs2.length > 1 && panels.every(Boolean)) {
      var selectTab = function (i, focus) {
        tabs2.forEach(function (t, j) {
          var on = (i === j);
          t.setAttribute('aria-selected', on ? 'true' : 'false');
          t.tabIndex = on ? 0 : -1;
          panels[j].hidden = !on;
        });
        if (focus) tabs2[i].focus();
      };
      // The class first, so the stylesheet's hiding rules are in force before
      // anything is hidden. The other order paints one panel, hides it, and
      // then reveals the strip, which a reader sees as a flicker.
      trust.classList.add('is-tabbed');
      selectTab(0, false);

      tabs2.forEach(function (t, i) {
        t.addEventListener('click', function () { selectTab(i, false); });
        t.addEventListener('keydown', function (e) {
          var next = null;
          if (e.key === 'ArrowRight') next = (i + 1) % tabs2.length;
          else if (e.key === 'ArrowLeft') next = (i - 1 + tabs2.length) % tabs2.length;
          else if (e.key === 'Home') next = 0;
          else if (e.key === 'End') next = tabs2.length - 1;
          if (next === null) return;
          e.preventDefault();
          selectTab(next, true);
        });
      });
    }
  }

  // --- Sort orderings that no longer have a column header --------------------
  // Four sortable <th> buttons used to set the SAME `sort` parameter the select
  // uses. The results are cards now and have no column headers, so every one of
  // those orderings is a server-rendered <option> in that select instead: the
  // values are unchanged, so a link somebody saved from the old page still
  // lands on the ordering it names.
  //
  // This map stays because applyUrlState() still needs it. A shared link can
  // carry a sort value that arrived before its option did, and dropping the
  // value because the option does not exist YET is what once made every shared
  // link to a single country come back as the whole world.
  var SORT_OPTION_LABEL = {
    employer_desc: 'Employer Z to A',
    place: 'By Place', place_desc: 'By Place, Reversed',
    evidence: 'Strongest Evidence First', evidence_desc: 'Weakest Evidence First'
  };

  syncAllPills();
  collapseMatrixNoteOnPhone();
  populateFacets();

  // Last, because it needs the inputs, the region tabs and refresh() to exist.
  // Only fetches when the link actually carried a view; the plain page is
  // already rendered by the server.
  applyUrlState();
  syncLooking();
  syncPlace();
  syncCountryButtons();
    syncCityButtons();
  syncBasis();
  if (location.search) refresh();
  // AFTER refresh(), because refresh() rewrites the address bar from the
  // filters alone and would drop the `card` this is about to read if it ran
  // first. It sets expandedCard, so every later write keeps it.
  openCardFromUrl();
})();
