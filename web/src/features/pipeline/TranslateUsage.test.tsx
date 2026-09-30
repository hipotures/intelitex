// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { Pipeline, Usage } from '../../api/schema'
import { RecordedProviderUsage, recordedProfileGroups } from './TranslateContent'
import { translationCost } from './p1Cost'

afterEach(cleanup)

type Pass = Usage['units'][number]['passes'][number]
const metric = (value: number | null) => ({
  value, known_attempts: value === null ? 0 : 1, unknown_attempts: value === null ? 1 : 0,
})
function pass(profile: string | null, model: string | null, input: number, amount: number | null,
              extra: Partial<Pass> = {}): Pass {
  return {
    pass_no: 3, profile, provider: 'codex', requested_model: model, reported_model: model,
    input_tokens: metric(input), cached_input_tokens: metric(input / 10),
    reasoning_output_tokens: metric(input / 100), output_tokens: metric(input / 10),
    elapsed_seconds: metric(1), attempts: [], result_status: 'checkpointed',
    physical_attempt_count: 2, provider_call_count: 2, unknown_provider_call_count: 0,
    failed_attempt_count: 1, retry_count: 1,
    cost: amount === null ? null : { amount, currency: 'USD', status: 'complete', estimate_type: 'api', note: null },
    ...extra,
  }
}
const pipeline = {
  units: [
    { id: 'c1', passes: { '3': { retained_count: 1, checkpoint_state: 'completed' } } },
    { id: 'c2', passes: { '3': { retained_count: 1, checkpoint_state: 'retained' } } },
    { id: 'c3', passes: {} },
  ],
} as Pipeline
const usage = {
  scope: 'project', warning: null, units: [
    { unit_id: 'c1', chapter_id: 'ch1', passes: [
      pass('codex-astra-medium', 'gpt-6-astra', 100, .1),
      pass('codex-sol-high', 'gpt-6.1-sol', 200, .2),
      pass('analysis-only', 'gpt-6-astra', 1000, 10, { pass_no: 1 }),
    ] },
    { unit_id: 'c2', chapter_id: 'ch1', passes: [
      pass('codex-astra-medium', 'gpt-6-astra', 300, .3),
    ] },
  ],
} satisfies Usage

