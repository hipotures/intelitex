import { expect, it } from 'vitest'
import { headingSemantics } from './Reader'

it('exposes Reader heading blocks as headings without changing their element', () => {
  expect(headingSemantics('h1', false)).toEqual({ role: 'heading', 'aria-level': 1 })
  expect(headingSemantics('h4', false)).toEqual({ role: 'heading', 'aria-level': 4 })
  expect(headingSemantics('heading', false)).toEqual({ role: 'heading', 'aria-level': 3 })
  expect(headingSemantics('heading', true)).toEqual({ role: 'heading', 'aria-level': 2 })
  expect(headingSemantics('paragraph', false)).toEqual({})
  expect(headingSemantics('hr', false)).toEqual({})
})
