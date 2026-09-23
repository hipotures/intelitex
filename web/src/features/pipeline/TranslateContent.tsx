import { useContext, useRef, useState } from 'react'
import { Play } from 'lucide-react'
import { endpoint, queryClient, reconcile, request, Scope, useApi } from '../../api/client'
import { jobSchema, pipelineSchema, translationPassPreviewSchema, type Pipeline, type Profiles, type Usage } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { createRequestKey } from '../../api/requestKey'
import { Button, Empty, ErrorNote, Overlay, Panel, ProfileSwatch } from '../../components/ui/common'
import { Activity } from './Workspace'
import { sectionTitles } from './sectionTitles'

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

export function TranslateContent({ id, pipeline, usage, profiles, usageError }: {
  id: string; pipeline: Pipeline; usage?: Usage; profiles?: Profiles; usageError: unknown
}) {
  const scope = useContext(Scope)
  const command = useCommand(id)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedPass, setSelectedPass] = useState<number>(pipeline.units.some(item => item.passes['5']?.checkpoint_state === 'completed') ? 5 : 2)
  const [pendingRun, setPendingRun] = useState<{ chunkId: string; passNo: number; rerun: boolean } | null>(null)
  const [localError, setLocalError] = useState<unknown>(null)
  const [unknownOutcome, setUnknownOutcome] = useState(false)
  const runLatch = useRef(false)
  const unit = pipeline.units.find(item => item.id === selectedId) ?? pipeline.units[0]
  const sections = new Map(pipeline.sections.map(section => [section.id, section]))
  const titles = sectionTitles(pipeline.sections, pipeline.metadata.creators)
  const preview = useApi(endpoint(id, `translation/chunks/${encodeURIComponent(unit?.id ?? '')}/passes/${selectedPass}`),
    translationPassPreviewSchema, !!unit)
  const disabled = command.disabled || pipeline.busy || pipeline.metadata.lifecycle.archived
  const selectedSection = unit ? sections.get(unit.chapter_id) : undefined

  async function startTarget() {
    if (!pendingRun || runLatch.current || disabled) return
    runLatch.current = true
    setLocalError(null)
    const chosen = pendingRun
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
        try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
        void reconcile(id)
        setPendingRun(null)
        setUnknownOutcome(false)
      } else if (command.unknownOutcome()) {
        try {
          const receipt = await request(`/api/requests/${encodeURIComponent(key)}`, jobSchema)
          if (receipt.workspace_id === id && receipt.operation === 'translate') {
            try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
            command.clearError()
            void reconcile(id)
            setPendingRun(null)
          } else setUnknownOutcome(true)
        } catch { setUnknownOutcome(true) }
      } else {
        try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
      }
    } catch (error) { setLocalError(error) }
    finally { runLatch.current = false }
  }

  return <div className="translate-content">
    <Panel debugId="PTS" title="Pass summary"><div className="diagnostic-scroll"><table className="phase-detail-table pass-summary"><thead><tr><th>Pass</th><th>Default profile</th><th>Chunks</th>{tokenLabels.map(label => <th className="num" key={label}>{label}</th>)}</tr></thead><tbody>{passNumbers.map(number => {
      const profile = profiles?.resolved_passes[String(number)]
      const saved = pipeline.units.filter(item => item.passes[String(number)]?.retained_count).length
      const completed = pipeline.units.filter(item => item.passes[String(number)]?.checkpoint_state === 'completed').length
      return <tr key={number}><td>P{number}</td><td><span className="phase-model"><ProfileSwatch index={profile?.stable_palette_index} name={profile?.name ?? 'Unavailable'} />{profile?.name ?? '—'}</span></td><td title={number === 5 ? 'Verified current final translations' : 'Saved historical results; current inputs are checked before reuse'}>{number === 5 ? `${completed}/${pipeline.units.length} verified` : `${saved}/${pipeline.units.length} saved`}</td>{totals(usage, number).map((value, index) => <td className="num" key={tokenFields[index]}>{value}</td>)}</tr>
    })}</tbody></table></div><p className="translate-usage-hint" title="Usage includes recorded attempts and retries. Cache and reasoning are subsets of other totals; do not add these columns together.">ⓘ Usage includes retries; hover for details.</p></Panel>
    <div className="phase-detail-grid translate-detail-grid"><div className="phase-detail-stack">
      <Panel debugId="PSC" title="Chunk progress"><div className="diagnostic-scroll"><table className="phase-detail-table translate-chunk-table"><thead><tr><th>Section / chunk</th>{passNumbers.map(number => <th key={number}>P{number}</th>)}</tr></thead><tbody>{pipeline.units.map(item => {
        const section = sections.get(item.chapter_id)
        return <tr key={item.id} className={unit?.id === item.id ? 'selected' : ''}><td title={item.id}><button className="translate-chunk-choice" onClick={() => { setSelectedId(item.id); setSelectedPass(2) }}>{titles.get(item.chapter_id) ?? section?.fallback_excerpt ?? item.chapter_id}<small>{item.id}</small></button></td>{passNumbers.map(number => {
          const pass = item.passes[String(number)]
          const state = pass?.checkpoint_state ?? 'pending'
          const hasSaved = !!pass?.retained_count
          const previous = number === 2 || !!item.passes[String(number - 1)]?.retained_count
          return <td key={number}><div className="translate-pass-cell"><button className={`translate-pass-state ${state}`} aria-label={`${item.id} P${number}: ${state}; preview`} aria-pressed={unit?.id === item.id && selectedPass === number} title={number < 5 && hasSaved ? 'Saved result; current inputs are checked before reuse. Open preview.' : `P${number}: ${state}. Open preview.`} onClick={() => { setSelectedId(item.id); setSelectedPass(number) }}>{state === 'completed' ? '✓' : state === 'stale' ? '↻' : hasSaved ? '◐' : '○'}</button><button className="translate-pass-run" aria-label={`${hasSaved ? 'Run again' : 'Run'} ${item.id} P${number}`} title={!previous ? `Run P${number - 1} for this chunk first.` : hasSaved ? `Run P${number} again; this contacts the model and replaces the selected result.` : `Run P${number}; this contacts the model.`} disabled={disabled || !previous} onClick={() => { setLocalError(null); setUnknownOutcome(false); setSelectedId(item.id); setSelectedPass(number); setPendingRun({ chunkId: item.id, passNo: number, rerun: hasSaved }) }}><Play size={13} /></button></div></td>
        })}</tr>
      })}</tbody></table></div>{!pipeline.units.length && <Empty>No translation chunks in this workspace.</Empty>}</Panel>
    </div><div className="phase-detail-stack">
      <Panel debugId="TPV" title={unit ? `P${selectedPass} preview · ${unit.id}` : 'Pass preview'}><div className="translate-pass-preview">{!unit ? <Empty>Select a translation chunk.</Empty> : <><div className="prepare-preview-kicker">{titles.get(unit.chapter_id) ?? selectedSection?.fallback_excerpt ?? unit.chapter_id}</div><ErrorNote error={preview.error} retry={() => void preview.refetch()} />{preview.isPending ? <p className="subtitle" role="status">Loading saved result…</p> : preview.data && <><p className="subtitle">{preview.data.available ? selectedPass === 5 && preview.data.current ? 'Verified final translation' : 'Saved pass result · current inputs may differ' : 'No saved result for this pass yet.'}</p><div className="translate-preview-scroll"><div className="translate-preview-column"><h3>Source</h3>{preview.data.source.map(block => <p key={block.id}><small>{block.id}</small>{block.text}</p>)}</div><div className="translate-preview-column"><h3>{selectedPass === 2 ? 'Semantic checks' : selectedPass === 4 ? 'Correction checks' : 'Polish'}</h3>{preview.data.translations.map(block => <p key={block.id}><small>{block.id}</small>{block.text}</p>)}{preview.data.checks.map((check, index) => <p key={`check-${index}`}><small>{String(check.sid ?? `Check ${index + 1}`)}</small>{describeFinding(check)}</p>)}{preview.data.findings.map((finding, index) => <p key={`finding-${index}`}><small>{String(finding.sid ?? finding.block_id ?? `Finding ${index + 1}`)}</small>{describeFinding(finding)}</p>)}{preview.data.available && !preview.data.translations.length && !preview.data.findings.length && !preview.data.checks.length && <Empty>No findings in this saved result.</Empty>}</div></div>{preview.data.truncated && <p className="subtitle">Preview shortened for this chunk.</p>}</>}</>}</div></Panel>
    </div></div>
    <Activity id={id} expanded title="Recent execution" />
    <Panel debugId="PDG" title="Attempt diagnostics"><details className="translate-diagnostics"><summary>Show recorded attempts and measurement notes</summary><ErrorNote error={usageError} />{usage?.warning && <p className="subtitle">{usage.warning}</p>}<div className="execution-diagnostics">{usage?.units.flatMap(item => item.passes.filter(pass => pass.pass_no > 1).map(pass => <details key={`${item.unit_id}-${pass.pass_no}`}><summary>{item.unit_id} · P{pass.pass_no} · {pass.physical_attempt_count} attempts</summary><p>{pass.provider ?? '—'} · {pass.reported_model ?? pass.requested_model ?? '—'} · {pass.result_status} · {pass.failed_attempt_count} failed</p>{pass.attempts.map(attempt => <p key={attempt.attempt_id}>{attempt.attempt_id} · {attempt.acceptance_status} · {attempt.generation_status}</p>)}</details>))}</div></details></Panel>
    {pendingRun && <Overlay title={pendingRun.rerun ? 'Run this pass again?' : 'Run this pass?'} debugId="TCM" compact close={() => { if (!command.pending) setPendingRun(null) }}><div className="modal-body"><p><strong>{pendingRun.chunkId} · P{pendingRun.passNo}</strong></p><p>This starts one model pass and spends tokens. The exact cost depends on the selected profile and response.</p>{pendingRun.rerun && <p>The new result replaces the selected result for this pass. Earlier artifacts remain saved. Later passes and the final translation may need to be run again.</p>}<p className="subtitle">If earlier chunks in this section or thread are unfinished, a P5 result remains stale until rerun with complete context.</p><ErrorNote error={localError ?? command.error} />{unknownOutcome && <p className="subtitle">The request outcome is unknown. Check the current job before trying again.</p>}</div><div className="modal-foot"><Button onClick={() => setPendingRun(null)}>Cancel</Button><Button variant="primary" disabled={disabled || unknownOutcome} onClick={() => void startTarget()}>{command.pending ? 'Starting…' : pendingRun.rerun ? 'Run again' : 'Run pass'}</Button></div></Overlay>}
  </div>
}
