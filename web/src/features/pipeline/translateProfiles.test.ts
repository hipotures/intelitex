import { expect, it } from 'vitest'
import type { Pipeline, Profiles, Usage } from '../../api/schema'
import { assignedProfile, recordedProfileNames } from './TranslateContent'

it('distinguishes section overrides from the profiles that produced recorded tokens', () => {
  const local = { name: 'local', provider: 'llamacpp', model: null, enabled: true, stable_palette_index: 2 }
  const luna = { name: 'codex-luna-medium', provider: 'codex', model: 'gpt-5.6-luna', enabled: true, stable_palette_index: 3 }
  const profiles = { profiles: [local, luna], resolved_passes: { '3': local } } as unknown as Profiles
  const section = { profiles: { '3': 'codex-luna-medium' } } as unknown as Pipeline['sections'][number]
  const usage = { units: [{ unit_id: 'ch0002_c0001', passes: [
    { pass_no: 3, profile: 'local', provider_call_count: 0 },
    { pass_no: 3, profile: 'codex-luna-medium', provider_call_count: 1 },
  ] }] } as Usage

  expect(assignedProfile(profiles, undefined, 3)?.name).toBe('local')
  expect(assignedProfile(profiles, section, 3)?.name).toBe('codex-luna-medium')
  expect(recordedProfileNames(usage, 3)).toEqual(['codex-luna-medium'])
  expect(recordedProfileNames(usage, 4)).toEqual([])
})
