import { useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { z } from 'zod'
import { ApiError, endpoint, queryClient, request, Scope, useApi } from '../../api/client'
import { chapterSchema, contextSchema, librarySchema, markerMutationSchema, markersSchema, readerProgressSchema, readerSchema, workspacesSchema } from '../../api/schema'
import { useConnection } from '../../realtime/coordinator'
import { Button, Empty, ErrorNote, Overlay } from '../../components/ui/common'
import { preference, savePreference } from '../../app/preferences'
import { debugTag } from '../../debug/regions'
import { blockRange, codePointToUtf16, inlineRuns, selectedRange, snapWordRange } from './ranges'
function paintRanges(layer: HTMLDivElement | null, ranges: Range[]) {
  if (!layer) return
  const fragment = document.createDocumentFragment()
  for (const range of ranges) for (const rect of typeof range.getClientRects === 'function' ? range.getClientRects() : []) {
    if (rect.width <= 0 || rect.height <= 0) continue
    const highlight = document.createElement('span')
    highlight.className = 'reader-range-rect preview'
    highlight.style.left = `${rect.left}px`
    highlight.style.top = `${rect.top}px`
    highlight.style.width = `${rect.width}px`
    highlight.style.height = `${rect.height}px`
    fragment.append(highlight)
  }
  layer.replaceChildren(fragment)
}
export function ReaderPage() {
  const { workspaceId, sourceId } = useParams({ strict: false })
  const scope = useContext(Scope)
  const navigate = useNavigate()
  const all = useApi('/api/workspaces', workspacesSchema)
  const library = useQuery({ queryKey: [scope, '/api/library', 'reader'], queryFn: ({ signal }) => request('/api/library', librarySchema, { signal }) })
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [sidebarTab, setSidebarTab] = useState<'chapters' | 'library'>('chapters')
  const chapterChooser = useRef<((chapterId: string) => void) | null>(null)
  const sidebarSearch = useSearch({ strict: false })
  useEffect(() => {
    if (!libraryOpen) return
    const dismiss = (event: PointerEvent) => {
      if (!(event.target as Element).closest('.reader-mobile-books, .reader-books')) setLibraryOpen(false)
    }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setLibraryOpen(false) }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', escape) }
  }, [libraryOpen])
  const books = useMemo(() => all.data?.workspaces.filter(w => w.prepared && !w.metadata.lifecycle.archived) ?? [], [all.data])
  const sources = useMemo(() => library.data?.sources.filter(source => source.source_id.toLowerCase().endsWith('.epub')) ?? [], [library.data])
  const chosenSource = sourceId ? sources.find(source => source.source_id === sourceId) : undefined
  const last = /^\/reader\/([A-Za-z0-9_.-]+)(?:\?|$)/.exec(preference(scope, 'reader.route'))?.[1]
  const working = /^\/work\/workspaces\/([A-Za-z0-9_.-]+)(?:\/|\?|$)/.exec(preference(scope, 'work.route'))?.[1]
  const workingBook = books.find(w => w.workspace_id === working)
  const chosen = sourceId ? undefined : workspaceId ? books.find(w => w.workspace_id === workspaceId) : books.find(w => w.workspace_id === last) ?? books.find(w => w.workspace_id === working) ?? books[0]
  const archivedBook = workspaceId && all.data?.workspaces.find(w => w.workspace_id === workspaceId && w.metadata.lifecycle.archived)
  const id = chosen?.workspace_id ?? ''
  const selectedKey = chosen ? id : chosenSource ? `source:${sourceId}` : ''
  const activeTab = selectedKey ? sidebarTab : 'library'
  const sidebarReaderPath = chosen ? endpoint(chosen.workspace_id, 'reader') : chosenSource ? `/api/library/sources/${encodeURIComponent(chosenSource.source_id)}/reader` : ''
  const sidebarMetadata = useApi(sidebarReaderPath, readerSchema, !!sidebarReaderPath)
  const sidebarProgress = useApi(endpoint(chosen?.workspace_id ?? '', 'reader/progress'), readerProgressSchema, !!chosen)
  const sidebarChapters = sidebarMetadata.data?.chapters ?? []
  const savedChapter = preference(scope, `${selectedKey}.reader.chapter`)
  const activeChapter = sidebarChapters.find(c => c.id === sidebarSearch.chapter)?.id ?? sidebarChapters.find(c => c.id === savedChapter)?.id ?? sidebarChapters[0]?.id
  const availableChapters = new Set(chosenSource ? sidebarChapters.map(c => c.id) : sidebarProgress.data?.chapters.map(c => c.id) ?? [])
  useEffect(() => { setSidebarTab(selectedKey ? 'chapters' : 'library'); setLibraryOpen(false) }, [selectedKey])
  useEffect(() => {
    if (workspaceId || sourceId || !all.data || !library.data) return
    let cancelled = false
    const select = async () => {
      let candidate = books.find(w => w.workspace_id === last) ?? books.find(w => w.workspace_id === working)
      if (!candidate) for (const book of books) {
        try {
          const progress = await request(endpoint(book.workspace_id, 'reader/progress'), readerProgressSchema)
          if (progress.total_words > 0) { candidate = book; break }
        } catch { /* Keep the first available workspace if its Reader is unavailable. */ }
      }
      if (cancelled) return
      if (candidate ?? books[0]) void navigate({ to: '/reader/$workspaceId', params: { workspaceId: (candidate ?? books[0]!).workspace_id }, replace: true })
      else if (sources[0]) void navigate({ to: '/reader/source/$sourceId', params: { sourceId: sources[0].source_id }, replace: true })
    }
    void select()
    return () => { cancelled = true }
  }, [all.data, library.data, books, sources, workspaceId, sourceId, last, working, navigate])
  const openChapters = () => { setSidebarTab('chapters'); if (window.matchMedia?.('(max-width:720px)').matches) setLibraryOpen(true) }
  const selectBook = () => { setSidebarTab('chapters'); setLibraryOpen(false) }
  const sidebar = <aside id="reader-sidebar" className={`reader-books ${libraryOpen ? 'open' : ''}`} aria-label="Reader navigation" {...debugTag('RBL')}>
    <div className="reader-sidebar-switch" role="group" aria-label="Reader navigation view"><button type="button" aria-pressed={activeTab === 'chapters'} disabled={!selectedKey} onClick={() => setSidebarTab('chapters')}>Chapters</button><button type="button" aria-pressed={activeTab === 'library'} onClick={() => setSidebarTab('library')}>Library</button></div>
    {activeTab === 'library' ? <nav aria-label="Reading books">{workingBook && <Link to="/reader/$workspaceId" params={{ workspaceId: workingBook.workspace_id }} onClick={selectBook} className="reader-book reader-current-workspace"><strong>Current workspace</strong><span>{workingBook.metadata.title}</span><span className="reader-book-identity">{workingBook.metadata.label ? `${workingBook.metadata.label} · ` : ''}Workspace {workingBook.workspace_id}</span></Link>}{books.map(w => <Link key={w.workspace_id} to="/reader/$workspaceId" params={{ workspaceId: w.workspace_id }} onClick={selectBook} className={`reader-book ${id === w.workspace_id ? 'active' : ''}`} {...debugTag('RBB', w.workspace_id)}><strong>{w.metadata.title}</strong><span className="reader-book-identity">{w.metadata.label ? `${w.metadata.label} · ` : ''}Workspace {w.workspace_id}</span><span>{w.metadata.creators.join(', ') || '—'}</span></Link>)}{sources.map(source => <Link key={source.source_id} to="/reader/source/$sourceId" params={{sourceId:source.source_id}} onClick={selectBook} className={`reader-book ${sourceId === source.source_id ? 'active' : ''}`} {...debugTag('RBB', source.source_id)}><strong>{source.title}</strong><span className="reader-book-identity">Original EPUB · no workspace markers</span></Link>)}</nav> : <nav className="reader-chapter-list" aria-label="Table of contents" {...debugTag('RTC')}><div className="reader-chapter-book">{chosen?.metadata.title ?? chosenSource?.title}</div>{sidebarChapters.map(chapter => <button type="button" key={chapter.id} className={chapter.id === activeChapter ? 'active' : ''} aria-current={chapter.id === activeChapter ? 'page' : undefined} onClick={() => { chapterChooser.current?.(chapter.id); setLibraryOpen(false) }}>{chapter.title}{!chosenSource && sidebarProgress.data && !availableChapters.has(chapter.id) && <span>Awaiting translation</span>}</button>)}{!sidebarChapters.length && <p className="reader-sidebar-empty">{sidebarMetadata.isPending ? 'Loading chapters…' : 'No chapters available.'}</p>}</nav>}
  </aside>
  return <main className="main reader-main" {...debugTag('RDR')}><div className="reader-mobile-books"><Button onClick={() => setLibraryOpen(open => !open)} aria-expanded={libraryOpen} aria-controls="reader-sidebar" title={chosen ? `${chosen.metadata.title} · Workspace ${chosen.workspace_id}` : chosenSource ? `${chosenSource.title} · Original EPUB` : 'Select book'}>{activeTab === 'chapters' ? 'Chapters' : 'Library'} · {chosen ? `${chosen.metadata.title} · ${chosen.metadata.label || chosen.workspace_id}` : chosenSource ? `${chosenSource.title} · EPUB` : 'Select book'}</Button></div><ErrorNote error={all.error ?? library.error} /><div className="reader-shell" {...debugTag('RSH')}>{sidebar}<div className="reader-content" {...debugTag('RBC', id || sourceId)}>{chosen ? <ReadingBook key={id} id={id} libraryOpen={libraryOpen} onOpenChapters={openChapters} onRegisterChoose={choose => { chapterChooser.current = choose }} /> : chosenSource ? <ReadingBook key={`source:${sourceId}`} id={`source:${sourceId}`} sourceId={sourceId} libraryOpen={libraryOpen} onOpenChapters={openChapters} onRegisterChoose={choose => { chapterChooser.current = choose }} /> : <Empty>{all.isPending || library.isPending ? 'Loading books…' : archivedBook ? <>This workspace is archived and hidden from Reader. Restore it from <Link to="/work">Work → Archive</Link>.</> : workspaceId ? 'This workspace is not prepared for reading.' : sourceId ? 'This EPUB is unavailable in Library.' : 'No books available for reading.'}</Empty>}</div></div></main>
}
export function ReadingBook({ id, libraryOpen, archived = false, sourceId, onOpenChapters, onRegisterChoose }: { id: string; libraryOpen: boolean; archived?: boolean; sourceId?: string; onOpenChapters?: () => void; onRegisterChoose?: (choose: ((id: string) => void) | null) => void }) {
  const readerPath = sourceId ? `/api/library/sources/${encodeURIComponent(sourceId)}/reader` : endpoint(id,'reader')
  const metadata = useApi(readerPath, readerSchema)
  const progress = useApi(endpoint(id,'reader/progress'), readerProgressSchema, !sourceId)
  const progressRef = useRef(progress.data)
  progressRef.current = progress.data
  const markers = useApi(endpoint(id,'reader/markers'), markersSchema, !sourceId)
  const scope = useContext(Scope)
  const search = useSearch({ strict: false })
  const navigate = useNavigate()
  const saved = preference(scope, `${id}.reader.chapter`)
  const chapters = metadata.data?.chapters ?? []
  const chapterId = chapters.find(c => c.id === search.chapter)?.id ?? chapters.find(c => c.id === saved)?.id ?? chapters[0]?.id
  const chapterPath = `${readerPath}/chapters/${encodeURIComponent(chapterId ?? '')}`
  const chapter = useApi(chapterPath, chapterSchema, !!chapterId)
  const connection = useConnection()
  const [markerPending, setMarkerPending] = useState(false)
  const [markerError, setMarkerError] = useState<unknown>(null)
  const [recentMarker, setRecentMarker] = useState<string | null>(null)
  const [hiddenMarkers, setHiddenMarkers] = useState<Set<string>>(() => new Set())
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [progressOpen, setProgressOpen] = useState(false)
  const [fontSize, setFontSize] = useState(() => Number(preference(scope, 'reader.fontSize', '20')))
  const [fontFamily, setFontFamily] = useState(() => preference(scope, 'reader.fontFamily', 'serif'))
  const [lineHeight, setLineHeight] = useState(() => Number(preference(scope, 'reader.lineHeight', '1.7')))
  const [contentWidth, setContentWidth] = useState(() => Number(preference(scope, 'reader.contentWidth', '42')))
  const [headerAutoHide, setHeaderAutoHide] = useState(() => Number(preference(scope, 'reader.headerAutoHide', '0')))
  const [markerGesture, setMarkerGesture] = useState(() => preference(scope, 'reader.markerGesture', 'drag'))
  const [contextGesture, setContextGesture] = useState(() => preference(scope, 'reader.contextGesture', 'long'))
  const [controlsHidden, setControlsHidden] = useState(false)
  const [newText, setNewText] = useState(false)
  const [readWords, setReadWords] = useState(0)
  const [contextError, setContextError] = useState('')
  const [activeMarker, setActiveMarker] = useState<string | null>(null)
  const [selection, setSelection] = useState<ReturnType<typeof selectedRange>>(null)
  const [context, setContext] = useState<z.infer<typeof contextSchema> | null>(null)
  const restored = useRef('')
  const savePosition = useRef<(() => void) | null>(null)
  const article = useRef<HTMLElement>(null)
  const scrollArea = useRef<HTMLDivElement>(null)
  const previewLayer = useRef<HTMLDivElement>(null)
  const markerPopover = useRef<HTMLElement>(null)
  const markerBusy = useRef(false)
  const previewFrame = useRef<number | null>(null)
  const previewTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const progressTapTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastAvailable = useRef<{ chapter: string; signature: string } | null>(null)
  const gesture = useRef<{ id: number; x: number; y: number; scrollTop: number; scrolling?: boolean; timer?: ReturnType<typeof setTimeout>; fired?: boolean } | null>(null)
  const [markerAnchor, setMarkerAnchor] = useState<string | null>(null)
  const controlTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const availableChapters = new Set(sourceId ? chapters.map(c => c.id) : progress.data?.chapters.map(c => c.id) ?? [])
  const visibleProgress = progress.data?.chapters.find(c => c.id === chapterId)
  const fingerprint = metadata.data?.book_fingerprint
  useEffect(() => {
    if (sourceId || !progress.data || !chapterId) return
    const signature = JSON.stringify(visibleProgress?.blocks.map(block => [block.id, block.words]) ?? [])
    if (lastAvailable.current?.chapter === chapterId && lastAvailable.current.signature !== signature && visibleProgress?.blocks.length) {
      if (!chapter.data?.blocks.length) void chapter.refetch()
      else setNewText(true)
    }
    lastAvailable.current = { chapter: chapterId, signature }
  // Chapter refetch is deliberately tied to availability, never to a timer or ordinary query refresh.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chapterId, progress.data, sourceId])
  useEffect(() => {
    for (const [key, value] of Object.entries({fontSize, fontFamily, lineHeight, contentWidth, headerAutoHide, markerGesture, contextGesture})) savePreference(scope, `reader.${key}`, String(value))
  }, [scope, fontSize, fontFamily, lineHeight, contentWidth, headerAutoHide, markerGesture, contextGesture])
  useEffect(() => {
    if (controlTimer.current) clearTimeout(controlTimer.current)
    if (!headerAutoHide || settingsOpen || libraryOpen) { setControlsHidden(false); return }
    if (controlsHidden || progressOpen) return
    controlTimer.current = setTimeout(() => setControlsHidden(true), headerAutoHide * 1000)
    return () => { if (controlTimer.current) clearTimeout(controlTimer.current) }
  }, [headerAutoHide, settingsOpen, progressOpen, libraryOpen, chapterId, controlsHidden])
  useLayoutEffect(() => {
    document.documentElement.classList.toggle('reader-chrome-hidden', controlsHidden)
    return () => document.documentElement.classList.remove('reader-chrome-hidden')
  }, [controlsHidden])
  useEffect(() => () => {
    if (gesture.current?.timer) clearTimeout(gesture.current.timer)
    if (previewFrame.current !== null) cancelAnimationFrame(previewFrame.current)
    if (previewTimer.current) clearTimeout(previewTimer.current)
    if (progressTapTimer.current) clearTimeout(progressTapTimer.current)
  }, [])
  useEffect(() => {
    if (!recentMarker) return
    const timer = setTimeout(() => setRecentMarker(null), 8000)
    return () => clearTimeout(timer)
  }, [recentMarker])
  useEffect(() => {
    if (!settingsOpen && !progressOpen) return
    const dismiss = (event: PointerEvent) => {
      const target = event.target
      if (!(target instanceof Element)) return
      if (settingsOpen && !target.closest('.reader-settings, [aria-label="Reader settings"]')) setSettingsOpen(false)
      if (progressOpen && !target.closest('.reader-progress-popup, .reader-progress-line')) setProgressOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setSettingsOpen(false); setProgressOpen(false) }
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', escape) }
  }, [settingsOpen, progressOpen])
  const chromeActivity = useCallback(() => {
    if (!headerAutoHide) return
    if (controlTimer.current) clearTimeout(controlTimer.current)
    if (controlsHidden) { setControlsHidden(false); return }
    if (!settingsOpen && !progressOpen && !libraryOpen) controlTimer.current = setTimeout(() => setControlsHidden(true), headerAutoHide * 1000)
  }, [controlsHidden, headerAutoHide, settingsOpen, progressOpen, libraryOpen])
  useEffect(() => {
    const onChromePointer = (event: PointerEvent) => {
      if (event.target instanceof Element && event.target.closest('.topbar, .reader-controls, .reader-books, .reader-mobile-books, .reader-settings')) chromeActivity()
    }
    document.addEventListener('pointerdown', onChromePointer, { passive: true })
    return () => document.removeEventListener('pointerdown', onChromePointer)
  }, [chromeActivity])
  useEffect(() => {
    if (!progressOpen) return
    const timer = setTimeout(() => setProgressOpen(false), 3600)
    return () => clearTimeout(timer)
  }, [progressOpen])
  const tapProgress = () => {
    if (progressTapTimer.current) {
      clearTimeout(progressTapTimer.current)
      progressTapTimer.current = null
      setProgressOpen(false)
      chromeActivity()
      return
    }
    progressTapTimer.current = setTimeout(() => { progressTapTimer.current = null; setProgressOpen(open => !open) }, 300)
  }
  useLayoutEffect(() => {
    if (!metadata.data || !chapter.data || chapter.data.id !== chapterId) return
    const restoreKey = `${id}.${metadata.data.book_fingerprint}.${chapterId}`
    if (restored.current === restoreKey) return
    restored.current = restoreKey
    const key = `${id}.${metadata.data.book_fingerprint}.${chapterId}.anchor`
    const stored = preference(scope, key)
    let anchor: { block_id: string; offset: number }
    try { anchor = JSON.parse(stored) as typeof anchor } catch { anchor = { block_id: stored, offset: 0 } }
    if (!anchor || typeof anchor.block_id !== 'string' || !anchor.block_id || !Number.isInteger(anchor.offset) || anchor.offset < 0) { scrollArea.current?.scrollTo({ top: 0 }); return }
    const node = Array.from(article.current?.querySelectorAll<HTMLElement>('[data-block-id]') ?? []).find(n => n.dataset.blockId === anchor.block_id)
    if (node) {
      let remaining = codePointToUtf16(node.textContent ?? '', anchor.offset)
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT)
      let text: Node | null
      while ((text = walker.nextNode())) {
        if (remaining <= (text.textContent?.length ?? 0)) {
          const range = document.createRange(); range.setStart(text, remaining); range.collapse(true)
          const area = scrollArea.current
          if (area) {
            const visibleTop = Math.max(area.getBoundingClientRect().top, area.querySelector('.reader-progress-line')?.getBoundingClientRect().bottom ?? 0)
            area.scrollBy(0, range.getBoundingClientRect().top - visibleTop - 8)
          }
          break
        }
        remaining -= text.textContent?.length ?? 0
      }
    }
  }, [chapter.data, chapterId, id, metadata.data, scope])
  useLayoutEffect(() => {
    const save = () => {
      if (!fingerprint || !chapterId || !article.current || article.current.dataset.chapterId !== chapterId) return
      if (!scrollArea.current || scrollArea.current.scrollTop <= 1) {
        savePreference(scope, `${id}.${fingerprint}.${chapterId}.anchor`, JSON.stringify({ block_id: '', offset: 0 }))
        savePreference(scope, `${id}.reader.chapter`, chapterId)
        setReadWords(0)
        return
      }
      const nodes = Array.from(article.current.querySelectorAll<HTMLElement>('[data-block-id]'))
      const top = Math.max(scrollArea.current.getBoundingClientRect().top, scrollArea.current.querySelector('.reader-progress-line')?.getBoundingClientRect().bottom ?? 0)
      const node = nodes.find(n => n.getBoundingClientRect().bottom > top + 8) ?? nodes.at(-1)
      if (node) {
        const rect = node.getBoundingClientRect()
        const x = rect.left + 2, y = Math.max(top + 10, rect.top + 3)
        const caret = document.caretPositionFromPoint?.(x, y)
        const fallback = caret ? null : document.caretRangeFromPoint?.(x, y)
        const caretNode = caret?.offsetNode ?? fallback?.startContainer
        const caretOffset = caret?.offset ?? fallback?.startOffset
        let offset = rect.bottom <= top + 8 ? Array.from(node.textContent ?? '').length : 0
        if (caretNode && caretOffset !== undefined && node.contains(caretNode)) {
          const prefix = document.createRange(); prefix.selectNodeContents(node); prefix.setEnd(caretNode, caretOffset)
          offset = Array.from(prefix.toString()).length
        }
        savePreference(scope, `${id}.${fingerprint}.${chapterId}.anchor`, JSON.stringify({ block_id: node.dataset.blockId, offset }))
        savePreference(scope, `${id}.reader.chapter`, chapterId)
        const currentProgress = progressRef.current
        const snapshot = currentProgress?.chapters.find(c => c.id === chapterId)?.blocks.find(b => b.id === node.dataset.blockId)
        if (snapshot) {
          const prefix = Array.from(node.textContent ?? '').slice(0, offset).join('')
          setReadWords(Math.min(currentProgress?.total_words ?? 0, snapshot.start + (prefix.match(/[\p{L}\p{N}\p{M}_]+(?:[’'-][\p{L}\p{N}\p{M}_]+)*/gu)?.length ?? 0)))
        }
      }
    }
    const area = scrollArea.current
    let timer: ReturnType<typeof setTimeout>
    savePosition.current = save
    const onScroll = () => {
      if (gesture.current) {
        gesture.current.scrolling = true
        if (gesture.current.timer) clearTimeout(gesture.current.timer)
      }
      paintRanges(previewLayer.current, [])
      clearTimeout(timer)
      timer = setTimeout(save, 400)
    }
    area?.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('pagehide', save)
    return () => { area?.removeEventListener('scroll', onScroll); window.removeEventListener('pagehide', save); clearTimeout(timer); save(); if (savePosition.current === save) savePosition.current = null }
  // Keep the handler stable across query updates so a pending restore is not overwritten by cleanup.
  }, [id, chapterId, fingerprint, scope])
  const choose = (value: string) => { if (value === chapterId) return; savePosition.current?.(); setSelection(null); setContext(null); setActiveMarker(null); setMarkerAnchor(null); setReadWords(0); setNewText(false); scrollArea.current?.scrollTo({ top: 0 }); savePreference(scope, `${id}.reader.chapter`, value); if (metadata.data) savePreference(scope, `${id}.${metadata.data.book_fingerprint}.${value}.anchor`, JSON.stringify({block_id:'',offset:0})); void queryClient.invalidateQueries({queryKey: [scope, `${readerPath}/chapters/${encodeURIComponent(value)}`], exact: true}); void navigate({ to: '.', search: old => ({ ...old, chapter: value }), replace: true }) }
  useEffect(() => { onRegisterChoose?.(choose); return () => onRegisterChoose?.(null) })
  const index = chapters.findIndex(c => c.id === chapterId)
  const refreshChapter = () => { if (window.getSelection()?.toString()) return; setNewText(false); void chapter.refetch() }
  const markerPath = endpoint(id, 'reader/markers')
  const updateMarkers = (revision: string, change: (items: z.infer<typeof markersSchema>['markers']) => z.infer<typeof markersSchema>['markers']) => {
    queryClient.setQueryData<z.infer<typeof markersSchema>>([scope, markerPath], current => current ? { ...current, _revision: revision, markers: change(current.markers) } : current)
  }
  const mark = async (value: NonNullable<ReturnType<typeof selectedRange>>) => {
    if (sourceId || archived || !markers.data || !chapterId || markerBusy.current || connection !== 'Live') return
    markerBusy.current = true; setMarkerPending(true); setMarkerError(null)
    setSelection(null)
    window.getSelection()?.removeAllRanges()
    try {
      const result = await request(markerPath, markerMutationSchema, { method: 'POST', body: { ...value, chapter_id: chapterId, revision: markers.data._revision } })
      if (!result.marker) throw new Error('Marker response is incomplete.')
      await queryClient.cancelQueries({ queryKey: [scope, markerPath], exact: true })
      updateMarkers(result.revision, items => items.some(item => item.id === result.marker!.id) ? items : [...items, result.marker!])
      setRecentMarker(result.marker.id)
      setActiveMarker(result.marker.block_id)
      setMarkerAnchor(result.marker.id)
    } catch (error) {
      setMarkerError(error)
      setSelection(value)
      if (error instanceof ApiError && error.status === 409) void markers.refetch()
    } finally {
      markerBusy.current = false; setMarkerPending(false)
    }
  }
  const deleteMarker = async (markerId: string) => {
    if (sourceId || archived || !markers.data || markerBusy.current || connection !== 'Live') return
    markerBusy.current = true; setMarkerPending(true); setMarkerError(null)
    window.getSelection()?.removeAllRanges()
    setHiddenMarkers(previous => new Set(previous).add(markerId))
    try {
      const result = await request(endpoint(id, `reader/markers/${encodeURIComponent(markerId)}`), markerMutationSchema,
        { method: 'DELETE', body: { revision: markers.data._revision } })
      if (result.deleted !== markerId) throw new Error('Marker deletion response is incomplete.')
      await queryClient.cancelQueries({ queryKey: [scope, markerPath], exact: true })
      updateMarkers(result.revision, items => items.filter(item => item.id !== markerId))
      setActiveMarker(null)
      setMarkerAnchor(null)
    } catch (error) {
      setMarkerError(error)
      if (error instanceof ApiError && error.status === 409) void markers.refetch()
    } finally {
      setHiddenMarkers(previous => { const next = new Set(previous); next.delete(markerId); return next })
      markerBusy.current = false; setMarkerPending(false)
    }
  }
  const openContext = async (blockId: string, position: number) => {
    if (sourceId || !chapterId) return
    setContextError('')
    try { setContext(await request(endpoint(id,'reader/context'), contextSchema, { method: 'POST', body: { chapter_id: chapterId, block_id: blockId, position } })) }
    catch (error) { setContextError(error instanceof Error ? error.message : 'Context is unavailable.') }
  }
  const wordAtPoint = (x: number, y: number) => {
    const position = document.caretPositionFromPoint?.(x, y)
    const fallback = position ? null : document.caretRangeFromPoint?.(x, y)
    const node = position?.offsetNode ?? fallback?.startContainer
    const offset = position?.offset ?? fallback?.startOffset
    const owner = node?.nodeType === Node.ELEMENT_NODE ? node as Element : node?.parentElement
    const block = owner?.closest<HTMLElement>('[data-block-id]')
    if (!node || offset === undefined || !block || !article.current?.contains(block)) return null
    const prefix = document.createRange(); prefix.selectNodeContents(block); prefix.setEnd(node, offset)
    return { block, offset: Array.from(prefix.toString()).length }
  }
  const gestureRangeAt = (x: number, y: number, endX = x, endY = y) => {
    const first = wordAtPoint(x, y), last = wordAtPoint(endX, endY)
    if (!first || !last || first.block !== last.block) return null
    const fullText = first.block.textContent ?? ''
    const snapped = snapWordRange(fullText, first.offset, last.offset)
    if (!snapped) return null
    const value = { block_id: first.block.dataset.blockId!, ...snapped, text: Array.from(fullText).slice(snapped.start, snapped.end).join('') }
    return { value, range: blockRange(first.block, snapped.start, snapped.end), position: Math.min(snapped.end - 1, Math.max(snapped.start, first.offset)) }
  }
  const previewGesture = (x: number, y: number, endX = x, endY = y) => {
    const target = gestureRangeAt(x, y, endX, endY)
    paintRanges(previewLayer.current, target?.range ? [target.range] : [])
  }
  const gestureAt = (kind: 'tap' | 'long' | 'drag', x: number, y: number, endX = x, endY = y) => {
    if (window.getSelection()?.toString()) return
    const target = gestureRangeAt(x, y, endX, endY)
    if (!target) { paintRanges(previewLayer.current, []); return }
    paintRanges(previewLayer.current, target.range ? [target.range] : [])
    if (previewTimer.current) clearTimeout(previewTimer.current)
    previewTimer.current = setTimeout(() => paintRanges(previewLayer.current, []), 900)
    if (!sourceId && !archived && markerGesture === kind) void mark(target.value)
    else if (!sourceId && contextGesture === kind) void openContext(target.value.block_id, target.position)
    else paintRanges(previewLayer.current, [])
  }
  const setGesture = (which: 'marker' | 'context', value: string) => {
    if (which === 'marker') { setMarkerGesture(value); if (value !== 'off' && contextGesture === value) setContextGesture(markerGesture) }
    else { setContextGesture(value); if (value !== 'off' && markerGesture === value) setMarkerGesture(contextGesture) }
  }
  const chapterMarkers = useMemo(() => markers.data?.markers.filter(m => m.chapter_id === chapterId && !hiddenMarkers.has(m.id)) ?? [], [markers.data, chapterId, hiddenMarkers])
  useLayoutEffect(() => {
    if (!activeMarker || !markerPopover.current) return
    const popover = markerPopover.current
    const position = () => {
      const block = Array.from(article.current?.querySelectorAll<HTMLElement>('[data-block-id]') ?? []).find(node => node.dataset.blockId === activeMarker)
      const marker = chapterMarkers.find(item => item.id === markerAnchor && item.block_id === activeMarker) ?? chapterMarkers.find(item => item.block_id === activeMarker)
      const wrapper = block?.parentElement
      if (!block || !marker || !wrapper) return
      const rects = Array.from(blockRange(block, marker.start, marker.end)?.getClientRects() ?? [])
      const tag = rects.at(-1) ?? block.getBoundingClientRect()
      const box = wrapper.getBoundingClientRect()
      const computed = getComputedStyle(block)
      const font = Number.parseFloat(computed.fontSize) || fontSize
      const lineValue = Number.parseFloat(computed.lineHeight)
      const line = Number.isFinite(lineValue) ? computed.lineHeight.endsWith('px') ? lineValue : lineValue * font : font * lineHeight
      popover.style.top = `${Math.max(0, tag.bottom - box.top + line * 2)}px`
      popover.style.left = `${Math.max(0, Math.min(tag.left - box.left, box.width - Math.min(520, box.width)))}px`
      const area = scrollArea.current
      if (area) {
        const areaBottom = area.getBoundingClientRect().bottom
        const controlsBottom = document.querySelector('.reader-progress-line')?.getBoundingClientRect().bottom ?? 0
        const canScroll = Math.max(0, tag.top - controlsBottom - 8)
        const desiredScroll = Math.max(0, popover.getBoundingClientRect().top + Math.min(popover.scrollHeight, 160) - areaBottom + 12)
        if (desiredScroll > 0 && canScroll > 0) area.scrollBy(0, Math.min(desiredScroll, canScroll))
        popover.style.maxHeight = `${Math.max(80, Math.min(520, areaBottom - popover.getBoundingClientRect().top - 12))}px`
      }
    }
    position()
    window.addEventListener('resize', position)
    return () => window.removeEventListener('resize', position)
  }, [activeMarker, markerAnchor, chapterMarkers, chapter.data, fontSize, fontFamily, lineHeight, contentWidth])
  const percent = progress.data?.total_words ? Math.min(100, Math.round(readWords / progress.data.total_words * 100)) : 0
  return <div className="reader-reading-area" ref={scrollArea}>
    <div className="reader-range-layer" ref={previewLayer} aria-hidden="true" />
    <div className={`reader-controls ${controlsHidden ? 'reader-controls-hidden' : ''}`} {...debugTag('RCO')}>
      <Button variant="ghost" aria-label="Table of contents" disabled={!onOpenChapters} onClick={() => { onOpenChapters?.(); setSettingsOpen(false) }}>☰</Button>
      <Button variant="ghost" aria-label="Previous chapter" disabled={index <= 0} onClick={() => choose(chapters[index - 1]!.id)}>‹</Button>
      <div className="reader-current"><strong title={sourceId ? 'Original EPUB' : `Workspace ${id}`}>{metadata.data?.title ?? 'Reader'} · {sourceId ? 'Original EPUB' : id}</strong><span>{chapter.data?.title ?? chapters[index]?.title ?? 'Choose a chapter'}</span></div>
      <Button variant="ghost" aria-label="Next chapter" disabled={index < 0 || index >= chapters.length - 1} onClick={() => choose(chapters[index + 1]!.id)}>›</Button>
      <Button variant="ghost" aria-label="Reader settings" aria-expanded={settingsOpen} onClick={() => setSettingsOpen(!settingsOpen)}>Aa</Button>
      <Button variant="ghost" aria-label="Toggle fullscreen" onClick={() => { void (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen()) }}>⛶</Button>
    </div>
    <button className="reader-progress-line" aria-label={sourceId ? 'Show Reader controls' : 'Show reading progress'} aria-expanded={sourceId ? undefined : progressOpen} onClick={sourceId ? chromeActivity : tapProgress}>{!sourceId && <span style={{ width: `${percent}%` }} />}</button>
    {progressOpen && <div className="reader-progress-popup" role="status"><strong>{percent}% of available translation</strong><span>{readWords} of {progress.data?.total_words ?? 0} available words</span><span>{progress.data?.last_chapter ? `Available through ${progress.data.last_chapter.title}` : 'No verified text yet'}</span></div>}
    {settingsOpen && <aside className="reader-settings" aria-label="Reader settings" {...debugTag('RST')}><h3>Reading settings</h3>
      <label>Font size <input type="range" min="15" max="30" value={fontSize} onChange={e => setFontSize(Number(e.target.value))} /><output>{fontSize}px</output></label>
      <label>Font family <select value={fontFamily} onChange={e => setFontFamily(e.target.value)}><option value="serif">Serif</option><option value="sans">Sans serif</option><option value="mono">Monospace</option></select></label>
      <label>Line height <input type="range" min="1.35" max="2.1" step="0.05" value={lineHeight} onChange={e => setLineHeight(Number(e.target.value))} /><output>{lineHeight.toFixed(2)}</output></label>
      <label>Content width <input type="range" min="32" max="54" value={contentWidth} onChange={e => setContentWidth(Number(e.target.value))} /><output>{contentWidth} rem</output></label>
      <label>Header auto-hide <select value={headerAutoHide} onChange={e => setHeaderAutoHide(Number(e.target.value))}><option value="0">Off</option><option value="5">5 seconds</option><option value="10">10 seconds</option><option value="15">15 seconds</option></select></label>
      <label>Marker gesture <select value={markerGesture} disabled={archived || !!sourceId} onChange={e => setGesture('marker', e.target.value)}><option value="tap">Tap / click</option><option value="long">Long press</option><option value="drag">Horizontal drag</option><option value="off">Off</option></select></label>
      {archived && <p role="status">This workspace is archived. Existing markers are read-only; restore the workspace in Work to add or delete them.</p>}
      <label>Context Helper <select value={contextGesture} disabled={!!sourceId} onChange={e => setGesture('context', e.target.value)}><option value="tap">Tap / click</option><option value="long">Long press</option><option value="drag">Horizontal drag</option><option value="off">Off</option></select></label>{sourceId && <p role="status">Original EPUB text view from Library. Images and page styling are omitted. Workspace markers and Context Helper are unavailable.</p>}<p>Change the global color theme in Settings.</p></aside>}
    <div className="reader-status"><span>{sourceId ? 'Original EPUB text · read-only' : progress.data?.total_words ? `${progress.data.total_words} translated words available` : 'Waiting for the first verified P5 segment'}</span>{!sourceId && <Button variant={newText ? 'secondary' : 'ghost'} onClick={refreshChapter}>{newText ? 'New text available · load' : 'Refresh availability'}</Button>}</div>
    <ErrorNote error={metadata.error ?? progress.error ?? chapter.error ?? markers.error ?? markerError} retry={() => { setMarkerError(null); void progress.refetch(); void chapter.refetch(); void markers.refetch() }} />
    {contextError && <div className="notice" role="alert">{contextError}</div>}
    {chapter.data?.stale && <div className="notice">{chapter.data.warning ?? 'This verified output is stale and may need retranslation.'}</div>}
    <article className={`reader-page ${!sourceId && [archived ? 'off' : markerGesture, contextGesture].some(value => value === 'drag' || value === 'long') ? 'gesture-active' : ''}`} style={{ maxWidth: `${contentWidth}rem`, fontSize, lineHeight, fontFamily: fontFamily === 'mono' ? 'ui-monospace, monospace' : fontFamily === 'sans' ? 'Inter, ui-sans-serif, system-ui, sans-serif' : 'Georgia, ui-serif, serif' }} {...debugTag('RPG', chapterId)} data-chapter-id={chapter.data?.id} ref={article}
      onPointerDown={event => { const block = (event.target as Element).closest<HTMLElement>('[data-block-id]'); if (sourceId || !block || event.button !== 0 || !((!archived && markerGesture !== 'off') || contextGesture !== 'off')) return; if (previewTimer.current) clearTimeout(previewTimer.current); const point = { id: event.pointerId, x: event.clientX, y: event.clientY, scrollTop: scrollArea.current?.scrollTop ?? 0 } as NonNullable<typeof gesture.current>; gesture.current = point; if ((!archived && markerGesture === 'long') || contextGesture === 'long') point.timer = setTimeout(() => { if (point.scrolling || Math.abs((scrollArea.current?.scrollTop ?? 0) - point.scrollTop) > 2) return; point.fired = true; gestureAt('long', point.x, point.y) }, 550) }}
      onPointerMove={event => { const point = gesture.current; if (!point || point.id !== event.pointerId || point.fired || point.scrolling) return; const dx = event.clientX - point.x, dy = event.clientY - point.y; if (Math.hypot(dx, dy) > 11 && point.timer) clearTimeout(point.timer); if (Math.abs(dy) > 11 && Math.abs(dy) >= Math.abs(dx)) { point.scrolling = true; paintRanges(previewLayer.current, []); return } if (Math.abs(dx) > 11 && Math.abs(dx) > Math.abs(dy) * 1.2 && ((!archived && markerGesture === 'drag') || contextGesture === 'drag')) { event.currentTarget.setPointerCapture?.(event.pointerId); if (previewFrame.current !== null) cancelAnimationFrame(previewFrame.current); previewFrame.current = requestAnimationFrame(() => { previewFrame.current = null; previewGesture(point.x, point.y, event.clientX, event.clientY) }) } }}
      onPointerUp={event => { const point = gesture.current; if (!point || point.id !== event.pointerId) return; if (point.timer) clearTimeout(point.timer); if (previewFrame.current !== null) cancelAnimationFrame(previewFrame.current); previewFrame.current = null; gesture.current = null; const dx = event.clientX - point.x, dy = event.clientY - point.y; if (point.scrolling || Math.abs((scrollArea.current?.scrollTop ?? 0) - point.scrollTop) > 2) { paintRanges(previewLayer.current, []); return } if (!point.fired && Math.hypot(dx, dy) <= 11) gestureAt('tap', point.x, point.y); else if (!point.fired && Math.abs(dx) >= 28 && Math.abs(dx) > Math.abs(dy) * 1.2) gestureAt('drag', point.x, point.y, event.clientX, event.clientY); else if (!point.fired) paintRanges(previewLayer.current, []) }}
      onPointerCancel={() => { if (gesture.current?.timer) clearTimeout(gesture.current.timer); if (previewFrame.current !== null) cancelAnimationFrame(previewFrame.current); previewFrame.current = null; gesture.current = null; paintRanges(previewLayer.current, []) }}
      onContextMenu={event => { if (((!archived && markerGesture === 'long') || contextGesture === 'long') && (event.target as Element).closest('[data-block-id]')) event.preventDefault() }}
      onMouseUp={() => { if (sourceId) return; const value = selectedRange(window.getSelection()); if (value) setSelection(value) }} onKeyUp={() => { if (sourceId) return; const value = selectedRange(window.getSelection()); if (value) setSelection(value) }}>
      <h2>{chapter.data?.title ?? metadata.data?.title ?? 'Loading translated text…'}</h2>
      {chapter.data?.blocks.map(block => <div className="reader-block-wrap" key={block.id}>{chapterMarkers.some(m => m.block_id === block.id) && <button className="reader-gutter-marker" aria-label={`Markers in ${block.id}`} onClick={() => { setMarkerAnchor(chapterMarkers.find(m => m.block_id === block.id)?.id ?? null); setActiveMarker(activeMarker === block.id ? null : block.id) }}>▮</button>}<p data-block-id={block.id} className={block.kind.startsWith('h') ? 'reader-heading' : undefined}>{inlineRuns(block.text, block.formatting).map((run,i) => run.style === 'em' ? <em key={i}>{run.text}</em> : run.style === 'strong' ? <strong key={i}>{run.text}</strong> : run.text)}</p>{activeMarker === block.id && chapterMarkers.some(m => m.block_id === block.id) && <aside ref={markerPopover} className="reader-marker-popover" aria-label="Marker details" {...debugTag('RMP', block.id)}><div className="reader-marker-popover-title">Marker {recentMarker === markerAnchor && <span role="status">saved</span>}</div>{chapterMarkers.filter(m => m.block_id === block.id).map(m => <div className="reader-marker-popover-entry" key={m.id}><span className="reader-marker-id">ID <code>{m.id}</code></span><blockquote>{m.text}</blockquote>{!archived && <Button className="reader-delete-marker" disabled={markerPending || connection !== 'Live'} onClick={() => void deleteMarker(m.id)}>Delete marker</Button>}</div>)}<Link className="reader-work-link" to="/work/workspaces/$workspaceId/review" params={{workspaceId:id}} hash="reader-markers">View markers in Work →</Link></aside>}</div>)}
      {chapter.data && !chapter.data.blocks.length && <Empty>{sourceId ? 'This EPUB section has no readable text.' : 'No completed translation segments are available for this chapter yet. Reader checks for new segments automatically.'}</Empty>}
      {chapter.data?.unavailable && chapter.data.blocks.length > 0 && <div className="reader-boundary">End of currently available translation. {chapter.data.unavailable.reason}</div>}
      {chapter.data && <footer className="reader-chapter-end">{chapters[index + 1] && availableChapters.has(chapters[index + 1]!.id) ? <Button variant="ghost" onClick={() => choose(chapters[index + 1]!.id)}>Next chapter → {chapters[index + 1]!.title}</Button> : sourceId ? 'End of book' : 'End of available translation'}</footer>}
    </article>
    {selection && <div className="reader-selection" {...debugTag('RSL')}><span>{selection.text}</span>{!archived && <Button disabled={markerPending || connection !== 'Live' || !markers.data} onClick={() => void mark(selection)}>Mark selection</Button>}<Button onClick={() => void openContext(selection.block_id, selection.start)}>Context</Button><Button variant="ghost" onClick={() => setSelection(null)}>Dismiss</Button></div>}
    {!!chapterMarkers.length && <details className="reader-markers" {...debugTag('RMK')}><summary>Markers · {chapterMarkers.length}</summary>{chapterMarkers.map(m => <div key={m.id}><span>{m.text}</span>{!archived && <Button className="reader-delete-marker" disabled={markerPending || connection !== 'Live'} onClick={() => void deleteMarker(m.id)}>Delete marker</Button>}</div>)}<Link className="reader-work-link" to="/work/workspaces/$workspaceId/review" params={{workspaceId:id}} hash="reader-markers">View markers in Work →</Link></details>}
    {context && <Overlay debugId="RCM" title={context.recognized ? context.display_name ?? context.title ?? 'Context' : 'No recognized term'} close={() => setContext(null)} compact><div className="modal-body reader-context-body">{context.recognized ? <>{!!context.attributes?.length && <dl>{context.attributes.map(a => <div key={a.label}><dt>{a.label}</dt><dd>{a.value}</dd></div>)}</dl>}{!!context.statements?.length && <ul>{context.statements.map((s,i) => <li key={i}>{s}</li>)}</ul>}{!!context.earlier_mentions?.length && <section><h3>Earlier mentions</h3>{context.earlier_mentions.map((m,i) => <figure key={i}><blockquote>{m.text}</blockquote><figcaption>{m.chapter_title}</figcaption></figure>)}</section>}{!!context.same_block_context?.length && <section><h3>Earlier in this passage</h3>{context.same_block_context.map((m,i) => <blockquote key={i}>{m.text}</blockquote>)}</section>}{!context.attributes?.length && !context.statements?.length && !context.earlier_mentions?.length && !context.same_block_context?.length && <p>No earlier context is available at this reading position.</p>}</> : <p>No terminology context was recognized at this position.</p>}</div></Overlay>}
  </div>
}
