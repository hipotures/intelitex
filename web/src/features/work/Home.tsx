import { useState } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { endpoint, useApi, reconcile } from '../../api/client'
import { draftSchema, librarySchema, lifecycleSchema, pipelineSchema, workspacesSchema, type Workspace } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { Button, Cover, Empty, ErrorNote, Overlay } from '../../components/ui/common'
import { announce } from '../../app/notifications'
import { Action } from '../pipeline/Action'
import { debugTag } from '../../debug/regions'
export function WorkspaceRow({ workspace }: { workspace: Workspace }) {
  const p = useApi(endpoint(workspace.workspace_id, 'pipeline'), pipelineSchema, workspace.prepared)
  return <div className="workspace-row" {...debugTag('WRC', workspace.workspace_id)} onClick={e => { if (!(e.target as HTMLElement).closest('button,a')) e.currentTarget.querySelector<HTMLAnchorElement>('a')?.click() }}>
    <Cover title={workspace.metadata.title} />
    <div><Link className="workspace-title" to="/work/workspaces/$workspaceId" params={{ workspaceId: workspace.workspace_id }}>{workspace.metadata.title}</Link><div className="workspace-author">{workspace.metadata.creators.join(', ') || '—'} · {workspace.metadata.language ?? '—'} → PL</div></div>
    <div className="workspace-state"><span className={`status-dot ${workspace.active_job ? 'running' : ''}`} />{workspace.active_job?.state ?? p.data?.stage ?? (workspace.prepared ? 'Loading…' : 'Not prepared')}<small>{p.data ? `${p.data.progress.translation.completed}/${p.data.progress.translation.required} translation units complete` : 'Inspect source locally'}</small></div>
    <div className="row-progress"><div className="progress-wrap"><progress max={100} value={p.data?.progress.percent ?? workspace.progress?.percent ?? undefined} title="Workflow progress — not an ETA" /><div className="progress-value">{p.data?.progress.percent ?? workspace.progress?.percent ?? '—'}{p.data?.progress.percent != null || workspace.progress?.percent != null ? '%' : ''}</div></div></div>
    <div className="row-actions"><Action workspace={workspace} pipeline={p.data} row /></div>
  </div>
}
export function Home() {
  const workspaces = useApi('/api/workspaces', workspacesSchema)
  const library = useApi('/api/library', librarySchema)
  const command = useCommand()
  const navigate = useNavigate()
  const [archive, setArchive] = useState(false)
  const active = workspaces.data?.workspaces.filter(w => !w.metadata.lifecycle.archived) ?? []
  const archived = workspaces.data?.workspaces.filter(w => w.metadata.lifecycle.archived) ?? []
  const hidden = new Set(archived.map(w => w.workspace_id))
  const sources = library.data?.sources.filter(s => !s.workspace_id || !hidden.has(s.workspace_id)) ?? []
  async function openSource(source: (typeof sources)[number]) {
    const id = source.workspace_id ?? (await command.send('/api/workspaces', draftSchema, { source_id: source.source_id, request_key: crypto.randomUUID() }))?.workspace_id
    if (id) await navigate({ to: '/work/workspaces/$workspaceId', params: { workspaceId: id } })
  }
  return <main className="main" {...debugTag('WRK')}><div className="page-title-row" {...debugTag('WHT')}><div><div className="eyebrow">Work</div><h1>Books in progress</h1><div className="subtitle">Open a workspace, continue the pipeline, or start working on a book from the library.</div></div></div>
    <ErrorNote error={workspaces.error ?? command.error} retry={() => void reconcile()} />
    <section className="section" {...debugTag('ACT')}><div className="section-header"><div className="section-title"><h2>Active workspaces</h2><span className="count-badge">{workspaces.data ? active.length : '—'}</span></div><Button variant="ghost" className="compact" onClick={() => setArchive(true)}>Archive →</Button></div>
      <div className="workspace-list" {...debugTag('WLS')}>{active.map(w => <WorkspaceRow key={w.workspace_id} workspace={w} />)}{!active.length && <Empty>{workspaces.isPending ? 'Loading workspaces…' : 'No active workspaces.'}</Empty>}</div></section>
    <section className="section" {...debugTag('LIB')}><div className="section-header"><div className="section-title"><h2>Library</h2><span className="count-badge">{library.data ? sources.length : '—'}</span></div><div className="subtitle">Source directory: {library.data?.configured ? 'Configured' : 'Not configured'}</div></div>
      <ErrorNote error={library.error} retry={() => void library.refetch()} />
      <div className="library-grid">{sources.map(source => <button className="book-card" data-source-id={source.source_id} {...debugTag('BKC', source.source_id)} key={source.source_id} onClick={() => void openSource(source)} disabled={command.disabled} aria-label={`Open ${source.title}`}><Cover title={source.title} large /><div className="book-meta"><div className="book-name">{source.title}</div><div className="book-detail"><span>{source.creators.join(', ') || '—'}</span><span>·</span><span>{source.word_count?.toLocaleString() ?? '—'} words</span>{source.workspace_id && <span className="workspace-chip">workspace</span>}</div></div></button>)}</div>
      {!sources.length && !library.error && <Empty>{library.isPending ? 'Loading library…' : !library.data?.configured ? 'Source library is not configured' : 'No source books found'}</Empty>}</section>
    {archive && <Overlay drawer title="Archive" eyebrow="Workspaces" debugId="ARD" close={() => setArchive(false)}><div className="drawer-body"><p className="subtitle">Completed or parked workspaces stay available here.</p><div className="archive-list">{archived.map(w => <div className="archive-item" {...debugTag('ARI', w.workspace_id)} key={w.workspace_id}><div><strong>{w.metadata.title}</strong><ArchivedProgress workspace={w} /></div><Button disabled={command.disabled} onClick={async () => { if (await command.send(endpoint(w.workspace_id, 'restore'), lifecycleSchema, { revision: w.metadata.lifecycle.revision })) { setArchive(false); announce('Workspace restored.') } }}>Restore</Button></div>)}</div>{!archived.length && <Empty>Archive is empty.</Empty>}<ErrorNote error={command.error} /></div></Overlay>}
  </main>
}

function ArchivedProgress({workspace}:{workspace:Workspace}) { const p=useApi(endpoint(workspace.workspace_id,'pipeline'),pipelineSchema,workspace.prepared);const percent=p.data?.progress.percent ?? workspace.progress?.percent;return <div className="subtitle">Archived · {percent == null ? '—' : `${percent}%`} workflow progress</div> }
