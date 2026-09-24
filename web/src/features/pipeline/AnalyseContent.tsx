import { useContext, useLayoutEffect, useRef, useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Play } from 'lucide-react'
import { endpoint, queryClient, reconcile, request, Scope } from '../../api/client'
import { analysisUnitPreviewSchema, jobSchema, pipelineSchema, type AnalysisUnitPreview, type Pipeline, type Profiles, type Usage } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { createRequestKey } from '../../api/requestKey'
import { Button, Empty, ErrorNote, Overlay, Panel, ProfileSwatch } from '../../components/ui/common'
import { useConnection, useLive } from '../../realtime/coordinator'
import { Activity } from './Workspace'
import { displayCost, p1Cost } from './p1Cost'
import { sectionTitles } from './sectionTitles'

const tokenFields = ['input_tokens', 'cached_input_tokens', 'reasoning_output_tokens', 'output_tokens'] as const
const tokenLabels = ['Input', 'Cache', 'Reason', 'Output']

function P1PreviewPage({ page }: { page: AnalysisUnitPreview }) {
  return <>{page.source.map(block => {
    const terms = page.terms.filter(item => item.evidence.includes(block.id))
    const observations = page.observations.filter(item => item.evidence.includes(block.id))
    return <div className="translate-preview-block" key={block.id}>
      <div className="translate-preview-block-label"><span>{block.id}</span></div>
      <div className={`translate-preview-pair${page.available ? '' : ' source-only'}`}>
        <div className="translate-preview-text">{block.text}</div>
        {page.available && <div className="analyse-preview-results">
          {!terms.length && !observations.length && <span className="subtitle">No new terms or observations for this block.</span>}
          {terms.map((term, index) => <div className="analyse-preview-result" key={`term-${index}`}>
            <strong>{term.source}</strong><span className="analyse-preview-meta">term · {term.category} · {term.confidence}</span>
            {term.aliases.length > 0 && <p>Aliases: {term.aliases.join(', ')}</p>}
            {term.meaning && <p>{term.meaning}</p>}
            {term.candidates.map((candidate, candidateIndex) => <p key={candidateIndex}><b>{candidate.text}</b>{candidate.reason ? ` · ${candidate.reason}` : ''}</p>)}
          </div>)}
          {observations.map((item, index) => <div className="analyse-preview-result" key={`observation-${index}`}>
            <strong>{item.kind}</strong><span className="analyse-preview-meta">observation · {item.confidence}</span>
            {item.about.length > 0 && <p>About: {item.about.join(', ')}</p>}
            <p>{item.statement}</p>
          </div>)}
        </div>}
      </div>
    </div>
  })}</>
}

