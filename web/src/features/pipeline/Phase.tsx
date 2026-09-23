import { useContext, useState } from 'react'
import { useParams } from '@tanstack/react-router'
import { endpoint, queryClient, request, Scope, useApi } from '../../api/client'
import { jobSchema, pipelineSchema, preparationSchema, profilesSchema, usageSchema, workspacesSchema, type Usage } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { createRequestKey } from '../../api/requestKey'
import { Back, Button, Empty, ErrorNote, Overlay, Panel, ProfileSwatch } from '../../components/ui/common'
import { Activity, PassMark, phaseStatus } from './Workspace'
import { Action } from './Action'
import { PrepareContent } from './PrepareContent'
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
const measured = (value: ReturnType<typeof usageTotals>[number]) => value.value == null ? '—' : `${value.value.toLocaleString()}${value.partial ? ' (partial)' : ''}`
export function PhasePage() {
  const { workspaceId: id = '', phase = 'prepare' } = useParams({ strict: false })
  const all = useApi('/api/workspaces', workspacesSchema)
  const workspace = all.data?.workspaces.find(w => w.workspace_id === id)
  const query = useApi(endpoint(id,'pipeline'), pipelineSchema, !!workspace?.prepared)
  const usage = useApi(endpoint(id,'usage'), usageSchema, !!workspace?.prepared && ['analyse','translate'].includes(phase))
  const profiles = useApi(endpoint(id,'profiles'), profilesSchema, !!workspace?.prepared)
  const preparation = useApi(endpoint(id,'preparation'), preparationSchema, !!workspace?.prepared && phase === 'prepare')
  const command = useCommand(id)
  const scope = useContext(Scope)
  const [rebuildOpen, setRebuildOpen] = useState(false)
  const [rebuildError, setRebuildError] = useState<unknown>(null)
  const p = query.data
  const title = ({ prepare: 'Prepare', analyse: 'Analyse', translate: 'Translate', publish: 'Publish' } as Record<string,string>)[phase] ?? phase
  const state = p ? phaseStatus(p,phase) : 'blocked'
  const totals = usageTotals(usage.data, phase === 'analyse' ? [1] : [2,3,4,5])
  const metrics = phase === 'prepare' ? [['Format',p?.metadata.format ?? '—'],['Sections',p?.sections.length ?? '—'],['Processable',p?.sections.filter(s => s.processing !== 'excluded').length ?? '—'],['Words',p?.metadata.word_count?.toLocaleString() ?? '—']] : phase === 'publish' ? [['Status',p?.publication.state ?? '—'],['Format',p?.publication.current ? 'EPUB' : '—'],['Sections',p?.sections.filter(s => s.processing !== 'excluded').length ?? '—'],['Size',p?.publication.size_bytes == null ? '—' : `${p.publication.size_bytes.toLocaleString()} bytes`]] : totals.map((t,i) => [labels[i],measured(t)])
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
  return <main className="main phase-detail-page" {...debugTag('PHD')}><Back id={id} /><div className="phase-detail-head" {...debugTag('PHH')}><div className="phase-detail-title"><div className="eyebrow">{title} · {p?.metadata.title ?? workspace?.metadata.title ?? 'Workspace'}</div><h1>{phase === 'analyse' ? 'Analyse · P1' : title}</h1><div className="phase-detail-meta">{phase === 'analyse' ? `Whole-book analysis · ${p?.progress.analysis.required ? `${p.progress.analysis.completed}/${p.progress.analysis.required} required units` : 'plan not created yet'}` : 'Phase details and execution diagnostics'}</div></div><span className={`phase-status-badge ${state}`}>{state === 'done' ? 'Complete' : state === 'blocked' ? 'Blocked' : state === 'error' ? 'Failed' : p?.active_job ? 'Running' : 'Ready'}</span></div><ErrorNote error={query.error ?? usage.error ?? profiles.error} retry={() => void query.refetch()} />
    <div className="phase-metrics" {...debugTag('PHM')}>{metrics.map(([label,value]) => <div className="phase-metric" key={label} {...debugTag('PMT', String(label))}><div className="phase-metric-label">{label}</div><div className="phase-metric-value" title={String(value)}>{value}</div></div>)}</div>
    {!p ? <Empty>{workspace?.prepared ? 'Loading phase details…' : <>Source structure has not been inspected yet. Run Prepare from the workspace to create the frozen section and chunk plan.{workspace && <Action workspace={workspace} />}</>}</Empty> : state === 'blocked' && phase !== 'prepare' ? <Empty>Complete the preceding phase before {title.toLowerCase()}.</Empty> : phase === 'prepare' ? <PrepareContent id={id} pipeline={p} preparation={preparation.data} preparationError={preparation.error} workspace={workspace} canReprepare={canReprepare} commandDisabled={command.disabled} onReprepare={() => setRebuildOpen(true)} /> : <div className="phase-detail-grid"><div className="phase-detail-stack">
      {phase === 'analyse' && <Panel debugId="PAN" title="Analysis units"><div className="diagnostic-scroll"><table className="phase-detail-table usage-table"><thead><tr><th>Section / unit</th><th>Model</th><th>Status</th>{labels.map(label => <th className="num" key={label}>{label}</th>)}</tr></thead><tbody>{p.analysis.units.map(unit => { const actual = usage.data?.units.find(u => u.unit_id === unit.id)?.passes.find(v => v.pass_no === 1); return <tr key={unit.id}><td title={unit.id}>{unit.id}</td><td title={actual?.reported_model ?? actual?.requested_model ?? 'Unavailable'}>{actual?.reported_model ?? actual?.requested_model ?? '—'}</td><td>{unit.state}</td>{fields.map(f => <td className="num" key={f}>{actual?.[f].value?.toLocaleString() ?? '—'}</td>)}</tr> })}</tbody></table></div>{!p.analysis.units.length && <Empty>No persisted analysis plan yet.</Empty>}</Panel>}
      {phase === 'translate' && <><Panel debugId="PTS" title="Pass summary"><div className="diagnostic-scroll"><table className="phase-detail-table pass-summary"><thead><tr><th>Pass</th><th>Default profile</th><th>Units</th>{labels.map(label => <th className="num" key={label}>{label}</th>)}</tr></thead><tbody>{[2,3,4,5].map(n => <tr key={n}><td>P{n}</td><td title="Current default; actual historical models may differ by section.">{currentProfile(n)}</td><td title={n < 5 ? 'Retained checkpoints have not been revalidated against current inputs.' : 'Verified completed final units'}>{p.units.filter(u => u.passes[String(n)]?.checkpoint_state === 'completed').length}/{p.units.length}{n < 5 ? ' verified' : ''}</td>{usageTotals(usage.data,[n]).map((t,i) => <td className="num" key={i} title={measured(t)}>{measured(t)}</td>)}</tr>)}</tbody></table></div></Panel><Panel debugId="PSC" title="Section progress"><table className="phase-detail-table"><thead><tr><th>Section</th>{[2,3,4,5].map(n => <th key={n}>P{n}</th>)}</tr></thead><tbody>{p.sections.filter(s => s.processing !== 'excluded').map(s => <tr key={s.id}><td title={s.title ?? s.fallback_excerpt}>{s.title || s.fallback_excerpt}</td>{[2,3,4,5].map(n => <td key={n}><PassMark section={s} number={n} /></td>)}</tr>)}</tbody></table></Panel></>}
      {phase === 'publish' && <><Panel debugId="PPO" title="Published output"><dl className="phase-kv"><dt>File</dt><dd>{p.publication.filename ?? '—'}</dd><dt>Resource</dt><dd>{p.publication.current ? 'Current workspace EPUB' : 'Unavailable'}</dd><dt>Language</dt><dd>{p.publication.target_language}</dd><dt>Edition</dt><dd>{p.publication.generated_by ?? '—'}</dd><dt>Source sections</dt><dd>{p.sections.length}</dd><dt>Excluded</dt><dd>{p.sections.filter(s => s.processing === 'excluded').length}</dd></dl>{p.publication.current && <a className="secondary-btn download" href={endpoint(id,'publication/download')}>Open EPUB</a>}{p.publication.last_error && <p className="subtitle">{p.publication.last_error}</p>}</Panel><Panel debugId="PPV" title="Package validation">{p.publication.checks.length ? p.publication.checks.map(check => <p className="phase-check" key={check}><span className="phase-check-icon">✓</span>{check}</p>) : <Empty>No current publication validation results.</Empty>}</Panel></>}
    </div><div className="phase-detail-stack">
      {phase === 'translate' ? <Activity id={id} expanded title="Recent execution" /> : phase === 'analyse' ? <Panel debugId="PGD" title="Generated data"><dl className="phase-kv"><dt>Terms / entities</dt><dd>{p.review.summary?.total ?? '—'}</dd><dt>Analysis units</dt><dd>{p.analysis.units.length}</dd><dt>Default profile</dt><dd>{currentProfile(1)}</dd><dt>Review gate</dt><dd>{p.approved ? 'Committed approval' : p.analysis.complete ? 'Ready for review' : 'Not ready'}</dd></dl></Panel> : <Panel debugId="PMD" title="Metadata"><dl className="phase-kv"><dt>Title</dt><dd>{p.metadata.title}</dd><dt>Author</dt><dd>{p.metadata.creators.join(', ') || '—'}</dd><dt>Source language</dt><dd>{p.metadata.language ?? '—'}</dd><dt>Output language</dt><dd>{p.publication.target_language}</dd><dt>Publisher</dt><dd>{p.publication.generated_by ?? '—'}</dd></dl></Panel>}
      {phase === 'analyse' && <Panel debugId="PAR" title="Artifacts"><p className="phase-check">{p.artifacts.terminology ? '✓' : '○'} Terminology candidates · {p.artifacts.terminology ? 'Available' : 'Not available'}</p><p className="phase-check">{p.artifacts.book_memory ? '✓' : '○'} Book memory · {p.artifacts.book_memory ? 'Available' : 'Not available'}</p></Panel>}
      {phase === 'translate' && <Panel debugId="PDG" title="Diagnostics"><p className="subtitle">Usage covers recorded attempts, including retries. Cache and reasoning can be subsets; do not add these totals together.</p><ErrorNote error={usage.error} />{usage.data?.warning && <p className="subtitle">{usage.data.warning}</p>}<div className="execution-diagnostics">{usage.data?.units.flatMap(u => u.passes.filter(pass => pass.pass_no > 1).map(pass => <details key={`${u.unit_id}-${pass.pass_no}`}><summary>{u.unit_id} · P{pass.pass_no} · {pass.physical_attempt_count} attempts</summary><p>{pass.provider ?? '—'} · {pass.reported_model ?? pass.requested_model ?? '—'} · {pass.result_status} · {pass.failed_attempt_count} failed · {pass.elapsed_seconds.value == null ? '—' : `${pass.elapsed_seconds.value.toFixed(2)} s`}</p>{pass.attempts.map(a => <p key={a.attempt_id}>{a.attempt_id} · {a.acceptance_status} · {a.generation_status} · {a.reported_model ?? '—'} · {a.elapsed_seconds == null ? '—' : `${a.elapsed_seconds.toFixed(2)} s`}</p>)}</details>))}</div></Panel>}
      {phase === 'publish' && <Activity id={id} publicationOnly expanded title="Publication log" />}
    </div></div>}
    {rebuildOpen && <Overlay title="Run Prepare again?" debugId="RPM" compact close={() => { if (!command.pending) setRebuildOpen(false) }}><div className="modal-body"><p>Rebuild source sections in this workspace without contacting a model. The current Prepare plan will be saved as a version. Your section F/T/E choices will be reset because section boundaries and IDs may change.</p><ErrorNote error={rebuildError ?? command.error} /></div><div className="modal-foot"><Button onClick={() => setRebuildOpen(false)}>Cancel</Button><Button variant="primary" disabled={!canReprepare || command.disabled} onClick={() => void runReprepare()}>Rebuild Prepare</Button></div></Overlay>}
  </main>
}
