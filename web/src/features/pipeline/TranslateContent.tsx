import { useContext, useRef, useState } from 'react'
import { Play } from 'lucide-react'
import { endpoint, queryClient, reconcile, request, Scope, useApi } from '../../api/client'
import { jobSchema, pipelineSchema, translationPassPreviewSchema, type Pipeline, type Profiles, type Usage } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { createRequestKey } from '../../api/requestKey'
import { Button, Empty, ErrorNote, Overlay, Panel, ProfileSwatch } from '../../components/ui/common'
import { Activity } from './Workspace'
import { sectionTitles } from './sectionTitles'
import { useConnection, useLive } from '../../realtime/coordinator'

const tokenFields = ['input_tokens', 'cached_input_tokens', 'reasoning_output_tokens', 'output_tokens'] as const
const tokenLabels = ['Input', 'Cache', 'Reason', 'Output']
const passNumbers = [2, 3, 4, 5] as const

function totals(usage: Usage | undefined, passNo: number) {
  const passes = usage?.units.flatMap(unit => unit.passes.filter(pass => pass.pass_no === passNo)) ?? []
  return tokenFields.map(field => {
    const values = passes.map(pass => pass[field])
    const known = values.filter(value => value.value !== null)
    return known.length ? known.reduce((sum, value) => sum + value.value!, 0).toLocaleString() : '—'
  })
}

function describeFinding(value: Record<string, unknown>) {
  return Object.entries(value).filter(([key]) => key !== 'sid' && key !== 'block_id')
    .map(([key, item]) => `${key.replaceAll('_', ' ')}: ${String(item)}`).join(' · ')
}

export function assignedProfile(profiles: Profiles | undefined, section: Pipeline['sections'][number] | undefined, passNo: number) {
  const override = section?.profiles[String(passNo)]
  if (override) return profiles?.profiles.find(profile => profile.name === override) ?? {
    name: override, provider: null, model: null, enabled: false, stable_palette_index: null,
  }
  return profiles?.resolved_passes[String(passNo)]
}

export function recordedProfileNames(usage: Usage | undefined, passNo: number) {
  return [...new Set(usage?.units.flatMap(item => item.passes.filter(pass => pass.pass_no === passNo && pass.provider_call_count > 0)
    .map(pass => pass.profile ?? 'Unknown profile')) ?? [])]
}

