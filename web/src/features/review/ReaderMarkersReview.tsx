import { Link } from '@tanstack/react-router'
import { endpoint, useApi } from '../../api/client'
import { markersSchema, readerSchema } from '../../api/schema'
import { ErrorNote } from '../../components/ui/common'
import { debugTag } from '../../debug/regions'

export function ReaderMarkersReview({ id }: { id: string }) {
  const markers = useApi(endpoint(id, 'reader/markers'), markersSchema)
  const metadata = useApi(endpoint(id, 'reader'), readerSchema)
  return <section id="reader-markers" className="review-reader-markers" aria-label="Reader markers" {...debugTag('RMR')}>
    <h2>Reader markers · {markers.data?.markers.length ?? 0}</h2>
    <p>Passages marked while reading this workspace. They do not change terminology approval.</p>
    <ErrorNote error={markers.error ?? metadata.error} retry={() => { void markers.refetch(); void metadata.refetch() }} />
    {markers.data?.markers.map(marker => <div className="review-reader-marker" key={marker.id}>
      <span>{metadata.data?.chapters.find(chapter => chapter.id === marker.chapter_id)?.title ?? marker.chapter_id} · {marker.id}</span>
      <blockquote>{marker.text}</blockquote>
      <Link to="/reader/$workspaceId" params={{workspaceId:id}} search={{chapter:marker.chapter_id}}>Read passage →</Link>
    </div>)}
    {markers.isPending && <span>Loading markers…</span>}
    {markers.data?.markers.length === 0 && <span>No Reader markers in this workspace.</span>}
  </section>
}
