import { expect, it } from 'vitest'
import type { Pipeline } from '../../api/schema'
import { highestSavedPass, passDisplay } from './TranslateContent'

type ChunkPass = Pipeline['units'][number]['passes'][string]
const pass = (checkpoint_state: string, retained_count: number, failed_attempt_count = 0,
  attempt_result: string | null = null) => ({ checkpoint_state, retained_count, failed_attempt_count, attempt_result }) as ChunkPass

it('shows each pass result while keeping stale and failed results distinct', () => {
  expect(passDisplay(pass('retained', 1), 'pending')).toMatchObject({ tone: 'completed', symbol: '✓' })
  expect(passDisplay(pass('retained', 1, 2), 'pending')).toMatchObject({ tone: 'completed', symbol: '✓' })
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
