// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { queryClient, Scope } from '../../api/client'
import { Home } from './Home'

vi.mock('../../realtime/coordinator', () => ({ useConnection: () => 'Live' }))
afterEach(() => { cleanup(); queryClient.clear(); vi.unstubAllGlobals(); localStorage.clear() })

it('filters from two characters, preserves the filter on refresh, and clears it', async () => {
  vi.stubGlobal('IntersectionObserver', class {
    constructor(private callback: (entries: { isIntersecting: boolean }[]) => void) {}
    observe() { queueMicrotask(() => this.callback([{ isIntersecting: true }])) }
    disconnect() {}
  })
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = new URL(input, 'http://localhost')
    const filtered = url.searchParams.has('title')
    if (url.pathname === '/api/library') requests.push(url.search)
    return new Response(JSON.stringify(url.pathname === '/api/library' ? {
      configured: true, next_cursor: null, sources: (filtered ? ['Nether Vortex'] : ['Relay Book', 'Nether Vortex']).map(title => ({
        source_id: title, title, creators: [], language: 'pl', word_count: null, workspace_id: null, groups: [],
      })),
    } : { workspaces: [] }), { headers: { 'Content-Type': 'application/json' } })
  }))
  render(<QueryClientProvider client={queryClient}><Scope.Provider value="test"><Home /></Scope.Provider></QueryClientProvider>)
  await screen.findByRole('button', { name: 'Open Relay Book' })
  const input = screen.getByRole('searchbox', { name: 'Filter Library by title' })
  fireEvent.change(input, { target: { value: 'v' } })
  await new Promise(resolve => setTimeout(resolve, 350))
  expect(requests.some(query => query.includes('title='))).toBe(false)
  fireEvent.change(input, { target: { value: 'vo' } })
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Open Relay Book' })).toBeNull())
  await screen.findByRole('button', { name: 'Open Nether Vortex' })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh Library' }))
  await waitFor(() => expect(requests.filter(query => query.includes('title=vo'))).toHaveLength(2))
  fireEvent.change(input, { target: { value: '' } })
  await screen.findByRole('button', { name: 'Open Relay Book' })
})
