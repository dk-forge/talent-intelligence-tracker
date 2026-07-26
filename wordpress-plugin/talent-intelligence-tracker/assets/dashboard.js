/* Filters talk to talent/v1/query. The table is already server-rendered, so
   this only ever replaces rows that are already there. If the API is
   unreachable the page keeps working with what the server sent. */

(function () {
  'use strict';

  var root = document.getElementById('tit-dashboard');
  if (!root || typeof TIT === 'undefined') return;

  var tbody = document.getElementById('tit-rows');
  var inputs = {
    pillar: document.getElementById('tit-f-pillar'),
    direction: document.getElementById('tit-f-direction'),
    country: document.getElementById('tit-f-country'),
    company: document.getElementById('tit-f-company')
  };

  var DIRECTION_CLASS = {
    hiring: 'tit-hiring',
    displacement: 'tit-displacement',
    comp_shift: 'tit-comp_shift'
  };

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function populateCountries() {
    fetch(TIT.api + 'facets')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.countries) return;
        data.countries.forEach(function (code) {
          var opt = document.createElement('option');
          opt.value = code;
          opt.textContent = code;
          inputs.country.appendChild(opt);
        });
      })
      .catch(function () { /* filters degrade to what the server rendered */ });
  }

  function renderRow(r) {
    // Fall back to headquarters when the source named no place, and say so.
    var isHq = !r.city && !r.country;
    var place = r.city || r.hq_city || '';
    var code = r.country || r.hq_country || '';
    var where = esc([place, code].filter(Boolean).join(', '));
    if (isHq && where) {
      where += ' <span class="tit-hq" title="Employer headquarters, not a location named in the source">HQ</span>';
    }

    return '<tr>' +
      '<td class="tit-headline"><span class="tit-h">' + esc(r.headline) + '</span>' +
      '<span class="tit-rt">' + esc(r.talent_readthrough) + '</span></td>' +
      '<td>' + esc(r.company) + '</td>' +
      '<td>' + where + '</td>' +
      '<td><span class="tit-tag ' + (DIRECTION_CLASS[r.signal_direction] || '') + '">' +
        esc(String(r.signal_direction).replace(/_/g, ' ')) + '</span></td>' +
      '<td><span class="tit-conf tit-c-' + esc(r.confidence) + '">' + esc(r.confidence) + '</span></td>' +
      '<td><a href="' + esc(r.source_url) + '" rel="nofollow noopener" target="_blank">' +
        esc(r.source_name) + '</a></td>' +
      '</tr>';
  }

  var pending = null;

  function refresh() {
    var params = new URLSearchParams();
    Object.keys(inputs).forEach(function (key) {
      var value = inputs[key] && inputs[key].value.trim();
      if (value) params.set(key, value);
    });
    params.set('per_page', '50');

    if (pending) pending.abort();
    pending = new AbortController();

    fetch(TIT.api + 'query?' + params.toString(), { signal: pending.signal })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        tbody.innerHTML = data.rows.length
          ? data.rows.map(renderRow).join('')
          : '<tr><td colspan="6">No signals match those filters.</td></tr>';
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
    inputs[key].addEventListener(inputs[key].tagName === 'SELECT' ? 'change' : 'input', debounced);
  });

  populateCountries();
})();
