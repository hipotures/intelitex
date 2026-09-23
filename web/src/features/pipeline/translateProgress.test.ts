import { expect, it } from 'vitest'
import type { Pipeline } from '../../api/schema'
import { passDisplay } from './TranslateContent'

type ChunkPass = Pipeline['units'][number]['passes'][string]
const pass = (checkpoint_state: string, retained_count: number, failed_attempt_count = 0,
  attempt_result: string | null = null) => ({ checkpoint_state, retained_count, failed_attempt_count, attempt_result }) as ChunkPass

it('shows each pass result while keeping stale and failed results distinct', () => {
  expect(passDisplay(pass('retained', 1), 'pending')).toMatchObject({ tone: 'completed', symbol: '✓' })
  expect(passDisplay(pass('retained', 1, 2), 'pending')).toMatchObject({ tone: 'completed', symbol: '✓' })
  expect(passDisplay(pass('retained', 1), 'stale')).toMatchObject({ tone: 'stale', symbol: '↻' })
  expect(passDisplay(pass('completed', 1), 'done')).toMatchObject({ tone: 'completed', symbol: '✓' })
  expect(passDisplay(pass('pending', 0, 1), 'pending')).toMatchObject({ tone: 'error', symbol: '×' })
  expect(passDisplay(pass('pending', 0), 'pending')).toMatchObject({ tone: 'pending', symbol: '○' })
})
