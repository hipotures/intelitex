import { useContext, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Play, Square } from 'lucide-react'
import { z } from 'zod'
import { endpoint, useApi, request, reconcile, Scope } from '../../api/client'
import { jobSchema, pipelineSchema, type Workspace, type Pipeline } from '../../api/schema'
import { useCommand } from '../../api/mutations'
import { createRequestKey } from '../../api/requestKey'
import { Button, ErrorNote } from '../../components/ui/common'
import { useLive } from '../../realtime/coordinator'
export function primaryAction(p: Pipeline) {
  const active = p.active_job
  const publication = p.publishing || active?.last_event?.event.kind === 'publication_started'
  if (active?.state === 'starting') return { label: 'Starting…', allowed: false }
  if (active?.state === 'stopping') return { label: 'Stopping…', allowed: false }
  if (publication || (active && active.operation === 'publish')) return { label: 'Publishing…', allowed: false }
  if (active) return { label: 'Stop', allowed: true, operation: 'stop' }
  if (p.metadata.lifecycle.archived) return { label: 'Restore before running', allowed: false }
  if (p.actions.analyze?.allowed) return { label: p.last_job?.state === 'failed' ? 'Retry' : 'Run', allowed: true, operation: 'analyze' }
  if (p.analysis.complete && !p.approved) return { label: 'Review', allowed: p.actions.prepare_review?.allowed, operation: 'review' }
  if (p.actions.translate?.allowed) return { label: p.last_job?.state === 'failed' ? 'Retry' : 'Run', allowed: true, operation: 'translate' }
  if (p.actions.publish?.allowed) return { label: p.publication.last_failure ? 'Retry publish' : 'Publish', allowed: true, operation: 'publish' }
  return { label: p.publication.current ? 'Finished' : 'Unavailable', allowed: false }
}
export function Action({ workspace, pipeline, row = false }: { workspace: Workspace; pipeline?: Pipeline; row?: boolean }) {
  const query = useApi(endpoint(workspace.workspace_id, 'pipeline'), pipelineSchema, workspace.prepared && !pipeline)
  const command = useCommand(workspace.workspace_id)
  const [localError, setLocalError] = useState<unknown>(null)
  const navigate = useNavigate()
  const scope = useContext(Scope)
  const live = useLive()
  const p = pipeline ?? query.data
  const snapshotJob = p?.active_job ?? workspace.active_job
  const freshJob = snapshotJob ? live.state.jobs[snapshotJob.job_id] : undefined
  const activeJob = freshJob && freshJob.sequence > snapshotJob!.sequence && ['starting','running','stopping'].includes(freshJob.state) ? freshJob : snapshotJob
  const recentPublication = [...(live.state.activity[workspace.workspace_id] ?? [])].reverse().find(e => e.job_id === activeJob?.job_id && e.event.kind.startsWith('publication_'))
  const action = p ? primaryAction({ ...p, publishing: p.publishing || recentPublication?.event.kind === 'publication_started', active_job: activeJob }) : workspace.metadata.lifecycle.archived
    ? { label: 'Restore before running', allowed: false, operation: 'prepare' }
    : { label: activeJob ? activeJob.state === 'stopping' ? 'Stopping…' : 'Stop' : 'Prepare', allowed: !activeJob || activeJob.state === 'running', operation: activeJob ? 'stop' : 'prepare' }
  async function run() {
    setLocalError(null)
    if (row && action.label === 'Finished') { await navigate({ to: '/work/workspaces/$workspaceId', params: { workspaceId: workspace.workspace_id } }); return }
    if (action.operation === 'review') { await navigate({ to: '/work/workspaces/$workspaceId/$phase', params: { workspaceId: workspace.workspace_id, phase: 'review' } }); return }
    if (action.operation === 'stop' && activeJob) { await command.send(`/api/jobs/${encodeURIComponent(activeJob.job_id)}/stop`, jobSchema, {}); return }
    const resource = action.operation === 'prepare' ? 'prepare' : 'jobs'
    const storageKey = `intelitex.pending.${scope}.${workspace.workspace_id}.${resource}.${action.operation}`
    let key: string
    try { key = sessionStorage.getItem(storageKey) ?? createRequestKey(); sessionStorage.setItem(storageKey, key) }
    catch { try { key = createRequestKey() } catch (error) { setLocalError(error); return } }
    const value = await command.send(endpoint(workspace.workspace_id, resource), jobSchema,
      action.operation === 'prepare' ? { request_key: key } : { operation: action.operation, request_key: key, ...(action.operation === 'translate' ? { chunk_limit: 0 } : {}) })
    if (value) { try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ } }
    else if (command.unknownOutcome()) {
      try {
        const receipt = await request(`/api/requests/${encodeURIComponent(key)}`, jobSchema)
        if (receipt.workspace_id === workspace.workspace_id && receipt.operation === (action.operation === 'prepare' ? 'import' : action.operation)) {
          try { sessionStorage.removeItem(storageKey) } catch { /* Optional storage. */ }
          command.clearError(); await reconcile(workspace.workspace_id)
        }
      } catch { /* Keep the pending receipt and the original error for deliberate recovery. */ }
    }
  }
  return <div className="command"><Button variant={action.operation === 'stop' ? 'danger' : row ? 'secondary' : 'primary'} disabled={command.disabled || (!action.allowed && !(row && action.label === 'Finished'))} onClick={() => void run()} title={!action.allowed ? p?.actions.publish?.reason?.replaceAll('_', ' ') : undefined}>
    {command.pending ? 'Saving…' : <>{action.operation === 'stop' ? <Square size={12} /> : action.label === 'Run' && !row ? <Play size={13} /> : null}{row && action.label === 'Finished' ? 'Open' : action.label}</>}</Button><ErrorNote error={localError ?? command.error ?? query.error} /></div>
}
export const emptyResponse = z.object({})
