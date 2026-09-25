import type { Term } from '../../api/schema'

export type ReviewStatus = 'pending' | 'individual' | 'bulk' | 'inherited' | 'unknown'

export function reviewStatus(term: Term): ReviewStatus {
  if (!term.reviewed) return 'pending'
  if (term.review_method === 'individual') return 'individual'
  if (term.review_method === 'bulk') return 'bulk'
  if (term.review_method === 'inherited') return 'inherited'
  return 'unknown'
}

export function reviewCounts(terms: Term[]) {
  const counts: Record<ReviewStatus, number> = { pending: 0, individual: 0, bulk: 0, inherited: 0, unknown: 0 }
  for (const term of terms) counts[reviewStatus(term)]++
  return counts
}

export function reviewStatusLabel(term: Term) {
  switch (reviewStatus(term)) {
    case 'individual': return '✓ individually reviewed'
    case 'bulk': return '✓ bulk accepted'
    case 'inherited': return '✓ inherited choice'
    case 'unknown': return '✓ accepted (method unknown)'
    case 'pending': return '○ pending'
  }
}

export function reviewCountLabel(terms: Term[]) {
  const counts = reviewCounts(terms)
  return [
    `${counts.individual}/${terms.length} individually reviewed`,
    `${counts.bulk} bulk accepted`,
    counts.inherited ? `${counts.inherited} inherited` : null,
    counts.unknown ? `${counts.unknown} accepted (method unknown)` : null,
    `${counts.pending} pending`,
  ].filter(Boolean).join(' · ')
}
