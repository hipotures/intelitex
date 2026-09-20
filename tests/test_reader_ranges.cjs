const {test} = require('node:test');
const assert = require('node:assert/strict');
const R = require('../bookpipe/reader.js');

test('UTF-16 browser offsets convert to Unicode code-point offsets', () => {
  const text = 'A😀gęślą';
  assert.equal(R.utf16ToCodePoint(text, 3), 2);
  assert.equal(R.codePointToUtf16(text, 2), 3);
});

test('word snapping handles Polish text and expands a drag outward', () => {
  const text = 'Zażółć gęślą jaźń.';
  assert.deepEqual(R.snapWordRange(text, 2, 2, null), {start: 0, end: 6});
  assert.deepEqual(R.snapWordRange(text, 8, 15, null), {start: 7, end: 17});
});

test('word snapping normalizes reverse drags', () => {
  const text = 'pierwsze drugie trzecie';
  assert.deepEqual(R.snapWordRange(text, 19, 10, null), {start: 9, end: 23});
});

test('reading progress combines prior blocks with the word at the viewport position', () => {
  const text = 'jeden dwa trzy cztery';
  const spans = R.wordSpans(text, null);
  const progress = {
    total_words: 20,
    chapters: [{id: 'ch2', blocks: [{id: 'B4', start: 11, words: 4}]}],
  };
  assert.equal(R.wordPosition(spans, 0), 0);
  assert.equal(R.wordPosition(spans, 8), 2);
  assert.deepEqual(R.progressAtLocation(progress, 'ch2', 'B4', spans, 8), {
    read: 13, total: 20, remaining: 7, ratio: 0.65, percent: 65,
  });
  assert.deepEqual(R.progressAtLocation(progress, 'ch2', 'B4', spans, text.length), {
    read: 15, total: 20, remaining: 5, ratio: 0.75, percent: 75,
  });
});

test('viewport midpoint resolves block interiors, gaps, and document edges', () => {
  const rects = [{top: 100, bottom: 180}, {top: 220, bottom: 300}];
  assert.deepEqual(R.viewportBlockPosition(rects, 50), {index: 0, edge: 'start'});
  assert.deepEqual(R.viewportBlockPosition(rects, 140), {index: 0, edge: 'inside'});
  assert.deepEqual(R.viewportBlockPosition(rects, 200), {index: 0, edge: 'end'});
  assert.deepEqual(R.viewportBlockPosition(rects, 260), {index: 1, edge: 'inside'});
  assert.deepEqual(R.viewportBlockPosition(rects, 350), {index: 1, edge: 'end'});
});

test('marker mutations execute serially and observe the latest revision', async () => {
  const enqueue = R.createSerialQueue();
  let revision = 0;
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const observed = [];
  const first = enqueue(async () => { observed.push(['create', revision]); await gate; revision += 1; });
  const second = enqueue(async () => { observed.push(['create', revision]); revision += 1; });
  const third = enqueue(async () => { observed.push(['delete', revision]); revision += 1; });
  await Promise.resolve();
  assert.deepEqual(observed, [['create', 0]]);
  release();
  await Promise.all([first, second, third]);
  assert.deepEqual(observed, [['create', 0], ['create', 1], ['delete', 2]]);
});

test('a rejected mutation does not break ordering or hide a genuine conflict', async () => {
  const enqueue = R.createSerialQueue();
  const conflict = enqueue(async () => { throw new Error('stale revision'); });
  const after = enqueue(async () => 'ran after refresh boundary');
  await assert.rejects(conflict, /stale revision/);
  assert.equal(await after, 'ran after refresh boundary');
});

test('neighboring gutter hit areas spread horizontally instead of extending vertically', () => {
  assert.deepEqual(R.markerLayout([
    {base: 9, screenTop: 100},
    {base: 9, screenTop: 100},
    {base: 3, screenTop: 130},
    {base: 3, screenTop: 170},
  ]), [
    {top: 9, shift: 0},
    {top: 9, shift: 28},
    {top: 3, shift: 56},
    {top: 3, shift: 0},
  ]);
});

