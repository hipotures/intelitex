// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it } from 'vitest'
import { queryClient } from '../../api/client'
import { SourceDetails } from './LibrarySource'

afterEach(() => { cleanup(); queryClient.clear() })
it('offers the encoded EPUB download in source details and hides it for directories', () => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value() { this.setAttribute('open', '') } })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value() { this.removeAttribute('open') } })
  const source = { source_id: 'Series/Żółw [PL].epub', title: 'Żółw', creators: [], language: 'pl', word_count: null, workspace_id: null, groups: [], gutenberg: false }
  const view = render(<QueryClientProvider client={queryClient}><SourceDetails source={source} close={() => {}} add={() => {}} /></QueryClientProvider>)
  const link = screen.getByRole('link', { name: 'Download' })
  expect(link.getAttribute('href')).toBe('/api/library/sources/' + encodeURIComponent(source.source_id) + '/download')
  expect(link.hasAttribute('download')).toBe(true)
  view.rerender(<QueryClientProvider client={queryClient}><SourceDetails source={{ ...source, source_id: 'unpacked-book' }} close={() => {}} add={() => {}} /></QueryClientProvider>)
  expect(screen.queryByRole('link', { name: 'Download' })).toBeNull()
})
