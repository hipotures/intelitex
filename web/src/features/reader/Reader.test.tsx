// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Scope, queryClient } from '../../api/client'
import { ReadingBook } from './Reader'

vi.mock('@tanstack/react-router', async original => ({ ...await original<typeof import('@tanstack/react-router')>(), useSearch: () => ({}), useNavigate: () => vi.fn() }))
vi.mock('../../realtime/coordinator', () => ({ useConnection: () => 'Live' }))

const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } })
const marker = { id: 'M000001', chapter_id: 'c1', block_id: 'b1', start: 0, end: 5, text: 'Relay' }
let finishDelete: ((response: Response) => void) | undefined
let requests: ReturnType<typeof vi.fn>

beforeEach(() => {
  queryClient.clear()
  localStorage.clear()
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: vi.fn() })
  Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', { configurable: true, value: vi.fn() })
  Object.defineProperty(Range.prototype, 'getClientRects', { configurable: true, value: () => [{ left: 20, top: 30, width: 50, height: 20 }] })
  requests = vi.fn((input: string, options?: RequestInit) => {
    const path = String(input)
    if (path.endsWith('/reader/markers/M000001') && options?.method === 'DELETE') return new Promise<Response>(resolve => { finishDelete = resolve })
    if (path.endsWith('/reader/markers') && options?.method === 'POST') return Promise.resolve(json({ revision: 'rev2', marker: { ...marker, id: 'M000002' } }))
    if (path.endsWith('/reader/markers')) return Promise.resolve(json({ _revision: 'rev1', book_fingerprint: 'fp', markers: [marker] }))
    if (path.endsWith('/reader/progress')) return Promise.resolve(json({ total_words: 2, last_chapter: { id: 'c1', title: 'One' }, chapters: [{ id: 'c1', start: 0, words: 2, blocks: [{ id: 'b1', start: 0, words: 2 }] }] }))
    if (path.endsWith('/reader/chapters/c1')) return Promise.resolve(json({ id: 'c1', title: 'One', complete: true, stale: false, blocks: [{ id: 'b1', kind: 'paragraph', text: 'Relay stayed' }] }))
    if (path.endsWith('/reader')) return Promise.resolve(json({ title: 'Book', book_fingerprint: 'fp', chapters: [{ id: 'c1', title: 'One' }] }))
    throw new Error(`Unexpected request: ${path}`)
  })
  vi.stubGlobal('fetch', requests)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); queryClient.clear() })

function mount() {
  return render(<QueryClientProvider client={queryClient}><Scope.Provider value="reader-test"><ReadingBook id="w1" libraryOpen={false} /></Scope.Provider></QueryClientProvider>)
}

it('closes settings outside and removes the marker panel and gutter before DELETE resolves', async () => {
  const view = mount()
  await waitFor(() => expect(screen.getByText('Relay stayed')).toBeTruthy())
  await waitFor(() => expect(view.container.querySelector('.reader-gutter-marker')).not.toBeNull())
  expect(view.container.querySelector('.reader-range-rect.marker')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Reader settings' }))
  expect(view.container.querySelector('.reader-settings')).not.toBeNull()
  fireEvent.pointerDown(document.body)
  expect(view.container.querySelector('.reader-settings')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Markers in b1' }))
  const details = screen.getByRole('complementary', { name: 'Marker details' })
  expect(details.getAttribute('data-ui-debug-id')).toBe('RMP')
  expect(screen.getByText('M000001')).toBeTruthy()
  expect(details.querySelector('.reader-delete-marker')).not.toBeNull()
  fireEvent.click(screen.getByText('Markers · 1'))
  fireEvent.click(details.querySelector('button')!)
  expect(view.container.querySelector('.reader-gutter-marker')).toBeNull()
  expect(view.container.querySelector('.reader-marker-popover')).toBeNull()
  expect(view.container.querySelector('.reader-range-rect.marker')).toBeNull()
  expect(finishDelete).toBeTypeOf('function')
  await act(async () => finishDelete!(json({ deleted: marker.id, revision: 'rev2' })))
  expect(queryClient.getQueryData<{ markers: unknown[]; _revision: string }>(['reader-test', '/api/workspaces/w1/reader/markers'])).toMatchObject({ markers: [], _revision: 'rev2' })
})

it('restores the marker indicator after a revision conflict without highlighting book text', async () => {
  const view = mount()
  await waitFor(() => expect(view.container.querySelector('.reader-gutter-marker')).not.toBeNull())
  fireEvent.click(screen.getByText('Markers · 1'))
  fireEvent.click(screen.getByRole('button', { name: 'Delete marker' }))
  expect(view.container.querySelector('.reader-gutter-marker')).toBeNull()
  await act(async () => finishDelete!(new Response(JSON.stringify({ error: { code: 'marker_conflict', message: 'Marker revision changed.' } }), { status: 409, headers: { 'Content-Type': 'application/json' } })))
  await waitFor(() => expect(view.container.querySelector('.reader-gutter-marker')).not.toBeNull())
  expect(view.container.querySelector('.reader-range-rect.marker')).toBeNull()
  expect(screen.getByText('Marker revision changed.')).toBeTruthy()
})

it('paints a gesture preview and hides Reader chrome after the selected delay', async () => {
  const view = mount()
  await waitFor(() => expect(screen.getByText('Relay stayed')).toBeTruthy())
  const paragraph = view.container.querySelector<HTMLElement>('[data-block-id="b1"]')!
  Object.defineProperty(document, 'caretPositionFromPoint', { configurable: true, value: (x: number) => ({ offsetNode: paragraph.firstChild, offset: x < 50 ? 2 : 9 }) })
  Object.defineProperty(Range.prototype, 'getClientRects', { configurable: true, value: function(this: Range) { return [{ left: 20, top: 30, width: this.toString().length * 10, height: 20 }] } })
  fireEvent.pointerDown(paragraph, { pointerId: 1, button: 0, clientX: 30, clientY: 30 })
  expect(view.container.querySelector<HTMLElement>('.reader-range-rect.preview')?.style.width).toBe('50px')
  fireEvent.pointerMove(paragraph, { pointerId: 1, clientX: 100, clientY: 30 })
  await waitFor(() => expect(view.container.querySelector<HTMLElement>('.reader-range-rect.preview')?.style.width).toBe('120px'))
  vi.useFakeTimers()
  fireEvent.pointerUp(paragraph, { pointerId: 1, clientX: 100, clientY: 30 })
  expect(view.container.querySelector('.reader-range-rect.preview')).not.toBeNull()
  act(() => vi.advanceTimersByTime(900))
  expect(view.container.querySelector('.reader-range-rect.preview')).toBeNull()
  expect(view.container.querySelector('.reader-range-rect.marker')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Reader settings' }))
  fireEvent.change(screen.getByLabelText('Header auto-hide'), { target: { value: '5' } })
  fireEvent.pointerDown(document.body)
  act(() => vi.advanceTimersByTime(5000))
  expect(document.documentElement.classList.contains('reader-chrome-hidden')).toBe(true)
  fireEvent.pointerMove(view.container.querySelector('.reader-reading-area')!)
  expect(document.documentElement.classList.contains('reader-chrome-hidden')).toBe(false)
})
