/** Request receipts need unpredictable IDs on both secure and plain-HTTP LAN origins. */
export function createRequestKey(): string {
  if (typeof globalThis.crypto?.getRandomValues !== 'function') {
    throw new Error('Secure random values are unavailable in this browser. Cannot save safely.')
  }
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
}
