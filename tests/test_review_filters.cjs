const {test} = require('node:test');
const assert = require('node:assert/strict');
const F = require('../bookpipe/review_filters.js');
const make = (id, category, confidence, reviewed, user_notes='') => ({id, source: id,
  category, reviewed, user_notes, select:1, custom:'', meaning_notes:[{text:'test',confidence}],
  candidates:[{number:1,text:'Chosen '+id,confidence}],observations:[]});
const terms = () => [make('A','people','medium',false,'Check in Polish'),make('B','people','low',true),
  make('C','people','high',false),make('D','place','medium',true),make('E','technology','high',false),
  make('F','place','high',true,'Keep this note')];
test('All + All returns every term; Uncertain + All returns only uncertain terms',()=>{
  assert.equal(F.scope(terms()).length,6);
  assert.equal(F.scope(terms(),{status:'uncertain'}).length,3);
});
test('category counts are scoped by status, not global category counts',()=>{
  assert.equal(F.categoryStats(terms(),'people','all','').total,3);
  assert.deepEqual(F.categoryStats(terms(),'people','uncertain',''),
    {total:2,reviewed:1,remaining:1,complete:false,baseTotal:2});
});
test('completed uncertain subset is green while whole category remains unfinished',()=>{
  const t=terms();t[0].reviewed=true;
  assert.equal(F.categoryStats(t,'people','uncertain','').complete,true);
  assert.equal(F.categoryStats(t,'people','all','').complete,false);
  assert.equal(F.categoryStats(t,'all','uncertain','').remaining,0);
});
test('Unreviewed zero is green only when its nonempty parent category is completed',()=>{
  assert.equal(F.categoryStats(terms(),'place','unreviewed','').total,0);
  assert.equal(F.categoryStats(terms(),'place','unreviewed','').complete,true);
  assert.equal(F.categoryStats(terms(),'ship','unreviewed','').complete,false);
});
test('zero uncertain matches are not presented as reviewed',()=>{
  assert.equal(F.categoryStats(terms(),'technology','uncertain','').complete,false);
});
test('search applies to both filter axes including notes and choices',()=>{
  assert.equal(F.scope(terms(),{category:'people',status:'notes',search:'Polish'}).length,1);
  assert.equal(F.categoryStats(terms(),'people','all','nonexistent').complete,false);
  assert.equal(F.scope(terms(),{search:'Chosen C'})[0].id,'C');
});
test('Notes preserves reviewed entries for a later context review',()=>{
  assert.deepEqual(F.scope(terms(),{status:'notes'}).map(t=>t.id),['A','F']);
});
test('Review next skips accepted terms, wraps, and returns null for a finished scope',()=>{
  let t=terms();assert.equal(F.nextUnreviewed(t,'A',{category:'people'}),'C');
  assert.equal(F.nextUnreviewed(t,'C',{category:'people'}),'A');
  assert.equal(F.nextUnreviewed(t,'B',{category:'people',status:'uncertain'}),'A');
  t[0].reviewed=true;
  assert.equal(F.nextUnreviewed(t,'B',{category:'people',status:'uncertain'}),null);
  assert.equal(F.nextUnreviewed(t,'B',{category:'people',status:'reviewed'}),'C');
});
test('observation uncertainty participates in the same scope',()=>{
  let t=terms();t[4].observations=[{statement:'Unclear origin',confidence:'low'}];
  assert.equal(F.scope(t,{category:'technology',status:'uncertain'}).length,1);
});
