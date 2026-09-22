import { useContext, useEffect, useSyncExternalStore } from 'react'
import { Scope, queryClient, request } from '../api/client'
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
    async function refresh() {
      if (closed) return
      if (refreshing) { dirty = true; return }
      refreshing = true
      const predicate = (q: { queryKey: readonly unknown[]; getObserversCount(): number }) => q.queryKey[0] === scope && q.getObserversCount() > 0 && q.queryKey[1] !== '/api/library' && !String(q.queryKey[1]).includes('/reader/chapters/')
      const results = await Promise.allSettled([
        queryClient.invalidateQueries({ predicate }, { cancelRefetch: false }),
        request('/api/jobs', jobsSchema),
      ])
      const jobs = results[1]
      if (jobs.status === 'fulfilled') view = { ...view, state: snapshot(view.state, jobs.value, false) }
      if (closed) return
      const failed = jobs.status === 'rejected' || queryClient.getQueryCache().findAll({ predicate }).some(q => q.state.status === 'error')
      view = { ...view, connection: navigator.onLine ? connected && !failed ? 'Live' : 'Reconnecting…' : 'Offline' }; emit()
      refreshing = false
      if (dirty) { dirty = false; schedule() }
    }
    function schedule(immediate = false) {
      if (immediate) { clearTimeout(flushTimer); flushTimer = undefined; void refresh(); return }
      if (!flushTimer) flushTimer = setTimeout(() => { flushTimer = undefined; void refresh() }, 900)
    }
    source.addEventListener('snapshot', (event: MessageEvent<string>) => {
      try {
        const value = jobsSchema.parse(JSON.parse(event.data))
        view = { ...view, state: snapshot(view.state, value) }
        connected = true; hasSnapshot = true; emit(); schedule(true)
      } catch { connected = false; view = { ...view, connection: 'Reconnecting…' }; emit(); schedule(true) }
    })
    source.addEventListener('progress', (event: MessageEvent<string>) => {
      try {
        const value = eventSchema.parse(JSON.parse(event.data))
        const next = progress(view.state, value)
        view = { ...view, state: next.state }
        const terminal = value.event.kind === 'job_state' && ['succeeded','failed','cancelled','abandoned'].includes(String(value.event.values.state))
        if (terminal) { clearTimeout(renderTimer); renderTimer = undefined; emit() }
        else if (!renderTimer) renderTimer = setTimeout(() => { renderTimer = undefined; emit() }, 100)
        if (next.gap || terminal) schedule(true)
        else if (!/waiting|delta|received|generation_progress/.test(value.event.kind)) schedule()
      } catch { connected = false; view = { ...view, connection: 'Reconnecting…' }; emit(); schedule(true) }
    })
    source.onerror = () => { connected = false; view = { ...view, connection: navigator.onLine ? 'Reconnecting…' : 'Offline' }; emit() }
    const resume = () => { if (!document.hidden) { if (navigator.onLine && hasSnapshot && source.readyState === EventSource.OPEN) connected = true; schedule(true) } }
    const offline = () => { connected = false; view = { ...view, connection: 'Offline' }; emit() }
    const polling = setInterval(() => {
      if (document.hidden) return
      ticks++
      if (!connected || ticks % 3 === 0) schedule(true)
      else void queryClient.invalidateQueries({ predicate: q => q.queryKey[0] === scope && q.getObserversCount() > 0 && String(q.queryKey[1]).endsWith('/review') }, { cancelRefetch: false })
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
