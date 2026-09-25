import { useContext, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { z } from 'zod'
import { endpoint, queryClient, request, Scope, useApi } from '../../api/client'
import { chapterSchema, contextSchema, markerMutationSchema, markersSchema, readerProgressSchema, readerSchema, workspacesSchema } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { Button, Empty, ErrorNote, Overlay } from '../../components/ui/common'
import { preference, savePreference } from '../../app/preferences'
import { debugTag } from '../../debug/regions'
import { codePointToUtf16, inlineRuns, selectedRange, snapWordRange } from './ranges'
export function ReaderPage() {
  const { workspaceId } = useParams({ strict: false })
  const scope = useContext(Scope)
  const navigate = useNavigate()
  const all = useApi('/api/workspaces', workspacesSchema)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const books = useMemo(() => all.data?.workspaces.filter(w => w.prepared) ?? [], [all.data])
  const last = /^\/reader\/([A-Za-z0-9_.-]+)(?:\?|$)/.exec(preference(scope, 'reader.route'))?.[1]
  const working = /^\/work\/workspaces\/([A-Za-z0-9_.-]+)(?:\/|\?|$)/.exec(preference(scope, 'work.route'))?.[1]
  const workingBook = books.find(w => w.workspace_id === working)
  const chosen = workspaceId ? books.find(w => w.workspace_id === workspaceId) : books.find(w => w.workspace_id === last) ?? books.find(w => w.workspace_id === working) ?? books[0]
  useEffect(() => {
    if (workspaceId || !all.data || !books.length) return
    let cancelled = false
    const select = async () => {
      let candidate = books.find(w => w.workspace_id === last) ?? books.find(w => w.workspace_id === working)
      if (!candidate) for (const book of books) {
        try {
          const progress = await request(endpoint(book.workspace_id, 'reader/progress'), readerProgressSchema)
          if (progress.total_words > 0) { candidate = book; break }
        } catch { /* Keep the first available workspace if its Reader is unavailable. */ }
      }
      if (!cancelled) void navigate({ to: '/reader/$workspaceId', params: { workspaceId: (candidate ?? books[0]!).workspace_id }, replace: true })
    }
    void select()
    return () => { cancelled = true }
  }, [all.data, books, workspaceId, last, working, navigate])
  const id = chosen?.workspace_id ?? ''
  const bookList = <nav className={`reader-books ${libraryOpen ? 'open' : ''}`} aria-label="Reading workspaces" {...debugTag('RBL')}>{workingBook && <Link to="/reader/$workspaceId" params={{ workspaceId: workingBook.workspace_id }} onClick={() => setLibraryOpen(false)} className="reader-book reader-current-workspace"><strong>Current workspace</strong><span>{workingBook.metadata.title}</span></Link>}{books.map(w => <Link key={w.workspace_id} to="/reader/$workspaceId" params={{ workspaceId: w.workspace_id }} onClick={() => setLibraryOpen(false)} className={`reader-book ${id === w.workspace_id ? 'active' : ''}`} {...debugTag('RBB', w.workspace_id)}><strong>{w.metadata.title}</strong><span>{w.metadata.creators.join(', ') || '—'}{w.metadata.lifecycle.archived ? ' · Archived' : ''}</span></Link>)}</nav>
  return <main className="main reader-main" {...debugTag('RDR')}><div className="reader-mobile-books"><Button onClick={() => setLibraryOpen(open => !open)} aria-expanded={libraryOpen}>Library · {chosen?.metadata.title ?? 'Select book'}</Button></div><ErrorNote error={all.error} /><div className="reader-shell" {...debugTag('RSH')}>{bookList}<div className="reader-content" {...debugTag('RBC', id)}>{chosen ? <ReadingBook key={id} id={id} /> : <Empty>{all.isPending ? 'Loading books…' : workspaceId ? 'This workspace is not prepared for reading.' : 'No prepared books yet.'}</Empty>}</div></div></main>
}
function ReadingBook({ id }: { id: string }) {
  const metadata = useApi(endpoint(id,'reader'), readerSchema)
  const progress = useApi(endpoint(id,'reader/progress'), readerProgressSchema)
  const markers = useApi(endpoint(id,'reader/markers'), markersSchema)
  const scope = useContext(Scope)
  const search = useSearch({ strict: false })
  const navigate = useNavigate()
  const saved = preference(scope, `${id}.reader.chapter`)
  const chapters = metadata.data?.chapters ?? []
  const chapterId = chapters.find(c => c.id === search.chapter)?.id ?? chapters.find(c => c.id === saved)?.id ?? chapters[0]?.id
  const chapter = useApi(endpoint(id,`reader/chapters/${encodeURIComponent(chapterId ?? '')}`), chapterSchema, !!chapterId)
  const command = useCommand(id)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [contentsOpen, setContentsOpen] = useState(false)
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
  const article = useRef<HTMLElement>(null)
  const scrollArea = useRef<HTMLDivElement>(null)
  const lastAvailable = useRef<{ chapter: string; signature: string } | null>(null)
  const gesture = useRef<{ x: number; y: number; block: HTMLElement; timer?: ReturnType<typeof setTimeout>; fired?: boolean } | null>(null)
  const controlTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const availableChapters = new Set(progress.data?.chapters.map(c => c.id) ?? [])
  const visibleProgress = progress.data?.chapters.find(c => c.id === chapterId)
  useEffect(() => {
    if (!progress.data || !chapterId) return
    const signature = JSON.stringify(visibleProgress?.blocks.map(block => [block.id, block.words]) ?? [])
    if (lastAvailable.current?.chapter === chapterId && lastAvailable.current.signature !== signature && visibleProgress?.blocks.length) {
      if (!chapter.data?.blocks.length) void chapter.refetch()
      else setNewText(true)
    }
    lastAvailable.current = { chapter: chapterId, signature }
  // Chapter refetch is deliberately tied to availability, never to a timer or ordinary query refresh.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chapterId, progress.data])
  useEffect(() => {
    for (const [key, value] of Object.entries({fontSize, fontFamily, lineHeight, contentWidth, headerAutoHide, markerGesture, contextGesture})) savePreference(scope, `reader.${key}`, String(value))
  }, [scope, fontSize, fontFamily, lineHeight, contentWidth, headerAutoHide, markerGesture, contextGesture])
  useEffect(() => {
    if (!headerAutoHide || settingsOpen || contentsOpen || progressOpen) { setControlsHidden(false); if (controlTimer.current) clearTimeout(controlTimer.current); return }
    controlTimer.current = setTimeout(() => setControlsHidden(true), headerAutoHide * 1000)
    return () => { if (controlTimer.current) clearTimeout(controlTimer.current) }
  }, [headerAutoHide, settingsOpen, contentsOpen, progressOpen, chapterId])
  const wakeControls = () => {
    setControlsHidden(false)
    if (controlTimer.current) clearTimeout(controlTimer.current)
    if (headerAutoHide && !settingsOpen && !contentsOpen && !progressOpen) controlTimer.current = setTimeout(() => setControlsHidden(true), headerAutoHide * 1000)
  }
  useEffect(() => {
    if (!metadata.data || !chapter.data || restored.current === chapterId) return
    restored.current = chapterId ?? ''
    const key = `${id}.${metadata.data.book_fingerprint}.${chapterId}.anchor`
    const stored = preference(scope, key)
    let anchor: { block_id: string; offset: number }
    try { anchor = JSON.parse(stored) as typeof anchor } catch { anchor = { block_id: stored, offset: 0 } }
    if (!anchor || typeof anchor.block_id !== 'string' || !Number.isInteger(anchor.offset)) { scrollArea.current?.scrollTo({ top: 0 }); return }
    const node = Array.from(article.current?.querySelectorAll<HTMLElement>('[data-block-id]') ?? []).find(n => n.dataset.blockId === anchor.block_id)
    if (node) {
      node.scrollIntoView({ block: 'start' })
      let remaining = codePointToUtf16(node.textContent ?? '', anchor.offset)
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT)
      let text: Node | null
      while ((text = walker.nextNode())) {
        if (remaining <= (text.textContent?.length ?? 0)) {
          const range = document.createRange(); range.setStart(text, remaining); range.collapse(true)
          scrollArea.current?.scrollBy(0, range.getBoundingClientRect().top - (scrollArea.current?.getBoundingClientRect().top ?? 0) - 76); break
        }
        remaining -= text.textContent?.length ?? 0
      }
    }
  }, [chapter.data, chapterId, id, metadata.data, scope])
  useEffect(() => {
    const save = () => {
      if (!metadata.data || !chapterId || !article.current) return
      const nodes = Array.from(article.current.querySelectorAll<HTMLElement>('[data-block-id]'))
      const top = scrollArea.current?.getBoundingClientRect().top ?? 70
      const node = nodes.find(n => n.getBoundingClientRect().bottom > top + 8) ?? nodes.at(-1)
      if (node) {
        const rect = node.getBoundingClientRect()
        const caret = document.caretPositionFromPoint?.(rect.left + 2, Math.max(top + 10, rect.top + 3))
        let offset = rect.bottom <= top + 8 ? Array.from(node.textContent ?? '').length : 0
        if (caret && node.contains(caret.offsetNode)) {
          const prefix = document.createRange(); prefix.selectNodeContents(node); prefix.setEnd(caret.offsetNode, caret.offset)
          offset = Array.from(prefix.toString()).length
        }
        savePreference(scope, `${id}.${metadata.data.book_fingerprint}.${chapterId}.anchor`, JSON.stringify({ block_id: node.dataset.blockId, offset }))
        const snapshot = visibleProgress?.blocks.find(b => b.id === node.dataset.blockId)
        if (snapshot) {
          const prefix = Array.from(node.textContent ?? '').slice(0, offset).join('')
          setReadWords(Math.min(progress.data?.total_words ?? 0, snapshot.start + (prefix.match(/[\p{L}\p{N}\p{M}_]+(?:[’'-][\p{L}\p{N}\p{M}_]+)*/gu)?.length ?? 0)))
        }
      }
    }
    const area = scrollArea.current
    let timer: ReturnType<typeof setTimeout>
    const onScroll = () => { wakeControls(); clearTimeout(timer); timer = setTimeout(save, 140) }
    area?.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('beforeunload', save)
    return () => { area?.removeEventListener('scroll', onScroll); window.removeEventListener('scroll', onScroll); window.removeEventListener('beforeunload', save); clearTimeout(timer) }
  // The handler is installed for the selected chapter; settings only affect the control timer.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, chapterId, metadata.data, scope, progress.data])
  const choose = (value: string) => { setSelection(null); setContext(null); setActiveMarker(null); setReadWords(0); setNewText(false); setContentsOpen(false); scrollArea.current?.scrollTo({ top: 0 }); savePreference(scope, `${id}.reader.chapter`, value); if (metadata.data) savePreference(scope, `${id}.${metadata.data.book_fingerprint}.${value}.anchor`, JSON.stringify({block_id:'',offset:0})); void queryClient.invalidateQueries({queryKey: [scope, endpoint(id, `reader/chapters/${encodeURIComponent(value)}`)], exact: true}); void navigate({ to: '.', search: old => ({ ...old, chapter: value }), replace: true }) }
  const index = chapters.findIndex(c => c.id === chapterId)
  const refreshChapter = () => { if (window.getSelection()?.toString()) return; setNewText(false); void chapter.refetch() }
  const mark = async (value: NonNullable<ReturnType<typeof selectedRange>>) => {
    if (!markers.data || !chapterId) return
    const result = await command.send(endpoint(id,'reader/markers'), markerMutationSchema, { ...value, chapter_id: chapterId, revision: markers.data._revision })
    if (result) { setSelection(null); await markers.refetch() }
  }
  const openContext = async (blockId: string, position: number) => {
    if (!chapterId) return
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
  const gestureAt = (kind: 'tap' | 'long' | 'drag', x: number, y: number, endX = x, endY = y) => {
    if (window.getSelection()?.toString()) return
    const first = wordAtPoint(x, y), last = wordAtPoint(endX, endY)
    if (!first || !last || first.block !== last.block) return
    const fullText = first.block.textContent ?? ''
    const range = snapWordRange(fullText, first.offset, last.offset)
    if (!range) return
    const value = { block_id: first.block.dataset.blockId!, ...range, text: Array.from(fullText).slice(range.start, range.end).join('') }
    if (markerGesture === kind) void mark(value)
    else if (contextGesture === kind) void openContext(value.block_id, Math.min(range.end - 1, Math.max(range.start, first.offset)))
  }
  const setGesture = (which: 'marker' | 'context', value: string) => {
    if (which === 'marker') { setMarkerGesture(value); if (value !== 'off' && contextGesture === value) setContextGesture(markerGesture) }
    else { setContextGesture(value); if (value !== 'off' && markerGesture === value) setMarkerGesture(contextGesture) }
  }
  const chapterMarkers = markers.data?.markers.filter(m => m.chapter_id === chapterId) ?? []
  const percent = progress.data?.total_words ? Math.min(100, Math.round(readWords / progress.data.total_words * 100)) : 0
  return <div className="reader-reading-area" ref={scrollArea}>
    <div className={`reader-controls ${controlsHidden ? 'reader-controls-hidden' : ''}`} {...debugTag('RCO')}>
      <Button variant="ghost" aria-label="Table of contents" aria-expanded={contentsOpen} onClick={() => { setContentsOpen(!contentsOpen); setSettingsOpen(false) }}>☰</Button>
      <Button variant="ghost" aria-label="Previous chapter" disabled={index <= 0} onClick={() => choose(chapters[index - 1]!.id)}>‹</Button>
      <div className="reader-current"><strong>{metadata.data?.title ?? 'Reader'}</strong><span>{chapters[index]?.title ?? 'Choose a chapter'}</span></div>
      <Button variant="ghost" aria-label="Next chapter" disabled={index < 0 || index >= chapters.length - 1} onClick={() => choose(chapters[index + 1]!.id)}>›</Button>
      <Button variant="ghost" aria-label="Reader settings" aria-expanded={settingsOpen} onClick={() => { setSettingsOpen(!settingsOpen); setContentsOpen(false) }}>Aa</Button>
      <Button variant="ghost" aria-label="Toggle fullscreen" onClick={() => { void (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen()) }}>⛶</Button>
    </div>
    <button className="reader-progress-line" aria-label="Show reading progress" aria-expanded={progressOpen} onClick={() => setProgressOpen(!progressOpen)}><span style={{ width: `${percent}%` }} /></button>
    {progressOpen && <div className="reader-progress-popup" role="status"><strong>{percent}% of available translation</strong><span>{readWords} of {progress.data?.total_words ?? 0} available words</span><span>{progress.data?.last_chapter ? `Available through ${progress.data.last_chapter.title}` : 'No verified text yet'}</span></div>}
    {contentsOpen && <aside className="reader-toc" aria-label="Table of contents" {...debugTag('RTC')}><h3>Contents</h3>{chapters.map(c => <button key={c.id} className={c.id === chapterId ? 'active' : ''} onClick={() => choose(c.id)}>{c.title}{availableChapters.has(c.id) ? '' : ' · awaiting translation'}</button>)}</aside>}
    {settingsOpen && <aside className="reader-settings" aria-label="Reader settings" {...debugTag('RST')}><h3>Reading settings</h3>
      <label>Font size <input type="range" min="15" max="30" value={fontSize} onChange={e => setFontSize(Number(e.target.value))} /><output>{fontSize}px</output></label>
      <label>Font family <select value={fontFamily} onChange={e => setFontFamily(e.target.value)}><option value="serif">Serif</option><option value="sans">Sans serif</option><option value="mono">Monospace</option></select></label>
      <label>Line height <input type="range" min="1.35" max="2.1" step="0.05" value={lineHeight} onChange={e => setLineHeight(Number(e.target.value))} /><output>{lineHeight.toFixed(2)}</output></label>
      <label>Content width <input type="range" min="32" max="54" value={contentWidth} onChange={e => setContentWidth(Number(e.target.value))} /><output>{contentWidth} rem</output></label>
      <label>Header auto-hide <select value={headerAutoHide} onChange={e => setHeaderAutoHide(Number(e.target.value))}><option value="0">Off</option><option value="5">5 seconds</option><option value="10">10 seconds</option><option value="15">15 seconds</option></select></label>
      <label>Marker gesture <select value={markerGesture} onChange={e => setGesture('marker', e.target.value)}><option value="tap">Tap / click</option><option value="long">Long press</option><option value="drag">Horizontal drag</option><option value="off">Off</option></select></label>
      <label>Context Helper <select value={contextGesture} onChange={e => setGesture('context', e.target.value)}><option value="tap">Tap / click</option><option value="long">Long press</option><option value="drag">Horizontal drag</option><option value="off">Off</option></select></label><p>Change the global color theme in Settings.</p></aside>}
    <div className="reader-status"><span>{progress.data?.total_words ? `${progress.data.total_words} translated words available` : 'Waiting for the first verified P5 segment'}</span><Button variant={newText ? 'secondary' : 'ghost'} onClick={refreshChapter}>{newText ? 'New text available · load' : 'Refresh availability'}</Button></div>
    <ErrorNote error={metadata.error ?? progress.error ?? chapter.error ?? markers.error ?? command.error} retry={() => { void progress.refetch(); void chapter.refetch(); void markers.refetch() }} />
    {contextError && <div className="notice" role="alert">{contextError}</div>}
    {chapter.data?.stale && <div className="notice">{chapter.data.warning ?? 'This verified output is stale and may need retranslation.'}</div>}
    <article className={`reader-page ${markerGesture === 'drag' || contextGesture === 'drag' ? 'gesture-drag' : ''}`} style={{ maxWidth: `${contentWidth}rem`, fontSize, lineHeight, fontFamily: fontFamily === 'mono' ? 'ui-monospace, monospace' : fontFamily === 'sans' ? 'Inter, ui-sans-serif, system-ui, sans-serif' : 'Georgia, ui-serif, serif' }} {...debugTag('RPG', chapterId)} ref={article}
      onPointerDown={event => { const block = (event.target as Element).closest<HTMLElement>('[data-block-id]'); if (!block || event.button !== 0) return; const point = { x: event.clientX, y: event.clientY, block } as NonNullable<typeof gesture.current>; if (markerGesture === 'long' || contextGesture === 'long') point.timer = setTimeout(() => { point.fired = true; gestureAt('long', point.x, point.y) }, 550); gesture.current = point }}
      onPointerMove={event => { const point = gesture.current; if (!point || point.fired) return; if (Math.hypot(event.clientX - point.x, event.clientY - point.y) > 11 && point.timer) clearTimeout(point.timer) }}
      onPointerUp={event => { const point = gesture.current; if (!point) return; if (point.timer) clearTimeout(point.timer); gesture.current = null; const dx = event.clientX - point.x, dy = event.clientY - point.y; if (!point.fired && Math.hypot(dx, dy) <= 11) gestureAt('tap', point.x, point.y); else if (!point.fired && Math.abs(dx) >= 28 && Math.abs(dx) > Math.abs(dy) * 1.2) gestureAt('drag', point.x, point.y, event.clientX, event.clientY) }}
      onPointerCancel={() => { if (gesture.current?.timer) clearTimeout(gesture.current.timer); gesture.current = null }}
      onMouseUp={() => { const value = selectedRange(window.getSelection()); if (value) setSelection(value) }} onKeyUp={() => { const value = selectedRange(window.getSelection()); if (value) setSelection(value) }}>
      <h2>{chapter.data?.title ?? metadata.data?.title ?? 'Loading translated text…'}</h2>
      {chapter.data?.blocks.map(block => <div className="reader-block-wrap" key={block.id}>{chapterMarkers.some(m => m.block_id === block.id) && <button className="reader-gutter-marker" aria-label={`Markers in ${block.id}`} onClick={() => setActiveMarker(activeMarker === block.id ? null : block.id)}>▮</button>}<p data-block-id={block.id} className={block.kind.startsWith('h') ? 'reader-heading' : undefined}>{inlineRuns(block.text, block.formatting).map((run,i) => run.style === 'em' ? <em key={i}>{run.text}</em> : run.style === 'strong' ? <strong key={i}>{run.text}</strong> : run.text)}</p>{activeMarker === block.id && <div className="reader-marker-popover">{chapterMarkers.filter(m => m.block_id === block.id).map(m => <div key={m.id}><span>{m.text}</span><Button disabled={command.disabled} onClick={async () => { if (await command.send(endpoint(id,`reader/markers/${encodeURIComponent(m.id)}`), markerMutationSchema, { revision: markers.data!._revision }, 'DELETE')) { setActiveMarker(null); await markers.refetch() } }}>Delete marker</Button></div>)}</div>}</div>)}
      {chapter.data && !chapter.data.blocks.length && <Empty>No completed translation segments are available for this chapter yet. Reader checks for new segments automatically.</Empty>}
      {chapter.data?.unavailable && chapter.data.blocks.length > 0 && <div className="reader-boundary">End of currently available translation. {chapter.data.unavailable.reason}</div>}
      {chapter.data && <footer className="reader-chapter-end">{chapters[index + 1] && availableChapters.has(chapters[index + 1]!.id) ? <Button variant="ghost" onClick={() => choose(chapters[index + 1]!.id)}>Next chapter → {chapters[index + 1]!.title}</Button> : 'End of available translation'}</footer>}
    </article>
    {selection && <div className="reader-selection" {...debugTag('RSL')}><span>{selection.text}</span><Button disabled={command.disabled || !markers.data} onClick={() => void mark(selection)}>Mark selection</Button><Button onClick={() => void openContext(selection.block_id, selection.start)}>Context</Button><Button variant="ghost" onClick={() => setSelection(null)}>Dismiss</Button></div>}
    {!!chapterMarkers.length && <details className="reader-markers" {...debugTag('RMK')}><summary>Markers · {chapterMarkers.length}</summary>{chapterMarkers.map(m => <div key={m.id}><span>{m.text}</span><Button disabled={command.disabled} onClick={async () => { if (await command.send(endpoint(id,`reader/markers/${encodeURIComponent(m.id)}`), markerMutationSchema, { revision: markers.data!._revision }, 'DELETE')) await markers.refetch() }}>Delete marker</Button></div>)}</details>}
    {context && <Overlay debugId="RCM" title={context.recognized ? context.display_name ?? context.title ?? 'Context' : 'No recognized term'} close={() => setContext(null)} compact><div className="modal-body reader-context-body">{context.recognized ? <>{!!context.attributes?.length && <dl>{context.attributes.map(a => <div key={a.label}><dt>{a.label}</dt><dd>{a.value}</dd></div>)}</dl>}{!!context.statements?.length && <ul>{context.statements.map((s,i) => <li key={i}>{s}</li>)}</ul>}{!!context.earlier_mentions?.length && <section><h3>Earlier mentions</h3>{context.earlier_mentions.map((m,i) => <figure key={i}><blockquote>{m.text}</blockquote><figcaption>{m.chapter_title}</figcaption></figure>)}</section>}{!!context.same_block_context?.length && <section><h3>Earlier in this passage</h3>{context.same_block_context.map((m,i) => <blockquote key={i}>{m.text}</blockquote>)}</section>}{!context.attributes?.length && !context.statements?.length && !context.earlier_mentions?.length && !context.same_block_context?.length && <p>No earlier context is available at this reading position.</p>}</> : <p>No terminology context was recognized at this position.</p>}</div></Overlay>}
  </div>
}
