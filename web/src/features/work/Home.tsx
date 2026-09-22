import { useContext, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useInfiniteQuery, type InfiniteData } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { endpoint, useApi, reconcile, queryClient, request, Scope } from '../../api/client'
import { draftSchema, libraryPageSchema, lifecycleSchema, pipelineSchema, workspacesSchema, type LibraryPage, type Workspace } from '../../api/schema'
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
  const command = useCommand()
  const navigate = useNavigate()
  const scope = useContext(Scope)
  const library = useInfiniteQuery({ queryKey: [scope, '/api/library'],
    queryFn: ({ pageParam, signal }) => request(`/api/library?limit=12${pageParam ? `&after=${encodeURIComponent(pageParam)}` : ''}`, libraryPageSchema, { signal }),
    initialPageParam: null as string | null, getNextPageParam: page => page.next_cursor ?? undefined,
    enabled: false, staleTime: Infinity })
  const [archive, setArchive] = useState(false)
  const [refreshingLibrary, setRefreshingLibrary] = useState(false)
  const [refreshError, setRefreshError] = useState<unknown>(null)
  const libraryRegion = useRef<HTMLElement>(null)
  const moreSources = useRef<HTMLDivElement>(null)
  const { data: libraryData, isFetching: loadingLibrary, isError: libraryFailed, hasNextPage, fetchNextPage } = library
  useEffect(() => {
    if (libraryData || loadingLibrary || libraryFailed || refreshingLibrary || !libraryRegion.current) return
    const observer = new IntersectionObserver(entries => { if (entries[0]?.isIntersecting) { observer.disconnect(); void fetchNextPage() } })
    observer.observe(libraryRegion.current)
    return () => observer.disconnect()
  }, [libraryData, loadingLibrary, libraryFailed, refreshingLibrary, fetchNextPage])
  useEffect(() => {
    if (!hasNextPage || loadingLibrary || libraryFailed || refreshingLibrary || !moreSources.current) return
    const observer = new IntersectionObserver(entries => { if (entries[0]?.isIntersecting) { observer.disconnect(); void fetchNextPage() } }, { rootMargin: '0px 0px 250px 0px' })
    observer.observe(moreSources.current)
    return () => observer.disconnect()
  }, [hasNextPage, loadingLibrary, libraryFailed, refreshingLibrary, fetchNextPage, libraryData])
  const active = workspaces.data?.workspaces.filter(w => !w.metadata.lifecycle.archived) ?? []
  const archived = workspaces.data?.workspaces.filter(w => w.metadata.lifecycle.archived) ?? []
  const hidden = new Set(archived.map(w => w.workspace_id))
  const sources = libraryData?.pages.flatMap(page => page.sources).filter(s => !s.workspace_id || !hidden.has(s.workspace_id)) ?? []
  const configured = libraryData?.pages[0]?.configured
  const libraryError = refreshError ?? library.error
  async function openSource(source: (typeof sources)[number]) {
    let id = source.workspace_id
    if (!id) {
      const draft = await command.send('/api/workspaces', draftSchema, { source_id: source.source_id, request_key: crypto.randomUUID() })
      id = draft?.workspace_id ?? null
      if (draft) queryClient.setQueryData<InfiniteData<LibraryPage, string | null>>([scope, '/api/library'], current => current && ({ ...current, pages: current.pages.map(page => ({ ...page, sources: page.sources.map(item => item.source_id === draft.source_id ? { ...item, workspace_id: draft.workspace_id } : item) })) }))
    }
    if (id) await navigate({ to: '/work/workspaces/$workspaceId', params: { workspaceId: id } })
  }
  async function refreshLibrary() {
    if (refreshingLibrary || loadingLibrary) return
    setRefreshingLibrary(true)
    setRefreshError(null)
    announce('Refreshing Library…')
    try {
      const first = await request('/api/library?limit=12', libraryPageSchema)
      queryClient.setQueryData<InfiniteData<LibraryPage, string | null>>([scope, '/api/library'], { pages: [first], pageParams: [null] })
      announce('Library refreshed.')
    } catch (error) { setRefreshError(error) }
    finally { setRefreshingLibrary(false) }
  }
  return <main className="main" {...debugTag('WRK')}><div className="page-title-row"><div {...debugTag('WHT')}><div className="eyebrow">Work</div><h1>Books in progress</h1><div className="subtitle">Open a workspace, continue the pipeline, or start working on a book from the library.</div></div></div>
    <ErrorNote error={workspaces.error ?? command.error} retry={() => void reconcile()} />
    <section className="section" {...debugTag('ACT')}><div className="section-header"><div className="section-title"><h2>Active workspaces</h2><span className="count-badge">{workspaces.data ? active.length : '—'}</span></div><Button variant="ghost" className="compact" onClick={() => setArchive(true)}>Archive →</Button></div>
      <div className="workspace-list" {...debugTag('WLS')}>{active.map(w => <WorkspaceRow key={w.workspace_id} workspace={w} />)}{!active.length && <Empty>{workspaces.isPending ? 'Loading workspaces…' : 'No active workspaces.'}</Empty>}</div></section>
    <section className="section" ref={libraryRegion} {...debugTag('LIB')}><div className="section-header"><div className="section-title"><h2>Library</h2><span className="count-badge" title="Loaded Library sources">{libraryData ? `${sources.length}${hasNextPage ? '+' : ''}` : '—'}</span></div><div className="library-header-actions"><Button variant="ghost" className="compact library-refresh" onClick={() => void refreshLibrary()} disabled={refreshingLibrary || loadingLibrary} aria-busy={refreshingLibrary} aria-label="Refresh Library"><RefreshCw size={14} className={refreshingLibrary ? 'library-refresh-icon busy' : 'library-refresh-icon'} aria-hidden="true" />Refresh</Button></div></div>
      {libraryError && <div className="notice error" role="alert"><span>{libraryError instanceof Error ? libraryError.message : 'Unable to load Library.'} {libraryData && 'Showing the last successful Library contents.'}</span><Button onClick={() => { if (libraryFailed && libraryData && hasNextPage) void fetchNextPage(); else void refreshLibrary() }} disabled={refreshingLibrary || loadingLibrary}>Retry Library refresh</Button></div>}
      <div className="library-grid">{sources.map(source => <button className="book-card" data-source-id={source.source_id} {...debugTag('BKC', source.source_id)} key={source.source_id} onClick={() => void openSource(source)} disabled={command.disabled} aria-label={`Open ${source.title}`}><Cover title={source.title} large /><div className="book-meta"><div className="book-name">{source.title}</div><div className="book-detail"><span>{source.creators.join(', ') || '—'}</span>{source.word_count != null && <><span>·</span><span>{source.word_count.toLocaleString()} words</span></>}{source.workspace_id && <span className="workspace-chip">workspace</span>}</div></div></button>)}</div>
      <div ref={moreSources} className="library-load-more" aria-hidden="true" />
      {!sources.length && !libraryData && !loadingLibrary && !refreshingLibrary && !libraryError && <Empty>Scroll here to discover source books, or select Refresh.</Empty>}
      {!sources.length && libraryData && !libraryError && <Empty>{!configured ? 'Source library is not configured' : 'No source books found'}</Empty>}</section>
    {archive && <Overlay drawer title="Archive" eyebrow="Workspaces" debugId="ARD" close={() => setArchive(false)}><div className="drawer-body"><p className="subtitle">Completed or parked workspaces stay available here.</p><div className="archive-list">{archived.map(w => <div className="archive-item" {...debugTag('ARI', w.workspace_id)} key={w.workspace_id}><div><strong>{w.metadata.title}</strong><ArchivedProgress workspace={w} /></div><Button disabled={command.disabled} onClick={async () => { if (await command.send(endpoint(w.workspace_id, 'restore'), lifecycleSchema, { revision: w.metadata.lifecycle.revision })) { setArchive(false); announce('Workspace restored.') } }}>Restore</Button></div>)}</div>{!archived.length && <Empty>Archive is empty.</Empty>}<ErrorNote error={command.error} /></div></Overlay>}
  </main>
}

function ArchivedProgress({workspace}:{workspace:Workspace}) { const p=useApi(endpoint(workspace.workspace_id,'pipeline'),pipelineSchema,workspace.prepared);const percent=p.data?.progress.percent ?? workspace.progress?.percent;return <div className="subtitle">Archived · {percent == null ? '—' : `${percent}%`} workflow progress</div> }
