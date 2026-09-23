import { describe, expect, it } from 'vitest'
import type { Usage } from '../../api/schema'
import { formatCost, p1Cost } from './p1Cost'

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
