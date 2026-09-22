import { useContext, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { z } from 'zod'
import { endpoint, request, Scope, useApi } from '../../api/client'
import { chapterSchema, contextSchema, markerMutationSchema, markersSchema, readerProgressSchema, readerSchema, workspacesSchema } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { Button, Empty, ErrorNote, Overlay } from '../../components/ui/common'
import { preference, savePreference } from '../../app/preferences'
import { debugTag } from '../../debug/regions'
import { codePointToUtf16, inlineRuns, selectedRange } from './ranges'
export function ReaderPage() {
  const { workspaceId } = useParams({ strict: false })
  const scope = useContext(Scope)
  const navigate = useNavigate()
  const all = useApi('/api/workspaces', workspacesSchema)
  const books = useMemo(() => all.data?.workspaces.filter(w => w.prepared) ?? [], [all.data])
  const last = /^\/reader\/([A-Za-z0-9_.-]+)(?:\?|$)/.exec(preference(scope, 'reader.route'))?.[1]
  const chosen = books.find(w => w.workspace_id === workspaceId) ?? books.find(w => w.workspace_id === last) ?? books[0]
  useEffect(() => {
    if (workspaceId || !all.data || !books.length) return
    let cancelled = false
    const select = async () => {
      let candidate = books.find(w => w.workspace_id === last)
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
  }, [all.data, books, workspaceId, last, navigate])
  const id = chosen?.workspace_id ?? ''
  return <main className="main" {...debugTag('RDR')}><div className="page-title-row"><div><div className="eyebrow">Reader</div><h1>Reading view</h1><div className="subtitle">Verified translated text. Your reading position is independent from Work.</div></div></div><ErrorNote error={all.error} /><div className="reader-shell" {...debugTag('RSH')}><nav className="reader-books" aria-label="Reading workspaces" {...debugTag('RBL')}>{books.map(w => <Link key={w.workspace_id} to="/reader/$workspaceId" params={{ workspaceId: w.workspace_id }} className={`reader-book ${id === w.workspace_id ? 'active' : ''}`} {...debugTag('RBB', w.workspace_id)}><strong>{w.metadata.title}</strong><span>{w.metadata.creators.join(', ') || '—'}{w.metadata.lifecycle.archived ? ' · Archived' : ''}</span></Link>)}</nav><div className="reader-content" {...debugTag('RBC', id)}>{chosen ? <ReadingBook key={id} id={id} /> : <Empty>{all.isPending ? 'Loading books…' : 'No translated books yet.'}</Empty>}</div></div></main>
}
function ReadingBook({ id }: { id: string }) {
  const metadata = useApi(endpoint(id,'reader'), readerSchema)
  const markers = useApi(endpoint(id,'reader/markers'), markersSchema)
  const scope = useContext(Scope)
  const search = useSearch({ strict: false })
  const navigate = useNavigate()
  const saved = preference(scope, `${id}.reader.chapter`)
  const chapters = metadata.data?.chapters ?? []
  const chapterId = chapters.find(c => c.id === search.chapter)?.id ?? chapters.find(c => c.id === saved)?.id ?? chapters[0]?.id
  const chapter = useApi(endpoint(id,`reader/chapters/${encodeURIComponent(chapterId ?? '')}`), chapterSchema, !!chapterId)
  const command = useCommand(id)
  const [selection, setSelection] = useState<ReturnType<typeof selectedRange>>(null)
  const [context, setContext] = useState<z.infer<typeof contextSchema> | null>(null)
  const restored = useRef('')
  const article = useRef<HTMLElement>(null)
  useEffect(() => {
    if (!metadata.data || !chapter.data || restored.current === chapterId) return
    restored.current = chapterId ?? ''
    const key = `${id}.${metadata.data.book_fingerprint}.${chapterId}.anchor`
    const stored = preference(scope, key)
    let anchor: { block_id: string; offset: number }
    try { anchor = JSON.parse(stored) as typeof anchor } catch { anchor = { block_id: stored, offset: 0 } }
    if (!anchor || typeof anchor.block_id !== 'string' || !Number.isInteger(anchor.offset)) return
    const node = Array.from(article.current?.querySelectorAll<HTMLElement>('[data-block-id]') ?? []).find(n => n.dataset.blockId === anchor.block_id)
    if (node) {
      node.scrollIntoView({ block: 'start' })
      let remaining = codePointToUtf16(node.textContent ?? '', anchor.offset)
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT)
      let text: Node | null
      while ((text = walker.nextNode())) {
        if (remaining <= (text.textContent?.length ?? 0)) {
          const range = document.createRange(); range.setStart(text, remaining); range.collapse(true)
          window.scrollBy(0, range.getBoundingClientRect().top - 76); break
        }
        remaining -= text.textContent?.length ?? 0
      }
    }
  }, [chapter.data, chapterId, id, metadata.data, scope])
  useEffect(() => {
    const save = () => {
      if (!metadata.data || !chapterId || !article.current) return
      const nodes = Array.from(article.current.querySelectorAll<HTMLElement>('[data-block-id]'))
      const node = nodes.find(n => n.getBoundingClientRect().bottom > 70)
      if (node) {
        const rect = node.getBoundingClientRect()
        const caret = document.caretPositionFromPoint(rect.left + 2, Math.max(76, rect.top + 3))
        let offset = 0
        if (caret && node.contains(caret.offsetNode)) {
          const prefix = document.createRange(); prefix.selectNodeContents(node); prefix.setEnd(caret.offsetNode, caret.offset)
          offset = Array.from(prefix.toString()).length
        }
        savePreference(scope, `${id}.${metadata.data.book_fingerprint}.${chapterId}.anchor`, JSON.stringify({ block_id: node.dataset.blockId, offset }))
      }
    }
    window.addEventListener('scroll', save, { passive: true })
    return () => window.removeEventListener('scroll', save)
  }, [id, chapterId, metadata.data, scope])
  const choose = (value: string) => { setSelection(null); setContext(null); savePreference(scope, `${id}.reader.chapter`, value); void navigate({ to: '.', search: old => ({ ...old, chapter: value }), replace: true }) }
  const index = chapters.findIndex(c => c.id === chapterId)
  return <><div className="reader-controls" {...debugTag('RCO')}><label htmlFor="reader-chapter">Chapter</label><select id="reader-chapter" value={chapterId ?? ''} onChange={e => choose(e.target.value)}>{chapters.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}</select><Button disabled={index <= 0} onClick={() => choose(chapters[index - 1]!.id)}>Previous</Button><Button disabled={index < 0 || index >= chapters.length - 1} onClick={() => choose(chapters[index + 1]!.id)}>Next</Button><Button variant="ghost" onClick={() => { if (!window.getSelection()?.toString()) void chapter.refetch() }}>Refresh availability</Button></div><ErrorNote error={metadata.error ?? chapter.error ?? command.error} retry={() => void chapter.refetch()} />
    {chapter.data?.stale && <div className="notice">{chapter.data.warning ?? 'This verified output is stale and may need retranslation.'}</div>}
    <article className="reader-page" {...debugTag('RPG', chapterId)} ref={article} onMouseUp={() => { const value = selectedRange(window.getSelection()); if (value) setSelection(value) }} onKeyUp={() => { const value = selectedRange(window.getSelection()); if (value) setSelection(value) }}><h2>{chapter.data?.title ?? metadata.data?.title ?? 'Loading translated text…'}</h2>{chapter.data?.blocks.map(block => <p key={block.id} data-block-id={block.id} className={block.kind.startsWith('h') ? 'reader-heading' : undefined}>{inlineRuns(block.text, block.formatting).map((run,i) => run.style === 'em' ? <em key={i}>{run.text}</em> : run.style === 'strong' ? <strong key={i}>{run.text}</strong> : run.text)}</p>)}{chapter.data && !chapter.data.blocks.length && <Empty>No translated text yet.</Empty>}{chapter.data?.unavailable && <div className="notice">Translation is unavailable beyond these blocks: {chapter.data.unavailable.reason}</div>}</article>
    {selection && <div className="reader-selection" {...debugTag('RSL')}><span>{selection.text}</span><Button disabled={command.disabled || !markers.data} onClick={async () => { if (await command.send(endpoint(id,'reader/markers'), markerMutationSchema, { ...selection, chapter_id: chapterId, revision: markers.data!._revision })) setSelection(null) }}>Mark selection</Button><Button disabled={command.disabled} onClick={async () => { const value = await command.send(endpoint(id,'reader/context'), contextSchema, { chapter_id: chapterId, block_id: selection.block_id, position: selection.start }); if (value) setContext(value) }}>Context</Button><Button variant="ghost" onClick={() => setSelection(null)}>Dismiss</Button></div>}
    {!!markers.data?.markers.length && <details className="reader-markers" {...debugTag('RMK')}><summary>Markers · {markers.data.markers.length}</summary><ErrorNote error={markers.error} />{markers.data.markers.filter(m => m.chapter_id === chapterId).map(m => <div key={m.id}><span>{m.text}</span><Button disabled={command.disabled} onClick={() => void command.send(endpoint(id,`reader/markers/${encodeURIComponent(m.id)}`), markerMutationSchema, { revision: markers.data!._revision }, 'DELETE')}>Delete marker</Button></div>)}</details>}
    {context && <Overlay debugId="RCM" title={context.recognized ? context.display_name ?? 'Context' : 'No recognized term'} close={() => setContext(null)} compact><div className="modal-body">{context.recognized ? <>{context.attributes?.map(a => <p key={a.label}>{a.label}: {a.value}</p>)}{context.statements?.map((s,i) => <p key={i}>{s}</p>)}{!context.statements?.length && <p>No earlier information is available at this reading position.</p>}</> : <p>No terminology context was recognized at this position.</p>}</div></Overlay>}
  </>
}
