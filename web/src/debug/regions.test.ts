import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { debugRegions, pipelineModelIds, sectionModelIds } from './regions'

describe('stable UI debug registry', () => {
  it('contains unique three-letter IDs with source-owned descriptions', () => {
    const ids = debugRegions.map(region => region.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const region of debugRegions) {
      expect(region.id).toMatch(/^[A-Z]{3}$/)
      expect(region.description.trim().length).toBeGreaterThan(3)
      expect(existsSync(resolve('src', region.source))).toBe(true)
    }
  })
  it('keeps repeated pass families distinct and stable', () => {
    expect(pipelineModelIds).toEqual(['MPA', 'MPB', 'MPC', 'MPD', 'MPE'])
    expect(sectionModelIds).toEqual(['SMA', 'SMB', 'SMC', 'SMD', 'SME'])
    for (const id of [...pipelineModelIds, ...sectionModelIds]) expect(debugRegions.some(region => region.id === id)).toBe(true)
  })
  it('keeps the human reference table aligned with the source registry', () => {
    const documentation = readFileSync(resolve('../docs/web/debug-ids.md'), 'utf8')
    const rows = [...documentation.matchAll(/^\| ([A-Z]{3}) \| ([^|]+) \| `web\/src\/([^`]+)` \|$/gm)]
    expect(rows.map(row => [row[1], row[2]?.trim(), row[3]])).toEqual(
      debugRegions.map(region => [region.id, region.description, region.source]),
    )
  })
  it('does not reuse a registered literal ID in another source component', () => {
    const owner = new Map<string, string>(debugRegions.map(region => [region.id, region.source]))
    for (const source of new Set(debugRegions.map(region => region.source))) {
      const content = readFileSync(resolve('src', source), 'utf8')
      const literals = [
        ...content.matchAll(/debugTag\(['"]([A-Z]{3})['"]/g),
        ...content.matchAll(/debugId=['"]([A-Z]{3})['"]/g),
      ]
      for (const match of literals) expect(owner.get(match[1]!)).toBe(source)
    }
  })
})
