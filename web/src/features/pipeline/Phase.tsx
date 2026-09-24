import { useContext, useRef, useState } from 'react'
import { useNavigate, useParams } from '@tanstack/react-router'
import { endpoint, matchesWorkspaceResource, queryClient, request, Scope, useApi } from '../../api/client'
import { analysisResetSchema, configSchema, jobSchema, pipelineSchema, preparationSchema, profilesSchema, usageSchema, workspacesSchema, type Pipeline, type Section, type Usage } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { createRequestKey } from '../../api/requestKey'
import { Back, Button, Empty, ErrorNote, Overlay, Panel, ProfileSwatch } from '../../components/ui/common'
import { Activity, phaseStatus } from './Workspace'
import { Action } from './Action'
import { PrepareContent } from './PrepareContent'
import { TranslateContent } from './TranslateContent'
import { AnalyseContent } from './AnalyseContent'
import { p1Cost, translationCost } from './p1Cost'
import { debugTag } from '../../debug/regions'
const fields = ['input_tokens','cached_input_tokens','reasoning_output_tokens','output_tokens'] as const
const labels = ['Input','Cache','Reason','Output']
export function usageTotals(usage: Usage | undefined, numbers: number[]) {
  return fields.map(field => {
    const aggregates = usage?.units.flatMap(u => u.passes.filter(p => numbers.includes(p.pass_no)).map(p => p[field])) ?? []
    const measured = aggregates.filter(a => a.value !== null)
    return { value: measured.length ? measured.reduce((sum,a) => sum + a.value!, 0) : null, partial: aggregates.some(a => a.unknown_attempts > 0 || a.value === null) }
  })
}
const measured = (value: ReturnType<typeof usageTotals>[number]) => value.value == null ? '—' : value.value.toLocaleString()
export function PhasePage() {
  const { workspaceId: id = '', phase = 'prepare' } = useParams({ strict: false })
  const all = useApi('/api/workspaces', workspacesSchema)
  const workspace = all.data?.workspaces.find(w => w.workspace_id === id)
  const query = useApi(endpoint(id,'pipeline'), pipelineSchema, !!workspace?.prepared)
  const usage = useApi(endpoint(id,'usage'), usageSchema, !!workspace?.prepared && ['analyse','translate'].includes(phase))
  const profiles = useApi(endpoint(id,'profiles'), profilesSchema, !!workspace?.prepared)
  const preparation = useApi(endpoint(id,'preparation'), preparationSchema, !!workspace?.prepared && phase === 'prepare')
  const analysisReset = useApi(endpoint(id,'analysis-reset'), analysisResetSchema, !!workspace?.prepared && phase === 'analyse')
  const command = useCommand(id)
  const navigate = useNavigate()
  const scope = useContext(Scope)
  const [rebuildOpen, setRebuildOpen] = useState(false)
  const [rebuildError, setRebuildError] = useState<unknown>(null)
  const [resetRevision, setResetRevision] = useState<string | null>(null)
  const [resetError, setResetError] = useState<unknown>(null)
  const [resetUnknown, setResetUnknown] = useState(false)
  const [pendingProcessing, setPendingProcessing] = useState<{ sectionId: string; mode: Section['processing'] } | null>(null)
  const processingLatch = useRef(false)
  const p = query.data
  const title = ({ prepare: 'Prepare', analyse: 'Analyse', translate: 'Translate', publish: 'Publish' } as Record<string,string>)[phase] ?? phase
  const state = p ? phaseStatus(p,phase) : 'blocked'
  const totals = usageTotals(usage.data, phase === 'analyse' ? [1] : [2,3,4,5])
  const cost = p1Cost(usage.data)
  const translateCost = translationCost(usage.data)
  const p1Running = phase === 'analyse' && p?.active_job?.operation === 'analyze'
  const translateRunning = phase === 'translate' && p?.active_job?.operation === 'translate'
  const metrics = phase === 'prepare' ? [['Format',p?.metadata.format ?? '—'],['Sections',p?.sections.length ?? '—'],['Processable',p?.sections.filter(s => s.processing !== 'excluded').length ?? '—'],['Words',p?.metadata.word_count?.toLocaleString() ?? '—']] : phase === 'publish' ? [['Status',p?.publication.state ?? '—'],['Format',p?.publication.current ? 'EPUB' : '—'],['Sections',p?.sections.filter(s => s.processing !== 'excluded').length ?? '—'],['Size',p?.publication.size_bytes == null ? '—' : `${p.publication.size_bytes.toLocaleString()} bytes`]] : phase === 'analyse' ? [...totals.map((t,i) => [labels[i],t.value == null ? '—' : t.value.toLocaleString()]), ['Cost',cost.text]] : [...totals.map((t,i) => [labels[i],measured(t)]), ['Cost',translateCost.text]]
  const currentProfile = (n:number) => { const profile=profiles.data?.resolved_passes[String(n)];return <span className="phase-model"><ProfileSwatch index={profile?.stable_palette_index} name={profile?.name ?? 'Unavailable'} />{profile?.name ?? '—'}</span> }
  async function runReprepare() {
    if (!p || !workspace || command.disabled) return
    setRebuildError(null)
    try {
      const latest = await request(endpoint(id, 'pipeline'), pipelineSchema)
      queryClient.setQueryData([scope, endpoint(id, 'pipeline')], latest)
      if (latest.config.revision !== p.config.revision || latest.busy || latest.metadata.lifecycle.archived) {
        setRebuildError(new Error('Workspace changed. Review its current state before preparing again.'))
        return
      }
    } catch (error) { setRebuildError(error); return }
    const storageKey = `intelitex.pending.${scope}.${id}.reprepare`
    let key: string
    try { key = sessionStorage.getItem(storageKey) ?? createRequestKey(); sessionStorage.setItem(storageKey, key) }
    catch { try { key = createRequestKey() } catch (error) { setRebuildError(error); return } }
    const value = await command.send(endpoint(id, 'reprepare'), jobSchema,
      { revision: p.config.revision, request_key: key })
    if (value) {
      try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
      setRebuildOpen(false)
    } else if (command.unknownOutcome()) {
      try {
        const receipt = await request(`/api/requests/${encodeURIComponent(key)}`, jobSchema)
        if (receipt.workspace_id === id && receipt.operation === 'import') {
          try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
          command.clearError(); setRebuildOpen(false)
        }
      } catch { /* Keep the pending receipt for deliberate recovery. */ }
    } else {
      try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
    }
  }
  const canReprepare = !!p && !!workspace?.source_id && !p.busy && !p.analysis.membership_locked && !p.metadata.lifecycle.archived
  async function changeProcessing(sectionId: string, mode: Section['processing']) {
    if (!p || command.disabled || processingLatch.current) return
    processingLatch.current = true
    setPendingProcessing({ sectionId, mode })
    try {
      const result = await command.send(endpoint(id, `sections/${encodeURIComponent(sectionId)}`), configSchema,
        { revision: p.config.revision, processing: mode }, 'PATCH', true)
      await queryClient.cancelQueries({ queryKey: [scope, endpoint(id, 'pipeline')] })
      if (result) queryClient.setQueryData<Pipeline>([scope, endpoint(id, 'pipeline')], current => current && ({
        ...current, config: { ...current.config, revision: result.revision },
        sections: current.sections.map((section: Section) => section.id === sectionId ? { ...section, processing: mode } : section),
      }))
      if (result) void queryClient.invalidateQueries({ queryKey: [scope, '/api/workspaces'], exact: true,
        refetchType: 'none' })
      void queryClient.invalidateQueries({ predicate: query => query.queryKey[0] === scope &&
        matchesWorkspaceResource(String(query.queryKey[1]), id) &&
        !String(query.queryKey[1]).includes('/reader/chapters/') }).catch(() => {})
    } finally {
      setPendingProcessing(null)
      processingLatch.current = false
    }
  }
  const analysisEmpty = phase === 'analyse' && !!p && !p.analysis.planned && !p.analysis.complete
  async function clearAnalysis() {
    if (!resetRevision || command.disabled) return
    setResetError(null)
    try {
      const latest = await request(endpoint(id, 'analysis-reset'), analysisResetSchema)
      queryClient.setQueryData([scope, endpoint(id, 'analysis-reset')], latest)
      if (latest.revision !== resetRevision || !latest.can_reset) {
        setResetError(new Error('P1 state changed. Close this dialog and review the current state.'))
        return
      }
    } catch (error) { setResetError(error); return }
    const result = await command.send(endpoint(id, 'analysis-reset'), analysisResetSchema,
      { revision: resetRevision })
    if (result) {
      setResetRevision(null)
      await navigate({ to: '/work/workspaces/$workspaceId', params: { workspaceId: id } })
    } else if (command.unknownOutcome()) {
      setResetUnknown(true)
    }
  }
  return <main className={`main phase-detail-page${phase === 'prepare' ? ' prepare-detail-page' : ''}${phase === 'analyse' ? ' analyse-detail-page' : ''}`} {...debugTag('PHD')}><Back id={id} /><div className="phase-detail-head" {...debugTag('PHH')}><div className="phase-detail-title"><div className="eyebrow">{title} · {p?.metadata.title ?? workspace?.metadata.title ?? 'Workspace'}</div><h1>{phase === 'analyse' ? 'Analyse · P1' : title}</h1><div className="phase-detail-meta">{phase === 'analyse' ? `Whole-book analysis · ${p?.progress.analysis.required ? `${p.progress.analysis.completed}/${p.progress.analysis.required} required units` : 'plan not created yet'}` : 'Phase details and execution diagnostics'}</div></div><span className={`phase-status-badge ${state}`}>{state === 'done' ? 'Complete' : state === 'blocked' ? 'Blocked' : state === 'error' ? phase === 'translate' ? 'Previous run failed' : 'Failed' : p?.active_job ? 'Running' : 'Ready'}</span></div><ErrorNote error={query.error ?? usage.error ?? profiles.error} retry={() => void query.refetch()} />
    {p && !analysisEmpty && <div className={`phase-metrics${phase === 'analyse' ? ' p1-metrics' : ''}${phase === 'translate' ? ' translate-metrics' : ''}`} {...debugTag('PHM')}>{metrics.map(([label,value], index) => <div className="phase-metric" key={label} {...debugTag('PMT', String(label))}><div className="phase-metric-label">{label}</div><div className={`phase-metric-value${(p1Running || translateRunning) && value !== '—' ? ' p1-counting' : ''}`} title={phase === 'analyse' && index === 4 ? cost.note : phase === 'translate' && index === 4 ? translateCost.note : phase === 'analyse' || phase === 'translate' ? totals[index]?.partial ? 'Recorded total so far; some attempts have unknown usage.' : 'Recorded total so far.' : String(value)}>{value}</div></div>)}</div>}
    {phase === 'analyse' && p && !analysisEmpty && <Panel debugId="PGD" title="Generated data"><dl className="analyse-generated-facts"><div><dt>Terms / entities</dt><dd>{p.review.summary?.total ?? '—'}</dd></div><div><dt>Analysis units</dt><dd>{p.analysis.units.length}</dd></div><div><dt>Default profile</dt><dd>{currentProfile(1)}</dd></div><div><dt>Review gate</dt><dd>{p.approved ? 'Committed approval' : p.analysis.complete ? 'Ready for review' : 'Not ready'}</dd></div></dl></Panel>}
    {!p ? <Panel debugId="PLD" title={all.isPending || workspace?.prepared ? 'Loading workspace state' : 'Prepare required'}><p className="subtitle" role="status">{all.isPending || workspace?.prepared ? 'Reading the saved workspace and pipeline state…' : workspace ? 'Run Prepare from the workspace before opening phase details.' : 'Workspace unavailable.'}</p>{workspace && !workspace.prepared && <Action workspace={workspace} />}</Panel> : state === 'blocked' && phase !== 'prepare' ? <Empty>Complete the preceding phase before {title.toLowerCase()}.</Empty> : phase === 'prepare' ? <PrepareContent id={id} pipeline={p} preparation={preparation.data} preparationError={preparation.error} workspace={workspace} canReprepare={canReprepare} commandDisabled={command.disabled} pendingProcessing={pendingProcessing} onProcessingChange={mode => void changeProcessing(mode.sectionId, mode.mode)} processingError={command.error} onReprepare={() => setRebuildOpen(true)} /> : analysisEmpty ? <Panel debugId="PAE" title={!analysisReset.data ? 'Checking saved P1 state' : analysisReset.data.has_data ? 'P1 has no saved plan' : 'P1 has not been started'}><ErrorNote error={analysisReset.error} retry={() => void analysisReset.refetch()} /><p className="subtitle">{analysisReset.error ? 'Could not read the current P1 state.' : !analysisReset.data ? 'Reading the saved analysis state…' : analysisReset.data.has_data ? 'There is P1 evidence without a current plan. Clear it before starting again if no later work depends on it.' : 'No analysis plan, units, or model results are saved for this workspace.'}</p><div className="analysis-empty-actions"><Button variant="primary" onClick={() => void navigate({ to: '/work/workspaces/$workspaceId', params: { workspaceId: id } })}>Return to workspace</Button></div></Panel> : phase === 'analyse' ? <AnalyseContent id={id} pipeline={p} usage={usage.data} profiles={profiles.data} usageError={usage.error} /> : phase === 'translate' ? <TranslateContent id={id} pipeline={p} usage={usage.data} profiles={profiles.data} usageError={usage.error} /> : <div className="phase-detail-grid"><div className="phase-detail-stack">
      {phase === 'publish' && <><Panel debugId="PPO" title="Published output"><dl className="phase-kv"><dt>File</dt><dd>{p.publication.filename ?? '—'}</dd><dt>Resource</dt><dd>{p.publication.current ? 'Current workspace EPUB' : 'Unavailable'}</dd><dt>Language</dt><dd>{p.publication.target_language}</dd><dt>Edition</dt><dd>{p.publication.generated_by ?? '—'}</dd><dt>Source sections</dt><dd>{p.sections.length}</dd><dt>Excluded</dt><dd>{p.sections.filter(s => s.processing === 'excluded').length}</dd></dl>{p.publication.current && <a className="secondary-btn download" href={endpoint(id,'publication/download')}>Open EPUB</a>}{p.publication.last_error && <p className="subtitle">{p.publication.last_error}</p>}</Panel><Panel debugId="PPV" title="Package validation">{p.publication.checks.length ? p.publication.checks.map(check => <p className="phase-check" key={check}><span className="phase-check-icon">✓</span>{check}</p>) : <Empty>No current publication validation results.</Empty>}</Panel></>}
    </div><div className="phase-detail-stack">
      {phase !== 'analyse' && <Panel debugId="PMD" title="Metadata"><dl className="phase-kv"><dt>Title</dt><dd>{p.metadata.title}</dd><dt>Author</dt><dd>{p.metadata.creators.join(', ') || '—'}</dd><dt>Source language</dt><dd>{p.metadata.language ?? '—'}</dd><dt>Output language</dt><dd>{p.publication.target_language}</dd><dt>Publisher</dt><dd>{p.publication.generated_by ?? '—'}</dd></dl></Panel>}
      {phase === 'publish' && <Activity id={id} publicationOnly expanded title="Publication log" />}
    </div></div>}
    {phase === 'analyse' && p && !analysisEmpty && <Panel debugId="PAR" title="Artifacts"><p className="phase-check">{p.artifacts.terminology ? '✓' : '○'} Terminology candidates · {p.artifacts.terminology ? 'Available' : 'Not available'}</p><p className="phase-check">{p.artifacts.book_memory ? '✓' : '○'} Book memory · {p.artifacts.book_memory ? 'Available' : 'Not available'}</p></Panel>}
    {phase === 'analyse' && p && (!analysisEmpty || analysisReset.data?.has_data) && <Panel debugId="PAC" title="P1 data"><ErrorNote error={analysisReset.error} retry={() => void analysisReset.refetch()} />{analysisReset.data?.has_data ? <div className="analysis-reset-actions"><p className="subtitle">Clear the current P1 plan, attempts, terminology and Review draft. A versioned copy is kept in this workspace's history. Prepare and section choices remain.</p><Button variant="danger" disabled={!analysisReset.data.can_reset || command.disabled} onClick={() => { setResetError(null); setResetUnknown(false); setResetRevision(analysisReset.data!.revision) }}>Clear P1</Button>{!analysisReset.data.can_reset && <p className="subtitle">{analysisReset.data.reason === 'dependent_work' ? 'P2–P5 or approved work depends on P1; this workspace cannot be reset.' : analysisReset.data.reason === 'workspace_busy' ? 'Stop the active job and wait for cleanup before clearing P1.' : 'P1 cannot be cleared in the current workspace state.'}</p>}</div> : analysisReset.data ? <p className="subtitle">There is no saved P1 data to clear.</p> : <p className="subtitle" role="status">Checking saved P1 data…</p>}</Panel>}
    {rebuildOpen && <Overlay title="Run Prepare again?" debugId="RPM" compact close={() => { if (!command.pending) setRebuildOpen(false) }}><div className="modal-body"><p>Rebuild source sections in this workspace without contacting a model. The current Prepare plan will be saved as a version. Your section F/T/E choices will be reset because section boundaries and IDs may change.</p><ErrorNote error={rebuildError ?? command.error} /></div><div className="modal-foot"><Button onClick={() => setRebuildOpen(false)}>Cancel</Button><Button variant="primary" disabled={!canReprepare || command.disabled} onClick={() => void runReprepare()}>Rebuild Prepare</Button></div></Overlay>}
    {resetRevision && <Overlay title="Clear P1?" debugId="PRM" compact close={() => { if (!command.pending) setResetRevision(null) }}><div className="modal-body"><p>The current P1 plan, attempts, terminology and Review draft will be removed from the active workflow. A versioned copy remains in this workspace's history. Prepare and F/T/E choices stay in place.</p><ErrorNote error={resetError ?? command.error} />{resetUnknown && <p className="subtitle">The request outcome is unknown. Refresh the current P1 state before trying again.</p>}</div><div className="modal-foot"><Button onClick={() => setResetRevision(null)}>Cancel</Button><Button variant="danger" disabled={command.disabled || resetUnknown} onClick={() => void clearAnalysis()}>Clear P1 and return</Button></div></Overlay>}
  </main>
}
