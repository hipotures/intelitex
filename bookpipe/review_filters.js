/* Pure review-scope functions, shared by the browser and Node regression tests. */
(function (root) {
  'use strict';
  const ranks = {high: 0, medium: 1, low: 2};
  const filterLabels = {all: 'All', unreviewed: 'Unreviewed', reviewed: 'Reviewed', uncertain: 'Uncertain', notes: 'Notes'};
  function confidence(t) {
    const values = [...(t.meaning_notes || []), ...(t.candidates || []), ...(t.observations || [])]
      .map(x => x.confidence).filter(x => Object.hasOwn(ranks, x));
    return values.sort((a, b) => ranks[b] - ranks[a])[0] || 'high';
  }
  function isUncertain(t) { return confidence(t) !== 'high'; }
  function hasNotes(t) { return Boolean(String(t.user_notes || '').trim()); }
  function selectedText(t) {
    if (String(t.custom || '').trim()) return t.custom.trim();
    return (t.candidates || []).find(c => c.number === t.select)?.text || '';
  }
  function haystack(t) {
    return [t.source, ...(t.aliases || []), selectedText(t), ...(t.candidates || []).map(c => c.text),
      ...(t.meaning_notes || []).map(n => n.text), ...(t.observations || []).map(o => o.statement), t.user_notes || '']
      .join(' ').toLocaleLowerCase();
  }
  function matchesStatus(t, status) {
    if (status === 'unreviewed') return !t.reviewed;
    if (status === 'reviewed') return Boolean(t.reviewed);
    if (status === 'uncertain') return isUncertain(t);
    if (status === 'notes') return hasNotes(t);
    return true;
  }
  function scope(terms, {category = 'all', status = 'all', search = ''} = {}) {
    const q = search.trim().toLocaleLowerCase();
    return terms.filter(t => (category === 'all' || (t.category || 'other') === category) &&
      matchesStatus(t, status) && (!q || haystack(t).includes(q)));
  }
  function stats(terms) {
    const reviewed = terms.filter(t => t.reviewed).length;
    return {total: terms.length, reviewed, remaining: terms.length - reviewed,
      complete: terms.length > 0 && reviewed === terms.length};
  }
  function categoryStats(terms, category, status, search) {
    const visible = stats(scope(terms, {category, status, search}));
    // An empty Unreviewed queue is complete only if its parent scope exists.
    // A genuinely empty category/search result is never painted green.
    const base = stats(scope(terms, {category, status: status === 'unreviewed' ? 'all' : status, search}));
    return {...visible, complete: status === 'unreviewed' ? base.complete : visible.complete, baseTotal: base.total};
  }
  function nextUnreviewed(terms, currentId, options = {}) {
    // Review navigation ignores the reviewed/unreviewed display toggle, but
    // preserves semantic filters, category and search.
    const status = ['reviewed', 'unreviewed'].includes(options.status) ? 'all' : (options.status || 'all');
    const items = scope(terms, {...options, status});
    const start = items.findIndex(t => t.id === currentId);
    for (let offset = 1; offset <= items.length; offset++) {
      const t = items[(start + offset) % items.length];
      if (!t.reviewed) return t.id;
    }
    return null;
  }
  const api = {filterLabels, confidence, isUncertain, hasNotes, selectedText, scope, stats, categoryStats, nextUnreviewed};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.ReviewFilters = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
