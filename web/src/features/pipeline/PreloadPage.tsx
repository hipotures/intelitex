import { useContext, useRef, useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { Play } from 'lucide-react'
import { endpoint, queryClient, reconcile, request, Scope, useApi } from '../../api/client'
import { jobSchema, preloadPreviewSchema, sourcePreloadSchema, type Pipeline, type PreloadTarget, type Profiles, type Usage, type Workspace } from '../../api/schema'
import { createRequestKey } from '../../api/requestKey'
import { useCommand } from '../../api/mutations'
import { Back, Button, Empty, ErrorNote, Overlay, Panel, ProfileSwatch } from '../../components/ui/common'
import { useConnection, useLive } from '../../realtime/coordinator'
import { preloadFields, preloadLabels, preloadLedger, preloadTotals, preloadModel, groupPreload, provisionalPreload, type PreloadRecord } from './preloadUsage'
import { usePreviewPosition } from './usePreviewPosition'
import { Activity } from './Workspace'
import { Action } from './Action'

const number = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString()
const reasons: Record<string,string> = { planning_unresolved: 'Exact analysis planning is unresolved; Play is unavailable.',
  saved_output: 'Consumers are satisfied by saved output; no preload required.', legacy_mode: 'This workspace uses legacy execution. P0 is not required.',
  evidence_unverifiable: 'Saved evidence cannot prove readiness. Inspect it locally.', recovery_required: 'Session requires local inspection before execution.' }
const statusLabels: Record<string, string> = { not_applicable: 'Not required', completed: 'Complete', accepted: 'Loaded',
  partial: 'Partially loaded', pending: 'Not started', running: 'Loading source', failed: 'Failed',
  error: 'Failed', unknown: 'Needs inspection', unverifiable: 'Needs inspection' }
const statusLabel = (state?: string) => statusLabels[state ?? ''] ?? 'Loading'
const short = (value: string | null) => value?.slice(0, 12) ?? '—'
const purpose = (target: PreloadTarget) => {
  const analysis = target.consumers.some(c => c.pass_no === 1)
  const translation = target.consumers.some(c => c.pass_no > 1)
  return analysis && translation ? 'Shared by analysis and translation' : analysis ? 'For analysis' : translation ? 'For translation' : 'Analysis planning unresolved'
}
const sourceSize = (target: PreloadTarget) => `${number(target.source_words)} words · ${number(target.source_utf8_bytes)} UTF-8 bytes`
const sourceUnit = (target: PreloadTarget) => target.consumers[0]?.unit_id ?? target.chapter_id
const sessionNotices: Record<string, { label: string; text: string }> = {
  consumer_active: { label: 'Later pass unfinished', text: 'A later pass is using this saved source session or was interrupted before finishing. P0 remains loaded. While the job is running, wait for it to finish. After an interruption, use Run in the workspace to resume saved work; another preload is unnecessary.' },
  cleanup_pending: { label: 'Result saved; cleanup pending', text: 'The result was saved, but the session still needs to return to its P0 baseline. Let the running job finish. After an interruption, use Run in the workspace to resume cleanup. The saved result must not be generated again.' },
  recovery_required: { label: 'Session recovery blocked', text: 'Saved session evidence requires recovery before another model call. P0 cannot run here. Preserve the session and use source-sessions inspect locally to identify the recovery hold; do not delete its files or repeat the preload.' },
}
const sessionNotice = (target: PreloadTarget) => sessionNotices[target.session_state]

function UsageTable({ groups }: { groups: ReturnType<typeof groupPreload> }) {
  return <div className="diagnostic-scroll"><table className="phase-detail-table usage-table"><thead><tr><th>Recorded identity</th><th className="num">Calls / unknown / failed</th>{preloadLabels.map(label => <th className="num" key={label}>{label}</th>)}<th className="num">Seconds</th><th className="num">Cost</th></tr></thead><tbody>
    {groups.map(group => <tr key={group.label}><td>{group.label}</td><td className="num">{group.totals.calls} / {group.totals.unknown} / {group.totals.failed}</td>{preloadFields.map(field => <td className="num" key={field}>{number(group.totals.tokens[field].value)}{group.totals.tokens[field].partial ? ' · partial' : ''}</td>)}<td className="num">{number(group.totals.tokens.elapsed_seconds.value)}</td><td className="num" title={group.totals.costNote}>{group.totals.cost}</td></tr>)}
    {!groups.length && <tr><td colSpan={8}>Not run yet</td></tr>}
  </tbody></table></div>
}

function Readiness({ target }: { target: PreloadTarget }) {
  const notice = sessionNotice(target)
  return <div className="analyse-preview-results preload-readiness"><h3>Readiness</h3><dl className="phase-kv">
    <dt>Source unit</dt><dd>{sourceUnit(target)}</dd>
    <dt>Use</dt><dd>{purpose(target)}</dd>
    <dt>Configured model / effort</dt><dd>{target.model ?? 'Unresolved'} · {target.effort ?? 'default'}</dd>
    <dt>P0 baseline</dt><dd>{statusLabel(target.baseline_state)}{target.acknowledgement ? ` · ${target.acknowledgement}` : ''}</dd>
    <dt>Session</dt><dd>{notice?.label ?? target.session_state.replaceAll('_',' ')}</dd>
    <dt>Model / effort</dt><dd>{target.reported_model ? `${target.reported_model} · ${target.reported_effort ?? 'default'}` : 'Applied selection not verified'}</dd>
    <dt>Generation</dt><dd>{target.generation ?? 'Not created'}</dd>
    <dt>Accepted</dt><dd>{target.accepted_at ?? 'Not run yet'}</dd>
    <dt>Selection verified</dt><dd>{target.selection_verified_at ?? '—'}</dd>
    <dt>Native checked</dt><dd>{target.last_verified_at ?? 'On use'}</dd>
  </dl><p className="subtitle">Saved evidence; native compatibility is checked on use. Readiness does not promise a provider cache hit.</p>
    {notice && <p className="notice preload-session-explanation" role="status">{notice.text}</p>}
    {target.reason && target.reason !== 'accepted' && !notice && <p className="notice" role="status">{reasons[target.reason] ?? 'Inspect saved source/session evidence locally.'}</p>}
    {target.baseline_state === 'pending' && <p className="preload-session-explanation">{target.consumers.some(c => c.pass_no === 1)
      ? 'Global Run prepares this source immediately before its P1 unit, in analysis order. Preload source can prepare it earlier without running P1.'
      : 'Global Run prepares this translation source when translation reaches it, after whole-book P1 and Review approval. Analysis can use a separate source range or model configuration. Preload source can prepare it earlier.'}</p>}
    <details><summary>Session diagnostics</summary><dl className="phase-kv"><dt>Scope</dt><dd>{target.scope_id ?? 'Planning unresolved'}</dd><dt>Slot</dt><dd>{target.slot_id ?? 'Not created'}</dd><dt>Thread</dt><dd>{target.thread_id ?? 'Not created'}</dd><dt>Later consumers</dt><dd>{target.consumers.map(c => `${c.unit_id} · P${c.pass_no}`).join(', ')}</dd></dl></details>
  </div>
}

export function PreloadPage({ id, workspace, pipeline, usage, profiles, usageError, pipelineError }: {
  id: string; workspace?: Workspace; pipeline?: Pipeline; usage?: Usage; profiles?: Profiles; usageError: unknown; pipelineError: unknown
}) {
  const context = useContext(Scope), connection = useConnection(), live = useLive()
  const navigate = useNavigate(), search = useSearch({ strict: false })
  const inventory = useApi(endpoint(id, 'source-preload'), sourcePreloadSchema, !!workspace?.prepared)
  const command = useCommand(id), latch = useRef(false), previewStack = usePreviewPosition(!!workspace?.prepared)
  const [confirmation, setConfirmation] = useState<{ target: PreloadTarget; revision: string } | null>(null)
  const [localError, setLocalError] = useState<unknown>(null), [unknown, setUnknown] = useState(false)
  const value = inventory.data
  const targets = value?.chapters.flatMap(c => c.targets) ?? []
  const linkedTarget = targets.find(t => t.target_id === search.preloadTarget)
  const [chosenView, setChosenView] = useState<'analysis' | 'translation' | null>(null)
  const view = chosenView ?? (linkedTarget && linkedTarget.relevance !== 'unresolved' && !linkedTarget.consumers.some(c => c.pass_no === 1) ? 'translation' : 'analysis')
  const inView = (target: PreloadTarget) => (view === 'analysis' && target.relevance === 'unresolved') || target.consumers.some(c => (c.pass_no === 1) === (view === 'analysis'))
  const chapters = value?.chapters.map(c => ({ ...c, targets: c.targets.filter(inView) })).filter(c => c.targets.length) ?? []
  const visibleTargets = chapters.flatMap(c => c.targets)
  const viewSummary = value?.views?.[view]
  const working = targets.find(t => t.baseline_state === 'running')
  const workingChapter = pipeline?.sections.find(s => s.passes['0']?.runtime_state === 'running')
  const running = !!working || pipeline?.source_preload?.state === 'running'
  const displayState = visibleTargets.some(t => t.baseline_state === 'running') ? 'running' : viewSummary?.state ?? value?.summary.state
  const loadedChapters = chapters.filter(c => c.targets.some(t => t.baseline_state === 'accepted')).length
  const requestedChapter = value?.chapters.find(c => c.chapter_id === search.chapter)
  const selected = visibleTargets.find(t => t.target_id === search.preloadTarget && (!search.chapter || t.chapter_id === search.chapter)) ??
    (requestedChapter ? visibleTargets.find(t => t.chapter_id === search.chapter) : visibleTargets[0])
  const stale = !!value && (!!search.preloadTarget && !targets.some(t => t.target_id === search.preloadTarget) ||
    !!search.chapter && !value.chapters.some(c => c.chapter_id === search.chapter))
  const previewPath = endpoint(id, `source-preload/targets/${selected?.target_id ?? ''}/preview`)
  const preview = useInfiniteQuery({ queryKey: [context, previewPath], initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => request(`${previewPath}?page=${pageParam}`, preloadPreviewSchema, { signal }),
    getNextPageParam: page => page.next_page ?? undefined, enabled: !!selected })
  const pages = preview.data?.pages ?? [], first = pages[0]
  const records = preloadLedger(usage), totals = preloadTotals(records)
  const provisional = provisionalPreload(records, pipeline?.active_job, live.state.activity[id] ?? [])
  const disabled = command.disabled || !!pipeline?.busy || !!workspace?.metadata.lifecycle.archived || !!workspace?.active_job || connection !== 'Live'
  const language = (() => { try { return new Intl.DisplayNames(['en'], { type: 'language' }).of(workspace?.metadata.source_language ?? workspace?.metadata.language ?? '') ?? 'Source' } catch { return 'Source' } })()
  const title = (chapter: string | null) => value?.chapters.find(c => c.chapter_id === chapter)?.title ?? chapter ?? 'Unassigned historical usage'
  const historyTitle = (chapter: string | null) => {
    const index = value?.chapters.findIndex(c => c.chapter_id === chapter) ?? -1
    return index < 0 ? 'Unassigned historical usage' : `${value!.chapters[index]!.title ?? 'Untitled section'} · section ${index + 1}`
  }
  const swatch = (name: string) => <span className="phase-model" key={name}><ProfileSwatch index={profiles?.profiles.find(p => p.name === name)?.stable_palette_index} name={name} />{name}</span>
  const select = (target: PreloadTarget) => {
    if (!inView(target)) setChosenView(target.consumers.some(c => c.pass_no === 1) ? 'analysis' : 'translation')
    void navigate({ to: '.', search: old => ({ ...old, chapter: target.chapter_id, preloadTarget: target.target_id }) })
  }
  const unitLabel = (r: PreloadRecord) => `${historyTitle(r.chapter)} · ${short(r.scope)} · ${preloadModel(r)} · slot ${short(r.slot_id ?? null)} · generation ${r.generation ?? 'unknown'}`

  async function run() {
    if (!confirmation || disabled || latch.current || unknown) return
    latch.current = true; setLocalError(null)
    const storageKey = `intelitex.pending.${context}.${id}.preload.${confirmation.target.target_id}`
    let key: string | undefined
    let pendingRevision: string | undefined
    try {
      // Recover a pending receipt before comparing revisions: an earlier accepted
      // job legitimately changes readiness while its HTTP reply can be lost.
      try {
        const saved = sessionStorage.getItem(storageKey)
        if (saved) {
          const pending: unknown = JSON.parse(saved)
          if (pending && typeof pending === 'object' && 'key' in pending && typeof pending.key === 'string' &&
              'revision' in pending && typeof pending.revision === 'string') {
            key = pending.key; pendingRevision = pending.revision
          }
        }
      } catch { /* Optional storage. */ }
      if (key) {
        try {
          const receipt = await request(`/api/requests/${encodeURIComponent(key)}`, jobSchema)
          if (receipt.workspace_id === id && receipt.operation === 'preload') {
            try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
            setConfirmation(null); void reconcile(id); return
          }
        } catch { /* The idempotent POST also resolves its existing receipt. */ }
        if (pendingRevision !== confirmation.revision) {
          setUnknown(true); setLocalError(new Error('An earlier P0 request has an unknown outcome. Its receipt must be resolved before a changed intent can run.')); return
        }
      }
      const latest = await request(endpoint(id, 'source-preload'), sourcePreloadSchema)
      queryClient.setQueryData([context, endpoint(id, 'source-preload')], latest)
      const target = latest.chapters.flatMap(c => c.targets).find(t => t.target_id === confirmation.target.target_id)
      if (latest.intent_revision !== confirmation.revision || !target?.can_run) {
        setLocalError(new Error('P0 source or configuration changed. Close this dialog and inspect the current target.')); return
      }
      key ??= createRequestKey()
      try { sessionStorage.setItem(storageKey, JSON.stringify({ key, revision: confirmation.revision })) } catch { /* Optional storage. */ }
      const result = await command.send(endpoint(id, 'jobs'), jobSchema, { operation: 'preload',
        preload_target_id: target.target_id, expected_preload_revision: confirmation.revision, request_key: key }, 'POST', true)
      if (result) {
        try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
        setConfirmation(null); void reconcile(id)
      } else if (command.unknownOutcome()) {
        try {
          const receipt = await request(`/api/requests/${encodeURIComponent(key)}`, jobSchema)
          if (receipt.workspace_id !== id || receipt.operation !== 'preload') throw new Error('Receipt mismatch')
          try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
          command.clearError(); setConfirmation(null); void reconcile(id)
        } catch { setUnknown(true) }
      } else { try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ } }
    } catch (error) { setLocalError(error) }
    finally { latch.current = false }
  }

  return <><Back id={id} /><div className="phase-detail-head"><div className="phase-detail-title"><div className="eyebrow">Preload · {workspace?.metadata.title ?? 'Workspace'}</div><h1>Preload · P0</h1><div className="phase-detail-meta">{value ? `${view === 'analysis' ? 'Analysis units · same order as P1' : 'Translation fragments'} · ${visibleTargets.length} units` : 'Source preload and session readiness'}</div></div><span className={`phase-status-badge ${displayState === 'completed' ? 'done' : displayState === 'running' ? 'active' : displayState ?? ''}`}>{statusLabel(displayState)}</span></div>
    <ErrorNote error={inventory.error ?? pipelineError ?? usageError} retry={() => void inventory.refetch()} />
    {connection !== 'Live' && <div className="notice" role="status">{connection}. Showing the last available snapshot; Run is unavailable.</div>}
    {value && <p className="subtitle preload-freshness" title={`Saved evidence observed ${new Date(value.observed_at).toLocaleString()}; native compatibility is checked on use.`}>P0 prepares source text only. {pipeline?.approved ? 'Translation follows its normal pass order.' : 'Translation waits for whole-book analysis and Review approval.'}</p>}
    <div className="preload-view-choice"><label>Preload list <select aria-label="Preload list" value={view} onChange={event => setChosenView(event.target.value as 'analysis' | 'translation')}><option value="analysis">Analysis · same units as P1</option><option value="translation">Translation · later fragments</option></select></label><p className="subtitle">{view === 'analysis' ? 'Same units and order as P1. Global Run prepares each unit immediately before analysing it.' : 'Prepared when translation reaches each fragment, after whole-book P1 and Review approval.'}</p></div>
    {value && <div className="preload-progress" role="status"><strong>{viewSummary?.accepted ?? visibleTargets.filter(t => t.baseline_state === 'accepted').length}/{viewSummary?.required ?? visibleTargets.filter(t => t.relevance === 'current').length} preloads loaded</strong><span>{loadedChapters} chapters with loaded sources</span>
      {working ? <button onClick={() => select(working)}>Loading: {sourceUnit(working)} · {title(working.chapter_id)}</button> : running && <strong>Loading source{workingChapter ? `: ${workingChapter.id} · ${workingChapter.title ?? ''}` : '…'}</strong>}
    </div>}
    {stale && <div className="notice" role="status">The selected source target is no longer current. Showing an available source target.</div>}
    {!workspace?.prepared ? <Panel debugId="PNU" title={workspace ? 'Prepare required' : 'Workspace unavailable'}><p className="subtitle">{workspace ? 'Run Prepare from the workspace to inspect its frozen source and preload targets.' : 'Workspace not found.'}</p>{workspace && <Action workspace={workspace} />}</Panel> : <>
      <div className="phase-metrics p1-metrics">{preloadFields.map((field, i) => {
        const saved = totals.tokens[field], liveValue = provisional?.[field]
        const displayed = liveValue == null ? saved.value : (saved.value ?? 0) + liveValue
        return <div className="phase-metric" key={field}><div className="phase-metric-label">{preloadLabels[i]}</div><div className={`phase-metric-value${provisional ? ' p1-counting' : ''}`} title={provisional ? 'Correlated provisional P0 snapshot plus other persisted attempts; reconciles after completion.' : saved.value == null ? 'Not run yet, or recorded measurement unavailable.' : saved.partial ? 'Known subtotal; some P0 attempts have unavailable usage.' : 'Recorded pass 0 only.'}>{number(displayed)}{saved.partial ? <small>partial</small> : null}</div></div>
      })}<div className="phase-metric"><div className="phase-metric-label">Cost</div><div className="phase-metric-value" title={totals.costNote}>{totals.cost}</div></div></div>
      {value && !value.applicable && <div className="notice">{reasons.legacy_mode}</div>}
      <div className="analyse-content"><div className="phase-detail-grid translate-detail-grid analyse-work-grid preload-work-grid"><div className="phase-detail-stack">
        <Panel debugId="PNU" title={view === 'analysis' ? 'Analysis units' : 'Translation fragments'}><span className="translate-working-announce" role="status">{value?.summary.state === 'running' ? 'Source preload P0 is running' : ''}</span><div className="diagnostic-scroll"><table className="phase-detail-table analyse-unit-table"><thead><tr><th>Section / unit</th><th>P0</th></tr></thead><tbody>{chapters.map(chapter => chapter.targets.map(target => {
          const state = target.baseline_state, working = state === 'running'
          const symbol = state === 'accepted' ? '✓' : state === 'failed' ? '×' : state === 'unverifiable' || target.relevance === 'satisfied' ? '—' : '○'
          const unit = sourceUnit(target)
          const multipleConfigs = chapter.targets.filter(t => sourceUnit(t) === unit).length > 1
          return <tr key={target.target_id} className={`${selected?.target_id === target.target_id ? 'selected' : ''} ${state === 'accepted' ? 'finished' : ''}${working ? ' working' : ''}`}><td><button className="translate-chunk-choice" title={`${target.label} · ${purpose(target)} · ${target.model ?? 'Configuration unresolved'} · ${target.effort ?? 'default'} effort`} onClick={() => select(target)}>{chapter.title ?? chapter.chapter_id}<small><span className="preload-unit-id">{unit}</span> · {sourceSize(target)}</small>{multipleConfigs && <small>{target.model ?? 'Configuration unresolved'} · {target.effort ?? 'default'}</small>}</button>{!['clean','not_created'].includes(target.session_state) && <details className="preload-session-warning"><summary>{sessionNotice(target)?.label ?? 'Session details'}</summary><p>{sessionNotice(target)?.text ?? 'Select this row to read its saved session state.'}</p></details>}</td>
            <td><span className={`preload-state-label ${state}`}>{target.relevance === 'satisfied' ? 'Not required' : statusLabel(state)}</span><div className="translate-pass-cell"><button className={`translate-pass-state ${state === 'accepted' ? 'completed' : state}${working ? ' working' : ''}`} aria-label={`${chapter.title ?? chapter.chapter_id}, ${unit}, ${target.label}, ${purpose(target)}, ${target.model} ${target.effort ?? 'default'} P0: ${state}; preview`} aria-pressed={selected?.target_id === target.target_id} title={`${state}; ${target.session_state}; saved evidence / native check on use`} onClick={() => select(target)}>{working ? '◌' : symbol}</button><button className="translate-pass-run preload-source-run" aria-label={`Preload source for ${chapter.title ?? chapter.chapter_id}, ${unit}, ${target.label} P0, ${purpose(target)}, ${target.model} ${target.effort ?? 'default'} effort`} title={target.can_run ? state === 'accepted' ? 'Reuse accepted P0; no new model turn.' : 'Preload this source/model target only; uses model tokens.' : reasons[target.reason ?? ''] ?? 'Not required or inspection needed.'} disabled={disabled || !target.can_run} onClick={() => { select(target); setLocalError(null); setUnknown(false); setConfirmation({ target, revision: value!.intent_revision }) }}><Play size={13} /></button></div></td></tr>
        }))}</tbody></table>{!value && <Empty>Reading source inventory…</Empty>}</div></Panel>
      </div><div className="phase-detail-stack translate-preview-stack" ref={previewStack}><Panel debugId="PNV" title={`P0 preview${selected ? ` · ${sourceUnit(selected)}` : ''}`}><div className="translate-pass-preview">
        {!selected ? <Empty>{requestedChapter?.reason ?? (value?.summary.unresolved ? 'Analysis planning is unresolved.' : 'No applicable P0 target. Source remains available in Prepare.')}</Empty> : <><div className="prepare-preview-kicker">{title(selected.chapter_id)}</div><ErrorNote error={preview.error} retry={() => void preview.refetch()} /><p className="subtitle" role={preview.isPending ? 'status' : undefined}>{preview.isPending ? 'Reading source preview…' : selected.baseline_state === 'running' ? 'Source preload in progress…' : selected.baseline_state === 'accepted' ? 'Recorded immutable P0 source and saved readiness.' : first?.reason ?? (selected.baseline_state === 'pending' ? 'Source to preload — P0 has not run' : statusLabel(selected.baseline_state))}</p>
          {first && <div className="translate-preview-scroll"><div className="translate-preview-head"><h3>{language}</h3><h3>Source preload session</h3></div><div className="preload-preview-pair"><div>{pages.flatMap(page => page.source).map((block, index) => <div className="translate-preview-block" key={`${block.id}-${index}`}><div className="translate-preview-block-label">{block.id}</div><div className="translate-preview-text">{block.text}</div></div>)}</div><Readiness target={selected} /></div>
            {preview.hasNextPage && <div className="translate-preview-more"><span>{pages.reduce((n,p) => n + p.source.length,0)} blocks shown</span><Button disabled={preview.isFetchingNextPage} onClick={() => void preview.fetchNextPage()}>Load next 5 blocks</Button></div>}</div>}{pages.some(p => p.truncated) && <p className="subtitle">Some very long source blocks were shortened.</p>}
        </>}
      </div></Panel></div>
        <Panel debugId="PNC" title="P0 costs and tokens"><details className="analyse-usage-details"><summary>Show details</summary><ErrorNote error={usageError} />
          <h3 className="translate-summary-heading">Current inherited assignments</h3><div className="diagnostic-scroll"><table className="phase-detail-table translate-model-grid"><thead><tr><th>Source / consumer</th><th>Inherited profile</th><th>Provider / model / effort</th></tr></thead><tbody>{value?.assignments.map(a => <tr key={a.pass_no}><td>Default P{a.pass_no}</td><td>{swatch(a.profile)}</td><td>{a.provider} · {a.model} · {a.effort ?? 'default'}</td></tr>)}{targets.filter(t => t.consumers.length).map(t => <tr key={t.target_id}><td>{title(t.chapter_id)} · {t.label}<small>{purpose(t)}</small></td><td>{t.profiles.map(swatch)}</td><td>{t.provider} · {t.model} · {t.effort ?? 'default'}</td></tr>)}</tbody></table></div>
          <h3 className="translate-summary-heading">Recorded P0 summary</h3><p className="subtitle">{records.length} physical attempts · {totals.calls} submitted · {totals.unknown} unknown submission · {totals.unsubmitted} unsubmitted · {totals.failed} failed · {new Set(records.map(r => r.scope)).size} historical source scopes · {value?.summary.accepted ?? 0} current accepted baselines.</p><UsageTable groups={records.length ? [{ label: 'Lifetime P0 · all profiles/models', records, totals }] : []} />
          <h3 className="translate-summary-heading">Usage by model/profile</h3><UsageTable groups={groupPreload(records, r => `${preloadModel(r)} · profile ${r.profile ?? 'unknown'} · requested ${r.requested ?? 'unknown'}`)} />
          <h3 className="translate-summary-heading">Usage by chapter</h3><UsageTable groups={groupPreload(records, r => historyTitle(r.chapter))} />
          <h3 className="translate-summary-heading">Usage by source unit/session</h3><UsageTable groups={groupPreload(records, unitLabel)} />
          {groupPreload(records, unitLabel).map(g => <details className="preload-attempts" key={g.label}><summary>{g.label} · {g.records.length} physical attempts</summary><UsageTable groups={g.records.map(r => ({ label: `${r.attempt_id} · ${r.generation_status}/${r.validation_status} · ${r.usage_status ?? 'unknown usage'}`, records: [r], totals: preloadTotals([r]) }))} /></details>)}
          {!!value?.history.length && <><h3 className="translate-summary-heading">Retained session history</h3>{value.history.map((h, i) => <p className="subtitle" key={h.slot_id ?? i}>{title(h.chapter_id)} · {short(h.scope_id)} · {h.model} · {h.effort} · generation {h.generation} · {h.state}</p>)}{value.history_truncated && <p className="subtitle">Showing the first 100 retained sessions; complete counts are in the summary.</p>}</>}
          <p className="translate-usage-hint">Lifetime P0 includes failed attempts and retained generations. Cache is part of input; reasoning is part of output. These columns are not added together. Costs are API-equivalent estimates, not subscription charges.</p>
        </details></Panel>
      </div><Activity id={id} title="Recent execution" preloadOnly />{pipeline?.active_job && workspace && <Action workspace={workspace} pipeline={pipeline} />}</div>
    </>}
    {confirmation && <Overlay title="Run this P0 unit?" debugId="PNM" compact close={() => { if (!command.pending) setConfirmation(null) }}><div className="modal-body"><p><strong>{title(confirmation.target.chapter_id)} · {sourceUnit(confirmation.target)} · P0</strong></p><p>Preload one source/session target and stop at its readiness checkpoint. This uses model tokens when P0 is missing.</p><p>Inherited profiles: <strong>{confirmation.target.profiles.join(', ')}</strong> · {confirmation.target.model} · {confirmation.target.effort ?? 'default effort'}</p><p>{title(confirmation.target.chapter_id)} · {confirmation.target.label} · {sourceSize(confirmation.target)}.</p><p>{purpose(confirmation.target)}. This action runs P0 only. Translation still requires completed whole-book analysis and Review approval.</p><p className="subtitle">Checks the complete P0 request bound. Future consumer memory/output will be checked when that pass starts.</p><ErrorNote error={localError ?? command.error} />{unknown && <p className="notice">Request outcome unknown. Check the job receipt before trying again.</p>}</div><div className="modal-foot"><Button onClick={() => setConfirmation(null)}>Cancel</Button><Button variant="primary" disabled={disabled || unknown} onClick={() => void run()}>{command.pending ? 'Starting…' : 'Run P0 unit'}</Button></div></Overlay>}
  </>
}
