import { useContext, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { endpoint, queryClient, Scope, useApi } from '../../api/client'
import { previewSchema, publicationSelectionSchema, type Pipeline } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { Button, Empty, ErrorNote, Panel } from '../../components/ui/common'
import { sectionTitles } from './sectionTitles'

export function PublishSelection({ id, pipeline }: { id: string; pipeline: Pipeline }) {
  const scope = useContext(Scope)
  const path = endpoint(id, 'publication/selection')
  const query = useApi(path, publicationSelectionSchema)
  const command = useCommand(id)
  const [intent, setIntent] = useState<{ id: string; excluded: boolean } | null>(null)
  const [selectedId, setSelectedId] = useState(() => pipeline.sections.find(section => section.processing !== 'excluded')?.id ?? pipeline.sections[0]?.id ?? '')
  const [page, setPage] = useState(0)
  const layout = useRef<HTMLDivElement>(null)
  const userSelected = useRef(false)
  const selectedSection = pipeline.sections.find(section => section.id === selectedId)
  const selected = new Set(query.data?.excluded_section_ids ?? [])
  const groups = new Map(query.data?.groups.flatMap(group => group.section_ids.map(sectionId => [sectionId, group] as const)) ?? [])
  const titles = sectionTitles(pipeline.sections, pipeline.metadata.creators)
  const preview = useApi(endpoint(id, `sections/${encodeURIComponent(selectedId)}/${page}`), previewSchema, !!selectedSection)
  const sourceCode = pipeline.metadata.source_language ?? pipeline.metadata.language
  const language = sourceCode === 'en' ? 'English' : sourceCode === 'pl' ? 'Polish' : sourceCode ?? 'Source language unknown'
  const diagnosticIds = new Set(query.data?.diagnostic?.section_ids ?? [])

  useEffect(() => {
    const first = query.data?.diagnostic?.section_ids[0]
    if (first && !userSelected.current) { setSelectedId(first); setPage(0); userSelected.current = true }
  }, [query.data?.diagnostic])

  useLayoutEffect(() => {
    const element = layout.current
    if (!element) return
    const update = () => element.style.setProperty('--publish-selection-top', `${element.getBoundingClientRect().top}px`)
    const observer = new ResizeObserver(update)
    const main = element.closest('main')
    if (main) observer.observe(main)
    window.addEventListener('resize', update)
    update()
    return () => { observer.disconnect(); window.removeEventListener('resize', update) }
  }, [])

  function select(sectionId: string) {
    userSelected.current = true
    if (selectedId !== sectionId) { setSelectedId(sectionId); setPage(0) }
  }

  async function change(group: NonNullable<typeof query.data>['groups'][number], excluded: boolean) {
    if (!query.data || command.disabled || pipeline.busy) return
    setIntent({ id: group.id, excluded })
    const next = new Set(query.data.excluded_section_ids)
    for (const sectionId of group.section_ids) {
      if (excluded) next.add(sectionId)
      else next.delete(sectionId)
    }
    const result = await command.send(path, publicationSelectionSchema,
      { revision: query.data.revision, excluded_section_ids: [...next].sort() }, 'PATCH', true)
    if (result) queryClient.setQueryData([scope, path], result)
    setIntent(null)
    void queryClient.invalidateQueries({ queryKey: [scope, path], exact: true })
    void queryClient.invalidateQueries({ queryKey: [scope, endpoint(id, 'pipeline')], exact: true })
  }

  const omitted = query.data?.groups.filter(group => group.section_ids.every(sectionId => selected.has(sectionId))).length ?? 0
  return <div className="phase-detail-grid publish-detail-grid" ref={layout}>
    <Panel debugId="PSE" title={`EPUB sections · ${omitted} omitted`}>
      <p className="subtitle publish-selection-help">Choose which pages appear in the final EPUB. Saved P1–P5 results remain in this workspace.</p>
      {!!diagnosticIds.size && <div className="notice error publish-selection-problem" role="alert">The last publication failed in {pipeline.sections.filter(section => diagnosticIds.has(section.id)).map(section => titles.get(section.id) ?? section.id).join(', ')}. Inspect its source content here before retrying.</div>}
      <ErrorNote error={query.error ?? command.error} retry={() => void query.refetch()} />
      {!query.data ? <Empty>Loading publication sections…</Empty> :
        <div className="publish-section-scroll diagnostic-scroll"><table className="phase-detail-table publish-section-table">
          <thead><tr><th>Section</th><th>Mode</th><th>EPUB</th></tr></thead>
          <tbody>{pipeline.sections.map(section => {
            const group = groups.get(section.id)
            const excluded = group ? intent?.id === group.id ? intent.excluded : group.section_ids.every(sectionId => selected.has(sectionId)) : false
            const name = titles.get(section.id) ?? section.title ?? 'Untitled section'
            const related = group?.section_ids.length ?? 1
            const attention = group?.section_ids.some(sectionId => diagnosticIds.has(sectionId)) && !excluded
            return <tr key={section.id} className={`${selectedId === section.id ? 'selected' : ''} ${excluded ? 'omitted' : ''} ${attention ? 'attention' : ''}`} onClick={() => select(section.id)}>
              <td><button className="publish-section-choice" aria-pressed={selectedId === section.id} aria-controls="publish-source-preview" onClick={() => select(section.id)} title={name}>{String(section.ordinal).padStart(2, '0')} · {name}</button></td>
              <td><span className={`processing-readonly ${section.processing}`} title={section.processing}>{section.processing === 'full' ? 'F' : section.processing === 'translate' ? 'T' : 'E'}</span></td>
              <td><div className={`processing-switch publish-processing-switch ${intent?.id === group?.id ? 'saving' : ''}`}
                aria-label={`EPUB inclusion for ${name}${related > 1 ? `; linked with ${related - 1} other sections` : ''}`} aria-busy={intent?.id === group?.id}>
                {([['Include', false], ['Omit', true]] as const).map(([label, value]) => <button key={label} className={excluded === value ? 'active' : ''}
                  aria-pressed={excluded === value} title={related > 1 ? `${label} all ${related} sections in this source document` : label}
                  disabled={!group || command.disabled || pipeline.busy || pipeline.metadata.lifecycle.archived || !!intent}
                  onClick={event => { event.stopPropagation(); select(section.id); if (group && excluded !== value) void change(group, value) }}>{label}</button>)}
              </div></td>
            </tr>
          })}</tbody>
        </table></div>}
    </Panel>
    <Panel debugId="PSP" title={`${language} preview`}>
      <div id="publish-source-preview" className="publish-source-preview">
        {!selectedSection ? <Empty>Select a section to inspect its content.</Empty> : <>
          <div className="publish-preview-heading"><span>{selectedSection.id} · {titles.get(selectedSection.id) ?? selectedSection.title ?? 'Untitled section'}</span><span>{groups.get(selectedSection.id)?.section_ids.every(sectionId => selected.has(sectionId)) ? 'Omitted from EPUB' : 'Included in EPUB'}</span></div>
          <ErrorNote error={preview.error} retry={() => void preview.refetch()} />
          <div className="publish-preview-scroll">
            {preview.isPending ? <p className="subtitle" role="status">Loading source text…</p> :
              preview.data?.blocks.length ? preview.data.blocks.map(block => <div key={block.id} className="publish-preview-block"><span>{block.id}</span><p>{block.text}</p></div>) :
              !preview.error && <Empty>No source text in this section.</Empty>}
          </div>
          <div className="publish-preview-pages"><Button disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button><span>Page {page + 1}</span><Button disabled={preview.data?.next_page == null} onClick={() => setPage(preview.data!.next_page!)}>Next</Button></div>
        </>}
      </div>
    </Panel>
  </div>
}
