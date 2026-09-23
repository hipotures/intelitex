import { describe, expect, it } from 'vitest'
import type { Term } from '../../api/schema'
import { nextUnreviewed } from './Review'

const term = (id: string, category: string, reviewed = false): Term => ({
  id, source: id, category, reviewed, aliases: [], select: 1, custom: '', user_notes: '',
  meaning_notes: [], observations: [], evidence: [],
  candidates: [{ number: 1, text: id }],
})

describe('legacy Review & next order', () => {
  const terms = [term('A', 'People'), term('B', 'People', true), term('C', 'People'), term('D', 'Places')]

  it('skips reviewed terms, wraps, and stays within the selected category', () => {
    expect(nextUnreviewed(terms, 'A', '', 'People')).toBe('C')
    expect(nextUnreviewed(terms, 'C', '', 'People')).toBe('A')
    expect(nextUnreviewed(terms, 'D', '', 'Places')).toBe('D')
  })

  it('respects search and stops when the matching scope is fully reviewed', () => {
    expect(nextUnreviewed(terms, 'A', 'A', 'People')).toBe('A')
    expect(nextUnreviewed(terms, 'C', ' C ', 'People')).toBe('C')
    expect(nextUnreviewed(terms.map(item => ({ ...item, reviewed: true })), 'A', '', 'People')).toBeUndefined()
    expect(nextUnreviewed(terms, 'D', '', 'People')).toBeUndefined()
  })
})
