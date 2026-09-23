import { useEffect, useRef, useState } from 'react'
import { endpoint, useApi } from '../../api/client'
import { previewSchema, type Pipeline, type Workspace } from '../../api/schema'
import { Button, Empty, ErrorNote, Panel } from '../../components/ui/common'
import { prepareExcerpt } from './prepareExcerpt'

const shortTypes: Record<string, string> = {
  narrative: 'Narr.', contents: 'Contents', glossary: 'Gloss.', footnotes: 'Notes',
  front_matter: 'Front', back_matter: 'Back', advertisement: 'Ad', unclassified: 'Other',
}
const modes = { full: ['F', 'Full'], translate: ['T', 'Translate only'], excluded: ['E', 'Excluded'] } as const

export function PrepareContent({ id, pipeline, preparation, preparationError, workspace, canReprepare,
  commandDisabled, onReprepare }: {
  id: string
  pipeline: Pipeline
  preparation?: { checks: string[]; unavailable: string | null; reading_order: string | null; source_id: string }
  preparationError: unknown
  workspace: Workspace | undefined
  canReprepare: boolean
  commandDisabled: boolean
  onReprepare: () => void
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = pipeline.sections.find(section => section.id === selectedId)
  const preview = useApi(endpoint(id, `sections/${encodeURIComponent(selected?.id ?? '')}/0`), previewSchema, !!selected)
  const excerpt = preview.data ? prepareExcerpt(preview.data.blocks) : null
  const previewRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (selectedId && window.matchMedia('(max-width: 900px)').matches) {
      previewRef.current?.scrollIntoView({ block: 'nearest' })
    }
  }, [selectedId])

  return <div className="phase-detail-grid prepare-detail-grid">
    <div className="phase-detail-stack">
      <Panel debugId="PSR" title="Source structure">
        <div className="prepare-structure-scroll"><table className="phase-detail-table prepare-structure">
          <colgroup><col className="prepare-section-col" /><col className="prepare-type-col" /><col className="prepare-processing-col" /><col className="prepare-p1-col" /></colgroup>
          <thead><tr><th>Section</th><th>Content type</th><th>Processing</th><th>P1</th></tr></thead>
          <tbody>{pipeline.sections.map(section => {
            const name = section.title || section.fallback_excerpt || 'Untitled section'
            const [letter, mode] = modes[section.processing]
            const type = section.content_type.replaceAll('_', ' ')
            return <tr key={section.id} className={selectedId === section.id ? 'selected' : ''} onClick={() => setSelectedId(section.id)}>
              <td title={name}><button className="prepare-section-choice" aria-pressed={selectedId === section.id} aria-controls="prepare-source-preview" onClick={() => setSelectedId(section.id)}>{String(section.ordinal).padStart(2, '0')} · {name}</button></td>
              <td title={type}><span className="prepare-type">{shortTypes[section.content_type] ?? type}</span></td>
              <td title={mode}><span className={`prepare-mode ${section.processing}`}>{letter}</span></td>
              <td title={section.processing === 'full' ? 'Included in P1' : 'Outside P1'}><span className={section.processing === 'full' ? 'prepare-p1 included' : 'prepare-p1'} aria-label={section.processing === 'full' ? 'Included in P1' : 'Outside P1'}>{section.processing === 'full' ? 'in' : '—'}</span></td>
            </tr>
          })}</tbody>
        </table></div>
      </Panel>
    </div>
    <div className="phase-detail-stack">
      <div ref={previewRef}>
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
      <Panel debugId="PSM" title="Source metadata"><dl className="phase-kv"><dt>Title</dt><dd>{pipeline.metadata.title}</dd><dt>Author</dt><dd>{pipeline.metadata.creators.join(', ') || '—'}</dd><dt>Source language</dt><dd>{pipeline.metadata.language ?? '—'}</dd><dt>Source</dt><dd>{pipeline.preparation.source_id}</dd></dl></Panel>
      <Panel debugId="PCK" title="Checks">
        {(preparation?.checks ?? []).map(check => <p className="phase-check" key={check}><span className="phase-check-icon">✓</span>{check}</p>)}
        <ErrorNote error={preparationError} />
        {preparation?.unavailable && <p className="subtitle">{preparation.unavailable}</p>}
        <p className="subtitle">Reading order: {preparation?.reading_order?.replaceAll('_', ' ') ?? '—'}</p>
        <div className="prepare-rerun"><p className="subtitle">Rebuild the source sections in this workspace. The previous plan is kept in versioned history; section F/T/E choices must be reviewed again.</p>
          <div className="prepare-rerun-actions"><Button variant="primary" disabled={!canReprepare || commandDisabled} onClick={onReprepare}>{pipeline.active_job?.operation === 'import' ? 'Rebuilding…' : 'Run Prepare again'}</Button></div>
          {pipeline.analysis.membership_locked && <p className="subtitle">P1 has persisted work, so this plan cannot be replaced in place.</p>}
          {!workspace?.source_id && <p className="subtitle">This workspace has no saved Library source for another Prepare.</p>}
          {pipeline.last_job?.operation === 'import' && pipeline.last_job.state === 'failed' && <p className="subtitle">The rebuild failed. The previous source plan remains available.</p>}
        </div>
      </Panel>
    </div>
  </div>
}