export function AnalyseContent({ id, pipeline, usage, profiles, usageError }: {
  id: string; pipeline: Pipeline; usage?: Usage; profiles?: Profiles; usageError: unknown
}) {
  const scope = useContext(Scope)
  const command = useCommand(id)
  const connection = useConnection()
  const live = useLive()
  const previewStack = useRef<HTMLDivElement>(null)
  const runLatch = useRef(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [pendingRun, setPendingRun] = useState<string | null>(null)
  const [localError, setLocalError] = useState<unknown>(null)
  const [unknownOutcome, setUnknownOutcome] = useState(false)
  const [startedTarget, setStartedTarget] = useState<{ unitId: string; jobId: string | null } | null>(null)
  const unit = pipeline.analysis.units.find(item => item.id === selectedId) ?? pipeline.analysis.units[0]
  const nextUnit = pipeline.analysis.units.find(item => item.state !== 'completed')
  const titles = sectionTitles(pipeline.sections, pipeline.metadata.creators)
  const previewPath = endpoint(id, `analysis/units/${encodeURIComponent(unit?.id ?? '')}/preview`)
  const preview = useInfiniteQuery({ queryKey: [scope, previewPath],
    queryFn: ({ pageParam, signal }) => request(`${previewPath}?page=${pageParam}`, analysisUnitPreviewSchema, { signal }),
    initialPageParam: 0, getNextPageParam: page => page.next_page ?? undefined, enabled: !!unit })
  const previewPages = preview.data?.pages ?? []
  const firstPreview = previewPages[0]
  const active = pipeline.active_job?.operation === 'analyze' ? pipeline.active_job : null
  const liveActive = active ? live.state.jobs[active.job_id] : null
  const activeEvent = active && [...(live.state.activity[id] ?? [])].reverse().find(event =>
    event.job_id === active.job_id && typeof event.event.values.unit_id === 'string')
  const activeValues = activeEvent?.event.values ?? active?.last_event?.event.values
  const activeUnitId = active && !['succeeded', 'failed', 'cancelled', 'abandoned'].includes(liveActive?.state ?? '') &&
    typeof activeValues?.unit_id === 'string' ? activeValues.unit_id : null
  const startedJob = startedTarget?.jobId ? live.state.jobs[startedTarget.jobId] : null
  const startedFinished = startedTarget?.jobId &&
    (['succeeded', 'failed', 'cancelled', 'abandoned'].includes(startedJob?.state ?? '') ||
      pipeline.last_job?.job_id === startedTarget.jobId && ['succeeded', 'failed', 'cancelled', 'abandoned'].includes(pipeline.last_job.state))
  const workingId = activeUnitId ?? (!startedFinished ? startedTarget?.unitId : null)
  const disabled = command.disabled || pipeline.busy || pipeline.metadata.lifecycle.archived || connection !== 'Live'
  const sourceLanguage = (() => { try { return new Intl.DisplayNames(['en'], { type: 'language' }).of(pipeline.metadata.source_language ?? pipeline.metadata.language ?? '') ?? 'Source' } catch { return 'Source' } })()
  const selectedProfile = profiles?.resolved_passes['1']
  const overriddenSections = pipeline.sections.filter(section => section.processing === 'full' && !!section.profiles['1'])
  const recordedP1 = usage?.units.flatMap(item => item.passes.filter(pass => pass.pass_no === 1)) ?? []
  const usedProfiles = [...new Set(recordedP1.filter(pass => pass.provider_call_count > 0)
    .map(pass => pass.profile ?? 'Unknown profile'))]
  const tokenTotals = tokenFields.map(field => {
    const known = recordedP1.map(pass => pass[field].value).filter((value): value is number => value !== null)
    return known.length ? known.reduce((sum, value) => sum + value, 0).toLocaleString() : '—'
  })
  const totalCost = p1Cost(usage)
  const completedUnits = pipeline.analysis.units.filter(item => item.state === 'completed').length

  useLayoutEffect(() => {
    const stack = previewStack.current
    if (!stack) return
    const grid = stack.parentElement
    if (!grid) return
    const updateTop = () => grid.style.setProperty('--translate-preview-top', `${stack.getBoundingClientRect().top + window.scrollY}px`)
    const observer = new ResizeObserver(updateTop)
    const main = stack.closest('main')
    if (main) observer.observe(main)
    window.addEventListener('resize', updateTop)
    updateTop()
    return () => { observer.disconnect(); window.removeEventListener('resize', updateTop) }
  }, [])

  async function startUnit() {
    if (!pendingRun || runLatch.current || disabled) return
    runLatch.current = true
    setLocalError(null)
    setStartedTarget({ unitId: pendingRun, jobId: null })
    try {
      const latest = await request(endpoint(id, 'pipeline'), pipelineSchema)
      queryClient.setQueryData([scope, endpoint(id, 'pipeline')], latest)
      if (latest.config.revision !== pipeline.config.revision || latest.busy || latest.metadata.lifecycle.archived ||
          !latest.actions.analyze?.allowed || latest.analysis.units.find(item => item.state !== 'completed')?.id !== pendingRun) {
        setLocalError(new Error('P1 state changed. Close this dialog and review the next unit.'))
        setStartedTarget(null)
        return
      }
      const storageKey = `intelitex.pending.${scope}.${id}.analysis.${pendingRun}`
      let key: string
      try { key = sessionStorage.getItem(storageKey) ?? createRequestKey(); sessionStorage.setItem(storageKey, key) }
      catch { key = createRequestKey() }
      const result = await command.send(endpoint(id, 'jobs'), jobSchema,
        { operation: 'analyze', unit_id: pendingRun, request_key: key }, 'POST', true)
      if (result) {
        setStartedTarget({ unitId: pendingRun, jobId: result.job_id })
        try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
        void reconcile(id)
        setPendingRun(null)
        setUnknownOutcome(false)
      } else if (command.unknownOutcome()) {
        try {
          const receipt = await request(`/api/requests/${encodeURIComponent(key)}`, jobSchema)
          if (receipt.workspace_id === id && receipt.operation === 'analyze') {
            setStartedTarget({ unitId: pendingRun, jobId: receipt.job_id })
            try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
            command.clearError(); void reconcile(id); setPendingRun(null)
          } else { setStartedTarget(null); setUnknownOutcome(true) }
        } catch { setStartedTarget(null); setUnknownOutcome(true) }
      } else {
        setStartedTarget(null)
        try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
      }
    } catch (error) { setStartedTarget(null); setLocalError(error) }
    finally { runLatch.current = false }
  }

  return <div className="analyse-content">
    {connection !== 'Live' && !pipeline.busy && <div className="notice" role="status">{connection}. Run buttons will be available when the connection recovers.</div>}
    <div className="phase-detail-grid translate-detail-grid analyse-work-grid"><div className="phase-detail-stack">
      <Panel debugId="PAN" title="Analysis units"><span className="translate-working-announce" role="status">{workingId ? `Running ${workingId} · P1` : ''}</span>
        <div className="diagnostic-scroll"><table className="phase-detail-table analyse-unit-table"><thead><tr><th>Section / unit</th><th>P1</th></tr></thead><tbody>{pipeline.analysis.units.map(item => {
          const isWorking = workingId === item.id
          const state = item.state === 'completed' ? 'completed' : item.state === 'error' || item.failed_attempt_count > 0 ? 'error' : 'pending'
          const symbol = state === 'completed' ? '✓' : state === 'error' ? '×' : '○'
          const canRun = item.id === nextUnit?.id && item.state === 'pending' && !pipeline.analysis.complete
          return <tr key={item.id} className={[unit?.id === item.id ? 'selected' : '', item.state === 'completed' ? 'finished' : ''].filter(Boolean).join(' ')}>
            <td><button className="translate-chunk-choice" onClick={() => setSelectedId(item.id)}>{titles.get(item.chapter_id) ?? item.chapter_id}<small>{item.id}</small></button></td>
            <td><div className="translate-pass-cell"><button className={`translate-pass-state ${state}${isWorking ? ' working' : ''}`}
              aria-label={`${item.id} P1: ${isWorking ? 'running' : state}; preview`} aria-pressed={unit?.id === item.id}
              title={`P1: ${state}. Open saved output.`} onClick={() => setSelectedId(item.id)}>{isWorking ? '◌' : symbol}</button>
              <button className="translate-pass-run" aria-label={`Run ${item.id} P1`} title={canRun ? 'Run this P1 unit; uses model tokens.' : item.state === 'completed' ? 'P1 is already saved. Clear the full P1 state before rerunning it.' : item.state === 'error' ? 'Saved P1 evidence is invalid. Inspect or restore the checkpoint locally.' : 'Earlier P1 units must finish first.'}
                disabled={disabled || !canRun} onClick={() => { setSelectedId(item.id); setLocalError(null); setUnknownOutcome(false); setPendingRun(item.id) }}><Play size={13} /></button></div></td>
          </tr>
        })}</tbody></table></div>
      </Panel>
    </div><div className="phase-detail-stack translate-preview-stack" ref={previewStack}>
      <Panel debugId="PAV" title={unit ? `P1 preview · ${unit.id}` : 'P1 preview'}><div className="translate-pass-preview">{!unit ? <Empty>No persisted P1 plan yet.</Empty> : <>
        <div className="prepare-preview-kicker">{titles.get(unit.chapter_id) ?? unit.chapter_id}</div>
        <ErrorNote error={preview.error} retry={() => void preview.refetch()} />
        <div className="translate-preview-tools"><p className="subtitle" role={preview.isPending ? 'status' : undefined}>{preview.isPending ? 'Loading saved result…' : firstPreview?.available ? 'Saved P1 terms and observations for this unit.' : 'Source text · no saved P1 result for this unit yet.'}</p></div>
        {firstPreview && <div className="translate-preview-scroll"><div className={`translate-preview-head${firstPreview.available ? '' : ' source-only'}`}><h3>{sourceLanguage}</h3>{firstPreview.available && <h3>Terms and observations</h3>}</div>
          {previewPages.map(page => <P1PreviewPage key={page.page} page={page} />)}
          {preview.hasNextPage && <div className="translate-preview-more"><span>{previewPages.reduce((sum, page) => sum + page.source.length, 0)} blocks shown</span><Button disabled={preview.isFetchingNextPage} onClick={() => void preview.fetchNextPage()}>{preview.isFetchingNextPage ? 'Loading…' : 'Load next 5 blocks'}</Button></div>}
        </div>}
        {previewPages.some(page => page.truncated) && <p className="subtitle">Some very long source blocks were shortened.</p>}
      </>}</div></Panel>
    </div>
      <Panel debugId="PUC" title="P1 costs and tokens"><details className="analyse-usage-details"><summary>Show details</summary><ErrorNote error={usageError} />
        <h3 className="translate-summary-heading">Current model assignments</h3>
        <div className="diagnostic-scroll"><table className="phase-detail-table translate-model-grid analyse-model-grid"><thead><tr><th>Section</th><th>P1</th></tr></thead><tbody>
          <tr><td><strong>Default</strong></td><td><span className="phase-model"><ProfileSwatch index={selectedProfile?.stable_palette_index} name={selectedProfile?.name ?? 'Unavailable'} />{selectedProfile?.name ?? '—'}</span></td></tr>
          {overriddenSections.map(section => {
            const name = section.profiles['1']!
            const profile = profiles?.profiles.find(item => item.name === name)
            return <tr key={section.id}><td title={section.id}><strong>{titles.get(section.id) ?? section.title ?? section.id}</strong><small>{section.id}</small></td><td className="overridden" title={`Section model: ${name}`}><span className="phase-model"><ProfileSwatch index={profile?.stable_palette_index} name={name} />{name}</span></td></tr>
          })}
        </tbody></table></div>
        <h3 className="translate-summary-heading">Recorded provider usage</h3>
        <div className="diagnostic-scroll"><table className="phase-detail-table pass-summary"><thead><tr><th>Pass</th><th>Used profile(s)</th><th>Units</th>{tokenLabels.map(label => <th className="num" key={label}>{label}</th>)}<th className="num">Cost</th></tr></thead><tbody><tr>
          <td>P1</td><td title={usedProfiles.join(', ')}>{usedProfiles.length ? usedProfiles.join(', ') : '—'}</td><td>{completedUnits}/{pipeline.analysis.units.length} completed</td>
          {tokenTotals.map((value, index) => <td className="num" key={tokenFields[index]}>{value}</td>)}<td className="num" title={totalCost.note}>{totalCost.text}</td>
        </tr></tbody></table></div>
        <h3 className="translate-summary-heading">Usage by unit</h3>
        <div className="diagnostic-scroll"><table className="phase-detail-table usage-table"><thead><tr><th>Unit</th><th>Recorded model</th>{tokenLabels.map(label => <th className="num" key={label}>{label}</th>)}<th className="num">Cost</th></tr></thead><tbody>{pipeline.analysis.units.map(item => {
            const actual = usage?.units.find(row => row.unit_id === item.id)?.passes.find(pass => pass.pass_no === 1)
            return <tr key={item.id}><td>{item.id}</td><td>{actual?.reported_model ?? actual?.requested_model ?? '—'}</td>{tokenFields.map(field => <td className="num" key={field}>{actual?.[field].value?.toLocaleString() ?? '—'}</td>)}<td className="num">{displayCost(actual?.cost)}</td></tr>
          })}</tbody></table></div><p className="translate-usage-hint" title="Usage includes recorded attempts and retries. Cache and reasoning are subsets of other totals; do not add these columns together.">ⓘ Usage includes retries, including failed attempts; hover for details.</p>
      </details></Panel>
    </div>
    <Activity id={id} title="Recent execution" />
    {pendingRun && <Overlay title="Run this P1 unit?" debugId="AUM" compact close={() => { if (!command.pending) setPendingRun(null) }}><div className="modal-body"><p><strong>{pendingRun} · P1</strong></p><p>This starts one analysis unit and spends model tokens. Later units use its terms and observations as memory.</p><p>Selected profile: <strong>{selectedProfile?.name ?? 'Unavailable'}</strong>.</p>{selectedProfile?.provider === 'llamacpp' && <p className="subtitle">The configured local llama.cpp server must be running.</p>}<ErrorNote error={localError ?? command.error} />{unknownOutcome && <p className="subtitle">The request outcome is unknown. Check the current job before trying again.</p>}</div><div className="modal-foot"><Button onClick={() => setPendingRun(null)}>Cancel</Button><Button variant="primary" disabled={disabled || unknownOutcome} onClick={() => void startUnit()}>{command.pending ? 'Starting…' : 'Run P1 unit'}</Button></div></Overlay>}
  </div>
}
