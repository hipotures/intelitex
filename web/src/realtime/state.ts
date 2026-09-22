import type { Envelope, Job } from '../api/schema'
export type Connection = 'Live' | 'Reconnecting…' | 'Offline'
export interface StreamState { jobs: Record<string, Job>; activity: Record<string, Envelope[]>; cursor: number; seen: Set<number> }
export const emptyStream = (): StreamState => ({ jobs: {}, activity: {}, cursor: 0, seen: new Set() })
export function snapshot(state: StreamState, value: { jobs: Job[]; cursor: number }, allowReset = true): StreamState {
  const base = allowReset && value.cursor < state.cursor ? emptyStream() : state
  const jobs = { ...base.jobs }
  for (const job of value.jobs) if (!jobs[job.job_id] || job.sequence >= jobs[job.job_id]!.sequence) jobs[job.job_id] = job
  return { ...base, jobs, cursor: Math.max(base.cursor, value.cursor) }
}
export function progress(state: StreamState, value: Envelope): { state: StreamState; gap: boolean; applied: boolean } {
  if (state.seen.has(value.id)) return { state, gap: false, applied: false }
  const seen = new Set(state.seen).add(value.id)
  if (seen.size > 10_000) seen.delete(seen.values().next().value!)
  const history = [...(state.activity[value.workspace_id] ?? []), value].sort((a, b) => a.id - b.id).slice(-120)
  const job = state.jobs[value.job_id]
  const applied = !!job && value.sequence > job.sequence
  const next = { ...state, seen, cursor: Math.max(state.cursor, value.id), activity: { ...state.activity, [value.workspace_id]: history } }
  if (applied) {
    const raw = value.event.values.state
    const stateValue = value.event.kind === 'job_state' && ['starting','running','stopping','succeeded','failed','cancelled','abandoned'].includes(String(raw)) ? raw as Job['state'] : job.state
    next.jobs = { ...state.jobs, [job.job_id]: { ...job, sequence: value.sequence, state: stateValue, last_event: value } }
  }
  return { state: next, applied, gap: !job || value.sequence > job.sequence + 1 }
}
