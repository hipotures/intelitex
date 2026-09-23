import { useContext, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { ChevronDown } from 'lucide-react'
import { endpoint, matchesWorkspaceResource, queryClient, Scope, useApi } from '../../api/client'
import { activitySchema, configSchema, lifecycleSchema, pipelineSchema, previewSchema, profilesSchema, workspacesSchema, type Pipeline, type Profiles, type Section } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { Back, Button, Cover, Empty, ErrorNote, Overlay, ProfileSwatch } from '../../components/ui/common'
import { useConnection, useLive } from '../../realtime/coordinator'
import { preference, savePreference } from '../../app/preferences'
import { announce } from '../../app/notifications'
import { Action } from './Action'
import { sectionTitles } from './sectionTitles'
import { debugTag, pipelineModelIds, sectionModelIds } from '../../debug/regions'
const modes = [['full', 'F', 'Full'], ['translate', 'T', 'Translate only'], ['excluded', 'E', 'Excluded']] as const
const contentTypes = ['narrative','contents','glossary','footnotes','front_matter','back_matter','advertisement','unclassified']
export function phaseStatus(p: Pipeline, phase: string) {
  if (phase === 'prepare' && p.active_job?.operation === 'import') return 'active'
  if (p.last_job?.state === 'failed' && (p.last_job.operation === 'analyze' && phase === 'analyse' || p.last_job.operation === 'translate' && phase === 'translate' && !p.translation_complete || phase === 'publish' && p.publication.last_failure)) return 'error'
  if (phase === 'prepare') return 'done'
  if (phase === 'analyse') return p.analysis.complete ? 'done' : p.active_job?.operation === 'analyze' ? 'active' : 'ready'
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
      {entries.slice(-80).map(e => <div className="activity-line" key={e.id}><time>{new Date(e.timestamp).toLocaleTimeString()}</time><span>{e.event.kind.replaceAll('_', ' ')} {typeof e.event.values.state === 'string' ? e.event.values.state : ''}{typeof e.event.values.pass_no === 'number' ? ` · P${e.event.values.pass_no}` : ''}{typeof e.event.values.attempt_number === 'number' ? ` · attempt ${e.event.values.attempt_number}` : ''}{e.event.current != null && e.event.total != null ? ` · ${e.event.current}/${e.event.total}` : ''}{typeof e.event.values.unit_id === 'string' ? ` · ${e.event.values.unit_id}` : typeof e.event.values.chunk_id === 'string' ? ` · ${e.event.values.chunk_id}` : ''}</span></div>)}{!entries.length && <Empty>No recorded activity yet.</Empty>}</div>
      {newActivity && <Button variant="ghost" onClick={() => { following.current = true; setNewActivity(false); if (ref.current) ref.current.scrollTop = ref.current.scrollHeight }}>New activity ↓</Button>}</div>}
  </section>
}
export function WorkspacePage() {
  const { workspaceId: id = '' } = useParams({ strict: false })
  const all = useApi('/api/workspaces', workspacesSchema)
  const workspace = all.data?.workspaces.find(w => w.workspace_id === id)
  const query = useApi(endpoint(id, 'pipeline'), pipelineSchema, !!workspace?.prepared)
  const profiles = useApi(endpoint(id, 'profiles'), profilesSchema, !!workspace)
  const command = useCommand(id)
  const connection = useConnection()
  const navigate = useNavigate()
  const search = useSearch({ strict: false })
  const scope = useContext(Scope)
  const filter = search.filter ?? preference(scope, `${id}.filter`, 'all')
  const selected = search.section
  const [models, setModels] = useState(() => preference(scope, `${id}.models`) === 'true')
  const [draftModels, setDraftModels] = useState(true)
  const [archive, setArchive] = useState(false)
  type Assignment = { section?: string; number: string; value: string | null }
  const [assignmentIntents, setAssignmentIntents] = useState<Record<string, string | null>>({})
  const assignmentQueue = useRef<Assignment[]>([])
  const assignmentProcessing = useRef(false)
  const assignmentRevision = useRef<string | null>(null)
  const p = query.data
  if (!assignmentProcessing.current) assignmentRevision.current = p?.config.revision ?? (!workspace?.prepared ? profiles.data?.revision ?? null : null)
  const displayedTitles = p ? sectionTitles(p.sections, p.metadata.creators) : new Map<string, string>()
  const draftJob = !workspace?.prepared && workspace?.last_job?.operation === 'import' ? workspace.last_job : null
  const draftPreparing = !!workspace && !workspace.prepared && !!workspace.active_job && workspace.active_job.operation === 'import'
  const draftFailed = !draftPreparing && draftJob?.state === 'failed'
  const draftP1 = profiles.data?.resolved_passes['1']
  const section = p?.sections.find(s => s.id === selected)
  const [previewPage, setPreviewPage] = useState(0)
  const preview = useApi(endpoint(id, `sections/${encodeURIComponent(selected ?? '')}/${previewPage}`), previewSchema, !!section)
  const setSearch = (values: Partial<typeof search>) => { if (values.filter) savePreference(scope, `${id}.filter`, values.filter); void navigate({ to: '.', search: old => ({ ...old, ...values }), replace: true }) }
  async function configure(body: Record<string, unknown>, sectionId?: string) {
    const revision = p?.config.revision ?? (!sectionId && !workspace?.prepared ? profiles.data?.revision : undefined)
    if (!revision) return
    return command.send(endpoint(id, sectionId ? `sections/${encodeURIComponent(sectionId)}` : 'settings'), configSchema,
      { revision, ...body }, 'PATCH')
  }
  const assignmentKey = (item: Assignment) => `${item.section ?? 'global'}:${item.number}`
  async function flushAssignments() {
    if (assignmentProcessing.current) return
    assignmentProcessing.current = true
    while (assignmentQueue.current.length) {
      const next = assignmentQueue.current.shift()!
      const revision = assignmentRevision.current
      if (!revision) { assignmentQueue.current = []; setAssignmentIntents({}); break }
      const result = await command.send(endpoint(id, next.section ? `sections/${encodeURIComponent(next.section)}` : 'settings'),
        configSchema, { revision, [next.section ? 'profiles' : 'pass_profiles']: { [next.number]: next.value },
          allow_model_change: true }, 'PATCH', true)
      if (!result) { assignmentQueue.current = []; setAssignmentIntents({}); break }
      assignmentRevision.current = result.revision
      await queryClient.cancelQueries({ predicate: item => item.queryKey[0] === scope &&
        [endpoint(id, 'pipeline'), endpoint(id, 'profiles')].includes(String(item.queryKey[1])) })
      queryClient.setQueryData<Pipeline>([scope, endpoint(id, 'pipeline')], old => old ? {
        ...old, config: result,
        sections: next.section ? old.sections.map(section => section.id === next.section
          ? { ...section, profiles: { ...section.profiles, [next.number]: next.value } } : section) : old.sections,
      } : old)
      queryClient.setQueryData<Profiles>([scope, endpoint(id, 'profiles')], old => {
        if (!old) return old
        const selected = next.value ? old.profiles.find(profile => profile.name === next.value) : null
        return { ...old, revision: result.revision,
          assignments: next.section ? old.assignments : { ...old.assignments, [next.number]: next.value },
          resolved_passes: !next.section && selected ? { ...old.resolved_passes, [next.number]: selected } : old.resolved_passes }
      })
      const key = assignmentKey(next)
      setAssignmentIntents(old => {
        if (old[key] !== next.value) return old
        const updated = { ...old }; delete updated[key]; return updated
      })
    }
    assignmentProcessing.current = false
    void queryClient.invalidateQueries({ predicate: item => item.queryKey[0] === scope &&
      matchesWorkspaceResource(String(item.queryKey[1]), id) })
    void queryClient.invalidateQueries({ queryKey: [scope, '/api/workspaces'], refetchType: 'none' })
  }
  function saveAssignment(next: Assignment) {
    const key = assignmentKey(next)
    assignmentQueue.current = [...assignmentQueue.current.filter(item => assignmentKey(item) !== key), next]
    setAssignmentIntents(old => ({ ...old, [key]: next.value }))
    void flushAssignments()
  }
  if (!workspace) return <main className="main"><Back /><ErrorNote error={all.error} /><Empty>{all.isPending ? 'Loading workspace…' : 'Workspace not found.'}</Empty></main>
  const modelsOpen = workspace.prepared ? models : draftModels
  const modelPanel = <section className="accordion" {...debugTag('PMA')}><button className="accordion-head" aria-expanded={modelsOpen} onClick={() => {
    if (workspace.prepared) { setModels(!models); savePreference(scope, `${id}.models`, String(!models)) }
    else setDraftModels(!draftModels)
  }}><span><span className="accordion-title">Pipeline models</span><span className="accordion-sub">{workspace.prepared ? 'Assign configured profiles to P1–P5' : 'Change saved profiles before Prepare'}</span></span><ChevronDown size={16} /></button>{modelsOpen && <div className="accordion-body"><ErrorNote error={profiles.error ?? command.error} retry={() => void profiles.refetch()} /><div className="model-grid">{[1,2,3,4,5].map(n => {
    const key = `global:${n}`
    const hasIntent = Object.prototype.hasOwnProperty.call(assignmentIntents, key)
    const value = hasIntent ? assignmentIntents[key] : profiles.data?.assignments[String(n)]
    return <div className="model-slot" key={n} {...debugTag(pipelineModelIds[n - 1]!)}><label htmlFor={`profile-${n}`}><ProfileSwatch index={profiles.data?.resolved_passes[String(n)]?.stable_palette_index} name={profiles.data?.resolved_passes[String(n)]?.name ?? 'Unknown profile'} /><span>Pass {n}</span></label><select id={`profile-${n}`} disabled={!profiles.data || (workspace.prepared && !p) || (command.pending && !assignmentProcessing.current) || connection !== 'Live' || !!p?.busy || workspace.metadata.lifecycle.archived || !!workspace.active_job} value={value ?? ''} onChange={e => saveAssignment({ number: String(n), value: e.target.value || null })}>{workspace.prepared ? <option value="">Inherit — {profiles.data?.default_profile ?? '—'}</option> : !profiles.data ? <option value="">Loading profiles…</option> : null}{profiles.data?.profiles.map(profile => <option key={profile.name} disabled={!profile.enabled} value={profile.name}>{profile.name}</option>)}</select></div>
  })}</div></div>}</section>
  return <main className="main workspace-page" {...debugTag('WSP', id)}><Back /><div className="workspace-head" {...debugTag('WHH')}><div className="workspace-heading"><Cover title={workspace.metadata.title} /><div><div className="eyebrow">Workspace</div><h1>{workspace.metadata.title}</h1>{workspace.metadata.label && <div className="workspace-label">{workspace.metadata.label}</div>}<div className="workspace-meta-line"><span>{workspace.metadata.creators.join(', ') || '—'}</span><span>·</span><span>{workspace.metadata.source_language ?? workspace.metadata.language ?? 'Source language unconfirmed'} → {workspace.metadata.target_language ?? 'target unknown'}</span>{workspace.metadata.word_count != null && <><span>·</span><span>{workspace.metadata.word_count.toLocaleString()} words</span></>}<span className="workspace-debug-identity">Workspace ID: <code>{id}</code></span></div></div></div>{workspace.prepared && <div className="workspace-head-actions"><Action workspace={workspace} pipeline={p} /></div>}</div>
    <ErrorNote error={query.error ?? command.error} retry={() => void query.refetch()} />
    {draftFailed && <div className="notice error" role="alert"><span>Prepare failed before the source structure was saved. Check the source and retry. The draft is still available.</span></div>}
    {p?.last_job && ['failed','abandoned','cancelled'].includes(p.last_job.state) && !p.active_job && <div className={`notice ${p.last_job.state === 'failed' ? 'error' : ''}`}><span>{p.last_job.state === 'abandoned' ? 'The previous server stopped. Reconcile the saved checkpoints before starting another job.' : p.last_job.state === 'cancelled' ? 'Stopped. Saved checkpoints are retained; Run continues eligible work.' : p.last_job.error?.message === 'Operation failed; inspect locally.' ? 'A previous job failed; its detailed reason was not recorded. Saved checkpoints remain available.' : p.last_job.error?.message ?? 'A previous job failed. Saved checkpoints remain available.'}</span></div>}
    <div className="phase-rail" {...debugTag('PHR')}>{(['prepare','analyse','review','translate','publish'] as const).map(phase => {
      const state = p ? phaseStatus(p, phase) : phase === 'prepare' && !workspace.prepared ? draftFailed ? 'error' : 'active' : 'blocked'
      const subtitle = !p ? phase === 'prepare' ? draftFailed ? 'Failed' : draftPreparing ? 'Preparing source…' : 'Ready to prepare' : 'Blocked' : phase === 'prepare' ? p.active_job?.operation === 'import' ? 'Rebuilding source…' : 'Source structure frozen' : phase === 'analyse' ? p.active_job?.operation === 'analyze' ? 'Running P1 analysis…' : p.progress.analysis.completed ? `Ready to continue · ${p.progress.analysis.completed}/${p.progress.analysis.required}` : p.analysis.membership_locked ? 'Ready to retry P1' : 'Start P1 with Run' : phase === 'review' ? p.approved ? 'Confirmed' : 'Human gate' : phase === 'translate' ? `${p.progress.translation.completed}/${p.progress.translation.required} complete` : p.publication.state.replaceAll('_', ' ')
      const p1HasDetails = !!p && (p.analysis.planned || p.analysis.membership_locked || p.analysis.complete || p.active_job?.operation === 'analyze')
      const canOpen = state !== 'blocked' && workspace.prepared && (phase !== 'analyse' || p1HasDetails)
      const title = phase === 'analyse' && !p1HasDetails ? 'Start P1 with Run to view analysis details.' : !workspace.prepared && phase === 'prepare' ? subtitle : !canOpen ? 'Complete the preceding phase first.' : `Open ${phase} details`
      return <button key={phase} className={`phase ${state} ${(draftPreparing || p?.active_job?.operation === 'import') && phase === 'prepare' ? 'preparing' : ''} ${phase === 'analyse' && !p1HasDetails ? 'unopened' : ''} ${canOpen ? 'clickable' : ''}`} disabled={!canOpen} title={title} onClick={() => void navigate({ to: '/work/workspaces/$workspaceId/$phase', params: { workspaceId: id, phase } })}><span className="phase-top"><span className="phase-icon">{state === 'done' ? '✓' : state === 'error' ? '×' : ''}</span><span className="phase-name">{phase === 'analyse' ? 'Analyse · P1' : phase[0]!.toUpperCase() + phase.slice(1)}</span></span><span className="phase-sub">{state === 'blocked' ? 'Blocked' : subtitle}</span></button>
    })}</div>
    {!workspace.prepared && modelPanel}
    {!workspace.prepared ? <div className="prepare-card" {...debugTag('PRC')}><div className="prepare-inner"><div className="prepare-icon">P</div><h2>{draftPreparing ? 'Preparing this workspace' : draftFailed ? 'Preparation failed' : 'Prepare this workspace'}</h2><p className="subtitle">Inspect the source and freeze its sections and translation units before analysis. Token counts here are local estimates: one token per four characters. Prepare does not contact a model.</p>{draftP1 && <p className="prepare-profile">Selected P1 profile for Analyse: {draftP1.name} ({draftP1.provider})</p>}<Action workspace={workspace} /></div></div> : !p ? <Empty>Loading source structure…</Empty> : <>
      {p.analysis.complete && !p.approved && <div className="notice"><span>Analysis is complete. Review and confirm terminology before translation.</span><Button onClick={() => void navigate({ to: '/work/workspaces/$workspaceId/$phase', params: { workspaceId: id, phase: 'review' } })}>Open Review</Button></div>}
      <div className="workspace-toolbar" {...debugTag('STF')}><div className="toolbar-left"><span className="toolbar-title">Sections</span><span className="toolbar-sub">{p.sections.length} detected · {p.sections.filter(s => s.processing === 'excluded').length} excluded</span></div><div className="filter-segment">{[['all','All'],['full','Full'],['translate','Translate only'],['excluded','Excluded']].map(([key, label]) => <button key={key} className={filter === key ? 'active' : ''} onClick={() => setSearch({ filter: key as typeof filter })}>{label}</button>)}</div></div>
      <div className="table-card" {...debugTag('SCT')}><table className="sections-table"><colgroup><col className="section-col" /><col className="processing-col" />{[1,2,3,4,5].map(n => <col className="pass-col" key={n} />)}</colgroup><thead><tr><th>Section</th><th>Processing</th>{[1,2,3,4,5].map(n => <th className="pass-cell" key={n}>P{n}</th>)}</tr></thead><tbody>{p.sections.filter(s => filter === 'all' || s.processing === filter).map(s => {
        const mode = modes.find(([value]) => value === s.processing)!
        return <tr className={s.processing === 'excluded' ? 'excluded' : ''} key={s.id} onClick={e => { if (!(e.target as HTMLElement).closest('button')) { setPreviewPage(0); setSearch({ section: s.id }) } }}><td title={displayedTitles.get(s.id) ?? s.fallback_excerpt}><span className="section-number">{String(s.ordinal).padStart(2,'0')}</span><button className={`section-name section-open ${s.title ? '' : 'fallback'}`} onClick={() => { setPreviewPage(0); setSearch({ section: s.id }) }}>{displayedTitles.get(s.id) ?? 'Untitled section'}</button></td><td><span className={`processing-readonly ${s.processing}`} title={mode[2]} aria-label={`Processing: ${mode[2]}`}>{mode[1]}</span></td>{[1,2,3,4,5].map(n => <td className="pass-cell" key={n}><PassMark section={s} number={n} /></td>)}</tr>
      })}</tbody></table></div>
      {modelPanel}<Activity id={id} /><div className="workspace-bottom-actions"><button className="archive-workspace-btn" disabled={command.disabled || p.busy || p.metadata.lifecycle.archived} onClick={() => setArchive(true)}>Archive workspace</button></div>
    </>}
    {!workspace.prepared && !workspace.metadata.lifecycle.archived && <div className="workspace-bottom-actions"><button className="archive-workspace-btn" disabled={command.disabled || !!workspace.active_job} onClick={() => setArchive(true)}>Archive workspace</button></div>}
    {section && <Overlay drawer title={displayedTitles.get(section.id) ?? 'Untitled section'} eyebrow="Section" debugId="PVD" close={() => setSearch({ section: undefined })}><div className="drawer-body"><div className="field-row"><div className="field"><label>Processing · change in Prepare</label><div className="readonly-field">{modes.find(([mode]) => mode === section.processing)?.[2]}</div></div><div className="field"><label htmlFor="section-type">Content type · metadata only</label><select id="section-type" value={section.content_type} disabled={command.disabled || !!p?.busy || p?.metadata.lifecycle.archived} onChange={e => void configure({ content_type: e.target.value }, section.id)}>{[...new Set([...contentTypes, section.content_type])].map(t => <option key={t} value={t}>{t.replaceAll('_',' ')}</option>)}</select></div></div><div className="autosave-note">Review source and change Processing in Prepare. Content type is descriptive metadata.</div><ErrorNote error={command.error} />
      <div className="field"><label>Model overrides · optional</label><div className="drawer-model-grid" {...debugTag('SMG')}>{[1,2,3,4,5].map(n => {
        const key = `${section.id}:${n}`
        const hasIntent = Object.prototype.hasOwnProperty.call(assignmentIntents, key)
        const value = hasIntent ? assignmentIntents[key] : section.profiles[String(n)]
        const displayName = value ?? profiles.data?.resolved_passes[String(n)]?.name ?? 'Unknown profile'
        return <div className="drawer-model-slot" key={n} {...debugTag(sectionModelIds[n - 1]!)}><label className="drawer-model-label" htmlFor={`override-${n}`}><ProfileSwatch index={profiles.data?.profiles.find(profile => profile.name === displayName)?.stable_palette_index} name={displayName} /><span>Pass {n}</span></label><select id={`override-${n}`} value={value ?? ''} disabled={(command.pending && !assignmentProcessing.current) || connection !== 'Live' || !!p?.busy || !!p?.metadata.lifecycle.archived} onChange={e => saveAssignment({ section: section.id, number: String(n), value: e.target.value || null })}><option value="">Inherit — {profiles.data?.resolved_passes[String(n)]?.name ?? '—'}</option>{profiles.data?.profiles.map(profile => <option key={profile.name} value={profile.name} disabled={!profile.enabled}>{profile.name}</option>)}</select></div>
      })}</div></div><ErrorNote error={preview.error} /><div className="preview-text">{preview.data?.blocks.map((b, i) => <p key={`${previewPage}-${b.id}-${i}`}>{b.text}</p>)}</div><div className="preview-pagination"><Button disabled={previewPage === 0} onClick={() => setPreviewPage(previewPage - 1)}>Previous</Button><span>Source preview · page {previewPage + 1}</span><Button disabled={preview.data?.next_page == null} onClick={() => setPreviewPage(preview.data!.next_page!)}>Load more</Button></div></div></Overlay>}
    {archive && (p || !workspace.prepared) && <Overlay title="Archive workspace?" eyebrow="Workspace" debugId="ACM" compact close={() => { if (!command.pending) setArchive(false) }}><div className="modal-body"><p className="subtitle"><strong>{workspace.metadata.title}</strong> will be removed from active workspaces. Its state will be kept in the archive and can be restored later.</p><ErrorNote error={command.error} /></div><div className="modal-foot"><Button onClick={() => setArchive(false)}>Cancel</Button><Button variant="danger" disabled={command.disabled} onClick={async () => { if (await command.send(endpoint(id, 'archive'), lifecycleSchema, { revision: (p?.metadata ?? workspace.metadata).lifecycle.revision })) { setArchive(false); announce('Workspace moved to archive.'); await navigate({ to: '/work' }) } }}>Archive workspace</Button></div></Overlay>}
  </main>
}
