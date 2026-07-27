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
    company: document.getElementById('tit-f-company'),
    q: document.getElementById('tit-f-q')
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
    wrap.innerHTML = rows.slice(0, 6).map(function (r) {
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
      if (value) params.set(key, value);
    });
    // A region is a list of country codes, so it takes the same parameter as the
    // country select. Whichever the person touched last is the one that counts —
    // silently ANDing "Europe" with "Japan" would return nothing and look broken.
    if (region) params.set('country', region);
    params.set('per_page', '50');

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

  populateFacets();
})();
