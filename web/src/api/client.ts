import { z } from 'zod'
import { QueryClient, useQuery } from '@tanstack/react-query'
import { createContext, useContext } from 'react'
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message) }
}
export const Scope = createContext('boot')
export const endpoint = (workspace: string, resource = '') => `/api/workspaces/${encodeURIComponent(workspace)}${resource ? '/' + resource : ''}`
export async function request<T>(path: string, schema: z.ZodType<T>, options: { signal?: AbortSignal; method?: string; body?: unknown } = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { signal: options.signal, method: options.method ?? 'GET', credentials: 'same-origin', redirect: 'error',
      headers: options.body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: options.body === undefined ? undefined : JSON.stringify(options.body) })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'unknown_outcome', options.method ? 'Request status unknown. Reload current state before trying again.' : 'Connection unavailable. Showing last known state.')
  }
  const value: unknown = await response.json()
  if (!response.ok) {
    const parsed = z.object({ error: z.object({ code: z.string(), message: z.string() }) }).safeParse(value)
    throw new ApiError(response.status, parsed.success ? parsed.data.error.code : 'invalid_response', parsed.success ? parsed.data.error.message : 'The server could not complete this request.')
  }
  const parsed = schema.safeParse(value)
  if (!parsed.success) throw new ApiError(502, 'incompatible_response', 'The server response is incompatible. Refresh after checking server and interface versions.')
  return parsed.data
}
export const queryClient = new QueryClient({ defaultOptions: { queries: {
  staleTime: 15_000, gcTime: 30 * 60_000, refetchOnWindowFocus: false, refetchOnReconnect: false,
  retry: (attempt, error) => attempt < 2 && error instanceof ApiError && (error.status === 0 || error.status >= 500),
  retryDelay: attempt => (attempt + 1) * 1000,
}, mutations: { retry: false, networkMode: 'always' } } })
export function useApi<T>(path: string, schema: z.ZodType<T>, enabled = true) {
  const scope = useContext(Scope)
  return useQuery({ queryKey: [scope, path], queryFn: ({ signal }) => request(path, schema, { signal }), enabled })
}
export async function reconcile(workspace?: string) {
  await queryClient.invalidateQueries({ predicate: q => !String(q.queryKey[1]).includes('/reader/chapters/') && (!workspace || String(q.queryKey[1]).startsWith(endpoint(workspace)) || ['/api/workspaces', '/api/library', '/api/jobs'].includes(String(q.queryKey[1]))) })
}
