import { useContext, useEffect, useSyncExternalStore } from 'react'
import { Scope, queryClient, request, matchesWorkspaceResource, isWorkspaceList } from '../api/client'
import { eventSchema, jobsSchema } from '../api/schema'
import { emptyStream, progress, snapshot } from './state'
import type { Connection } from './state'
let view = { state: emptyStream(), connection: 'Reconnecting…' as Connection }
const listeners = new Set<() => void>()
const emit = () => listeners.forEach(fn => fn())
export const useLive = () => useSyncExternalStore(fn => { listeners.add(fn); return () => { listeners.delete(fn) } }, () => view)
export const useConnection = () => useSyncExternalStore(fn => { listeners.add(fn); return () => { listeners.delete(fn) } }, () => view.connection)
export function Realtime() {
  const scope = useContext(Scope)
  useEffect(() => {
    view = { state: emptyStream(), connection: 'Reconnecting…' }; emit()
    const source = new EventSource('/api/events')
    let closed = false, refreshing = false, dirty = false, connected = false, hasSnapshot = false, ticks = 0
    let flushTimer: ReturnType<typeof setTimeout> | undefined
    let renderTimer: ReturnType<typeof setTimeout> | undefined
    let refreshAll = true
    const changedWorkspaces = new Set<string>()
    async function refresh() {
      if (closed) return
      if (refreshing) { dirty = true; return }
      refreshing = true
      const all = refreshAll, changed = [...changedWorkspaces]
      refreshAll = false; changedWorkspaces.clear()
      const predicate = (q: { queryKey: readonly unknown[]; getObserversCount(): number }) => {
        const path = String(q.queryKey[1])
        return q.queryKey[0] === scope && q.getObserversCount() > 0 && !path.startsWith('/api/library') && !path.includes('/reader/chapters/') &&
          (all || isWorkspaceList(path) || changed.some(id => matchesWorkspaceResource(path, id) &&
            /\/(pipeline|summary|usage|activity|analysis-reset|translation\/|analysis\/|reader\/progress)/.test(path)))
      }
      // Revalidating large Reader/Review queries must not hold the connection in
      // "Reconnecting" or prevent read-only navigation while their responses load.
      void queryClient.invalidateQueries({ predicate }, { cancelRefetch: false })
      const jobs = await Promise.allSettled([request('/api/jobs', jobsSchema)])
      if (jobs[0].status === 'fulfilled') view = { ...view, state: snapshot(view.state, jobs[0].value, false) }
      if (closed) return
      // An unrelated detail query can fail while SSE and the job endpoint are
      // healthy. Its own panel reports that error; it must not lock every action.
      const failed = jobs[0].status === 'rejected'
      view = { ...view, connection: navigator.onLine ? connected && !failed ? 'Live' : 'Reconnecting…' : 'Offline' }; emit()
      refreshing = false
      if (dirty) { dirty = false; void refresh() }
    }
    function schedule(immediate = false, workspace?: string) {
      if (workspace) changedWorkspaces.add(workspace)
      else refreshAll = true
      if (immediate) { clearTimeout(flushTimer); flushTimer = undefined; void refresh(); return }
      if (!flushTimer) flushTimer = setTimeout(() => { flushTimer = undefined; void refresh() }, 900)
    }
    source.addEventListener('snapshot', (event: MessageEvent<string>) => {
      try {
        const value = jobsSchema.parse(JSON.parse(event.data))
        view = { ...view, state: snapshot(view.state, value) }
        connected = true; hasSnapshot = true
        view = { ...view, connection: navigator.onLine ? 'Live' : 'Offline' }
        emit(); schedule(true)
      } catch { connected = false; view = { ...view, connection: 'Reconnecting…' }; emit(); schedule(true) }
    })
    source.addEventListener('progress', (event: MessageEvent<string>) => {
      try {
        const value = eventSchema.parse(JSON.parse(event.data))
        const next = progress(view.state, value)
        view = { ...view, state: next.state }
        if (value.event.kind === 'publication_library_added') {
          queryClient.removeQueries({ queryKey: [scope, '/api/library'], exact: true })
          void queryClient.invalidateQueries({ predicate: query => query.queryKey[0] === scope &&
            String(query.queryKey[1]).startsWith('/api/library') })
        }
        const terminal = value.event.kind === 'job_state' && ['succeeded','failed','cancelled','abandoned'].includes(String(value.event.values.state))
        if (terminal) { clearTimeout(renderTimer); renderTimer = undefined; emit() }
        else if (!renderTimer) renderTimer = setTimeout(() => { renderTimer = undefined; emit() }, 100)
        if (next.gap) schedule(true)
        else if (terminal) schedule(true, value.workspace_id)
        else if (!/waiting|delta|received|generation_progress/.test(value.event.kind)) schedule(false, value.workspace_id)
      } catch { connected = false; view = { ...view, connection: 'Reconnecting…' }; emit(); schedule(true) }
    })
    source.onerror = () => { connected = false; view = { ...view, connection: navigator.onLine ? 'Reconnecting…' : 'Offline' }; emit() }
    const resume = () => { if (!document.hidden) { if (navigator.onLine && hasSnapshot && source.readyState === EventSource.OPEN) connected = true; schedule(true) } }
    const offline = () => { connected = false; view = { ...view, connection: 'Offline' }; emit() }
    const polling = setInterval(() => {
      if (document.hidden) return
      ticks++
      if (!connected || ticks % 6 === 0) schedule(true)
    }, 5000)
    window.addEventListener('focus', resume); window.addEventListener('online', resume); window.addEventListener('offline', offline)
    document.addEventListener('visibilitychange', resume)
    return () => {
      closed = true; source.close(); clearInterval(polling); clearTimeout(flushTimer); clearTimeout(renderTimer)
      window.removeEventListener('focus', resume); window.removeEventListener('online', resume); window.removeEventListener('offline', offline)
      document.removeEventListener('visibilitychange', resume)
    }
  }, [scope])
  return null
}