describe('PTS recorded usage by profile and model', () => {
  it('shows the pass total followed by separate profiles, retaining tokens, costs and retry usage', () => {
    const { container } = render(<RecordedProviderUsage pipeline={pipeline} usage={usage} />)
    const rows = container.querySelectorAll('tbody')[1]!.querySelectorAll('tr')
    expect(rows).toHaveLength(3)
    const values = (index: number) => [...rows[index]!.children].map(cell => cell.textContent)
    expect(values(0)).toEqual(['P3', 'Total', '2/3 saved', '600', '60', '6', '60', '$0.6000'])
    expect(values(1)).toEqual(['↳ P3', 'codex-astra-mediumgpt-6-astra', '2 used', '400', '40', '4', '40', '$0.4000'])
    expect(values(2)).toEqual(['↳ P3', 'codex-sol-highgpt-6.1-sol', '1 used', '200', '20', '2', '20', '$0.2000'])
    expect(rows[1]!.children[2]!.getAttribute('title')).toContain('A chunk may appear in several rows')
    expect(rows[1]!.lastElementChild!.getAttribute('title')).toContain('retries')
    expect(container.textContent).not.toContain('analysis-only')
    // Retries contribute usage, but never multiply the number of chunks.
    expect(recordedProfileGroups(usage, 3).map(group => group.chunks)).toEqual([2, 1])
  })

  it('keeps the existing single-profile row and empty passes without redundant breakdown rows', () => {
    const only = { ...usage, units: [{ ...usage.units[0]!, passes: [usage.units[0]!.passes[0]!] }] }
    const { container } = render(<RecordedProviderUsage pipeline={pipeline} usage={only} />)
    expect(container.querySelectorAll('tbody tr')).toHaveLength(4)
    expect(container.querySelectorAll('.translate-provider-detail')).toHaveLength(0)
    const rows = container.querySelectorAll('tbody')[1]!.querySelectorAll('tr')
    expect(rows[0]!.children[1]!.textContent).toBe('codex-astra-medium')
    expect(rows[0]!.lastElementChild!.textContent).toBe('$0.1000')
    expect(recordedProfileGroups(undefined, 3)).toEqual([])
  })

  it('does not merge different models or providers using the same profile name', () => {
    const mixed = { ...usage, units: [{ unit_id: 'c1', chapter_id: 'ch1', passes: [
      pass('shared', 'model-a', 10, .1), pass('shared', 'model-b', 20, .2),
      pass('shared', 'model-a', 30, .3, { provider: 'openai' }),
    ] }] }
    const groups = recordedProfileGroups(mixed, 3)
    expect(groups).toHaveLength(3)
    expect(groups.map(group => group.usage.units[0]!.passes[0]!.input_tokens.value).sort()).toEqual([10, 20, 30])
    expect(new Set(groups.map(group => group.key)).size).toBe(3)
  })

  it('preserves unknown tokens and partial prices without assigning them to a known profile', () => {
    const unknown = pass(null, null, 50, null, { input_tokens: metric(null), provider_call_count: 0,
                                              unknown_provider_call_count: 1 })
    const mixed = { ...usage, units: [{ unit_id: 'c1', chapter_id: 'ch1',
                                      passes: [usage.units[0]!.passes[0]!, unknown] }] }
    const { container } = render(<RecordedProviderUsage pipeline={pipeline} usage={mixed} />)
    const rows = container.querySelectorAll('tbody')[1]!.querySelectorAll('tr')
    expect(rows).toHaveLength(3)
    expect(rows[0]!.children[3]!.textContent).toBe('100')
    expect(rows[0]!.lastElementChild!.getAttribute('title')).toContain('only known amounts')
    const unknownRow = [...rows].find(row => row.children[1]!.textContent!.includes('Unknown profile'))!
    expect(unknownRow.children[3]!.textContent).toBe('—')
    expect(unknownRow.lastElementChild!.textContent).toBe('—')
  })

  it('keeps currency-specific subtotals when the overall price cannot be added', () => {
    const eur = pass('euro-profile', 'other-model', 200, .2, {
      cost: { amount: .2, currency: 'EUR', status: 'complete', estimate_type: 'api', note: null },
    })
    const mixed = { ...usage, units: [{ unit_id: 'c1', chapter_id: 'ch1',
                                      passes: [usage.units[0]!.passes[0]!, eur] }] }
    const { container } = render(<RecordedProviderUsage pipeline={pipeline} usage={mixed} />)
    const rows = container.querySelectorAll('tbody')[1]!.querySelectorAll('tr')
    expect(rows[0]!.lastElementChild!.textContent).toBe('—')
    expect(rows[0]!.lastElementChild!.getAttribute('title')).toContain('different currencies')
    expect(rows[1]!.lastElementChild!.textContent).toBe('$0.1000')
    expect(rows[2]!.lastElementChild!.textContent).toBe('€0.2000')
  })

  it('counts a chunk once per profile and excludes models that never reached the provider', () => {
    const duplicate = { ...usage, units: [{ unit_id: 'c1', chapter_id: 'ch1', passes: [
      pass('used', 'model', 100, .1), pass('used', 'model', 200, .2),
      pass('never-submitted', 'model', 0, null, { provider_call_count: 0 }),
    ] }] }
    const groups = recordedProfileGroups(duplicate, 3)
    expect(groups).toHaveLength(1)
    expect(groups[0]!.chunks).toBe(1)
    expect(translationCost(groups[0]!.usage).text).toBe('$0.3000')
  })
})
