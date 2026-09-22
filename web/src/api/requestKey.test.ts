import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRequestKey } from './requestKey'

afterEach(() => vi.unstubAllGlobals())

describe('request identity on plain-HTTP origins', () => {
  it('uses cryptographic random bytes without randomUUID', () => {
    vi.stubGlobal('crypto', { getRandomValues: (bytes: Uint8Array) => {
      bytes.set(Array.from({ length: 16 }, (_, index) => index))
      return bytes
    } })
    expect(createRequestKey()).toBe('000102030405060708090a0b0c0d0e0f')
  })

  it('fails visibly when secure random bytes are unavailable', () => {
    vi.stubGlobal('crypto', {})
    expect(createRequestKey).toThrow('Secure random values are unavailable')
  })
})
