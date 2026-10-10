import { describe, expect, it } from 'vitest'
import { eventSchema, jobSchema, sourcePreloadSchema, usageSchema } from '../../api/schema'
import { groupPreload, preloadLedger, preloadModel, preloadTotals, provisionalPreload } from './preloadUsage'

const aggregate = (value: number | null) => ({ value, known_attempts: value == null ? 0 : 1, unknown_attempts: value == null ? 1 : 0 })
const attempt = (id: string, model: string | null, input: number | null, price: number | null = .1, contacted: boolean | null = true) => ({
  attempt_id: '001', physical_record_id: id, attempt_number: 1, reported_model: model, requested_model: 'requested-model',
  reported_effort: 'high', requested_effort: 'high', selection_status: model ? 'verified' : 'held', scope_id: 'scope', slot_id: id, generation: 1,
  generation_status: 'completed', validation_status: 'passed', acceptance_status: 'checkpointed', provider_contacted: contacted,
  usage_status: input == null ? 'unavailable' : 'reported', input_tokens: input, cached_input_tokens: input == null ? null : 0,
  cache_write_input_tokens: input == null ? null : 5, reasoning_output_tokens: 2, output_tokens: 5, total_tokens: input == null ? null : input + 5,
  elapsed_seconds: 2, cost: price == null ? null : { amount: price, currency: 'USD', estimate_type: 'api', status: 'complete', note: null },
})
const group = (attempts: ReturnType<typeof attempt>[], profile = 'profile', pass_no = 0) => ({ pass_no, profile, provider: 'codex', requested_model: 'requested-model',
  reported_model: 'multiple', attempts, task_key: 'pass0/scope', input_tokens: aggregate(300), cached_input_tokens: aggregate(0),
  reasoning_output_tokens: aggregate(4), output_tokens: aggregate(10), elapsed_seconds: aggregate(4), cost: null,
  result_status: 'completed', physical_attempt_count: attempts.length, provider_call_count: attempts.filter(a => a.provider_contacted === true).length,
  unknown_provider_call_count: 0, failed_attempt_count: 0, retry_count: 0 })
const usage = (groups: ReturnType<typeof group>[]) => usageSchema.parse({ scope: 'physical', warning: null,
  units: [{ unit_id: 'scope', chapter_id: 'ch1', passes: groups }] })

describe('P0 physical ledger', () => {
  it('keeps retained history when the server redacts an unsafe or unknown chapter identity', () => {
    const history = sourcePreloadSchema.shape.history.parse([{ slot_id: null, scope_id: null, chapter_id: null,
      generation: null, model: null, effort: null, state: 'unverifiable', reason: 'Retained evidence.' }])
    expect(history).toHaveLength(1)
    expect(history[0]!.chapter_id).toBeNull()
  })
  it('retains every scope/profile group and splits actual applied models, counting one shared record once', () => {
    const a = attempt('physical-a', 'model-a', 100), b = attempt('physical-b', 'model-b', 200)
    const records = preloadLedger(usage([group([a,b]), group([a], 'alias'), group([attempt('p1','ignored',900)],'p1',1)]))
    expect(records).toHaveLength(2)
    expect(preloadTotals(records).tokens.input_tokens.value).toBe(300)
    expect(preloadTotals(records).tokens.total_tokens.value).toBe(310)
    expect(preloadTotals(records).cost).toBe('$0.2000')
    const models = groupPreload(records, preloadModel)
    expect(models).toHaveLength(2)
    expect(models.every(g => !g.label.includes('multiple'))).toBe(true)
    expect(models.reduce((sum,g) => sum + g.totals.tokens.input_tokens.value!,0)).toBe(300)
    for (const key of [(r: typeof records[number]) => r.chapter ?? 'Unassigned historical usage', (r: typeof records[number]) => r.scope]) {
      expect(groupPreload(records, key).reduce((sum,g) => sum + g.totals.tokens.input_tokens.value!,0)).toBe(300)
    }
  })
  it('distinguishes never run, zero, unknown, partial, unsubmitted and incompatible prices', () => {
    expect(preloadTotals([]).tokens.input_tokens.value).toBeNull()
    expect(preloadTotals(preloadLedger(usage([group([attempt('zero','model',0,0)])]))).tokens.input_tokens.value).toBe(0)
    const records = preloadLedger(usage([group([attempt('known','model',100), attempt('unknown',null,null,null,null), attempt('unsent',null,null,null,false)])]))
    expect(preloadTotals(records).tokens.input_tokens).toEqual({ value:100, partial:true })
    expect(preloadTotals(records).unknown).toBe(1)
    expect(preloadTotals(records).unsubmitted).toBe(1)
    expect(preloadTotals(records).cost).toBe('$0.1000 · partial')
    expect(preloadModel(records[1]!)).toContain('requested only / unverified')
    records[1]!.cost = { amount: .2, currency:'EUR',estimate_type:'api',status:'complete',note:null }
    expect(preloadTotals(records).cost).toBe('—')
    records[1]!.cost.currency = 'USD'; records[1]!.cost.estimate_type = 'different'
    expect(preloadTotals(records).cost).toBe('—')
  })
  it('preserves history and an unassigned chapter without relabelling recorded selection', () => {
    const parsed = usage([group([attempt('old','historical-model',10)], 'old-profile')])
    parsed.units[0]!.chapter_id = null
    const records = preloadLedger(parsed)
    expect(preloadModel(records[0]!)).toContain('historical-model')
    expect(groupPreload(records, r => r.chapter ?? 'Unassigned historical usage')[0]!.label).toBe('Unassigned historical usage')
  })
})

describe('correlated provisional P0 snapshots', () => {
  const active = jobSchema.parse({ job_id:'current', workspace_id:'w',operation:'analyze',state:'running',sequence:1,
    started_at:null,finished_at:null,last_event:null,error:null })
  const event = (id: number, kind: string, input = 0, job_id = 'current') => eventSchema.parse({ id,job_id,workspace_id:'w',sequence:id,timestamp:'now',
    event:{kind,values:{pass_no:0,scope_id:'scope',slot_id:'physical-live',physical_record_id:'physical-live',chapter_id:'ch1',input_tokens:input}} })
  it('takes the latest cumulative snapshot and drops it after persistence, completion, or foreign activity', () => {
    const events = [event(1,'source_preload_started'), event(2,'provider_usage_update',10), event(3,'provider_usage_update',20)]
    expect(provisionalPreload([],active,events)?.input_tokens).toBe(20)
    const persisted = preloadLedger(usage([group([attempt('physical-live','model',20)])]))
    expect(provisionalPreload(persisted,active,events)).toBeNull()
    expect(provisionalPreload([],active,[...events,event(4,'source_preload_completed')])).toBeNull()
    expect(provisionalPreload([],active,[event(1,'source_preload_started'),event(2,'provider_usage_update',20,'old')])).toBeNull()
    expect(provisionalPreload([], {...active,state:'succeeded'},events)).toBeNull()
  })
})