test('inline runs preserve plain offsets and whitelist only emphasis tags', () => {
  const text = 'To ważne i bardzo mocne <img src=x>.';
  assert.deepEqual(R.inlineRuns(text, [
    {start: 3, end: 8, style: 'em'},
    {start: 11, end: 23, style: 'strong'},
  ]), [
    {text: 'To ', style: null},
    {text: 'ważne', style: 'em'},
    {text: ' i ', style: null},
    {text: 'bardzo mocne', style: 'strong'},
    {text: ' <img src=x>.', style: null},
  ]);
  assert.deepEqual(R.inlineRuns(text, [{start: 0, end: 2, style: 'script'}]), [{text, style: null}]);
});

test('only the newest chapter request remains current', () => {
  const gate = R.createRequestGate();
  const chapterTwo = gate.next();
  const chapterThree = gate.next();
  assert.equal(gate.isCurrent(chapterTwo), false);
  assert.equal(gate.isCurrent(chapterThree), true);
});

test('an outdated marker remains anchored locally without claiming an exact match', () => {
  const marker = {start: 7, end: 12, text: 'stare'};
  assert.deepEqual(R.markerAnchor('Nowy tekst akapitu', marker), {current: false, start: 7, end: 8});
  assert.deepEqual(R.markerAnchor('krótki', {...marker, start: 50, end: 55}),
    {current: false, start: 5, end: 6});
  assert.deepEqual(R.markerAnchor('Nowy tekst akapitu', {start: 5, end: 10, text: 'tekst'}),
    {current: true, start: 5, end: 10});
});

test('gesture defaults, migration, and assignment swapping stay unambiguous', () => {
  assert.deepEqual(R.gestureSettings({}), {markerGesture: 'drag', contextGesture: 'long'});
  assert.deepEqual(R.gestureSettings({gesture: 'tap'}), {markerGesture: 'tap', contextGesture: 'long'});
  assert.deepEqual(R.gestureSettings({gesture: 'long'}), {markerGesture: 'long', contextGesture: 'drag'});
  assert.deepEqual(
    R.assignGesture({markerGesture: 'drag', contextGesture: 'long'}, 'contextGesture', 'drag'),
    {markerGesture: 'long', contextGesture: 'drag'},
  );
  assert.deepEqual(
    R.assignGesture({markerGesture: 'off', contextGesture: 'long'}, 'markerGesture', 'long'),
    {markerGesture: 'long', contextGesture: 'off'},
  );
});

test('context dismissal consumes the complete tap sequence before a tap action can run', () => {
  const guard = R.createContextDismissalGuard();
  let tapActions = 0;
  const send = (type, outside, pointerId) => {
    if (!guard.consume(type, outside, pointerId) && type === 'click') tapActions += 1;
  };
  guard.open();
  send('pointerdown', true, 7);
  send('pointerup', true, 7);
  send('click', true, null);
  assert.equal(guard.isOpen(), false);
  assert.equal(tapActions, 0);
  send('click', true, null);
  assert.equal(tapActions, 1);
});

test('recognized context opens a card even when no earlier knowledge exists', () => {
  assert.equal(R.contextResultState({recognized: true, statements: [], earlier_mentions: []}), 'card');
  assert.equal(R.contextResultState({recognized: false}), 'flash');
});

test('release and click from the gesture that opened context are consumed without closing it', () => {
  const guard = R.createContextDismissalGuard();
  guard.open(9);
  assert.equal(guard.consume('pointerup', true, 9), true);
  assert.equal(guard.consume('click', true, null), true);
  assert.equal(guard.isOpen(), true);
  assert.equal(guard.consume('pointerdown', true, 10), true);
  assert.equal(guard.isOpen(), false);
});

function fakeTimers() {
  const jobs = [];
  return {
    jobs,
    setTimer(callback, delay) {
      const job = {callback, delay, cancelled: false};
      jobs.push(job);
      return job;
    },
    clearTimer(job) { job.cancelled = true; },
    run(job) { if (!job.cancelled) job.callback(); },
  };
}

test('header auto-hide accepts only the supported values and defaults to off', () => {
  assert.equal(R.headerAutoHideSetting(undefined), 0);
  assert.equal(R.headerAutoHideSetting('5'), 5);
  assert.equal(R.headerAutoHideSetting(10), 10);
  assert.equal(R.headerAutoHideSetting(15), 15);
  assert.equal(R.headerAutoHideSetting(8), 0);
});

