import type { Envelope, Job, Usage } from '../../api/schema'
import { formatCost } from './p1Cost'

export const preloadFields = ['input_tokens', 'cached_input_tokens', 'reasoning_output_tokens', 'output_tokens'] as const
export const preloadLabels = ['Input', 'Cache', 'Reason', 'Output'] as const
type Attempt = Usage['units'][number]['passes'][number]['attempts'][number]
export type PreloadRecord = Attempt & { key: string; chapter: string | null; scope: string;
  provider: string | null; profile: string | null; requested: string | null }

export function preloadLedger(usage?: Usage): PreloadRecord[] {
  const result = new Map<string, PreloadRecord>()
  for (const unit of usage?.units ?? []) for (const [groupIndex, group] of unit.passes.entries()) {
    if (group.pass_no !== 0) continue
    for (const [index, attempt] of group.attempts.entries()) {
      const key = attempt.physical_record_id ?? `${unit.unit_id}:${group.task_key ?? groupIndex}:${group.profile}:${attempt.attempt_id}:${index}`
      result.set(key, { ...attempt, key, chapter: unit.chapter_id, scope: attempt.scope_id ?? unit.unit_id,
        provider: group.provider, profile: group.profile, requested: attempt.requested_model ?? group.requested_model })
    }
  }
  return [...result.values()]
}

export function preloadTotals(records: PreloadRecord[]) {
  const contacted = records.filter(r => r.provider_contacted !== false)
  const values = [...preloadFields, 'elapsed_seconds', 'total_tokens', 'cache_write_input_tokens'] as const
  const tokens = Object.fromEntries(values.map(field => {
    const known = contacted.map(r => r[field]).filter((v): v is number => v != null)
    return [field, { value: known.length ? known.reduce((sum, v) => sum + v, 0) : null,
      partial: contacted.some(r => r[field] == null) }]
  })) as Record<typeof values[number], { value: number | null; partial: boolean }>
  const priced = contacted.map(r => r.cost).filter(c => c?.amount != null && c.currency)
  const compatible = new Set(priced.map(c => `${c!.currency}:${c!.estimate_type}`)).size === 1
  const partial = priced.length < contacted.length || priced.some(c => c?.status !== 'complete')
  const amount = compatible && priced.length ? priced.reduce((sum, c) => sum + c!.amount!, 0) : null
  return { tokens, amount, cost: amount == null ? '—' : formatCost(amount, priced[0]!.currency!) + (partial ? ' · partial' : ''),
    costNote: !contacted.length ? 'Not run yet' : !priced.length ? 'Usage or saved pricing unavailable.' : !compatible ? 'Different currencies or estimate types cannot be added.' : partial ? 'Only known amounts; some usage/prices are unavailable.' : 'Recorded P0 API-equivalent estimate; not a subscription invoice.',
    calls: records.filter(r => r.provider_contacted === true).length,
    unknown: records.filter(r => r.provider_contacted == null).length,
    unsubmitted: records.filter(r => r.provider_contacted === false).length,
    failed: records.filter(r => r.generation_status === 'failed' || r.validation_status === 'failed').length }
}

export function groupPreload(records: PreloadRecord[], key: (record: PreloadRecord) => string) {
  const groups = new Map<string, PreloadRecord[]>()
  for (const record of records) { const label = key(record); groups.set(label, [...(groups.get(label) ?? []), record]) }
  return [...groups].map(([label, records]) => ({ label, records, totals: preloadTotals(records) }))
}

export function preloadModel(record: PreloadRecord) {
  return record.selection_status === 'verified' && record.reported_model
    ? `${record.provider ?? 'Unknown provider'} · ${record.reported_model} · ${record.reported_effort ?? 'default effort'}`
    : `${record.provider ?? 'Unknown provider'} · ${record.requested ?? 'Unknown model'} · ${record.requested_effort ?? 'default effort'} (requested only / unverified)`
}

export function provisionalPreload(records: PreloadRecord[], active: Job | null | undefined, events: Envelope[]) {
  if (!active || !['starting','running','stopping'].includes(active.state)) return null
  const owned = events.filter(e => e.job_id === active.job_id && e.workspace_id === active.workspace_id)
  const boundary = [...owned].reverse().find(e => ['source_preload_started','source_preload_completed','preload_target_completed','pass_started'].includes(e.event.kind))
  if (boundary?.event.kind !== 'source_preload_started') return null
  const event = [...owned].reverse().find(e => e.event.kind === 'provider_usage_update' && e.event.values.pass_no === 0 &&
    e.event.values.scope_id === boundary.event.values.scope_id && e.event.values.slot_id === boundary.event.values.slot_id)
  if (!event || event.id <= boundary.id) return null
  const v = event.event.values
  if (typeof v.physical_record_id !== 'string' || records.some(r => r.key === v.physical_record_id)) return null
  // Last correlated snapshot replaces the prior live snapshot; it is never
  // summed on every tick or added to the persisted copy of this physical turn.
  return { key: v.physical_record_id, scope: String(v.scope_id), slot_id: String(v.slot_id), chapter: String(v.chapter_id),
    ...Object.fromEntries(preloadFields.map(field => [field, typeof v[field] === 'number' ? v[field] : null])) } as Pick<PreloadRecord, 'key'|'scope'|'slot_id'|'chapter'> & Record<typeof preloadFields[number], number | null>
}
