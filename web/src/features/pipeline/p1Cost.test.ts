import { describe, expect, it } from 'vitest'
import type { Usage } from '../../api/schema'
import { formatCost, p1Cost, translationCost } from './p1Cost'

const usageWithCosts = (...costs: Array<{ amount: number | null; currency: string | null;
  status: string; estimate_type: string | null; note: string | null } | null>) => ({
  units: costs.map((cost, index) => ({ unit_id: `a${index}`, passes: [{ pass_no: 1,
    provider_call_count: 1, unknown_provider_call_count: 0, cost }] })),
}) as Usage

describe('P1 recorded cost summary', () => {
  it('keeps a known subtotal honest when another recorded attempt has no price', () => {
    const value = p1Cost(usageWithCosts(
      { amount: .125, currency: 'USD', status: 'complete', estimate_type: 'codex_api_equivalent_standard', note: null },
      null))
    expect(value.text).toBe('$0.1250')
    expect(value.note).toContain('only known amounts')
  })

  it('never adds different currencies and does not round tiny real costs to zero', () => {
    expect(p1Cost(usageWithCosts(
      { amount: 1, currency: 'USD', status: 'complete', estimate_type: 'api', note: null },
      { amount: 1, currency: 'EUR', status: 'complete', estimate_type: 'api', note: null })).text).toBe('—')
    expect(formatCost(.0000001, 'USD')).toBe('<$0.0001')
    expect(formatCost(.003184, 'USD')).toBe('$0.0032')
    expect(formatCost(.3, 'USD')).toBe('$0.3000')
  })
})

describe('translation recorded cost summary', () => {
  it('sums P2–P5 across profiles and keeps an unknown-price attempt visible in the note', () => {
    const usage = { units: [{ unit_id: 'c1', passes: [
      { pass_no: 1, profile: 'p1', provider_call_count: 1, unknown_provider_call_count: 0,
        cost: { amount: 10, currency: 'USD', status: 'complete', estimate_type: 'api', note: null } },
      { pass_no: 2, profile: 'luna-low', provider_call_count: 1, unknown_provider_call_count: 0,
        cost: { amount: .1, currency: 'USD', status: 'complete', estimate_type: 'api', note: null } },
      { pass_no: 4, profile: 'luna-medium', provider_call_count: 1, unknown_provider_call_count: 0,
        cost: { amount: .2, currency: 'USD', status: 'complete', estimate_type: 'other', note: null } },
      { pass_no: 5, profile: 'local', provider_call_count: 1, unknown_provider_call_count: 0, cost: null },
    ] }] } as Usage
    const result = translationCost(usage)
    expect(result.text).toBe('$0.3000')
    expect(result.note).toContain('only known amounts')
  })
})
