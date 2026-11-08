(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.UCFilter = api;
  else if (root && typeof root === 'object') root.UCFilter = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var CITS = { 50: 50, 25: 75, 10: 90 };

  function isActive(f) {
    if (!f) return false;
    return !!((f.inst && f.inst.length) || f.country || f.venue || f.code || f.cit);
  }

  function buildMask(data, f, out) {
    if (!isActive(f)) return null;
    var flags = data.flags, cit = data.citpct;
    var n = flags.length;
    var mask = (out && out.length === n) ? out : new Uint8Array(n);
    var i, j, rows, len;

    // Stage 1: institutions (OR) or everything
    var hasInst = f.inst && f.inst.length > 0;
    if (hasInst) {
      mask.fill(0);
      var lists = data.instRows || [];
      for (j = 0; j < lists.length; j++) {
        rows = lists[j];
        len = rows.length;
        for (i = 0; i < len; i++) mask[rows[i]] = 1;
      }
    } else {
      mask.fill(1);
    }

    // Stage 2: country (AND) via sorted row list
    if (f.country) {
      var cr = data.countryRows;
      if (!cr) {
        mask.fill(0);
      } else {
        // zero everything except rows in cr: walk cr, keep marks
        var prev = 0;
        len = cr.length;
        for (i = 0; i < len; i++) {
          var r = cr[i];
          for (j = prev; j < r; j++) mask[j] = 0;
          prev = r + 1;
        }
        for (j = prev; j < n; j++) mask[j] = 0;
      }
    }

    // Stage 3: per-row flag/cit checks
    var wantVenue = !!f.venue, wantCode = !!f.code;
    var minCit = f.cit ? (CITS[f.cit] || 0) : -1;
    var wantCit = minCit >= 0;
    if (wantVenue || wantCode || wantCit) {
      for (i = 0; i < n; i++) {
        if (mask[i] === 0) continue;
        var fl = flags[i];
        if (wantVenue && (fl & 3) === 0) { mask[i] = 0; continue; }
        if (wantCode && (fl & 8) === 0) { mask[i] = 0; continue; }
        if (wantCit) {
          var c = cit[i];
          if (c === 255 || c < minCit) mask[i] = 0;
        }
      }
    }
    return mask;
  }

  function countByTopic(mask, labels, k) {
    var counts = new Uint32Array(k);
    var n = labels.length, i, l;
    if (mask === null || mask === undefined) {
      for (i = 0; i < n; i++) { l = labels[i]; if (l >= 0 && l < k) counts[l]++; }
    } else {
      for (i = 0; i < n; i++) {
        if (mask[i] === 0) continue;
        l = labels[i];
        if (l >= 0 && l < k) counts[l]++;
      }
    }
    return counts;
  }

  function toQuery(f) {
    var parts = [];
    if (f.inst && f.inst.length) parts.push('inst=' + f.inst.join(','));
    if (f.country) parts.push('country=' + f.country);
    if (f.venue) parts.push('venue=1');
    if (f.code) parts.push('code=1');
    if (f.cit) parts.push('cit=' + f.cit);
    return parts.join('&');
  }

  function fromQuery(s) {
    var f = { inst: [], country: null, venue: false, code: false, cit: null };
    var p = new URLSearchParams(s || '');
    var inst = p.get('inst');
    if (inst) {
      var ids = inst.split(',');
      var seen = {};
      for (var i = 0; i < ids.length; i++) {
        var id = ids[i];
        if (/^I\d+$/.test(id) && !seen[id] && f.inst.length < 5) {
          seen[id] = 1;
          f.inst.push(id);
        }
      }
    }
    var c = p.get('country');
    if (c && /^[A-Z]{2}$/.test(c)) f.country = c;
    if (p.get('venue') === '1') f.venue = true;
    if (p.get('code') === '1') f.code = true;
    var ct = p.get('cit');
    if (ct === '50' || ct === '25' || ct === '10') f.cit = Number(ct);
    return f;
  }

  return { buildMask: buildMask, countByTopic: countByTopic, isActive: isActive, toQuery: toQuery, fromQuery: fromQuery };
});
