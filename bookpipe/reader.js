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

  function markerTops(entries, hitHeight = 36) {
    const byBlock = new Map();
    return entries.map(entry => {
      let lines = byBlock.get(entry.blockId);
      if (!lines) { lines = new Map(); byBlock.set(entry.blockId, lines); }
      const line = Math.round(entry.base / 4);
      const collision = lines.get(line) || 0;
      lines.set(line, collision + 1);
      return entry.base + collision * hitHeight;
    });
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
    createSerialQueue, createRequestGate, markerTops, markerAnchor,
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
  let renderedChapterId = null;
  let fallbackHighlights = [];
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
    requestAnimationFrame(positionMarkers);
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
    const start16 = codePointToUtf16(block.firstChild?.nodeValue || block.textContent, start);
    const end16 = codePointToUtf16(block.firstChild?.nodeValue || block.textContent, end);
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
      const offset = utf16ToCodePoint(block.firstChild?.nodeValue || block.textContent, prefix.toString().length);
      return {block, offset};
    } catch (_) { return null; }
  }

  function normalizedLocation(first, second = first) {
    if (!first || !second || first.block !== second.block) return null;
    const text = first.block.firstChild?.nodeValue || '';
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
    const text = block.firstChild?.nodeValue || '';
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
      entries.push({blockId: marker.block_id, base: Math.max(0, top - blockRect.top + 3)});
      buttons.push(button);
    });
    const hitHeight = Math.max(36, ...buttons.map(button => button.getBoundingClientRect().height));
    markerTops(entries, hitHeight).forEach((top, index) => { buttons[index].style.top = `${top}px`; });
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
    clearMarkerControl();
    renderMarkers();
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
      const title = document.createElement('h1');
      title.className = 'chapter-display-title';
      title.textContent = chapter.title;
      chapterNode.append(title);
      for (const item of chapter.blocks) {
        const block = document.createElement('p');
        block.className = 'reader-block';
        block.dataset.blockId = item.id;
        block.dataset.kind = item.kind;
        block.textContent = item.text;
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
        if (chapterRequests.isCurrent(request)) restorePosition(restore);
      });
    } catch (error) {
      if (!chapterRequests.isCurrent(request)) return;
      renderedChapterId = null;
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
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') { $('tocPanel').hidden = true; $('settingsPanel').hidden = true; clearMarkerControl(); }
    });
    window.addEventListener('resize', positionMarkers);
    window.addEventListener('scroll', () => {
      clearTimeout(positionTimer);
      positionTimer = setTimeout(savePosition, 180);
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
