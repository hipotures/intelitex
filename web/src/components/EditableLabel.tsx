import { useContext, useState } from 'react'
import { z } from 'zod'
import { endpoint, queryClient, reconcile, request, Scope } from '../api/client'
import { ErrorNote } from './ui/common'

const labelSchema = z.object({ label: z.string().nullable() })

/** Workspace label; double-click to rename. Affects only this workspace, never the shared book metadata. */
export function EditableLabel({ id, label, showEmpty = false }: { id: string; label: string | null; showEmpty?: boolean }) {
  const scope = useContext(Scope)
  const [draft, setDraft] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  async function save() {
    if (draft === null || saving) return
    if ((draft.trim() || null) === label) { setDraft(null); return }
    setSaving(true); setError(null)
    try {
      await request(endpoint(id, 'label'), labelSchema, { method: 'PATCH', body: { label: draft.trim() || null } })
      queryClient.removeQueries({ predicate: q => q.queryKey[0] === scope && String(q.queryKey[1]).startsWith('/api/library') })
      await reconcile(id)
      setDraft(null)
    } catch (failure) { setError(failure) }
    finally { setSaving(false) }
  }
  if (draft !== null) return <div className="workspace-label"><input className="workspace-label-input" aria-label="Workspace label" autoFocus maxLength={64} value={draft} disabled={saving}
    placeholder="Workspace label" onChange={event => setDraft(event.target.value)} onBlur={() => void save()}
    onKeyDown={event => { if (event.key === 'Enter') void save(); else if (event.key === 'Escape') { setDraft(null); setError(null) } }} /><ErrorNote error={error} /></div>
  if (!label && !showEmpty) return null
  return <div className={`workspace-label${label ? '' : ' empty'}`} title="Double-click to rename" onDoubleClick={() => setDraft(label ?? '')}>{label || 'Add label'}</div>
}
