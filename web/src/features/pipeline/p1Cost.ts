import type { Usage } from '../../api/schema'

type Cost = NonNullable<Usage['units'][number]['passes'][number]['cost']>

export function formatCost(amount: number, currency: string): string {
  if (!/^[A-Z]{3}$/.test(currency)) return `${amount.toFixed(4)} ${currency}`
  const value = new Intl.NumberFormat('en-US', { style: 'currency', currency,
    minimumFractionDigits: 4, maximumFractionDigits: 4 }).format(amount)
  return amount > 0 && amount < 0.00005 ? `<${new Intl.NumberFormat('en-US', { style: 'currency', currency,
    minimumFractionDigits: 4, maximumFractionDigits: 4 }).format(0.0001)}` : value
}

export function displayCost(cost: Cost | null | undefined): string {
  if (cost?.amount == null || !cost.currency) return '—'
  return formatCost(cost.amount, cost.currency)
}

export function p1Cost(usage: Usage | undefined) {
  const passes = usage?.units.flatMap(unit => unit.passes.filter(pass => pass.pass_no === 1)) ?? []
  const contacted = passes.filter(pass => pass.provider_call_count > 0 || pass.unknown_provider_call_count > 0)
  const priced = contacted.map(pass => pass.cost).filter((cost): cost is Cost => cost?.amount != null && !!cost.currency)
  if (!contacted.length) return { text: '—', note: 'Waiting for recorded P1 model usage.' }
  if (!priced.length) return { text: '—', note: 'P1 price unavailable: usage or a saved model rate is missing.' }
  const identities = new Set(priced.map(cost => `${cost.currency}:${cost.estimate_type ?? ''}`))
  if (identities.size !== 1) return { text: '—', note: 'P1 estimates use different currencies or pricing types and cannot be added.' }
  const amount = priced.reduce((sum, cost) => sum + cost.amount!, 0)
  const partial = priced.length !== contacted.length || priced.some(cost => cost.status !== 'complete')
  const fallback = contacted.some(pass => pass.cost?.note?.includes('Current catalog rates'))
  const detail = 'Recorded P1 attempts, including retries. Each attempt is priced as (uncached input × input rate + cached input × cache rate + cache writes × write rate + output × output rate) / 1,000,000. Reasoning tokens are included in output. Codex uses API-equivalent rates; this is not a subscription invoice.'
  return { text: formatCost(amount, priced[0]!.currency!),
    note: `${detail}${fallback ? ' Older attempts without saved rates use the current model catalog.' : ''}${partial ? ' Some attempts have unknown price or usage, so the shown sum covers only known amounts.' : ''}` }
}

export function translationCost(usage: Usage | undefined) {
  const passes = usage?.units.flatMap(unit => unit.passes.filter(pass => pass.pass_no >= 2 && pass.pass_no <= 5)) ?? []
  const contacted = passes.filter(pass => pass.provider_call_count > 0 || pass.unknown_provider_call_count > 0)
  const priced = contacted.map(pass => pass.cost).filter((cost): cost is Cost => cost?.amount != null && !!cost.currency)
  if (!contacted.length) return { text: '—', note: 'Waiting for recorded P2–P5 model usage.' }
  if (!priced.length) return { text: '—', note: 'P2–P5 price unavailable: usage or a saved model rate is missing.' }
  const currencies = new Set(priced.map(cost => cost.currency))
  if (currencies.size !== 1) return { text: '—', note: 'P2–P5 estimates use different currencies and cannot be added.' }
  const amount = priced.reduce((sum, cost) => sum + cost.amount!, 0)
  const partial = priced.length !== contacted.length || priced.some(cost => cost.status !== 'complete')
  return { text: formatCost(amount, priced[0]!.currency!),
    note: `Recorded P2–P5 attempts across all chunks, passes, models and retries. This is an estimate, not a provider invoice.${partial ? ' Some attempts have unknown price or usage, so the shown sum covers only known amounts.' : ''}` }
}
