import { describe,it,expect } from 'vitest'
import { emptyStream,snapshot,progress,latestWorkspaceJob } from './state'
import type { Envelope,Job } from '../api/schema'
const job = (id='one',sequence=5): Job => ({ job_id:id,workspace_id:id,sequence,operation:'translate',state:'running',started_at:null,finished_at:null,last_event:null,error:null })
const event = (id:number,sequence:number, workspace='one', state='running'): Envelope => ({ id,sequence,job_id:workspace,workspace_id:workspace,timestamp:'2026-09-22T00:00:00Z',event:{ kind:'job_state',values:{state} } })
describe('snapshot and replay ordering',() => {
 it('keeps current state while adding older replay history and deduplicating global ids',() => {
  const initial=snapshot(emptyStream(),{jobs:[job()],cursor:50})
  const old=progress(initial,event(44,4,'one','starting'))
  expect(old.state.jobs.one?.state).toBe('running');expect(old.state.jobs.one?.sequence).toBe(5)
  expect(old.state.activity.one).toHaveLength(1)
  expect(progress(old.state,event(44,4)).state).toBe(old.state)
 })
 it('isolates workspaces and flags gaps without inventing pipeline completion',() => {
  const initial=snapshot(emptyStream(),{jobs:[job(),job('two',2)],cursor:50})
  const latest=progress(initial,event(51,8,'one','succeeded'))
  expect(latest.gap).toBe(true);expect(latest.state.jobs.one?.state).toBe('succeeded')
  expect(latest.state.jobs.two?.sequence).toBe(2);expect(latest.state).not.toHaveProperty('translation_complete')
 })
 it('resets ordering namespace when server cursor regresses',() => {
  const initial=progress(snapshot(emptyStream(),{jobs:[job()],cursor:50}),event(51,6)).state
  const next=snapshot(initial,{jobs:[job('new',1)],cursor:2})
  expect(next.jobs.one).toBeUndefined();expect(next.seen.size).toBe(0)
 })
 it('bounds each workspace activity while accepting unknown-job replay for reconciliation',() => {
  let state=emptyStream()
  for(let n=1;n<=140;n++) state=progress(state,event(n,n)).state
  expect(state.activity.one).toHaveLength(120);expect(state.activity.one?.[0]?.id).toBe(21)
  expect(state.jobs.one).toBeUndefined()
 })
})
it('an older in-flight HTTP snapshot cannot reset a newer stream state',()=>{
 const initial=progress(snapshot(emptyStream(),{jobs:[job()],cursor:50}),event(52,7)).state
 const refreshed=snapshot(initial,{jobs:[job('one',6)],cursor:51},false)
 expect(refreshed.jobs.one?.sequence).toBe(7);expect(refreshed.cursor).toBe(52)
})

it('replaces an old failed job when a later retry starts or succeeds', () => {
 const failed = { ...job('old', 3), workspace_id: 'book', state: 'failed' as const,
   started_at: '2026-09-26T00:54:29Z', finished_at: '2026-09-26T00:54:30Z' }
 const retry = { ...job('retry', 2), workspace_id: 'book', started_at: '2026-09-26T00:59:03Z' }
 const other = { ...job('other', 9), workspace_id: 'other', started_at: '2026-09-26T01:00:00Z' }
 expect(latestWorkspaceJob('book', failed, { retry, other })?.job_id).toBe('retry')
 expect(latestWorkspaceJob('book', failed, { retry: { ...retry, state: 'succeeded', sequence: 7 } })?.state).toBe('succeeded')
 expect(latestWorkspaceJob('book', retry, { retry: { ...retry, state: 'succeeded', sequence: 7 } })?.state).toBe('succeeded')
 expect(latestWorkspaceJob('book', failed, { other })?.state).toBe('failed')
 expect(latestWorkspaceJob('book', null, { retry })).toBeNull()
})
