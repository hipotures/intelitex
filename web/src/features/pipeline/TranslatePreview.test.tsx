// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import type { TranslationPassPreview } from '../../api/schema'
import { PreviewPage } from './TranslateContent'

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
  expect(container.querySelectorAll('.translate-preview-sentence-label')).toHaveLength(0)
  expect(pairs[0]!.children[0]!.textContent).toContain('First English block.')
  expect(pairs[0]!.children[1]!.textContent).toContain('Pierwszy polski blok.')
  expect(pairs[1]!.children[0]!.textContent).toContain('Second English block.')
  expect(pairs[1]!.children[1]!.textContent).toContain('Drugi polski blok.')
})

it('attaches checks and findings to their source sentence', () => {
  const analysis = { ...page, pass_no: 2, translations: [],
    checks: [{ sid: 'B1:S001', risk: 'low' }, { sid: 'B2:S001', risk: 'high' }],
    findings: [{ sid: 'B2:S001', type: 'style', meaning: 'Second sentence issue' }] }
  const { container } = render(<PreviewPage page={analysis} passNo={2} />)
  const pairs = container.querySelectorAll('.translate-preview-pair')
  expect(pairs).toHaveLength(2)
  expect([...container.querySelectorAll('.translate-preview-block-label')].map(item => item.textContent)).toEqual(['B1', 'B2'])
  expect([...container.querySelectorAll('.translate-preview-sentence-label')].map(item => item.textContent)).toEqual(['B1:S001', 'B2:S001'])
  expect(pairs[0]!.textContent).not.toContain('B1:S001')
  expect(pairs[1]!.textContent).not.toContain('B2:S001')
  expect(pairs[0]!.textContent).toContain('First English sentence.')
  expect(pairs[0]!.textContent).toContain('Risk: low')
  expect(pairs[0]!.textContent).not.toContain('Second sentence issue')
  expect(pairs[1]!.textContent).toContain('Second English sentence.')
  expect(pairs[1]!.textContent).toContain('Risk: high')
  expect(pairs[1]!.textContent).toContain('Second sentence issue')
  expect(pairs[0]!.querySelector('.translate-preview-check.attention')).toBeNull()
  expect(pairs[1]!.querySelector('.translate-preview-check.attention')).not.toBeNull()
})

it('marks only P4 corrections for attention', () => {
  const correction = { ...page, pass_no: 4, translations: [],
    checks: [{ sid: 'B1:S001', status: 'ok' }, { sid: 'B2:S001', status: 'needs_correction' }] }
  const { container } = render(<PreviewPage page={correction} passNo={4} />)
  const pairs = container.querySelectorAll('.translate-preview-pair')
  expect(pairs[0]!.textContent).toContain('Status: ok')
  expect(pairs[0]!.querySelector('.attention')).toBeNull()
  expect(pairs[1]!.textContent).toContain('Status: needs correction')
  expect(pairs[1]!.querySelector('.attention')).not.toBeNull()
})
