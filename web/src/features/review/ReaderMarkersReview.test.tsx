// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { Scope, queryClient } from '../../api/client'
import { ReaderMarkersReview } from './ReaderMarkersReview'

vi.mock('@tanstack/react-router', async original => ({ ...await original<typeof import('@tanstack/react-router')>(), Link: ({ children }: { children: ReactNode }) => <a>{children}</a> }))
afterEach(() => { cleanup(); vi.unstubAllGlobals(); queryClient.clear() })

it('shows markers only for the selected workspace in Work Review', async () => {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn((input: string) => {
    const path = String(input)
    calls.push(path)
    const value = path.endsWith('/reader/markers')
      ? { _revision: 'rev1', book_fingerprint: 'fp', markers: [{ id: 'M000001', chapter_id: 'c1', block_id: 'b1', start: 0, end: 5, text: 'Marked words' }] }
      : { title: 'Book', book_fingerprint: 'fp', chapters: [{ id: 'c1', title: 'Chapter one' }] }
    return Promise.resolve(new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  }))
  render(<QueryClientProvider client={queryClient}><Scope.Provider value="review-test"><ReaderMarkersReview id="workspace-b" /></Scope.Provider></QueryClientProvider>)
  await waitFor(() => expect(screen.getByText('Marked words')).toBeTruthy())
  expect(screen.getByText('Chapter one · M000001')).toBeTruthy()
  expect(screen.getByRole('region', { name: 'Reader markers' }).getAttribute('data-ui-debug-id')).toBe('RMR')
  expect(calls).toEqual(['/api/workspaces/workspace-b/reader/markers', '/api/workspaces/workspace-b/reader'])
})
