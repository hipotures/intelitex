import { useContext, useEffect, useRef, useState } from 'react'
import { useBlocker, useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { endpoint, queryClient, Scope, useApi } from '../../api/client'
import { approvalSchema, bulkReviewSchema, evidenceSchema, patchSchema, pipelineSchema, reviewSchema, workspacesSchema, type Review, type Term } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { Back, Button, Empty, ErrorNote, Overlay } from '../../components/ui/common'
import { debugTag } from '../../debug/regions'
import { reviewCountLabel, reviewCounts, reviewStatus, reviewStatusLabel } from './reviewStatus'
export const effective = (term: Term) => term.custom.trim() || term.candidates.find(c => c.number === term.select)?.text || ''
export function matches(term: Term, query: string, category: string, status: string) {
  const text = [term.source, term.custom, term.user_notes, ...term.aliases,
    ...term.meaning_notes.map(n => n.text), ...term.observations.map(o => o.statement),
    ...term.candidates.map(c => c.text)].join(' ').normalize().toLowerCase()
  const disposition = reviewStatus(term)
  const matchesStatus = status === 'All' || (['Pending', 'Unreviewed'].includes(status) && disposition === 'pending')
    || (['Accepted', 'Reviewed'].includes(status) && disposition !== 'pending')
    || (status === 'Individually reviewed' && disposition === 'individual')
    || (status === 'Bulk accepted' && disposition === 'bulk')
  return text.includes(query.trim().normalize().toLowerCase()) && (category === 'All' || term.category === category) && matchesStatus
}
export function nextUnreviewed(terms: Term[], currentId: string, query: string, category: string): string | undefined {
  const scoped = terms.filter(term => matches(term, query, category, 'All'))
  const index = scoped.findIndex(term => term.id === currentId)
  if (index < 0) return undefined
  for (let step = 1; step <= scoped.length; step++) {
    const term = scoped[(index + step) % scoped.length]
    if (term && !term.reviewed) return term.id
  }
  return undefined
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
  const status = search.status === 'Reviewed' ? 'Accepted' : search.status === 'Unreviewed' ? 'Pending' : search.status ?? 'All'
  const category = search.category ?? 'All'
  const [searchInput, setSearchInput] = useState(search.q ?? '')
  const [draft, setDraft] = useState<{ id: string; value: string; revision: string } | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)
  const [moving, setMoving] = useState<'previous' | 'primary' | 'plain' | null>(null)
  const [reviewNotice, setReviewNotice] = useState<string | null>(null)
  const [bulkPending, setBulkPending] = useState<{ ids: string[]; revision: string; scope: string } | null>(null)
  const [bulkUnknown, setBulkUnknown] = useState(false)
  const [approvalPending, setApprovalPending] = useState(false)
  const composing = useRef(false)
  const saving = useRef<Promise<boolean> | null>(null)
  const movingRef = useRef(false)
  const setSearch = (values: Partial<typeof search>, savedNavigation = false) => void navigate({ to: '.', search: old => ({ ...old, ...values }), replace: true, ignoreBlocker: savedNavigation })
  useEffect(() => {
    if (composing.current || searchInput === (search.q ?? '')) return
    const timer = setTimeout(() => { void navigate({ to: '.', search: old => ({ ...old, q: searchInput }), replace: true }) }, 150)
    return () => clearTimeout(timer)
  }, [searchInput, navigate, search.q])
  const terms = query.data?.terms ?? []
  const filtered = terms.filter(t => matches(t, search.q ?? '', category, status))
  const selected = (draft ? terms.find(t => t.id === draft.id) : undefined) ?? filtered.find(t => t.id === search.term)
    ?? (status === 'Pending' && !filtered.length ? terms.find(t => t.id === search.term) : undefined) ?? filtered[0]
  const evidence = useApi(endpoint(id, `review/terms/${encodeURIComponent(selected?.id ?? '')}/evidence`), evidenceSchema, !!selected)
  const dirty = !!draft && draft.value !== (terms.find(t => t.id === draft.id)?.custom ?? '')
  const blocker = useBlocker({ shouldBlockFn: ({ current, next }) => dirty && (current.pathname !== next.pathname || current.search.term !== next.search.term), enableBeforeUnload: dirty, withResolver: true })
  const bodyRef = useRef<HTMLDivElement>(null)
  useEffect(() => { if (bodyRef.current) bodyRef.current.scrollTop = 0 }, [selected?.id])
  const currentReview = () => queryClient.getQueryData<Review>([scope, endpoint(id, 'review')]) ?? query.data
  async function patch(termId: string, body: Record<string, unknown>, revision?: string) {
    const current = currentReview()
    if (!current) return false
    const sentRevision = revision ?? current._revision
    const result = await command.send(endpoint(id, `review/terms/${encodeURIComponent(termId)}`), patchSchema, { revision: sentRevision, ...body }, 'PATCH', true)
    const reviewKey = [scope, endpoint(id, 'review')]
    if (!result) {
      void queryClient.invalidateQueries({ queryKey: reviewKey })
      return false
    }
    const cached = queryClient.getQueryData<Review>(reviewKey)
    if (cached?._revision === sentRevision) {
      queryClient.setQueryData<Review>(reviewKey, { ...cached, _revision: result.revision,
        confirmed: result.summary.confirmed, terms: cached.terms.map(term => term.id === termId ? result.term : term) })
    } else {
      void queryClient.invalidateQueries({ queryKey: reviewKey })
    }
    void queryClient.invalidateQueries({ queryKey: [scope, endpoint(id, 'pipeline')] })
    void queryClient.invalidateQueries({ queryKey: [scope, '/api/workspaces'], refetchType: 'none' })
    return true
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
  async function openBulk() {
    if (!await flush()) return
    const current = currentReview()
    if (!current) return
    const ids = current.terms.filter(term => matches(term, search.q ?? '', category, status) && !term.reviewed).map(term => term.id)
    if (!ids.length) return
    command.clearError()
    setBulkUnknown(false)
    setBulkPending({ ids, revision: current._revision,
      scope: `${status} / ${category === 'All' ? 'All categories' : category}${search.q ? ` / search: ${search.q}` : ''}` })
  }
  async function confirmBulk() {
    if (!bulkPending || command.disabled) return
    const result = await command.send(endpoint(id, 'review/bulk-review'), bulkReviewSchema,
      { revision: bulkPending.revision, term_ids: bulkPending.ids }, 'POST', true)
    const reviewKey = [scope, endpoint(id, 'review')]
    if (!result) {
      if (command.unknownOutcome()) setBulkUnknown(true)
      void queryClient.invalidateQueries({ queryKey: reviewKey })
      return
    }
    const cached = queryClient.getQueryData<Review>(reviewKey)
    if (cached?._revision === bulkPending.revision) {
      const changed = new Map(result.terms.map(term => [term.id, term]))
      queryClient.setQueryData<Review>(reviewKey, { ...cached, _revision: result.revision,
        confirmed: result.summary.confirmed, terms: cached.terms.map(term => changed.get(term.id) ?? term) })
    } else {
      void queryClient.invalidateQueries({ queryKey: reviewKey })
    }
    void queryClient.invalidateQueries({ queryKey: [scope, endpoint(id, 'pipeline')] })
    void queryClient.invalidateQueries({ queryKey: [scope, '/api/workspaces'], refetchType: 'none' })
    setReviewNotice(`${result.changed_count} ${result.changed_count === 1 ? 'term' : 'terms'} bulk accepted. Current choices and notes kept.`)
    setBulkPending(null)
  }
  async function next(review: boolean, direction = 1, action: 'previous' | 'primary' | 'plain' = 'primary') {
    if (!selected || movingRef.current) return
    movingRef.current = true
    setMoving(action)
    try {
      const index = filtered.findIndex(t => t.id === selected.id)
      const target = filtered[index + direction]?.id
      if (!await flush()) return
      if (review) {
        if (!await patch(selected.id, { reviewed: true })) return
        const nextId = nextUnreviewed(currentReview()?.terms ?? [], selected.id, search.q ?? '', category)
        if (nextId) {
          setReviewNotice(null)
          setSearch({ term: nextId, ...(['Accepted', 'Bulk accepted', 'Individually reviewed'].includes(status) ? { status: 'Pending' } : {}) }, true)
        } else {
          setReviewNotice('All matching terms accepted. Review methods remain visible for each term.')
          if (status === 'Pending') setSearch({ term: selected.id }, true)
        }
        return
      }
      if (target) { setReviewNotice(null); setSearch({ term: target }, true) }
    } finally { movingRef.current = false; setMoving(null) }
  }
  const p = pipeline.data
  const counts = reviewCounts(terms)
  const uncertain = terms.filter(t => [...t.meaning_notes, ...t.candidates].some(x => x.confidence === 'low' || x.confidence === 'medium')).length
  const disabled = command.disabled || !!moving || !!p?.busy || !!p?.metadata.lifecycle.archived
  const remaining = filtered.filter(term => !term.reviewed).length
  const reviewScope = terms.filter(term => matches(term, search.q ?? '', category, status === 'Pending' ? 'All' : status))
  const scopeComplete = status === 'Pending' && !filtered.length && reviewScope.length > 0 && reviewScope.every(term => term.reviewed)
  const nextPending = selected ? nextUnreviewed(terms, selected.id, search.q ?? '', category) : undefined
  return <main className="main review-page" {...debugTag('REV', id)}><Back id={id} /><div className="review-page-head" {...debugTag('RVH')}><div className="review-page-title"><div><div className="eyebrow">Review · {p?.metadata.title ?? 'Workspace'}</div><h1>Terminology Review</h1><div className="review-page-meta">{query.data ? `${reviewCountLabel(terms)} · ${uncertain} uncertain` : 'Load the terminology draft to review its choices.'}</div></div></div><div className="review-heading-actions">{p?.approved && <span className="review-confirmed-badge">✓ Glossary approved</span>}<Button variant="primary" disabled={disabled || !query.data || counts.pending !== 0 || dirty || !p?.review.current} onClick={() => { command.clearError(); setApprovalPending(true) }}>{p?.approved ? 'Reapprove glossary' : 'Approve glossary'}</Button></div></div>
    {p?.approved && <div className="review-warning">This review remains available after confirmation. Editing terminology requires renewed approval before translation continues. Existing translation checkpoints are retained.</div>}
    <ErrorNote error={all.error ?? pipeline.error ?? query.error ?? command.error ?? (localError ? new Error(localError) : null)} retry={() => void query.refetch()} />
    {dirty && draft?.revision !== query.data?._revision && <div className="notice"><span>Review changed elsewhere. Your custom form is preserved.</span><Button disabled={disabled} onClick={() => void flush(true)}>Reapply my form</Button><Button onClick={() => { setDraft(null); setLocalError(null) }}>Discard my form</Button></div>}
    {!p?.analysis.complete ? <Empty>{workspace && !workspace.prepared ? 'Prepare this workspace before Review.' : 'Complete whole-book analysis before Review.'}</Empty> : !p.review.prepared ? <Empty><p>The terminology draft is not prepared.</p><Button disabled={disabled} onClick={() => void command.send(endpoint(id,'review/prepare'), reviewSchema, {})}>Prepare review</Button></Empty> : <>
      <div className="review-toolbar" {...debugTag('RVF')}><input className="review-search" aria-label="Search terminology" placeholder="Search source, alias, translation, meaning…" value={searchInput} onCompositionStart={() => { composing.current = true }} onCompositionEnd={e => { composing.current = false; setSearchInput(e.currentTarget.value) }} onChange={e => setSearchInput(e.target.value)} /><div className="review-filter-row">{['All','Pending','Accepted','Individually reviewed','Bulk accepted'].map(value => <button className={`review-chip ${status === value ? 'active' : ''}`} key={value} disabled={disabled} onClick={async () => { if (await flush()) { setReviewNotice(null); setSearch({ status: value }, true) } }}>{value}</button>)}</div></div>
      <div className="review-filter-row categories" {...debugTag('RVC')}>{['All', ...new Set(terms.map(t => t.category))].map(value => {
        const visible = terms.filter(term => matches(term, search.q ?? '', value, status))
        const base = status === 'Pending' ? terms.filter(term => matches(term, search.q ?? '', value, 'All')) : visible
        const complete = base.length > 0 && base.every(term => term.reviewed)
        return <button className={`review-chip ${category === value ? 'active' : ''}${complete ? ' complete' : ''}`} key={value} disabled={disabled} aria-pressed={category === value} title={`${visible.length} matching terms; ${base.filter(term => !term.reviewed).length} remaining.`} onClick={async () => { if (await flush()) { setReviewNotice(null); setSearch({ category: value }, true) } }}>{complete ? '✓ ' : ''}{value} {visible.length}</button>
      })}</div>
      <div className="review-bulk-bar" {...debugTag('RVB')}><div><span>This view: {reviewCountLabel(filtered)}.</span>{reviewNotice && <span className="review-bulk-notice" role="status">{reviewNotice}</span>}</div><Button disabled={disabled || remaining === 0} onClick={() => void openBulk()}>Bulk accept remaining in this view ({remaining})</Button></div>
      <div className="review-layout" {...debugTag('RVL')}><div className="review-term-card" {...debugTag('RFL')}><div className="review-term-scroll">{filtered.map(term => <button key={term.id} className={`review-term-row ${term.id === selected?.id ? 'active' : ''}`} disabled={disabled} onClick={async () => { if (await flush()) { setReviewNotice(null); setSearch({ term: term.id }, true) } }}><span className="review-term-title"><span>{term.source}</span><span className={`review-term-status status-${reviewStatus(term)}`}>{reviewStatusLabel(term)}</span></span><span className="review-term-sub">{term.category} · {effective(term)}</span></button>)}{!filtered.length && <Empty>{query.isPending ? 'Loading terminology…' : scopeComplete ? 'All terms in this scope are accepted.' : 'No terms match the current filters.'}</Empty>}<div className="review-term-list-tail" /></div></div>
      <div className="review-detail-wrap">{selected ? <div className="review-detail-card" {...debugTag('RVD', selected.id)}><div className="review-detail-body" ref={bodyRef}><div className="review-detail-heading"><div><h2>{selected.source}</h2><div className="review-id">{selected.id}</div></div><div className="review-tags"><span className="review-tag">{selected.category}</span></div></div>
        <div className="review-block"><div className="review-label">Meaning</div><div className="review-copy">{selected.meaning_notes.map((n,i) => <p key={i}>{n.text}{n.confidence ? ` · ${n.confidence}` : ''}</p>)}{selected.observations.map((n,i) => <p key={`observation-${i}`}>{n.statement}</p>)}{!selected.meaning_notes.length && !selected.observations.length && '—'}</div></div><div className="review-block"><div className="review-label">Aliases</div><div className="review-copy">{selected.aliases.join(' · ') || '—'}</div></div>
        <div className="review-block"><div className="review-label">Translation</div><div className="candidate-list">{selected.candidates.map(candidate => <button key={candidate.number} className={`candidate ${selected.select === candidate.number && !selected.custom ? 'selected' : ''}`} disabled={disabled} onClick={async () => { if (await patch(selected.id, { select: candidate.number, custom: '' })) setDraft(null) }}><span className="candidate-dot" /><span><span className="candidate-title">{candidate.text}</span><span className="candidate-note">{candidate.reasons?.join(' · ') ?? candidate.reason ?? ''}</span></span><span className="candidate-confidence">{candidate.confidence ?? '—'}</span></button>)}</div><div className="review-custom-row"><input aria-label="Custom Polish form" placeholder="Custom Polish form" disabled={disabled} value={draft?.id === selected.id ? draft.value : selected.custom} onChange={e => setDraft({ id: selected.id, value: e.target.value, revision: draft?.id === selected.id ? draft.revision : query.data!._revision })} onBlur={e => { if (!(e.relatedTarget instanceof Element) || !e.relatedTarget.closest('button, a')) void flush() }} onKeyDown={e => { if (e.key === 'Enter') void flush() }} /><Button disabled={disabled} onClick={async () => { if (await patch(selected.id, { custom: selected.source })) setDraft(null) }}>Keep source</Button><Button disabled={disabled || !selected.candidates.some(c => c.number === selected.select)} onClick={async () => { if (await patch(selected.id, { custom: '' })) setDraft(null) }}>Use candidate</Button></div></div>
        <div className="review-block" {...debugTag('RVE')}><div className="review-label">Evidence {evidence.data?.entries.length ?? '—'}</div><ErrorNote error={evidence.error} /><div className="evidence-list">{evidence.data?.entries.map((entry,i) => <div className="evidence-item" key={`${entry.block_id}-${i}`}><div className="evidence-id">{entry.chapter_id} · {entry.block_id}</div><div className="evidence-text">{entry.source_text ?? entry.message ?? 'Source evidence unavailable.'}</div>{entry.polish_text && <p className="evidence-text">{entry.polish_text}</p>}</div>)}{evidence.isPending ? <Empty>Loading evidence…</Empty> : evidence.data?.entries.length === 0 && <Empty>No evidence available.</Empty>}{evidence.data?.warnings.map((w,i) => <p className="subtitle" key={i}>{w}</p>)}</div></div>
      </div><div className="review-detail-footer" {...debugTag('RVM')}><div className={`review-term-status status-${reviewStatus(selected)}`}>{reviewStatusLabel(selected)}</div><div className="review-footer-actions"><Button id="reviewPrevBtn" disabled={disabled || filtered[0]?.id === selected.id} onClick={() => void next(false,-1,'previous')}>{moving === 'previous' ? 'Saving…' : '← Previous'}</Button><Button id="reviewNextBtn" variant="primary" disabled={disabled || (reviewStatus(selected) === 'individual' && !dirty && !nextPending)} onClick={() => void next(true)}>{moving === 'primary' ? 'Saving…' : 'Review individually & next'}</Button><Button id="reviewOnlyNextBtn" disabled={disabled || filtered.at(-1)?.id === selected.id} onClick={() => void next(false,1,'plain')}>{moving === 'plain' ? 'Saving…' : 'Next →'}</Button></div></div></div> : <Empty>Select a terminology item.</Empty>}</div></div>
    </>}
    {bulkPending && <Overlay title="Bulk accept remaining terms?" debugId="RBM" compact close={() => { if (!command.pending) setBulkPending(null) }}><div className="modal-body"><p>Accept {bulkPending.ids.length} remaining {bulkPending.ids.length === 1 ? 'term' : 'terms'} in this view without individual review?</p><p className="subtitle">{bulkPending.scope}</p><p className="subtitle">Current candidate or custom forms and notes will be kept. Each term will be marked bulk accepted. This does not approve the glossary or call a model.</p>{bulkUnknown && <p className="subtitle">The outcome is unknown. Reload the current Review state before another attempt.</p>}<ErrorNote error={command.error} /></div><div className="modal-foot"><Button disabled={command.pending} onClick={() => setBulkPending(null)}>Cancel</Button><Button variant="primary" disabled={disabled || bulkUnknown} onClick={() => void confirmBulk()}>Bulk accept {bulkPending.ids.length} {bulkPending.ids.length === 1 ? 'term' : 'terms'}</Button></div></Overlay>}
    {approvalPending && <Overlay title="Approve glossary?" debugId="RAM" compact close={() => { if (!command.pending) setApprovalPending(false) }}><div className="modal-body"><p>This commits the selected glossary forms.</p><p>{reviewCountLabel(terms)}.</p><p className="subtitle">Bulk acceptance does not count as individual review. Approval will preserve each term’s review method.</p><ErrorNote error={command.error} /></div><div className="modal-foot"><Button disabled={command.pending} onClick={() => setApprovalPending(false)}>Cancel</Button><Button variant="primary" disabled={disabled || counts.pending !== 0 || dirty} onClick={async () => { const current = currentReview(); if (!current) return; const result = await command.send(endpoint(id, 'review/confirm-and-approve'), approvalSchema, { revision: current._revision }); if (result) setApprovalPending(false) }}>Approve selected forms</Button></div></Overlay>}
    {blocker.status === 'blocked' && <Overlay title="Keep your unsaved form?" debugId="RUC" compact close={() => blocker.reset()}><div className="modal-body">Your custom form has not been saved. Stay here to save it, or discard it before leaving.</div><div className="modal-foot"><Button onClick={() => blocker.reset()}>Stay</Button><Button variant="danger" disabled={command.pending} onClick={() => { setDraft(null); blocker.proceed() }}>Discard and leave</Button></div></Overlay>}
  </main>
}
