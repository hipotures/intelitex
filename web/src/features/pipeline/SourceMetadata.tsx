import { useContext, useState } from 'react'
import { endpoint, queryClient, reconcile, request, Scope, useApi } from '../../api/client'
import { bookMetadataSchema, type Pipeline } from '../../api/schema'
import { Button, ErrorNote, Overlay, Panel } from '../../components/ui/common'

export function SourceMetadata({ id, metadata, disabled }: { id: string; metadata: Pipeline['metadata']; disabled: boolean }) {
  const [open, setOpen] = useState(false)
  return <Panel debugId="PSM" title="Source metadata">
    <dl className="phase-kv"><dt>Title</dt><dd>{metadata.title}</dd><dt>Author</dt><dd>{metadata.creators.join(', ') || 'Not recorded'}</dd><dt>Source language</dt><dd>{metadata.language ?? 'Not recorded'}</dd><dt>Book ID</dt><dd className="book-identity">{metadata.book_id ?? 'Not recorded'}</dd></dl>
    <Button disabled={disabled} onClick={() => setOpen(true)}>Edit metadata / original</Button>
    {open && <MetadataEditor id={id} disabled={disabled} close={() => setOpen(false)} />}
  </Panel>
}

function MetadataEditor({ id, disabled, close }: { id: string; disabled: boolean; close: () => void }) {
  const scope = useContext(Scope)
  const path = endpoint(id, 'metadata')
  const snapshot = useApi(path, bookMetadataSchema)
  const [baseline, setBaseline] = useState<NonNullable<typeof snapshot.data> | null>(null)
  const [draft, setDraft] = useState<{ title: string; creators: string; language: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const current = baseline ?? snapshot.data
  const form = draft ?? (current ? { title: current.effective.title, creators: current.effective.creators.join('\n'), language: current.effective.language ?? '' } : null)
  async function save() {
    if (!current || !form || saving || disabled) return
    setSaving(true); setError(null)
    try {
      const creators = form.creators.split('\n').map(value => value.trim()).filter(Boolean)
      const value = await request(path, bookMetadataSchema, { method: 'PATCH', body: { revision: current.revision, corrections: {
        title: form.title.trim() === current.original.title ? null : form.title,
        creators: JSON.stringify(creators) === JSON.stringify(current.original.creators) ? null : creators,
        language: form.language === (current.original.language ?? '') ? null : form.language,
      } } })
      queryClient.setQueryData([scope, path], value)
      // Library is lazily fetched; discard inactive pages so its next visit also re-sorts corrections.
      queryClient.removeQueries({ predicate: q => q.queryKey[0] === scope && String(q.queryKey[1]).startsWith('/api/library') })
      await reconcile()
      close()
    } catch (failure) { setError(failure) }
    finally { setSaving(false) }
  }
  return <Overlay title="Book metadata" close={() => { if (!saving) close() }}>
    <div className="book-metadata-editor">
      <p className="subtitle">Corrections apply to every workspace of this book, Library, Reader and the next Publish. Original values are kept; only the latest corrections are stored. Translation checkpoints stay valid.</p>
      <ErrorNote error={snapshot.error} retry={() => void snapshot.refetch()} />
      {!current || !form ? <p role="status">Loading metadata…</p> : <form onSubmit={event => { event.preventDefault(); void save() }}>
        <p className="book-identity">Book ID: {current.book_id}</p>
        {(['title', 'creators', 'language'] as const).map(key => {
          const label = { title: 'Title', creators: 'Authors (one per line)', language: 'Source language' }[key]
          const original = key === 'creators' ? current.original.creators.join('\n') : current.original[key] ?? ''
          return <div className="book-metadata-field" key={key}>
            <label htmlFor={`book-metadata-${key}`}>{label}</label>
            {key === 'creators' ? <textarea id={`book-metadata-${key}`} rows={3} value={form[key]} disabled={saving || disabled} onChange={event => { setBaseline(current); setDraft({ ...form, [key]: event.target.value }) }} />
              : <input id={`book-metadata-${key}`} value={form[key]} required={key === 'title'} maxLength={key === 'title' ? 500 : 50} placeholder={key === 'language' ? 'e.g. en' : undefined} disabled={saving || disabled} onChange={event => { setBaseline(current); setDraft({ ...form, [key]: event.target.value }) }} />}
            <div className="book-metadata-original"><span>Original: {original || 'Not recorded'}</span><Button type="button" disabled={saving || disabled} onClick={() => { setBaseline(current); setDraft({ ...form, [key]: original }) }}>Restore original</Button></div>
          </div>
        })}
        {current.updated_at && <p className="subtitle">Last correction: {new Date(current.updated_at).toLocaleString()}</p>}
        <ErrorNote error={error} />
        {error != null && <Button type="button" disabled={saving} onClick={() => { setDraft(null); setBaseline(null); setError(null); void snapshot.refetch() }}>Reload saved values</Button>}
        <div className="book-metadata-actions"><Button type="button" disabled={saving} onClick={close}>Cancel</Button><Button type="submit" variant="primary" disabled={saving || disabled}>{saving ? 'Saving…' : 'Save metadata'}</Button></div>
      </form>}
    </div>
  </Overlay>
}
