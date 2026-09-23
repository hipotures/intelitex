/** A display-only UTF-8 prefix of source text returned by the existing section preview API. */
export function prepareExcerpt(blocks: readonly { text: string }[], limit = 1024) {
  const source = blocks.map(block => block.text).join('\n\n')
  const bytes = new TextEncoder().encode(source)
  return {
    text: new TextDecoder('utf-8').decode(bytes.subarray(0, limit), { stream: true }),
    truncated: bytes.length > limit,
  }
}
