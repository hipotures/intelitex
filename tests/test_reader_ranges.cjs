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
