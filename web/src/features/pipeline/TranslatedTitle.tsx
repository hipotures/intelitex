import { useContext, useState } from 'react'
import { z } from 'zod'
import { endpoint, queryClient, reconcile, request, Scope } from '../../api/client'
import type { Pipeline } from '../../api/schema'
import { Button, ErrorNote, Panel } from '../../components/ui/common'

const savedSchema = z.object({ title: z.string().nullable() })

/** Hand-entered title of the translation. The Reader shows it instead of the source title. */
export function TranslatedTitle({ id, metadata, disabled }: { id: string; metadata: Pipeline['metadata']; disabled: boolean }) {
  const scope = useContext(Scope)
  const saved = metadata.translated_title ?? ''
  const [draft, setDraft] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const value = draft ?? saved
  const changed = value.trim() !== saved
  async function save(title: string | null) {
    if (saving || disabled) return
    setSaving(true); setError(null)
    try {
      await request(endpoint(id, 'translated-title'), savedSchema, { method: 'PATCH', body: { title } })
      // The Reader keeps its own copy of the book metadata; drop it so it shows the new title.
      queryClient.removeQueries({ predicate: q => q.queryKey[0] === scope && String(q.queryKey[1]).startsWith(endpoint(id, 'reader')) })
      await reconcile(id)
      setDraft(null)
    } catch (failure) { setError(failure) }
    finally { setSaving(false) }
  }
  return <Panel debugId="PTT" title="Translated title">
    <form className="translated-title-form" onSubmit={event => { event.preventDefault(); void save(value.trim() || null) }}>
      <p className="subtitle">Title of the book in the target language. The Reader shows it instead of the source title; the translation itself is not changed. Leave empty to keep the source title.</p>
      <label htmlFor="translated-title">Title</label>
      <input id="translated-title" value={value} maxLength={200} placeholder={metadata.title} disabled={saving || disabled} onChange={event => setDraft(event.target.value)} />
      <ErrorNote error={error} />
      <div className="translated-title-actions">
        <Button type="submit" disabled={saving || disabled || !changed}>Save title</Button>
        {saved && <Button type="button" disabled={saving || disabled} onClick={() => void save(null)}>Clear</Button>}
      </div>
    </form>
  </Panel>
}
