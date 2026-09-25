import { describe, expect, it } from 'vitest'
import type { Term } from '../../api/schema'
import { nextUnreviewed } from './Review'
import { reviewCountLabel, reviewCounts, reviewStatusLabel } from './reviewStatus'

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
  it('does not count bulk, inherited, or legacy acceptance as individual review', () => {
    const terms = [
      { ...term('A', 'People', true), review_method: 'bulk' },
      { ...term('B', 'People', true), review_method: 'individual' },
      { ...term('C', 'People', true), review_method: 'inherited' },
      term('D', 'People', true),
      term('E', 'People'),
    ]
    expect(reviewCounts(terms)).toEqual({ pending: 1, individual: 1, bulk: 1, inherited: 1, unknown: 1 })
    expect(reviewCountLabel(terms)).toBe('1/5 individually reviewed · 1 bulk accepted · 1 inherited · 1 accepted (method unknown) · 1 pending')
    expect(reviewStatusLabel(terms[0]!)).toBe('✓ bulk accepted')
    expect(reviewStatusLabel(terms[3]!)).toBe('✓ accepted (method unknown)')
  })
  it('reports a fully bulk accepted glossary without claiming human review', () => {
    const terms = Array.from({ length: 55 }, (_, index) => ({
      ...term(String(index), 'People', true), review_method: 'bulk',
    }))
    expect(reviewCountLabel(terms)).toBe('0/55 individually reviewed · 55 bulk accepted · 0 pending')
  })
})
