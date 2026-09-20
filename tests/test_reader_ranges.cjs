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
