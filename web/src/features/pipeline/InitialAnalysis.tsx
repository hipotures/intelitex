import { useContext, useRef, useState } from 'react'
import { ApiError, endpoint, reconcile, request, Scope, useApi } from '../../api/client'
import { jobSchema, pipelineSchema, sourcePreloadSchema, type Pipeline } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { createRequestKey } from '../../api/requestKey'
import { Button, ErrorNote, Overlay } from '../../components/ui/common'

// Source-only P0 does not persist a P1 plan. Its exact local projection still
// identifies the first P1 unit, so a manual cache experiment need not start all P1.
export function InitialAnalysis({ id, pipeline }: { id: string; pipeline: Pipeline }) {
  const inventory = useApi(endpoint(id, 'source-preload'), sourcePreloadSchema)
  const first = inventory.data?.chapters.flatMap(chapter => chapter.targets.flatMap(target =>
    target.consumers.filter(consumer => consumer.pass_no === 1).map(consumer => ({ chapter, target, consumer }))))[0]
  const scope = useContext(Scope), command = useCommand(id), latch = useRef(false)
  const [open, setOpen] = useState(false), [error, setError] = useState<unknown>(null)
  const disabled = command.disabled || pipeline.busy || pipeline.metadata.lifecycle.archived
  async function run() {
    if (!first || !inventory.data || disabled || latch.current) return
    latch.current = true
    setError(null)
    const storageKey = `intelitex.pending.${scope}.${id}.analysis.${first.consumer.unit_id}`
    try {
      // Resolve an earlier acknowledgement before interpreting a changed plan.
      const saved = sessionStorage.getItem(storageKey)
      if (saved) {
        try {
          const receipt = await request(`/api/requests/${encodeURIComponent(saved)}`, jobSchema)
          if (receipt.workspace_id !== id || receipt.operation !== 'analyze') throw new Error('Unexpected job receipt.')
          sessionStorage.removeItem(storageKey); setOpen(false); void reconcile(id); return
        } catch (failure) { if (!(failure instanceof ApiError && failure.status === 404)) throw failure }
      }
      const latest = await request(endpoint(id, 'pipeline'), pipelineSchema)
      const source = await request(endpoint(id, 'source-preload'), sourcePreloadSchema)
      if (latest.analysis.planned || latest.analysis.membership_locked || !latest.actions.analyze?.allowed ||
          latest.busy || latest.metadata.lifecycle.archived || source.intent_revision !== inventory.data.intent_revision)
        throw new Error('P1 state changed. Refresh and review the next analysis unit.')
      const key = saved ?? createRequestKey()
      sessionStorage.setItem(storageKey, key)
      const result = await command.send(endpoint(id, 'jobs'), jobSchema,
        { operation: 'analyze', unit_id: first.consumer.unit_id, request_key: key }, 'POST', true)
      if (result) {
        sessionStorage.removeItem(storageKey); setOpen(false); void reconcile(id)
      } else if (!command.unknownOutcome()) sessionStorage.removeItem(storageKey)
      else setError(new Error('Request outcome unknown. Retry checks the saved job receipt first.'))
    } catch (failure) { setError(failure) }
    finally { latch.current = false }
  }
  return <>
    <ErrorNote error={inventory.error} />
    {first && <div className="initial-analysis">
      <p className="subtitle">Run one P1 unit after source preload, then inspect its measured Cache tokens. P1 follows chapter order; a cache hit is not guaranteed.</p>
      <Button disabled={disabled} onClick={() => setOpen(true)}>Run first P1 unit · {first.chapter.title ?? first.chapter.chapter_id}</Button>
      {open && <Overlay title="Run this P1 unit?" debugId="AUM" compact close={() => setOpen(false)}>
        <div className="modal-body"><p>{first.chapter.title ?? first.chapter.chapter_id} · {first.target.label}</p>
          <p>This starts one analysis unit and spends model tokens. Compatible P0 is reused; missing P0 is prepared internally.</p>
          <p>{first.target.model} · {first.target.effort ?? 'default'} effort</p><ErrorNote error={error ?? command.error} /></div>
        <div className="modal-foot"><Button onClick={() => setOpen(false)}>Cancel</Button><Button variant="primary" disabled={disabled} onClick={() => void run()}>Run P1 unit</Button></div>
      </Overlay>}
    </div>}
  </>
}
