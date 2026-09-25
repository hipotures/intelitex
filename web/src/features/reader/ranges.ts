// Typed port of the legacy Reader's tested Unicode/inline run mapping.
export const utf16ToCodePoint = (text: string, offset: number) => Array.from(text.slice(0, Math.max(0, offset))).length
export const codePointToUtf16 = (text: string, offset: number) => Array.from(text).slice(0, Math.max(0, offset)).join('').length
export function snapWordRange(text: string, first: number, second = first) {
  const spans = Array.from(text.matchAll(/[\p{L}\p{N}\p{M}_]+(?:[’'-][\p{L}\p{N}\p{M}_]+)*/gu), match => ({
    start: utf16ToCodePoint(text, match.index), end: utf16ToCodePoint(text, match.index + match[0].length),
  }))
  if (!spans.length) return null
  const start = Math.min(first, second), end = Math.max(first, second)
  const selected = spans.filter(span => end === start ? span.start <= start && start <= span.end : span.end > start && span.start < end)
  if (selected.length) return { start: selected[0]!.start, end: selected[selected.length - 1]!.end }
  const nearest = spans.reduce((best, span) => {
    const distance = start < span.start ? span.start - start : start > span.end ? start - span.end : 0
    return distance < best.distance ? { span, distance } : best
  }, { span: spans[0]!, distance: Infinity }).span
  return { start: nearest.start, end: nearest.end }
}
export function inlineRuns(text: string, formatting: { start: number; end: number; style: 'em'|'strong' }[] = []) {
  const characters = Array.from(text)
  const runs: { text: string; style: 'em'|'strong'|null }[] = []
  let offset = 0
  for (const span of formatting) {
    if (!['em','strong'].includes(span.style) || !Number.isInteger(span.start) || !Number.isInteger(span.end) || span.start < offset || span.end <= span.start || span.end > characters.length) return [{ text, style: null }]
    if (span.start > offset) runs.push({ text: characters.slice(offset, span.start).join(''), style: null })
    runs.push({ text: characters.slice(span.start, span.end).join(''), style: span.style }); offset = span.end
  }
  if (offset < characters.length) runs.push({ text: characters.slice(offset).join(''), style: null })
  return runs.length ? runs : [{ text, style: null }]
}
export function selectedRange(selection: Selection | null) {
  if (!selection?.rangeCount || selection.isCollapsed) return null
  const range = selection.getRangeAt(0)
  const parent = range.startContainer.nodeType === Node.ELEMENT_NODE ? range.startContainer as Element : range.startContainer.parentElement
  const block = parent?.closest<HTMLElement>('[data-block-id]')
  if (!block || !block.contains(range.endContainer)) return null
  const prefix = document.createRange(); prefix.selectNodeContents(block); prefix.setEnd(range.startContainer, range.startOffset)
  const start = Array.from(prefix.toString()).length
  const text = range.toString()
  return { block_id: block.dataset.blockId!, start, end: start + Array.from(text).length, text }
}
