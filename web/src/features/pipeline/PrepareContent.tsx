import { useState } from 'react'
import { endpoint, useApi } from '../../api/client'
import { previewSchema, type Pipeline, type Section, type Workspace } from '../../api/schema'
import { Button, Empty, ErrorNote, Panel } from '../../components/ui/common'
import { useConnection } from '../../realtime/coordinator'
import { prepareExcerpt } from './prepareExcerpt'

const shortTypes: Record<string, string> = {
  narrative: 'Narr.', contents: 'Contents', glossary: 'Gloss.', footnotes: 'Notes',
  front_matter: 'Front', back_matter: 'Back', advertisement: 'Ad', unclassified: 'Other',
}
const modes = { full: ['F', 'Full'], translate: ['T', 'Translate only'], excluded: ['E', 'Excluded'] } as const

export function PrepareContent({ id, pipeline, preparation, preparationError, workspace, canReprepare,
  commandDisabled, pendingProcessing, onProcessingChange, processingError, onReprepare }: {
  id: string
  pipeline: Pipeline
  preparation?: { checks: string[]; unavailable: string | null; reading_order: string | null; source_id: string }
  preparationError: unknown
  workspace: Workspace | undefined
  canReprepare: boolean
  commandDisabled: boolean
  pendingProcessing: { sectionId: string; mode: Section['processing'] } | null
  onProcessingChange: (value: { sectionId: string; mode: Section['processing'] }) => void
  processingError: unknown
  onReprepare: () => void
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const connection = useConnection()
  const selected = pipeline.sections.find(section => section.id === selectedId)
  const preview = useApi(endpoint(id, `sections/${encodeURIComponent(selected?.id ?? '')}/0`), previewSchema, !!selected)
  const excerpt = preview.data ? prepareExcerpt(preview.data.blocks) : null
  const rebuildUnavailable = pipeline.metadata.lifecycle.archived ? 'Restore this workspace before rebuilding Prepare.'
    : pipeline.busy ? 'Wait for the current workspace operation to finish before rebuilding Prepare.'
    : pipeline.analysis.membership_locked ? 'P1 has saved work. Clear P1 in Analyse first if the backend permits it.'
    : !workspace?.source_id ? 'This workspace is not linked to its original Library source, so Prepare cannot rebuild it.'
    : connection === 'Offline' ? 'The server is offline. Reconnect before rebuilding Prepare.'
    : connection !== 'Live' ? 'The server is reconnecting. Wait for a live connection before rebuilding Prepare.'
    : commandDisabled ? 'Wait for the current workspace change to finish before rebuilding Prepare.' : null

  return <div className="phase-detail-grid prepare-detail-grid">
    <div className="phase-detail-stack">
      <Panel debugId="PSR" title="Source structure">
        <div className="prepare-structure-scroll"><table className="phase-detail-table prepare-structure">
          <colgroup><col className="prepare-section-col" /><col className="prepare-type-col" /><col className="prepare-processing-col" /><col className="prepare-p1-col" /></colgroup>
          <thead><tr><th>Section</th><th>Content type</th><th>Processing</th><th>P1</th></tr></thead>
          <tbody>{pipeline.sections.map(section => {
            const name = section.title || section.fallback_excerpt || 'Untitled section'
            const type = section.content_type.replaceAll('_', ' ')
            const pending = pendingProcessing?.sectionId === section.id ? pendingProcessing : null
            const displayedMode = pending?.mode ?? section.processing
            return <tr key={section.id} className={selectedId === section.id ? 'selected' : ''} onClick={() => setSelectedId(section.id)}>
              <td title={name}><button className="prepare-section-choice" aria-pressed={selectedId === section.id} aria-controls="prepare-source-preview" onClick={() => setSelectedId(section.id)}>{String(section.ordinal).padStart(2, '0')} · {name}</button></td>
              <td title={type}><span className="prepare-type">{shortTypes[section.content_type] ?? type}</span></td>
              <td><div className={`processing-switch prepare-processing-switch ${pending ? 'saving' : ''}`} aria-label={`Processing ${name}${pending ? ' · saving change' : ''}`} aria-busy={!!pending}>{(Object.entries(modes) as [Section['processing'], readonly [string, string]][]).map(([mode, [letter, label]]) => <button key={mode} data-mode-value={label} title={`${letter} — ${label}`} aria-pressed={displayedMode === mode} className={displayedMode === mode ? 'active' : ''} disabled={commandDisabled || !!pending || pipeline.busy || pipeline.metadata.lifecycle.archived || (pipeline.analysis.membership_locked && mode !== section.processing && (mode === 'full' || section.processing === 'full'))} onClick={event => { event.stopPropagation(); setSelectedId(section.id); onProcessingChange({ sectionId: section.id, mode }) }}>{letter}</button>)}</div></td>
              <td title={pending ? 'Saving P1 membership choice' : section.processing === 'full' ? 'Included in P1' : 'Outside P1'}><span className={displayedMode === 'full' ? 'prepare-p1 included' : 'prepare-p1'} aria-label={pending ? 'Saving P1 membership choice' : displayedMode === 'full' ? 'Included in P1' : 'Outside P1'}>{displayedMode === 'full' ? 'in' : '—'}</span></td>
            </tr>
          })}</tbody>
        </table></div>
        <ErrorNote error={processingError} />
      </Panel>
    </div>
    <div className="phase-detail-stack">
      <div>
        <Panel debugId="PPR" title="Source preview">
          <div id="prepare-source-preview" className="prepare-source-preview">
            {!selected ? <Empty>Select a section to read the first 1 KiB of its source text.</Empty> : <>
              <div className="prepare-preview-kicker">Section {String(selected.ordinal).padStart(2, '0')} · first 1 KiB</div>
              <h3 title={selected.title ?? undefined}>{selected.title || selected.fallback_excerpt || 'Untitled section'}</h3>
              <ErrorNote error={preview.error} retry={() => void preview.refetch()} />
              {preview.isPending ? <p className="subtitle" role="status">Loading source text…</p> :
                excerpt?.text ? <div className="prepare-preview-text">{excerpt.text}</div> :
                  !preview.error && <Empty>No source text in this section.</Empty>}
            </>}
          </div>
        </Panel>
      </div>
      <Panel debugId="PSM" title="Source metadata"><dl className="phase-kv"><dt>Title</dt><dd>{pipeline.metadata.title}</dd><dt>Author</dt><dd>{pipeline.metadata.creators.join(', ') || 'Not recorded'}</dd><dt>Source language</dt><dd>{pipeline.metadata.source_language ?? pipeline.metadata.language ?? 'Not recorded'}</dd><dt>{workspace?.source_id ? 'Library source' : 'Import folder'}</dt><dd>{workspace?.source_id ?? pipeline.preparation.source_id}</dd></dl></Panel>
      <Panel debugId="PCK" title="Checks">
        {(preparation?.checks ?? []).map(check => <p className="phase-check" key={check}><span className="phase-check-icon">✓</span>{check}</p>)}
        <ErrorNote error={preparationError} />
        {preparation?.unavailable && <p className="subtitle">{preparation.unavailable}</p>}
        <p className="subtitle">Reading order: {preparation?.reading_order?.replaceAll('_', ' ') ?? '—'}</p>
        <div className="prepare-rerun"><p className="subtitle">Rebuild the source sections in this workspace. The previous plan is kept in versioned history; section F/T/E choices must be reviewed again.</p>
          <div className="prepare-rerun-actions">
            {rebuildUnavailable && <p id="prepare-rebuild-reason" className="prepare-rerun-reason"><strong>Rebuild unavailable:</strong> {rebuildUnavailable}</p>}
            <Button variant="primary" disabled={!canReprepare || commandDisabled} aria-describedby={rebuildUnavailable ? 'prepare-rebuild-reason' : undefined} onClick={onReprepare}>{pipeline.active_job?.operation === 'import' ? 'Rebuilding…' : 'Rebuild'}</Button>
          </div>
          {pipeline.last_job?.operation === 'import' && pipeline.last_job.state === 'failed' && <p className="subtitle">The rebuild failed. The previous source plan remains available.</p>}
        </div>
      </Panel>
    </div>
  </div>
}