export function TranslateContent({ id, pipeline, usage, profiles, usageError }: {
  id: string; pipeline: Pipeline; usage?: Usage; profiles?: Profiles; usageError: unknown
}) {
  const scope = useContext(Scope)
  const command = useCommand(id)
  const connection = useConnection()
  const live = useLive()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedPass, setSelectedPass] = useState<number>(pipeline.units.some(item => item.passes['5']?.checkpoint_state === 'completed') ? 5 : 2)
  const [pendingRun, setPendingRun] = useState<{ chunkId: string; passNo: number; rerun: boolean } | null>(null)
  const [localError, setLocalError] = useState<unknown>(null)
  const [unknownOutcome, setUnknownOutcome] = useState(false)
  const [startedTarget, setStartedTarget] = useState<{ chunkId: string; passNo: number; jobId: string | null } | null>(null)
  const runLatch = useRef(false)
  const unit = pipeline.units.find(item => item.id === selectedId) ?? pipeline.units[0]
  const sections = new Map(pipeline.sections.map(section => [section.id, section]))
  const titles = sectionTitles(pipeline.sections, pipeline.metadata.creators)
  const overriddenSections = pipeline.sections.filter(section => passNumbers.some(number => !!section.profiles[String(number)]))
  const preview = useApi(endpoint(id, `translation/chunks/${encodeURIComponent(unit?.id ?? '')}/passes/${selectedPass}`),
    translationPassPreviewSchema, !!unit)
  const disabled = command.disabled || pipeline.busy || pipeline.metadata.lifecycle.archived
  const candidateActive = pipeline.active_job?.operation === 'translate' ? pipeline.active_job : null
  const liveActive = candidateActive ? live.state.jobs[candidateActive.job_id] : null
  const active = candidateActive && !['succeeded', 'failed', 'cancelled', 'abandoned'].includes(liveActive?.state ?? '')
    ? candidateActive : null
  const activeEvent = active && [...(live.state.activity[id] ?? [])].reverse().find(event =>
    event.job_id === active.job_id && typeof event.event.values.chunk_id === 'string' &&
    typeof event.event.values.pass_no === 'number')
  const activeValues = activeEvent?.event.values ?? active?.last_event?.event.values
  const activeTarget = active && typeof activeValues?.chunk_id === 'string' && typeof activeValues.pass_no === 'number'
    ? { chunkId: activeValues.chunk_id, passNo: activeValues.pass_no, jobId: active.job_id } : null
  const startedJob = startedTarget?.jobId ? live.state.jobs[startedTarget.jobId] : null
  const startedFinished = startedTarget?.jobId &&
    (['succeeded', 'failed', 'cancelled', 'abandoned'].includes(startedJob?.state ?? '') ||
      (pipeline.last_job?.job_id === startedTarget.jobId && ['succeeded', 'failed', 'cancelled', 'abandoned'].includes(pipeline.last_job.state)))
  const working = activeTarget ?? (!startedFinished ? startedTarget : null)
  const attempt = activeEvent?.event.values.attempt_number ?? active?.last_event?.event.values.attempt_number
  const failedJob = pipeline.last_job?.operation === 'translate' && pipeline.last_job.state === 'failed' && !pipeline.busy
    ? pipeline.last_job : null
  const failureTime = failedJob?.finished_at ? new Date(failedJob.finished_at).toLocaleString() : null
  const failureDetail = failedJob?.error?.message
  const knownFailure = failureDetail && failureDetail !== 'Operation failed; inspect locally.'
  const pendingChunk = pipeline.units.find(item => item.id === pendingRun?.chunkId)
  const pendingProfile = pendingRun ? assignedProfile(profiles, sections.get(pendingChunk?.chapter_id ?? ''), pendingRun.passNo) : undefined
  const selectedSection = unit ? sections.get(unit.chapter_id) : undefined

  async function startTarget() {
    if (!pendingRun || runLatch.current || disabled) return
    runLatch.current = true
    setLocalError(null)
    const chosen = pendingRun
    setStartedTarget({ chunkId: chosen.chunkId, passNo: chosen.passNo, jobId: null })
    try {
      const latest = await request(endpoint(id, 'pipeline'), pipelineSchema)
      queryClient.setQueryData([scope, endpoint(id, 'pipeline')], latest)
      const current = latest.units.find(item => item.id === chosen.chunkId)
      const original = pipeline.units.find(item => item.id === chosen.chunkId)
      if (!latest.approved || latest.busy || latest.metadata.lifecycle.archived ||
          latest.config.revision !== pipeline.config.revision || !current || !original ||
          current.status !== original.status ||
          current.passes[String(chosen.passNo)]?.retained_count !== original.passes[String(chosen.passNo)]?.retained_count) {
        setLocalError(new Error('Translation state changed. Close this dialog and review the current chunk.'))
        setStartedTarget(null)
        return
      }
      const storageKey = `intelitex.pending.${scope}.${id}.pass.${chosen.chunkId}.${chosen.passNo}`
      let key: string
      try { key = sessionStorage.getItem(storageKey) ?? createRequestKey(); sessionStorage.setItem(storageKey, key) }
      catch { key = createRequestKey() }
      const result = await command.send(endpoint(id, 'jobs'), jobSchema,
        { operation: 'translate', chunk_id: chosen.chunkId, pass_no: chosen.passNo,
          rerun: chosen.rerun, request_key: key }, 'POST', true)
      if (result) {
        setStartedTarget({ chunkId: chosen.chunkId, passNo: chosen.passNo, jobId: result.job_id })
        try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
        void reconcile(id)
        setPendingRun(null)
        setUnknownOutcome(false)
      } else if (command.unknownOutcome()) {
        try {
          const receipt = await request(`/api/requests/${encodeURIComponent(key)}`, jobSchema)
          if (receipt.workspace_id === id && receipt.operation === 'translate') {
            setStartedTarget({ chunkId: chosen.chunkId, passNo: chosen.passNo, jobId: receipt.job_id })
            try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
            command.clearError()
            void reconcile(id)
            setPendingRun(null)
          } else { setStartedTarget(null); setUnknownOutcome(true) }
        } catch { setStartedTarget(null); setUnknownOutcome(true) }
      } else {
        setStartedTarget(null)
        try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
      }
    } catch (error) { setStartedTarget(null); setLocalError(error) }
    finally { runLatch.current = false }
  }

  return <div className="translate-content">
    {failedJob && <div className="notice error" role="status"><strong>Previous run failed{failureTime ? ` · ${failureTime}` : ''}.</strong> No translation job is running now. {knownFailure ? failureDetail : 'This older job did not record a detailed reason.'} Saved passes remain available.</div>}
    {connection !== 'Live' && !pipeline.busy && <div className="notice" role="status">{connection}. Run buttons will be available when the connection recovers.</div>}
    <Panel debugId="PTS" title="Models and usage">
      <h3 className="translate-summary-heading">Current model assignments</h3>
      <div className="diagnostic-scroll"><table className="phase-detail-table pass-summary translate-model-grid"><thead><tr><th>Section</th>{passNumbers.map(number => <th key={number}>P{number}</th>)}</tr></thead><tbody>
        <tr><td><strong>Default</strong><small>Inherited unless overridden</small></td>{passNumbers.map(number => {
          const profile = assignedProfile(profiles, undefined, number)
          return <td key={number}><span className="phase-model"><ProfileSwatch index={profile?.stable_palette_index} name={profile?.name ?? 'Unavailable'} />{profile?.name ?? '—'}</span></td>
        })}</tr>
        {overriddenSections.map(section => <tr key={section.id}><td title={section.id}><strong>{titles.get(section.id) ?? section.title ?? section.id}</strong><small>{section.id}</small></td>{passNumbers.map(number => {
          const profile = assignedProfile(profiles, section, number)
          const overridden = !!section.profiles[String(number)]
          return <td key={number} className={overridden ? 'overridden' : 'inherited'}><span className="phase-model"><ProfileSwatch index={profile?.stable_palette_index} name={profile?.name ?? 'Unavailable'} />{profile?.name ?? '—'}</span><small>{overridden ? 'Override' : 'Inherited'}</small></td>
        })}</tr>)}
      </tbody></table></div>
      <h3 className="translate-summary-heading">Recorded provider usage</h3>
      <div className="diagnostic-scroll"><table className="phase-detail-table pass-summary"><thead><tr><th>Pass</th><th>Used profile(s)</th><th>Chunks</th>{tokenLabels.map(label => <th className="num" key={label}>{label}</th>)}</tr></thead><tbody>{passNumbers.map(number => {
        const used = recordedProfileNames(usage, number)
        const saved = pipeline.units.filter(item => item.passes[String(number)]?.retained_count).length
        const completed = pipeline.units.filter(item => item.passes[String(number)]?.checkpoint_state === 'completed').length
        return <tr key={number}><td>P{number}</td><td title={used.join(', ')}>{used.length ? used.join(', ') : '—'}</td><td title={number === 5 ? 'Verified current final translations' : 'Saved historical results; current inputs are checked before reuse'}>{number === 5 ? `${completed}/${pipeline.units.length} verified` : `${saved}/${pipeline.units.length} saved`}</td>{totals(usage, number).map((value, index) => <td className="num" key={tokenFields[index]}>{value}</td>)}</tr>
      })}</tbody></table></div><p className="translate-usage-hint" title="Usage includes recorded attempts and retries. Cache and reasoning are subsets of other totals; do not add these columns together.">ⓘ Usage includes retries, including failed attempts; hover for details.</p>
    </Panel>
    <div className="phase-detail-grid translate-detail-grid"><div className="phase-detail-stack">
      <Panel debugId="PSC" title="Chunk progress">{working && <p className="translate-working" role="status">Running {working.chunkId} · P{working.passNo}{typeof attempt === 'number' ? ` · attempt ${attempt}` : ' · starting…'}</p>}<div className="diagnostic-scroll"><table className="phase-detail-table translate-chunk-table"><thead><tr><th>Section / chunk</th>{passNumbers.map(number => <th key={number}>P{number}</th>)}</tr></thead><tbody>{pipeline.units.map(item => {
        const section = sections.get(item.chapter_id)
        return <tr key={item.id} className={unit?.id === item.id ? 'selected' : ''}><td title={item.id}><button className="translate-chunk-choice" onClick={() => { setSelectedId(item.id); setSelectedPass(2) }}>{titles.get(item.chapter_id) ?? section?.fallback_excerpt ?? item.chapter_id}<small>{item.id}</small></button></td>{passNumbers.map(number => {
          const pass = item.passes[String(number)]
          const state = pass?.checkpoint_state ?? 'pending'
          const hasSaved = !!pass?.retained_count
          const previous = number === 2 || !!item.passes[String(number - 1)]?.retained_count
          const isWorking = working?.chunkId === item.id && working.passNo === number
          const modelName = assignedProfile(profiles, section, number)?.name ?? 'Unavailable'
          return <td key={number}><div className="translate-pass-cell"><button className={`translate-pass-state ${state}${isWorking ? ' working' : ''}`} aria-label={`${item.id} P${number}: ${isWorking ? 'running' : state}; preview`} aria-pressed={unit?.id === item.id && selectedPass === number} title={isWorking ? `P${number} is running with ${modelName}; open preview after it finishes.` : number < 5 && hasSaved ? `Assigned model: ${modelName}. Saved result; current inputs are checked before reuse. Open preview.` : `P${number}: ${state}. Assigned model: ${modelName}. Open preview.`} onClick={() => { setSelectedId(item.id); setSelectedPass(number) }}>{isWorking ? '◌' : state === 'completed' ? '✓' : state === 'stale' ? '↻' : hasSaved ? '◐' : '○'}</button><button className="translate-pass-run" aria-label={`${hasSaved ? 'Run again' : 'Run'} ${item.id} P${number}`} title={!previous ? `Run P${number - 1} for this chunk first.` : hasSaved ? `Run P${number} again with ${modelName}; this contacts the model and replaces the selected result.` : `Run P${number} with ${modelName}; this contacts the model.`} disabled={disabled || !previous} onClick={() => { setLocalError(null); setUnknownOutcome(false); setSelectedId(item.id); setSelectedPass(number); setPendingRun({ chunkId: item.id, passNo: number, rerun: hasSaved }) }}><Play size={13} /></button></div></td>
        })}</tr>
      })}</tbody></table></div>{!pipeline.units.length && <Empty>No translation chunks in this workspace.</Empty>}</Panel>
    </div><div className="phase-detail-stack">
      <Panel debugId="TPV" title={unit ? `P${selectedPass} preview · ${unit.id}` : 'Pass preview'}><div className="translate-pass-preview">{!unit ? <Empty>Select a translation chunk.</Empty> : <><div className="prepare-preview-kicker">{titles.get(unit.chapter_id) ?? selectedSection?.fallback_excerpt ?? unit.chapter_id}</div><ErrorNote error={preview.error} retry={() => void preview.refetch()} />{preview.isPending ? <p className="subtitle" role="status">Loading saved result…</p> : preview.data && <><p className="subtitle">{preview.data.available ? selectedPass === 5 && preview.data.current ? 'Verified final translation' : 'Saved pass result · current inputs may differ' : 'No saved result for this pass yet.'}</p><div className="translate-preview-scroll"><div className="translate-preview-column"><h3>Source</h3>{preview.data.source.map(block => <p key={block.id}><small>{block.id}</small>{block.text}</p>)}</div><div className="translate-preview-column"><h3>{selectedPass === 2 ? 'Semantic checks' : selectedPass === 4 ? 'Correction checks' : 'Polish'}</h3>{preview.data.translations.map(block => <p key={block.id}><small>{block.id}</small>{block.text}</p>)}{preview.data.checks.map((check, index) => <p key={`check-${index}`}><small>{String(check.sid ?? `Check ${index + 1}`)}</small>{describeFinding(check)}</p>)}{preview.data.findings.map((finding, index) => <p key={`finding-${index}`}><small>{String(finding.sid ?? finding.block_id ?? `Finding ${index + 1}`)}</small>{describeFinding(finding)}</p>)}{preview.data.available && !preview.data.translations.length && !preview.data.findings.length && !preview.data.checks.length && <Empty>No findings in this saved result.</Empty>}</div></div>{preview.data.truncated && <p className="subtitle">Preview shortened for this chunk.</p>}</>}</>}</div></Panel>
    </div></div>
    <Activity id={id} expanded title="Recent execution" />
    <Panel debugId="PDG" title="Attempt diagnostics"><details className="translate-diagnostics"><summary>Show recorded attempts and measurement notes</summary><ErrorNote error={usageError} />{usage?.warning && <p className="subtitle">{usage.warning}</p>}<div className="execution-diagnostics">{usage?.units.flatMap(item => item.passes.filter(pass => pass.pass_no > 1).map(pass => <details key={`${item.unit_id}-${pass.pass_no}`}><summary>{item.unit_id} · P{pass.pass_no} · {pass.physical_attempt_count} attempts</summary><p>{pass.provider ?? '—'} · {pass.reported_model ?? pass.requested_model ?? '—'} · {pass.result_status} · {pass.failed_attempt_count} failed</p>{pass.attempts.map(attempt => <p key={attempt.attempt_id}>{attempt.attempt_id} · {attempt.acceptance_status} · {attempt.generation_status}</p>)}</details>))}</div></details></Panel>
    {pendingRun && <Overlay title={pendingRun.rerun ? 'Run this pass again?' : 'Run this pass?'} debugId="TCM" compact close={() => { if (!command.pending) setPendingRun(null) }}><div className="modal-body"><p><strong>{pendingRun.chunkId} · P{pendingRun.passNo}</strong></p><p>This starts one model pass and spends tokens. The exact cost depends on the selected profile and response.</p><p>Selected profile: <strong>{pendingProfile?.name ?? 'Unavailable'}</strong>.</p>{pendingProfile?.provider === 'llamacpp' && <p className="subtitle">This pass needs the configured local llama.cpp server to be running.</p>}{pendingRun.rerun && <p>The new result replaces the selected result for this pass. Earlier artifacts remain saved. Later passes and the final translation may need to be run again.</p>}<p className="subtitle">If earlier chunks in this section or thread are unfinished, a P5 result remains stale until rerun with complete context.</p><ErrorNote error={localError ?? command.error} />{unknownOutcome && <p className="subtitle">The request outcome is unknown. Check the current job before trying again.</p>}</div><div className="modal-foot"><Button onClick={() => setPendingRun(null)}>Cancel</Button><Button variant="primary" disabled={disabled || unknownOutcome} onClick={() => void startTarget()}>{command.pending ? 'Starting…' : pendingRun.rerun ? 'Run again' : 'Run pass'}</Button></div></Overlay>}
  </div>
}
