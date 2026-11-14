import { useContext, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { z } from 'zod'
import { endpoint, queryClient, reconcile, request, Scope } from '../api/client'
import { ErrorNote } from './ui/common'

const labelSchema = z.object({ label: z.string().nullable() })

/** True while an IME composition owns the key press. Safari resets isComposing before the confirming Enter keydown, so keyCode 229 is checked too. */
function composing(event: KeyboardEvent) {
  return event.nativeEvent.isComposing || event.keyCode === 229
}

/** Workspace label; double-click (or Enter/Space when focused) to rename. Affects only this workspace, never the shared book metadata. */
export function EditableLabel({ id, label, showEmpty = false }: { id: string; label: string | null; showEmpty?: boolean }) {
  const scope = useContext(Scope)
  const [draft, setDraft] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const savingRef = useRef(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const refocus = useRef(false)
  useEffect(() => {
    if (draft === null && refocus.current) { refocus.current = false; trigger.current?.focus() }
  }, [draft])
  async function save() {
    if (draft === null || savingRef.current) return
    if ((draft.trim() || null) === label) { setDraft(null); return }
    savingRef.current = true; setSaving(true); setError(null)
    try {
      await request(endpoint(id, 'label'), labelSchema, { method: 'PATCH', body: { label: draft.trim() || null } })
      queryClient.removeQueries({ predicate: q => q.queryKey[0] === scope && String(q.queryKey[1]).startsWith('/api/library') })
      await reconcile(id)
      setDraft(null)
    } catch (failure) { refocus.current = false; setError(failure) }
    finally { savingRef.current = false; setSaving(false) }
  }
  if (draft !== null) return <div className="workspace-label"><input className="workspace-label-input" aria-label="Workspace label" autoFocus maxLength={64} value={draft} disabled={saving}
    placeholder="Workspace label" onChange={event => setDraft(event.target.value)} onBlur={() => void save()}
    onKeyDown={event => {
      if (composing(event)) return
      if (event.key === 'Enter') { refocus.current = true; void save() }
      else if (event.key === 'Escape') { refocus.current = true; setDraft(null); setError(null) }
    }} /><ErrorNote error={error} /></div>
  if (!label && !showEmpty) return null
  // A pointer starts renaming with a double-click as before; a keyboard or assistive-technology activation
  // (a click with detail 0) starts it directly, so the control is no longer pointer-only.
  return <button ref={trigger} type="button" className={`workspace-label${label ? '' : ' empty'}`} title="Double-click or press Enter to rename"
    onClick={event => { if (event.detail === 0) setDraft(label ?? '') }} onDoubleClick={() => setDraft(label ?? '')}>{label || 'Add label'}</button>
}
