import { describe, expect, it } from 'vitest'
import { prepareExcerpt } from './prepareExcerpt'

describe('Prepare source excerpt', () => {
  it('shows the first 1 KiB in UTF-8 without splitting a character', () => {
    const excerpt = prepareExcerpt([{ text: 'a'.repeat(1023) + 'ą' + 'later' }])
    expect(excerpt.text).toBe('a'.repeat(1023))
    expect(new TextEncoder().encode(excerpt.text).length).toBeLessThanOrEqual(1024)
    expect(excerpt.truncated).toBe(true)
  })

  it('keeps shorter source paragraphs and does not invent missing text', () => {
    expect(prepareExcerpt([{ text: 'First' }, { text: 'Second' }])).toEqual({
      text: 'First\n\nSecond', truncated: false,
    })
    expect(prepareExcerpt([])).toEqual({ text: '', truncated: false })
  })
})
