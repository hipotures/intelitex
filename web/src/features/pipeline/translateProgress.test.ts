import { expect, it } from 'vitest'
import type { Envelope, Pipeline } from '../../api/schema'
import { highestSavedPass, completedPassesInLiveEvents, passDisplay } from './TranslateContent'

type ChunkPass = Pipeline['units'][number]['passes'][string]
const pass = (checkpoint_state: string, retained_count: number, failed_attempt_count = 0,
  attempt_result: string | null = null) => ({ checkpoint_state, retained_count, failed_attempt_count, attempt_result }) as ChunkPass

it('shows each pass result while keeping stale and failed results distinct', () => {
  expect(passDisplay(pass('retained', 1), 'pending')).toMatchObject({ tone: 'retained', symbol: '◐' })
  expect(passDisplay(pass('retained', 1, 2), 'pending')).toMatchObject({ tone: 'retained', symbol: '◐' })
  expect(passDisplay(pass('retained', 1), 'stale')).toMatchObject({ tone: 'stale', symbol: '↻' })
  expect(passDisplay(pass('completed', 1), 'done')).toMatchObject({ tone: 'completed', symbol: '✓' })
  expect(passDisplay(pass('pending', 0, 1), 'pending')).toMatchObject({ tone: 'error', symbol: '×' })
  expect(passDisplay(pass('pending', 0, 1), 'pending', true)).toMatchObject({ tone: 'pending', symbol: '○' })
  expect(passDisplay(pass('pending', 0, 1, 'not_checkpointed'), 'pending', true)).toMatchObject({ tone: 'pending', symbol: '○' })
  expect(passDisplay(pass('error', 0, 1), 'pending', true)).toMatchObject({ tone: 'error', symbol: '×' })
  expect(passDisplay(pass('pending', 0), 'pending')).toMatchObject({ tone: 'pending', symbol: '○' })
})

it('opens the highest saved pass for each selected chunk and source for an untouched chunk', () => {
  const unit = (passes: Record<string, ChunkPass>) => ({ passes }) as Pipeline['units'][number]
  expect(highestSavedPass(unit({}))).toBeNull()
  expect(highestSavedPass(unit({ '2': pass('retained', 1) }))).toBe(2)
  expect(highestSavedPass(unit({ '2': pass('retained', 1), '4': pass('retained', 1) }))).toBe(4)
  expect(highestSavedPass(unit({ '2': pass('retained', 1), '5': pass('completed', 1) }))).toBe(5)
})

it('shows a committed pass as successful before a stale pipeline snapshot catches up', () => {
  const event = (id: number, kind: string, current: number | null, passNo?: number): Envelope => ({
    id, job_id: 'retry', workspace_id: 'book', sequence: id, timestamp: '2026-09-26T01:00:00Z',
    event: { kind, current, values: { chunk_id: 'ch0001_c0001', ...(passNo ? { pass_no: passNo } : {}) } },
  })
  const started = event(1, 'pass_started', null, 4)
  const committed = event(2, 'translation_unit_progress', 3)
  const oldFailure = pass('pending', 0, 1, 'not_checkpointed')
  expect(completedPassesInLiveEvents([started], 'retry').has('ch0001_c0001:4')).toBe(false)
  expect(passDisplay(oldFailure, 'pending', false, false).tone).toBe('error')
  expect(completedPassesInLiveEvents([started, committed], 'retry').has('ch0001_c0001:4')).toBe(true)
  expect(completedPassesInLiveEvents([started, event(2, 'translation_unit_progress', 3, 4)], 'retry').has('ch0001_c0001:4')).toBe(true)
  expect(passDisplay(oldFailure, 'pending', false, true)).toMatchObject({ tone: 'completed', symbol: '✓' })
  expect(completedPassesInLiveEvents([started, committed, event(3, 'pass_started', null, 4)], 'retry').has('ch0001_c0001:4')).toBe(false)
  expect(completedPassesInLiveEvents([started, committed], 'other').has('ch0001_c0001:4')).toBe(false)
})
