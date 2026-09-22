import { useContext, useEffect, useRef, useState } from 'react'
import { useBlocker, useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { endpoint, queryClient, Scope, useApi } from '../../api/client'
import { approvalSchema, evidenceSchema, patchSchema, pipelineSchema, reviewSchema, workspacesSchema, type Review, type Term } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { Back, Button, Empty, ErrorNote, Overlay } from '../../components/ui/common'
import { debugTag } from '../../debug/regions'
export const effective = (term: Term) => term.custom.trim() || term.candidates.find(c => c.number === term.select)?.text || ''
export function matches(term: Term, query: string, category: string, status: string) {
  const text = [term.source, term.category, term.custom, ...term.aliases, ...term.meaning_notes.map(n => n.text), ...term.candidates.map(c => c.text)].join(' ').normalize().toLowerCase()
  return text.includes(query.normalize().toLowerCase()) && (category === 'All' || term.category === category) && (status === 'All' || (status === 'Reviewed' ? term.reviewed : !term.reviewed))
}
export function ReviewPage() {
  const { workspaceId: id = '' } = useParams({ strict: false })
  const scope = useContext(Scope)
  const all = useApi('/api/workspaces', workspacesSchema)
  const workspace = all.data?.workspaces.find(w => w.workspace_id === id)
  const pipeline = useApi(endpoint(id, 'pipeline'), pipelineSchema, !!workspace?.prepared)
  const query = useApi(endpoint(id, 'review'), reviewSchema, pipeline.data?.review.prepared === true)
  const command = useCommand(id)
  const navigate = useNavigate()
  const search = useSearch({ strict: false })
  const status = search.status ?? 'All', category = search.category ?? 'All'
  const [searchInput, setSearchInput] = useState(search.q ?? '')
  const [draft, setDraft] = useState<{ id: string; value: string; revision: string } | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)
  const composing = useRef(false)
  const saving = useRef<Promise<boolean> | null>(null)
  const setSearch = (values: Partial<typeof search>) => void navigate({ to: '.', search: old => ({ ...old, ...values }), replace: true })
  useEffect(() => {
    if (composing.current || searchInput === (search.q ?? '')) return
    const timer = setTimeout(() => { void navigate({ to: '.', search: old => ({ ...old, q: searchInput }), replace: true }) }, 150)
    return () => clearTimeout(timer)
  }, [searchInput, navigate, search.q])
  const terms = query.data?.terms ?? []
  const filtered = terms.filter(t => matches(t, search.q ?? '', category, status))
  const selected = (draft ? terms.find(t => t.id === draft.id) : undefined) ?? filtered.find(t => t.id === search.term) ?? filtered[0]
  const evidence = useApi(endpoint(id, `review/terms/${encodeURIComponent(selected?.id ?? '')}/evidence`), evidenceSchema, !!selected)
  const dirty = !!draft && draft.value !== (terms.find(t => t.id === draft.id)?.custom ?? '')
  const blocker = useBlocker({ shouldBlockFn: ({ current, next }) => (dirty || command.pending) && (current.pathname !== next.pathname || current.search.term !== next.search.term), enableBeforeUnload: dirty, withResolver: true })
  const bodyRef = useRef<HTMLDivElement>(null)
  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = 0 }, [selected?.id])
  const currentReview = () => queryClient.getQueryData<Review>([scope, endpoint(id, 'review')]) ?? query.data
  async function patch(termId: string, body: Record<string, unknown>, revision?: string) {
    const current = currentReview()
    if (!current) return false
    const result = await command.send(endpoint(id, `review/terms/${encodeURIComponent(termId)}`), patchSchema, { revision: revision ?? current._revision, ...body }, 'PATCH')
    return !!result
  }
  async function flush(force = false): Promise<boolean> {
    if (saving.current) return saving.current
    if (!dirty || !draft) return true
    const current = currentReview()
    if (!force && draft.revision !== current?._revision) { return false }
    const captured = draft
    const work = patch(captured.id, { custom: captured.value.trim() }, force ? current?._revision : captured.revision).then(ok => { if (ok) { setDraft(null); setLocalError(null) } return ok })
    saving.current = work
    try { return await work } finally { saving.current = null }
  }
  async function next(review: boolean, direction = 1) {
    if (!selected) return
    const index = filtered.findIndex(t => t.id === selected.id)
    const target = filtered[index + direction]?.id
    if (!await flush()) return
    if (review && !await patch(selected.id, { reviewed: true })) return
    if (target) setSearch({ term: target })
  }
  const p = pipeline.data
  const reviewed = terms.filter(t => t.reviewed).length
  const uncertain = terms.filter(t => [...t.meaning_notes, ...t.candidates].some(x => x.confidence === 'low' || x.confidence === 'medium')).length
  const disabled = command.disabled || !!p?.busy || !!p?.metadata.lifecycle.archived
  return <main className="main review-page" {...debugTag('REV', id)}><Back id={id} /><div className="review-page-head" {...debugTag('RVH')}><div className="review-page-title"><div><div className="eyebrow">Review · {p?.metadata.title ?? 'Workspace'}</div><h1>Terminology Review</h1><div className="review-page-meta">{query.data ? `${reviewed}/${terms.length} reviewed · ${terms.length - reviewed} remaining · ${uncertain} uncertain` : 'Load the terminology draft to review its choices.'}</div></div></div><div className="review-heading-actions">{p?.approved && <span className="review-confirmed-badge">✓ Glossary confirmed</span>}<Button variant="primary" disabled={disabled || !query.data || reviewed !== terms.length || dirty || !p?.review.current} onClick={async () => { const current = currentReview(); if (current) await command.send(endpoint(id, 'review/confirm-and-approve'), approvalSchema, { revision: current._revision }) }}>{p?.approved ? 'Reconfirm glossary' : 'Confirm glossary'}</Button></div></div>
    {p?.approved && <div className="review-warning">This review remains available after confirmation. Editing terminology requires renewed approval before translation continues. Existing translation checkpoints are retained.</div>}
    <ErrorNote error={all.error ?? pipeline.error ?? query.error ?? command.error ?? (localError ? new Error(localError) : null)} retry={() => void query.refetch()} />
    {dirty && draft?.revision !== query.data?._revision && <div className="notice"><span>Review changed elsewhere. Your custom form is preserved.</span><Button disabled={disabled} onClick={() => void flush(true)}>Reapply my form</Button><Button onClick={() => { setDraft(null); setLocalError(null) }}>Discard my form</Button></div>}
    {!p?.analysis.complete ? <Empty>{workspace && !workspace.prepared ? 'Prepare this workspace before Review.' : 'Complete whole-book analysis before Review.'}</Empty> : !p.review.prepared ? <Empty><p>The terminology draft is not prepared.</p><Button disabled={disabled} onClick={() => void command.send(endpoint(id,'review/prepare'), reviewSchema, {})}>Prepare review</Button></Empty> : <>
      <div className="review-toolbar" {...debugTag('RVF')}><input className="review-search" aria-label="Search terminology" placeholder="Search source, alias, translation, meaning…" value={searchInput} onCompositionStart={() => { composing.current = true }} onCompositionEnd={e => { composing.current = false; setSearchInput(e.currentTarget.value) }} onChange={e => setSearchInput(e.target.value)} /><div className="review-filter-row">{['All','Unreviewed','Reviewed'].map(value => <button className={`review-chip ${status === value ? 'active' : ''}`} key={value} onClick={async () => { if (await flush()) setSearch({ status: value as typeof status }) }}>{value}</button>)}</div></div>
      <div className="review-filter-row categories" {...debugTag('RVC')}>{['All', ...new Set(terms.map(t => t.category))].map(value => <button className={`review-chip ${category === value ? 'active' : ''}`} key={value} onClick={async () => { if (await flush()) setSearch({ category: value }) }}>{value} {value === 'All' ? terms.length : terms.filter(t => t.category === value).length}</button>)}</div>
      <div className="review-layout" {...debugTag('RVL')}><div className="review-term-card" {...debugTag('RFL')}><div className="review-term-scroll">{filtered.map(term => <button key={term.id} className={`review-term-row ${term.id === selected?.id ? 'active' : ''}`} onClick={async () => { if (await flush()) setSearch({ term: term.id }) }}><span className="review-term-title"><span>{term.source}</span><span className={`review-term-status ${term.reviewed ? 'reviewed' : ''}`}>{term.reviewed ? '✓ reviewed' : '○ pending'}</span></span><span className="review-term-sub">{term.category} · {effective(term)}</span></button>)}{!filtered.length && <Empty>{query.isPending ? 'Loading terminology…' : 'No terms match the current filters.'}</Empty>}<div className="review-term-list-tail" /></div></div>
      <div className="review-detail-wrap">{selected ? <div className="review-detail-card" {...debugTag('RVD', selected.id)}><div className="review-detail-body" ref={bodyRef}><div className="review-detail-heading"><div><h2>{selected.source}</h2><div className="review-id">{selected.id}</div></div><div className="review-tags"><span className="review-tag">{selected.category}</span></div></div>
        <div className="review-block"><div className="review-label">Meaning</div><div className="review-copy">{selected.meaning_notes.map((n,i) => <p key={i}>{n.text}{n.confidence ? ` · ${n.confidence}` : ''}</p>)}{selected.observations.map((n,i) => <p key={`observation-${i}`}>{n.statement}</p>)}{!selected.meaning_notes.length && !selected.observations.length && '—'}</div></div><div className="review-block"><div className="review-label">Aliases</div><div className="review-copy">{selected.aliases.join(' · ') || '—'}</div></div>
        <div className="review-block"><div className="review-label">Translation</div><div className="candidate-list">{selected.candidates.map(candidate => <button key={candidate.number} className={`candidate ${selected.select === candidate.number && !selected.custom ? 'selected' : ''}`} disabled={disabled} onClick={async () => { if (await patch(selected.id, { select: candidate.number, custom: '' })) setDraft(null) }}><span className="candidate-dot" /><span><span className="candidate-title">{candidate.text}</span><span className="candidate-note">{candidate.reasons?.join(' · ') ?? candidate.reason ?? ''}</span></span><span className="candidate-confidence">{candidate.confidence ?? '—'}</span></button>)}</div><div className="review-custom-row"><input aria-label="Custom Polish form" placeholder="Custom Polish form" disabled={disabled} value={draft?.id === selected.id ? draft.value : selected.custom} onChange={e => setDraft({ id: selected.id, value: e.target.value, revision: draft?.id === selected.id ? draft.revision : query.data!._revision })} onBlur={() => void flush()} onKeyDown={e => { if (e.key === 'Enter') void flush() }} /><Button disabled={disabled} onClick={async () => { if (await patch(selected.id, { custom: selected.source })) setDraft(null) }}>Keep source</Button><Button disabled={disabled || !selected.candidates.some(c => c.number === selected.select)} onClick={async () => { if (await patch(selected.id, { custom: '' })) setDraft(null) }}>Use candidate</Button></div></div>
        <div className="review-block" {...debugTag('RVE')}><div className="review-label">Evidence {evidence.data?.entries.length ?? '—'}</div><ErrorNote error={evidence.error} /><div className="evidence-list">{evidence.data?.entries.map((entry,i) => <div className="evidence-item" key={`${entry.block_id}-${i}`}><div className="evidence-id">{entry.chapter_id} · {entry.block_id}</div><div className="evidence-text">{entry.source_text ?? entry.message ?? 'Source evidence unavailable.'}</div>{entry.polish_text && <p className="evidence-text">{entry.polish_text}</p>}</div>)}{evidence.isPending ? <Empty>Loading evidence…</Empty> : evidence.data?.entries.length === 0 && <Empty>No evidence available.</Empty>}{evidence.data?.warnings.map((w,i) => <p className="subtitle" key={i}>{w}</p>)}</div></div>
      </div><div className="review-detail-footer" {...debugTag('RVM')}><div className={`review-term-status ${selected.reviewed ? 'reviewed' : ''}`}>{selected.reviewed ? 'Reviewed' : 'Not reviewed'}</div><div className="review-footer-actions"><Button id="reviewPrevBtn" disabled={disabled || filtered[0]?.id === selected.id} onClick={() => void next(false,-1)}>← Previous</Button><Button id="reviewNextBtn" variant="primary" disabled={disabled || (selected.reviewed && filtered.at(-1)?.id === selected.id)} onClick={() => void next(!selected.reviewed)}>{selected.reviewed ? 'Next →' : 'Review & next'}</Button><Button id="reviewOnlyNextBtn" disabled={disabled || filtered.at(-1)?.id === selected.id} onClick={() => void next(false)}>Next →</Button></div></div></div> : <Empty>Select a terminology item.</Empty>}</div></div>
    </>}
    {blocker.status === 'blocked' && <Overlay title="Keep your unsaved form?" debugId="RUC" compact close={() => blocker.reset()}><div className="modal-body">Your custom form has not been saved. Stay here to save it, or discard it before leaving.</div><div className="modal-foot"><Button onClick={() => blocker.reset()}>Stay</Button><Button variant="danger" disabled={command.pending} onClick={() => { setDraft(null); blocker.proceed() }}>Discard and leave</Button></div></Overlay>}
  </main>
}
