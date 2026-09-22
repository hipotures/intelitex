import { useContext, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { ChevronDown } from 'lucide-react'
import { endpoint, Scope, useApi } from '../../api/client'
import { activitySchema, configSchema, lifecycleSchema, pipelineSchema, previewSchema, profilesSchema, workspacesSchema, type Pipeline, type Section } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { Back, Button, Cover, Empty, ErrorNote, Overlay, ProfileSwatch } from '../../components/ui/common'
import { useLive } from '../../realtime/coordinator'
import { preference, savePreference } from '../../app/preferences'
import { announce } from '../../app/notifications'
import { Action } from './Action'
import { debugTag, pipelineModelIds, sectionModelIds } from '../../debug/regions'
const modes = [['full', 'F', 'Full'], ['translate', 'T', 'Translate only'], ['excluded', 'E', 'Excluded']] as const
const contentTypes = ['narrative','contents','glossary','footnotes','front_matter','back_matter','advertisement','unclassified']
export function phaseStatus(p: Pipeline, phase: string) {
  if (p.last_job?.state === 'failed' && (p.last_job.operation === 'analyze' && phase === 'analyse' || p.last_job.operation === 'translate' && phase === 'translate' && !p.translation_complete || phase === 'publish' && p.publication.last_failure)) return 'error'
  if (phase === 'prepare') return 'done'
  if (phase === 'analyse') return p.analysis.complete ? 'done' : 'active'
  if (phase === 'review') return !p.analysis.complete ? 'blocked' : p.approved ? 'done' : 'active'
  if (phase === 'translate') return !p.approved ? 'blocked' : p.translation_complete ? 'done' : 'active'
  return !p.translation_complete ? 'blocked' : p.publication.current ? 'done' : 'active'
}
export function PassMark({ section, number }: { section: Section; number: number }) {
  const cell = section.passes[String(number)]
  const state = cell?.runtime_state ?? cell?.state ?? 'unknown'
  const provenance = cell?.provenance ?? []
  const color = provenance.length === 1 ? provenance[0]?.stable_palette_index : null
  const identity = provenance.length ? provenance.map(v => [v.profile,v.provider,v.model].filter(Boolean).join(' · ')).join('; ') : 'Model identity unavailable'
  return <span tabIndex={0} data-palette={color == null ? undefined : color % 18} className={`${color == null ? '' : 'has-model'} pass-mark ${state === 'completed' ? 'done' : state === 'not_applicable' ? 'na' : state}`} aria-label={`P${number}: ${state}, ${cell?.completed ?? 0}/${cell?.required ?? 0} units`} title={`${identity}. P${number} · ${state} · ${cell?.completed ?? 0}/${cell?.required ?? 0} required units. ${state === 'retained' ? 'Historical result retained; current completion is unverified.' : ''}`}>
    {state === 'completed' ? '✓' : state === 'error' ? '×' : state === 'stale' ? '↻' : state === 'not_applicable' || state === 'unknown' ? '—' : state === 'partial' ? '◐' : state === 'running' ? '◌' : ''}</span>
}
export function Activity({ id, expanded = false, publicationOnly = false, title }: { id: string; expanded?: boolean; publicationOnly?: boolean; title?: string }) {
  const scope = useContext(Scope)
  const [open, setOpen] = useState(expanded || preference(scope, `${id}.activity`) === 'true')
  const query = useApi(endpoint(id, 'activity'), activitySchema, open)
  const live = useLive()
  const entries = [...new Map([...(query.data?.events ?? []), ...(live.state.activity[id] ?? [])].map(e => [e.id, e])).values()].sort((a, b) => a.id - b.id).filter(e => !publicationOnly || e.event.kind.startsWith('publication')).slice(-120)
  const ref = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  const [newActivity, setNewActivity] = useState(false)
  const last = entries.at(-1)?.id
  useEffect(() => {
    if (open && ref.current && following.current) ref.current.scrollTop = ref.current.scrollHeight
    if (open && !following.current) setNewActivity(true)
  }, [open, last])
  return <section className={title ? 'phase-detail-card' : 'accordion'} {...debugTag(publicationOnly ? 'PUL' : title ? 'RAL' : 'LVA')}><button className={title ? 'phase-detail-card-head activity-card-head' : 'accordion-head'} onClick={() => { setOpen(!open); following.current = true; savePreference(scope, `${id}.activity`, String(!open)) }} aria-expanded={open}>
    <span><span className="accordion-title">{title ?? (publicationOnly ? 'Publication log' : 'Live activity')}</span><span className="activity-count">{entries.length}</span>{!title && <span className="accordion-sub">Pipeline events and model waits</span>}</span><ChevronDown size={16} /></button>
    {open && <div className="accordion-body activity-body"><ErrorNote error={query.error} /><div ref={ref} className="activity-stream" onScroll={() => { const e = ref.current!; following.current = e.scrollHeight - e.scrollTop - e.clientHeight < 24; if (following.current) setNewActivity(false) }}>
      {entries.slice(-80).map(e => <div className="activity-line" key={e.id}><time>{new Date(e.timestamp).toLocaleTimeString()}</time><span>{e.event.kind.replaceAll('_', ' ')} {typeof e.event.values.state === 'string' ? e.event.values.state : ''}{e.event.current != null && e.event.total != null ? ` · ${e.event.current}/${e.event.total}` : ''}{typeof e.event.values.unit_id === 'string' ? ` · ${e.event.values.unit_id}` : ''}</span></div>)}{!entries.length && <Empty>No recorded activity yet.</Empty>}</div>
      {newActivity && <Button variant="ghost" onClick={() => { following.current = true; setNewActivity(false); if (ref.current) ref.current.scrollTop = ref.current.scrollHeight }}>New activity ↓</Button>}</div>}
  </section>
}
export function WorkspacePage() {
  const { workspaceId: id = '' } = useParams({ strict: false })
  const all = useApi('/api/workspaces', workspacesSchema)
  const workspace = all.data?.workspaces.find(w => w.workspace_id === id)
  const query = useApi(endpoint(id, 'pipeline'), pipelineSchema, !!workspace?.prepared)
  const profiles = useApi(endpoint(id, 'profiles'), profilesSchema, !!workspace?.prepared)
  const command = useCommand(id)
  const navigate = useNavigate()
  const search = useSearch({ strict: false })
  const scope = useContext(Scope)
  const filter = search.filter ?? preference(scope, `${id}.filter`, 'all')
  const selected = search.section
  const [models, setModels] = useState(() => preference(scope, `${id}.models`) === 'true')
  const [archive, setArchive] = useState(false)
  const [assignment, setAssignment] = useState<{ section?: string; number: string; value: string | null } | null>(null)
  const p = query.data
  const section = p?.sections.find(s => s.id === selected)
  const [previewPage, setPreviewPage] = useState(0)
  const preview = useApi(endpoint(id, `sections/${encodeURIComponent(selected ?? '')}/${previewPage}`), previewSchema, !!section)
  const setSearch = (values: Partial<typeof search>) => { if (values.filter) savePreference(scope, `${id}.filter`, values.filter); void navigate({ to: '.', search: old => ({ ...old, ...values }), replace: true }) }
  async function configure(body: Record<string, unknown>, sectionId?: string) {
    if (!p) return
    return await command.send(endpoint(id, sectionId ? `sections/${encodeURIComponent(sectionId)}` : 'settings'), configSchema, { revision: p.config.revision, ...body }, 'PATCH')
  }
  if (!workspace) return <main className="main"><Back /><ErrorNote error={all.error} /><Empty>{all.isPending ? 'Loading workspace…' : 'Workspace not found.'}</Empty></main>
  return <main className="main workspace-page" {...debugTag('WSP', id)}><Back /><div className="workspace-head" {...debugTag('WHH')}><div className="workspace-heading"><Cover title={workspace.metadata.title} /><div><div className="eyebrow">Workspace</div><h1>{workspace.metadata.title}</h1>{workspace.metadata.label && <div className="workspace-label">{workspace.metadata.label}</div>}<div className="workspace-meta-line"><span>{workspace.metadata.creators.join(', ') || '—'}</span><span>·</span><span>{workspace.metadata.source_language ?? workspace.metadata.language ?? 'Source language unconfirmed'} → {workspace.metadata.target_language ?? 'target unknown'}</span>{workspace.metadata.word_count != null && <><span>·</span><span>{workspace.metadata.word_count.toLocaleString()} words</span></>}</div></div></div><div className="workspace-head-actions"><Action workspace={workspace} pipeline={p} /></div></div>
    <ErrorNote error={query.error ?? command.error} retry={() => void query.refetch()} />
    {p?.last_job && ['failed','abandoned','cancelled'].includes(p.last_job.state) && !p.active_job && <div className={`notice ${p.last_job.state === 'failed' ? 'error' : ''}`}><span>{p.last_job.state === 'abandoned' ? 'The previous server stopped. Reconcile the saved checkpoints before starting another job.' : p.last_job.state === 'cancelled' ? 'Stopped. Saved checkpoints are retained; Run continues eligible work.' : p.last_job.error?.message ?? 'The operation failed. Saved checkpoints are retained.'}</span></div>}
    <div className="phase-rail" {...debugTag('PHR')}>{(['prepare','analyse','review','translate','publish'] as const).map(phase => {
      const state = p ? phaseStatus(p, phase) : 'blocked'
      const subtitle = !p ? phase === 'prepare' ? 'Inspect source locally' : 'Blocked' : phase === 'prepare' ? 'Source structure frozen' : phase === 'analyse' ? `${p.progress.analysis.completed}/${p.progress.analysis.required} analysed` : phase === 'review' ? p.approved ? 'Confirmed' : 'Human gate' : phase === 'translate' ? `${p.progress.translation.completed}/${p.progress.translation.required} complete` : p.publication.state.replaceAll('_', ' ')
      return <button key={phase} className={`phase ${state} ${state !== 'blocked' ? 'clickable' : ''}`} disabled={state === 'blocked'} title={state === 'blocked' ? 'Complete the preceding phase first.' : `Open ${phase} details`} onClick={() => void navigate({ to: '/work/workspaces/$workspaceId/$phase', params: { workspaceId: id, phase } })}><span className="phase-top"><span className="phase-icon">{state === 'done' ? '✓' : ''}</span><span className="phase-name">{phase[0]!.toUpperCase() + phase.slice(1)}</span></span><span className="phase-sub">{state === 'blocked' ? 'Blocked' : subtitle}</span></button>
    })}</div>
    {!workspace.prepared ? <div className="prepare-card" {...debugTag('PRC')}><div className="prepare-icon">P</div><h2>Prepare this workspace</h2><p className="subtitle">Inspect the source and freeze its sections and translation units before running analysis.</p><Action workspace={workspace} /></div> : !p ? <Empty>Loading source structure…</Empty> : <>
      {p.analysis.complete && !p.approved && <div className="notice"><span>Analysis is complete. Review and confirm terminology before translation.</span><Button onClick={() => void navigate({ to: '/work/workspaces/$workspaceId/$phase', params: { workspaceId: id, phase: 'review' } })}>Open Review</Button></div>}
      <div className="workspace-toolbar" {...debugTag('STF')}><div className="toolbar-left"><span className="toolbar-title">Sections</span><span className="toolbar-sub">{p.sections.length} detected · {p.sections.filter(s => s.processing === 'excluded').length} excluded</span></div><div className="filter-segment">{[['all','All'],['full','Full'],['translate','Translate only'],['excluded','Excluded']].map(([key, label]) => <button key={key} className={filter === key ? 'active' : ''} onClick={() => setSearch({ filter: key as typeof filter })}>{label}</button>)}</div></div>
      <div className="table-card" {...debugTag('SCT')}><table className="sections-table"><colgroup><col className="section-col" /><col className="processing-col" />{[1,2,3,4,5].map(n => <col className="pass-col" key={n} />)}</colgroup><thead><tr><th>Section</th><th>Processing</th>{[1,2,3,4,5].map(n => <th className="pass-cell" key={n}>P{n}</th>)}</tr></thead><tbody>{p.sections.filter(s => filter === 'all' || s.processing === filter).map(s => <tr className={s.processing === 'excluded' ? 'excluded' : ''} key={s.id} onClick={e => { if (!(e.target as HTMLElement).closest('button')) { setPreviewPage(0); setSearch({ section: s.id }) } }}><td title={s.title || s.fallback_excerpt}><span className="section-number">{String(s.ordinal).padStart(2,'0')}</span><button className={`section-name section-open ${s.title ? '' : 'fallback'}`} onClick={() => { setPreviewPage(0); setSearch({ section: s.id }) }}>{s.title || s.fallback_excerpt || 'Untitled section'}</button></td><td><div className="processing-switch" aria-label={`Processing ${s.title ?? s.id}`}>{modes.map(([mode, letter, label]) => <button key={mode} data-mode-value={label} title={`${letter} — ${label}`} aria-pressed={s.processing === mode} className={s.processing === mode ? 'active' : ''} disabled={command.disabled || p.busy || p.metadata.lifecycle.archived || (p.analysis.membership_locked && mode !== s.processing && (mode === 'full' || s.processing === 'full'))} onClick={() => void configure({ processing: mode }, s.id)}>{letter}</button>)}</div></td>{[1,2,3,4,5].map(n => <td className="pass-cell" key={n}><PassMark section={s} number={n} /></td>)}</tr>)}</tbody></table></div>
      <section className="accordion" {...debugTag('PMA')}><button className="accordion-head" aria-expanded={models} onClick={() => { setModels(!models); savePreference(scope, `${id}.models`, String(!models)) }}><span><span className="accordion-title">Pipeline models</span><span className="accordion-sub">Assign configured profiles to P1–P5</span></span><ChevronDown size={16} /></button>{models && <div className="accordion-body"><ErrorNote error={profiles.error} /><div className="model-grid">{[1,2,3,4,5].map(n => <div className="model-slot" key={n} {...debugTag(pipelineModelIds[n - 1]!)}><label htmlFor={`profile-${n}`}><ProfileSwatch index={profiles.data?.resolved_passes[String(n)]?.stable_palette_index} name={profiles.data?.resolved_passes[String(n)]?.name ?? 'Unknown profile'} /><span>Pass {n}</span></label><select id={`profile-${n}`} disabled={command.disabled || p.busy || p.metadata.lifecycle.archived} value={profiles.data?.assignments[String(n)] ?? ''} onChange={e => setAssignment({ number: String(n), value: e.target.value || null })}><option value="">Inherit — {profiles.data?.default_profile ?? '—'}</option>{profiles.data?.profiles.map(profile => <option key={profile.name} disabled={!profile.enabled} value={profile.name}>{profile.name}</option>)}</select></div>)}</div></div>}</section>
      <Activity id={id} /><div className="workspace-bottom-actions"><button className="archive-workspace-btn" disabled={command.disabled || p.busy || p.metadata.lifecycle.archived} onClick={() => setArchive(true)}>Archive workspace</button></div>
    </>}
    {!workspace.prepared && !workspace.metadata.lifecycle.archived && <div className="workspace-bottom-actions"><button className="archive-workspace-btn" disabled={command.disabled || !!workspace.active_job} onClick={() => setArchive(true)}>Archive workspace</button></div>}
    {section && !assignment && <Overlay drawer title={section.title || section.fallback_excerpt || 'Untitled section'} eyebrow="Section" debugId="PVD" close={() => setSearch({ section: undefined })}><div className="drawer-body"><div className="field-row"><div className="field"><label htmlFor="section-mode">Processing</label><select id="section-mode" value={section.processing} disabled={command.disabled || !!p?.busy || p?.metadata.lifecycle.archived} onChange={e => void configure({ processing: e.target.value }, section.id)}>{modes.map(([mode, letter, label]) => <option key={mode} value={mode} disabled={p?.analysis.membership_locked && mode !== section.processing && (mode === 'full' || section.processing === 'full')}>{letter} — {label}</option>)}</select></div><div className="field"><label htmlFor="section-type">Content type · metadata only</label><select id="section-type" value={section.content_type} disabled={command.disabled || !!p?.busy || p?.metadata.lifecycle.archived} onChange={e => void configure({ content_type: e.target.value }, section.id)}>{[...new Set([...contentTypes, section.content_type])].map(t => <option key={t} value={t}>{t.replaceAll('_',' ')}</option>)}</select></div></div><div className="autosave-note">Changes are applied immediately. Only Processing controls pipeline inclusion; Content type is descriptive metadata.</div><ErrorNote error={command.error} />
      <div className="field"><label>Model overrides · optional</label><div className="drawer-model-grid" {...debugTag('SMG')}>{[1,2,3,4,5].map(n => <div className="drawer-model-slot" key={n} {...debugTag(sectionModelIds[n - 1]!)}><label className="drawer-model-label" htmlFor={`override-${n}`}><ProfileSwatch index={profiles.data?.profiles.find(v => v.name === (section.profiles[String(n)] ?? profiles.data?.resolved_passes[String(n)]?.name))?.stable_palette_index} name={section.profiles[String(n)] ?? profiles.data?.resolved_passes[String(n)]?.name ?? 'Unknown profile'} /><span>Pass {n}</span></label><select id={`override-${n}`} value={section.profiles[String(n)] ?? ''} disabled={command.disabled || !!p?.busy || p?.metadata.lifecycle.archived} onChange={e => setAssignment({ section: section.id, number: String(n), value: e.target.value || null })}><option value="">Inherit — {profiles.data?.resolved_passes[String(n)]?.name ?? '—'}</option>{profiles.data?.profiles.map(profile => <option key={profile.name} value={profile.name} disabled={!profile.enabled}>{profile.name}</option>)}</select></div>)}</div></div><ErrorNote error={preview.error} /><div className="preview-text">{preview.data?.blocks.map((b, i) => <p key={`${previewPage}-${b.id}-${i}`}>{b.text}</p>)}</div><div className="preview-pagination"><Button disabled={previewPage === 0} onClick={() => setPreviewPage(previewPage - 1)}>Previous</Button><span>Source preview · page {previewPage + 1}</span><Button disabled={preview.data?.next_page == null} onClick={() => setPreviewPage(preview.data!.next_page!)}>Load more</Button></div></div></Overlay>}
    {assignment && <Overlay title="Change model assignment?" debugId="MCM" compact close={() => setAssignment(null)}><div className="modal-body"><p>Apply {assignment.value ?? 'the inherited profile'} to future Pass {assignment.number} work. Completed results and their model history are kept.</p><ErrorNote error={command.error} /></div><div className="modal-foot"><Button onClick={() => setAssignment(null)}>Cancel</Button><Button variant="primary" disabled={command.disabled} onClick={async () => { if (await configure({ [assignment.section ? 'profiles' : 'pass_profiles']: { [assignment.number]: assignment.value }, allow_model_change: true }, assignment.section)) setAssignment(null) }}>Apply change</Button></div></Overlay>}
    {archive && (p || !workspace.prepared) && <Overlay title="Archive workspace?" eyebrow="Workspace" debugId="ACM" compact close={() => { if (!command.pending) setArchive(false) }}><div className="modal-body"><p className="subtitle"><strong>{workspace.metadata.title}</strong> will be removed from active workspaces. Its state will be kept in the archive and can be restored later.</p><ErrorNote error={command.error} /></div><div className="modal-foot"><Button onClick={() => setArchive(false)}>Cancel</Button><Button variant="danger" disabled={command.disabled} onClick={async () => { if (await command.send(endpoint(id, 'archive'), lifecycleSchema, { revision: (p?.metadata ?? workspace.metadata).lifecycle.revision })) { setArchive(false); announce('Workspace moved to archive.'); await navigate({ to: '/work' }) } }}>Archive workspace</Button></div></Overlay>}
  </main>
}
