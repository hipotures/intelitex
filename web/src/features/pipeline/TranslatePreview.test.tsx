// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { TranslationPassPreview } from '../../api/schema'
import { PreviewFilter, PreviewPage } from './TranslateContent'

afterEach(cleanup)

const page: TranslationPassPreview = {
  chunk_id: 'ch0001_c0001', pass_no: 3, page: 0, next_page: null,
  available: true, current: false, truncated: false,
  source: [{ id: 'B1', text: 'First English block.' }, { id: 'B2', text: 'Second English block.' }],
  sentences: [{ id: 'B1:S001', block_id: 'B1', text: 'First English sentence.' },
    { id: 'B2:S001', block_id: 'B2', text: 'Second English sentence.' }],
  translations: [{ id: 'B2', text: 'Drugi polski blok.' }, { id: 'B1', text: 'Pierwszy polski blok.' }],
  checks: [], findings: [],
}

it('keeps source and translation for each block in one row even if saved output order differs', () => {
  const { container } = render(<PreviewPage page={page} passNo={3} />)
  const pairs = container.querySelectorAll('.translate-preview-pair')
  expect(pairs).toHaveLength(2)
  expect([...container.querySelectorAll('.translate-preview-block-label')].map(item => item.textContent)).toEqual(['B1', 'B2'])
  expect(container.querySelectorAll('.translate-preview-segment-id')).toHaveLength(0)
  expect(pairs[0]!.children[0]!.textContent).toContain('First English block.')
  expect(pairs[0]!.children[1]!.textContent).toContain('Pierwszy polski blok.')
  expect(pairs[1]!.children[0]!.textContent).toContain('Second English block.')
  expect(pairs[1]!.children[1]!.textContent).toContain('Drugi polski blok.')
})

it('keeps one block header and places short sentence IDs beside their checks', () => {
  const analysis = { ...page, pass_no: 2, translations: [],
    checks: [{ sid: 'B1:S001', risk: 'low' }, { sid: 'B2:S001', risk: 'high' }],
    findings: [{ sid: 'B2:S001', type: 'style', meaning: 'Second sentence issue' }] }
  const { container } = render(<PreviewPage page={analysis} passNo={2} />)
  const segments = container.querySelectorAll('.translate-preview-segment')
  expect(segments).toHaveLength(2)
  expect([...container.querySelectorAll('.translate-preview-block-label')].map(item => item.textContent)).toEqual(['B1', 'B2'])
  expect([...container.querySelectorAll('.translate-preview-segment-id')].map(item => item.textContent)).toEqual(['S001', 'S001'])
  expect(segments[0]!.querySelector('.translate-preview-segment-id')?.getAttribute('title')).toBe('B1:S001')
  expect(segments[0]!.textContent).toContain('First English sentence.')
  expect(segments[0]!.textContent).toContain('Risk: low')
  expect(segments[0]!.textContent).not.toContain('Second sentence issue')
  expect(segments[1]!.textContent).toContain('Second English sentence.')
  expect(segments[1]!.textContent).toContain('Risk: high')
  expect(segments[1]!.textContent).toContain('Second sentence issue')
  expect(segments[0]!.querySelector('.translate-preview-check.attention')).toBeNull()
  expect(segments[1]!.querySelector('.translate-preview-check.attention')).not.toBeNull()
})

it('marks only P4 corrections for attention', () => {
  const correction = { ...page, pass_no: 4, translations: [],
    checks: [{ sid: 'B1:S001', status: 'ok' }, { sid: 'B2:S001', status: 'needs_correction' }] }
  const { container } = render(<PreviewPage page={correction} passNo={4} />)
  const segments = container.querySelectorAll('.translate-preview-segment')
  expect(segments[0]!.textContent).toContain('Status: ok')
  expect(segments[0]!.classList.contains('attention')).toBe(false)
  expect(segments[1]!.textContent).toContain('Status: needs correction')
  expect(segments[1]!.classList.contains('attention')).toBe(true)
})

it('shows source-only blocks with one compact header each', () => {
  const sourceOnly = { ...page, available: false, translations: [] }
  const { container } = render(<PreviewPage page={sourceOnly} passNo={null} />)
  expect(container.querySelectorAll('.translate-preview-block-label')).toHaveLength(2)
  expect(container.querySelectorAll('.translate-preview-pair.source-only')).toHaveLength(2)
  expect(container.querySelectorAll('.translate-preview-segment')).toHaveLength(0)
})

it('uses three buttons for P2 and P4 filters', () => {
  const onChange = vi.fn()
  const { rerender } = render(<PreviewFilter passNo={2} value="attention" onChange={onChange} />)
  expect(screen.getAllByRole('button')).toHaveLength(3)
  expect(screen.getByRole('button', { name: 'M+H' }).getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: 'High' }))
  expect(onChange).toHaveBeenCalledWith('high')
  rerender(<PreviewFilter passNo={4} value="needs_correction" onChange={onChange} />)
  expect(screen.getAllByRole('button')).toHaveLength(3)
  expect(screen.getByRole('button', { name: 'Needs correction' }).getAttribute('aria-pressed')).toBe('true')
})
