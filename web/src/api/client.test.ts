import { describe, expect, it } from 'vitest'
import { isWorkspaceList, matchesWorkspaceResource } from './client'

describe('workspace query boundaries', () => {
  it('matches the selected workspace and nested resources without matching a sibling ID', () => {
    expect(matchesWorkspaceResource('/api/workspaces/w-1', 'w-1')).toBe(true)
    expect(matchesWorkspaceResource('/api/workspaces/w-1/pipeline', 'w-1')).toBe(true)
    expect(matchesWorkspaceResource('/api/workspaces/w-10/pipeline', 'w-1')).toBe(false)
    expect(matchesWorkspaceResource('/api/workspaces', 'w-1')).toBe(false)
  })
  it('recognizes filtered workspace lists for mutation reconciliation', () => {
    expect(isWorkspaceList('/api/workspaces')).toBe(true)
    expect(isWorkspaceList('/api/workspaces?archived=false')).toBe(true)
    expect(isWorkspaceList('/api/workspaces?archived=true')).toBe(true)
    expect(isWorkspaceList('/api/workspaces/w-1/pipeline')).toBe(false)
  })
})
