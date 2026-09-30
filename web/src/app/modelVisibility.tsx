import { useCallback, useContext, useMemo, useSyncExternalStore } from 'react'
import { Scope } from '../api/client'
import type { Profiles } from '../api/schema'
import { preference, savePreference, subscribePreferences } from './preferences'

export function useModelVisibility() {
  const scope = useContext(Scope)
  const subscribe = useCallback((listener: () => void) => subscribePreferences(scope, listener), [scope])
  const snapshot = useCallback(() => preference(scope, 'models.hidden', '[]'), [scope])
  const raw = useSyncExternalStore(subscribe, snapshot)
  const hidden = useMemo(() => {
    try {
      const value: unknown = JSON.parse(raw)
      return new Set<string>(Array.isArray(value) ? value.filter((name): name is string => typeof name === 'string') : [])
    } catch { return new Set<string>() }
  }, [raw])
  return {
    hidden,
    setVisible(name: string, visible: boolean) {
      const next = new Set(hidden)
      if (visible) next.delete(name)
      else next.add(name)
      savePreference(scope, 'models.hidden', JSON.stringify([...next]))
    },
  }
}

export function ProfileOptions({ profiles, selected }: { profiles?: Profiles['profiles']; selected?: string | null }) {
  const { hidden } = useModelVisibility()
  return profiles?.filter(profile => profile.enabled && (!hidden.has(profile.name) || profile.name === selected)).map(profile =>
    <option key={profile.name} value={profile.name} disabled={hidden.has(profile.name)}>
      {profile.name}{hidden.has(profile.name) ? ' (hidden · current assignment)' : ''}
    </option>,
  )
}
