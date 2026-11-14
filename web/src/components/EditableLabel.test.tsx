// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { request } from '../api/client'
import { EditableLabel } from './EditableLabel'

vi.mock('../api/client', async () => {
  const { createContext } = await import('react')
  return {
    Scope: createContext('scope'),
    endpoint: (id: string, path: string) => `/api/workspaces/${id}/${path}`,
    queryClient: { removeQueries: vi.fn() },
    reconcile: vi.fn(async () => {}),
    request: vi.fn(async () => ({ label: 'Renamed' })),
  }
})

beforeEach(() => vi.mocked(request).mockClear())
afterEach(cleanup)

it('opens rename from keyboard activation but keeps double-click for pointers', () => {
  render(<EditableLabel id="w1" label="Draft" />)
  const trigger = screen.getByRole('button', { name: 'Draft' })
  fireEvent.click(trigger, { detail: 1 })
  expect(screen.queryByRole('textbox')).toBeNull()
  fireEvent.doubleClick(trigger)
  expect(screen.getByRole('textbox', { name: 'Workspace label' })).toBeTruthy()
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' })
  // Enter and Space on a focused button dispatch a click with detail 0.
  fireEvent.click(screen.getByRole('button', { name: 'Draft' }), { detail: 0 })
  expect(screen.getByRole('textbox', { name: 'Workspace label' })).toBeTruthy()
})

it('ignores Enter and Escape while an IME composition is active', async () => {
  render(<EditableLabel id="w1" label="Draft" />)
  fireEvent.click(screen.getByRole('button', { name: 'Draft' }), { detail: 0 })
  const input = screen.getByRole('textbox')
  fireEvent.change(input, { target: { value: '翻訳' } })
  fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
  fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 })
  fireEvent.keyDown(input, { key: 'Escape', isComposing: true })
  expect(request).not.toHaveBeenCalled()
  expect(screen.getByRole('textbox')).toBe(input)
  await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }) })
  expect(request).toHaveBeenCalledTimes(1)
  expect(vi.mocked(request).mock.calls[0]![2]).toEqual({ method: 'PATCH', body: { label: '翻訳' } })
})

it('returns focus to the rename button after Enter or Escape', async () => {
  render(<EditableLabel id="w1" label="Draft" />)
  fireEvent.click(screen.getByRole('button', { name: 'Draft' }), { detail: 0 })
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' })
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Draft' }))
  fireEvent.click(screen.getByRole('button', { name: 'Draft' }), { detail: 0 })
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Renamed' } })
  await act(async () => { fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' }) })
  expect(request).toHaveBeenCalledTimes(1)
  expect(document.activeElement).toBe(screen.getByRole('button'))
})
