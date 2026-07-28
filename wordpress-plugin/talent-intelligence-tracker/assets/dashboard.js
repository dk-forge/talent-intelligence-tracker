/* Filters talk to talent/v1/query. The table is already server-rendered, so
   this only ever replaces rows that are already there. If the API is
   unreachable the page keeps working with what the server sent. */

(function () {
  'use strict';

  var root = document.getElementById('tit-dashboard');
  if (!root || typeof TIT === 'undefined') return;

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
    confidence: document.getElementById('tit-f-confidence'),
    country_basis: document.getElementById('tit-f-country_basis'),
    company: document.getElementById('tit-f-company'),
    q: document.getElementById('tit-f-q'),
    since: document.getElementById('tit-f-since'),
    until: document.getElementById('tit-f-until'),
    sort: document.getElementById('tit-f-sort')
  };

  // Values that narrow nothing. They are never sent, never become a chip, and
  // never make the page count as filtered. Without this, country_basis="any"
  // and sort="newest" would each show as an active filter on a page nobody had
  // touched, which is the fastest way to make a filter bar mean nothing.
  var NEUTRAL = { sort: 'newest', country_basis: 'any' };

  // How each filter names itself in the active-filter bar.
  var FILTER_LABEL = {
    pillar: 'Kind', direction: 'Direction', 'function': 'Roles',
    industry: 'Industry', country: 'Country', state: 'US state',
    confidence: 'How solid', country_basis: 'Place basis', company: 'Employer',
    q: 'Search', since: 'From', until: 'To', region: 'Region', quickview: 'View'
  };

  var DIRECTION_CLASS = {
    hiring: 'tit-hiring',
    displacement: 'tit-displacement',
    comp_shift: 'tit-comp_shift'
  };

  // Recruiter language. Colour never carries the meaning on its own, so the
  // words have to be right.
  var DIRECTION_LABEL = {
    hiring: 'Hiring up',
    displacement: 'Cutting back',
    comp_shift: 'Pay change',
    neutral: 'Other change'
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
        fill(inputs.country, data.countries);
        fill(inputs.state, data.states);
      })
      .catch(function () { /* filters degrade to what the server rendered */ });
  }

  function fill(select, values) {
    if (!select || !values) return;
    values.forEach(function (v) {
      var opt = document.createElement('option');
      opt.value = v;
      opt.textContent = v;
      select.appendChild(opt);
    });
  }

  function renderRow(r) {
    // Fall back to headquarters when the source named no place, and say so.
    var isHq = !r.city && !r.country;
    var place = r.city || r.hq_city || '';
    var code = r.country || r.hq_country || '';
    var country = (TIT.countries && TIT.countries[code]) || code;
    var where = esc([place, country].filter(Boolean).join(', '));
    if (!where) {
      where = '<span class="tit-nowhere">Location not stated</span>';
    } else if (isHq) {
      where += ' <span class="tit-hq" title="Employer headquarters, not a location named in the source">HQ</span>';
    }

    // data-label mirrors the header text. Below the table breakpoint each row
    // becomes a card and the labels are the only thing naming the fields.
    return '<tr>' +
      '<td data-label="Employer">' + esc(r.company) + '</td>' +
      '<td class="tit-headline" data-label="What happened"><span class="tit-h">' + esc(r.headline) + '</span>' +
      '<span class="tit-rt">' + esc(r.talent_readthrough) + '</span></td>' +
      '<td data-label="Where">' + where + '</td>' +
      '<td data-label="What it means"><span class="tit-tag ' + (DIRECTION_CLASS[r.signal_direction] || '') + '">' +
        esc(DIRECTION_LABEL[r.signal_direction] || r.signal_direction) + '</span></td>' +
      '<td data-label="How solid"><span class="tit-conf tit-c-' + esc(r.confidence) + '">' + esc(r.confidence) + '</span></td>' +
      '<td data-label="Source"><a href="' + esc(r.source_url) + '" rel="nofollow noopener" target="_blank">' +
        esc(r.source_name) + '</a></td>' +
      '</tr>';
  }

  // --- The rest of the page follows the filters -----------------------------
  // Until now only the table re-rendered, so the hero said "13 updates · 3
  // countries" and the charts drew the whole world while the rows underneath
  // showed one region. The page implied the filter applied to everything, and
  // that implication is exactly what a dashboard must not get wrong.

  var PILLAR_LABEL = {
    company_development: 'Growing and expanding',
    leadership_change: 'Leadership moves',
    rewards_comp: 'Pay and benefits',
    how_we_work: 'Ways of working'
  };

  function nfmt(n) { return Number(n || 0).toLocaleString(); }

  function paintRank(chart, rows, label, dirKey) {
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
    wrap.innerHTML = rows.map(function (r) {
      var pct = Math.max(4, Math.round(100 * r.n / max));
      return '<div class="tit-rank-row"' + (dirKey ? ' data-dir="' + esc(r.k) + '"' : '') + '>' +
        '<span class="tit-rank-name">' + esc(label(r.k)) + '</span>' +
        '<span class="tit-rank-track"><span class="tit-rank-fill" style="width:' + pct + '%"></span></span>' +
        '<span class="tit-rank-n">' + nfmt(r.n) + '</span></div>';
    }).join('');
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
      lead.textContent = nfmt(total) + ' updates · ' + nfmt(data.companies) +
        ' employers · ' + nfmt(data.countries) + ' countries · ' +
        nfmt(data.verified) + ' from official filings. ';
    }

    var charts = root.querySelectorAll('.tit-charts .tit-chart');
    // Pillars keep their own markup (a bar per pillar), so they are painted
    // separately from the two rank charts.
    var pillars = root.querySelector('.tit-pillars');
    if (pillars) {
      var rows = data.by_pillar || [];
      pillars.innerHTML = rows.length ? rows.map(function (r) {
        var pct = total ? Math.round(100 * r.n / total) : 0;
        return '<div class="tit-pillar"><div class="tit-pillar-head">' +
          '<span class="tit-pillar-name">' + esc(PILLAR_LABEL[r.k] || r.k) + '</span>' +
          '<span class="tit-pillar-n">' + nfmt(r.n) + '</span></div>' +
          '<div class="tit-bar"><span style="width:' + pct + '%"></span></div></div>';
      }).join('') : '<p class="tit-rank-empty">Nothing in this view.</p>';
    }
    paintRank(charts[1], data.by_country || [], function (k) {
      return (TIT.countries && TIT.countries[k]) || k;
    });
    paintRank(charts[2], data.by_direction || [], function (k) {
      return DIRECTION_LABEL[k] || k;
    }, true);
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

  function refresh() {
    var params = new URLSearchParams();
    Object.keys(inputs).forEach(function (key) {
      var value = inputs[key] && inputs[key].value.trim();
      if (value && value !== NEUTRAL[key]) params.set(key, value);
    });
    // A region is a list of country codes, so it takes the same parameter as the
    // country select. Whichever the person touched last is the one that counts —
    // silently ANDing "Europe" with "Japan" would return nothing and look broken.
    if (region) params.set('country', region);
    // A quick view is a saved set of parameters, applied on top of whatever
    // else is selected rather than replacing it: someone who has picked Europe
    // and then clicks "Raised money" means both.
    if (quickView) {
      quickView.split('&').forEach(function (pair) {
        var kv = pair.split('=');
        // since/until are already in the date inputs (setQuickView put them
        // there), so re-applying them here would let a quick view move the
        // window while the From box still showed the reader's own date.
        if (kv[0] && kv[0] !== 'since' && kv[0] !== 'until') {
          params.set(kv[0], decodeURIComponent(kv[1] || ''));
        }
      });
    }
    params.set('per_page', '50');

    paintActive();

    // Charts and figures move with the same querystring the table uses, so the
    // two can never disagree about what is being shown.
    refreshAggregate(params);

    if (pending) pending.abort();
    pending = new AbortController();

    fetch(TIT.api + 'query?' + params.toString(), { signal: pending.signal })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        tbody.innerHTML = data.rows.length
          ? data.rows.map(renderRow).join('')
          : '<tr><td colspan="6">Nothing matches those filters yet.</td></tr>';
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
      if (inputs.country) inputs.country.value = '';
      refresh();
    });
  });
  setRegion(null);

  var quickView = null;
  var quickButtons = Array.prototype.slice.call(root.querySelectorAll('.tit-qv'));

  function setQuickView(v) {
    quickView = v || null;
    quickButtons.forEach(function (o) {
      var on = (o.dataset.qv || '') === (quickView || '');
      o.classList.toggle('is-on', on);
      o.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    // A quick view carrying a date writes it into the date inputs rather than
    // straight into the querystring. "This week" has to move the box a reader
    // can see, or the page would be filtering on a window nothing on screen
    // reports.
    if (quickView) {
      quickView.split('&').forEach(function (pair) {
        var kv = pair.split('=');
        if ((kv[0] === 'since' || kv[0] === 'until') && inputs[kv[0]]) {
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

  function chip(key, text) {
    return '<button type="button" class="tit-chip" data-clear="' + esc(key) + '">' +
      '<span class="tit-chip-k">' + esc(FILTER_LABEL[key] || key) + '</span>' +
      '<span class="tit-chip-v">' + esc(text) + '</span>' +
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
    if (quickView) {
      var qb = quickFind(quickButtons, function (b) { return (b.dataset.qv || '') === quickView; });
      if (qb) chips.push(chip('quickview', qb.textContent.trim()));
    }
    Object.keys(inputs).forEach(function (key) {
      var el = inputs[key];
      if (!el || key === 'sort') return;
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
    activeBar.hidden = chips.length === 0;
  }

  function quickFind(list, test) {
    for (var i = 0; i < list.length; i++) { if (test(list[i])) return list[i]; }
    return null;
  }

  function clearOne(key) {
    if (key === 'region') setRegion(null);
    else if (key === 'quickview') setQuickView(null);
    else if (inputs[key]) inputs[key].value = NEUTRAL[key] || '';
    refresh();
  }

  if (activeChips) {
    activeChips.addEventListener('click', function (e) {
      var btn = e.target && e.target.closest ? e.target.closest('[data-clear]') : null;
      if (btn) clearOne(btn.getAttribute('data-clear'));
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener('click', function () {
      Object.keys(inputs).forEach(function (k) {
        if (!inputs[k] || k === 'sort') return;
        inputs[k].value = NEUTRAL[k] || '';
      });
      setRegion(null);
      setQuickView(null);
      refresh();
    });
  }

  // --- Charts expand in place ----------------------------------------------
  // The button is rendered hidden by the shortcode and revealed here, so a
  // reader without JavaScript is never offered a control that does nothing.
  Array.prototype.slice.call(root.querySelectorAll('.tit-chart')).forEach(function (chart) {
    var btn = chart.querySelector('.tit-expand');
    if (!btn) return;
    btn.hidden = false;
    var label = btn.querySelector('.tit-expand-t');
    btn.addEventListener('click', function () {
      var on = chart.classList.toggle('is-expanded');
      btn.setAttribute('aria-expanded', on ? 'true' : 'false');
      if (label) label.textContent = on ? 'Collapse' : 'Expand';
    });
  });

  populateFacets();
})();