test('header activity resets its auto-hide timer', () => {
  const timers = fakeTimers();
  const changes = [];
  const controller = R.createHeaderAutoHideController({
    onChange: hidden => changes.push(hidden), panelsOpen: () => false,
    setTimer: timers.setTimer, clearTimer: timers.clearTimer,
  });
  controller.configure(5);
  const first = timers.jobs.at(-1);
  assert.equal(first.delay, 5000);
  controller.activity();
  const second = timers.jobs.at(-1);
  assert.equal(first.cancelled, true);
  timers.run(first);
  assert.deepEqual(changes, []);
  timers.run(second);
  assert.deepEqual(changes, [true]);
  controller.show();
  assert.deepEqual(changes, [true, false]);
  assert.equal(controller.state().scheduled, true);
});

test('an open header panel suspends auto-hide until it closes', () => {
  const timers = fakeTimers();
  let panelOpen = false;
  let hidden = false;
  const controller = R.createHeaderAutoHideController({
    onChange: value => { hidden = value; }, panelsOpen: () => panelOpen,
    setTimer: timers.setTimer, clearTimer: timers.clearTimer,
  });
  controller.configure(10);
  const beforePanel = timers.jobs.at(-1);
  panelOpen = true;
  controller.panelChanged();
  assert.equal(beforePanel.cancelled, true);
  assert.equal(controller.state().scheduled, false);
  panelOpen = false;
  controller.panelChanged();
  timers.run(timers.jobs.at(-1));
  assert.equal(hidden, true);
});

test('header visibility changes only the overlay and leaves prose position untouched', () => {
  const classes = new Set();
  const body = {classList: {toggle(name, active) { active ? classes.add(name) : classes.delete(name); }}};
  const attributes = {};
  const header = {inert: false, setAttribute(name, value) { attributes[name] = value; }};
  const position = {scrollY: 842, saved: {blockId: 'B7', relative: 0.42}};
  const before = JSON.stringify(position);
  R.applyHeaderVisibility(body, header, true);
  assert.equal(classes.has('header-hidden'), true);
  assert.equal(header.inert, true);
  R.applyHeaderVisibility(body, header, false);
  assert.equal(classes.has('header-hidden'), false);
  assert.equal(attributes['aria-hidden'], 'false');
  assert.equal(JSON.stringify(position), before);
});

test('top-zone single and double taps are disambiguated without mixed actions', () => {
  const timers = fakeTimers();
  const actions = [];
  const taps = R.createTapDisambiguator({
    onSingle: () => actions.push('progress'), onDouble: () => actions.push('header'),
    delay: 300, setTimer: timers.setTimer, clearTimer: timers.clearTimer,
  });
  taps.tap();
  assert.deepEqual(actions, []);
  timers.run(timers.jobs.at(-1));
  assert.deepEqual(actions, ['progress']);

  taps.tap();
  const pendingSingle = timers.jobs.at(-1);
  taps.tap();
  assert.equal(pendingSingle.cancelled, true);
  timers.run(pendingSingle);
  assert.deepEqual(actions, ['progress', 'header']);
});

test('top-zone pointer and click events are consumed before prose gestures', () => {
  let prevented = 0, stopped = 0, proseActions = 0;
  const event = {
    preventDefault() { prevented += 1; },
    stopImmediatePropagation() { stopped += 1; },
  };
  R.consumeTopZoneEvent(event);
  if (!stopped) proseActions += 1;
  assert.deepEqual({prevented, stopped, proseActions}, {prevented: 1, stopped: 1, proseActions: 0});
});

test('chapter end offers only the next chapter in the readable prefix', () => {
  const chapters = [
    {id: 'ch1', title: 'One'}, {id: 'ch2', title: 'Two'}, {id: 'ch3', title: 'Three'},
  ];
  const progress = {chapters: [{id: 'ch1'}, {id: 'ch2'}]};
  assert.deepEqual(R.chapterEndState(chapters, progress, 0), {kind: 'next', index: 1, title: 'Two'});
  assert.deepEqual(R.chapterEndState(chapters, progress, 1), {kind: 'end'});
  assert.deepEqual(R.chapterEndState(chapters, {chapters: [{id: 'ch1'}]}, 0), {kind: 'end'});
});
