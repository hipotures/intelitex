/* Reader range helpers plus the dependency-free browser application. */
(function (root) {
  'use strict';

  function utf16ToCodePoint(text, offset) {
    return Array.from(text.slice(0, Math.max(0, offset))).length;
  }

  function codePointToUtf16(text, offset) {
    return Array.from(text).slice(0, Math.max(0, offset)).join('').length;
  }

  function wordSpans(text, segmenter) {
    if (segmenter) {
      return Array.from(segmenter.segment(text))
        .filter(item => item.isWordLike)
        .map(item => ({
          start: utf16ToCodePoint(text, item.index),
          end: utf16ToCodePoint(text, item.index + item.segment.length),
        }));
    }
    const spans = [];
    const expression = /[\p{L}\p{N}\p{M}_]+(?:[’'-][\p{L}\p{N}\p{M}_]+)*/gu;
    for (const match of text.matchAll(expression)) {
      spans.push({
        start: utf16ToCodePoint(text, match.index),
        end: utf16ToCodePoint(text, match.index + match[0].length),
      });
    }
    return spans;
  }

  function wordPosition(spans, offset) {
    let low = 0, high = spans.length;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (spans[middle].start < offset) low = middle + 1;
      else high = middle;
    }
    return low;
  }

  function progressAtLocation(progress, chapterId, blockId, spans, offset) {
    const total = Math.max(0, Number(progress?.total_words) || 0);
    const chapter = progress?.chapters?.find(item => item.id === chapterId);
    const block = chapter?.blocks?.find(item => item.id === blockId);
    if (!block) return null;
    const inside = Math.min(Number(block.words) || 0, wordPosition(spans, offset));
    const read = Math.max(0, Math.min(total, (Number(block.start) || 0) + inside));
    const ratio = total ? read / total : 0;
    return {read, total, remaining: total - read, ratio, percent: Math.round(ratio * 100)};
  }

  function viewportBlockPosition(rects, midpoint) {
    if (!rects.length) return null;
    for (let index = 0; index < rects.length; index += 1) {
      const rect = rects[index];
      if (midpoint < rect.top) return index ? {index: index - 1, edge: 'end'} : {index: 0, edge: 'start'};
      if (midpoint <= rect.bottom) return {index, edge: 'inside'};
    }
    return {index: rects.length - 1, edge: 'end'};
  }

  function snapWordRange(text, first, second, segmenter) {
    const length = Array.from(text).length;
    let start = Math.max(0, Math.min(length, Math.min(first, second)));
    let end = Math.max(0, Math.min(length, Math.max(first, second)));
    const spans = wordSpans(text, segmenter);
    if (!spans.length) return null;
    let selected = spans.filter(span => end === start
      ? span.start <= start && start <= span.end
      : span.end > start && span.start < end);
    if (!selected.length) {
      selected = [spans.reduce((best, span) => {
        const distance = start < span.start ? span.start - start : (start > span.end ? start - span.end : 0);
        return distance < best.distance ? {span, distance} : best;
      }, {span: spans[0], distance: Infinity}).span];
    }
    return {start: selected[0].start, end: selected[selected.length - 1].end};
  }

  function createSerialQueue() {
    let tail = Promise.resolve();
    return task => {
      const result = tail.then(task, task);
      tail = result.catch(() => {});
      return result;
    };
  }

  function createRequestGate() {
    let generation = 0;
    return {
      next() { generation += 1; return generation; },
      isCurrent(value) { return value === generation; },
    };
  }

  function markerLayout(entries, hitWidth = 28, hitHeight = 36) {
    const layout = Array(entries.length);
    const columnBottoms = [];
    entries.map((entry, index) => ({entry, index})).sort((left, right) =>
      left.entry.screenTop - right.entry.screenTop || left.index - right.index
    ).forEach(({entry, index}) => {
      let column = 0;
      while (columnBottoms[column] !== undefined && entry.screenTop < columnBottoms[column]) column += 1;
      columnBottoms[column] = entry.screenTop + hitHeight;
      layout[index] = {top: entry.base, shift: column * hitWidth};
    });
    return layout;
  }

  function inlineRuns(text, formatting = []) {
    const characters = Array.from(text);
    const runs = [];
    let offset = 0;
    for (const span of formatting) {
      if (!span || !['em', 'strong'].includes(span.style)
          || !Number.isInteger(span.start) || !Number.isInteger(span.end)
          || span.start < offset || span.end <= span.start || span.end > characters.length) {
        return [{text, style: null}];
      }
      if (span.start > offset) runs.push({text: characters.slice(offset, span.start).join(''), style: null});
      runs.push({text: characters.slice(span.start, span.end).join(''), style: span.style});
      offset = span.end;
    }
    if (offset < characters.length) runs.push({text: characters.slice(offset).join(''), style: null});
    return runs.length ? runs : [{text, style: null}];
  }

  function markerAnchor(text, marker) {
    const characters = Array.from(text);
    const current = marker.end <= characters.length
      && characters.slice(marker.start, marker.end).join('') === marker.text;
    const start = current
      ? marker.start : Math.max(0, Math.min(marker.start, Math.max(0, characters.length - 1)));
    const end = current ? marker.end : Math.min(characters.length, start + 1);
    return {current, start, end};
  }

  const helpers = {
    utf16ToCodePoint, codePointToUtf16, wordSpans, snapWordRange,
    wordPosition, progressAtLocation, viewportBlockPosition,
    createSerialQueue, createRequestGate, markerLayout, inlineRuns, markerAnchor,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = helpers;
  root.ReaderRanges = helpers;
  if (typeof document === 'undefined') return;

  const $ = id => document.getElementById(id);
  const DEFAULTS = {fontSize: 20, fontFamily: 'serif', lineHeight: 1.7, contentWidth: 42, theme: 'light', gesture: 'tap'};
  const FONT_STACKS = {
    serif: 'ui-serif, Charter, "Bitstream Charter", Georgia, serif',
    sans: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
    mono: 'ui-monospace, "Cascadia Mono", "Liberation Mono", monospace',
  };
  const SETTINGS_KEY = 'intelitex-reader-settings-v1';
  const LONG_PRESS_MS = 550;
  const MOVE_TOLERANCE = 11;
  const DRAG_THRESHOLD = 28;
  let metadata = null;
  let markerState = null;
  let chapterIndex = 0;
  let activeMarker = null;
  let pointer = null;
  let positionTimer = null;
  let noticeTimer = null;
  let progressFrame = null;
  let progressPopupTimer = null;
  let renderedChapterId = null;
  let fallbackHighlights = [];
  let progressWordSpans = new Map();
  let readingProgress = {read: 0, total: 0, remaining: 0, ratio: 0, percent: 0};
  let settings = loadSettings();
  const enqueueMarkerMutation = createSerialQueue();
  const chapterRequests = createRequestGate();
  const segmenter = typeof Intl !== 'undefined' && Intl.Segmenter
    ? new Intl.Segmenter('pl', {granularity: 'word'}) : null;

  function loadSettings() {
    try {
      const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}');
      return {...DEFAULTS, ...(saved && typeof saved === 'object' ? saved : {})};
    } catch (_) { return {...DEFAULTS}; }
  }

  function saveSettings() {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  }

  function applySettings() {
    const doc = document.documentElement;
    doc.dataset.theme = ['light', 'sepia', 'dark'].includes(settings.theme) ? settings.theme : 'light';
    doc.style.setProperty('--reader-size', `${Number(settings.fontSize)}px`);
    doc.style.setProperty('--reader-leading', String(Number(settings.lineHeight)));
    doc.style.setProperty('--reader-width', `${Number(settings.contentWidth)}rem`);
    doc.style.setProperty('--reader-font', FONT_STACKS[settings.fontFamily] || FONT_STACKS.serif);
    $('chapter').classList.toggle('gesture-drag', settings.gesture === 'drag');
    $('chapter').classList.toggle('gesture-long', settings.gesture === 'long');
    for (const key of Object.keys(DEFAULTS)) if ($(key)) $(key).value = String(settings[key]);
    requestAnimationFrame(() => { positionMarkers(); updateReadingProgress(); });
  }

  function showNotice(message, duration = 1800) {
    const notice = $('notice');
    notice.textContent = message;
    notice.classList.add('visible');
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => notice.classList.remove('visible'), duration);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: options.body ? {'Content-Type': 'application/json'} : {},
      ...options,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(result.error || `${response.status} ${response.statusText}`);
      error.status = response.status;
      throw error;
    }
    return result;
  }

  function currentChapter() { return metadata.chapters[chapterIndex]; }
  function blockElement(blockId) {
    return Array.from(document.querySelectorAll('.reader-block')).find(node => node.dataset.blockId === blockId) || null;
  }

  function blockPlainText(block) {
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return node.parentElement.closest('.gutter-marker') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      },
    });
    const chunks = [];
    let node;
    while ((node = walker.nextNode())) chunks.push(node.nodeValue);
    return chunks.join('');
  }

  function renderInline(block, text, formatting) {
    for (const run of inlineRuns(text, formatting)) {
      const node = run.style ? document.createElement(run.style) : document.createTextNode(run.text);
      if (run.style) {
        node.append(document.createTextNode(run.text));
      }
      block.append(node);
    }
  }

  function textRange(block, start, end) {
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return node.parentElement.closest('.gutter-marker') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      },
    });
    const range = document.createRange();
    let node;
    let traversed = 0;
    let startSet = false;
    const text = blockPlainText(block);
    const start16 = codePointToUtf16(text, start);
    const end16 = codePointToUtf16(text, end);
    while ((node = walker.nextNode())) {
      const next = traversed + node.nodeValue.length;
      if (!startSet && start16 <= next) {
        range.setStart(node, Math.max(0, start16 - traversed));
        startSet = true;
      }
      if (startSet && end16 <= next) {
        range.setEnd(node, Math.max(0, end16 - traversed));
        return range;
      }
      traversed = next;
    }
    return null;
  }

  function caretAtPoint(x, y) {
    if (document.caretPositionFromPoint) {
      const caret = document.caretPositionFromPoint(x, y);
      if (caret) return {node: caret.offsetNode, offset: caret.offset};
    }
    if (document.caretRangeFromPoint) {
      const range = document.caretRangeFromPoint(x, y);
      if (range) return {node: range.startContainer, offset: range.startOffset};
    }
    return null;
  }

  function locationAtPoint(x, y) {
    const caret = caretAtPoint(x, y);
    if (!caret) return null;
    const owner = caret.node.nodeType === Node.ELEMENT_NODE ? caret.node : caret.node.parentElement;
    const block = owner?.closest?.('.reader-block');
    if (!block || !blockElement(block.dataset.blockId)) return null;
    try {
      const prefix = document.createRange();
      prefix.selectNodeContents(block);
      prefix.setEnd(caret.node, caret.offset);
      const offset = utf16ToCodePoint(blockPlainText(block), prefix.toString().length);
      return {block, offset};
    } catch (_) { return null; }
  }

  function viewportLocation() {
    const blocks = Array.from(document.querySelectorAll('.reader-block'));
    const rects = blocks.map(block => block.getBoundingClientRect());
    const midpoint = window.innerHeight / 2;
    const target = viewportBlockPosition(rects, midpoint);
    if (!target) return null;
    const block = blocks[target.index];
    const text = blockPlainText(block);
    if (target.edge === 'start') return {block, offset: 0};
    if (target.edge === 'end') return {block, offset: Array.from(text).length};
    const rect = rects[target.index];
    const candidates = [rect.left + rect.width / 2, rect.left + 4, rect.right - 4];
    for (const candidate of candidates) {
      const x = Math.max(1, Math.min(window.innerWidth - 1, candidate));
      const location = locationAtPoint(x, midpoint);
      if (location?.block === block) return location;
    }
    const fraction = rect.height ? Math.max(0, Math.min(1, (midpoint - rect.top) / rect.height)) : 0;
    return {block, offset: Math.round(Array.from(text).length * fraction)};
  }

  function chapterBoundaryProgress() {
    const progress = metadata?.progress;
    const total = Math.max(0, Number(progress?.total_words) || 0);
    const chapter = progress?.chapters?.find(item => item.id === renderedChapterId);
    const lastId = progress?.last_chapter?.id;
    const lastIndex = metadata?.chapters?.findIndex(item => item.id === lastId) ?? -1;
    const read = chapter ? Number(chapter.start) || 0 : (chapterIndex > lastIndex ? total : 0);
    const ratio = total ? read / total : 0;
    return {read, total, remaining: total - read, ratio, percent: Math.round(ratio * 100)};
  }

  function renderProgressPopup() {
    $('progressPercent').textContent = `${readingProgress.percent}% read`;
    $('progressCounts').textContent = `${readingProgress.read.toLocaleString()} / ${readingProgress.total.toLocaleString()} words · ${readingProgress.remaining.toLocaleString()} remaining`;
    const title = metadata?.progress?.last_chapter?.title;
    $('progressAvailable').textContent = title ? `Available through ${title}` : 'No translated text is currently available';
  }

  function updateReadingProgress() {
    if (!metadata?.progress) return;
    const location = viewportLocation();
    const spans = location && progressWordSpans.get(location.block.dataset.blockId);
    readingProgress = location && spans
      ? progressAtLocation(metadata.progress, renderedChapterId, location.block.dataset.blockId, spans, location.offset)
      : null;
    if (!readingProgress) readingProgress = chapterBoundaryProgress();
    $('readingProgressFill').style.width = `${readingProgress.ratio * 100}%`;
    $('readingProgress').setAttribute('aria-label', `Show reading progress, ${readingProgress.percent}% read`);
    if (!$('progressPopup').hidden) renderProgressPopup();
  }

  function scheduleReadingProgress() {
    if (progressFrame !== null) return;
    progressFrame = requestAnimationFrame(() => {
      progressFrame = null;
      updateReadingProgress();
    });
  }

  function hideProgressPopup() {
    clearTimeout(progressPopupTimer);
    $('progressPopup').hidden = true;
    $('readingProgress').setAttribute('aria-expanded', 'false');
  }

  function showProgressPopup(event) {
    event.stopPropagation();
    $('tocPanel').hidden = true;
    $('settingsPanel').hidden = true;
    updateReadingProgress();
    renderProgressPopup();
    $('progressPopup').hidden = false;
    $('readingProgress').setAttribute('aria-expanded', 'true');
    clearTimeout(progressPopupTimer);
    progressPopupTimer = setTimeout(hideProgressPopup, 3600);
  }

  function normalizedLocation(first, second = first) {
    if (!first || !second || first.block !== second.block) return null;
    const text = blockPlainText(first.block);
    const snapped = snapWordRange(text, first.offset, second.offset, segmenter);
    return snapped ? {block: first.block, text, ...snapped} : null;
  }

  function flashRange(range) {
    for (const rect of range.getClientRects()) {
      if (!rect.width || !rect.height) continue;
      const flash = document.createElement('span');
      flash.className = 'range-flash';
      Object.assign(flash.style, {left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px`});
      document.body.append(flash);
      setTimeout(() => flash.remove(), 1050);
    }
  }

  function createMarker(location) {
    if (!location) return;
    const range = textRange(location.block, location.start, location.end);
    const payload = {
      chapter_id: renderedChapterId,
      block_id: location.block.dataset.blockId,
      start: location.start,
      end: location.end,
      text: Array.from(location.text).slice(location.start, location.end).join(''),
    };
    if (!payload.chapter_id) return;
    return enqueueMarkerMutation(async () => {
      try {
        const body = {...payload, revision: markerState._revision};
        const result = await api('/api/markers', {method: 'POST', body: JSON.stringify(body)});
        markerState._revision = result.revision;
        if (!markerState.markers.some(marker => marker.id === result.marker.id)) markerState.markers.push(result.marker);
        if (range && location.block.isConnected && renderedChapterId === payload.chapter_id) flashRange(range);
        renderMarkers();
      } catch (error) {
        showNotice(error.message, 3500);
        if (error.status === 409) await reloadMarkerState();
      }
    });
  }

  function markerForButton(button) {
    return markerState.markers.find(marker => marker.id === button.dataset.markerId);
  }

  function markerLocation(marker) {
    const block = blockElement(marker.block_id);
    if (!block) return null;
    const text = blockPlainText(block);
    const {current, start, end} = markerAnchor(text, marker);
    return {block, range: end > start ? textRange(block, start, end) : null, current};
  }

  function positionMarkers() {
    const entries = [];
    const buttons = [];
    document.querySelectorAll('.gutter-marker').forEach(button => {
      const marker = markerForButton(button);
      const location = marker && markerLocation(marker);
      if (!location) return;
      const rect = location.range && (location.range.getClientRects()[0] || location.range.getBoundingClientRect());
      const blockRect = location.block.getBoundingClientRect();
      const top = rect ? rect.top : blockRect.top;
      const base = Math.max(0, top - blockRect.top + 3);
      entries.push({base, screenTop: blockRect.top + base});
      buttons.push(button);
    });
    const hitWidth = Math.max(28, ...buttons.map(button => button.getBoundingClientRect().width));
    const hitHeight = Math.max(36, ...buttons.map(button => button.getBoundingClientRect().height));
    markerLayout(entries, hitWidth, hitHeight).forEach(({top, shift}, index) => {
      buttons[index].style.top = `${top}px`;
      buttons[index].style.transform = `translateX(-${shift}px)`;
    });
  }

  function renderMarkers() {
    document.querySelectorAll('.gutter-marker').forEach(node => node.remove());
    if (!renderedChapterId) return;
    for (const marker of markerState.markers.filter(item => item.chapter_id === renderedChapterId)) {
      const block = blockElement(marker.block_id);
      if (!block) continue;
      const location = markerLocation(marker);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `gutter-marker${location?.current ? '' : ' stale'}`;
      button.dataset.markerId = marker.id;
      button.setAttribute('aria-label', location?.current ? 'Open marker' : 'Open outdated marker');
      if (!location?.current) button.title = 'The saved text has changed; open this marker to delete it.';
      button.addEventListener('click', event => {
        event.stopPropagation();
        openMarker(marker, button);
      });
      block.append(button);
    }
    positionMarkers();
  }

  function clearMarkerControl() {
    activeMarker = null;
    $('markerControl').hidden = true;
    document.querySelectorAll('.gutter-marker.active').forEach(node => node.classList.remove('active'));
    if (root.CSS?.highlights) root.CSS.highlights.delete('reader-marker-preview');
    fallbackHighlights.forEach(node => node.remove());
    fallbackHighlights = [];
  }

  function showMarkerHighlight(range) {
    if (root.CSS?.highlights && root.Highlight) {
      root.CSS.highlights.set('reader-marker-preview', new root.Highlight(range));
      return;
    }
    for (const rect of range.getClientRects()) {
      if (!rect.width || !rect.height) continue;
      const highlight = document.createElement('span');
      highlight.className = 'marker-preview-fallback';
      Object.assign(highlight.style, {left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px`});
      document.body.append(highlight);
      fallbackHighlights.push(highlight);
    }
  }

  function openMarker(marker, button) {
    clearMarkerControl();
    const location = markerLocation(marker);
    if (!location) return showNotice('Stored marker no longer maps to this chapter.', 3000);
    if (location.current && location.range) showMarkerHighlight(location.range);
    else showNotice('The saved text has changed. You can delete this marker.', 3000);
    activeMarker = marker;
    button.classList.add('active');
    const rect = location.range?.getBoundingClientRect() || button.getBoundingClientRect();
    const control = $('markerControl');
    control.hidden = false;
    const controlRect = control.getBoundingClientRect();
    const left = Math.min(window.innerWidth - controlRect.width - 8, Math.max(8, rect.left));
    const below = rect.bottom + 8;
    const top = below + controlRect.height <= window.innerHeight - 8
      ? below : Math.max(8, rect.top - controlRect.height - 8);
    Object.assign(control.style, {left: `${left}px`, top: `${top}px`});
    $('deleteMarker').focus({preventScroll: true});
  }

  function deleteActiveMarker() {
    if (!activeMarker) return;
    const id = activeMarker.id;
    return enqueueMarkerMutation(async () => {
      try {
        const result = await api(`/api/markers/${encodeURIComponent(id)}`, {
          method: 'DELETE', body: JSON.stringify({revision: markerState._revision}),
        });
        markerState.markers = markerState.markers.filter(marker => marker.id !== id);
        markerState._revision = result.revision;
        clearMarkerControl();
        renderMarkers();
        showNotice('Marker deleted', 1000);
      } catch (error) {
        showNotice(error.message, 3500);
        if (error.status === 409) await reloadMarkerState();
      }
    });
  }

  async function reloadMarkerState() {
    const latest = await api('/api/reader');
    markerState = latest.marker_state;
    metadata.progress = latest.progress;
    clearMarkerControl();
    renderMarkers();
    scheduleReadingProgress();
  }

  function renderToc() {
    const toc = $('toc');
    toc.replaceChildren();
    metadata.chapters.forEach((chapter, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = chapter.title;
      button.setAttribute('aria-current', index === chapterIndex ? 'true' : 'false');
      button.onclick = () => { $('tocPanel').hidden = true; loadChapter(index); };
      toc.append(button);
    });
  }

  function positionKey() { return `intelitex-reader-position-v1:${metadata.book_fingerprint}`; }

  function savePosition() {
    if (!metadata || !currentChapter() || currentChapter().id !== renderedChapterId) return;
    const top = document.querySelector('.reader-bar').getBoundingClientRect().bottom + 8;
    const blocks = Array.from(document.querySelectorAll('.reader-block'));
    const block = blocks.find(node => node.getBoundingClientRect().bottom > top) || blocks.at(-1);
    const rect = block?.getBoundingClientRect();
    const relative = rect && rect.height ? Math.max(0, Math.min(1, (top - rect.top) / rect.height)) : 0;
    localStorage.setItem(positionKey(), JSON.stringify({chapterId: currentChapter().id, blockId: block?.dataset.blockId || null, relative}));
  }

  function savedPosition() {
    try { return JSON.parse(localStorage.getItem(positionKey()) || 'null'); } catch (_) { return null; }
  }

  function restorePosition(saved) {
    if (!saved || saved.chapterId !== currentChapter().id || !saved.blockId) return window.scrollTo(0, 0);
    const block = blockElement(saved.blockId);
    if (!block) return;
    block.scrollIntoView({block: 'start'});
    const header = document.querySelector('.reader-bar').getBoundingClientRect().height;
    window.scrollBy(0, Number(saved.relative || 0) * block.getBoundingClientRect().height - header - 8);
  }

  async function loadChapter(index, restore = null) {
    if (metadata && currentChapter()) savePosition();
    const targetIndex = Math.max(0, Math.min(metadata.chapters.length - 1, index));
    const target = metadata.chapters[targetIndex];
    const request = chapterRequests.next();
    chapterIndex = targetIndex;
    clearMarkerControl();
    document.body.classList.add('loading');
    const chapterNode = $('chapter');
    try {
      const chapter = await api(`/api/chapters/${encodeURIComponent(target.id)}`);
      if (!chapterRequests.isCurrent(request)) return;
      chapterNode.replaceChildren();
      progressWordSpans = new Map();
      const title = document.createElement('h1');
      title.className = 'chapter-display-title';
      title.textContent = chapter.title;
      chapterNode.append(title);
      for (const item of chapter.blocks) {
        const block = document.createElement('p');
        block.className = 'reader-block';
        block.dataset.blockId = item.id;
        block.dataset.kind = item.kind;
        renderInline(block, item.text, item.formatting);
        progressWordSpans.set(item.id, wordSpans(item.text, null));
        chapterNode.append(block);
      }
      if (chapter.warning) {
        const warning = document.createElement('div');
        warning.className = 'chapter-warning'; warning.textContent = chapter.warning; chapterNode.append(warning);
      }
      if (chapter.unavailable) {
        const boundary = document.createElement('div');
        boundary.className = 'unavailable'; boundary.textContent = chapter.unavailable.reason; chapterNode.append(boundary);
      }
      $('chapterTitle').textContent = chapter.title;
      renderedChapterId = chapter.id;
      document.title = `${chapter.title} · ${metadata.title}`;
      $('previousButton').disabled = chapterIndex === 0;
      $('nextButton').disabled = chapterIndex === metadata.chapters.length - 1;
      renderToc();
      renderMarkers();
      requestAnimationFrame(() => {
        if (chapterRequests.isCurrent(request)) {
          restorePosition(restore);
          scheduleReadingProgress();
        }
      });
    } catch (error) {
      if (!chapterRequests.isCurrent(request)) return;
      renderedChapterId = null;
      progressWordSpans = new Map();
      chapterNode.replaceChildren();
      const message = document.createElement('div');
      message.className = 'reader-error';
      message.textContent = error.message;
      chapterNode.append(message);
    } finally {
      if (chapterRequests.isCurrent(request)) document.body.classList.remove('loading');
    }
  }

  function cancelLongPress() {
    if (pointer?.timer) clearTimeout(pointer.timer);
    if (pointer) pointer.timer = null;
  }

  function onPointerDown(event) {
    if (!event.isPrimary || event.button !== 0 || event.target.closest('.gutter-marker')) return;
    const block = event.target.closest('.reader-block');
    if (!block) return;
    const start = locationAtPoint(event.clientX, event.clientY);
    if (!start) return;
    pointer = {id: event.pointerId, x: event.clientX, y: event.clientY, start, fired: false, time: performance.now(), timer: null};
    if (settings.gesture === 'long') {
      pointer.timer = setTimeout(() => {
        if (!pointer) return;
        pointer.fired = true;
        createMarker(normalizedLocation(pointer.start));
      }, LONG_PRESS_MS);
    }
  }

  function onPointerMove(event) {
    if (!pointer || event.pointerId !== pointer.id) return;
    const dx = event.clientX - pointer.x, dy = event.clientY - pointer.y;
    if (Math.hypot(dx, dy) > MOVE_TOLERANCE) cancelLongPress();
    if (settings.gesture === 'drag' && Math.abs(dx) >= DRAG_THRESHOLD && Math.abs(dx) > Math.abs(dy) * 1.2) event.preventDefault();
  }

  function onPointerUp(event) {
    if (!pointer || event.pointerId !== pointer.id) return;
    const state = pointer;
    cancelLongPress();
    pointer = null;
    const dx = event.clientX - state.x, dy = event.clientY - state.y;
    if (settings.gesture === 'tap' && Math.hypot(dx, dy) <= MOVE_TOLERANCE) {
      createMarker(normalizedLocation(locationAtPoint(event.clientX, event.clientY) || state.start));
    } else if (settings.gesture === 'drag' && Math.abs(dx) >= DRAG_THRESHOLD && Math.abs(dx) > Math.abs(dy) * 1.2) {
      createMarker(normalizedLocation(state.start, locationAtPoint(event.clientX, event.clientY)));
    }
  }

  function bindControls() {
    $('previousButton').onclick = () => loadChapter(chapterIndex - 1);
    $('nextButton').onclick = () => loadChapter(chapterIndex + 1);
    $('tocButton').onclick = event => { event.stopPropagation(); $('settingsPanel').hidden = true; $('tocPanel').hidden = !$('tocPanel').hidden; };
    $('settingsButton').onclick = event => { event.stopPropagation(); $('tocPanel').hidden = true; $('settingsPanel').hidden = !$('settingsPanel').hidden; };
    $('fullscreenButton').onclick = () => document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen();
    $('readingProgress').onclick = showProgressPopup;
    $('deleteMarker').onclick = deleteActiveMarker;
    for (const key of Object.keys(DEFAULTS)) {
      const control = $(key);
      if (!control) continue;
      control.addEventListener('input', () => {
        settings[key] = ['fontSize', 'lineHeight', 'contentWidth'].includes(key) ? Number(control.value) : control.value;
        applySettings(); saveSettings();
      });
    }
    $('chapter').addEventListener('pointerdown', onPointerDown);
    document.addEventListener('pointermove', onPointerMove, {passive: false});
    document.addEventListener('pointerup', onPointerUp);
    document.addEventListener('pointercancel', () => { cancelLongPress(); pointer = null; });
    $('chapter').addEventListener('contextmenu', event => {
      if (settings.gesture === 'long' && event.target.closest('.reader-block')) event.preventDefault();
    });
    document.addEventListener('click', event => {
      if (!event.target.closest('.panel') && !event.target.closest('#tocButton') && !event.target.closest('#settingsButton')) {
        $('tocPanel').hidden = true; $('settingsPanel').hidden = true;
      }
      if (!event.target.closest('.gutter-marker') && !event.target.closest('#markerControl')) clearMarkerControl();
      if (!event.target.closest('#readingProgress') && !event.target.closest('#progressPopup')) hideProgressPopup();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        $('tocPanel').hidden = true; $('settingsPanel').hidden = true; clearMarkerControl(); hideProgressPopup();
      }
    });
    window.addEventListener('resize', () => { positionMarkers(); scheduleReadingProgress(); });
    window.addEventListener('scroll', () => {
      clearTimeout(positionTimer);
      positionTimer = setTimeout(savePosition, 180);
      scheduleReadingProgress();
      if (!$('markerControl').hidden) clearMarkerControl();
    }, {passive: true});
    window.addEventListener('beforeunload', savePosition);
  }

  async function start() {
    applySettings();
    bindControls();
    metadata = await api('/api/reader');
    markerState = metadata.marker_state;
    $('bookTitle').textContent = metadata.title;
    if (!metadata.chapters.length) throw new Error('This project has no readable chapters.');
    const saved = savedPosition();
    const index = saved ? metadata.chapters.findIndex(chapter => chapter.id === saved.chapterId) : 0;
    await loadChapter(index >= 0 ? index : 0, saved);
  }

  start().catch(error => {
    const message = document.createElement('div');
    message.className = 'reader-error'; message.textContent = error.message;
    $('chapter').replaceChildren(message);
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
